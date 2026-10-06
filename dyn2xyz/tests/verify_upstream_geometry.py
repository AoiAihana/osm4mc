#!/usr/bin/env python3
"""对上游 Dynmap 服务器做**像素级**几何验证。

原理
----
Dynmap 的瓦片金字塔是严格 2 倍的：一个 level-L 瓦片恰好覆盖 2×2 个 level-(L-1) 瓦片。
所以只要我的「方块坐标 <-> 瓦片索引」推导正确，那么：

    level-L 瓦片  ≈  downsample(由 4 个 level-(L-1) 瓦片拼成的 2×2 拼图)

反过来，如果我算错了原点、y 方向或分片规则，两张图就会对不上。
脚本同时跑几个**故意算错**的对照组（上下翻转、左右错一格），用来证明这个检验确实有分辨力。

用法::

    python3 tests/verify_upstream_geometry.py

需要 Pillow 与网络。默认验证 Paralon/flat 的 level 5 对 level 4。
"""

from __future__ import annotations

import io
import math
import sys
import urllib.error
import urllib.request

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # pragma: no cover
    print("需要 Pillow: python3 -m pip install Pillow")
    raise SystemExit(2)

BASE = "https://map.bilicraft.com/s2/hyperion"
WORLD = "Paralon"
PREFIX = "flat"
EXT = "jpg"

# 该地图的几何参数，取自 <BASE>/standalone/dynmap_config.json
TILE_PX = 128      # 128 << tilescale, tilescale=0
SCALE_X = 4.0      # worldtomap[0]
SCALE_Z = -4.0     # worldtomap[5]
MAX_LEVEL = 5      # mapzoomout

UA = {"User-Agent": "dyn2xyz-geometry-check/1.0"}


def level0_tile(bx: float, bz: float) -> tuple[int, int]:
    """方块 -> level-0 瓦片索引（未做「取 2 的幂倍数」对齐）。"""
    nx = math.floor(SCALE_X * bx / TILE_PX)
    ny = math.floor((TILE_PX - SCALE_Z * bz) / TILE_PX)
    return nx, ny


def align_down(v: int, level: int) -> int:
    """把 level-0 索引向下对齐到 2^level 的倍数。"""
    m = 1 << level
    return m * (v // m)


def tile_url(nx0: int, ny0: int, level: int) -> str:
    """level-0 索引 + 层级 -> 上游瓦片 URL。"""
    nlx = align_down(nx0, level)
    nly = align_down(ny0, level)
    fx, fy = nlx, -nly  # HD 地图在命名层对 y 取反
    stem = (("z" * level) + "_" if level > 0 else "") + f"{fx}_{fy}"
    return f"{BASE}/tiles/{WORLD}/{PREFIX}/{fx >> 5}_{fy >> 5}/{stem}.{EXT}"


def fetch_image(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def mosaic(urls: list[list[str]]) -> Image.Image:
    """按行优先把瓦片拼成一张大图。"""
    rows, cols = len(urls), len(urls[0])
    out = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX))
    for j, row in enumerate(urls):
        for i, u in enumerate(row):
            out.paste(fetch_image(u), (i * TILE_PX, j * TILE_PX))
    return out


def mad(a: Image.Image, b: Image.Image, size: int = 96) -> float:
    """两张图缩放到同一尺寸后的平均绝对色差（0=完全一致，255=完全不同）。"""
    ra = a.resize((size, size), Image.LANCZOS)
    rb = b.resize((size, size), Image.LANCZOS)
    return ImageStat.Stat(ImageChops.difference(ra, rb)).mean[0]


def main() -> int:
    level = MAX_LEVEL
    # 取样例 URL 覆盖区域内的一点，反推它所属的各层级瓦片
    bx, bz = 3500.0, -500.0
    nx0, ny0 = level0_tile(bx, bz)

    print(f"探针方块坐标: ({bx}, {bz})  ->  level-0 瓦片索引 {nx0}, {ny0}")
    print(f"验证层级: level {level} vs level {level - 1} 的 2×2 拼图\n")

    big_url = tile_url(nx0, ny0, level)
    print(f"  level {level}   : {big_url.split('/tiles/')[1]}")
    big = fetch_image(big_url)

    # 该 level 瓦片覆盖 level-0 索引 [nlx, nlx+2^level)，其 4 个 level-1 子瓦片
    nlx, nly = align_down(nx0, level), align_down(ny0, level)
    half = 1 << (level - 1)
    subs = [
        [tile_url(nlx, nly, level - 1), tile_url(nlx + half, nly, level - 1)],
        [tile_url(nlx, nly + half, level - 1), tile_url(nlx + half, nly + half, level - 1)],
    ]
    for row in subs:
        for u in row:
            print(f"  level {level - 1} 子瓦片: {u.split('/tiles/')[1]}")
    small = mosaic(subs)
    print()

    def show(label: str, img: Image.Image) -> float:
        v = mad(big, img)
        print(f"  {label:<34} MAD = {v:7.3f}")
        return v

    # ---- 正确对齐 ---------------------------------------------------------
    good = show("正确对齐（本程序推导）", small)

    # ---- 对照组：故意算错，检验这个指标有分辨力 ---------------------------
    ctrl = {}
    ctrl["左右翻转"] = small.transpose(Image.FLIP_LEFT_RIGHT)
    ctrl["上下翻转（y 方向搞反）"] = small.transpose(Image.FLIP_TOP_BOTTOM)
    ctrl["旋转 180°"] = small.transpose(Image.ROTATE_180)
    # 整体错开一个子瓦片（模拟分片/取整错误）
    shifted = Image.new("RGB", small.size)
    shifted.paste(small.crop((TILE_PX, 0, small.width, small.height)), (0, 0))
    shifted.paste(small.crop((0, 0, TILE_PX, small.height)), (small.width - TILE_PX, 0))
    ctrl["横向错开一个子瓦片"] = shifted

    print()
    worst_ctrl = 0.0
    for label, img in ctrl.items():
        v = show(f"对照组：{label}", img)
        worst_ctrl = max(worst_ctrl, v) if False else worst_ctrl
    ctrl_vals = [mad(big, img) for img in ctrl.values()]
    best_ctrl = min(ctrl_vals)

    print()
    print(f"正确对齐 MAD      = {good:.3f}")
    print(f"最好的错误对照组 MAD = {best_ctrl:.3f}")
    if good < best_ctrl / 2 and good < 20:
        print("\n✅ 通过：正确对齐明显优于任何错误对齐，几何推导得到像素级确认。")
        return 0
    print("\n❌ 失败：正确对齐没有明显优于对照组，几何推导可能有误。")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:  # pragma: no cover
        print(f"上游返回 HTTP {e.code}: {e.url}")
        raise SystemExit(3)
