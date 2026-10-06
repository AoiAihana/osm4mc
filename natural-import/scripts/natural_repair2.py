"""续传：changeset 94 只传完前 10000 个节点就被超时关闭。

这里：
  1. 校验 cs 94 已传的节点与本地顺序一致（占位 id -1.. -N 对应 id 升序）；
  2. 剩余节点 + 全部闭合路径，**每批一个独立 changeset**（创建→上传→立即关闭），
     这样即使中途停顿，也不会再出现"changeset 被关闭、数据半拉子"的情况。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))
sys.path.insert(0, HERE)

import geohelper as g          # noqa: E402
import osm_api as O            # noqa: E402
from natural_upload import ORDER, TAGS, xml_escape   # noqa: E402

DONE_CS = 94
POLYS = os.path.join(HERE, "polygons_coast.json")
COMMENT = "自然地貌（按海岸线裁剪）：森林/雪原/裸岩/沙地/湖泊等，仅画大范围"


def q(sql):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def build():
    polys = json.load(open(POLYS))
    d = np.load(os.path.join(HERE, "grid32.npz"))
    lab = np.load(os.path.join(HERE, "labels_fine.npy"))
    ox, oz = d["origin"]
    cell = 16.0
    water = (lab == 1)          # 细网格上的水（含海）
    wd = np.zeros_like(water)
    wd[1:, :] |= water[:-1, :]
    wd[:-1, :] |= water[1:, :]
    wd[:, 1:] |= water[:, :-1]
    wd[:, :-1] |= water[:, 1:]

    def coastal(ring):
        for bx, bz in ring:
            j = int((bx - ox) // cell)
            i = int((bz - oz) // cell)
            if 0 <= i < wd.shape[0] and 0 <= j < wd.shape[1] and wd[i, j]:
                return True
        return False

    nodes, ways = [], []
    nid = 0
    wid = 0
    for cls in ORDER:
        if cls == "beach":
            continue
        for p in polys.get(cls, []):
            ring = [(round(x, 1), round(z, 1)) for x, z in p["ring"]]
            real = ("beach" if coastal(ring) else "sand") if cls == "sand" else cls
            pts = ring[:-1] if len(ring) > 1 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9 else ring
            wid -= 1
            refs = []
            for bx, bz in pts:
                lon, lat = g.block_to_lonlat(bx, bz)
                nid -= 1
                refs.append(nid)
                nodes.append((nid, lat, lon))
            refs.append(refs[0])
            ways.append((wid, refs, real))
    return nodes, ways


def fresh_upload(api, kind_label, elems_fn, comment):
    cs = api.create_changeset({
        "comment": comment,
        "created_by": "natural-import 1.0",
        "locale": "zh-CN",
        "source": "Minecraft Dynmap 卫星影像 + OSM 海岸线",
    })
    t0 = time.time()
    res = api.upload(cs, O.build_osmchange(creates=elems_fn(cs)))
    d = O.parse_diff_result(res)
    api.close_changeset(cs)
    print(f"  {kind_label}: cs{cs} +{len(d['node'])} 节点 +{len(d['way'])} 路径（{time.time()-t0:.0f}s）", flush=True)
    return d


def main():
    nodes, ways = build()
    print(f"本地共 {len(nodes)} 节点 / {len(ways)} 路径")

    db = q(f"select id, latitude/1e7, longitude/1e7 from current_nodes where changeset_id={DONE_CS} order by id;")
    done = len(db)
    print(f"cs{DONE_CS} 已传 {done} 个节点")
    err = 0.0
    for k in (0, 1, done // 2, done - 1):
        pid, lat, lon = nodes[k]
        did, dlat, dlon = db[k][0], float(db[k][1]), float(db[k][2])
        err = max(err, abs(lat - dlat), abs(lon - dlon))
        assert abs(lat - dlat) < 1e-6 and abs(lon - dlon) < 1e-6, f"第 {k} 个节点对不上"
    first_id = int(db[0][0])
    print(f"校验通过（最大坐标误差 {err:.9f}），首个 id = {first_id}")
    node_map = {str(nodes[k][0]): first_id + k for k in range(done)}

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)

    todo = nodes[done:]
    nb = 2500
    for i in range(0, len(todo), nb):
        chunk = todo[i:i + nb]
        d = fresh_upload(api, f"节点 {i//nb+1}/{(len(todo)+nb-1)//nb}",
                         lambda cs, chunk=chunk: [
                             f'<node id="{pid}" lat="{lat:.7f}" lon="{lon:.7f}" version="0" changeset="{cs}"/>'
                             for pid, lat, lon in chunk],
                         COMMENT)
        node_map.update(d["node"])

    def ref(pid):
        return node_map.get(str(pid), pid)

    wb = 40
    total = 0
    for i in range(0, len(ways), wb):
        chunk = ways[i:i + wb]

        def elems(cs, chunk=chunk):
            out = []
            for wid_, refs, cls in chunk:
                nds = "".join(f'<nd ref="{ref(r)}"/>' for r in refs)
                tags = "".join(f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>'
                               for k, v in TAGS[cls].items())
                out.append(f'<way id="{wid_}" version="0" changeset="{cs}">{nds}{tags}</way>')
            return out

        d = fresh_upload(api, f"路径 {i//wb+1}/{(len(ways)+wb-1)//wb}", elems, COMMENT)
        total += len(d["way"])
    print(f"完成：补传 {len(todo)} 节点 + {total} 路径")


if __name__ == "__main__":
    main()
