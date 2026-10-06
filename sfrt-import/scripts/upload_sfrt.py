"""把构建好的 SFRT osmChange 上传到本地 OSM（分批 + 一次一个 changeset）。"""
from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")

import osm_api as O  # noqa: E402

API_BASE = "http://localhost:3001"
COMMENT = "绘制舒芙蕾地铁线路与车站（数据来源：帕拉伦铁路线路图）"
NODE_BATCH = 2500
WAY_BATCH = 300


def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def upload(path: str) -> None:
    root = ET.parse(path).getroot()
    nodes, ways = [], []
    for el in root.iter():
        if el.tag == "node":
            nodes.append(el)
        elif el.tag == "way":
            ways.append(el)
    print(f"待上传：节点 {len(nodes)}，路径 {len(ways)}", flush=True)

    api = O.OsmApi(base=API_BASE, timeout=7200)
    cs = api.create_changeset({
        "comment": COMMENT,
        "created_by": "sfrt-import 1.0",
        "locale": "zh-CN",
        "source": "帕拉伦铁路线路图 (railwaymap.big-brother.top)",
        "imagery_used": "Minecraft Dynmap (dyn2xyz)",
    })
    print("changeset", cs, flush=True)

    node_map = {}
    for i in range(0, len(nodes), NODE_BATCH):
        chunk = nodes[i:i + NODE_BATCH]
        elems = []
        for el in chunk:
            tags = "".join(f'<tag k="{xml_escape(t.get("k"))}" v="{xml_escape(t.get("v"))}"/>'
                           for t in el.findall("tag"))
            elems.append(f'<node id="{el.get("id")}" lat="{el.get("lat")}" lon="{el.get("lon")}" '
                         f'version="0" changeset="{cs}">{tags}</node>')
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=elems))
        d = O.parse_diff_result(res)["node"]
        node_map.update(d)
        print(f"  节点批次 {i//NODE_BATCH+1}: +{len(d)}（{time.time()-t0:.0f}s）", flush=True)

    def ref(old):
        old = int(old)
        if old > 0:
            return old
        return node_map.get(str(old), old)

    total = 0
    for i in range(0, len(ways), WAY_BATCH):
        chunk = ways[i:i + WAY_BATCH]
        elems = []
        for el in chunk:
            nds = "".join(f'<nd ref="{ref(nd.get("ref"))}"/>' for nd in el.findall("nd"))
            tags = "".join(f'<tag k="{xml_escape(t.get("k"))}" v="{xml_escape(t.get("v"))}"/>'
                           for t in el.findall("tag"))
            elems.append(f'<way id="{el.get("id")}" version="0" changeset="{cs}">{nds}{tags}</way>')
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(creates=elems))
        d = O.parse_diff_result(res)["way"]
        total += len(d)
        print(f"  路径批次 {i//WAY_BATCH+1}: +{len(d)}（{time.time()-t0:.0f}s）", flush=True)

    api.close_changeset(cs)
    print(f"changeset {cs} 完成：+{len(node_map)} 节点 +{total} 路径", flush=True)


if __name__ == "__main__":
    upload(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sfrt.osmchange.xml"))
