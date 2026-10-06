"""只补传"路径"部分（节点已在 cs 94/95/96 全部传完）。

* 占位 id -k 对应 cs94 首个 id + (k-1)；
* 超过 2000 节点的环拆成多段外环 + multipolygon 关系（OSM 单条 way 上限 2000 点）；
* 每批一个独立 changeset。
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

import osm_api as O                                   # noqa: E402
from natural_upload import TAGS, xml_escape           # noqa: E402
from natural_repair2 import build, fresh_upload       # noqa: E402

NODE_CS = (94, 95, 96)
COMMENT = "自然地貌（按海岸线裁剪）：森林/雪原/裸岩/沙地/湖泊等，仅画大范围"
MAX_WAY_NODES = 1900


def q(sql):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def main():
    nodes, ways = build()
    print(f"本地 {len(nodes)} 节点 / {len(ways)} 路径")
    # 节点 id 在各 changeset 内连续，但 changeset 之间可能有空洞，所以逐批取回再拼接
    ids = []
    for cs_id in NODE_CS:
        ids += [int(r[0]) for r in q(f"select id from current_nodes where changeset_id={cs_id} order by id;")]
    print(f"库中已传节点 {len(ids)}")
    assert len(ids) == len(nodes), f"节点数量不一致：库 {len(ids)} vs 本地 {len(nodes)}"
    node_map = {str(nodes[k][0]): ids[k] for k in range(len(nodes))}

    def ref(pid):
        return node_map.get(str(pid), pid)

    # 找出超大环
    big = [(wid, refs, cls) for wid, refs, cls in ways if len(refs) > MAX_WAY_NODES]
    print(f"超过 {MAX_WAY_NODES} 点的环: {len(big)} 个 -> {[len(r) for _, r, _ in big]}")

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    plain = [(w, r, c) for w, r, c in ways if len(r) <= MAX_WAY_NODES]
    total_w = total_r = 0

    # cs98 已经传了前 40 条
    already = int(q("select count(*) from current_ways where changeset_id=98;")[0][0])
    print(f"cs98 已传 {already} 条路径，从第 {already+1} 条继续")
    wb = 40
    for i in range(already, len(plain), wb):
        chunk = plain[i:i + wb]

        def elems(cs, chunk=chunk):
            out = []
            for wid_, refs, cls in chunk:
                nds = "".join(f'<nd ref="{ref(r)}"/>' for r in refs)
                tags = "".join(f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>'
                               for k, v in TAGS[cls].items())
                out.append(f'<way id="{wid_}" version="0" changeset="{cs}">{nds}{tags}</way>')
            return out

        d = fresh_upload(api, f"路径 {i//wb+1}/{(len(plain)+wb-1)//wb}", elems, COMMENT)
        total_w += len(d["way"])

    # 超大环：拆段 + multipolygon
    for wid_, refs, cls in big:
        pts = refs[:-1] if refs[0] == refs[-1] else refs
        n = len(pts)
        parts = []
        step = MAX_WAY_NODES - 1
        i = 0
        while i < n:
            j = min(i + step, n)
            seg = pts[i:j]
            if j < n:
                seg = seg + [pts[j]]
            else:
                seg = seg + [pts[0]]
            parts.append(seg)
            i = j
        print(f"  环 {wid_} {n} 点 -> {len(parts)} 段 {[len(p) for p in parts]}", flush=True)

        def elems(cs, parts=parts, wid_=wid_, cls=cls):
            out = []
            way_ids = []
            for k, seg in enumerate(parts):
                wid2 = wid_ * 100 - k
                way_ids.append((wid2, seg))
                nds = "".join(f'<nd ref="{ref(r)}"/>' for r in seg)
                out.append(f'<way id="{wid2}" version="0" changeset="{cs}">{nds}</way>')
            mem = "".join(f'<member type="way" ref="{wid2}" role="outer"/>' for wid2, _ in way_ids)
            tags = "".join(f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>'
                           for k, v in TAGS[cls].items())
            out.append(f'<relation id="{wid_}" version="0" changeset="{cs}">{mem}'
                       f'<tag k="type" v="multipolygon"/>{tags}</relation>')
            return out

        cs = api.create_changeset({"comment": COMMENT, "created_by": "natural-import 1.0",
                                   "locale": "zh-CN"})
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=elems(cs)))
        d = O.parse_diff_result(res)
        api.close_changeset(cs)
        total_w += len(d["way"])
        total_r += len(d.get("relation", {}))
        print(f"  环 {wid_}: cs{cs} +{len(d['way'])} 路径 +{len(d.get('relation', {}))} 关系"
              f"（{time.time()-t0:.0f}s）", flush=True)
    print(f"完成：+{total_w} 路径 +{total_r} 关系")


if __name__ == "__main__":
    main()
