"""把分类得到的自然要素多边形上传到本地 OSM。

* 输入：polygons_final.json（由 polygons.mask_to_polygons 生成）
* 标签：natural=water/lake、glacier、wood、grassland、scrub、sand/beach、bare_rock
* 上传：直连 Rails（3001），先节点后路径，分批。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))

import geohelper as g          # noqa: E402
import osm_api as O            # noqa: E402
from polygons import components, mask_to_polygons   # noqa: E402

TAGS = {
    "water": {"natural": "water", "water": "lake"},
    "glacier": {"natural": "glacier"},
    "wood": {"natural": "wood"},
    "grassland": {"natural": "grassland"},
    "scrub": {"natural": "scrub"},
    "sand": {"natural": "sand"},
    "beach": {"natural": "beach"},
    "bare_rock": {"natural": "bare_rock"},
}
ORDER = ["glacier", "wood", "scrub", "bare_rock", "sand", "beach", "grassland", "water"]


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--polys", default=os.path.join(HERE, "polygons_final.json"))
    ap.add_argument("--grid", default=os.path.join(HERE, "grid32.npz"))
    ap.add_argument("--labels", default=os.path.join(HERE, "labels_v3.npy"))
    ap.add_argument("--out", default=os.path.join(HERE, "natural.osmchange.xml"))
    ap.add_argument("--api-base", default="http://localhost:3001")
    ap.add_argument("--comment", default="按 Minecraft 卫星影像粗略绘制自然地貌（森林/雪原/沙地/湖泊/岩地等）")
    ap.add_argument("--node-batch", type=int, default=2500)
    ap.add_argument("--way-batch", type=int, default=200)
    ap.add_argument("--upload", action="store_true")
    args = ap.parse_args()

    polys = json.load(open(args.polys))
    d = np.load(args.grid)
    lab = np.load(args.labels)
    ox, oz = d["origin"]
    cell = float(d["cell"])

    # 靠水的沙地 -> natural=beach（海滩/湖滩）；内陆沙漠状沙地 -> natural=sand
    water = (lab == 1)
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

    items = []          # (class, ring, area)
    for cls in ORDER:
        if cls == "beach":      # 沙地只处理一次，靠水与否在下面决定 beach / sand
            continue
        for p in polys.get(cls, []):
            ring = [(round(x, 1), round(z, 1)) for x, z in p["ring"]]
            real = ("beach" if coastal(ring) else "sand") if cls == "sand" else cls
            items.append((real, ring, p["area"]))

    # --- 生成 XML ---
    node_id = 0
    node_xml = []
    way_parts = []
    way_id = 0
    for cls, ring, area in items:
        way_id -= 1
        # 闭合环：最后一点与第一点坐标相同 —— 复用第一个节点，path 才是 closed way
        pts = ring[:-1] if len(ring) > 1 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9 else ring
        refs = []
        for bx, bz in pts:
            lon, lat = g.block_to_lonlat(bx, bz)
            node_id -= 1
            refs.append(node_id)
            node_xml.append(f'<node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="0" changeset="0"/>')
        refs.append(refs[0])          # 闭合
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        tags = "".join(f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>' for k, v in TAGS[cls].items())
        way_parts.append(f'<way id="{way_id}" version="0" changeset="0">{nds}{tags}</way>')
        if len(set(refs)) != len(refs) - 1:
            pass
    print(f"多边形 {len(items)} 个，节点 {len(node_xml)} 个")
    from collections import Counter
    print("  分类统计:", Counter(c for c, _, _ in items))
    area_by = {}
    for c, _, a in items:
        area_by[c] = area_by.get(c, 0) + a
    for c, a in sorted(area_by.items(), key=lambda kv: -kv[1]):
        print(f"  {c:10s} {a/1e6:7.2f} km²")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<osmChange version="0.6" generator="natural-import">\n<create>\n'
           + "\n".join(node_xml + way_parts) + "\n</create>\n</osmChange>")
    with open(args.out, "w") as fh:
        fh.write(xml)
    print("written", args.out, f"{len(xml)/1e6:.2f} MB")

    if not args.upload:
        return
    api = O.OsmApi(base=args.api_base, timeout=7200)
    cs = api.create_changeset({
        "comment": args.comment,
        "created_by": "natural-import 1.0",
        "locale": "zh-CN",
        "source": "Minecraft Dynmap 卫星影像（map.bilicraft.com，Paralon flat 图层）",
        "imagery_used": "Minecraft Dynmap (flat)",
    })
    print("changeset", cs, flush=True)

    def replace_cs(elem: str) -> str:
        return elem.replace('changeset="0"', f'changeset="{cs}"')

    # 阶段一：节点
    node_map = {}
    for i in range(0, len(node_xml), args.node_batch):
        chunk = [replace_cs(e) for e in node_xml[i:i + args.node_batch]]
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=chunk))
        d2 = O.parse_diff_result(res)["node"]
        node_map.update(d2)
        print(f"  节点批次 {i//args.node_batch+1}: +{len(d2)}（{time.time()-t0:.0f}s）", flush=True)

    def ref(pid: int) -> int:
        return node_map.get(str(pid), pid)

    # 阶段二：路径（用真实 id 重写引用）
    total = 0
    for i in range(0, len(way_parts), args.way_batch):
        chunk = []
        for e in way_parts[i:i + args.way_batch]:
            e2 = replace_cs(e)
            import re
            e2 = re.sub(r'<nd ref="(-\d+)"/>', lambda m: f'<nd ref="{ref(int(m.group(1)))}"/>', e2)
            chunk.append(e2)
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=chunk))
        d2 = O.parse_diff_result(res)["way"]
        total += len(d2)
        print(f"  路径批次 {i//args.way_batch+1}: +{len(d2)}（{time.time()-t0:.0f}s）", flush=True)
    api.close_changeset(cs)
    print(f"changeset {cs} 关闭，+{len(node_map)} 节点 +{total} 路径", flush=True)


if __name__ == "__main__":
    main()
