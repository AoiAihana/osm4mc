"""把构建好的 SFRT 数据画在上游 Dynmap 影像上，用于人工核对。

用法：
    python3 preview.py --level 5 --out preview_all.png
    python3 preview.py --level 3 --center 11521 -4826 --half 700 --out preview_frtbn.png
    python3 preview.py --level 3 --center 9450 -2500 --half 700 --out preview_cygk.png --osm
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")
sys.path.insert(0, "/home/aoiaihana/osm/.work/natural")

from PIL import Image, ImageDraw  # noqa: E402

import geohelper as g  # noqa: E402
from dynmap_tiles import DynmapTiles  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

COLORS = {"subway": (0, 110, 255), "light_rail": (0, 190, 80)}
CONTACT_COLOR = (255, 130, 0)
OSM_COLOR = (150, 150, 150)


def load_change(path):
    root = ET.parse(path).getroot()
    nodes, ways = {}, []
    for el in root.iter():
        if el.tag == "node":
            nodes[int(el.get("id"))] = (float(el.get("lon")), float(el.get("lat")))
        elif el.tag == "way":
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            ways.append((tags, [int(nd.get("ref")) for nd in el.findall("nd")]))
    return nodes, ways


def load_osm_railways():
    sql = ("select ST_AsText(way) from planet_osm_line where railway is not null;")
    p = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
                        "-d", "gis", "-t", "-A", "-c", sql],
                       cwd="/home/aoiaihana/osm", capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        if not line.strip().startswith("LINESTRING"):
            continue
        body = line.split("(", 1)[1].rsplit(")", 1)[0]
        pts = []
        for pair in body.split(","):
            xs, ys = pair.split()
            pts.append((float(xs) + 11168.0, -float(ys) - 5568.0))
        out.append(pts)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=5)
    ap.add_argument("--center", nargs=2, type=float)
    ap.add_argument("--half", type=float, default=0)
    ap.add_argument("--out", default=os.path.join(HERE, "preview_sfrt.png"))
    ap.add_argument("--osm", action="store_true", help="同时叠加本地 OSM 已有铁路（灰）")
    ap.add_argument("--change", default=os.path.join(HERE, "sfrt.osmchange.xml"))
    args = ap.parse_args()

    nodes, ways = load_change(args.change)
    polys = [(t, [(g.lonlat_to_block(*nodes[r])) for r in refs if r in nodes]) for t, refs in ways]
    polys = [(t, p) for t, p in polys if len(p) >= 2]
    print(f"ways={len(polys)} nodes={len(nodes)}")

    xs = [p[0] for _, pl in polys for p in pl]
    zs = [p[1] for _, pl in polys for p in pl]
    if args.center and args.half:
        bx0, bx1 = args.center[0] - args.half, args.center[0] + args.half
        bz0, bz1 = args.center[1] - args.half, args.center[1] + args.half
    else:
        bx0, bx1 = min(xs) - 150, max(xs) + 150
        bz0, bz1 = min(zs) - 150, max(zs) + 150
    print(f"extent x {bx0:.0f}..{bx1:.0f}  z {bz0:.0f}..{bz1:.0f}")

    dt = DynmapTiles(level=args.level)
    m = 1 << args.level
    nx0, ny0 = dt.tile_index(bx0, bz0)
    nx1, ny1 = dt.tile_index(bx1, bz1)
    tiles = [(nx, ny) for nx in range(nx0, nx1 + 1, m) for ny in range(ny0, ny1 + 1, m)]
    print(f"tiles {len(tiles)}")
    dt.prefetch(tiles)
    T = int(dt.scale * dt.blocks_per_tile)
    cols = (nx1 - nx0) // m + 1
    rows = (ny1 - ny0) // m + 1
    M = Image.new("RGB", (cols * T, rows * T), (20, 20, 20))
    for nx in range(nx0, nx1 + 1, m):
        for ny in range(ny0, ny1 + 1, m):
            img = dt.tile(nx, ny)
            if img is None:
                continue
            M.paste(img, (((nx - nx0) // m) * T, ((ny - ny0) // m) * T))
    print("mosaic", M.size)

    def to_px(bx, bz):
        return ((bx - nx0 * 32.0) / (32.0 * m) * T, (bz - 32.0 * (ny0 - 1)) / (32.0 * m) * T)

    d = ImageDraw.Draw(M)
    if args.osm:
        for pts in load_osm_railways():
            pp = [to_px(bx, bz) for bx, bz in pts]
            if max(p[0] for p in pp) < 0 or min(p[0] for p in pp) > M.size[0]:
                continue
            d.line(pp, fill=OSM_COLOR, width=1)
    for tags, pl in polys:
        name = tags.get("name", "")
        rail = tags.get("railway", "subway")
        col = CONTACT_COLOR if name == "联络线" else COLORS.get(rail, (255, 0, 255))
        if tags.get("tunnel") == "yes":
            col = tuple(int(c * 0.5 + 70) for c in col)
        d.line([to_px(bx, bz) for bx, bz in pl], fill=col, width=2 if M.size[0] < 4000 else 3)
    for nid, (lon, lat) in nodes.items():
        bx, bz = g.lonlat_to_block(lon, lat)
        x, y = to_px(bx, bz)
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255), outline=(0, 0, 0))
    M.save(args.out)
    print("saved", args.out, M.size)


if __name__ == "__main__":
    main()
