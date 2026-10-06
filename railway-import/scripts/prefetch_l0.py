"""后台抓取 level-0 上游瓦片。用法：python3 prefetch_l0.py tiles_l0.txt [workers]"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import l0imagery as L0

lock = threading.Lock()
done = 0
fail = 0
empty = 0
start = time.time()


def fetch(t):
    global done, fail, empty
    nx, ny = t
    p = L0.CACHE_DIR + f"/{nx}_{ny}.jpg"
    if os.path.exists(p) or os.path.exists(p + ".missing"):
        with lock:
            done += 1
        return
    cache = L0.L0Cache(workers=1)
    img = cache._download(nx, ny)
    with lock:
        done += 1
        if img is None:
            if os.path.exists(p + ".missing"):
                empty += 1
            else:
                fail += 1
        if done % 200 == 0:
            el = time.time() - start
            print(f"[{done}] {el:.0f}s {done/el:.2f} t/s fail={fail} empty={empty}", flush=True)


def main():
    tiles = []
    with open(sys.argv[1]) as fh:
        for line in fh:
            line = line.strip()
            if line:
                x, y = line.split()
                tiles.append((int(x), int(y)))
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    print(f"tiles={len(tiles)} workers={workers}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(fetch, tiles))
    el = time.time() - start
    print(f"DONE {done} in {el:.0f}s ({done/el:.2f}/s) fail={fail} empty={empty}", flush=True)


if __name__ == "__main__":
    main()
