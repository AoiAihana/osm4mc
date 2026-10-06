"""把整张世界地图重采样成「网格特征图」，供自然要素分类使用。

每格 CELL 个方块，统计该格内所有像素的：
    mean_r/g/b, std（亮度标准差，反映纹理：森林碎、草地平）
    frac_water（蓝色像素占比）、frac_snow（高亮低饱和）、frac_green（绿主导）、
    frac_sand（暖黄）、frac_gray（低饱和中灰）、frac_dark（很暗）
输出 grid.npz：features, valid, 以及网格原点的方块坐标与格宽。
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image

from dynmap_tiles import DynmapTiles

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD_BOUNDS = (-1024, -17440, 23552, 6112)


def build(level: int, cell: int, out_path: str, workers: int = 8):
    t = DynmapTiles(level=level, workers=workers)
    x0, z0, x1, z1 = WORLD_BOUNDS
    m = 1 << level
    nx0, ny0 = t.tile_index(x0, z0)
    nx1, ny1 = t.tile_index(x1, z1)
    xs = list(range(nx0, nx1 + 1, m))
    ys = list(range(ny0, ny1 + 1, m))
    W = len(xs) * 128
    H = len(ys) * 128
    M = Image.new("RGB", (W, H), (0, 0, 0))
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            img = t.tile(x, y)
            if img:
                M.paste(img, (i * 128, j * 128))
    a = np.asarray(M).astype(np.float32)
    # 像素 -> 方块： 瓦片 nx0 左上角 = (nx0*32, 32*(ny0-1))
    px0_block = nx0 * 32.0
    pz0_block = 32.0 * (ny0 - 1)
    blocks_per_px = 32.0 * m / 128.0     # = 32 / (128/2^L*4)… 即 tiles 覆盖方块 / 128
    cell_px = int(round(cell / blocks_per_px))
    gh = H // cell_px
    gw = W // cell_px
    a = a[: gh * cell_px, : gw * cell_px]
    r = a[:, :, 0]
    g = a[:, :, 1]
    b = a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = mx - mn
    bright = a.mean(axis=2)

    def block_mean(x):
        return x.reshape(gh, cell_px, gw, cell_px).mean(axis=(1, 3))

    def block_frac(x):
        return x.reshape(gh, cell_px, gw, cell_px).mean(axis=(1, 3))

    water = ((b > r + 15) & (b > g + 5) & (b > 55)).astype(np.float32)
    snow = ((bright > 205) & (sat < 45)).astype(np.float32)
    green = ((g > r + 8) & (g > b + 8)).astype(np.float32)
    sand = ((r > g + 6) & (g > b + 10) & (r - b > 35) & (bright > 140)).astype(np.float32)
    gray = ((sat < 28) & (bright > 80) & (bright <= 205)).astype(np.float32)
    dark = (bright < 45).astype(np.float32)
    # Dynmap 未渲染区块的固定底色 (79,79,79)：必须当成"无数据"
    void = ((np.abs(r - 79) < 3) & (np.abs(g - 79) < 3) & (np.abs(b - 79) < 3)).astype(np.float32)

    feats = np.stack([
        block_mean(r), block_mean(g), block_mean(b), block_mean(bright), block_mean(sat),
        block_mean(bright ** 2) - block_mean(bright) ** 2,
        block_frac(water), block_frac(snow), block_frac(green), block_frac(sand),
        block_frac(gray), block_frac(dark), block_frac(void),
    ], axis=-1)
    valid = (block_mean(bright) > 12) & (block_frac(void) < 0.5)
    np.savez_compressed(out_path, feats=feats.astype(np.float32), valid=valid,
                        origin=(px0_block, pz0_block), cell=cell)
    print(f"grid {feats.shape} cell={cell} 原点方块=({px0_block:.0f},{pz0_block:.0f}) "
          f"有效格 {int(valid.sum())}/{valid.size}")
    return feats, valid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=3)
    ap.add_argument("--cell", type=int, default=32)
    ap.add_argument("--out", default=os.path.join(HERE, "grid.npz"))
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    build(args.level, args.cell, args.out, args.workers)
