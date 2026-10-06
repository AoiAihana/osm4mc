"""补渲染：有些自然要素 way 在渲染库里缺失。

原因：changeset 94（前 10000 个节点）因为超时直到很晚才关闭，同步服务在处理
cs 98 时那些节点还没进渲染库，osm2pgsql 建不出几何就把这些 way 丢掉了。
现在节点齐了，把这些 way 重新"modify"一次（内容不变）就能补上。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))

import osm_api as O  # noqa: E402

SRC_CS = (98, 101, 102, 103)


def q(db, user, sql):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", user, "-d", db,
                          "-At", "-F", "|", "-c", sql], capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def main():
    api_ways = {int(r[0]): r for r in q("openstreetmap", "openstreetmap",
        "select w.id, w.version from current_ways w where w.changeset_id in (%s);"
        % ",".join(map(str, SRC_CS)))}
    gis_ids = {int(r[0]) for r in q("gis", "postgres",
        "select osm_id from planet_osm_polygon where osm_id > 4400;")}
    missing = sorted(set(api_ways) - gis_ids)
    print(f"API {len(api_ways)} 条，渲染库有 {len(gis_ids & set(api_ways))} 条，缺 {len(missing)} 条")

    ways = []
    for wid in missing:
        refs = [r[0] for r in q("openstreetmap", "openstreetmap",
                f"select node_id from current_way_nodes where way_id={wid} order by sequence_id;")]
        tags = q("openstreetmap", "openstreetmap",
                 f"select k, v from current_way_tags where way_id={wid};")
        ways.append((wid, int(api_ways[wid][1]), refs, tags))

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    cs = api.create_changeset({
        "comment": "补齐自然要素的渲染（几何未变，仅重新提交以重建渲染库几何）",
        "created_by": "natural-import 1.0", "locale": "zh-CN",
    })
    print("changeset", cs, flush=True)

    def xml_esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    batch = 20
    for i in range(0, len(ways), batch):
        chunk = []
        for wid, ver, refs, tags in ways[i:i + batch]:
            nds = "".join(f'<nd ref="{r}"/>' for r in refs)
            tg = "".join(f'<tag k="{xml_esc(k)}" v="{xml_esc(v)}"/>' for k, v in tags)
            chunk.append(f'<way id="{wid}" version="{ver}" changeset="{cs}">{nds}{tg}</way>')
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(modifies=chunk))
        d = O.parse_diff_result(res)
        print(f"  批次 {i//batch+1}: ~{len(chunk)} 条（{time.time()-t0:.0f}s）", flush=True)
    api.close_changeset(cs)
    print("changeset", cs, "closed", flush=True)


if __name__ == "__main__":
    main()
