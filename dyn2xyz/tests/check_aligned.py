#!/usr/bin/env python3
"""零容差的决定性验证：把渲染比例调成「两套网格精确对齐」，此时输出必须是纯裁剪。

原理
----
Dynmap 层级 L 的瓦片边长是 ``tile_px · 2^L / scale_x`` 个方块；
XYZ 层级 z 的瓦片边长是 ``WORLD_WIDTH / 2^z`` 米。
只要挑选渲染比例

    meters_per_block = WORLD_WIDTH · scale_x / (tile_px · 2^k)

就能让 ``z = k + log2(tile_px / 256)`` 这一层**精确对齐**：一张 XYZ 瓦片恰好等于整数张
Dynmap 瓦片，既不需要缩放也不需要亚像素偏移。

对默认源（scale_x=4, tile_px=128）取 k=20 得 meters_per_block ≈ 1.194332，
对齐层级 z=19，且**一张 XYZ 瓦片恰好等于 2×2 张 L=0 瓦片、共 256×256 源像素**
——输出尺寸也是 256，所以仿射缩放因子恰为 1.0、偏移恰为 0。

于是本程序的输出必须与「把 4 张上游瓦片直接拼起来、原样裁一块」**逐像素相同**。
这条断言对半像素修正是零容差的：若仿射系数里的 ``-0.5`` 写错，输出会整体偏一个像素，
本测试会立刻失败。

用法::

    python3 tests/check_aligned.py [配置文件]
"""

from __future__ import annotations

import io
import math
import sys

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, ".")

from app.config import build_source, load_config  # noqa: E402
from app.dynmap import http_get  # noqa: E402
from app.geo import WORLD_WIDTH  # noqa: E402
from app.render import TileRenderer  # noqa: E402

PROBE_BLOCK = (3500.0, -500.0)


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config/dyn2xyz.toml"
    cfg = load_config(cfg_path)
    base_src = build_source(cfg)
    g = base_src.geometry

    # 解出让网格精确对齐的渲染比例
    k = round(math.log2(WORLD_WIDTH * abs(g.scale_x) / g.tile_px))
    mpb = WORLD_WIDTH * abs(g.scale_x) / (g.tile_px * 2**k)
    print(f"源: {base_src.webroot}  {base_src.world}/{base_src.prefix}")
    print(f"几何: tile_px={g.tile_px}  scale_x={g.scale_x:g}  mapzoomout={g.mapzoomout}")
    print(f"对齐参数: k={k}  ->  meters_per_block = {mpb!r}")
    print(f"（对比：默认 1.0；这里只是为了让网格对齐，不是推荐的生产取值）\n")

    cfg.geo.meters_per_block = mpb
    src = build_source(cfg)
    r = TileRenderer(src, cfg.geo.placement(), cfg.render)
    w = cfg.render.output_px

    zn = r.native_zoom_exact
    print(f"native_zoom_exact = {zn!r}")
    if abs(zn - round(zn)) > 1e-9:
        print("❌ 对齐失败：native_zoom_exact 不是整数，推导有误")
        r.close()
        return 1
    z = int(round(zn))
    level = r.level_for_zoom(z)
    tiles_per_side = (g.tile_px * (1 << level)) and None  # 仅占位，下面按像素窗口算
    print(f"对齐层级 z={z} -> Dynmap L={level}\n")

    fails: list[str] = []
    bx, bz = PROBE_BLOCK
    mx, my = cfg.geo.placement().block_to_meters(bx, bz)
    from app.geo import meters_to_xyz

    fx, fy = meters_to_xyz(mx, my, z)
    X, Y = int(fx), int(fy)

    bx0, bz0, bx1, bz1 = cfg.geo.placement().xyz_tile_block_bounds(z, X, Y)
    px0, py0 = g.level_pixels(bx0, bz0, level)
    px1, py1 = g.level_pixels(bx1, bz1, level)
    px0, px1 = sorted((px0, px1))
    py0, py1 = sorted((py0, py1))
    t = g.tile_px
    kx0, ky0 = math.floor(px0 / t), math.floor(py0 / t)
    nkx = math.floor((px1 - 1e-9) / t) - kx0 + 1
    nky = math.floor((py1 - 1e-9) / t) - ky0 + 1

    print(f"该 XYZ 瓦片覆盖 {nkx}×{nky} 张 L={level} 源瓦片，"
          f"源像素窗口 {px1 - px0:.6f}×{py1 - py0:.6f}")
    print(f"输出 {w}×{w}，故缩放因子 = {(px1 - px0) / w:.6f}（期望恰好 2 的幂或 1.0）")
    print(f"窗口在拼图中的偏移 = ({px0 - kx0 * t:.6f}, {py0 - ky0 * t:.6f})（期望恰好整数）\n")

    # 容差取 1e-6：墨卡托三角函数会留下 ~1e-9 量级的浮点残差，那不是逻辑误差
    ox_raw, oy_raw = px0 - kx0 * t, py0 - ky0 * t
    off_ok = (abs(ox_raw - round(ox_raw)) < 1e-6 and abs(oy_raw - round(oy_raw)) < 1e-6)
    print(f"[A] 窗口偏移为整数（残差 {abs(ox_raw - round(ox_raw)):.2e} / "
          f"{abs(oy_raw - round(oy_raw)):.2e}）: {'✅' if off_ok else '❌'}")
    if not off_ok:
        fails.append("窗口偏移非整数")

    # 直接拼上游、原样裁剪——完全不走仿射变换
    ref = Image.new("RGBA", (nkx * t, nky * t), (0, 0, 0, 0))
    missing = 0
    for j in range(nky):
        for i in range(nkx):
            url = src.tile_url((kx0 + i) << level, (ky0 + j) << level, level)
            try:
                with Image.open(io.BytesIO(http_get(url, timeout=cfg.source.timeout))) as im:
                    ref.paste(im.convert("RGBA"), (i * t, j * t))
            except Exception:  # noqa: BLE001
                missing += 1
    ox, oy = round(px0 - kx0 * t), round(py0 - ky0 * t)
    ref_crop = ref.crop((ox, oy, ox + round(px1 - px0), oy + round(py1 - py0)))

    mine = r.render(z, X, Y)
    print(f"[B] 源瓦片缺失数 = {missing}（期望 0）: {'✅' if missing == 0 else '❌'}")
    if missing:
        fails.append("源瓦片缺失")

    print(f"[C] 本程序输出 {mine.size} vs 参考裁剪 {ref_crop.size}: "
          f"{'✅' if mine.size == ref_crop.size else '❌'}")
    if mine.size != ref_crop.size:
        fails.append("尺寸不符")
        r.close()
        return 1

    if ref_crop.size == mine.size:
        diff = ImageChops.difference(mine.convert("RGB"), ref_crop.convert("RGB"))
        st = ImageStat.Stat(diff)
        maxdiff = max(st.extrema[i][1] for i in range(3))
        mean = sum(st.mean) / 3
        identical = maxdiff == 0
        print(f"[D] 与参考裁剪的最大通道差 = {maxdiff}，平均差 = {mean:.6f}  "
              f"{'✅ 逐像素完全相同' if identical else '⚠️ 存在差异'}")
        # 允许 JPEG 解码/编码带来的极微小差异，但必须远小于「偏一个像素」的量级
        if maxdiff > 0:
            fails.append(f"与参考裁剪不一致 (mean={mean:.3f})")
        # 作为对照，偏一个像素会差多少
        shifted = Image.new("RGBA", ref_crop.size)
        shifted.paste(ref_crop.crop((1, 0, ref_crop.width, ref_crop.height)), (0, 0))
        smean = sum(ImageStat.Stat(ImageChops.difference(
            mine.convert("RGB"), shifted.convert("RGB"))).mean) / 3
        print(f"    对照：若整体偏 1 像素，平均差会是 {smean:.3f}")

    r.close()
    if fails:
        print(f"\n❌ 失败项: {', '.join(fails)}")
        return 1
    print("\n✅ 通过：仿射变换（含半像素修正）在精确对齐下与纯裁剪完全一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
