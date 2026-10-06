"""按海岸线裁剪自然要素：陆地类别只允许出现在陆地掩膜内。

做法：
  * 分类结果 labels_v3.npy 是 32 方块/格；升采样到 CELL_FINE=16 方块/格；
  * 用 OSM 的海岸线环在这一分辨率上栅格化出陆地掩膜；
  * 陆地类别（雪/沙/林/草/稀树/岩）落在海里的一律改成"水"，于是面自然止于海岸线；
  * 重新成面（抽稀容差 1 格 = 16 方块），面积阈值按分辨率换算。
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))
sys.path.insert(0, HERE)

from land_mask import build_rings, land_mask, load_coastline  # noqa: E402
from polygons import mask_to_polygons                        # noqa: E402

FINE = 16                      # 方块/格
LAND_CLASSES = (2, 3, 4, 5, 6, 7)     # glacier,sand,wood,grassland,scrub,bare_rock
MIN_CELLS_32 = {1: 30, 2: 200, 3: 30, 4: 150, 5: 60, 6: 150, 7: 100}
NAMES = {1: "water", 2: "glacier", 3: "sand", 4: "wood", 5: "grassland", 6: "scrub", 7: "bare_rock"}


def main():
    d = np.load(os.path.join(HERE, "grid32.npz"))
    ox, oz = d["origin"]
    cell = float(d["cell"])
    lab = np.load(os.path.join(HERE, "labels_v3.npy"))

    up = int(cell // FINE)
    lab_fine = np.repeat(np.repeat(lab, up, axis=0), up, axis=1)
    print("升采样后", lab_fine.shape, "格宽", FINE, "方块")

    rings = build_rings(load_coastline())
    t0 = time.time()
    land = land_mask(rings, (ox, oz), FINE, lab_fine.shape)
    print(f"陆地掩膜 {int(land.sum())} / {land.size} 格（{time.time()-t0:.1f}s）")

    before = {k: int((lab_fine == k).sum()) for k in LAND_CLASSES}
    for k in LAND_CLASSES:
        lab_fine[(lab_fine == k) & ~land] = 1        # 海里的陆地面 -> 水
    after = {k: int((lab_fine == k).sum()) for k in LAND_CLASSES}
    for k in LAND_CLASSES:
        print(f"  {NAMES[k]:10s} {before[k]:8d} -> {after[k]:8d} 格（裁掉 {before[k]-after[k]}）")
    np.save(os.path.join(HERE, "labels_fine.npy"), lab_fine)
    np.save(os.path.join(HERE, "land_mask_fine.npy"), land)

    # 重新成面
    factor = (cell / FINE) ** 2      # 32 格 -> 16 格的换算
    polys = {}
    t0 = time.time()
    for k, name in NAMES.items():
        kw = dict(min_cells=max(4, int(MIN_CELLS_32[k] * factor)), eps_cells=1.0)
        if name == "water":
            kw.update(skip_touching_border=True, max_cells=int(40000 * factor))
        polys[name] = mask_to_polygons(lab_fine == k, (ox, oz), FINE, **kw)
        print(f"  {name:10s} 面 {len(polys[name]):4d} 节点 {sum(len(p['ring']) for p in polys[name]):6d}")
    print(f"成面耗时 {time.time()-t0:.1f}s，总节点 "
          f"{sum(len(p['ring']) for v in polys.values() for p in v)}")
    json.dump({k: [{"ring": p["ring"], "cells": p["cells"], "area": p["area"]} for p in v]
               for k, v in polys.items()},
              open(os.path.join(HERE, "polygons_coast.json"), "w"))


if __name__ == "__main__":
    main()
