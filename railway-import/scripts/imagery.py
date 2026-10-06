"""影像工具：从本地 dyn2xyz 代理（edge :3000）取 XYZ 瓦片并缓存到磁盘。

用法示例：
    from imagery import TileCache, sample_blocks
    tc = TileCache()
    img, origin = tc.mosaic(bx0, bz0, bx1, bz1, z=19)
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

import geohelper as g

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tilecache")
os.makedirs(CACHE_DIR, exist_ok=True)

PROXY = "http://localhost:3000/tiles"


class TileCache:
    def __init__(self, z: int = 19, base: str = PROXY, workers: int = 6):
        self.z = z
        self.base = base.rstrip("/")
        self.workers = workers
        self._lock = threading.Lock()
        self._mem: dict[tuple[int, int], Image.Image | None] = {}
        self.stats = {"hit": 0, "miss": 0, "fail": 0}

    # -- 单个瓦片 --------------------------------------------------------
    def _path(self, x: int, y: int) -> str:
        return os.path.join(CACHE_DIR, f"z{self.z}_{x}_{y}.png")

    def _download(self, x: int, y: int) -> Image.Image | None:
        url = f"{self.base}/{self.z}/{x}/{y}.png"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "osm-railway-import/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if not data:
                return None
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            with open(self._path(x, y), "wb") as fh:
                fh.write(data)
            return img
        except Exception:
            with self._lock:
                self.stats["fail"] += 1
            return None

    def tile(self, x: int, y: int) -> Image.Image | None:
        key = (x, y)
        with self._lock:
            if key in self._mem:
                self.stats["hit"] += 1
                return self._mem[key]
        img = None
        p = self._path(x, y)
        if os.path.exists(p):
            try:
                img = Image.open(p).convert("RGBA")
            except Exception:
                img = None
        if img is None:
            img = self._download(x, y)
            with self._lock:
                self.stats["miss"] += 1
        with self._lock:
            self._mem[key] = img
        return img

    def prefetch(self, tiles: list[tuple[int, int]]) -> None:
        todo = []
        with self._lock:
            for t in set(tiles):
                if t in self._mem or os.path.exists(self._path(*t)):
                    continue
                todo.append(t)
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(lambda t: self.tile(*t), todo))

    # -- 大图拼接 --------------------------------------------------------
    def mosaic(self, bx0: float, bz0: float, bx1: float, bz1: float) -> tuple[Image.Image, tuple[float, float], float]:
        """拼出方块范围 [bx0,bx1] x [bz0,bz1] 的影像。

        返回 (图像, (左上角方块坐标), 每方块像素数)。
        """
        z = self.z
        tx0, ty0 = g.block_to_xyz(bx0, bz0, z)
        tx1, ty1 = g.block_to_xyz(bx1, bz1, z)
        tx0, tx1 = min(tx0, tx1), max(tx0, tx1)
        ty0, ty1 = min(ty0, ty1), max(ty0, ty1)
        ix0, iy0, ix1, iy1 = int(math.floor(tx0)), int(math.floor(ty0)), int(math.floor(tx1)), int(math.floor(ty1))
        tiles = [(x, y) for x in range(ix0, ix1 + 1) for y in range(iy0, iy1 + 1)]
        self.prefetch(tiles)
        w = (ix1 - ix0 + 1) * 256
        h = (iy1 - iy0 + 1) * 256
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for (x, y) in tiles:
            img = self.tile(x, y)
            if img is None:
                continue
            out.paste(img, ((x - ix0) * 256, (y - iy0) * 256))
        # 左上角瓦片左上角对应的方块坐标
        bxa, bza = g.xyz_to_block(z, ix0, iy0)
        bxb, bzb = g.xyz_to_block(z, ix0 + 1, iy0 + 1)
        blocks_per_px_x = (bxb - bxa) / 256.0
        blocks_per_px_y = (bzb - bza) / 256.0
        return out, (bxa, bza), (blocks_per_px_x, blocks_per_px_y)

    def block_to_px(self, bx: float, bz: float, origin: tuple[float, float], bpp: tuple[float, float]) -> tuple[float, float]:
        return (bx - origin[0]) / bpp[0], (bz - origin[1]) / bpp[1]
