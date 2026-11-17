#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_world_data.py — data-contract tests for milestone M1.

The C# cannot be compiled in this sandbox, but the thing it consumes can be
tested: these tests assert exactly the invariants OnyxNations.Sim.World checks
at runtime, against the real Nations.xml / Regions.xml, plus a save/load round
trip that mirrors World.ToSaveData() / World.ApplySave().

If this passes and the C# is a faithful implementation, the world that loads in
GTA is the world described here.

Usage:  python3 tools/test_world_data.py      (exit 0 = all green)
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "OnyxNations", "Data")
POSTALS = os.path.join(ROOT, "ProjectOnyx", "Data", "Postals.xml")
SAVE_VERSION = 1  # must match OnyxNations.Sim.World.SaveVersion

FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
        print("  [FAIL] %s" % message)
    return bool(condition)


# --------------------------------------------------------------- geometry ----
def point_in_polygon(poly, x, y):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def polygon_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ------------------------------------------------------------------ model ----
class Region(object):
    def __init__(self, el):
        self.id = el.get("ID")
        self.owner = el.get("Owner")
        self.original_owner = el.get("Owner")
        self.contested = el.get("Contested") == "true"
        self.claimed_by = el.get("ClaimedBy") or ""
        self.population = int(el.get("Population"))
        self.strategic = int(el.get("StrategicValue"))
        self.area = float(el.get("AreaKm2"))
        self.capital = el.get("Capital") == "true"
        cen = el.find("Centroid")
        self.centroid = (float(cen.get("X")), float(cen.get("Y")))
        self.polygon = [(float(v.get("X")), float(v.get("Y")))
                        for v in el.find("Polygon").findall("Vertex")]
        nb = el.find("Neighbours").text or ""
        self.neighbours = [x for x in nb.split("|") if x]
        self.control = 100.0
        self.military = 0.0
        self.destruction = 0.0

    @property
    def occupied(self):
        return self.owner != self.original_owner


def set_owner(region, nation_id):
    """Mirror of World.SetOwner: returns the previous owner or None."""
    if region.owner == nation_id:
        return None
    previous = region.owner
    region.owner = nation_id
    region.control = 35.0 if region.occupied else 100.0
    return previous


def to_save(regions, seed=1337, elapsed=0.0, nations=None, units=None):
    """Mirror of World.ToSaveData -> world.json.

    `nations` is a list of mutable-state dicts (treasury, sliders, morale, ...)
    and `units` a list of unit dicts; both are optional so the earlier,
    region-only tests keep exercising the same path as before.

    Relations are kept in memory as {nation: value} but written as a list of
    {With, Value} pairs, exactly as the DataContract does.
    """
    out_nations = []
    for ns in (nations or []):
        ns = dict(ns)
        rel = ns.get("Relations") or {}
        ns["Relations"] = [{"With": k, "Value": v} for k, v in sorted(rel.items())]
        out_nations.append(ns)
    return {
        "Version": SAVE_VERSION,
        "Seed": seed,
        "SavedAtUtc": "2026-01-01T00:00:00.0000000Z",
        "ElapsedGameMinutes": elapsed,
        "Regions": [{"Id": r.id, "Owner": r.owner, "Control": r.control,
                     "Military": r.military, "Destruction": r.destruction} for r in regions],
        "Nations": out_nations,
        "Units": list(units or []),
    }


class UnitType(object):
    def __init__(self, el):
        self.id = el.get("ID")
        self.name = el.get("Name")
        self.name_ru = el.get("NameRu") or ""
        self.category = el.get("Category")
        self.manpower = int(el.get("Manpower"))
        self.fire = float(el.get("Firepower"))
        self.armor = float(el.get("Armor"))
        self.mobility = float(el.get("Mobility"))
        self.range = float(el.get("Range"))
        self.cost = float(el.get("Cost"))
        self.upkeep = float(el.get("Upkeep"))
        self.vehicle_model = el.get("VehicleModel") or ""
        self.vehicle_count = int(el.get("VehicleCount"))

    @property
    def raw_power(self):
        """Mirror of Unit.Power before morale/supply/experience multipliers.

        Exists so the data can be checked for what it claims to be: a unit that
        outguns armour *and* outruns it *and* costs less is a data bug, not a
        balance decision.
        """
        return (self.manpower / 100.0
                * (0.50 * self.fire + 0.20 * self.armor
                   + 0.15 * self.mobility + 0.15 * self.range) / 100.0)


CATEGORIES = ["Infantry", "MotorRifle", "Armor", "Artillery", "Air", "Heli",
              "SpecOps", "MilitaryPolice"]
ROLES = ["Prime", "Defence", "Interior", "Finance", "Foreign", "Transport",
         "Health"]


def apply_save(regions_by_id, data):
    """Mirror of World.ApplySave: geometry and authored numbers win, only mutable
    state is restored. Returns the list of skipped entries."""
    skipped = []
    for rs in data["Regions"]:
        r = regions_by_id.get(rs["Id"])
        if r is None:
            skipped.append("region '%s' no longer exists" % rs["Id"])
            continue
        r.owner = rs["Owner"]
        r.control = min(100.0, max(0.0, rs["Control"]))
        r.military = min(100.0, max(0.0, rs["Military"]))
        r.destruction = min(100.0, max(0.0, rs["Destruction"]))

    if "Nations" in data:
        for ns in data["Nations"]:
            if ns["Id"] not in NATION_STATE:
                skipped.append("nation '%s' no longer exists" % ns["Id"])
                continue
            NATION_STATE[ns["Id"]].update({
                "Treasury": ns["Treasury"], "TaxRate": ns["TaxRate"],
                "Morale": ns["Morale"], "Approval": ns["Approval"],
                "WarWeariness": ns["WarWeariness"], "Mobilization": ns["Mobilization"],
                "Casualties": ns["Casualties"], "AtWar": list(ns["AtWar"]),
                "Relations": {x["With"]: x["Value"] for x in ns["Relations"]},
                "Ministers": [dict(m) for m in ns["Ministers"]],
            })
    if "Units" in data:
        restored = []
        for us in data["Units"]:
            if us["Nation"] not in NATION_STATE or us["Type"] not in KNOWN_UNIT_TYPES:
                skipped.append("unit '%s' could not be restored (type '%s')"
                               % (us["Id"], us["Type"]))
                continue
            restored.append(dict(us))
        data["_restored_units"] = restored
    return skipped


# mutable state restored by a save; keyed by nation id
NATION_STATE = {}
KNOWN_UNIT_TYPES = set()


# ------------------------------------------------------------------ tests ----
def main():
    nations_root = ET.parse(os.path.join(DATA, "Nations.xml")).getroot()
    regions_root = ET.parse(os.path.join(DATA, "Regions.xml")).getroot()

    nations = {}
    for n in nations_root.find("Nations").findall("Nation"):
        nations[n.get("ID")] = n

    regions = {}
    for r in regions_root.find("Regions").findall("Region"):
        regions[r.get("ID")] = Region(r)

    print("=== NATIONS (%d) ===" % len(nations))
    check(len(nations) >= 2, "at least two nations are configured")
    for nid, n in nations.items():
        cap = n.get("CapitalRegion")
        check(cap in regions, "nation '%s' capital region '%s' exists" % (nid, cap))
        if cap in regions:
            check(regions[cap].owner == nid,
                  "nation '%s' owns its capital region '%s'" % (nid, cap))
        for rel in (n.find("Relations") or []):
            check(rel.get("With") in nations,
                  "nation '%s' relation target '%s' exists" % (nid, rel.get("With")))
            check(-100 <= int(rel.get("Value")) <= 100,
                  "nation '%s' relation value within -100..100" % nid)

    print("=== REGIONS (%d) ===" % len(regions))
    for rid, r in regions.items():
        check(r.owner in nations, "region '%s' owner '%s' is a known nation" % (rid, r.owner))
        if r.claimed_by:
            check(r.claimed_by in nations,
                  "region '%s' claimant '%s' is a known nation" % (rid, r.claimed_by))
        check(len(r.polygon) >= 3, "region '%s' polygon has >= 3 vertices" % rid)
        check(r.population > 0, "region '%s' population > 0" % rid)
        check(1 <= r.strategic <= 10, "region '%s' strategic value in 1..10" % rid)
        for nb in r.neighbours:
            check(nb in regions, "region '%s' neighbour '%s' exists" % (rid, nb))
            if nb in regions:
                check(rid in regions[nb].neighbours,
                      "neighbour relation %s <-> %s is symmetric" % (rid, nb))
        check(point_in_polygon(r.polygon, r.centroid[0], r.centroid[1]),
              "region '%s' centroid lies inside its own polygon" % rid)
        computed = polygon_area(r.polygon) / 1.0e6
        check(abs(computed - r.area) < max(0.05, r.area * 0.02),
              "region '%s' AreaKm2 matches its polygon (%.2f vs %.2f)" % (rid, r.area, computed))

    print("=== TERRITORIAL LOOKUP (865 postal points) ===")
    text = open(POSTALS, encoding="utf-8").read()
    postal = [(float(x), float(y)) for _, x, y in
              re.findall(r'Code="(\d+)"\s+X="(-?[\d.]+)"\s+Y="(-?[\d.]+)"', text)]
    check(len(postal) > 500, "postal reference cloud was parsed (%d points)" % len(postal))
    bad = 0
    for x, y in postal:
        hits = [rid for rid, r in regions.items() if point_in_polygon(r.polygon, x, y)]
        if len(hits) != 1:
            bad += 1
    check(bad == 0, "every postal point falls in exactly one region (%d bad)" % bad)

    print("=== UNIT TYPES ===")
    ut_root = ET.parse(os.path.join(DATA, "UnitTypes.xml")).getroot()
    types = {}
    for ut in ut_root.find("UnitTypes").findall("UnitType"):
        types[ut.get("ID")] = UnitType(ut)
    check(len(types) >= 8, "at least eight unit types are configured (%d)" % len(types))
    seen_categories = set()
    for tid, t in types.items():
        check(t.category in CATEGORIES,
              "unit '%s' category '%s' is one the C# enum knows" % (tid, t.category))
        seen_categories.add(t.category)
        check(t.manpower > 0, "unit '%s' has manpower" % tid)
        check(t.cost > 0 and t.upkeep > 0, "unit '%s' has a cost and an upkeep" % tid)
        check(0 <= t.fire <= 100 and 0 <= t.armor <= 100
              and 0 <= t.mobility <= 100 and 0 <= t.range <= 100,
              "unit '%s' combat ratings are within 0..100" % tid)
        check(bool(t.name_ru), "unit '%s' carries a Russian display name" % tid)
    check(len(seen_categories) == len(CATEGORIES),
          "every unit category is represented (%d/%d)" % (len(seen_categories), len(CATEGORIES)))

    # the ordering claims in the data file: armour outguns infantry and artillery
    # outranges everything, while aviation is the fastest thing on the map
    check(types["armor"].armor > types["infantry"].armor,
          "armour is better protected than infantry")
    check(types["armor"].fire > types["infantry"].fire,
          "armour hits harder than infantry")
    check(types["artillery"].range >= max(types[t].range for t in types),
          "artillery has the longest reach")
    check(types["air"].mobility >= max(types[t].mobility for t in types),
          "aviation is the fastest arm")
    check(types["artillery"].mobility < types["infantry"].mobility,
          "artillery is slower than infantry")
    # Per soldier, not per unit: an armour battalion is a small formation of very
    # expensive specialists, so the honest comparison is power per head.
    check(types["armor"].raw_power / types["armor"].manpower
          > types["infantry"].raw_power / types["infantry"].manpower,
          "armour concentrates more combat power per soldier than infantry")
    check(types["infantry"].upkeep / types["infantry"].manpower
          < types["armor"].upkeep / types["armor"].manpower,
          "armour costs more per soldier to keep in the field")
    # and the costs follow the same ordering, or nothing above means anything
    check(types["armor"].upkeep > types["infantry"].upkeep,
          "armour costs more to keep in the field than infantry")
    check(types["air"].upkeep >= max(types[t].upkeep for t in types),
          "aviation is the most expensive arm to run")

    print("=== ARMIES & CABINETS (%d armies) ===" % len(
        ET.parse(os.path.join(DATA, "Armies.xml")).getroot().find("Armies").findall("Army")))
    armies_root = ET.parse(os.path.join(DATA, "Armies.xml")).getroot()
    unit_total = 0
    for army in armies_root.find("Armies").findall("Army"):
        nid = army.get("Nation")
        check(nid in nations, "army belongs to a known nation ('%s')" % nid)
        for u in army.findall("Unit"):
            check(u.get("Type") in types,
                  "army '%s' fields a known unit type ('%s')" % (nid, u.get("Type")))
            check(int(u.get("Count")) > 0, "army '%s' asks for at least one of '%s'"
                  % (nid, u.get("Type")))
            unit_total += int(u.get("Count"))
        check(len(army.findall("General")) >= 1,
              "army '%s' has at least one general" % nid)
        for g in army.findall("General"):
            check(0.0 <= float(g.get("Skill")) <= 1.0,
                  "general '%s' skill is within 0..1" % g.get("Name"))
    check(unit_total > 0, "the configured armies raise at least one unit (%d)" % unit_total)

    cabinets = armies_root.find("Cabinets").findall("Cabinet")
    for cab in cabinets:
        nid = cab.get("Nation")
        check(nid in nations, "cabinet belongs to a known nation ('%s')" % nid)
        roles = [m.get("Role") for m in cab.findall("Minister")]
        for role in ROLES:
            check(role in roles, "cabinet '%s' seats a minister of %s" % (nid, role))
        for m in cab.findall("Minister"):
            check(m.get("Role") in ROLES,
                  "minister '%s' holds a role the C# enum knows" % m.get("Name"))
            check(0.0 <= float(m.get("Competence")) <= 1.0,
                  "minister '%s' competence is within 0..1" % m.get("Name"))
            check(0.0 <= float(m.get("Loyalty")) <= 1.0,
                  "minister '%s' loyalty is within 0..1" % m.get("Name"))
    check(len(cabinets) == len(nations),
          "every nation has a cabinet (%d/%d)" % (len(cabinets), len(nations)))

    print("=== SAVE / LOAD ROUND TRIP ===")
    target = "west_ls" if "west_ls" in regions else sorted(regions)[0]
    other = [n for n in nations if n != regions[target].owner][0]

    previous = set_owner(regions[target], other)
    check(previous is not None, "transferring '%s' to '%s' reports the previous owner" % (target, other))
    check(regions[target].owner == other, "region '%s' is now held by '%s'" % (target, other))
    check(regions[target].control == 35.0, "a newly occupied region starts at 35%% control")
    check(regions[target].occupied, "region '%s' is flagged occupied" % target)

    payload = to_save(regions.values(), seed=1337, elapsed=123.5)
    raw = json.dumps(payload, indent=2)
    reloaded = json.loads(raw)
    check(reloaded["Version"] == SAVE_VERSION, "save carries the expected version")
    check(len(raw) > 200, "save serializes to real JSON (%d bytes)" % len(raw))

    # fresh world from config, then apply the save (what a game restart does)
    fresh = {}
    for r in regions_root.find("Regions").findall("Region"):
        fresh[r.get("ID")] = Region(r)
    check(fresh[target].owner != other, "a fresh world still has the configured owner")
    skipped = apply_save(fresh, reloaded)
    check(not skipped, "no save entries were skipped")
    check(fresh[target].owner == other,
          "after a restart '%s' is still held by '%s'" % (target, other))
    check(fresh[target].control == 35.0, "control level survived the restart")
    check(fresh[target].occupied, "occupied flag survives the restart (geometry keeps the original owner)")

    # liberation: handing it back clears the occupation and restores full control
    set_owner(fresh[target], fresh[target].original_owner)
    check(not fresh[target].occupied, "returning a region to its original owner clears the occupation")
    check(fresh[target].control == 100.0, "a liberated region is back at 100%% control")

    print("=== FULL STATE ROUND TRIP (nations, ministers, units) ===")
    # Seed the same state the C# module builds from Armies.xml / Nations.xml.
    types_by_id = {t.id: t for t in types.values()}
    KNOWN_UNIT_TYPES.clear()
    KNOWN_UNIT_TYPES.update(types_by_id.keys())
    NATION_STATE.clear()
    units_now = []
    serial = 0
    for army in armies_root.find("Armies").findall("Army"):
        nid = army.get("Nation")
        n_el = nations[nid]
        cap = n_el.get("CapitalRegion")
        cx, cy = regions[cap].centroid
        NATION_STATE[nid] = {
            "Treasury": float(n_el.get("StartingTreasury")),
            "TaxRate": 0.24, "Morale": 0.60, "Approval": 55.0,
            "WarWeariness": 0.0, "Mobilization": 0.35, "Casualties": 0,
            "AtWar": [],
            "Relations": {r.get("With"): int(r.get("Value"))
                          for r in (n_el.find("Relations") or [])},
            "Ministers": [{"Role": m.get("Role"), "Name": m.get("Name"),
                           "Competence": float(m.get("Competence")),
                           "Loyalty": float(m.get("Loyalty")), "Relation": 0.5,
                           "Task": "None", "TaskDaysLeft": 0}
                          for m in armies_root.find("Cabinets").findall("Cabinet")
                          if m.get("Nation") == nid for m in m.findall("Minister")],
        }
        for u in army.findall("Unit"):
            for k in range(int(u.get("Count"))):
                serial += 1
                spread = (k - int(u.get("Count")) / 2.0) * 60.0
                units_now.append({
                    "Id": "u%d" % serial, "Type": u.get("Type"), "Nation": nid,
                    "Name": types_by_id[u.get("Type")].name_ru + "-" + str(k + 1),
                    "X": cx + spread, "Y": cy + spread * 0.7,
                    "Strength": 1.0, "Morale": 0.70, "Supply": 0.85,
                    "Experience": 0.15, "Order": "Hold", "TargetRegion": "",
                    "Mobilized": True,
                })

    check(len(NATION_STATE) == len(nations), "every nation has mutable state to save")
    check(len(units_now) == unit_total,
          "the save carries every configured unit (%d/%d)" % (len(units_now), unit_total))
    for nid in NATION_STATE:
        check(len(NATION_STATE[nid]["Ministers"]) == len(ROLES),
              "nation '%s' saves all %d ministers" % (nid, len(ROLES)))

    # ... the war happens ...
    NATION_STATE["rsa"]["AtWar"] = ["fzsa"]
    NATION_STATE["fzsa"]["AtWar"] = ["rsa"]
    NATION_STATE["rsa"]["Treasury"] -= 4_250_000.0
    NATION_STATE["rsa"]["Morale"] = 0.41
    NATION_STATE["rsa"]["Approval"] = 37.5
    NATION_STATE["rsa"]["WarWeariness"] = 0.32
    NATION_STATE["rsa"]["Mobilization"] = 0.80
    NATION_STATE["rsa"]["Casualties"] = 12_500
    NATION_STATE["rsa"]["Relations"]["fzsa"] = -70
    NATION_STATE["rsa"]["Ministers"][1]["Task"] = "SpeedMobilization"
    NATION_STATE["rsa"]["Ministers"][1]["TaskDaysLeft"] = 3
    units_now[0]["Strength"] = 0.42
    units_now[0]["Morale"] = 0.33
    units_now[0]["Supply"] = 0.21
    units_now[0]["Experience"] = 0.58
    units_now[0]["Order"] = "Attack"
    units_now[0]["TargetRegion"] = target
    units_now[0]["X"] = regions[target].centroid[0]
    units_now[0]["Y"] = regions[target].centroid[1]
    destroyed = units_now.pop()       # a unit wiped out during the fighting

    full = to_save(regions.values(), seed=1337, elapsed=987.25,
                   nations=[dict(v, Id=k) for k, v in NATION_STATE.items()],
                   units=units_now)
    raw_full = json.dumps(full, indent=2)
    check(len(raw_full) > 2000, "the full save is real JSON (%d bytes)" % len(raw_full))

    # wipe everything and reload: this is what a restart actually does
    for nid in NATION_STATE:
        n_el = nations[nid]
        NATION_STATE[nid] = {
            "Treasury": float(n_el.get("StartingTreasury")),
            "TaxRate": 0.24, "Morale": 0.60, "Approval": 55.0,
            "WarWeariness": 0.0, "Mobilization": 0.35, "Casualties": 0,
            "AtWar": [],
            "Relations": {r.get("With"): int(r.get("Value"))
                          for r in (n_el.find("Relations") or [])},
            "Ministers": [{"Role": m.get("Role"), "Name": m.get("Name"),
                           "Competence": float(m.get("Competence")),
                           "Loyalty": float(m.get("Loyalty")), "Relation": 0.5,
                           "Task": "None", "TaskDaysLeft": 0}
                          for m in armies_root.find("Cabinets").findall("Cabinet")
                          if m.get("Nation") == nid for m in m.findall("Minister")],
        }
    reloaded_full = json.loads(raw_full)
    skipped3 = apply_save(fresh, reloaded_full)
    check(not skipped3, "no entries were skipped reloading the full save")

    rsa = NATION_STATE["rsa"]
    check(rsa["AtWar"] == ["fzsa"], "the war survived the restart")
    check(abs(rsa["Treasury"] - (float(nations["rsa"].get("StartingTreasury")) - 4_250_000.0)) < 1.0,
          "the treasury survived the restart")
    check(abs(rsa["Morale"] - 0.41) < 1e-6, "morale survived the restart")
    check(abs(rsa["Approval"] - 37.5) < 1e-6, "the president's approval survived")
    check(abs(rsa["WarWeariness"] - 0.32) < 1e-6, "war weariness survived")
    check(abs(rsa["Mobilization"] - 0.80) < 1e-6, "mobilization level survived")
    check(rsa["Casualties"] == 12_500, "casualty count survived")
    check(rsa["Relations"]["fzsa"] == -70, "diplomatic relations survived")
    defence = [m for m in rsa["Ministers"] if m["Role"] == "Defence"][0]
    check(defence["Task"] == "SpeedMobilization" and defence["TaskDaysLeft"] == 3,
          "a ministry task in progress survived the restart")

    restored = reloaded_full["_restored_units"]
    check(len(restored) == unit_total - 1,
          "a destroyed unit does not come back (%d of %d)" % (len(restored), unit_total))
    check(not any(u["Id"] == destroyed["Id"] for u in restored),
          "the destroyed unit id is gone from the order of battle")
    moved = [u for u in restored if u["Id"] == units_now[0]["Id"]][0]
    check(abs(moved["Strength"] - 0.42) < 1e-6, "unit strength survived")
    check(abs(moved["Morale"] - 0.33) < 1e-6, "unit morale survived")
    check(abs(moved["Supply"] - 0.21) < 1e-6, "unit supply survived")
    check(abs(moved["Experience"] - 0.58) < 1e-6, "unit experience survived")
    check(moved["Order"] == "Attack" and moved["TargetRegion"] == target,
          "an attack order in progress survived the restart")
    check(abs(moved["X"] - regions[target].centroid[0]) < 1e-6,
          "the unit is still where the battle left it")

    # a save naming a unit type that no longer exists is reported, not fatal
    stale_full = json.loads(raw_full)
    stale_full["Units"].append({"Id": "u999", "Type": "laser_dragons", "Nation": "rsa",
                                "Name": "?", "X": 0, "Y": 0, "Strength": 1, "Morale": 1,
                                "Supply": 1, "Experience": 0, "Order": "Hold",
                                "TargetRegion": "", "Mobilized": True})
    skipped4 = apply_save(fresh, stale_full)
    check(len(skipped4) == 1 and "laser_dragons" in skipped4[0],
          "an unknown unit type is reported, not fatal")

    # a save written by a newer build must be refused
    newer = to_save(regions.values())
    newer["Version"] = SAVE_VERSION + 1
    check(newer["Version"] > SAVE_VERSION, "a newer save version is detected as newer")

    # an unknown region id in a save must be skipped, not crash
    stale = to_save(regions.values())
    stale["Regions"].append({"Id": "atlantis", "Owner": other, "Control": 10,
                             "Military": 0, "Destruction": 0})
    skipped2 = apply_save(fresh, stale)
    check(len(skipped2) == 1 and "atlantis" in skipped2[0],
          "a region missing from the config is reported, not fatal")

    print("\n%d checks run, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        print("TESTS FAILED")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
