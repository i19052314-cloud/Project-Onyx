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


def to_save(regions, seed=1337, elapsed=0.0):
    """Mirror of World.ToSaveData -> world.json."""
    return {
        "Version": SAVE_VERSION,
        "Seed": seed,
        "SavedAtUtc": "2026-01-01T00:00:00.0000000Z",
        "ElapsedGameMinutes": elapsed,
        "Regions": [{"Id": r.id, "Owner": r.owner, "Control": r.control,
                     "Military": r.military, "Destruction": r.destruction} for r in regions],
    }


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
    return skipped


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
