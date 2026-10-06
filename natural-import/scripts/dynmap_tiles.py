"""Dynmap 上游瓦片（任意 zoomout 层级 L）的抓取与缓存。

层级 L 的瓦片：128×128 像素，覆盖 32·2^L 个方块（即 4/2^L 像素每方块）。
    瓦片索引  nx = floor(4·bx / (128·2^L))
              ny = floor((128 + 4·bz) / (128·2^L))
    文件路径  <webroot>/tiles/Paralon/flat/<nx>>5>_<(-ny)>>5>/<z*L>_<nx>_<-ny>.jpg
"""
from __future__ import annotations

import io
import math
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
WEBROOT = "https://map.bilicraft.com/s2/hyperion"
WORLD = "Paralon"
TILE_PX = 128


class DynmapTiles:
    def __init__(self, level: int = 4, prefix: str = "flat", workers: int = 8,
                 cache_root: str | None = None, timeout: float = 30.0):
        self.level = level
        self.prefix = prefix
        self.workers = workers
        self.timeout = timeout
        self.blocks_per_tile = 32.0 * (1 << level)     # 每瓦片覆盖的方块数
        self.scale = TILE_PX / self.blocks_per_tile    # 像素/方块
        self.cache_dir = os.path.join(cache_root or os.path.join(HERE, "dynmap_cache"),
                                      f"{prefix}_L{level}")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._mem: dict[tuple[int, int], Image.Image | None] = {}
        self._lock = threading.Lock()
        self.stats = {"hit": 0, "miss": 0, "fail": 0, "missing": 0}
        self._last_key = None
        self._last_img = None

    # -- 索引与 URL ------------------------------------------------------
    def tile_index(self, bx: float, bz: float) -> tuple[int, int]:
        """方块坐标 -> 层级 L 的瓦片索引。

        Dynmap 的索引是「L0 索引向下对齐到 2^L 的倍数」，不是简单的缩放：
            n0x = floor(bx / 32)          n0y = floor((128 + 4·bz) / 128)
            nLx = 2^L · floor(n0x / 2^L)  nLy = 2^L · floor(n0y / 2^L)
        所以层级 L 的瓦片覆盖 32·2^L 个方块。
        """
        m = 1 << self.level
        n0x = math.floor(bx / 32.0)
        n0y = math.floor((128.0 + 4.0 * bz) / 128.0)
        return m * (n0x // m), m * (n0y // m)

    def tile_block_bounds(self, nx: int, ny: int) -> tuple[float, float, float, float]:
        """瓦片覆盖的方块范围 (bx_min, bz_min, bx_max, bz_max)。"""
        m = 1 << self.level
        bx0 = nx * 32.0
        bx1 = (nx + m) * 32.0
        bz0 = 32.0 * (ny - 1)
        bz1 = 32.0 * (ny + m - 1)
        return bx0, bz0, bx1, bz1

    def tile_url(self, nx: int, ny: int) -> str:
        shardx = nx >> 5
        shardy = (-ny) >> 5
        stem = ("z" * self.level) + f"_{nx}_{-ny}" if self.level else f"{nx}_{-ny}"
        return f"{WEBROOT}/tiles/{WORLD}/{self.prefix}/{shardx}_{shardy}/{stem}.jpg"

    # -- 抓取 ------------------------------------------------------------
    def _path(self, nx: int, ny: int) -> str:
        return os.path.join(self.cache_dir, f"{nx}_{ny}.jpg")

    def _download(self, nx: int, ny: int) -> Image.Image | None:
        url = self.tile_url(nx, ny)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "osm-natural-import/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = r.read()
                if not data:
                    return None
                tmp = self._path(nx, ny) + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, self._path(nx, ny))
                return Image.open(io.BytesIO(data)).convert("RGB")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    with self._lock:
                        self.stats["missing"] += 1
                    open(self._path(nx, ny) + ".missing", "w").close()
                    return None
                time.sleep(1.0 + attempt)
            except Exception:
                time.sleep(1.0 + attempt)
        with self._lock:
            self.stats["fail"] += 1
        return None

    def tile(self, nx: int, ny: int) -> Image.Image | None:
        if self._last_key != (nx, ny):
            pass
        with self._lock:
            if (nx, ny) in self._mem:
                self.stats["hit"] += 1
                return self._mem[(nx, ny)]
        p = self._path(nx, ny)
        img = None
        if os.path.exists(p):
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                img = None
        elif os.path.exists(p + ".missing"):
            img = None
        else:
            img = self._download(nx, ny)
            with self._lock:
                self.stats["miss"] += 1
        with self._lock:
            self._mem[(nx, ny)] = img
        return img

    def prefetch(self, tiles) -> None:
        todo = []
        with self._lock:
            for t in set(tiles):
                if t in self._mem or os.path.exists(self._path(*t)) or os.path.exists(self._path(*t) + ".missing"):
                    continue
                todo.append(t)
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(lambda t: self.tile(*t), todo))
