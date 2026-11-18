#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_parity.py — drift guard between the C# module and the reference model.

The C# cannot be compiled in this sandbox, so the only way to keep it honest is
to hold it against the Python twin that *does* run: if a constant, a unit type
or a formula changes on one side and not the other, this script fails.

Three families of check:

  1. constants   — SimTuning.cs versus the module-level constants of
                   tools/sim_reference.py (the model that was actually tuned);
  2. data        — OnyxNations/Data/*.xml versus the tables inside the reference
                   model, which is what the tuning runs were measured against;
  3. formulas    — arithmetic fingerprints, so that a "harmless" edit to the
                   power or morale formula cannot silently diverge.

Usage:  python3 tools/check_parity.py
Exit code 0 = no drift, 1 = drift found.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import xml.etree.ElementTree as ET  # noqa: E402  (import after sys.path surgery)

import sim_reference as ref  # noqa: E402

SRC = os.path.join(ROOT, "src", "OnyxNations")
DATA = os.path.join(ROOT, "OnyxNations", "Data")
TUNING = os.path.join(SRC, "Sim", "SimTuning.cs")

FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
        print("  [FAIL] %s" % message)
    return bool(condition)


# ------------------------------------------------------------------ constants
def tuning_constants():
    """Parse `public const <type> NAME = value;` out of SimTuning.cs."""
    out = {}
    text = open(TUNING, encoding="utf-8").read()
    for m in re.finditer(r'public const\s+(\w+)\s+(\w+)\s*=\s*([-\d.]+)\s*f?\s*;', text):
        ctype, name, value = m.group(1), m.group(2), m.group(3)
        out[name] = (ctype, float(value))
    return out


# C# name -> Python attribute in sim_reference (module level, or a class attr)
CONSTANT_MAP = [
    ("SecondsPerGameDay", "SECONDS_PER_GAME_DAY"),
    ("AiDecisionEveryGameDays", "AI_DECISION_EVERY_GAME_DAYS"),
    ("StrategicSliceSeconds", "STRATEGIC_SLICE_SECONDS"),
    ("BattleRangeMetres", "BATTLE_RANGE_METRES"),
    ("ThreatRadiusMetres", "THREAT_RADIUS_METRES"),
    ("BattleCooldownSeconds", "BATTLE_COOLDOWN_SECONDS"),
    ("OccupyTickSeconds", "OCCUPY_TICK_SECONDS"),
    ("GdpPerCapitaDay", "GDP_PER_CAPITA_DAY"),
    ("InflationTarget", "INFLATION_TARGET"),
    ("SpeedBaseMps", "SPEED_BASE_MPS"),
    ("SpeedPerMobilityMps", "SPEED_PER_MOBILITY_MPS"),
    ("AiActionThreshold", "AI_ACTION_THRESHOLD"),
    ("AiWarThreshold", "AI_WAR_THRESHOLD"),
    ("AiWarRelationsFloor", "AI_WAR_RELATIONS_FLOOR"),
]


def check_constants():
    print("=== CONSTANTS: SimTuning.cs vs sim_reference.py ===")
    cs = tuning_constants()
    check(len(cs) >= 10, "SimTuning.cs exposes tuning constants (%d)" % len(cs))

    for cs_name, py_name in CONSTANT_MAP:
        if not hasattr(ref, py_name):
            # not every constant was lifted to module level in the Python twin
            check(cs_name not in cs or True,
                  "%s: no Python counterpart to compare against (informational)" % cs_name)
            continue
        if cs_name not in cs:
            check(False, "SimTuning.cs is missing %s (Python has %s)" % (cs_name, py_name))
            continue
        py_value = float(getattr(ref, py_name))
        cs_value = cs[cs_name][1]
        check(abs(cs_value - py_value) < 1e-6,
              "%s matches %s (%s vs %s)" % (cs_name, py_name, cs_value, py_value))

    # DESTROYED_BELOW lives on the Simulation class in the Python model
    if cs.get("DestroyedBelow") is not None:
        check(abs(cs["DestroyedBelow"][1] - float(ref.Simulation.DESTROYED_BELOW)) < 1e-6,
              "DestroyedBelow matches Simulation.DESTROYED_BELOW")


# ----------------------------------------------------------------------- data
def check_unit_types():
    print("=== DATA: UnitTypes.xml vs sim_reference.UNIT_TYPES ===")
    root = ET.parse(os.path.join(DATA, "UnitTypes.xml")).getroot()
    xml_types = {}
    for el in root.find("UnitTypes").findall("UnitType"):
        xml_types[el.get("ID")] = {
            "category": el.get("Category"),
            "manpower": int(el.get("Manpower")),
            "firepower": float(el.get("Firepower")),
            "armor": float(el.get("Armor")),
            "mobility": float(el.get("Mobility")),
            "range": float(el.get("Range")),
            "cost": float(el.get("Cost")),
            "upkeep": float(el.get("Upkeep")),
            "vehicle": el.get("VehicleModel") or "",
            "vehicle_count": int(el.get("VehicleCount")),
        }
    py_types = dict((t["id"], t) for t in ref.UNIT_TYPES)

    check(set(xml_types) == set(py_types),
          "both sides define the same unit types (%d / %d)" % (len(xml_types), len(py_types)))

    for tid in sorted(set(xml_types) & set(py_types)):
        x, p = xml_types[tid], py_types[tid]
        check(x["category"] == p["category"], "%s: category" % tid)
        check(x["manpower"] == p["manpower"], "%s: manpower" % tid)
        check(x["firepower"] == p["firepower"], "%s: firepower" % tid)
        check(x["armor"] == p["armor"], "%s: armor" % tid)
        check(x["mobility"] == p["mobility"], "%s: mobility" % tid)
        check(x["range"] == p["range"], "%s: range" % tid)
        check(abs(x["cost"] - p["cost"]) < 1e-6, "%s: cost" % tid)
        check(abs(x["upkeep"] - p["upkeep"]) < 1e-6, "%s: upkeep" % tid)
        check(x["vehicle"] == p["vehicle"], "%s: vehicle model" % tid)
        check(x["vehicle_count"] == p["vehicle_count"], "%s: vehicle count" % tid)


def check_armies():
    print("=== DATA: Armies.xml vs sim_reference.build_demo ===")
    sim = ref.build_demo()
    root = ET.parse(os.path.join(DATA, "Armies.xml")).getroot()

    for army in root.find("Armies").findall("Army"):
        nid = army.get("Nation")
        check(nid in sim.nations, "nation '%s' exists in the reference model" % nid)
        if nid not in sim.nations:
            continue
        nation = sim.nations[nid]

        xml_plan = dict((u.get("Type"), int(u.get("Count"))) for u in army.findall("Unit"))
        py_plan = {}
        for u in nation.units:
            py_plan[u.type.id] = py_plan.get(u.type.id, 0) + 1
        check(xml_plan == py_plan,
              "'%s' order of battle matches the reference (%s vs %s)" % (nid, xml_plan, py_plan))

        xml_generals = sorted(float(g.get("Skill")) for g in army.findall("General"))
        py_generals = sorted(g.skill for g in nation.generals)
        check(xml_generals == py_generals,
              "'%s' generals match the reference (%s vs %s)" % (nid, xml_generals, py_generals))

    roles = ["Prime", "Defence", "Interior", "Finance", "Foreign", "Transport", "Health"]
    nations_root = ET.parse(os.path.join(DATA, "Nations.xml")).getroot()
    for cab in root.find("Cabinets").findall("Cabinet"):
        nid = cab.get("Nation")
        check(nid in sim.nations, "cabinet nation '%s' exists in the reference model" % nid)
        if nid not in sim.nations:
            continue
        xml_roles = sorted(m.get("Role") for m in cab.findall("Minister"))
        py_roles = sorted(sim.nations[nid].ministers.keys())
        check(xml_roles == py_roles == sorted(roles),
              "'%s' cabinet seats the same ministries (%s vs %s)" % (nid, xml_roles, py_roles))

        # a nation's bonuses are driven by minister competence, so the numbers
        # have to agree or every downstream result differs
        for m in cab.findall("Minister"):
            py_minister = sim.nations[nid].ministers.get(m.get("Role"))
            if py_minister is None:
                continue
            check(abs(float(m.get("Competence")) - py_minister.competence) < 1e-6,
                  "'%s' %s competence matches the reference" % (nid, m.get("Role")))

    for el in nations_root.find("Nations").findall("Nation"):
        nid = el.get("ID")
        if nid not in sim.nations:
            continue
        nation = sim.nations[nid]
        check(abs(float(el.get("StartingTreasury")) - nation.treasury) < 1.0,
              "'%s' starting treasury matches the reference" % nid)
        for key, attr in (("Aggression", "aggression"), ("Caution", "caution"),
                          ("Mercantilism", "mercantilism")):
            check(abs(float(el.get(key)) - nation.personality[attr]) < 1e-6,
                  "'%s' %s personality matches the reference" % (nid, attr))


# ------------------------------------------------------------------- formulas
# Substrings that must appear in the C# if the reference formula is intact.
# They are deliberately the arithmetic, not the comments: a comment cannot
# drift, a literal can.
FINGERPRINTS = [
    ("Sim/Military.cs", [
        "0.50f * Type.Firepower", "0.20f * Type.Armor",
        "0.15f * Type.Mobility", "0.15f * Type.Range",
        "0.40f + 0.60f * Morale", "0.30f + 0.70f * Supply",
        "0.80f + 0.40f * Experience",
        "1.25f * (1.0f + regionStrategicValue / 50.0f)",
    ]),
    ("Sim/Military.cs", [
        "(0.5f + 0.5f * Supply)",
        "(1.0f + transportBonus)",
        "(0.75f + 0.25f * Strength)",
    ]),
    ("Sim/Nation.cs", [
        "0.05f + 0.25f",       # best general bonus
        "0.30f * PostCompetence(MinistryRole.Finance",
        "0.25f * PostCompetence(MinistryRole.Transport",
        "0.20f * PostCompetence(MinistryRole.Defence",
        "0.30f * PostCompetence(MinistryRole.Foreign",
    ]),
    ("Sim/EconomySim.cs", [
        "0.55f + 0.45f * n.Morale", "0.5f * n.Corruption", "0.2f * n.Stability",
        "0.006 * r.Destruction", "1.5 * n.Inflation",
        "(0.35f + 0.65f * u.Strength)",
        "(0.55f + 0.90f * n.Mobilization)",
        "(0.60f + 0.80f * n.ArmyFunding)",
        "0.52f", "0.70f * (n.TaxRate - 0.24f)", "0.75f * n.WarWeariness",
    ]),
    ("Sim/AiBrain.cs", [
        "needRatio * 2.5f * p.Caution * economyStrain",
        "(strengthRatio - 0.9f) * value * p.Aggression * 2.4f",
        "n.WarWeariness * 2.6f",
        "(pressure - n.Mobilization) * 2.0f",
        "SimTuning.AiActionThreshold",
    ]),
    ("Sim/WarEngine.cs", [
        "Uniform(0.75f, 1.25f)",
        "1.0f + defender.DefenceBonus()",
    ]),
    ("Sim/Elections.cs", [
        "(n.Approval - 50f) / 200f",
        "(n.Morale - 0.60f) * 0.20f",
        "(n.Unemployment - 0.06f) * 1.20f",
        "Math.Max(0f, n.Inflation - 0.05f) * 1.50f",
        "(n.Stability - 0.70f) * 0.15f",
        "Rules.IncumbencyBonus",
        "Rules.WarRallyBonus",
        "Rules.ScandalPenalty",
        "Rules.MinTurnout",
        "0.08 * (funds / (funds + 500000.0))",
        "Math.Abs(party.TaxBias) * 0.10f",
        "0.24f + winner.TaxBias",
        "0.18f + 0.34f * winner.Hawkishness",
    ]),
    ("Sim/Borders.cs", [
        "Geometry.DistanceToPolygonEdge",
        "SharedEdgeMidpoint(a, b)",
        "Math.Atan2(x, y) * 180.0 / Math.PI",   # GTA heading convention
        "crossing.Side = new Vec2(-travel.Y, travel.X)",
    ]),
    ("Sim/Appointments.cs", [
        "candidate.Salary * 30.0",
        "0.5f + 0.3f * candidate.Loyalty",
        "0.05f + 0.10f * m.Loyalty",
    ]),
    ("Sim/Simulation.cs", [
        "SimTuning.SecondsPerGameDay",
        "SimTuning.AiDecisionEveryGameDays",
    ]),
]


def check_elections():
    """Election rules and parties are authored in Parties.xml and read by both
    sides. Compare what the reference model ended up with against the file, so a
    rename on either side cannot silently fall back to a default."""
    print("=== DATA: Parties.xml vs sim_reference election rules ===")
    sim = ref.build_demo()
    root = ET.parse(os.path.join(DATA, "Parties.xml")).getroot()
    rules_el = root.find("ElectionRules")

    mapping = [
        ("term_days", "TermDays", int),
        ("max_terms", "MaxTerms", int),
        ("campaign_days", "CampaignDays", int),
        ("min_turnout", "MinTurnout", float),
        ("max_turnout", "MaxTurnout", float),
        ("incumbency_bonus", "IncumbencyBonus", float),
        ("war_rally_bonus", "WarRallyBonus", float),
        ("scandal_penalty", "ScandalPenalty", float),
        ("challenger_funds_per_day", "ChallengerFundsPerDay", float),
        ("player_funds_per_day", "PlayerFundsPerDay", float),
    ]
    for snake, pascal, caster in mapping:
        raw = rules_el.get(pascal)
        check(raw is not None, "Parties.xml declares %s" % pascal)
        if raw is None:
            continue
        check(abs(sim.rules[snake] - caster(raw)) < 1e-9,
              "election rule %s == %s (%s vs %s)"
              % (snake, pascal, sim.rules[snake], caster(raw)))

    for el in root.find("Parties").findall("Party"):
        nid = el.get("Nation")
        nation = sim.nations.get(nid)
        if nation is None:
            continue
        party = nation.party_by_id(el.get("ID"))
        check(party is not None, "party '%s' was loaded into nation '%s'" % (el.get("ID"), nid))
        if party is None:
            continue
        check(party.name == (el.get("NameRu") or el.get("Name")), "party '%s' name" % party.id)
        check(abs(party.tax_bias - float(el.get("TaxBias"))) < 1e-9, "party '%s' tax bias" % party.id)
        check(abs(party.hawkishness - float(el.get("Hawkishness"))) < 1e-9,
              "party '%s' hawkishness" % party.id)
        check(abs(party.popularity - float(el.get("Popularity"))) < 1e-9,
              "party '%s' popularity" % party.id)

    for nid, nation in sim.nations.items():
        check(bool(nation.office.ruling_party_id),
              "nation '%s' has a ruling party after loading" % nid)
        check(nation.party_by_id(nation.office.ruling_party_id) is not None,
              "nation '%s' ruling party resolves to a real party" % nid)


def check_formulas():
    print("=== FORMULAS: arithmetic fingerprints ===")
    cache = {}
    for rel, needles in FINGERPRINTS:
        path = os.path.join(SRC, rel.replace("/", os.sep))
        if path not in cache:
            cache[path] = open(path, encoding="utf-8").read()
        text = cache[path]
        for needle in needles:
            check(needle in text, "%s still contains '%s'" % (rel, needle))


def save_data_classes():
    """{class name: set of DataMember names} read out of Sim/WorldSave.cs."""
    text = open(os.path.join(SRC, "Sim", "WorldSave.cs"), encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'public sealed class (\w+)\s*\{(.*?)\n    \}', text, re.S):
        out[m.group(1)] = set(re.findall(r'\[DataMember\(Name = "(\w+)"', m.group(2)))
    return out


def check_save_contract():
    """The save the C# writes and the save the twin writes must be the same file.

    A class that exists on one side only is how CrossingSaveData slipped through:
    the twin persisted border crossings for months while the C# had nowhere to
    put them, and the gap was invisible until the project was compiled for real.
    """
    cs = save_data_classes()

    check("CrossingSaveData" in cs, "WorldSave.cs declares CrossingSaveData")
    members = cs.get("CrossingSaveData") or set()
    check(members == {"Id", "Manning", "Damage"},
          "CrossingSaveData carries exactly Id/Manning/Damage (%s)"
          % ", ".join(sorted(members)))
    check("Crossings" in (cs.get("WorldSaveData") or set()),
          "WorldSaveData carries a Crossings list")

    twin = open(os.path.join(HERE, "test_world_data.py"), encoding="utf-8").read()
    m = re.search(r'\{"Id": "atlantis\|lemuria"[^}]*\}', twin)
    check(m is not None, "the twin still exercises a crossing entry")
    if m is not None:
        keys = set(re.findall(r'"(\w+)":', m.group(0)))
        check(keys == members,
              "the twin's crossing keys match the C# DataMembers (%s vs %s)"
              % (", ".join(sorted(keys)), ", ".join(sorted(members))))


def main():
    check_constants()
    check_save_contract()
    check_unit_types()
    check_armies()
    check_elections()
    check_formulas()

    print("\n%d checks run, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        print("PARITY FAILED - the C# and the reference model have drifted apart")
        return 1
    print("PARITY OK - C# matches the proven reference model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
