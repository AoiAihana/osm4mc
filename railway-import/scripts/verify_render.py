"""核对：从渲染库（gis.planet_osm_line）取出铁路线，叠加到 Minecraft 影像上，
检查线位是否贴着轨道、bridge/tunnel 是否合理。

用法：python3 verify_render.py [--out verify.png] [--spots "x,z;x,z;..."]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess

from PIL import Image, ImageDraw

import geohelper as g
import l0imagery as L0

HERE = os.path.dirname(os.path.abspath(__file__))


def query_lines(where="railway IS NOT NULL", db="gis"):
    sql = (f"SELECT osm_id, name, railway, bridge, tunnel, layer, "
           f"ST_AsGeoJSON(ST_Transform(way, 4326)) FROM planet_osm_line WHERE {where};")
    out = subprocess.run(
        ["docker", "exec", "osm-db-1", "psql", "-U", "postgres", "-d", db, "-At", "-F", "\t", "-c", sql],
        capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        osm_id, name, railway, bridge, tunnel, layer, geom = parts[:7]
        try:
            gj = json.loads(geom)
        except Exception:
            continue
        coords = [(c[0], c[1]) for c in gj["coordinates"]]
        rows.append({"osm_id": int(osm_id), "name": name, "bridge": bridge, "tunnel": tunnel,
                     "layer": layer, "coords": coords})
    return rows


def render(rows, spots, out_path, half=260, scale=1.0):
    cache = L0.L0Cache(workers=8)
    panels = []
    for (bx, bz, title) in spots:
        n0x, n0y = L0.tile_index(bx - half, bz - half)
        n1x, n1y = L0.tile_index(bx + half, bz + half)
        cache.prefetch([(a, b) for a in range(n0x, n1x + 1) for b in range(n0y, n1y + 1)])
        W = (n1x - n0x + 1) * 128
        H = (n1y - n0y + 1) * 128
        M = Image.new("RGB", (W, H), (0, 0, 0))
        for a in range(n0x, n1x + 1):
            for b in range(n0y, n1y + 1):
                im = cache.tile(a, b)
                if im:
                    M.paste(im, ((a - n0x) * 128, (b - n0y) * 128))
        ox = n0x * 32.0
        oz = (n0y - 1) * 32.0
        d = ImageDraw.Draw(M)
        for r in rows:
            pts = []
            for lon, lat in r["coords"]:
                p = g.lonlat_to_block(lon, lat)
                if abs(p[0] - bx) < half + 200 and abs(p[1] - bz) < half + 200:
                    pts.append(((p[0] - ox) * 4, (p[1] - oz) * 4))
            if len(pts) < 2:
                continue
            if r["tunnel"] == "yes":
                col = (255, 140, 0)
            elif r["bridge"] == "yes":
                col = (0, 220, 0)
            else:
                col = (255, 0, 0)
            d.line(pts, fill=col, width=2)
        d.text((10, 10), title, fill=(255, 255, 0))
        if scale != 1.0:
            M = M.resize((int(M.width * scale), int(M.height * scale)), Image.LANCZOS)
        panels.append(M)
    W = sum(p.width for p in panels)
    H = max(p.height for p in panels)
    out = Image.new("RGB", (W, H), (0, 0, 0))
    x = 0
    for p in panels:
        out.paste(p, (x, 0))
        x += p.width
    if out.width > 2400:
        f = 2400 / out.width
        out = out.resize((int(out.width * f), int(out.height * f)), Image.LANCZOS)
    out.save(out_path)
    return out.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="verify.png")
    ap.add_argument("--spots", default="")
    ap.add_argument("--half", type=int, default=260)
    args = ap.parse_args()
    rows = query_lines()
    print(f"渲染库中的铁路线：{len(rows)} 条（bridge={sum(1 for r in rows if r['bridge']=='yes')} "
          f"tunnel={sum(1 for r in rows if r['tunnel']=='yes')}）")
    if args.spots:
        spots = []
        for item in args.spots.split(";"):
            parts = item.split(",")
            spots.append((float(parts[0]), float(parts[1]), parts[2] if len(parts) > 2 else "spot"))
    else:
        spots = [(2731, -191, "长岛站"), (4319, -12201, "雪原"), (4070, -11704, "高架"),
                 (11973, -7265, "站场"), (6307, -3951, "腾龙海港")]
    print(render(rows, spots, args.out, half=args.half))


if __name__ == "__main__":
    main()
