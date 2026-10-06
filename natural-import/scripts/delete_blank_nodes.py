"""删除之前那次自然要素导入留下的"空白节点"（无标签、不属于任何路径）。

这些节点来自被超时中断的 changeset 75 / 76（10000 + 2840 个）。
删除用单独的 changeset，分批进行。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))

import osm_api as O   # noqa: E402

TARGET_CS = (75, 76)


def main():
    sql = ("select id, version from current_nodes where changeset_id in (%s) "
           "and not exists (select 1 from current_node_tags t where t.node_id=current_nodes.id) "
           "and not exists (select 1 from current_way_nodes w where w.node_id=current_nodes.id) "
           "order by id;" % ",".join(str(c) for c in TARGET_CS))
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    rows = [tuple(map(int, r.split("|"))) for r in out.split("\n") if r]
    print(f"待删除空白节点 {len(rows)} 个")

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    cs = api.create_changeset({
        "comment": "删除自然要素导入中断后遗留的空白节点（无标签、不属于任何路径）",
        "created_by": "natural-import 1.0",
        "locale": "zh-CN",
    })
    print("changeset", cs, flush=True)
    batch = 2000
    done = 0
    for i in range(0, len(rows), batch):
        chunk = [f'<node id="{nid}" version="{ver}" changeset="{cs}"/>' for nid, ver in rows[i:i + batch]]
        t0 = time.time()
        api.upload(cs, O.build_osmchange(deletes=chunk))
        done += len(chunk)
        print(f"  删除批次 {i//batch+1}: -{len(chunk)}（{time.time()-t0:.0f}s）", flush=True)
    api.close_changeset(cs)
    print(f"changeset {cs} 关闭，共删除 {done} 个空白节点", flush=True)


if __name__ == "__main__":
    main()
