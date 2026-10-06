#!/usr/bin/env python3
"""扫描上游 Dynmap，找出地图实际覆盖的方块范围，输出可直接粘进配置的 bounds_blocks。

做法是在**最粗层级**（L = mapzoomout，覆盖范围最大）从种子瓦片向外一圈圈扩，
直到连续若干圈都没有数据为止。这样对"世界集中在原点附近"的常见情况只需很少的请求。

用法::

    python3 tools/scan_extent.py [配置文件] [--max-radius 40] [--empty-rings 3]

输出形如::

    bounds_blocks = [-8192, -12288, 10240, 8192]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import build_source, load_config  # noqa: E402
from app.dynmap import http_get  # noqa: E402


def tile_exists(url: str, timeout: float) -> bool:
    """用 HEAD 探测瓦片是否存在。Dynmap 对未渲染/越界瓦片返回 404。"""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "dyn2xyz-scan/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (404, 403, 410, 204):
            return False
        raise
    except Exception:  # noqa: BLE001 - 网络抖动按「不存在」处理，宁可少算
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", default="config/dyn2xyz.toml")
    ap.add_argument("--max-radius", type=int, default=40, help="最大扫描半径（瓦片数）")
    ap.add_argument("--empty-rings", type=int, default=3, help="连续多少圈为空就停止")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--seed", default="0,0", help="起始方块坐标 bx,bz")
    args = ap.parse_args()

    cfg = load_config(args.config)
    src = build_source(cfg)
    g = src.geometry
    level = g.mapzoomout
    span = g.blocks_per_tile(level)
    print(f"源: {src.webroot}  {src.world}/{src.prefix}")
    print(f"在最粗层级 L={level} 上扫描，每瓦片覆盖 {span:g} 方块\n")

    sbx, sbz = (float(v) for v in args.seed.split(","))
    cx, cy = g.tile_index(sbx, sbz, level)
    # 关键：层级 L 的合法瓦片索引必须是 2^L 的倍数（见 tools/../app/dynmap.py 的
    # tile_index 说明：nL = 2^L * floor(n0 / 2^L)）。所以向外扩圈时步长必须是 2^L，
    # 而不是 1——按 1 递增探到的索引（如 L=5 时的 95、97）根本不存在，永远 404，
    # 会把「邻居都是空的」这个假象当成结论。
    step = 1 << level

    found: dict[tuple[int, int], bool] = {}
    checked = 0

    def check(cell: tuple[int, int]) -> tuple[tuple[int, int], bool]:
        url = src.tile_url(*cell, level)
        return cell, tile_exists(url, cfg.source.timeout)

    empty_streak = 0
    for ring in range(0, args.max_radius + 1):
        cells = []
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if max(abs(dx), abs(dy)) != ring:
                    continue  # 只取当前这一圈
                cells.append((cx + dx * step, cy + dy * step))
        if not cells:
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(check, cells))
        checked += len(cells)
        hits = [c for c, ok in results if ok]
        for c, ok in results:
            found[c] = ok

        print(f"  第 {ring:>3} 圈：{len(hits):>3}/{len(cells):>3} 有数据"
              + (f"   累计请求 {checked}" if ring % 5 == 0 else ""))
        empty_streak = empty_streak + 1 if not hits else 0
        if empty_streak >= args.empty_rings and any(found.values()):
            print(f"  连续 {args.empty_rings} 圈为空，停止。")
            break

    live = [c for c, ok in found.items() if ok]
    if not live:
        print("\n没有扫到任何数据。可能是种子点选错了，或该地图尚未渲染。")
        return 1

    # 连通聚类：地图常常是「若干个互不相连的据点」，而不是一整块。
    # 只报外接矩形会让中心落在空地上，所以顺便把最大的那一簇找出来。
    live_set = set(live)
    seen: set[tuple[int, int]] = set()
    clusters: list[list[tuple[int, int]]] = []
    for cell in live:
        if cell in seen:
            continue
        stack, comp = [cell], []
        seen.add(cell)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for dx in (-step, 0, step):
                for dy in (-step, 0, step):
                    nb = (cur[0] + dx, cur[1] + dy)
                    if nb in live_set and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        clusters.append(comp)
    clusters.sort(key=len, reverse=True)

    minx = min(c[0] for c in live)
    maxx = max(c[0] for c in live)
    miny = min(c[1] for c in live)
    maxy = max(c[1] for c in live)
    x0, z0, _, _ = g.tile_block_bounds(minx, miny, level)
    _, _, x1, z1 = g.tile_block_bounds(maxx, maxy, level)

    print(f"\n扫到 {len(live)} 个有数据的瓦片（共请求 {checked} 次）")
    print(f"全部数据的瓦片索引范围: x [{minx}, {maxx}], y [{miny}, {maxy}]")

    cx_t, cy_t = live[len(live) // 2]
    sum_x = sum(c[0] for c in live) / len(live)
    sum_y = sum(c[1] for c in live) / len(live)
    bx_c, bz_c, _, _ = g.tile_block_bounds(int(round(sum_x)), int(round(sum_y)), level)

    print(f"\n连通性: 共 {len(clusters)} 个互不相连的据点")
    for i, comp in enumerate(clusters[:5]):
        cminx = min(c[0] for c in comp); cmaxx = max(c[0] for c in comp)
        cminy = min(c[1] for c in comp); cmaxy = max(c[1] for c in comp)
        ccx = sum(c[0] for c in comp) / len(comp)
        ccy = sum(c[1] for c in comp) / len(comp)
        a, b, c2, d = g.tile_block_bounds(int(round(ccx)), int(round(ccy)), level)
        ax, az, _, _ = g.tile_block_bounds(cminx, cminy, level)
        _, _, bx2, bz2 = g.tile_block_bounds(cmaxx, cmaxy, level)
        print(f"  #{i + 1}: {len(comp):>4} 块瓦片  中心≈方块 ({a + span / 2:.0f}, {b + span / 2:.0f})"
              f"  范围 X[{ax:.0f},{bx2:.0f}] Z[{az:.0f},{bz2:.0f}]")
    if len(clusters) > 5:
        print(f"  ... 另有 {len(clusters) - 5} 个更小的据点")

    print(f"\n把下面这几行加进配置文件的 [geo] 段：\n")
    print(f"bounds_blocks = [{x0:.0f}, {z0:.0f}, {x1:.0f}, {z1:.0f}]")
    print(f"# 含义: 全部数据的外接矩形，方块 X ∈ [{x0:.0f}, {x1:.0f}]，Z ∈ [{z0:.0f}, {z1:.0f}]")
    print(f"#       约 {(x1 - x0) / 1000:.1f}k × {(z1 - z0) / 1000:.1f}k 方块，"
          f"但只有 {len(live)} 个瓦片有数据，所以里面大部分是空的")
    print()
    print(f"# 让 iD / QGIS 一打开就落在**最大的那个据点**中心（把地图摆到经纬度 0,0）：")
    print(f"origin_block_x = {a + span / 2:.0f}")
    print(f"origin_block_z = {b + span / 2:.0f}")
    print(f"# （全部数据的质心在方块 ({bx_c + span / 2:.0f}, {bz_c + span / 2:.0f})，"
          "若最大据点不是你想看的，改用质心或某个据点的中心）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
