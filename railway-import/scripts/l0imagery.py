"""直接从上游 Dynmap 抓 level-0 瓦片（原生分辨率 4 像素/方块），缓存到磁盘。

上游：https://map.bilicraft.com/s2/hyperion ，世界 Paralon，地图 flat。
level-0 瓦片：128×128 像素，覆盖 32×32 方块。
    瓦片索引  nx = floor(4 * bx / 128) = floor(bx / 32)
              ny = floor((128 + 4 * bz) / 128)
    文件路径  <webroot>/tiles/Paralon/flat/<nx>>5>_<(-ny)>>5>/<nx>_<-ny>.jpg
    瓦片内像素 px = 4*bx - 128*nx, py = (128 + 4*bz) - 128*ny
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
CACHE_DIR = os.path.join(HERE, "l0cache")
os.makedirs(CACHE_DIR, exist_ok=True)

WEBROOT = "https://map.bilicraft.com/s2/hyperion"
WORLD = "Paralon"
PREFIX = "flat"
EXT = "jpg"
TILE_PX = 128
PX_PER_BLOCK = 4.0


def tile_index(bx: float, bz: float) -> tuple[int, int]:
    nx = math.floor(PX_PER_BLOCK * bx / TILE_PX)
    ny = math.floor((TILE_PX - (-PX_PER_BLOCK) * bz) / TILE_PX)
    return nx, ny


def tile_block_bounds(nx: int, ny: int) -> tuple[float, float, float, float]:
    """瓦片覆盖的方块范围（bx_min, bz_min, bx_max, bz_max）。"""
    bx0 = nx * TILE_PX / PX_PER_BLOCK
    bx1 = (nx + 1) * TILE_PX / PX_PER_BLOCK
    bz0 = (TILE_PX - (ny + 1) * TILE_PX) / (-PX_PER_BLOCK)
    bz1 = (TILE_PX - ny * TILE_PX) / (-PX_PER_BLOCK)
    return bx0, min(bz0, bz1), bx1, max(bz0, bz1)


def tile_url(nx: int, ny: int) -> str:
    shardx = nx >> 5
    shardy = (-ny) >> 5
    return f"{WEBROOT}/tiles/{WORLD}/{PREFIX}/{shardx}_{shardy}/{nx}_{-ny}.{EXT}"


class L0Cache:
    def __init__(self, workers: int = 6, timeout: float = 30.0):
        self.workers = workers
        self.timeout = timeout
        self._mem: dict[tuple[int, int], Image.Image | None] = {}
        self._lock = threading.Lock()
        self.stats = {"hit": 0, "miss": 0, "fail": 0, "empty": 0}
        # 单线程采样用的快速路径（避免每个像素都加锁查表）
        self._last_key: tuple[int, int] | None = None
        self._last_img: Image.Image | None = None
        self._last_px = None

    def _path(self, nx: int, ny: int) -> str:
        return os.path.join(CACHE_DIR, f"{nx}_{ny}.jpg")

    def _download(self, nx: int, ny: int) -> Image.Image | None:
        url = tile_url(nx, ny)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (osm-railway-import)"})
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
                        self.stats["empty"] += 1
                    # 记录为缺失，避免反复请求
                    with open(self._path(nx, ny) + ".missing", "w") as fh:
                        fh.write("404")
                    return None
                time.sleep(1.0 + attempt)
            except Exception:
                time.sleep(1.0 + attempt)
        with self._lock:
            self.stats["fail"] += 1
        return None

    def tile(self, nx: int, ny: int) -> Image.Image | None:
        key = (nx, ny)
        with self._lock:
            if key in self._mem:
                self.stats["hit"] += 1
                return self._mem[key]
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
            self._mem[key] = img
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

    def pixel(self, bx: float, bz: float):
        nx, ny = tile_index(bx, bz)
        if self._last_key != (nx, ny):
            self._last_img = self.tile(nx, ny)
            self._last_key = (nx, ny)
        img = self._last_img
        if img is None:
            return None
        px = int(PX_PER_BLOCK * bx - TILE_PX * nx)
        py = int((TILE_PX + PX_PER_BLOCK * bz) - TILE_PX * ny)
        px = max(0, min(TILE_PX - 1, px))
        py = max(0, min(TILE_PX - 1, py))
        return img.getpixel((px, py))
