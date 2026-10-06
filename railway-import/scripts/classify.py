"""用 Minecraft 卫星影像判断线路是否在地表可见（=> 高架/桥梁），还是被地表覆盖（=> 隧道/地下）。

影像来源：上游 Dynmap 的 level-0 瓦片（4 像素/方块，见 l0imagery.py）。

判据（核心是"垂直剖面"）
------------------------
在采样点处取垂直于轨道的剖面：k = -16…16 格（每 2 格一点），
再沿轨道方向取若干帧（-24…24 格）求平均——这样能压掉地表纹理（树、雪斑）噪声，
保留沿轨道连续的线状结构。

由平均剖面得到
    center = |k| <= 3 的平均色
    flank  = 7 <= |k| <= 15 的平均色
    delta  = center 与 flank 的最大通道差
    gray   = center 的通道极差
    bright = center 亮度
    fstd   = flank 各点的空间标准差（地形纹理噪声）

可见（地表/高架）：delta 够大、center 够"灰"、亮度落在轨道结构范围内。
"""
from __future__ import annotations

import math

import numpy as np

import l0imagery as L0

KS = list(range(-16, 17, 2))
TS = (-24, -12, 0, 12, 24)


class Visibility:
    def __init__(self, cache: L0.L0Cache | None = None):
        self.c = cache or L0.L0Cache(workers=8)

    # -- 单点剖面 --------------------------------------------------------
    def profile(self, x: float, z: float, nx: float, nz: float, tx: float, tz: float):
        cols = []
        for k in KS:
            acc = [0.0, 0.0, 0.0]
            n = 0
            for t in TS:
                c = self.c.pixel(x + nx * k + tx * t, z + nz * k + tz * t)
                if c is None:
                    continue
                acc[0] += c[0]
                acc[1] += c[1]
                acc[2] += c[2]
                n += 1
            cols.append(tuple(a / n for a in acc) if n else None)
        return cols

    @staticmethod
    def features(cols):
        ok = [(KS[i], c) for i, c in enumerate(cols) if c is not None]
        if len(ok) < 8:
            return None
        center = [c for k, c in ok if abs(k) <= 3]
        flank = [c for k, c in ok if 7 <= abs(k) <= 15]
        if not center or not flank:
            return None
        cm = tuple(sum(c[i] for c in center) / len(center) for i in range(3))
        # flank 用逐通道中位数，抗地表纹理
        fm = tuple(sorted(c[i] for c in flank)[len(flank) // 2] for i in range(3))
        delta = max(abs(cm[i] - fm[i]) for i in range(3))
        gray = max(cm) - min(cm)
        bright = sum(cm) / 3.0
        green = cm[1] - max(cm[0], cm[2])
        fstd = float(np.mean([np.std([c[i] for c in flank]) for i in range(3)]))
        cstd = float(np.mean([np.std([c[i] for c in center]) for i in range(3)]))
        return {
            "delta": delta, "gray": gray, "bright": bright, "green": green,
            "fstd": fstd, "cstd": cstd, "center": cm, "flank": fm,
        }

    # -- 沿线打分 --------------------------------------------------------
    def score_polyline(self, coords, step: float = 4.0):
        pts = [(c[0], c[1]) for c in coords]
        samples = []
        acc = 0.0
        carry = 0.0
        samples.append((0.0, pts[0]))
        for a, b in zip(pts, pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg < 1e-9:
                continue
            t = 0.0
            while carry + (seg - t) >= step:
                t += step - carry
                carry = 0.0
                r = t / seg
                samples.append((acc + t, (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)))
            carry += seg - t
            acc += seg
        if samples[-1][1] != pts[-1]:
            samples.append((acc, pts[-1]))

        out = []
        n = len(samples)
        for i, (s, (x, z)) in enumerate(samples):
            j0 = max(0, i - 2)
            j1 = min(n - 1, i + 2)
            tx = samples[j1][1][0] - samples[j0][1][0]
            tz = samples[j1][1][1] - samples[j0][1][1]
            L = math.hypot(tx, tz)
            if L < 1e-9:
                tx, tz, nx, nz = 1.0, 0.0, 0.0, 1.0
            else:
                tx, tz = tx / L, tz / L
                nx, nz = -tz, tx
            cols = self.profile(x, z, nx, nz, tx, tz)
            f = self.features(cols)
            rec = {"s": s, "x": x, "z": z, "valid": f is not None}
            if f:
                rec.update(f)
            out.append(rec)
        return out

    # -- 分类 ------------------------------------------------------------
    @staticmethod
    def visible_of(rec, delta_th=12.0, gray_th=30.0, green_th=12.0, bright_lo=40.0, bright_hi=215.0):
        """可见（地表/高架）判据：中心是"灰的、亮度适中的人工结构"，且与两侧地表有明显差异。"""
        if not rec.get("valid"):
            return False
        return (
            rec["delta"] >= delta_th
            and rec["gray"] <= gray_th
            and rec["green"] <= green_th
            and bright_lo <= rec["bright"] <= bright_hi
        )

    @classmethod
    def classify(cls, scores, median_win: int = 5, min_run: float = 40.0, **kw):
        n = len(scores)
        if n == 0:
            return []
        vis = np.array([1.0 if cls.visible_of(r, **kw) else 0.0 for r in scores])
        valid = np.array([bool(r.get("valid")) for r in scores])
        if not valid.all():
            idx = np.where(valid)[0]
            if len(idx) == 0:
                return [(0, n - 1, False)]
            for i in range(n):
                if not valid[i]:
                    vis[i] = vis[idx[np.argmin(np.abs(idx - i))]]
        if median_win > 1 and n > median_win:
            k = median_win
            pad = np.pad(vis, (k // 2, k // 2), mode="edge")
            vis = np.array([np.median(pad[i:i + k]) for i in range(n)])
        runs = []
        start = 0
        cur = vis[0]
        for i in range(1, n):
            if vis[i] != cur:
                runs.append([start, i - 1, bool(cur)])
                start = i
                cur = vis[i]
        runs.append([start, n - 1, bool(cur)])
        # 合并过短的段
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, r in enumerate(runs):
                ln = scores[r[1]]["s"] - scores[r[0]]["s"]
                if ln >= min_run:
                    continue
                if i == 0:
                    runs[1][0] = r[0]
                elif i == len(runs) - 1:
                    runs[-2][1] = r[1]
                else:
                    prev_len = scores[runs[i - 1][1]]["s"] - scores[runs[i - 1][0]]["s"]
                    next_len = scores[runs[i + 1][1]]["s"] - scores[runs[i + 1][0]]["s"]
                    if prev_len >= next_len:
                        runs[i - 1][1] = r[1]
                    else:
                        runs[i + 1][0] = r[0]
                runs.pop(i)
                changed = True
                break
        out = []
        for r in runs:
            if out and out[-1][2] == r[2]:
                out[-1][1] = r[1]
            else:
                out.append(r)
        return [(a, b, v) for a, b, v in out]
