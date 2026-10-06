"""把类别栅格转成多边形（闭合环 + Douglas-Peucker 简化）。

栅格是 cell×cell 方块一格；边界沿格子边走，得到直角折线，再用 DP 抽稀成粗略多边形。
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np


def components(mask: np.ndarray):
    """4 邻域连通块，返回 [(label, cells)]，cells 为 (row, col) 列表。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for i in range(h):
        row = mask[i]
        for j in np.nonzero(row & ~seen[i])[0]:
            if seen[i, j]:
                continue
            stack = [(i, j)]
            seen[i, j] = True
            cells = []
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                if y > 0 and mask[y - 1, x] and not seen[y - 1, x]:
                    seen[y - 1, x] = True
                    stack.append((y - 1, x))
                if y + 1 < h and mask[y + 1, x] and not seen[y + 1, x]:
                    seen[y + 1, x] = True
                    stack.append((y + 1, x))
                if x > 0 and mask[y, x - 1] and not seen[y, x - 1]:
                    seen[y, x - 1] = True
                    stack.append((y, x - 1))
                if x + 1 < w and mask[y, x + 1] and not seen[y, x + 1]:
                    seen[y, x + 1] = True
                    stack.append((y, x + 1))
            out.append(cells)
    return out


def trace_boundaries(cells):
    """把连通块的外边界拆成若干闭合环（栅格角点坐标，单位=格）。"""
    cellset = set(cells)
    edges = defaultdict(list)   # (y,x) -> [(y,x), ...]
    for (y, x) in cells:
        if (y - 1, x) not in cellset:      # 上边：左->右
            edges[(y, x)].append((y, x + 1))
        if (y + 1, x) not in cellset:      # 下边：右->左
            edges[(y + 1, x + 1)].append((y + 1, x))
        if (y, x - 1) not in cellset:      # 左边：下->上
            edges[(y + 1, x)].append((y, x))
        if (y, x + 1) not in cellset:      # 右边：上->下
            edges[(y, x + 1)].append((y + 1, x + 1))
    loops = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        cur = start
        while True:
            nxts = edges.get(cur)
            if not nxts:
                break
            nxt = nxts.pop()
            if not nxts:
                del edges[cur]
            loop.append(nxt)
            cur = nxt
            if cur == start:
                break
        if len(loop) > 3:
            loops.append(loop)
    return loops


def _dp(points, eps):
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        dmax, idx = -1.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            if L2 <= 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > dmax:
                dmax, idx = d, k
        if dmax > eps:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def _signed_area(loop):
    a = 0.0
    for i in range(len(loop) - 1):
        (y0, x0), (y1, x1) = loop[i], loop[i + 1]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def mask_to_polygons(mask: np.ndarray, origin: tuple[float, float], cell: float, *,
                     min_cells: int = 8, eps_cells: float = 1.2,
                     skip_touching_border: bool = False, max_cells: int | None = None):
    """把二值掩膜转成多边形。

    * 每个连通块只取**外环**（|有向面积| 最大的那个环），内部孔洞丢弃：
      渲染时内层类别会按面积从大到小后画，视觉上仍然是内层覆盖外层。
    * skip_touching_border=True 时跳过与栅格边界相连的连通块（用于排除海洋）。
    """
    h, w = mask.shape
    out = []
    for cells in components(mask):
        n = len(cells)
        if n < min_cells:
            continue
        if max_cells is not None and n > max_cells:
            continue
        if skip_touching_border:
            if any(y == 0 or y == h - 1 or x == 0 or x == w - 1 for (y, x) in cells):
                continue
        loops = trace_boundaries(cells)
        if not loops:
            continue
        outer = max(loops, key=lambda lp: abs(_signed_area(lp)))
        pts = [(origin[0] + x * cell, origin[1] + y * cell) for (y, x) in outer]
        simp = _dp(pts, eps_cells * cell)
        if len(simp) < 4:
            continue
        if math.hypot(simp[0][0] - simp[-1][0], simp[0][1] - simp[-1][1]) > 1e-9:
            simp.append(simp[0])
        area = abs(sum(simp[i][0] * simp[i + 1][1] - simp[i + 1][0] * simp[i][1]
                       for i in range(len(simp) - 1))) / 2.0
        out.append({"ring": simp, "cells": n, "area": area})
        _ = h, w
    return out
