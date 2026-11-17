#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_regions.py — generator and verifier of OnyxNations/Data/Regions.xml

ONYX RP: NATIONS & WAR — milestone M1 ("государства и карта").

Why this exists
---------------
The territorial division must be built from *real* world coordinates, not from
guessed numbers. This script uses the postal point cloud that ships with Onyx RP
(ProjectOnyx/Data/Postals.xml, 865 points, world X/Y) as the reference geometry:

  1. k-means (deterministic, fixed seed) groups the points into K clusters;
  2. the cluster centroids become region seeds;
  3. each region polygon is the Voronoi cell of its seed, clipped to the map
     bounding box — Voronoi cells tile the plane exactly: no gaps, no overlaps;
  4. nation ownership, names and gameplay numbers are authored below (the
     geometry is generated, the *design* is not);
  5. everything is verified: point-in-polygon over all 865 points must agree
     with nearest-seed, every polygon must be non-degenerate, adjacency must be
     symmetric.

Result: OnyxNations/Data/Regions.xml + docs/onyx-nations/map-preview.png.

Usage:  python3 tools/build_regions.py [--k 14] [--verify-only]
"""

import argparse
import math
import os
import random
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTALS = os.path.join(ROOT, "ProjectOnyx", "Data", "Postals.xml")
OUT_XML = os.path.join(ROOT, "OnyxNations", "Data", "Regions.xml")
OUT_PNG = os.path.join(ROOT, "docs", "onyx-nations", "map-preview.png")

SEED = 1337
MAP_PAD = 400.0

# --------------------------------------------------------------------------
# Authored design data. Geometry comes from the postal cloud; this is the part
# a designer edits. `anchor` is the expected centroid of the cluster (taken
# from a first run of the clustering) and is only used to bind a generated
# cluster to a stable region id — if you change K, re-check the report.
# --------------------------------------------------------------------------
REGION_DESIGN = {
    # id          anchor (cluster centroid)  EN                              RU                                   owner  contested claimed_by  pop      strat capital
    "paleto":     dict(anchor=(-43, 6250),    en="Paleto Bay & North Coast",   ru="Пале́то-Бэй и северное побережье", owner="fzsa", contested=False, claimed_by="",  pop=165000, strat=5,  capital=True),
    "grapeseed":  dict(anchor=(2157, 4161),   en="Grapeseed & North-East",     ru="Грейпсид и северо-восток",        owner="fzsa", contested=False, claimed_by="",  pop=132000, strat=6,  capital=False),
    "zancudo":    dict(anchor=(-25, 3220),    en="Fort Zancudo & West Blaine", ru="Форт-Занкудо и западный Блейн",   owner="fzsa", contested=True,  claimed_by="rsa", pop=74000, strat=10, capital=False),
    "senora":     dict(anchor=(1633, 2478),   en="Grand Senora East",          ru="Гранд-Сенора (восток)",           owner="rsa",  contested=False, claimed_by="",  pop=62000,  strat=7,  capital=False),
    "chumash":    dict(anchor=(-2984, 939),   en="Chumash & Great Ocean Hwy",  ru="Чумаш и Большой Океанский тракт", owner="fzsa", contested=False, claimed_by="",  pop=195000, strat=4,  capital=False),
    "vinewood":   dict(anchor=(-822, 430),    en="Vinewood & Rockford Hills",  ru="Вайнвуд и Рокфорд-Хиллз",         owner="rsa",  contested=False, claimed_by="",  pop=310000, strat=8,  capital=False),
    "downtown":   dict(anchor=(-18, 86),      en="Downtown Los Santos",        ru="Центр Лос-Сантоса",               owner="rsa",  contested=False, claimed_by="",  pop=420000, strat=10, capital=True),
    "west_ls":    dict(anchor=(-1686, -8),    en="West Los Santos",            ru="Западный Лос-Сантос",             owner="rsa",  contested=True,  claimed_by="fzsa", pop=265000, strat=7, capital=False),
    "east_ls":    dict(anchor=(1102, -556),   en="East Los Santos",            ru="Восточный Лос-Сантос",            owner="rsa",  contested=False, claimed_by="",  pop=380000, strat=6,  capital=False),
    "south_ls":   dict(anchor=(-1034, -1026), en="South Los Santos",           ru="Южный Лос-Сантос",                owner="rsa",  contested=False, claimed_by="",  pop=440000, strat=6,  capital=False),
    "davis":      dict(anchor=(129, -1463),   en="Davis & Rancho",             ru="Дэвис и Ранчо",                   owner="rsa",  contested=False, claimed_by="",  pop=210000, strat=5,  capital=False),
    "cypress":    dict(anchor=(1141, -1746),  en="Cypress Flats & El Burro",   ru="Сайпресс-Флэтс и Эль-Бурро",      owner="rsa",  contested=False, claimed_by="",  pop=150000, strat=6,  capital=False),
    "lsia":       dict(anchor=(-549, -2354),  en="LSIA & La Puerta",           ru="ЛСИА и Ла-Пуэрта",                owner="rsa",  contested=False, claimed_by="",  pop=45000,  strat=9,  capital=False),
    "port":       dict(anchor=(667, -2674),   en="Port & Terminal",            ru="Порт и Терминал",                 owner="rsa",  contested=False, claimed_by="",  pop=38000,  strat=9,  capital=False),
}

NATION_COLOR = {"rsa": (47, 111, 208), "fzsa": (208, 96, 47)}
CONTESTED_COLOR = (232, 196, 70)


# ---------------------------------------------------------------- geometry --
def area2(poly):
    """Twice the signed area (positive = counter-clockwise)."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s


def point_in_poly(poly, px, py):
    """Standard ray-cast test. `poly` is a list of (x, y)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            xint = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < xint:
                inside = not inside
        j = i
    return inside


def clip_half_plane(poly, a, b, c):
    """Sutherland-Hodgman clip against  a*x + b*y <= c.
    Returns (new_poly, did_cut)."""
    if not poly:
        return poly, False
    out = []
    cut = False
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        dc = a * cur[0] + b * cur[1] - c
        dn = a * nxt[0] + b * nxt[1] - c
        if dc <= 0.0:
            out.append(cur)
        if (dc <= 0.0) != (dn <= 0.0):
            t = dc / (dc - dn)
            out.append((cur[0] + t * (nxt[0] - cur[0]), cur[1] + t * (nxt[1] - cur[1])))
            cut = True
        if dc > 0.0:
            cut = True
    return out, cut


def voronoi_cells(seeds, bounds):
    """Voronoi cell per seed, clipped to `bounds` = (minx, miny, maxx, maxy).

    Cell i is the set of points closer to seed i than to any other seed; it is
    obtained by clipping the bounding rectangle with every bisector half-plane.
    Returns (cells, adjacency) where adjacency[i] is the set of seeds whose
    bisector actually cut cell i (i.e. cells sharing an edge).
    """
    minx, miny, maxx, maxy = bounds
    rect = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    cells, adj = [], [set() for _ in seeds]
    for i, si in enumerate(seeds):
        cell = list(rect)
        for j, sj in enumerate(seeds):
            if i == j:
                continue
            # |p - si|^2 <= |p - sj|^2   ->   2(sj-si).p <= |sj|^2 - |si|^2
            a = 2.0 * (sj[0] - si[0])
            b = 2.0 * (sj[1] - si[1])
            c = (sj[0] ** 2 + sj[1] ** 2) - (si[0] ** 2 + si[1] ** 2)
            cell, cut = clip_half_plane(cell, a, b, c)
            if cut and cell:
                adj[i].add(j)
        cells.append(cell)
    return cells, adj


def shared_edge_length(pa, pb):
    """Length of the longest common boundary segment between two polygons.

    Voronoi cells are clipped from the same rectangle by the same set of
    bisectors, so two cells that touch along an edge do so along an exactly
    collinear, exactly overlapping segment. Comparing final polygons (rather
    than clip events) is what makes adjacency symmetric: a bisector may cut a
    cell early and have that edge removed by a later clip.
    """
    best = 0.0
    for ia in range(len(pa)):
        a1, a2 = pa[ia], pa[(ia + 1) % len(pa)]
        ax, ay = a2[0] - a1[0], a2[1] - a1[1]
        la = math.hypot(ax, ay)
        if la < 1e-9:
            continue
        for ib in range(len(pb)):
            b1, b2 = pb[ib], pb[(ib + 1) % len(pb)]
            bx, by = b2[0] - b1[0], b2[1] - b1[1]
            lb = math.hypot(bx, by)
            if lb < 1e-9:
                continue
            # collinear?
            if abs(ax * by - ay * bx) > 1e-6 * la * lb:
                continue
            # perpendicular offset between the two supporting lines
            if abs((b1[0] - a1[0]) * ay - (b1[1] - a1[1]) * ax) > 1e-6 * la:
                continue
            # overlap along the direction of edge a
            ux, uy = ax / la, ay / la
            t1 = 0.0
            t2 = la
            s1 = (b1[0] - a1[0]) * ux + (b1[1] - a1[1]) * uy
            s2 = (b2[0] - a1[0]) * ux + (b2[1] - a1[1]) * uy
            lo, hi = (s1, s2) if s1 <= s2 else (s2, s1)
            ov = min(t2, hi) - max(t1, lo)
            if ov > best:
                best = ov
    return best


def edge_adjacency(cells):
    n = len(cells)
    adj = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if shared_edge_length(cells[i], cells[j]) > 1.0:  # > 1 metre
                adj[i].add(j)
                adj[j].add(i)
    return adj


def kmeans(pts, k, iters=300, seed=SEED):
    """Deterministic k-means++ (fixed seed) — pure python, no numpy."""
    rng = random.Random(seed)
    centres = [pts[rng.randrange(len(pts))]]
    while len(centres) < k:
        d2 = [min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 for c in centres) for p in pts]
        total = sum(d2)
        r = rng.random() * total
        acc = 0.0
        for i, v in enumerate(d2):
            acc += v
            if acc >= r:
                centres.append(pts[i])
                break
    labels = [0] * len(pts)
    for _ in range(iters):
        moved = False
        for i, p in enumerate(pts):
            best = min(range(k), key=lambda j: (p[0] - centres[j][0]) ** 2 + (p[1] - centres[j][1]) ** 2)
            if best != labels[i]:
                labels[i] = best
                moved = True
        if not moved:
            break
        for j in range(k):
            members = [pts[i] for i in range(len(pts)) if labels[i] == j]
            if members:
                centres[j] = (sum(p[0] for p in members) / len(members),
                              sum(p[1] for p in members) / len(members))
    return centres, labels


# ------------------------------------------------------------------- input --
def esc(v):
    """XML-escape an attribute value."""
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def load_postals(path):
    text = open(path, encoding="utf-8").read()
    triples = re.findall(r'Code="(\d+)"\s+X="(-?[\d.]+)"\s+Y="(-?[\d.]+)"', text)
    if not triples:
        sys.exit("[!] no postal points parsed from %s" % path)
    return [(int(c), float(x), float(y)) for c, x, y in triples]


# -------------------------------------------------------------- png writer --
def write_png(path, w, h, pixels):
    """pixels: list of rows, each row a list of (r, g, b). Minimal PNG encoder."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


# --------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=len(REGION_DESIGN))
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    postal = load_postals(POSTALS)
    pts = [(x, y) for _, x, y in postal]
    print("[i] postal points: %d" % len(pts))

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bounds = (min(xs) - MAP_PAD, min(ys) - MAP_PAD, max(xs) + MAP_PAD, max(ys) + MAP_PAD)
    print("[i] map bounds: X %.0f..%.0f   Y %.0f..%.0f" % (bounds[0], bounds[2], bounds[1], bounds[3]))

    k = args.k
    if k != len(REGION_DESIGN):
        print("[!] --k=%d differs from the authored design table (%d); ids will be "
              "positional and probably wrong" % (k, len(REGION_DESIGN)))

    centres, labels = kmeans(pts, k)
    cells, _clip_adj = voronoi_cells(centres, bounds)
    adj = edge_adjacency(cells)

    # bind each generated cluster to a stable region id via the authored anchor
    ids = list(REGION_DESIGN.keys())
    binding = {}
    used = set()
    for ci, c in enumerate(centres):
        if k == len(REGION_DESIGN):
            best = min(ids, key=lambda rid: (c[0] - REGION_DESIGN[rid]["anchor"][0]) ** 2
                                             + (c[1] - REGION_DESIGN[rid]["anchor"][1]) ** 2)
            if best in used:                      # keep it deterministic & unique
                best = next(r for r in ids if r not in used)
        else:
            best = ids[ci] if ci < len(ids) else "region_%02d" % ci
        used.add(best)
        binding[ci] = best
    if len(set(binding.values())) != k:
        sys.exit("[!] cluster -> region binding is not unique")

    # ------------------------------------------------------------ verify ----
    print("\n=== VERIFICATION ===")
    ok = True
    counts = dict((r, 0) for r in binding.values())
    mismatch = 0
    for p in pts:
        hits = [binding[i] for i in range(k) if point_in_poly(cells[i], p[0], p[1])]
        if len(hits) != 1:
            ok = False
            print("  [FAIL] point (%.1f, %.1f) is inside %d regions: %s" % (p[0], p[1], len(hits), hits))
            if mismatch < 5:
                mismatch += 1
            continue
        counts[hits[0]] += 1
        # cross-check: point-in-polygon must agree with nearest seed
        nearest = binding[min(range(k), key=lambda j: (p[0] - centres[j][0]) ** 2 + (p[1] - centres[j][1]) ** 2)]
        if nearest != hits[0]:
            ok = False
            print("  [FAIL] PIP/nearest-seed disagree at (%.1f, %.1f): %s vs %s"
                  % (p[0], p[1], hits[0], nearest))
    print("  every postal point in exactly one region : %s" % ("OK" if ok else "FAIL"))
    print("  PIP == nearest-seed for all points      : %s" % ("OK" if ok else "FAIL"))

    for i in range(k):
        poly = cells[i]
        a = abs(area2(poly)) / 2.0 / 1.0e6  # km^2-ish (1 unit = 1 m)
        if len(poly) < 3 or a < 1.0:
            ok = False
            print("  [FAIL] degenerate cell for %s (verts=%d area=%.3f)" % (binding[i], len(poly), a))
    print("  all polygons non-degenerate             : %s" % ("OK" if ok else "FAIL"))

    asym = [(binding[i], binding[j]) for i in range(k) for j in adj[i] if i not in adj[j]]
    if not asym:
        # a cell must not neighbour itself, and every cell must have >= 1 neighbour
        asym += [("SELF", binding[i]) for i in range(k) if i in adj[i]]
        asym += [("ISOLATED", binding[i]) for i in range(k) if not adj[i]]
    if asym:
        ok = False
        print("  [FAIL] asymmetric adjacency: %s" % asym[:4])
    print("  adjacency symmetric                     : %s" % ("OK" if not asym else "FAIL"))

    total_area = sum(abs(area2(cells[i])) / 2.0 for i in range(k))
    rect_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    cover = total_area / rect_area
    if abs(cover - 1.0) > 1e-6:
        ok = False
    print("  cells tile the map box (coverage %.6f)  : %s" % (cover, "OK" if abs(cover - 1.0) <= 1e-6 else "FAIL"))

    if args.verify_only:
        print("\n%s" % ("VERIFY PASSED" if ok else "VERIFY FAILED"))
        return 0 if ok else 1

    # ------------------------------------------------------------- emit -----
    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8"?>')
    lines.append("<!-- GENERATED by tools/build_regions.py — do not hand-edit the geometry.")
    lines.append("     Geometry: Voronoi cells over %d seeds (deterministic k-means, seed=%d) of the" % (k, SEED))
    lines.append("     %d postal points in ProjectOnyx/Data/Postals.xml, clipped to the map box." % len(pts))
    lines.append("     Design fields (Owner / Contested / Population / StrategicValue / names) are authored")
    lines.append("     in the generator and are meant to be edited here by hand afterwards.")
    lines.append("     World coordinates: X = east/west, Y = north/south, 1 unit = 1 metre. -->")
    lines.append("<OnyxNations>")
    lines.append('  <MapBounds MinX="%.1f" MinY="%.1f" MaxX="%.1f" MaxY="%.1f"/>' % bounds)
    lines.append("  <Regions>")
    for i in range(k):
        rid = binding[i]
        d = REGION_DESIGN[rid]
        cx, cy = centres[i]
        a = abs(area2(cells[i])) / 2.0 / 1.0e6
        nb = sorted(binding[j] for j in adj[i])
        lines.append('    <Region ID="%s" Name="%s" NameRu="%s" Owner="%s" Contested="%s" ClaimedBy="%s"'
                     % (esc(rid), esc(d["en"]), esc(d["ru"]), esc(d["owner"]),
                        "true" if d["contested"] else "false", esc(d["claimed_by"])))
        lines.append('            Population="%d" StrategicValue="%d" AreaKm2="%.2f" PostalPoints="%d"'
                     % (d["pop"], d["strat"], a, counts[rid]))
        lines.append('            Capital="%s" Source="generated">'
                     % ("true" if d["capital"] else "false"))
        lines.append('      <Centroid X="%.2f" Y="%.2f"/>' % (cx, cy))
        lines.append("      <Polygon>")
        for vx, vy in cells[i]:
            lines.append('        <Vertex X="%.2f" Y="%.2f"/>' % (vx, vy))
        lines.append("      </Polygon>")
        lines.append("      <Neighbours>%s</Neighbours>" % esc("|".join(nb)))
        lines.append("    </Region>")
    lines.append("  </Regions>")
    lines.append("</OnyxNations>")

    os.makedirs(os.path.dirname(OUT_XML), exist_ok=True)
    with open(OUT_XML, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n[+] wrote %s (%d regions)" % (os.path.relpath(OUT_XML, ROOT), k))

    # ------------------------------------------------------- map preview ----
    W = H = 512
    scale = min(W / (bounds[2] - bounds[0]), H / (bounds[3] - bounds[1]))
    offx = (W - (bounds[2] - bounds[0]) * scale) / 2.0
    offy = (H - (bounds[3] - bounds[1]) * scale) / 2.0

    def to_px(x, y):
        return ((x - bounds[0]) * scale + offx,
                H - ((y - bounds[1]) * scale + offy))

    px = [[(14, 18, 28) for _ in range(W)] for _ in range(H)]
    for i in range(k):
        rid = binding[i]
        d = REGION_DESIGN[rid]
        col = CONTESTED_COLOR if d["contested"] else NATION_COLOR[d["owner"]]
        poly = [to_px(x, y) for x, y in cells[i]]
        ys_p = [p[1] for p in poly]
        for row in range(max(0, int(min(ys_p))), min(H, int(max(ys_p)) + 1)):
            yc = row + 0.5
            xsi = []
            n = len(poly)
            for a in range(n):
                x1, y1 = poly[a]
                x2, y2 = poly[(a + 1) % n]
                if (y1 > yc) != (y2 > yc):
                    xsi.append(x1 + (yc - y1) / (y2 - y1) * (x2 - x1))
            xsi.sort()
            for s in range(0, len(xsi) - 1, 2):
                for c in range(max(0, int(xsi[s])), min(W, int(xsi[s + 1]) + 1)):
                    px[row][c] = col
    # region borders (black) + postal dots
    for i in range(k):
        poly = [to_px(x, y) for x, y in cells[i]]
        n = len(poly)
        for a in range(n):
            x1, y1 = poly[a]
            x2, y2 = poly[(a + 1) % n]
            steps = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
            for t in range(steps + 1):
                xx = int(x1 + (x2 - x1) * t / steps)
                yy = int(y1 + (y2 - y1) * t / steps)
                if 0 <= xx < W and 0 <= yy < H:
                    px[yy][xx] = (8, 10, 16)
    for x, y in pts:
        xx, yy = to_px(x, y)
        if 0 <= int(xx) < W and 0 <= int(yy) < H:
            px[int(yy)][int(xx)] = (235, 240, 250)

    os.makedirs(os.path.dirname(OUT_PNG), exist_ok=True)
    write_png(OUT_PNG, W, H, px)
    print("[+] wrote %s" % os.path.relpath(OUT_PNG, ROOT))

    print("\n=== SUMMARY ===")
    print("  %-12s %-9s %7s %9s %s" % ("REGION", "OWNER", "POSTAL", "AREA km2", "NOTES"))
    for i in range(k):
        rid = binding[i]
        d = REGION_DESIGN[rid]
        note = ("CONTESTED (claimed by %s)" % d["claimed_by"]) if d["contested"] else (
            "CAPITAL" if d["capital"] else "")
        a = abs(area2(cells[i])) / 2.0 / 1.0e6
        print("  %-12s %-9s %7d %9.2f %s" % (rid, d["owner"], counts[rid], a, note))
    pop = {"rsa": 0, "fzsa": 0}
    for rid, d in REGION_DESIGN.items():
        pop[d["owner"]] += d["pop"]
    print("  population: RSA {:,}   FZSA {:,}".format(pop["rsa"], pop["fzsa"]))
    print("\n%s" % ("BUILD OK" if ok else "BUILD COMPLETED WITH VERIFICATION FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
