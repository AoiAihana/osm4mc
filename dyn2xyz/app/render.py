"""render.py —— 把 Dynmap 瓦片实时合成为 XYZ 瓦片。

流程（全部在内存里完成，**不落盘**）::

    XYZ (z,x,y)
      -> 墨卡托米        (geo.xyz_tile_meters)
      -> 方块范围        (geo.Placement.xyz_tile_block_bounds)
      -> 选一个 Dynmap 层级 L
      -> 层级 L 的像素窗口 (DynmapGeometry.level_pixels)
      -> 找出覆盖该窗口的源瓦片，并发抓取
      -> 拼成马赛克
      -> 仿射变换（裁剪 + 缩放一次完成）到输出尺寸
      -> 编码返回

仿射变换里包含半个像素的修正，保证输出瓦片与 XYZ 网格严格对齐。
"""

from __future__ import annotations

import io
import logging
import math
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

from PIL import Image

from .dynmap import DynmapSource, http_get
from .geo import XYZ_TILE_PX, WORLD_WIDTH, Placement

log = logging.getLogger("dyn2xyz.render")


class TileUnavailable(RuntimeError):
    """该 XYZ 瓦片无法生成（源瓦片太多、超出范围等）。"""


# ---------------------------------------------------------------------------
# 源瓦片缓存（纯内存）
# ---------------------------------------------------------------------------


class TileCache:
    """按 URL 缓存的 LRU。只存解码后的 RGB 图，进程退出即消失。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(0, capacity)
        self._items: OrderedDict[str, Image.Image | None] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[bool, Image.Image | None]:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                self.hits += 1
                return True, self._items[key]
            self.misses += 1
            return False, None

    def put(self, key: str, value: Image.Image | None) -> None:
        if self.capacity == 0:
            return
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "capacity": self.capacity,
                "size": len(self._items),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class RenderSettings:
    """渲染与输出参数（来自配置文件的 ``[render]`` / ``[output]`` 段）。"""

    output_px: int = 256
    overzoom: int = 2
    underzoom: int = 0
    max_source_tiles: int = 16
    #: 一张 XYZ 瓦片最多等源瓦片多久（秒）；到点就用已到的拼图返回。
    render_deadline: float = 10.0
    fetch_workers: int = 8
    fetch_timeout: float = 20.0
    fetch_retries: int = 2
    cache_tiles: int = 1024
    background: str = "#00000000"
    flatten: str = "#000000"
    jpeg_quality: int = 82
    resample: str = "bilinear"

    def resample_filter(self):
        return {
            "nearest": Image.NEAREST,
            "bilinear": Image.BILINEAR,
            "bicubic": Image.BICUBIC,
            "lanczos": Image.LANCZOS,
        }[self.resample]


def parse_color(value: str) -> tuple[int, int, int, int]:
    """解析 ``#rgb`` / ``#rrggbb`` / ``#rrggbbaa``。"""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        raise ValueError(f"颜色格式不合法: {value!r}（应为 #rgb / #rrggbb / #rrggbbaa）")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 渲染器
# ---------------------------------------------------------------------------


class TileRenderer:
    """把 XYZ 请求翻译成 Dynmap 请求并合成输出。"""

    def __init__(self, source: DynmapSource, placement: Placement, settings: RenderSettings) -> None:
        self.source = source
        self.placement = placement
        self.settings = settings
        self.cache = TileCache(settings.cache_tiles)
        self._pool = ThreadPoolExecutor(max_workers=max(1, settings.fetch_workers),
                                        thread_name_prefix="dyn2xyz-fetch")
        self._bg = parse_color(settings.background)
        self._flatten = parse_color(settings.flatten)

    # -- 缩放层级 -----------------------------------------------------------
    @property
    def native_zoom_exact(self) -> float:
        """Dynmap 原生层（L=0）在数值上等价于哪个**实数** XYZ 缩放层级。

        推导：令 XYZ 的米/像素 等于 Dynmap 原生层的米/像素

            WORLD_WIDTH / (XYZ_TILE_PX * 2^z) = (1 / scale_x) * meters_per_block

        解出 ``z = log2(WORLD_WIDTH * scale_x / (XYZ_TILE_PX * meters_per_block))``。
        """
        g = self.source.geometry
        return math.log2(WORLD_WIDTH * abs(g.scale_x)
                          / (XYZ_TILE_PX * self.placement.meters_per_block))

    @property
    def native_zoom(self) -> int:
        return round(self.native_zoom_exact)

    @property
    def minzoom(self) -> int:
        """最粗层级：再往外就要拼太多源瓦片了（对应 Dynmap 的 mapzoomout）。"""
        return max(0, self.native_zoom - self.source.geometry.mapzoomout - self.settings.underzoom)

    @property
    def maxzoom(self) -> int:
        """最细层级：超过原生层只是放大，没有新信息。"""
        return min(30, self.native_zoom + self.settings.overzoom)

    def level_for_zoom(self, z: int) -> int:
        """XYZ 层级 -> Dynmap 层级 L（夹取到可提供的范围）。"""
        ideal = self.native_zoom_exact - z
        return max(0, min(self.source.geometry.mapzoomout, round(ideal)))

    # -- 源瓦片抓取 ---------------------------------------------------------
    def _load_source_tile(self, nlx: int, nly: int, level: int) -> Image.Image | None:
        url = self.source.tile_url(nlx, nly, level)
        found, cached = self.cache.get(url)
        if found:
            return cached

        img: Image.Image | None = None
        last_exc: Exception | None = None
        for attempt in range(self.settings.fetch_retries + 1):
            try:
                raw = http_get(url, timeout=self.settings.fetch_timeout,
                               user_agent=self.source.user_agent)
                with Image.open(io.BytesIO(raw)) as im:
                    im.load()
                    img = im.convert("RGBA")
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (204, 404, 410):
                    # Dynmap 对未渲染/越界的瓦片返回 404；这不是错误，而是「这里没东西」。
                    log.debug("源瓦片缺失 %s (HTTP %s)", url, exc.code)
                    img = None
                    break
                last_exc = exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            if attempt < self.settings.fetch_retries:
                log.debug("重试源瓦片 %s (%s/%s)", url, attempt + 1, self.settings.fetch_retries)

        if img is None and last_exc is not None:
            log.warning("源瓦片抓取失败 %s: %s", url, last_exc)

        self.cache.put(url, img)
        return img

    # -- 主流程 -------------------------------------------------------------
    def render(self, z: int, x: int, y: int) -> Image.Image:
        """生成 XYZ 瓦片。可能抛 :class:`TileUnavailable`。"""
        s = self.settings
        g = self.source.geometry

        bx0, bz0, bx1, bz1 = self.placement.xyz_tile_block_bounds(z, x, y)
        level = self.level_for_zoom(z)

        # 该 XYZ 瓦片在 Dynmap 层级 L 的像素空间里占据的窗口
        px0, py0 = g.level_pixels(bx0, bz0, level)   # 西北角
        px1, py1 = g.level_pixels(bx1, bz1, level)   # 东南角
        px0, px1 = sorted((px0, px1))
        py0, py1 = sorted((py0, py1))

        t = g.tile_px
        eps = 1e-9
        # px0/py0 是「层级 L 的像素空间」，一个瓦片占 t 像素。
        # 该空间里的格子编号是**粗索引** k，真正的 Dynmap 瓦片索引是 k << L
        # （对应源码 nL = 2^L * floor(n0 / 2^L)）。
        kx0, kx1 = math.floor(px0 / t), math.floor((px1 - eps) / t)
        ky0, ky1 = math.floor(py0 / t), math.floor((py1 - eps) / t)

        nx, ny = kx1 - kx0 + 1, ky1 - ky0 + 1
        if nx <= 0 or ny <= 0:
            raise TileUnavailable("瓦片窗口为空")
        if nx * ny > s.max_source_tiles:
            raise TileUnavailable(
                f"需要 {nx}×{ny}={nx * ny} 个源瓦片，超过上限 {s.max_source_tiles}；"
                f"该请求的缩放层级（z={z}）超出了本数据源能支撑的范围"
            )

        # 粗索引 -> 真实瓦片索引
        shift = level
        coords = [((kx0 + i) << shift, (ky0 + j) << shift)
                  for j in range(ny) for i in range(nx)]
        futures = [self._pool.submit(self._load_source_tile, cx, cy, level) for cx, cy in coords]

        # 软截止时间：最多等这么久，然后就用已经到手的源瓦片拼图。
        # 上游偶尔会长时间卡住（CDN 报 525、TLS 握手超时），如果一直等下去，
        # 一张 XYZ 瓦片可能要几十秒才返回，浏览器早就放弃了。宁可先给出一张
        # 局部有洞的图，也不要让请求挂死——慢到超时的瓦片对用户等于没有。
        # 没等到的 future 不取消：让它们在后台跑完，结果会进缓存，下次请求就命中。
        done, pending = wait(futures, timeout=s.render_deadline)
        if pending:
            log.debug("瓦片 %s/%s/%s: %d/%d 个源瓦片在 %.1fs 内没回来，先用已到的拼图",
                      z, x, y, len(pending), len(futures), s.render_deadline)

        mosaic = Image.new("RGBA", (nx * t, ny * t), self._bg)
        for ((cx, cy), fut), (i, j) in zip(zip(coords, futures),
                                           ((i, j) for j in range(ny) for i in range(nx))):
            if fut not in done:
                continue
            try:
                img = fut.result()
            except Exception as exc:  # noqa: BLE001 - 单张源瓦片失败不应让整张图失败
                log.debug("源瓦片 %s,%s@L%d 失败: %s", cx, cy, level, exc)
                continue
            if img is not None:
                mosaic.paste(img, (i * t, j * t))

        # 裁剪 + 缩放一次完成的仿射变换
        out = s.output_px
        sx = (px1 - px0) / out
        sy = (py1 - py0) / out
        # 半个像素修正：输出像素中心 <-> 源像素索引
        cx_off = (px0 - kx0 * t) + 0.5 * sx - 0.5
        cy_off = (py0 - ky0 * t) + 0.5 * sy - 0.5

        # 当渲染比例恰好让两套网格对齐时（缩放因子为 1、偏移为整数），直接裁剪即可：
        # 既更快，也避免重采样带来的 ±1 色阶抖动。
        # 偏移里那点 1e-9 量级的残差来自墨卡托三角函数的浮点误差，不是逻辑误差。
        if (abs(sx - 1.0) < 1e-12 and abs(sy - 1.0) < 1e-12
                and abs(cx_off - round(cx_off)) < 1e-6
                and abs(cy_off - round(cy_off)) < 1e-6):
            x0, y0 = round(cx_off), round(cy_off)
            return mosaic.crop((x0, y0, x0 + out, y0 + out))

        return mosaic.transform(
            (out, out), Image.AFFINE, (sx, 0.0, cx_off, 0.0, sy, cy_off),
            resample=s.resample_filter(),
        )

    # -- 编码 ---------------------------------------------------------------
    def encode(self, img: Image.Image, fmt: str) -> tuple[bytes, str]:
        """把图像编码成字节流，返回 ``(数据, Content-Type)``。"""
        fmt = (fmt or "png").lower()
        buf = io.BytesIO()
        if fmt in ("jpg", "jpeg"):
            bg = Image.new("RGBA", img.size, self._flatten)
            bg.alpha_composite(img)
            bg.convert("RGB").save(buf, format="JPEG", quality=self.settings.jpeg_quality,
                                   optimize=True, progressive=True)
            return buf.getvalue(), "image/jpeg"
        if fmt == "webp":
            img.save(buf, format="WEBP", quality=self.settings.jpeg_quality, method=4)
            return buf.getvalue(), "image/webp"
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"

    def tile(self, z: int, x: int, y: int, fmt: str) -> tuple[bytes, str]:
        return self.encode(self.render(z, x, y), fmt)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- 诊断 ---------------------------------------------------------------
    def describe(self) -> dict:
        g = self.source.geometry
        return {
            **self.source.describe(),
            "native_zoom_exact": round(self.native_zoom_exact, 4),
            "native_zoom": self.native_zoom,
            "minzoom": self.minzoom,
            "maxzoom": self.maxzoom,
            "level_per_zoom": {z: self.level_for_zoom(z) for z in range(self.minzoom, self.maxzoom + 1)},
            "output_px": self.settings.output_px,
            "max_source_tiles": self.settings.max_source_tiles,
            "cache": self.cache.stats(),
        }
