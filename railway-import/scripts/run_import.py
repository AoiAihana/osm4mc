"""驱动脚本：构建（并可上传）帕拉伦国有铁路的 OSM 数据。

用法：
    python3 run_import.py --dry-run [--area x0 z0 x1 z1] [--limit N] [--out osmchange.xml]
    python3 run_import.py --upload --comment "..." [--area ...]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import build_osm as B
import l0imagery as L0
import network as N
import osm_api as O
from classify import Visibility

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", nargs=4, type=float, metavar=("X0", "Z0", "X1", "Z1"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "osmchange.xml"))
    ap.add_argument("--comment", default="导入帕拉伦国有铁路线路与车站（数据来源：帕拉伦铁路线路图）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--api-base", default="http://localhost:3001")
    ap.add_argument("--node-batch", type=int, default=2500)
    ap.add_argument("--way-batch", type=int, default=300)
    args = ap.parse_args()

    data = N.load()
    chains, nodes, scope = N.build_chains(data)
    stations = [f for f in data["features"]
                if f["geometry"]["type"] == "Point" and f["properties"].get("type") == "station"]
    existing = B.load_existing_rails()

    if args.area:
        x0, z0, x1, z1 = args.area
        def in_area(c):
            return any(x0 <= p[0] <= x1 and z0 <= p[1] <= z1 for p in c.coords)
        chains = [c for c in chains if in_area(c)]
        stations = [f for f in stations
                    if x0 <= f["geometry"]["coordinates"][0] <= x1 and z0 <= f["geometry"]["coordinates"][1] <= z1]
    if args.limit:
        chains = chains[: args.limit]

    print(f"chains={len(chains)} stations={len(stations)} existing_ways={len(existing)}")
    cache = L0.L0Cache(workers=args.workers)
    V = Visibility(cache)
    builder = B.Builder(chains, stations, existing, V)

    t0 = time.time()
    for i, c in enumerate(chains):
        builder.process_chain(c)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(chains)} ways={len(builder.ways)} nodes={len(builder.nodes)} "
                  f"({time.time()-t0:.0f}s)", flush=True)
    builder.process_stations(stations)
    print("统计:", dict(builder.stats))
    print(f"ways={len(builder.ways)} nodes={len(builder.nodes)} "
          f"(其中复用既有节点 {sum(1 for n in builder.nodes.values() if 'existing' in n)})")
    print(f"L0 缓存: {cache.stats}")

    # 自检：节点经纬度与方块坐标必须能互相还原
    import geohelper as _g
    worst = 0.0
    for k, n in builder.nodes.items():
        if "existing" in n:
            continue
        bx, bz = _g.lonlat_to_block(n["lon"], n["lat"])
        worst = max(worst, abs(bx - n["bx"]), abs(bz - n["bz"]))
    print(f"坐标自检：最大还原误差 {worst:.6f} 格")
    assert worst < 1e-4, "经纬度与方块坐标不一致！"

    xml, idmap = builder.to_osmchange(0)
    print(f"osmChange 大小 {len(xml)/1e6:.2f} MB")
    with open(args.out, "w") as fh:
        fh.write(xml)
    print("written", args.out)

    if args.upload:
        # 直连 Rails（3001），绕开 edge 的 300s 代理读超时；分批以便观察进度。
        api = O.OsmApi(base=args.api_base, timeout=7200)
        cs = api.create_changeset({
            "comment": args.comment,
            "created_by": "railway-import 1.0",
            "locale": "zh-CN",
            "source": "帕拉伦铁路线路图 (railwaymap.big-brother.top)",
            "imagery_used": "Minecraft Dynmap (dyn2xyz)",
        })
        print("changeset", cs, flush=True)
        idmap, node_elems, way_elems = builder.parts(cs)
        print(f"节点 {len(node_elems)} 个，路径 {len(way_elems)} 条", flush=True)

        # 阶段一：节点
        node_map = {}
        batch = args.node_batch
        for i in range(0, len(node_elems), batch):
            chunk = node_elems[i:i + batch]
            t0 = time.time()
            res = api.upload(cs, O.build_osmchange(creates=[e[2] for e in chunk]))
            d = O.parse_diff_result(res)["node"]
            node_map.update(d)
            print(f"  节点批次 {i//batch+1}: +{len(d)}（{time.time()-t0:.0f}s）", flush=True)

        def ref(k):
            pid = idmap[k]
            if pid > 0:
                return pid
            return node_map.get(str(pid), pid)

        # 阶段二：路径
        way_batch = args.way_batch
        total_ways = 0
        for i in range(0, len(way_elems), way_batch):
            chunk = way_elems[i:i + way_batch]
            elems = []
            for wid, keys, tags in chunk:
                nds = "".join(f'<nd ref="{ref(k)}"/>' for k in keys)
                tg = "".join(f'<tag k="{B.xml_escape(k)}" v="{B.xml_escape(v)}"/>'
                             for k, v in sorted(tags.items()))
                elems.append(f'<way id="{wid}" version="0" changeset="{cs}">{nds}{tg}</way>')
            t0 = time.time()
            res = api.upload(cs, O.build_osmchange(creates=elems))
            d = O.parse_diff_result(res)["way"]
            total_ways += len(d)
            print(f"  路径批次 {i//way_batch+1}: +{len(d)}（{time.time()-t0:.0f}s）", flush=True)
        api.close_changeset(cs)
        print(f"changeset {cs} 关闭，共 +{len(node_map)} 节点 +{total_ways} 路径", flush=True)


if __name__ == "__main__":
    main()
