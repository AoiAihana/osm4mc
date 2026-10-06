"""补传：重跑构建（结果确定），把上一轮因 changeset 超时关闭而没传完的路径补上。

前置：changeset 58 已创建 8444 个节点（id 18553..26996，顺序与占位 id -1..-8444 一致）
      以及前 1500 条路径。
"""
from __future__ import annotations

import time

import build_osm as B
import l0imagery as L0
import network as N
import osm_api as O
from classify import Visibility

DONE_WAYS = 1500      # 已上传的路径条数
BASE_NODE_ID = 18552  # 占位 -1 对应的真实 id 减 1


def main():
    data = N.load()
    chains, nodes, scope = N.build_chains(data)
    stations = [f for f in data["features"]
                if f["geometry"]["type"] == "Point" and f["properties"].get("type") == "station"]
    existing = B.load_existing_rails()
    V = Visibility(L0.L0Cache(workers=8))
    b = B.Builder(chains, stations, existing, V)
    for c in chains:
        b.process_chain(c)
    b.process_stations(stations)
    idmap, node_elems, way_elems = b.parts(0)
    print(f"nodes={len(node_elems)} ways={len(way_elems)} 已传 {DONE_WAYS}，待补 {len(way_elems)-DONE_WAYS}")
    assert len(node_elems) == 8444, "节点数量与上一轮不一致，必须重来"

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    cs = api.create_changeset({
        "comment": "补传帕拉伦国有铁路线路（上一 changeset 超时关闭后剩余部分）",
        "created_by": "railway-import 1.0",
        "locale": "zh-CN",
        "source": "帕拉伦铁路线路图 (railwaymap.big-brother.top)",
        "imagery_used": "Minecraft Dynmap (dyn2xyz)",
    })
    print("changeset", cs, flush=True)

    def ref(k):
        pid = idmap[k]
        if pid > 0:
            return pid
        return BASE_NODE_ID + (-pid)

    todo = way_elems[DONE_WAYS:]
    batch = 120
    total = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        elems = []
        for wid, keys, tags in chunk:
            nds = "".join(f'<nd ref="{ref(k)}"/>' for k in keys)
            tg = "".join(f'<tag k="{B.xml_escape(k)}" v="{B.xml_escape(v)}"/>'
                         for k, v in sorted(tags.items()))
            elems.append(f'<way id="{wid}" version="0" changeset="{cs}">{nds}{tg}</way>')
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=elems))
        d = O.parse_diff_result(res)["way"]
        total += len(d)
        print(f"  批次 {i//batch+1}: +{len(d)}（{time.time()-t0:.0f}s）", flush=True)
    api.close_changeset(cs)
    print(f"changeset {cs} 关闭，补传 {total} 条路径", flush=True)


if __name__ == "__main__":
    main()
