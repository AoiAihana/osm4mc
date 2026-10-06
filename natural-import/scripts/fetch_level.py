"""后台抓取某一层级的 Dynmap 瓦片。

用法：python3 fetch_level.py <level> [workers]
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dynmap_tiles import DynmapTiles

WORLD_BOUNDS = (-1024, -17440, 23552, 6112)   # x0, z0, x1, z1（dyn2xyz 扫出来的范围）

lock = threading.Lock()
done = fail = missing = 0
start = time.time()


def main():
    level = int(sys.argv[1])
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    t = DynmapTiles(level=level, workers=workers)
    x0, z0, x1, z1 = WORLD_BOUNDS
    m = 1 << level
    nx0, ny0 = t.tile_index(x0, z0)
    nx1, ny1 = t.tile_index(x1, z1)
    tiles = [(x, y) for x in range(nx0, nx1 + 1, m) for y in range(ny0, ny1 + 1, m)]
    print(f"L{level}: {len(tiles)} tiles, {t.blocks_per_tile:.0f} blocks/tile, {t.scale:.3f} px/block", flush=True)

    def one(tile):
        global done, fail, missing
        img = t.tile(*tile)
        with lock:
            done += 1
            if img is None:
                missing += 1
            if done % 200 == 0:
                el = time.time() - start
                print(f"[{done}/{len(tiles)}] {el:.0f}s {done/el:.2f} t/s missing={missing}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, tiles))
    el = time.time() - start
    print(f"DONE {done} tiles in {el:.0f}s missing={missing}", flush=True)


if __name__ == "__main__":
    main()
