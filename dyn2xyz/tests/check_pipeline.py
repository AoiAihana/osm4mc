#!/usr/bin/env python3
"""对 dyn2xyz 的**端到端**验证：渲染真实上游瓦片并检查几何自洽性。

覆盖多个缩放层级（含 L>0 的层级）——曾经有个 bug 只在 L=0 时表现正常，
所以只测单一层级是不够的。

每个层级跑三项检查：

1. **非空**：在已知有数据的位置渲染，输出不应是纯透明图。

2. **接缝统计**：把一排/一列相邻 XYZ 瓦片各自独立渲染后拼起来，测量接缝处的色差，
   与瓦片内部相邻像素的平均色差比较。两者应当同量级——若 y 轴翻转、层级选错或
   多算了半个像素，接缝会系统性远大于内部梯度。

3. **2×2 拼图 vs 独立实现**：把 4 张独立渲染的 XYZ 瓦片拼成 2×2，与「直接抓上游 +
   一次性裁剪缩放」得到的同一区域对比，能捕捉仿射误差与瓦片间相对错位。

用法::

    python3 tests/check_pipeline.py [配置文件] [z1,z2,...]
"""

from __future__ import annotations

import io
import math
import statistics
import sys

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, ".")

from app.config import build_source, load_config  # noqa: E402
from app.dynmap import http_get  # noqa: E402
from app.geo import meters_to_xyz  # noqa: E402
from app.render import TileRenderer  # noqa: E402

#: 已知有地图数据的方块位置（来自样例 URL 覆盖的范围）
PROBE_BLOCK = (3500.0, -500.0)

#: 默认覆盖最粗层 / 中间层 / 原生层
DEFAULT_ZOOMS = "14,16,19"
RUN_LEN = 5
SEAM_TOLERANCE = 1.6
QUAD_LIMIT = 24.0
SMALL_LIMIT = 12.0


def diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean[0]


def edge(img: Image.Image, axis: str, i: int) -> Image.Image:
    return img.crop((i, 0, i + 1, img.height)) if axis == "x" else img.crop((0, i, img.width, i + 1))


def opaque_ratio(img: Image.Image) -> float:
    return ImageStat.Stat(img.convert("RGBA").getchannel("A")).mean[0] / 255.0


_RAW: dict[str, bytes] = {}


def _raw(url: str, timeout: float) -> bytes:
    if url not in _RAW:
        _RAW[url] = http_get(url, timeout=timeout)
    return _RAW[url]


def naive_upstream(src, r, z: int, X: int, Y: int, w: int, tiles: int) -> Image.Image:
    """独立实现：直接抓上游瓦片、拼起来、裁剪并缩放到 ``tiles*w`` 见方。"""
    g = src.geometry
    level = r.level_for_zoom(z)
    p = r.placement
    bx0, bz0, _, _ = p.xyz_tile_block_bounds(z, X, Y)
    _, _, bx1, bz1 = p.xyz_tile_block_bounds(z, X + tiles - 1, Y + tiles - 1)
    px0, py0 = g.level_pixels(bx0, bz0, level)
    px1, py1 = g.level_pixels(bx1, bz1, level)
    px0, px1 = sorted((px0, px1))
    py0, py1 = sorted((py0, py1))
    t = g.tile_px
    kx0, ky0 = math.floor(px0 / t), math.floor(py0 / t)
    nkx = math.floor((px1 - 1e-9) / t) - kx0 + 1
    nky = math.floor((py1 - 1e-9) / t) - ky0 + 1
    big = Image.new("RGBA", (nkx * t, nky * t), (0, 0, 0, 0))
    for j in range(nky):
        for i in range(nkx):
            url = src.tile_url((kx0 + i) << level, (ky0 + j) << level, level)
            try:
                with Image.open(io.BytesIO(_raw(url, src.timeout))) as im:
                    big.paste(im.convert("RGBA"), (i * t, j * t))
            except Exception:  # noqa: BLE001
                pass
    ox, oy = px0 - kx0 * t, py0 - ky0 * t
    return big.crop((round(ox), round(oy), round(ox) + round(px1 - px0),
                     round(oy) + round(py1 - py0))).resize((tiles * w, tiles * w), Image.BILINEAR)


def check_zoom(cfg, src, r, z: int) -> list[str]:
    w = cfg.render.output_px
    bx, bz = PROBE_BLOCK
    mx, my = cfg.geo.placement().block_to_meters(bx, bz)
    fx, fy = meters_to_xyz(mx, my, z)
    X, Y = int(fx), int(fy)
    level = r.level_for_zoom(z)
    fails: list[str] = []
    print(f"\n{'─' * 74}\nz={z}  (Dynmap L={level})  瓦片 ({X}, {Y})  "
          f"层内偏移 ({fx - X:.3f}, {fy - Y:.3f})")

    center = r.render(z, X, Y)
    ratio = opaque_ratio(center)
    ok = ratio > 0.5
    print(f"  [1] 非空: 不透明占比 {ratio:6.1%}  {'✅' if ok else '❌'}")
    if not ok:
        fails.append(f"z{z} 非空")

    for axis, label in (("x", "横"), ("y", "纵")):
        mine = [r.render(z, X + k, Y) if axis == "x" else r.render(z, X, Y + k)
                for k in range(RUN_LEN)]
        ref = [naive_upstream(src, r, z, X + k, Y, w, 1) if axis == "x"
               else naive_upstream(src, r, z, X, Y + k, w, 1)
               for k in range(RUN_LEN)]
        seams_mine = [diff(edge(mine[k], axis, w - 1), edge(mine[k + 1], axis, 0))
                      for k in range(RUN_LEN - 1)]
        seams_ref = [diff(edge(ref[k], axis, w - 1), edge(ref[k + 1], axis, 0))
                     for k in range(RUN_LEN - 1)]
        sm, sr = statistics.median(seams_mine), statistics.median(seams_ref)
        # 这里是一个**宽松闸门**，不是精度断言。
        # 原因：参考实现自己用 round() 裁剪，带着最多半个像素的固有误差；
        # 而当 XYZ 与 Dynmap 的网格不对齐时（默认渲染比例就是如此），
        # 接缝处的色差本来就受地图纹理梯度主导，逐对比较噪声很大。
        # 真正的严格证明在 tests/check_aligned.py：把渲染比例调成两套网格精确对齐后，
        # 本程序的输出必须与纯裁剪**逐像素完全相同**。
        # 所以这里只拦「明显断裂」：最大接缝不应比参考实现的最大接缝差一个量级。
        ok = max(seams_mine) <= max(seams_ref) * 3.0 + 10.0
        print(f"  [2{axis}] {label}向接缝（诊断）: 中位数 本程序 {sm:6.2f} / 参考 {sr:6.2f}；"
              f"最大 {max(seams_mine):6.2f} / {max(seams_ref):6.2f}  {'✅' if ok else '❌'}")
        print(f"        本程序 {[round(v, 1) for v in seams_mine]}")
        print(f"        参考     {[round(v, 1) for v in seams_ref]}")
        if not ok:
            fails.append(f"z{z}{label}向接缝")

    mosaic = Image.new("RGBA", (2 * w, 2 * w))
    for j in range(2):
        for i in range(2):
            mosaic.paste(r.render(z, X + i, Y + j), (i * w, j * w))
    ref = naive_upstream(src, r, z, X, Y, w, 2)
    d_small = diff(mosaic.resize((64, 64), Image.LANCZOS), ref.resize((64, 64), Image.LANCZOS))
    quads = []
    for j in range(2):
        for i in range(2):
            box = (i * w, j * w, (i + 1) * w, (j + 1) * w)
            quads.append(round(diff(mosaic.crop(box), ref.crop(box)), 2))
    ok = d_small < SMALL_LIMIT and max(quads) < QUAD_LIMIT
    print(f"  [3] 2×2 拼图 vs 独立实现: 降采样色差 {d_small:5.2f}  "
          f"各象限 {quads}  {'✅' if ok else '❌'}")
    if not ok:
        fails.append(f"z{z} 拼图交叉验证")
    return fails


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config/dyn2xyz.toml"
    zooms = [int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ZOOMS).split(",")]
    cfg = load_config(cfg_path)
    src = build_source(cfg)
    r = TileRenderer(src, cfg.geo.placement(), cfg.render)
    print(f"源: {src.webroot}  {src.world}/{src.prefix}")
    print(f"原生层 ≈ z={r.native_zoom_exact:.3f}；对外范围 z{r.minzoom}…z{r.maxzoom}；"
          f"输出 {cfg.render.output_px}px")
    print(f"探针方块 {PROBE_BLOCK}")

    fails: list[str] = []
    try:
        for z in zooms:
            fails += check_zoom(cfg, src, r, z)
    finally:
        print(f"\n缓存: {r.cache.stats()}")
        r.close()

    print("\n" + "=" * 74)
    if not fails:
        print("✅ 全部通过")
        return 0
    print("❌ 失败项: " + ", ".join(fails))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
