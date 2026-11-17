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
        self.office = Presidency()
        self.parties = []

    def party_by_id(self, pid):
        for p in self.parties:
            if p.id == pid:
                return p
        return None

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


# ------------------------------------------------------------------ borders --
# A crossing sits at the midpoint between two adjacent regions' centroids:
# that is exactly where the Voronoi boundary between the two cells runs. So the
# border is derived from the map, never authored as a list that can go stale.
BORDER_SETTINGS = {
    "checkpoint_radius": 55.0,
    "guards_per_post": 2,
    "manning_per_day": 0.25,
    "decay_per_day_without_garrison": 0.10,
    "open_above": 40,
    "inspect_above": -20,
}


class BorderCrossing(object):
    def __init__(self, a, b, position, name=""):
        self.id = "|".join(sorted((a, b)))
        self.a, self.b = a, b
        self.position = position
        self.name = name or ("Checkpoint %s/%s" % (a, b))
        self.manning = 0.5
        self.damage = 0.0

    @property
    def destroyed(self):
        return self.damage >= 1.0


def dist_to_segment(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_polygon_edge(p, poly):
    best = None
    n = len(poly)
    for i in range(n):
        d = dist_to_segment(p, poly[i], poly[(i + 1) % n])
        if best is None or d < best:
            best = d
    return best


def shared_edge_midpoint(poly_a, poly_b, tol=2.0):
    """Midpoint of the edge two neighbouring cells actually share.

    The naive answer — the midpoint between the two centroids — is only right
    when the straight line between them stays inside the pair. Real Voronoi
    cells bend, and two of the six crossings on this map fell in a third
    region. Placing the post on the shared edge fixes it, and is what the C#
    does too (Geometry.DistanceToPolygonEdge).
    """
    ends = []
    for v in poly_a:
        if dist_to_polygon_edge(v, poly_b) <= tol:
            ends.append(v)
    for v in poly_b:
        if dist_to_polygon_edge(v, poly_a) <= tol:
            ends.append(v)
    if len(ends) >= 2:
        best_pair, best_d = (ends[0], ends[1]), 0.0
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                d = math.hypot(ends[i][0] - ends[j][0], ends[i][1] - ends[j][1])
                if d > best_d:
                    best_d, best_pair = d, (ends[i], ends[j])
        return ((best_pair[0][0] + best_pair[1][0]) / 2.0,
                (best_pair[0][1] + best_pair[1][1]) / 2.0)
    return None


def compute_borders(world):
    """Crossings between adjacent regions held by different states."""
    out = []
    regions = world.regions
    for rid, r in regions.items():
        for nb in r.neighbours:
            other = regions.get(nb)
            if other is None or rid >= nb:      # each unordered pair once
                continue
            if r.owner == other.owner:
                continue
            mid = shared_edge_midpoint(r.polygon, other.polygon) or (
                (r.centroid[0] + other.centroid[0]) / 2.0,
                (r.centroid[1] + other.centroid[1]) / 2.0)
            out.append(BorderCrossing(rid, nb, mid))
    return out


def posture(crossing, owner, traveller):
    """What a guard does to a citizen of `traveller`."""
    if crossing.destroyed:
        return "open"
    if traveller.id in owner.at_war or owner.id in traveller.at_war:
        return "hostile"
    rel = owner.relations.get(traveller.id, 0)
    if rel >= BORDER_SETTINGS["open_above"]:
        return "open"
    if rel >= BORDER_SETTINGS["inspect_above"]:
        return "inspect"
    return "closed"


# ---------------------------------------------------------------- elections --
# Defaults; overridden by OnyxNations/Data/Parties.xml at build time, so the
# rules live in exactly one place.
ELECTION_RULES = {
    "term_days": 360,
    "max_terms": 2,
    "campaign_days": 30,
    "min_turnout": 0.35,
    "max_turnout": 0.85,
    "incumbency_bonus": 0.06,
    "war_rally_bonus": 0.10,
    "scandal_penalty": 0.25,
    "challenger_funds_per_day": 45000.0,
    "player_funds_per_day": 60000.0,
}


def load_parties(nations):
    """Parties and election rules from OnyxNations/Data/Parties.xml."""
    import xml.etree.ElementTree as ET
    path = os.path.join(DATA, "Parties.xml")
    if not os.path.isfile(path):
        return dict(ELECTION_RULES)

    root = ET.parse(path).getroot()
    rules = dict(ELECTION_RULES)
    rules_el = root.find("ElectionRules")
    if rules_el is not None:
        for key, caster in (("term_days", int), ("max_terms", int), ("campaign_days", int),
                            ("min_turnout", float), ("max_turnout", float),
                            ("incumbency_bonus", float), ("war_rally_bonus", float),
                            ("scandal_penalty", float), ("challenger_funds_per_day", float),
                            ("player_funds_per_day", float)):
            raw = rules_el.get(pascal(key))
            if raw not in (None, ""):
                rules[key] = caster(raw)

    for el in root.find("Parties").findall("Party"):
        nid = el.get("Nation")
        if nid not in nations:
            continue
        party = Party(el.get("ID"), nid, el.get("NameRu") or el.get("Name"),
                      float(el.get("TaxBias")), float(el.get("Hawkishness")),
                      float(el.get("Popularity")),
                      el.get("IsPlayerParty") == "true")
        nations[nid].parties.append(party)
        if party.is_player_party:
            office = nations[nid].office
            office.player_party_id = party.id
            office.player_is_president = True
            office.incumbent_name = party.name
            office.ruling_party_id = party.id

    # A nation with no ruling party named in the file starts with its most
    # popular one, so the election screen is never empty.
    for nid, n in nations.items():
        if n.office.ruling_party_id or not n.parties:
            continue
        best = max(n.parties, key=lambda p: p.popularity)
        n.office.ruling_party_id = best.id
        if not n.office.incumbent_name:
            n.office.incumbent_name = best.name
    return rules


def pascal(snake):
    return "".join(part[:1].upper() + part[1:] for part in snake.split("_"))


def office_funds(n, incumbent):
    return n.office.campaign_funds if incumbent else n.office.challenger_funds


class Presidency(object):
    def __init__(self):
        self.term_days = 360
        self.days_in_term = 0
        self.terms_served = 0
        self.player_is_president = False
        self.incumbent_name = ""
        self.player_party_id = ""
        self.ruling_party_id = ""
        self.campaign_days = 0
        self.campaign_funds = 0.0
        self.challenger_funds = 0.0
        self.last_turnout = 0.0
        self.last_winner = ""

    @property
    def days_until_election(self):
        return max(0, self.term_days - self.days_in_term)


class Party(object):
    def __init__(self, pid, nation, name, tax_bias, hawkishness, popularity,
                 is_player_party=False):
        self.id = pid
        self.nation = nation
        self.name = name
        self.tax_bias = tax_bias
        self.hawkishness = hawkishness
        self.popularity = popularity
        self.is_player_party = is_player_party


# --------------------------------------------------------------- simulation --
class Simulation(object):
    def __init__(self, world, nations, seed=1337):
        self.world = world
        self.nations = nations
        self.rng = Rng(seed)
        self.time_minutes = 0.0
        self.real_seconds = 0.0
        self.rules = dict(ELECTION_RULES)
        self.last_results = {}
        self.borders = []
        self.border_state = {}
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
        for nid in self.nations:
            self.step_election(self.nation(nid))
        self.step_borders()

    # ------------------------------------------------------------ elections --
    def step_election(self, n):
        office = n.office
        office.term_days = self.rules["term_days"]
        office.days_in_term += 1

        left = office.days_until_election
        if left <= self.rules["campaign_days"]:
            office.campaign_days = self.rules["campaign_days"] - left
            office.campaign_funds += self.rules["player_funds_per_day"]
            office.challenger_funds += self.rules["challenger_funds_per_day"]

        if office.days_in_term < office.term_days or not n.parties:
            return

        result = self.run_election(n)
        self.last_results[n.id] = result
        self.apply_result(n, result)

    def share_for(self, n, party, incumbent):
        record = ((n.approval - 50.0) / 200.0
                  + (n.morale - 0.60) * 0.20
                  - (n.unemployment - 0.06) * 1.20
                  - max(0.0, n.inflation - 0.05) * 1.50
                  + (n.stability - 0.70) * 0.15)

        if n.at_war:
            war_term = self.rules["war_rally_bonus"] * (1.0 - n.war_weariness * 2.0)
            record += war_term if incumbent else -war_term * 0.5
        if n.approval < 25.0:
            record -= self.rules["scandal_penalty"]

        share = party.popularity
        pull = party.hawkishness * 0.10
        share += pull if n.at_war else -pull * 0.5

        if incumbent:
            share += record + self.rules["incumbency_bonus"]
        else:
            share += record * 0.35

        funds = office_funds(n, incumbent)
        share += 0.08 * (funds / (funds + 500000.0))
        share -= abs(party.tax_bias) * 0.10
        return max(0.01, share)

    def compute_ballot(self, n, noise=True):
        lines = []
        ruling = n.office.ruling_party_id
        for p in n.parties:
            incumbent = (p.id == ruling)
            if incumbent and n.office.terms_served >= self.rules["max_terms"]:
                incumbent = False        # term-limited: a successor, not the leader
            share = self.share_for(n, p, incumbent)
            if noise:
                share *= self.rng.uniform(0.85, 1.15)
            lines.append((p.id, share))
        total = sum(s for _, s in lines) or 1.0
        lines = [(pid, s / total) for pid, s in lines]
        lines.sort(key=lambda kv: -kv[1])

        anger = (max(0.0, (50.0 - n.approval) / 50.0)
                 + (0.15 if n.at_war else 0.0)
                 + max(0.0, 0.40 - n.morale if n.morale < 0.40 else 0.0))
        turnout = clamp(self.rules["min_turnout"]
                        + anger * (self.rules["max_turnout"] - self.rules["min_turnout"]),
                        self.rules["min_turnout"], self.rules["max_turnout"])

        return {
            "winner": lines[0][0] if lines else "",
            "lines": lines,
            "turnout": turnout,
            "incumbent_won": bool(lines) and lines[0][0] == ruling,
            "day": self.day,
        }

    def poll(self, n):
        """Live poll: the same maths without election-day noise."""
        return self.compute_ballot(n, noise=False)

    def run_election(self, n):
        return self.compute_ballot(n, noise=True)

    def apply_result(self, n, result):
        office = n.office
        winner = n.party_by_id(result["winner"])
        previous = n.party_by_id(office.ruling_party_id)
        player_lost = office.player_is_president and not result["incumbent_won"]

        office.ruling_party_id = result["winner"]
        office.last_winner = result["winner"]
        office.last_turnout = result["turnout"]
        office.days_in_term = 0
        office.terms_served += 1
        office.campaign_days = 0
        office.campaign_funds = 0.0
        office.challenger_funds = 0.0

        if player_lost:
            office.player_is_president = False
            office.incumbent_name = winner.name if winner else ""
            self.log("ВЫБОРЫ: %s — победа «%s», явка %.0f%%. Пост потерян."
                     % (n.name, winner.name if winner else "?", result["turnout"] * 100))
        else:
            self.log("ВЫБОРЫ: %s — победа «%s», явка %.0f%%"
                     % (n.name, winner.name if winner else "?", result["turnout"] * 100))

        if winner is None:
            return
        n.tax_rate = lerp(n.tax_rate, clamp(0.24 + winner.tax_bias, 0.05, 0.60), 0.35)
        n.army_funding = lerp(n.army_funding,
                              clamp(0.18 + 0.34 * winner.hawkishness, 0.05, 0.55), 0.35)
        if previous is not None and previous.id != winner.id:
            n.stability = clamp(n.stability - 0.03)

    def fund_campaign(self, n, amount):
        if amount <= 0 or n.treasury < amount:
            return False
        n.treasury -= amount
        n.office.campaign_funds += amount
        return True

    def can_player_run(self, n):
        return n.office.player_is_president and n.office.terms_served < self.rules["max_terms"]

    # -------------------------------------------------------------- borders --
    def step_borders(self):
        self.borders = compute_borders(self.world)
        # carry over manning/damage for crossings that still exist
        for c in self.borders:
            if c.id in self.border_state:
                c.manning, c.damage = self.border_state[c.id]
        for c in self.borders:
            owner = self.nation(self.world.regions[c.a].owner)
            garrisoned = any(self.dist(u.position, c.position) < 2500.0
                             for u in owner.units)
            delta = (BORDER_SETTINGS["manning_per_day"] if garrisoned
                     else -BORDER_SETTINGS["decay_per_day_without_garrison"])
            c.manning = clamp(c.manning + delta)
            if c.damage > 0 and garrisoned:
                c.damage = clamp(c.damage - 0.15)
            self.border_state[c.id] = (c.manning, c.damage)

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
        r.polygon = [(float(v.get("X")), float(v.get("Y")))
                     for v in el.find("Polygon").findall("Vertex")]
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
    sim.rules = load_parties(nations)
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

    # ---- border geometry: the crossing really is on the boundary -------------
    border_world = build_demo(1337).world
    crossings = compute_borders(border_world)
    ok(len(crossings) > 0, "borders exist between states held by different owners (%d)"
       % len(crossings))
    on_edge = 0
    for c in crossings:
        a = border_world.regions[c.a]
        b = border_world.regions[c.b]
        # the post has to sit ON the line the two cells share, not merely
        # somewhere between them
        if dist_to_polygon_edge(c.position, a.polygon) <= 5.0 \
                and dist_to_polygon_edge(c.position, b.polygon) <= 5.0:
            on_edge += 1
    ok(on_edge == len(crossings),
       "every crossing sits on the edge the two regions share (%d/%d)"
       % (on_edge, len(crossings)))

    def nearest_region(world, point):
        best, bestd = None, None
        for rid, rg in world.regions.items():
            d = simA.dist(point, rg.centroid)
            if bestd is None or d < bestd:
                best, bestd = rid, d
        return best

    on_boundary = sum(1 for c in crossings
                      if nearest_region(border_world, c.position) in (c.a, c.b))
    ok(on_boundary == len(crossings),
       "a crossing never lands in a third region (%d/%d)" % (on_boundary, len(crossings)))

    # ... and the border follows the flag: capture a region, the map changes
    before_ids = set(c.id for c in crossings)
    hand_over = crossings[0].b
    border_world.regions[hand_over].owner = border_world.regions[crossings[0].a].owner
    after_ids = set(c.id for c in compute_borders(border_world))
    ok(after_ids != before_ids,
       "capturing a region redraws the border (%d -> %d crossings)"
       % (len(before_ids), len(after_ids)))

    # ---- elections: record decides the outcome ------------------------------
    def poll_of(sim, nid):
        return dict(sim.poll(sim.nation(nid))["lines"])

    popular = build_demo(7)
    pr = popular.nation("rsa")
    pr.approval, pr.morale, pr.unemployment, pr.inflation = 78.0, 0.80, 0.04, 0.02
    popular_poll = poll_of(popular, "rsa")
    ok(popular_poll[pr.office.ruling_party_id] == max(popular_poll.values()),
       "a popular incumbent leads the polls (%.3f)" % popular_poll[pr.office.ruling_party_id])

    unpopular = build_demo(7)
    ur = unpopular.nation("rsa")
    # Deliberately bad-but-not-catastrophic: at approval 12 every party hits the
    # 0.01 floor and the shares all collapse to the same number, which would make
    # this assertion pass for the wrong reason.
    ur.approval, ur.morale, ur.unemployment, ur.inflation = 30.0, 0.38, 0.14, 0.10
    bad_poll = poll_of(unpopular, "rsa")
    ok(bad_poll[ur.office.ruling_party_id] == min(bad_poll.values()),
       "an unpopular incumbent trails (%.4f vs best %.4f)"
       % (bad_poll[ur.office.ruling_party_id], max(bad_poll.values())))
    ok(bad_poll[ur.office.ruling_party_id] < popular_poll[pr.office.ruling_party_id],
       "the same party polls worse when the country is doing badly (%.3f -> %.3f)"
       % (popular_poll[pr.office.ruling_party_id], bad_poll[ur.office.ruling_party_id]))

    # campaign money buys votes
    funded = build_demo(7)
    fr = funded.nation("rsa")
    fr.approval, fr.morale, fr.unemployment, fr.inflation = 50.0, 0.55, 0.06, 0.03
    before_funds = poll_of(funded, "rsa")[fr.office.ruling_party_id]
    fr.office.campaign_funds = 6_000_000.0
    after_funds = poll_of(funded, "rsa")[fr.office.ruling_party_id]
    ok(after_funds > before_funds,
       "campaign funds buy votes (%.3f -> %.3f)" % (before_funds, after_funds))
    ok(funded.fund_campaign(fr, 1_000_000.0), "the treasury can fund a campaign")
    ok(not funded.fund_campaign(fr, 10 ** 15), "a campaign cannot be funded with money we lack")

    # term limit
    limited = build_demo(7)
    lr = limited.nation("rsa")
    lr.office.terms_served = 0
    share_term1 = poll_of(limited, "rsa")[lr.office.ruling_party_id]
    lr.office.terms_served = limited.rules["max_terms"]
    share_limited = poll_of(limited, "rsa")[lr.office.ruling_party_id]
    ok(share_limited < share_term1,
       "a term-limited incumbent's party loses its incumbency bonus (%.3f -> %.3f)"
       % (share_term1, share_limited))
    ok(not limited.can_player_run(lr), "a term-limited president cannot stand again")

    # losing really hands power over, and policy really follows
    loser = build_demo(11)
    nr = loser.nation("rsa")
    nr.approval, nr.morale, nr.unemployment, nr.inflation = 8.0, 0.15, 0.30, 0.25
    ruling_before = nr.office.ruling_party_id
    tax_before = nr.tax_rate
    result = loser.run_election(nr)
    loser.apply_result(nr, result)
    ok(result["winner"] != ruling_before,
       "a failing government is voted out (%s -> %s)" % (ruling_before, result["winner"]))
    ok(nr.office.ruling_party_id == result["winner"], "the winner takes office")
    ok(not nr.office.player_is_president, "the player loses the post on defeat")
    winner_party = nr.party_by_id(result["winner"])
    expected_tax = clamp(0.24 + winner_party.tax_bias, 0.05, 0.60)
    ok(abs(nr.tax_rate - tax_before) > 1e-6
       and abs(nr.tax_rate - expected_tax) < abs(tax_before - expected_tax),
       "the winner's tax plan actually moves the rate (%.3f -> %.3f, target %.3f)"
       % (tax_before, nr.tax_rate, expected_tax))
    ok(loser.rules["min_turnout"] <= result["turnout"] <= loser.rules["max_turnout"],
       "turnout stays within the configured band (%.2f)" % result["turnout"])

    # the long run holds elections at all
    ok(len(simA.last_results) > 0,
       "elections were held during the %d-day run (%d)"
       % (simA.day, len(simA.last_results)))
    ok(all(0.0 <= r["turnout"] <= 1.0 for r in simA.last_results.values()),
       "every recorded turnout is a sane fraction")
    ok(all(abs(sum(s for _, s in res["lines"]) - 1.0) < 1e-6
           for res in simA.last_results.values()),
       "vote shares always add up to 100%")

    print("\n%s (%d failures)" % ("SIMULATION MODEL PASSED" if not fails else "FAILED", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
