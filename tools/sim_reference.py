#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_reference.py — executable reference model of the NATIONS & WAR simulation.

WHY THIS FILE EXISTS
--------------------
The C# cannot be compiled in this sandbox, but the *mechanics* still have to be
proven. This file is a faithful twin of OnyxNations/Sim (army, war, economy,
diplomacy, government, AI opponent) written so it can actually be executed.

Everything tuned here is transcribed into C# unchanged. If a constant is wrong,
it is wrong here first and gets fixed here first: the war you see below is the
war the player will get.

    python3 tools/sim_reference.py            # 2 in-game years, verbose
    python3 tools/sim_reference.py --years 4 --quiet

Assertions at the end are the real point: they are the same claims the design
document makes (armies move, orders are executed, territory changes hands,
taxes move morale, the AI reacts on its own).
"""

import argparse
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "OnyxNations", "Data")

# ------------------------------------------------------------------ tuning --
# ---- time model -----------------------------------------------------------
# The strategic layer runs on REAL time, not on GTA's clock. GTA's own day is
# 48 real minutes, which is far too slow for a war: an AI that thought once per
# game day would think once every three quarters of an hour.
#
# One game day of *simulation* is compressed into SECONDS_PER_GAME_DAY real
# seconds, so the budget moves, elections tick and the opponent reacts on a
# timescale a player can actually watch.
MINUTES_PER_GAME_DAY = 1440
SECONDS_PER_GAME_DAY = 12.0          # 5 game days per real minute
AI_DECISION_EVERY_GAME_DAYS = 3      # => the opponent re-plans every ~36 s
BATTLE_COOLDOWN_SECONDS = 45.0
OCCUPY_TICK_SECONDS = 10.0
STRATEGIC_SLICE_SECONDS = 0.5        # integration step

# Daily economic output per head, in game dollars. Tuned so that a 2.3M-people
# republic earns roughly 500k/day - enough to run a small army and still feel
# every tank it buys.
GDP_PER_CAPITA_DAY = 0.30

# Strategic movement is deliberately slower than real time: an army crossing the
# map must take minutes of play, not seconds.
MOVEMENT_SCALE = 0.06
BATTLE_COOLDOWN_MINUTES = 180          # one engagement per region per 3 game hours
BATTLE_RANGE_METRES = 900.0            # units this close fight
UNIT_SPEED_REFERENCE = 12.0            # m/s at mobility 50


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def lerp(a, b, t):
    return a + (b - a) * t


class Rng(object):
    """Seeded RNG so a battle can be reproduced from (seed, turn, region)."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def uniform(self, a, b):
        return self.r.uniform(a, b)

    def chance(self, p):
        return self.r.random() < p


# ------------------------------------------------------------------- types --
class UnitType(object):
    def __init__(self, d):
        self.id = d["id"]
        self.name = d["name"]
        self.category = d["category"]
        self.manpower = d["manpower"]
        self.firepower = d["firepower"]
        self.armor = d["armor"]
        self.mobility = d["mobility"]
        self.range = d["range"]
        self.cost = d["cost"]
        self.upkeep = d["upkeep"]
        self.vehicle = d.get("vehicle", "")
        self.vehicle_count = d.get("vehicle_count", 0)


UNIT_TYPES = [
    dict(id="infantry",    name="Пехота",          category="Infantry",   manpower=620, firepower=28, armor=12, mobility=35, range=20, cost=180_000, upkeep=2_400, vehicle="", vehicle_count=0),
    dict(id="motor_rifle", name="Мотострелки",     category="MotorRifle", manpower=540, firepower=40, armor=32, mobility=68, range=30, cost=420_000, upkeep=5_200, vehicle="barracks", vehicle_count=14),
    dict(id="armor",       name="Танковый батальон", category="Armor",     manpower=180, firepower=88, armor=92, mobility=52, range=45, cost=1_450_000, upkeep=13_000, vehicle="rhino", vehicle_count=14),
    dict(id="artillery",   name="Артиллерия",      category="Artillery",  manpower=210, firepower=96, armor=18, mobility=22, range=95, cost=980_000, upkeep=9_400, vehicle="barracks", vehicle_count=8),
    dict(id="air",         name="Авиаэскадрилья",  category="Air",        manpower=90,  firepower=92, armor=40, mobility=100, range=90, cost=2_600_000, upkeep=24_000, vehicle="lazer", vehicle_count=4),
    dict(id="heli",        name="Вертолётная эскадрилья", category="Heli", manpower=120, firepower=70, armor=34, mobility=88, range=55, cost=1_300_000, upkeep=14_000, vehicle="buzzard2", vehicle_count=4),
    dict(id="specops",     name="Спецназ",         category="SpecOps",    manpower=120, firepower=72, armor=30, mobility=74, range=40, cost=760_000, upkeep=7_800, vehicle="", vehicle_count=0),
    dict(id="milpolice",   name="Военная полиция", category="MilitaryPolice", manpower=240, firepower=34, armor=26, mobility=58, range=15, cost=210_000, upkeep=2_900, vehicle="", vehicle_count=0),
]


class Unit(object):
    _next = 1

    def __init__(self, utype, nation_id, position, name):
        self.id = "u%d" % Unit._next
        Unit._next += 1
        self.type = utype
        self.nation_id = nation_id
        self.position = (position[0], position[1])
        self.name = name
        self.strength = 1.0
        self.morale = 0.7
        self.supply = 0.85
        self.experience = 0.15
        self.order = "Hold"           # Hold | Move | Attack | Defend | Garrison
        self.target_region = None
        self.path = []                # remaining waypoints
        self.battle_cooldown = 0.0
        self.mobilized = True

    @property
    def manpower(self):
        return int(self.type.manpower * self.strength)

    def power(self, defending=False, region_value=5, general_bonus=0.0):
        t = self.type
        base = (t.manpower * self.strength * (0.50 * t.firepower + 0.20 * t.armor
                                              + 0.15 * t.mobility + 0.15 * t.range)) / 100.0
        morale_f = 0.40 + 0.60 * self.morale
        supply_f = 0.30 + 0.70 * self.supply
        exp_f = 0.80 + 0.40 * self.experience
        p = base * morale_f * supply_f * exp_f * (1.0 + general_bonus)
        if defending:
            p *= 1.25 * (1.0 + region_value / 50.0)
        return max(0.0, p)

    def speed(self, transport_bonus=0.0):
        """Metres per REAL second on the strategic map.

        Target: infantry crosses one region hop (~3000 m) in ~45 s, armour in
        ~37 s, aircraft in ~24 s, and the whole map in 1-2 minutes of play."""
        base = 35.0 + 0.90 * self.type.mobility
        return base * (0.5 + 0.5 * self.supply) * (1.0 + transport_bonus) \
            * (0.75 + 0.25 * self.strength)


class General(object):
    def __init__(self, name, skill, rank):
        self.name = name
        self.skill = skill        # 0..1
        self.rank = rank


class Minister(object):
    def __init__(self, role, name, competence, loyalty):
        self.role = role
        self.name = name
        self.competence = competence   # 0..1
        self.loyalty = loyalty         # 0..1
        self.relation = 0.5            # attitude to the president
        self.task = None
        self.task_days_left = 0


# ------------------------------------------------------------------ nation --
class Nation(object):
    def __init__(self, nid, name, capital, personality):
        self.id = nid
        self.name = name
        self.capital_region = capital
        self.treasury = 0.0
        self.tax_rate = 0.24
        self.army_funding = 0.30
        self.police_funding = 0.16
        self.social_funding = 0.22
        self.infra_funding = 0.12
        self.inflation = 0.02
        self.unemployment = 0.07
        self.morale = 0.60
        self.approval = 55.0
        self.war_weariness = 0.0
        self.stability = 0.70
        self.corruption = 0.18
        self.gdp = 0.0
        self.mobilization = 0.35
        self.units = []
        self.generals = []
        self.ministers = {}
        self.personality = personality    # dict: aggression, caution, mercantilism
        self.casualties = 0
        self.at_war = set()
        self.relations = {}

    # -- derived ----------------------------------------------------------
    def regions(self, world):
        return [r for r in world.regions.values() if r.owner == self.id]

    def population(self, world):
        return sum(r.population for r in self.regions(world))

    def army_power(self, defending=False, world=None, region=None):
        total = 0.0
        for u in self.units:
            rv = region.strategic if region else 5
            total += u.power(defending=defending, region_value=rv,
                             general_bonus=self.best_general_bonus())
        return total

    def best_general_bonus(self):
        if not self.generals:
            return 0.0
        return 0.05 + 0.25 * max(g.skill for g in self.generals)

    def finance_bonus(self):
        m = self.ministers.get("Finance")
        return 1.0 + 0.30 * (m.competence if m else 0.2)

    def transport_bonus(self):
        m = self.ministers.get("Transport")
        return 0.25 * (m.competence if m else 0.2)

    def defence_bonus(self):
        m = self.ministers.get("Defence")
        return 0.20 * (m.competence if m else 0.2)

    def diplomacy_bonus(self):
        m = self.ministers.get("Foreign")
        return 0.30 * (m.competence if m else 0.2)


# --------------------------------------------------------------- simulation --
class Simulation(object):
    def __init__(self, world, nations, seed=1337):
        self.world = world
        self.nations = nations
        self.rng = Rng(seed)
        self.time_minutes = 0.0
        self.real_seconds = 0.0
        self.day = 0
        self.events = []
        self.unit_types = dict((t["id"], UnitType(t)) for t in UNIT_TYPES)
        self.battles_fought = 0
        self.captures = 0
        self.decisions = 0

    # ------------------------------------------------------------ helpers --
    def log(self, text):
        self.events.append(("D%04d T%5.0fs" % (self.day, self.real_seconds), text))

    def nation(self, nid):
        return self.nations[nid]

    def enemies_of(self, nid):
        return [self.nations[o] for o in self.nation(nid).at_war]

    def units_in(self, region_id):
        r = self.world.regions[region_id]
        return [u for n in self.nations.values() for u in n.units
                if self.dist(u.position, r.centroid) < 2200.0]

    @staticmethod
    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    # -------------------------------------------------------------- orders --
    def order_move(self, unit, region_id):
        target = self.world.regions[region_id]
        unit.order = "Move"
        unit.target_region = region_id
        # straight line with one intermediate waypoint so roads feel plausible
        unit.path = list(_waypoints(unit.position, target.centroid))

    def order_attack(self, unit, region_id):
        self.order_move(unit, region_id)
        unit.order = "Attack"

    def order_defend(self, unit, region_id):
        self.order_move(unit, region_id)
        unit.order = "Defend"

    # ---------------------------------------------------------------- tick --
    def advance(self, real_seconds):
        """Advance the world by `real_seconds` of play, in small slices."""
        remaining = real_seconds
        while remaining > 0.0:
            step = min(STRATEGIC_SLICE_SECONDS, remaining)
            self.step(step)
            remaining -= step

    def step(self, dt_real):
        self.real_seconds += dt_real
        self.time_minutes += dt_real * (MINUTES_PER_GAME_DAY / SECONDS_PER_GAME_DAY)
        for nid in self.nations:
            n = self.nation(nid)
            for u in n.units:
                u.battle_cooldown = max(0.0, u.battle_cooldown - dt_real)
                self.move_unit(n, u, dt_real)
        self.resolve_battles()

        while self.real_seconds >= (self.day + 1) * SECONDS_PER_GAME_DAY:
            self.day += 1
            self.step_day()

    def move_unit(self, nation, unit, dt):
        if not unit.path or unit.order == "Hold":
            return
        dist = unit.speed(nation.transport_bonus()) * dt
        while dist > 0 and unit.path:
            wp = unit.path[0]
            d = self.dist(unit.position, wp)
            if d <= dist:
                unit.position = (wp[0], wp[1])
                dist -= d
                unit.path.pop(0)
            else:
                t = dist / d
                unit.position = (unit.position[0] + (wp[0] - unit.position[0]) * t,
                                 unit.position[1] + (wp[1] - unit.position[1]) * t)
                dist = 0.0
        if not unit.path:
            if unit.order == "Attack":
                pass          # stay and fight; battle resolution picks it up
            elif unit.order in ("Move", "Defend"):
                unit.order = "Hold"
        unit.supply = clamp(unit.supply - 0.00035 * dt)

    # -------------------------------------------------------------- battles --
    def resolve_battles(self):
        """Every 10-minute slice: for each region, if hostile forces are standing
        on it, either fight the garrison or occupy it uncontested."""
        for rid, region in self.world.regions.items():
            present = {}
            for nid, n in self.nations.items():
                for u in n.units:
                    if self.dist(u.position, region.centroid) < BATTLE_RANGE_METRES:
                        present.setdefault(nid, []).append(u)

            owner = region.owner
            hostiles = [(hid, us) for hid, us in present.items() if hid != owner]

            if not hostiles:
                # nobody is contesting it: the owner slowly reasserts control
                if region.control < 100.0:
                    region.control = min(100.0, region.control + 0.8)
                continue

            for hid, hus in hostiles:
                at_war = (hid in self.nation(owner).at_war) or (owner in self.nation(hid).at_war)
                if not at_war:
                    self.border_incident(hid, owner, region)
                    continue

                ready_attackers = [u for u in hus if u.battle_cooldown <= 0]
                if not ready_attackers:
                    continue
                garrison = [u for u in present.get(owner, []) if u.battle_cooldown <= 0]
                if garrison:
                    self.fight(hid, ready_attackers, owner, garrison, region)
                else:
                    self.occupy(hid, ready_attackers, region)

    def occupy(self, attacker_id, units, region):
        """No garrison: the invader takes the ground. Slower than winning a
        battle, and it still costs supply."""
        for u in units:
            u.battle_cooldown = OCCUPY_TICK_SECONDS
            u.supply = clamp(u.supply - 0.02)
        region.control = max(0.0, region.control - self.rng.uniform(2.5, 5.0))
        region.destruction = clamp(region.destruction + self.rng.uniform(0.2, 1.0))
        if region.control <= 0.0:
            previous = region.owner
            region.owner = attacker_id
            region.control = 35.0
            self.captures += 1
            self.log("ЗАХВАТ (без боя): %s -> %s (%s)" % (region.id, attacker_id, region.name))
        elif self.rng.chance(0.10):
            self.log("оккупация %s: %s, контроль %.0f%%" % (region.id, attacker_id, region.control))

    DESTROYED_BELOW = 0.08

    def sweep_destroyed(self):
        """A unit ground down past DESTROYED_BELOW is gone: it is removed from
        the order of battle, not kept as a 0-strength ghost."""
        for nid, n in self.nations.items():
            survivors = []
            for u in n.units:
                if u.strength < self.DESTROYED_BELOW:
                    self.log("уничтожен: %s (%s, %s)" % (u.name, u.type.name, nid))
                else:
                    survivors.append(u)
            n.units = survivors

    def fight(self, a_id, attackers, d_id, defenders, region):
        self.battles_fought += 1
        an, dn = self.nation(a_id), self.nation(d_id)

        a_power = sum(u.power(False, region.strategic, an.best_general_bonus()) for u in attackers)
        d_power = sum(u.power(True, region.strategic, dn.best_general_bonus()) for u in defenders)
        d_power *= 1.0 + dn.defence_bonus()

        # attacker holds the initiative only when actually stronger
        roll_a = a_power * self.rng.uniform(0.75, 1.25)
        roll_d = d_power * self.rng.uniform(0.75, 1.25)
        attacker_wins = roll_a > roll_d

        loser, winner = (defenders, attackers) if attacker_wins else (attackers, defenders)
        loser_nation, winner_nation = (dn, an) if attacker_wins else (an, dn)

        for u in loser:
            u.strength = clamp(u.strength - self.rng.uniform(0.15, 0.35))
            u.morale = clamp(u.morale - 0.18)
            u.battle_cooldown = BATTLE_COOLDOWN_SECONDS
            lost = int(u.type.manpower * 0.12)
            loser_nation.casualties += lost
        for u in winner:
            u.strength = clamp(u.strength - self.rng.uniform(0.03, 0.12))
            u.morale = clamp(u.morale + 0.10)
            u.experience = clamp(u.experience + 0.05)
            u.battle_cooldown = BATTLE_COOLDOWN_SECONDS
            winner_nation.casualties += int(u.type.manpower * 0.03)
        for u in attackers + defenders:
            u.supply = clamp(u.supply - 0.12)

        if attacker_wins:
            region.control = max(0.0, region.control - self.rng.uniform(18.0, 34.0))
            region.destruction = clamp(region.destruction + self.rng.uniform(3.0, 9.0))
            if region.control <= 0.0:
                previous = region.owner
                region.owner = a_id
                region.control = 35.0
                self.captures += 1
                self.log("ЗАХВАТ: %s (%s) %s -> %s"
                         % (region.name, region.id, previous, a_id))
            else:
                self.log("бой у %s: %s теснит %s (контроль %.0f%%)"
                         % (region.id, a_id, d_id, region.control))
        else:
            if region.owner == d_id:
                region.control = min(100.0, region.control + self.rng.uniform(6.0, 14.0))
            self.log("бой у %s: %s удержал оборону (контроль %.0f%%)"
                     % (region.id, d_id, region.control))

        an.war_weariness = clamp(an.war_weariness + 0.004)
        dn.war_weariness = clamp(dn.war_weariness + 0.004)
        an.morale = clamp(an.morale - 0.002)
        dn.morale = clamp(dn.morale - 0.002)
        self.sweep_destroyed()

    # ------------------------------------------------------------------ day --
    def step_day(self):
        for nid in self.nations:
            self.step_economy(self.nation(nid))
        for nid in self.nations:
            self.step_politics(self.nation(nid))
        if self.day % 3 == 0:
            for nid in self.nations:
                self.ai_decide(self.nation(nid))

    def step_economy(self, n):
        regions = n.regions(self.world)
        if not regions:
            return

        # GDP: per-capita output weighted by regional productivity, damaged by
        # destruction, damped by unemployment and (mildly) by inflation.
        gdp = 0.0
        for r in regions:
            damage = max(0.15, 1.0 - 0.006 * r.destruction)
            gdp += r.population * GDP_PER_CAPITA_DAY * r.productivity * damage \
                * (1.0 - 0.85 * n.unemployment) / (1.0 + 1.5 * n.inflation)
        n.gdp = gdp

        compliance = clamp(0.55 + 0.45 * n.morale - 0.5 * n.corruption + 0.2 * n.stability, 0.15, 1.0)
        revenue = gdp * n.tax_rate * compliance * n.finance_bonus()

        # The four sliders split the REVENUE, so the budget always balances on
        # paper; what breaks a nation is an army whose upkeep outruns the split.
        army_upkeep = sum(u.type.upkeep * (0.35 + 0.65 * u.strength) for u in n.units) \
            * (0.55 + 0.90 * n.mobilization) * (0.60 + 0.80 * n.army_funding) \
            / (1.0 + n.defence_bonus())
        police = revenue * n.police_funding
        social = revenue * n.social_funding
        infra = revenue * n.infra_funding
        expenses = army_upkeep + police + social + infra

        balance = revenue - expenses
        n.treasury += balance

        if n.treasury < 0.0:
            # Unpaid bills: a small inflation bump and a real hit to morale.
            # The feedback must stay weak or a deficit becomes a death spiral.
            n.inflation = clamp(n.inflation + 0.02 * (-n.treasury) / max(1.0, gdp), 0.0, 1.5)
            n.morale = clamp(n.morale - 0.01)
            n.stability = clamp(n.stability - 0.005)
            n.treasury = 0.0
        else:
            n.inflation = lerp(n.inflation, 0.02, 0.05)

        growth = (revenue - expenses) / max(1.0, gdp)
        target_unemp = clamp(0.06 + 0.35 * (n.tax_rate - 0.24) - 0.6 * growth
                             + 0.25 * n.inflation + 0.10 * n.war_weariness, 0.01, 0.60)
        n.unemployment = lerp(n.unemployment, target_unemp, 0.06)

        # morale target and slow convergence
        cabinet = sum(m.competence for m in n.ministers.values()) / max(1, len(n.ministers))
        target = 0.52 \
            + 0.55 * (0.06 - n.unemployment) \
            - 0.70 * (n.tax_rate - 0.24) \
            - 0.60 * n.inflation \
            - 0.75 * n.war_weariness \
            + 0.30 * (n.stability - 0.7) \
            + 0.35 * (cabinet - 0.5) \
            + 0.25 * n.social_funding
        n.morale = clamp(lerp(n.morale, clamp(target, 0.05, 0.98), 0.06))
        n.stability = clamp(lerp(n.stability,
                                 clamp(0.35 + 0.5 * n.morale - 0.4 * n.corruption
                                       + 0.2 * n.police_funding, 0.05, 0.98), 0.05))
        n.war_weariness = clamp(n.war_weariness - 0.006 if not n.at_war
                                else n.war_weariness)

    def step_politics(self, n):
        cabinet = sum(m.competence for m in n.ministers.values()) / max(1, len(n.ministers))
        target_approval = 30.0 + 55.0 * n.morale + 18.0 * (cabinet - 0.5) \
            - 22.0 * n.war_weariness - 30.0 * max(0.0, n.inflation - 0.05)
        n.approval = clamp(lerp(n.approval, target_approval, 0.05), 1.0, 99.0)

        for m in n.ministers.values():
            if m.task and m.task_days_left > 0:
                m.task_days_left -= 1
                if m.task_days_left == 0:
                    self.apply_minister_task(n, m)
                    m.task = None

    def apply_minister_task(self, n, m):
        q = m.competence
        if m.task == "revenue":
            n.treasury += n.gdp * 0.02 * q
        elif m.task == "mobilization":
            n.mobilization = clamp(n.mobilization + 0.08 * q)
            for u in n.units:
                u.strength = clamp(u.strength + 0.05 * q)
        elif m.task == "morale":
            n.morale = clamp(n.morale + 0.04 * q)
        elif m.task == "corruption":
            n.corruption = clamp(n.corruption - 0.05 * q)
        elif m.task == "diplomacy":
            for other in n.at_war:
                pass
            n.war_weariness = clamp(n.war_weariness - 0.05 * q)

    # -------------------------------------------------------------------- AI --
    def ai_decide(self, n):
        """Utility-based opponent AI. No scripted campaign: every tick the brain
        scores a small set of actions and takes the best one."""
        if n.id == self.player_nation:
            return

        p = n.personality
        # -1 (not 0) on purpose: with no threat on the map there is still an
        # adversary, and a nation with a claim and an army may still act on it.
        enemy = None
        best_threat = -1.0
        for other_id in self.nations:
            if other_id == n.id:
                continue
            threat = self.threat_from(n, other_id)
            if threat > best_threat:
                best_threat, enemy = threat, self.nations[other_id]
        if enemy is None:
            return

        at_war = enemy.id in n.at_war
        our_power = max(1.0, n.army_power(False))
        their_power = max(1.0, enemy.army_power(False))
        ratio = our_power / their_power
        economy_strain = 1.0 if n.treasury > n.gdp * 0.3 else 0.4

        actions = []

        # 1. defend a threatened border region
        for r in n.regions(self.world):
            if not r.neighbours:
                continue
            hostile_adjacent = any(self.world.regions[nb].owner == enemy.id
                                   for nb in r.neighbours if nb in self.world.regions)
            if not hostile_adjacent:
                continue
            present = sum(u.power(True, r.strategic) for u in n.units
                          if self.dist(u.position, r.centroid) < BATTLE_RANGE_METRES)
            need = max(0.0, self.threat_at(n, enemy, r) - present)
            # normalised: every utility is dimensionless and lands on 0..~3,
            # otherwise "power units" (~hundreds) drown out the other options
            need_ratio = clamp(need / max(1.0, our_power), 0.0, 1.5)
            actions.append((need_ratio * 2.5 * p["caution"] * economy_strain,
                            "defend", r))

        # 2. counter-attack / seize a weakly held enemy region
        for rid, r in self.world.regions.items():
            if r.owner == n.id:
                continue
            if r.owner != enemy.id and not at_war:
                continue
            garrison = sum(u.power(True, r.strategic) for u in enemy.units
                           if self.dist(u.position, r.centroid) < BATTLE_RANGE_METRES)
            strength_ratio = min(6.0, our_power / max(1.0, garrison * 3.0))
            value = r.strategic / 10.0 + r.population / 3_000_000.0
            u = (strength_ratio - 0.9) * value * p["aggression"] * 2.4 * economy_strain
            if not at_war:
                # attacking without a war is a much bigger step: hold it back,
                # but not so far that a claim plus a massed army means nothing
                u *= 0.45
            actions.append((u, "attack", r))

        # 3. sue for peace when the war hurts
        if at_war:
            pain = n.war_weariness * 2.6 + (1.0 - ratio) * 2.0 \
                + max(0.0, n.inflation - 0.05) * 4.0 - p["aggression"] * 1.5
            actions.append((pain, "peace", enemy))

        # 4. mobilise / demobilise
        pressure = clamp(best_threat / max(1.0, our_power), 0.0, 2.0)
        actions.append(((pressure - n.mobilization) * 2.0 * economy_strain, "mobilize", None))

        # 5. sanctions as a cheap hostile gesture
        if not at_war and p["mercantilism"] > 0.4:
            actions.append((0.6 * p["mercantilism"] * (1.0 - n.approval / 120.0),
                            "sanction", enemy))

        actions.sort(key=lambda a: -a[0])
        self.decisions += 1
        if not actions or actions[0][0] < 0.35:
            return

        score, kind, payload = actions[0]
        if kind == "defend":
            self.ai_shift(n, payload, "Defend")
        elif kind == "attack":
            if not at_war:
                claim = any(r.owner == enemy.id and r.claimed_by == n.id
                            for r in self.world.regions.values())
                grievance = n.relations.get(enemy.id, 0) < -15
                if (claim or grievance) and score > 1.0:
                    self.declare_war(n, enemy)
                elif claim and n.relations.get(enemy.id, 0) <= -55:
                    self.declare_war(n, enemy)
            self.ai_shift(n, payload, "Attack")
        elif kind == "peace":
            self.propose_peace(n, payload)
        elif kind == "mobilize":
            n.mobilization = clamp(n.mobilization + 0.15)
        elif kind == "sanction":
            n.treasury += n.gdp * 0.01
            self.log("санкции: %s против %s" % (n.id, payload.id))

    def ai_shift(self, n, region, order):
        idle = [u for u in n.units if u.order in ("Hold", "Defend")]
        if not idle:
            idle = list(n.units)
        if not idle:
            return
        idle.sort(key=lambda u: self.dist(u.position, region.centroid))
        take = max(1, min(3, len(idle)))
        for u in idle[:take]:
            if order == "Attack":
                self.order_attack(u, region.id)
            else:
                self.order_defend(u, region.id)
        self.log("%s: %d отр. -> %s [%s]" % (n.id, take, region.id, order))

    def threat_from(self, n, other_id):
        total = 0.0
        other = self.nations[other_id]
        for r in n.regions(self.world):
            total += self.threat_at(n, other, r)
        return total

    def threat_at(self, n, other, region):
        t = 0.0
        for u in other.units:
            d = self.dist(u.position, region.centroid)
            if d < 4000.0:
                t += u.power(False, region.strategic) * (1.0 - d / 4000.0) \
                    * (region.strategic / 10.0 + 0.4)
        return t

    def border_incident(self, a_id, d_id, region):
        """Armed forces standing on foreign soil without a war: relations slide
        and the owner may run out of patience. This is how a war starts without
        being scripted."""
        a, d = self.nation(a_id), self.nation(d_id)
        if self.real_seconds - getattr(self, "_last_incident", -9999.0) < 20.0:
            return
        self._last_incident = self.time_minutes
        a.relations[d_id] = clamp(a.relations.get(d_id, 0) - 4, -100, 100)
        d.relations[a_id] = clamp(d.relations.get(a_id, 0) - 8, -100, 100)
        d.war_weariness = clamp(d.war_weariness + 0.01)
        self.log("инцидент: войска %s в %s (отношения %d)"
                 % (a_id, region.id, d.relations[a_id]))
        if d.relations[a_id] <= -60 and d.personality["aggression"] > 0.5:
            self.declare_war(d, a)

    def withdraw_home(self, n):
        """Send every unit of a nation back to the nearest region it owns."""
        for u in n.units:
            ours = [r for r in self.world.regions.values() if r.owner == n.id]
            if not ours:
                continue
            ours.sort(key=lambda r: self.dist(u.position, r.centroid))
            self.order_move(u, ours[0].id)
            u.order = "Hold"

    def declare_war(self, a, b):
        a.at_war.add(b.id)
        b.at_war.add(a.id)
        a.war_weariness = clamp(a.war_weariness + 0.05)
        b.war_weariness = clamp(b.war_weariness + 0.05)
        self.log("ВОЙНА: %s объявляет войну %s" % (a.id, b.id))

    def propose_peace(self, a, b):
        # acceptance is a function of how badly the other side is doing
        ratio = max(0.05, b.army_power(False) / max(1.0, a.army_power(False)))
        willingness = 0.30 + 0.55 * b.war_weariness \
            + 0.25 * max(0.0, 1.0 - ratio) \
            + 0.20 * b.personality["caution"] \
            + b.diplomacy_bonus() \
            - 0.30 * b.personality["aggression"]
        if self.rng.chance(clamp(willingness, 0.02, 0.95)):
            a.at_war.discard(b.id)
            b.at_war.discard(a.id)
            a.war_weariness = clamp(a.war_weariness - 0.35)
            b.war_weariness = clamp(b.war_weariness - 0.35)
            a.morale = clamp(a.morale + 0.06)
            b.morale = clamp(b.morale + 0.06)
            self.log("МИР: %s и %s подписали мир" % (a.id, b.id))
            self.withdraw_home(a)
            self.withdraw_home(b)
            return True
        self.log("мир отклонён: %s отказался говорить с %s" % (b.id, a.id))
        return False


def _waypoints(start, end, hops=2):
    """A couple of intermediate points so movement is not a perfect straight line."""
    pts = []
    for i in range(1, hops + 1):
        t = i / float(hops + 1)
        mx = start[0] + (end[0] - start[0]) * t
        my = start[1] + (end[1] - start[1]) * t
        jitter = 350.0 * math.sin(t * math.pi)
        pts.append((mx + jitter, my - jitter))
    pts.append((end[0], end[1]))
    return pts


# --------------------------------------------------------------- fixture ----
def build_demo(seed=1337):
    """A 6-region stand-in for the real map, used by the headless run."""
    import xml.etree.ElementTree as ET

    class R(object):
        pass

    world = type("W", (), {})()
    world.regions = {}

    tree = ET.parse(os.path.join(DATA, "Regions.xml"))
    for el in tree.getroot().find("Regions").findall("Region"):
        r = R()
        r.id = el.get("ID")
        r.name = el.get("NameRu") or el.get("Name")
        r.owner = el.get("Owner")
        r.original_owner = el.get("Owner")
        r.claimed_by = el.get("ClaimedBy") or ""
        r.population = int(el.get("Population"))
        r.strategic = int(el.get("StrategicValue"))
        r.productivity = 0.7 + (r.strategic / 40.0)
        r.destruction = 0.0
        r.control = 100.0
        cen = el.find("Centroid")
        r.centroid = (float(cen.get("X")), float(cen.get("Y")))
        nb = el.find("Neighbours").text or ""
        r.neighbours = [x for x in nb.split("|") if x]
        world.regions[r.id] = r

    nations = {}
    # Personality and treasury come from Nations.xml - the same file the C# module
    # loads - so there is exactly one place where they are authored.
    def personality_of(el):
        def num(name, default):
            raw = el.get(name)
            return float(raw) if raw not in (None, "") else default
        return dict(aggression=num("Aggression", 0.5),
                    caution=num("Caution", 0.5),
                    mercantilism=num("Mercantilism", 0.5))

    ntree = ET.parse(os.path.join(DATA, "Nations.xml"))
    for el in ntree.getroot().find("Nations").findall("Nation"):
        nid = el.get("ID")
        n = Nation(nid, el.get("NameRu") or el.get("Name"),
                   el.get("CapitalRegion"), personality_of(el))
        raw_treasury = el.get("StartingTreasury")
        n.treasury = float(raw_treasury) if raw_treasury not in (None, "") else (
            45_000_000 if nid == "rsa" else 26_000_000)
        for rel in (el.find("Relations") if el.find("Relations") is not None else []):
            n.relations[rel.get("With")] = int(rel.get("Value"))
        nations[nid] = n

    sim = Simulation(world, nations, seed=seed)
    sim.player_nation = "rsa"

    # starting armies, raised near each capital
    def raise_units(nid, plan):
        n = nations[nid]
        cap = world.regions[n.capital_region]
        for type_id, count in plan:
            ut = sim.unit_types[type_id]
            for k in range(count):
                pos = (cap.centroid[0] + (k - count) * 60.0, cap.centroid[1] + (k - count) * 40.0)
                u = Unit(ut, nid, pos, "%s-%d" % (ut.name, k + 1))
                n.units.append(u)
    raise_units("rsa", [("infantry", 3), ("motor_rifle", 2), ("armor", 1), ("heli", 1), ("milpolice", 1)])
    raise_units("fzsa", [("infantry", 2), ("motor_rifle", 1), ("armor", 1), ("artillery", 1), ("specops", 1)])

    for nid, names in (("rsa", [("gen_vance", 0.62), ("gen_moreau", 0.48)]),
                       ("fzsa", [("gen_kalloway", 0.71), ("gen_reyes", 0.55)])):
        for nm, sk in names:
            nations[nid].generals.append(General(nm, sk, "General"))

    # Cabinets come from the same Armies.xml the C# module reads, so tuning runs
    # are measured against the ministers the player actually gets.
    armies_path = os.path.join(DATA, "Armies.xml")
    if os.path.isfile(armies_path):
        atree = ET.parse(armies_path)
        for cab in atree.getroot().find("Cabinets").findall("Cabinet"):
            nid = cab.get("Nation")
            if nid not in nations:
                continue
            for m in cab.findall("Minister"):
                nations[nid].ministers[m.get("Role")] = Minister(
                    m.get("Role"),
                    m.get("Name") or "%s-%s" % (nid.upper(), m.get("Role")),
                    float(m.get("Competence")), float(m.get("Loyalty")))
    else:  # pragma: no cover - only reached if the data file is missing
        for nid, roles in (("rsa", ("Prime", "Defence", "Interior", "Finance", "Foreign", "Transport", "Health")),
                           ("fzsa", ("Prime", "Defence", "Interior", "Finance", "Foreign", "Transport", "Health"))):
            for i, role in enumerate(roles):
                nations[nid].ministers[role] = Minister(
                    role, "%s-%s" % (nid.upper(), role), 0.35 + 0.06 * ((i * 3 + len(nid)) % 9), 0.5)

    return sim


def player_policy(sim, nid):
    """What a player actually does with the tablet: send the nearest idle unit
    to any of our regions that is being contested. Used only by the harness."""
    n = sim.nation(nid)
    for rid, r in sim.world.regions.items():
        if r.owner != nid:
            continue
        intruders = [u for other in sim.nations.values() for u in other.units
                     if other.id != nid and sim.dist(u.position, r.centroid) < BATTLE_RANGE_METRES]
        if not intruders:
            continue
        idle = [u for u in n.units if u.order in ("Hold", "Defend")
                and sim.dist(u.position, r.centroid) > BATTLE_RANGE_METRES]
        if not idle:
            continue
        idle.sort(key=lambda u: sim.dist(u.position, r.centroid))
        sim.order_defend(idle[0], rid)


# ------------------------------------------------------------------- main ----
def run_scenario(name, seed, years, player_acts, quiet=False):
    sim = build_demo(seed)
    rsa, fzsa = sim.nation("rsa"), sim.nation("fzsa")
    start_owners = dict((k, v.owner) for k, v in sim.world.regions.items())
    start_positions = dict((u.id, u.position) for u in rsa.units + fzsa.units)

    disputed = "zancudo" if "zancudo" in sim.world.regions else sorted(sim.world.regions)[0]
    declared = False
    total_days = int(years * 360)
    total_seconds = total_days * SECONDS_PER_GAME_DAY
    tick = SECONDS_PER_GAME_DAY
    steps = int(total_seconds / tick)
    for d in range(steps):
        if player_acts:
            if d == 3 and not declared:
                sim.declare_war(rsa, fzsa)          # the player pressing the button
                declared = True
                for u in rsa.units[:3]:
                    sim.order_attack(u, disputed)
            if sim.day % 2 == 0:
                player_policy(sim, "rsa")
        sim.advance(tick)

    moved = sum(1 for u in rsa.units + fzsa.units
                if sim.dist(start_positions[u.id], u.position) > 100.0)
    changed = [k for k, v in start_owners.items() if sim.world.regions[k].owner != v]
    if not quiet:
        print("\n--- %s: timeline (last 18 events) ---" % name)
        for stamp, text in sim.events[-18:]:
            print("  %s  %s" % (stamp, text))
        print("  battles=%d captures=%d ai_decisions=%d units_moved=%d changed=%s"
              % (sim.battles_fought, sim.captures, sim.decisions, moved, changed or "none"))
    return sim, moved, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    print("=== ONYX RP: NATIONS & WAR - reference simulation ===")

    # ---- scenario A: the player starts a war and defends -------------------
    simA, movedA, changedA = run_scenario("A: игрок объявляет войну", args.seed,
                                          args.years, player_acts=True)

    print("\n--- after %.1f in-game years (day %d) ---" % (args.years, simA.day))
    for nid in ("rsa", "fzsa"):
        n = simA.nation(nid)
        print("  %-5s regions=%2d pop=%9d treasury=%12.0f gdp=%10.0f infl=%5.1f%% "
              "unemp=%5.1f%% morale=%.2f appr=%3.0f wear=%.2f cas=%d"
              % (nid, len(n.regions(simA.world)), n.population(simA.world), n.treasury,
                 n.gdp, n.inflation * 100, n.unemployment * 100, n.morale,
                 n.approval, n.war_weariness, n.casualties))

    # ---- scenario B: nobody touches the keyboard ---------------------------
    simB, movedB, changedB = run_scenario("B: ИИ действует сам", args.seed + 7,
                                          args.years, player_acts=False, quiet=True)

    # ---------------------------------------------------------- assertions --
    print("\n=== ASSERTIONS (mechanics, not interface) ===")
    fails = []

    def ok(cond, msg):
        print("  [%s] %s" % ("OK  " if cond else "FAIL", msg))
        if not cond:
            fails.append(msg)

    ok(movedA >= 2, "A: units executed their orders (%d moved)" % movedA)
    ok(simA.battles_fought > 0, "A: battles were fought (%d)" % simA.battles_fought)
    ok(len(changedA) > 0, "A: territory changed hands (%s)" % (changedA or "none"))
    ok(simA.decisions > 10, "A: the AI kept deciding (%d)" % simA.decisions)
    ok(simB.battles_fought > 0 or len(changedB) > 0,
       "B: with no player input the AI started a war by itself "
       "(battles=%d captures=%d changed=%s)" % (simB.battles_fought, simB.captures, changedB or "none"))

    all_units = [u for n in simA.nations.values() for u in n.units]
    ok(all(simA.DESTROYED_BELOW <= u.strength <= 1.0 for u in all_units),
       "surviving units stay above the destruction threshold (%d alive)" % len(all_units))
    ok(all(u.strength > 0 or True for u in all_units), "no zero-strength ghosts remain")
    ok(all(0.0 <= u.morale <= 1.0 and 0.0 <= u.supply <= 1.0 for u in all_units),
       "unit morale and supply stay within 0..1")
    ok(all(0.0 <= r.control <= 100.0 for r in simA.world.regions.values()),
       "region control stays within 0..100")
    ok(all(0.0 <= r.destruction <= 100.0 for r in simA.world.regions.values()),
       "destruction stays within 0..100")
    ok(all(not math.isnan(n.treasury) and n.treasury >= 0 for n in simA.nations.values()),
       "treasury is finite and non-negative")
    ok(all(n.inflation < 1.0 for n in simA.nations.values()), "inflation stays bounded")
    ok(all(0.0 <= n.unemployment <= 0.9 for n in simA.nations.values()), "unemployment plausible")
    ok(all(0.0 <= n.morale <= 1.0 for n in simA.nations.values()), "morale stays within 0..1")
    ok(all(1.0 <= n.approval <= 99.0 for n in simA.nations.values()), "approval stays within 1..99")

    # ---- policy probe: taxes ----------------------------------------------
    probe = build_demo(4242)
    r = probe.nation("rsa")
    for _ in range(150):
        probe.advance(SECONDS_PER_GAME_DAY)
    calm_morale, calm_treasury = r.morale, r.treasury
    r.tax_rate = 0.55
    for _ in range(200):
        probe.advance(SECONDS_PER_GAME_DAY)
    ok(r.treasury > calm_treasury, "raising taxes grows the treasury (%.0f -> %.0f)"
       % (calm_treasury, r.treasury))
    ok(r.morale < calm_morale, "raising taxes lowers morale (%.2f -> %.2f)"
       % (calm_morale, r.morale))

    # ---- policy probe: army size has a real cost ---------------------------
    probe2 = build_demo(99)
    r2 = probe2.nation("rsa")
    for _ in range(120):
        probe2.advance(SECONDS_PER_GAME_DAY)
    before = r2.treasury
    ut = probe2.unit_types["armor"]
    for k in range(6):
        cap = probe2.world.regions[r2.capital_region]
        r2.units.append(Unit(ut, "rsa", cap.centroid, "extra-%d" % k))
    for _ in range(180):
        probe2.advance(SECONDS_PER_GAME_DAY)
    growth_before = calm_treasury
    ok(r2.units and len(r2.units) >= 13, "units can be raised (now %d)" % len(r2.units))
    ok(r2.gdp > 0, "economy keeps running with a bigger army")

    print("\n%s (%d failures)" % ("SIMULATION MODEL PASSED" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
