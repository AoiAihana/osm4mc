"""可视化核对：把某条链的 L0 影像拼出来，叠加原线（红）、平滑线（蓝）与可见性判定（绿=可见/高架，橙=不可见/隧道）。

用法：python3 render_check.py <bx> <bz> [half_blocks] [out.png]
"""
from __future__ import annotations

import json
import math
import sys

from PIL import Image, ImageDraw

import l0imagery as L0
import smooth
from classify import Visibility


def render_chain(cache, chain, half=260, out="check.png", step=2.0):
    coords = [tuple(p) for p in chain["coords"]]
    sm = smooth.smooth_chain(coords)
    V = Visibility(cache)
    sc = V.score_polyline(sm, step=step)
    runs = V.classify(sc)
    label = [False] * len(sc)
    for a, b, v in runs:
        for i in range(a, b + 1):
            label[i] = v

    # 拼接影像
    xs = [p[0] for p in sm]
    zs = [p[1] for p in sm]
    bx0, bx1 = min(xs) - half, max(xs) + half
    bz0, bz1 = min(zs) - half, max(zs) + half
    nx0, ny0 = L0.tile_index(bx0, bz0)
    nx1, ny1 = L0.tile_index(bx1, bz1)
    tiles = [(nx, ny) for nx in range(nx0, nx1 + 1) for ny in range(ny0, ny1 + 1)]
    cache.prefetch(tiles)
    W = (nx1 - nx0 + 1) * 128
    H = (ny1 - ny0 + 1) * 128
    M = Image.new("RGB", (W, H), (0, 0, 0))
    for nx in range(nx0, nx1 + 1):
        for ny in range(ny0, ny1 + 1):
            img = cache.tile(nx, ny)
            if img is None:
                continue
            M.paste(img, ((nx - nx0) * 128, (ny - ny0) * 128))
    ox = nx0 * 32.0
    oz = (ny0 - 1) * 32.0  # 左上角方块 z（瓦片 ny 覆盖 bz ∈ [(ny-1)*32, ny*32]）

    def to_px(bx, bz):
        return ((bx - ox) * 4.0, (bz - oz) * 4.0)

    dr = ImageDraw.Draw(M)
    dr.line([to_px(p[0], p[1]) for p in coords], fill=(255, 0, 0), width=1)
    # 按可见性分段画
    cur = None
    seg = []
    for rec, v in zip(sc, label):
        if cur is None or v == cur:
            seg.append(rec)
            cur = v
        else:
            if len(seg) > 1:
                dr.line([to_px(r["x"], r["z"]) for r in seg], fill=(0, 220, 0) if cur else (255, 140, 0), width=2)
            seg = [rec]
            cur = v
    if len(seg) > 1:
        dr.line([to_px(r["x"], r["z"]) for r in seg], fill=(0, 220, 0) if cur else (255, 140, 0), width=2)
    # 顶点
    for p in sm:
        x, y = to_px(p[0], p[1])
        dr.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(0, 160, 255))
    if M.width > 1400:
        M = M.resize((M.width // 2, M.height // 2), Image.LANCZOS)
    M.save(out)
    return M.size, runs


def main():
    bx, bz = float(sys.argv[1]), float(sys.argv[2])
    half = int(sys.argv[3]) if len(sys.argv) > 3 else 260
    out = sys.argv[4] if len(sys.argv) > 4 else "check.png"
    chains = json.load(open("chains_raw.json"))
    best = None
    for c in chains:
        d = min(math.hypot(p[0] - bx, p[1] - bz) for p in c["coords"])
        if best is None or d < best[0]:
            best = (d, c)
    print("chain", best[1]["name"], best[1]["owner"], "len", round(best[1]["length"]), "dist", round(best[0], 1))
    cache = L0.L0Cache(workers=10)
    print(render_chain(cache, best[1], half=half, out=out))


if __name__ == "__main__":
    main()
