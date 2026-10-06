"""后台批量抓取 z=19 瓦片到 tilecache/（配合 imagery.TileCache 使用）。

用法：python3 prefetch_tiles.py tiles_z19.txt [workers]
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

Z = 19
BASE = "http://localhost:3000/tiles"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tilecache")
os.makedirs(CACHE, exist_ok=True)

lock = threading.Lock()
done = 0
fail = 0
empty = 0
start = time.time()


def path(x: int, y: int) -> str:
    return os.path.join(CACHE, f"z{Z}_{x}_{y}.png")


def fetch(t: tuple[int, int]) -> None:
    global done, fail, empty
    x, y = t
    p = path(x, y)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        with lock:
            done += 1
        return
    url = f"{BASE}/{Z}/{x}/{y}.png"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "osm-railway-import/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if data:
                tmp = p + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, p)
                if len(data) < 2000:
                    with lock:
                        empty += 1
            break
        except Exception:
            if attempt == 2:
                with lock:
                    fail += 1
                return
            time.sleep(1.0 + attempt)
    with lock:
        done += 1
        if done % 100 == 0:
            el = time.time() - start
            print(f"[{done}] {el:.0f}s {done/el:.2f} tiles/s fail={fail} empty={empty}", flush=True)


def main() -> None:
    tiles = []
    with open(sys.argv[1]) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            x, y = line.split()
            tiles.append((int(x), int(y)))
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print(f"tiles={len(tiles)} workers={workers}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(fetch, tiles))
    el = time.time() - start
    print(f"DONE {done} tiles in {el:.0f}s ({done/el:.2f}/s) fail={fail} empty={empty}", flush=True)


if __name__ == "__main__":
    main()
