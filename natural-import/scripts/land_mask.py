"""从 OSM 的海岸线（natural=coastline）生成"陆地掩膜"，用于把自然要素裁到海岸线以内。

* 海岸线 way 可能首尾相接分成多段：先按端点把开链接成闭环。
* 把闭环栅格化到与分类相同的 32 方块网格：格子中心落在环内即算陆地。
"""
from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, "/home/aoiaihana/osm/.work/railwaymap")
import geohelper as g  # noqa: E402


def load_coastline(path="/home/aoiaihana/osm/.work/natural/coastline_nodes.txt"):
    ways = defaultdict(list)
    for line in open(path):
        wid, seq, nid, lat, lon = line.strip().split("|")
        bx, bz = g.lonlat_to_block(float(lon), float(lat))
        ways[int(wid)].append((int(nid), bx, bz))
    return ways


def build_rings(ways):
    rings = []
    open_ways = []
    for wid, pts in ways.items():
        if pts[0][0] == pts[-1][0]:
            rings.append(pts)
        else:
            open_ways.append(pts)
    # 把开链按端点接起来
    ends = defaultdict(list)
    for i, pts in enumerate(open_ways):
        ends[pts[0][0]].append(i)
        ends[pts[-1][0]].append(i)
    used = [False] * len(open_ways)
    for i, pts in enumerate(open_ways):
        if used[i]:
            continue
        used[i] = True
        chain = list(pts)
        # 向后接
        while True:
            tail = chain[-1][0]
            nxt = [j for j in ends[tail] if not used[j] and j != i]
            if not nxt:
                break
            j = nxt[0]
            used[j] = True
            seg = open_ways[j]
            if seg[0][0] == tail:
                chain.extend(seg[1:])
            else:
                chain.extend(list(reversed(seg))[1:])
        # 向前接
        while True:
            head = chain[0][0]
            nxt = [j for j in ends[head] if not used[j]]
            if not nxt:
                break
            j = nxt[0]
            used[j] = True
            seg = open_ways[j]
            if seg[-1][0] == head:
                chain = list(seg[:-1]) + chain
            else:
                chain = list(reversed(seg))[:-1] + chain
        if chain[0][0] == chain[-1][0]:
            rings.append(chain)
        else:
            print("警告：接不成闭环，端点", chain[0][0], chain[-1][0])
    return rings


def land_mask(rings, origin, cell, shape, close=True):
    """返回 bool 数组：True=陆地。"""
    h, w = shape
    img = Image.new("1", (w, h), 0)
    dr = ImageDraw.Draw(img)
    for ring in rings:
        pts = [((bx - origin[0]) / cell, (bz - origin[1]) / cell) for _, bx, bz in ring]
        if close and len(pts) >= 3:
            dr.polygon(pts, fill=1)
    return np.array(img, dtype=bool)


def main():
    d = np.load("/home/aoiaihana/osm/.work/natural/grid32.npz")
    feats = d["feats"]
    valid = d["valid"]
    ox, oz = d["origin"]
    cell = float(d["cell"])
    ways = load_coastline()
    rings = build_rings(ways)
    print(f"海岸线闭环 {len(rings)} 个")
    mask = land_mask(rings, (ox, oz), cell, valid.shape)
    print(f"陆地格 {int(mask.sum())} / 有效格 {int(valid.sum())}")
    np.save("/home/aoiaihana/osm/.work/natural/land_mask.npy", mask)
    # 预览
    lab = np.load("/home/aoiaihana/osm/.work/natural/labels_v3.npy")
    img = np.zeros((*mask.shape, 3), dtype=np.uint8)
    img[mask] = (200, 220, 180)
    img[~mask & valid] = (90, 140, 200)
    img[~valid] = (40, 40, 40)
    relabel = {1: (40, 90, 200), 2: (255, 255, 255), 3: (235, 215, 150), 4: (25, 80, 30),
               5: (140, 200, 100), 6: (180, 170, 100), 7: (150, 150, 155)}
    for k, c in relabel.items():
        sel = (lab == k) & mask & (k != 1)
        img[sel] = c
    Image.fromarray(img).save("/home/aoiaihana/osm/.work/natural/land_mask_preview.png")
    print("saved land_mask_preview.png")


if __name__ == "__main__":
    main()
