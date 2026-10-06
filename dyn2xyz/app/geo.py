"""geo.py —— 墨卡托投影 / XYZ 瓦片 / Minecraft 方块坐标之间的纯数学换算。

本模块不依赖任何第三方库，也没有状态，便于单独测试。

坐标约定
--------
* **方块坐标 (bx, bz)**：Minecraft 世界坐标。+bx 向东，+bz 向南（这是 Minecraft 的约定）。
* **墨卡托米 (mx, my)**：EPSG:3857。+mx 向东，+my 向北。
* **经纬度 (lon, lat)**：WGS84 十进制度。
* **XYZ 瓦片 (z, x, y)**：slippy-map 约定。x 向东自 -180°，y 向南自北界，原点在西北角。

"1 方块 = 1 米"这类比例只出现在 :class:`Placement` 里；本模块其余部分只做纯几何。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量（全部由 WGS84 长半轴导出，数值与 GIS 生态一致）
# ---------------------------------------------------------------------------

#: Web Mercator 使用的球体半径（WGS84 长半轴）
R = 6378137.0

#: 投影平面半宽，即 πR = 20037508.342789244 米
ORIGIN_SHIFT = math.pi * R

#: 投影平面全宽，即 2πR = 40075016.68557849 米
WORLD_WIDTH = 2 * ORIGIN_SHIFT

#: 墨卡托可表示的纬度上限 ±85.0511287798066°
LAT_LIMIT = math.degrees(math.atan(math.sinh(math.pi)))

#: XYZ 规范固定的瓦片像素数；缩放层级 z 的分辨率定义在这个基准上。
XYZ_TILE_PX = 256

#: z=0 时赤道处的分辨率（米/像素）= 156543.03392804097
EQ_M_PER_PX_Z0 = WORLD_WIDTH / XYZ_TILE_PX


def lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    """经纬度 -> 墨卡托米。纬度会被截断到 ±85.0511287798066°。"""
    lat = max(min(lat, LAT_LIMIT), -LAT_LIMIT)
    mx = math.radians(lon) * R
    my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return mx, my


def mercator_to_lonlat(mx: float, my: float) -> tuple[float, float]:
    """墨卡托米 -> 经纬度。"""
    lon = math.degrees(mx / R)
    lat = math.degrees(2 * math.atan(math.exp(my / R)) - math.pi / 2)
    return lon, lat


def meters_per_pixel(z: float, tile_px: int = XYZ_TILE_PX) -> float:
    """XYZ 层级 z 在赤道处的分辨率（米/像素）。

    注意：这是**赤道处**的值；在纬度 φ 处实际分辨率为该值乘以 cos(φ)。
    因为 Minecraft 世界被当作平面处理，我们只按赤道尺度对齐（见 README 的误差一节）。
    """
    return WORLD_WIDTH / (tile_px * 2.0**z)


def zoom_for_meters_per_pixel(mpp: float, tile_px: int = XYZ_TILE_PX) -> float:
    """:func:`meters_per_pixel` 的逆函数，返回**实数**缩放层级。"""
    return math.log2(WORLD_WIDTH / (tile_px * mpp))


def xyz_tile_meters(z: int, x: int, y: int, tile_px: int = XYZ_TILE_PX) -> tuple[float, float, float, float]:
    """XYZ 瓦片覆盖的墨卡托米矩形，返回 ``(mx_min, my_min, mx_max, my_max)``。

    网格定义在 256 像素基准上，与 ``tile_px`` 无关；``tile_px`` 只用于计算分辨率。
    """
    span = WORLD_WIDTH / 2**z
    mx_min = -ORIGIN_SHIFT + x * span
    my_max = ORIGIN_SHIFT - y * span
    return mx_min, my_max - span, mx_min + span, my_max


def meters_to_xyz(mx: float, my: float, z: int) -> tuple[float, float]:
    """墨卡托米 -> 实数 XYZ 瓦片坐标（结果含小数，便于算瓦片内的像素偏移）。"""
    span = WORLD_WIDTH / 2**z
    return (mx + ORIGIN_SHIFT) / span, (ORIGIN_SHIFT - my) / span


def xyz_tile_lonlat_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """XYZ 瓦片覆盖的经纬度矩形，返回 ``(lon_min, lat_min, lon_max, lat_max)``。"""
    mx0, my0, mx1, my1 = xyz_tile_meters(z, x, y)
    lon0, lat0 = mercator_to_lonlat(mx0, my0)
    lon1, lat1 = mercator_to_lonlat(mx1, my1)
    return lon0, lat0, lon1, lat1


# ---------------------------------------------------------------------------
# 世界摆放：Minecraft 方块坐标 <-> 墨卡托米
# ---------------------------------------------------------------------------


@dataclass
class Placement:
    """把 Minecraft 的平面方块世界摆到地球上的方式。

    默认值对应需求里的约定：**方块 (0, 0) 落在赤道与本初子午线的交点**，
    **1 方块 = 1 米**。

    :param origin_lon: 方块 (origin_block_x, origin_block_z) 对应的经度（度）
    :param origin_lat: 同上，纬度（度）
    :param meters_per_block: 渲染比例。1.0 表示 1 方块 = 1 米
    :param origin_block_x: 被钉到 (origin_lon, origin_lat) 的方块 X（默认 0）
    :param origin_block_z: 被钉到 (origin_lon, origin_lat) 的方块 Z（默认 0）
    """

    origin_lon: float = 0.0
    origin_lat: float = 0.0
    meters_per_block: float = 1.0
    origin_block_x: float = 0.0
    origin_block_z: float = 0.0

    def __post_init__(self) -> None:
        if self.meters_per_block <= 0:
            raise ValueError("meters_per_block 必须为正数")
        self._mx0, self._my0 = lonlat_to_mercator(self.origin_lon, self.origin_lat)

    # -- 正向：方块 -> 米 ---------------------------------------------------
    def block_to_meters(self, bx: float, bz: float) -> tuple[float, float]:
        """方块坐标 -> 墨卡托米。

        Minecraft 的 +bz 向南，而墨卡托的 +my 向北，所以 z 方向取负。
        """
        mx = self._mx0 + (bx - self.origin_block_x) * self.meters_per_block
        my = self._my0 - (bz - self.origin_block_z) * self.meters_per_block
        return mx, my

    # -- 逆向：米 -> 方块 ---------------------------------------------------
    def meters_to_block(self, mx: float, my: float) -> tuple[float, float]:
        """墨卡托米 -> 方块坐标（含小数）。"""
        bx = (mx - self._mx0) / self.meters_per_block + self.origin_block_x
        bz = (self._my0 - my) / self.meters_per_block + self.origin_block_z
        return bx, bz

    def block_to_lonlat(self, bx: float, bz: float) -> tuple[float, float]:
        return mercator_to_lonlat(*self.block_to_meters(bx, bz))

    def lonlat_to_block(self, lon: float, lat: float) -> tuple[float, float]:
        return self.meters_to_block(*lonlat_to_mercator(lon, lat))

    # -- 便捷：直接给出 XYZ 瓦片覆盖的方块范围 -------------------------------
    def xyz_tile_block_bounds(self, z: int, x: int, y: int) -> tuple[float, float, float, float]:
        """XYZ 瓦片覆盖的方块范围，返回 ``(bx_min, bz_min, bx_max, bz_max)``。

        注意返回的是**浮点**边界：瓦片边界一般不会刚好落在整数方块上。
        """
        mx0, my0, mx1, my1 = xyz_tile_meters(z, x, y)
        bx_a, bz_a = self.meters_to_block(mx0, my0)  # 西北角
        bx_b, bz_b = self.meters_to_block(mx1, my1)  # 东南角
        return min(bx_a, bx_b), min(bz_a, bz_b), max(bx_a, bx_b), max(bz_a, bz_b)


def lat_correction_note(z: int, lat: float) -> float:
    """在纬度 lat 处，平面近似相对真实墨卡托的纵向拉伸倍数（= 1/cos(lat)）。

    只用于文档/诊断：当这个值明显偏离 1 时，说明世界已经离赤道太远，
    "方块当平面"的假设开始产生可见误差。
    """
    return 1.0 / math.cos(math.radians(max(min(lat, LAT_LIMIT), -LAT_LIMIT)))
