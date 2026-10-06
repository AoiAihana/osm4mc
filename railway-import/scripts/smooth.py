"""线形处理：把 Minecraft 方块台阶状的原始折线变成平滑、疏密有致的线路。

流程：
    重采样(1 格) -> 滑动平均(去台阶锯齿) -> Douglas-Peucker(去冗余点) -> 按角度抽稀(弯道约 20° 一点)
"""
from __future__ import annotations

import math

Point = tuple[float, float]


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def resample(points: list[Point], step: float = 1.0) -> list[Point]:
    """按固定弧长重采样（保留首尾点）。"""
    if len(points) < 2:
        return list(points)
    out = [points[0]]
    carry = 0.0
    for a, b in zip(points, points[1:]):
        seg = _dist(a, b)
        if seg <= 1e-12:
            continue
        t = 0.0
        while carry + (seg - t) >= step:
            t += step - carry
            carry = 0.0
            r = t / seg
            out.append((a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r))
        carry += seg - t
    if _dist(out[-1], points[-1]) > 1e-9:
        out.append(points[-1])
    return out


def moving_average(points: list[Point], half: int = 3, weights: str = "triangular") -> list[Point]:
    """对称滑动平均，端点用镜像延拓，因此首尾点不会被拉动。"""
    n = len(points)
    if n < 3 or half <= 0:
        return list(points)
    if weights == "triangular":
        w = [half + 1 - abs(k) for k in range(-half, half + 1)]
    else:
        w = [1] * (2 * half + 1)
    wsum = sum(w)

    def get(i):
        # 镜像延拓
        if i < 0:
            i = -i
        if i >= n:
            i = 2 * (n - 1) - i
        i = max(0, min(n - 1, i))
        return points[i]

    out = []
    for i in range(n):
        sx = sy = 0.0
        for k in range(-half, half + 1):
            p = get(i + k)
            ww = w[k + half]
            sx += p[0] * ww
            sy += p[1] * ww
        out.append((sx / wsum, sy / wsum))
    out[0] = points[0]
    out[-1] = points[-1]
    return out


def _perp_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dp_simplify(points: list[Point], eps: float) -> list[Point]:
    """Douglas-Peucker 简化（迭代实现，避免深递归）。"""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        dmax, idx = -1.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(points[k], points[i], points[j])
            if d > dmax:
                dmax, idx = d, k
        if dmax > eps and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def _heading(a: Point, b: Point) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _angdiff(a: float, b: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return d


def densify_max_seg(points: list[Point], max_seg: float) -> list[Point]:
    """把过长的直线段按 max_seg 打断（保证没有超长段）。"""
    if len(points) < 2 or max_seg <= 0:
        return list(points)
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        d = _dist(a, b)
        if d > max_seg:
            n = int(math.ceil(d / max_seg))
            for k in range(1, n):
                t = k / n
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


def angle_thin(points: list[Point], max_turn_deg: float = 20.0, max_seg: float = 200.0,
               max_dev: float = 1.2) -> list[Point]:
    """按累计转角抽稀：从上一次输出的点开始，累计转角达到 max_turn_deg 才放一个新点。

    另外限制单段弦长（max_seg）与弦到原折线的偏差（max_dev）。
    """
    n = len(points)
    if n < 3:
        return list(points)
    budget = math.radians(max_turn_deg)
    out = [points[0]]
    i = 0
    while i < n - 1:
        acc = 0.0
        j = i + 1
        last_ok = j
        while j + 1 < n:
            turn = abs(_angdiff(_heading(points[j - 1], points[j]), _heading(points[j], points[j + 1])))
            if acc + turn > budget:
                break
            acc += turn
            j += 1
            if _dist(points[i], points[j]) > max_seg:
                break
            last_ok = j
        j = max(last_ok, i + 1)
        # 精确校验偏差，超了就逐步回退
        while j > i + 1:
            dev = max(_perp_dist(points[k], points[i], points[j]) for k in range(i + 1, j))
            if dev <= max_dev:
                break
            j -= 1
        out.append(points[j])
        i = j
    if _dist(out[-1], points[-1]) > 1e-9:
        out.append(points[-1])
    return out


def smooth_chain(coords: list[tuple[float, float, float]], *, step: float = 1.0, half: int = 3,
                 eps: float = 0.6, max_turn_deg: float = 20.0, max_seg: float = 200.0, max_dev: float = 1.2) -> list[tuple[float, float, float]]:
    """输入是一条链的原始三维坐标（x,y,z 方块），输出平滑后的坐标（z 取两端线性插值）。"""
    if len(coords) < 2:
        return list(coords)
    xy = [(c[0], c[1]) for c in coords]
    zs = [c[2] for c in coords]
    # 弧长 → z 的映射（用于插值）
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + _dist((a[0], a[1]), (b[0], b[1])))
    total = cum[-1] or 1.0

    r = resample(xy, step)
    m = moving_average(r, half)
    d = dp_simplify(m, eps)
    d = densify_max_seg(d, max_seg)
    t = angle_thin(d, max_turn_deg=max_turn_deg, max_seg=max_seg, max_dev=max_dev)

    # 给输出点补 z：按到原折线的最近点位置插值
    out = []
    for p in t:
        # 找最近的原始线段
        best_d, best_z = 1e18, zs[0]
        for k in range(len(xy) - 1):
            a, b = xy[k], xy[k + 1]
            dd = _perp_dist(p, a, b)
            if dd < best_d:
                best_d = dd
                ax, ay = a
                bx, by = b
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                tt = 0.0 if L2 <= 1e-18 else ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
                tt = max(0.0, min(1.0, tt))
                best_z = zs[k] + (zs[k + 1] - zs[k]) * tt
        out.append((p[0], p[1], best_z))
    return out
