"""把网格特征图分类成 natural=* 类别，并输出预览图。

类别（数值即代码）：
    0 nodata   1 water   2 glacier(snow/ice)   3 sand   4 wood
    5 grassland   6 scrub   7 bare_rock   8 wetland   9 beach
"""
from __future__ import annotations

import numpy as np

CLASS_NAMES = ["nodata", "water", "glacier", "sand", "wood", "grassland", "scrub",
               "bare_rock", "wetland", "beach"]

# 预览配色
CLASS_COLORS = {
    0: (0, 0, 0),
    1: (40, 90, 200),
    2: (240, 245, 250),
    3: (225, 205, 140),
    4: (30, 90, 35),
    5: (130, 190, 90),
    6: (170, 160, 90),
    7: (140, 140, 145),
    8: (60, 120, 110),
    9: (235, 220, 160),
}


def classify(feats: np.ndarray, valid: np.ndarray, *,
             water_th=0.45, snow_th=0.50, bright_snow=175.0, sat_snow=32.0,
             sand_th=0.32, green_th=0.45, gray_th=0.45,
             wood_var=320.0, wood_bright=110.0, dry_rb=22.0) -> np.ndarray:
    (r, g, b, bright, sat, var, water, snow, green, sand, gray, dark, void) = [feats[..., i] for i in range(13)]
    out = np.full(valid.shape, 0, dtype=np.uint8)
    out[valid] = 5                                  # 默认草地
    # 干暖色（稀树草原/恶地）
    out[valid & (r - b > dry_rb)] = 6
    # 灰色岩石（明显低于雪线）
    out[valid & (gray > gray_th) & (bright <= bright_snow + 20)] = 7
    # 绿主导
    greenish = valid & (green > green_th)
    out[greenish] = 5
    out[greenish & ((var > wood_var) | (bright < wood_bright))] = 4
    # 沙
    out[valid & (sand > sand_th) & (r - b > 35)] = 3
    # 雪/冰：极白，或雪占比高
    out[valid & ((snow > snow_th) | ((bright > bright_snow) & (sat < sat_snow)))] = 2
    # 水
    out[valid & (water > water_th)] = 1
    return out


def majority_filter(labels: np.ndarray, k: int = 3) -> np.ndarray:
    """3×3 众数滤波（忽略 nodata 之外的类别边界抖动）。"""
    from collections import Counter
    h, w = labels.shape
    pad = np.pad(labels, k // 2, mode="edge")
    out = labels.copy()
    for i in range(h):
        for j in range(w):
            block = pad[i:i + k, j:j + k].ravel()
            out[i, j] = Counter(block.tolist()).most_common(1)[0][0]
    return out


def drop_small(labels: np.ndarray, min_cells: int = 12) -> np.ndarray:
    """删除面积过小的连通块（按 4 邻域）。"""
    h, w = labels.shape
    seen = np.zeros_like(labels, dtype=bool)
    out = labels.copy()
    for i in range(h):
        for j in range(w):
            if seen[i, j]:
                continue
            lab = labels[i, j]
            stack = [(i, j)]
            seen[i, j] = True
            comp = []
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and labels[ny, nx] == lab:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(comp) < min_cells:
                for y, x in comp:
                    out[y, x] = 0
    return out


def preview(labels: np.ndarray, path: str, scale: int = 1) -> None:
    from PIL import Image
    img = np.zeros((*labels.shape, 3), dtype=np.uint8)
    for k, col in CLASS_COLORS.items():
        img[labels == k] = col
    im = Image.fromarray(img)
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    im.save(path)
