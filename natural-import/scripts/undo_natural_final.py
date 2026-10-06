"""撤销自然要素（natural=*）导入：删除之前画的全部自然面及其节点。

删除范围：
  * 路径：natural 属于 {wood, glacier, scrub, bare_rock, sand, beach, grassland, water}
    且属于本次导入的 way（changeset 101/102/104/107，以及被后续编辑过的 110 里那条 wood）；
  * 节点：changeset 94/95/96 里、删完路径后不再被任何路径引用的节点。
    （海岸线 coastline / cliff 以及你自己画的 water 不在此列，保持不动。）

先删路径再删节点；每批一个独立 changeset，避免被超时关闭。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))

import osm_api as O  # noqa: E402

NATURAL_KEEP = ("coastline", "cliff")          # 你自己画的，别动
SRC_CS = (101, 102, 104, 107, 110)             # 本次导入相关（110 是你编辑过的那条 wood）
NODE_CS = (94, 95, 96)
WAY_BATCH = 40
NODE_BATCH = 2000


def q(sql):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def main():
    dry = "--dry-run" in sys.argv
    ways = q("select w.id, w.version, t2.v from current_ways w "
             "join current_way_tags t on t.way_id=w.id and t.k='natural' "
             "join current_way_tags t2 on t2.way_id=w.id and t2.k='natural' "
             f"where t.v not in ('{NATURAL_KEEP[0]}','{NATURAL_KEEP[1]}') "
             f"and w.changeset_id in ({','.join(map(str, SRC_CS))}) "
             "and w.visible order by w.id;")
    print(f"待删路径 {len(ways)} 条")
    cnt = defaultdict(int)
    for _, _, v in ways:
        cnt[v] += 1
    for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
        print(f"   natural={k:10s} {v} 条")
    if dry:
        return

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    n = 0
    for i in range(0, len(ways), WAY_BATCH):
        chunk = ways[i:i + WAY_BATCH]
        cs = api.create_changeset({"comment": "撤销 natural=* 自然要素导入（删除所绘制的自然面）",
                                   "created_by": "natural-undo 1.0", "locale": "zh-CN"})
        elems = [f'<way id="{w}" version="{v}" changeset="{cs}"/>' for w, v, _ in chunk]
        t0 = time.time()
        api.upload(cs, O.build_osmchange(deletes=elems))
        api.close_changeset(cs)
        n += len(chunk)
        print(f"  路径批次 {i//WAY_BATCH+1}: -{len(chunk)}（cs{cs}，{time.time()-t0:.0f}s）累计 {n}",
              flush=True)

    # 删节点：只删已经不被任何路径引用的
    left = q("select n.id, n.version from current_nodes n "
             f"where n.changeset_id in ({','.join(map(str, NODE_CS))}) and n.visible "
             "and not exists (select 1 from current_way_nodes w where w.node_id=n.id) order by n.id;")
    print(f"待删节点 {len(left)} 个")
    m = 0
    for i in range(0, len(left), NODE_BATCH):
        chunk = left[i:i + NODE_BATCH]
        cs = api.create_changeset({"comment": "撤销 natural=* 导入：删除不再被引用的节点",
                                   "created_by": "natural-undo 1.0", "locale": "zh-CN"})
        elems = [f'<node id="{nid}" version="{ver}" changeset="{cs}"/>' for nid, ver in chunk]
        t0 = time.time()
        api.upload(cs, O.build_osmchange(deletes=elems))
        api.close_changeset(cs)
        m += len(chunk)
        print(f"  节点批次 {i//NODE_BATCH+1}: -{len(chunk)}（cs{cs}，{time.time()-t0:.0f}s）累计 {m}",
              flush=True)
    print(f"完成：删除 {n} 条路径、{m} 个节点")


if __name__ == "__main__":
    main()
