"""Minecraft 方块坐标 <-> WGS84 / XYZ 瓦片 的换算（与 dyn2xyz 的 Placement 保持一致）。

放置参数来自 dyn2xyz/config/dyn2xyz.toml：
    origin_block = (11168, -5568)  ->  (lon 0, lat 0)
    meters_per_block = 1.0
"""
from __future__ import annotations

import math

R = 6378137.0
ORIGIN_SHIFT = math.pi * R
WORLD_WIDTH = 2 * ORIGIN_SHIFT

ORIGIN_BLOCK_X = 11168.0
ORIGIN_BLOCK_Z = -5568.0
METERS_PER_BLOCK = 1.0


def block_to_lonlat(bx: float, bz: float) -> tuple[float, float]:
    mx = (bx - ORIGIN_BLOCK_X) * METERS_PER_BLOCK
    my = -(bz - ORIGIN_BLOCK_Z) * METERS_PER_BLOCK
    lon = math.degrees(mx / R)
    lat = math.degrees(2 * math.atan(math.exp(my / R)) - math.pi / 2)
    return lon, lat


def lonlat_to_block(lon: float, lat: float) -> tuple[float, float]:
    mx = math.radians(lon) * R
    my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    bx = mx / METERS_PER_BLOCK + ORIGIN_BLOCK_X
    bz = -my / METERS_PER_BLOCK + ORIGIN_BLOCK_Z
    return bx, bz


def block_to_xyz(bx: float, bz: float, z: int) -> tuple[float, float]:
    """方块坐标 -> 实数 XYZ 瓦片坐标（与 MapLibre/Leaflet 的 slippy 约定一致）。"""
    mx = (bx - ORIGIN_BLOCK_X) * METERS_PER_BLOCK
    my = -(bz - ORIGIN_BLOCK_Z) * METERS_PER_BLOCK
    span = WORLD_WIDTH / 2**z
    return (mx + ORIGIN_SHIFT) / span, (ORIGIN_SHIFT - my) / span


def xyz_to_block(z: int, x: float, y: float) -> tuple[float, float]:
    span = WORLD_WIDTH / 2**z
    mx = x * span - ORIGIN_SHIFT
    my = ORIGIN_SHIFT - y * span
    return mx / METERS_PER_BLOCK + ORIGIN_BLOCK_X, -my / METERS_PER_BLOCK + ORIGIN_BLOCK_Z


def lon2tile(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * 2**z


def lat2tile(lat: float, z: int) -> float:
    rad = math.radians(lat)
    return (1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * 2**z


def tile2lon(x: float, z: int) -> float:
    return x / 2**z * 360.0 - 180.0


def tile2lat(y: float, z: int) -> float:
    n = math.pi - 2.0 * math.pi * y / 2**z
    return math.degrees(math.atan(math.sinh(n)))
