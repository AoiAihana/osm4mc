"""dynmap.py —— Dynmap 瓦片源的解析、几何建模与 URL 生成。

Dynmap 的瓦片布局（源码依据见 mcmap/docs/02-dynmap.md）
------------------------------------------------------
磁盘/URL 路径::

    <webroot>/tiles/<world>/<prefix>/<x>>5>_<y>>5>/<zoomprefix>_<x>_<y>.<ext>

其中：

* ``<prefix>`` 是地图前缀（常见 ``flat`` / ``surface`` / ``t``），后面可能跟 ``_day``。
* ``<x>>5>_<y>>5>`` 是**分片目录**：每 32×32 个瓦片一组。
* ``<zoomprefix>`` 是**字面量的一串 ``z``**，个数 = zoomout 层级 L。``L=0`` 时整个前缀省略。
* 文件名里的 ``<y>`` 是**已经取反过**的（源码注释：``// Y is inverted for HD-map.``）。

层级 L 的瓦片索引不是「第几格」，而是 level-0 索引向下对齐到 ``2^L`` 的倍数：

    n0  = floor(scale * block / tile_px)          # level 0
    nL  = 2^L * floor(n0 / 2^L)                   # level L
    filename_x, filename_y = nL_x, -nL_y

几何参数来自 ``<webroot>/standalone/dynmap_config.json`` 的 ``worldtomap`` 矩阵。
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: 仅当矩阵里的交叉项小于该相对阈值时才认为地图是「正北朝上」的
AXIS_EPS = 1e-6

#: 分片目录的粒度（Dynmap 源码里的 ``>> 5``）
SHARD_SHIFT = 5

DEFAULT_USER_AGENT = "dyn2xyz/1.0 (+https://github.com/; dynmap-to-xyz tile proxy)"


class SourceError(RuntimeError):
    """瓦片源不可用或配置不合法。"""


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DynmapGeometry:
    """从 ``worldtomap`` 矩阵推出的、可用于 XYZ 换算的地图几何。

    只支持**轴对齐的俯视图**（正北朝上）。等轴测图（world X/Z 同时影响屏幕两轴）
    无法映射到北朝上的 XYZ 网格，构造时会直接报错。
    """

    tile_px: int          # 瓦片边长（像素）= 128 << tilescale
    mapzoomout: int       # 最大 zoomout 层级，也是可用的最粗层级
    mapzoomin: int        # 客户端可超出原生层的放大档数（本程序用 overzoom 自行模拟）
    scale_x: float        # worldtomap[0]：地图经度 / 方块 X
    scale_z: float        # worldtomap[5]：地图纬度 / 方块 Z
    image_format: str = "png"

    def __post_init__(self) -> None:
        if self.tile_px <= 0 or (self.tile_px & (self.tile_px - 1)):
            raise SourceError(f"tile_px 必须是 2 的幂，得到 {self.tile_px}")
        if self.scale_x == 0 or self.scale_z == 0:
            raise SourceError("worldtomap 的缩放系数不能为 0")

    # -- 分辨率 -------------------------------------------------------------
    @property
    def blocks_per_pixel_native(self) -> float:
        """原生层（L=0）1 像素覆盖多少方块（X 方向）。"""
        return 1.0 / abs(self.scale_x)

    def blocks_per_tile(self, level: int) -> float:
        """层级 L 一个瓦片覆盖多少方块（边长）。"""
        return self.tile_px * (1 << level) / abs(self.scale_x)

    # -- 方块 -> 瓦片 -------------------------------------------------------
    def level0_tile_index(self, bx: float, bz: float) -> tuple[int, int]:
        """方块坐标 -> level-0 瓦片索引。"""
        nx = math.floor(self.scale_x * bx / self.tile_px)
        ny = math.floor((self.tile_px - self.scale_z * bz) / self.tile_px)
        return nx, ny

    def tile_index(self, bx: float, bz: float, level: int) -> tuple[int, int]:
        """方块坐标 -> 层级 L 的瓦片索引（已向下对齐到 2^L 的倍数）。"""
        nx, ny = self.level0_tile_index(bx, bz)
        m = 1 << level
        return m * (nx // m), m * (ny // m)

    # -- 层级像素空间 -------------------------------------------------------
    def level_pixels(self, bx: float, bz: float, level: int) -> tuple[float, float]:
        """方块坐标 -> 层级 L 的「全局像素坐标」（左上角为原点，y 向南增）。

        L 层的一个瓦片恰好占据该空间里 ``tile_px × tile_px`` 的方块。
        """
        m = 1 << level
        return (self.scale_x * bx / m, (self.tile_px - self.scale_z * bz) / m)

    def block_from_level_pixels(self, px: float, py: float, level: int) -> tuple[float, float]:
        """:meth:`level_pixels` 的逆变换。"""
        m = 1 << level
        return (px * m / self.scale_x, (self.tile_px - py * m) / self.scale_z)

    # -- 瓦片 -> 方块 -------------------------------------------------------
    def tile_block_bounds(self, nlx: int, nly: int, level: int) -> tuple[float, float, float, float]:
        """层级 L 瓦片覆盖的方块矩形 ``(bx_min, bz_min, bx_max, bz_max)``。"""
        m = 1 << level
        xs = sorted((nlx * self.tile_px / self.scale_x,
                     (nlx + m) * self.tile_px / self.scale_x))
        zs = sorted(((self.tile_px - nly * self.tile_px) / self.scale_z,
                     (self.tile_px - (nly + m) * self.tile_px) / self.scale_z))
        return xs[0], zs[0], xs[1], zs[1]

    # -- 文件名 -------------------------------------------------------------
    @staticmethod
    def zoom_prefix(level: int) -> str:
        """Dynmap 的缩放前缀：L 个 ``z``；L=0 时为空。"""
        return "z" * level

    def tile_stem(self, nlx: int, nly: int, level: int) -> str:
        """瓦片文件名（不含扩展名）。注意 y 被取反。"""
        fx, fy = nlx, -nly
        zp = self.zoom_prefix(level)
        return f"{zp}_{fx}_{fy}" if zp else f"{fx}_{fy}"

    def tile_shard(self, nlx: int, nly: int) -> tuple[int, int]:
        """分片目录编号。Python/Java 的 ``>>`` 对负数都是向下取整，语义一致。"""
        return nlx >> SHARD_SHIFT, (-nly) >> SHARD_SHIFT


def geometry_from_map_config(map_cfg: dict[str, Any], *, max_level_override: int | None = None) -> DynmapGeometry:
    """从 ``dynmap_config.json`` 里的一张地图条目构造几何，并校验可投影性。

    抛 :class:`SourceError` 说明这张图不能用于 XYZ 输出（例如等轴测图）。
    """
    wtp = map_cfg.get("worldtomap")
    if not isinstance(wtp, (list, tuple)) or len(wtp) != 9:
        raise SourceError("地图配置缺少合法的 worldtomap 矩阵（9 个数字）")

    a, b, c, d, e, f = (float(wtp[0]), float(wtp[1]), float(wtp[2]),
                        float(wtp[3]), float(wtp[4]), float(wtp[5]))
    # lng = a*X + b*Y + c*Z ; lat = d*X + e*Y + f*Z
    scale = max(abs(a), abs(f), 1.0)
    eps = AXIS_EPS * scale

    if abs(b) > eps or abs(c) > eps:
        raise SourceError(
            "这张地图不是北朝上的俯视图：worldtomap 显示地图经度还依赖于 "
            f"方块 Y/Z（交叉项 b={b:.6g}, c={c:.6g}）。等轴测（isometric）地图的瓦片是"
            "斜置菱形网格，无法映射到标准 XYZ 网格。请改用 prefix 为 flat 的地图。"
        )
    if abs(d) > eps or abs(e) > eps:
        raise SourceError(
            "这张地图不是北朝上的俯视图：worldtomap 显示地图纬度还依赖于 "
            f"方块 X/Y（交叉项 d={d:.6g}, e={e:.6g}）。请改用 flat 地图。"
        )
    if a <= 0 or f >= 0:
        raise SourceError(
            f"这张地图的轴向与标准 XYZ 相反（scale_x={a:.6g}, scale_z={f:.6g}），"
            "输出会是镜像的。本程序要求 scale_x > 0 且 scale_z < 0。"
        )

    tilescale = int(map_cfg.get("tilescale", 0) or 0)
    tile_px = 128 << max(0, min(tilescale, 4))

    mapzoomout = int(map_cfg.get("mapzoomout", 0) or 0)
    if max_level_override is not None:
        mapzoomout = int(max_level_override)
    if mapzoomout < 0:
        raise SourceError(f"mapzoomout 非法: {mapzoomout}")

    return DynmapGeometry(
        tile_px=tile_px,
        mapzoomout=mapzoomout,
        mapzoomin=int(map_cfg.get("mapzoomin", 0) or 0),
        scale_x=a,
        scale_z=f,
        image_format=str(map_cfg.get("image-format", "png") or "png"),
    )


# ---------------------------------------------------------------------------
# 样例 URL 解析
# ---------------------------------------------------------------------------


@dataclass
class ParsedSample:
    """从一条真实的瓦片 URL 里反推出的信息。"""

    webroot: str
    world: str
    prefix: str
    image_format: str
    level: int
    filename_x: int
    filename_y: int
    raw: str

    @property
    def nightday_suffix(self) -> str:
        for suffix in ("_day", "_night"):
            if self.prefix.endswith(suffix):
                return suffix
        return ""

    @property
    def base_prefix(self) -> str:
        """去掉 ``_day`` / ``_night`` 后的地图前缀。"""
        return self.prefix[: -len(self.nightday_suffix)] if self.nightday_suffix else self.prefix


class SampleParseError(ValueError):
    """样例 URL 不符合 Dynmap 的瓦片路径格式。"""


_SAMPLE_RE = re.compile(r"^(?P<stem>[^/]+?)(?:\.(?P<ext>[A-Za-z0-9]+))?$")


def parse_sample_url(url: str) -> ParsedSample:
    """解析一条真实的 Dynmap 瓦片 URL。

    接受的形状（``?query`` 可有可无）::

        https://host/ctx/tiles/<world>/<prefix>/<shardx>_<shardy>/<zoomprefix>_<x>_<y>.<ext>
        https://host/ctx/tiles/<world>/<prefix>/<shardx>_<shardy>/<x>_<y>.<ext>        # level 0
    """
    raw = url.strip()
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if "://" not in raw:
        raise SampleParseError("样例 URL 必须是完整的 http(s) URL")
    scheme, rest = raw.split("://", 1)
    if "/tiles/" not in rest:
        raise SampleParseError("样例 URL 里找不到 '/tiles/' 路径段")
    host_and_ctx, path = rest.split("/tiles/", 1)
    webroot = f"{scheme}://{host_and_ctx}"

    parts = [p for p in path.split("/") if p]
    if len(parts) < 4:
        raise SampleParseError(
            "路径段不足：期望 <world>/<prefix>/<shardx>_<shardy>/<文件名>，"
            f"实际只有 {parts!r}"
        )
    filename = parts[-1]
    shard = parts[-2]
    world = parts[0]
    prefix = "/".join(parts[1:-2])

    m = _SAMPLE_RE.match(filename)
    if not m:
        raise SampleParseError(f"无法解析文件名 {filename!r}")
    stem = m.group("stem")
    ext = (m.group("ext") or "png").lower()

    bits = stem.split("_")
    if len(bits) == 2:
        zoomprefix, fx, fy = "", bits[0], bits[1]
    elif len(bits) == 3:
        zoomprefix, fx, fy = bits[0], bits[1], bits[2]
    else:
        raise SampleParseError(f"文件名 {stem!r} 不是 <zoomprefix>_<x>_<y> 或 <x>_<y> 形状")

    if zoomprefix and set(zoomprefix) != {"z"}:
        raise SampleParseError(f"缩放前缀 {zoomprefix!r} 只能由字母 z 组成")
    try:
        fx_i, fy_i = int(fx), int(fy)
    except ValueError as exc:
        raise SampleParseError(f"瓦片坐标 {fx!r}/{fy!r} 不是整数") from exc

    if not shard.count("_"):
        raise SampleParseError(f"分片目录 {shard!r} 不是 <x>_<y> 形状")
    sx, sy = shard.split("_", 1)
    try:
        sx_i, sy_i = int(sx), int(sy)
    except ValueError as exc:
        raise SampleParseError(f"分片目录 {shard!r} 含非整数") from exc

    # 关键自检：分片目录必须等于坐标 >> 5。这条能同时验证「y 已取反」的约定。
    if (fx_i >> SHARD_SHIFT) != sx_i or (fy_i >> SHARD_SHIFT) != sy_i:
        raise SampleParseError(
            f"分片目录与瓦片坐标不自洽：得到 {sx_i}_{sy_i}，但 "
            f"{fx_i}>>5={fx_i >> SHARD_SHIFT}, {fy_i}>>{SHARD_SHIFT}={fy_i >> SHARD_SHIFT}。"
            "如果这条 URL 确实来自 Dynmap，说明该服务器的路径规则与预期不同，"
            "请在配置里改用显式 template。"
        )

    return ParsedSample(
        webroot=webroot,
        world=world,
        prefix=prefix,
        image_format=ext,
        level=len(zoomprefix),
        filename_x=fx_i,
        filename_y=fy_i,
        raw=url.strip(),
    )


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------


def http_get(url: str, *, timeout: float = 20.0, user_agent: str = DEFAULT_USER_AGENT,
             max_bytes: int = 8 << 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                               "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes)


def fetch_dynmap_config(webroot: str, *, timeout: float = 20.0,
                        config_path: str = "standalone/dynmap_config.json",
                        user_agent: str = DEFAULT_USER_AGENT) -> dict[str, Any]:
    """抓取并解析 ``dynmap_config.json``。"""
    url = f"{webroot.rstrip('/')}/{config_path.lstrip('/')}"
    try:
        payload = http_get(url, timeout=timeout, user_agent=user_agent)
    except urllib.error.HTTPError as exc:
        raise SourceError(f"抓取 Dynmap 配置失败：HTTP {exc.code} @ {url}") from exc
    except Exception as exc:  # noqa: BLE001 - 网络异常种类多，统一转成 SourceError
        raise SourceError(f"抓取 Dynmap 配置失败：{exc} @ {url}") from exc
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Dynmap 配置不是合法 JSON：{url}") from exc


def find_map(config: dict[str, Any], world: str, prefix: str) -> dict[str, Any]:
    """在配置里按世界名 + 地图前缀查找地图条目。"""
    base_prefix = prefix
    for suffix in ("_day", "_night"):
        if base_prefix.endswith(suffix):
            base_prefix = base_prefix[: -len(suffix)]

    worlds = config.get("worlds")
    if not isinstance(worlds, list):
        raise SourceError("Dynmap 配置里没有 worlds 数组")

    available: list[str] = []
    for w in worlds:
        wname = str(w.get("name", ""))
        for m in w.get("maps", []) or []:
            mname = str(m.get("name", ""))
            mprefix = str(m.get("prefix", mname))
            available.append(f"{wname}/{mprefix}")
            if wname != world:
                continue
            if base_prefix in (mprefix, mname):
                return m
    raise SourceError(
        f"在 Dynmap 配置里找不到世界 {world!r} 的前缀 {base_prefix!r}。"
        f"可用组合：{', '.join(sorted(set(available))) or '(空)'}"
    )


# ---------------------------------------------------------------------------
# 瓦片源
# ---------------------------------------------------------------------------

#: 默认 URL 模板。可用占位符见 README / 配置文件注释。
DEFAULT_TEMPLATE = "{webroot}/tiles/{world}/{prefix}/{shardx}_{shardy}/{stem}.{ext}"


@dataclass
class DynmapSource:
    """一个可直接生成瓦片 URL 的 Dynmap 源。"""

    webroot: str
    world: str
    prefix: str
    geometry: DynmapGeometry
    template: str = DEFAULT_TEMPLATE
    extra_query: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 20.0
    _url_cache: dict = field(default_factory=dict, repr=False)

    def tile_url(self, nlx: int, nly: int, level: int) -> str:
        """层级 L 的瓦片索引 -> 完整 URL。"""
        g = self.geometry
        shardx, shardy = g.tile_shard(nlx, nly)
        url = self.template.format(
            webroot=self.webroot.rstrip("/"),
            world=self.world,
            prefix=self.prefix,
            level=level,
            zoomprefix=g.zoom_prefix(level),
            x=nlx,
            y=-nly,
            shardx=shardx,
            shardy=shardy,
            stem=g.tile_stem(nlx, nly, level),
            ext=g.image_format,
        )
        if self.extra_query:
            url = f"{url}{'&' if '?' in url else '?'}{self.extra_query.lstrip('?&')}"
        return url

    def tile_url_for_block(self, bx: float, bz: float, level: int) -> str:
        """方块坐标 -> 覆盖它的层级 L 瓦片 URL（便于人工核对）。"""
        nlx, nly = self.geometry.tile_index(bx, bz, level)
        return self.tile_url(nlx, nly, level)

    def describe(self) -> dict[str, Any]:
        g = self.geometry
        return {
            "webroot": self.webroot,
            "world": self.world,
            "prefix": self.prefix,
            "tile_px": g.tile_px,
            "mapzoomout": g.mapzoomout,
            "mapzoomin": g.mapzoomin,
            "scale_x": g.scale_x,
            "scale_z": g.scale_z,
            "blocks_per_pixel_native": g.blocks_per_pixel_native,
            "blocks_per_tile_at_max_level": g.blocks_per_tile(g.mapzoomout),
            "image_format": g.image_format,
            "template": self.template,
        }
