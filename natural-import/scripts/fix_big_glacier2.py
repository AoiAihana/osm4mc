"""重做超大冰川面：上一次脚本把经纬度当成方块坐标，抽稀成了 2 个点。

这次：
  * 用本地 polygons_coast.json 里的环（2056 点，方块坐标）；
  * 用占位 id -> 真实 id 的映射取出这条环的节点 id；
  * 以 32 方块的容差抽稀到 ~800 点，作为一条闭合 way 重新上传；
  * 同时删掉上一次生成的退化 way（2 个点）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))
sys.path.insert(0, HERE)

import osm_api as O                     # noqa: E402
from polygons import _dp                # noqa: E402
from natural_repair2 import build       # noqa: E402

EPS_CELLS = 2.0        # 容差 = 2 格 × 16 方块 = 32 方块
NEW_WAY_PLACEHOLDER = -1


def q(db, user, sql):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", user, "-d", db,
                          "-At", "-F", "|", "-c", sql], capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def main():
    nodes, ways = build()
    big = [(wid, refs, cls) for wid, refs, cls in ways if len(refs) > 2000]
    assert len(big) == 1, big
    wid, refs, cls = big[0]
    pts = [(x, z) for x, z in [(0, 0)]][:0]
    print(f"大环：{len(refs)} 点，类 {cls}")

    # 占位 id -> 真实 id
    ids = []
    for cs_id in (94, 95, 96):
        ids += [int(r[0]) for r in q("openstreetmap", "openstreetmap",
                                     f"select id from current_nodes where changeset_id={cs_id} order by id;")]
    node_map = {nodes[k][0]: ids[k] for k in range(len(nodes))}
    real = [node_map[r] for r in refs]
    print("真实节点数", len(real))

    # 抽稀（用环上节点的方块坐标）
    coord = {n[0]: (gx, gz) for n, (gx, gz) in zip(nodes, [])} if False else None
    import geohelper as g
    coords = []
    for pid in refs:
        idx = next(i for i, n in enumerate(nodes) if n[0] == pid)
        lon, lat = nodes[idx][2], nodes[idx][1]
        coords.append(g.lonlat_to_block(lon, lat))
    simp = _dp(coords, EPS_CELLS * 16)
    keep = set()
    # 用同样的 DP 求出保留的下标
    def dp_idx(points, eps):
        keep = {0, len(points) - 1}
        stack = [(0, len(points) - 1)]
        import math
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
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2)) if L2 > 1e-12 else 0.0
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                if d > dmax:
                    dmax, idx = d, k
            if dmax > eps:
                keep.add(idx)
                stack.append((i, idx))
                stack.append((idx, j))
        return sorted(keep)

    idx = dp_idx(coords, EPS_CELLS * 16)
    new_refs = [real[i] for i in idx]
    if new_refs[0] != new_refs[-1]:
        new_refs.append(new_refs[0])
    print(f"抽稀到 {len(idx)} 点（含闭合 {len(new_refs)}）")

    # 找到上次那条退化 way
    bad = q("openstreetmap", "openstreetmap",
            "select id, version from current_ways where changeset_id=105 and visible;")
    print("待删退化 way:", bad)

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    cs = api.create_changeset({
        "comment": "重做冰川大面：抽稀为单条闭合 way（修正上次误用经纬度导致的退化几何）",
        "created_by": "natural-import 1.0", "locale": "zh-CN",
    })
    print("changeset", cs, flush=True)
    nds = "".join(f'<nd ref="{r}"/>' for r in new_refs)
    create = [f'<way id="-1" version="0" changeset="{cs}">{nds}<tag k="natural" v="glacier"/></way>']
    delete = [f'<way id="{w}" version="{v}" changeset="{cs}"/>' for w, v in bad]
    t0 = time.time()
    res = api.upload(cs, O.build_osmchange(creates=create, deletes=delete))
    d = O.parse_diff_result(res)
    print(f"+{len(d['way'])} way，-{len(delete)}（{time.time()-t0:.0f}s）")
    api.close_changeset(cs)


if __name__ == "__main__":
    main()
