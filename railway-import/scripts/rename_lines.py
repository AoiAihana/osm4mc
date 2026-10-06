"""把铁路线路名里的「xx铁路」统一改成「xx线」。

映射（用户确认）：
    环线铁路→环线，普蒙铁路→普蒙线，普朗铁路→普朗线，赫蒙铁路→赫蒙线，
    芙德铁路→芙德线，蒙德铁路→蒙德线，极地铁路→极地线，群岛铁路→群岛线，
    芙德铁路支线→芙德线支线，普朗铁路支线→普朗线支线；
    「联络线」「结束乐队专线」不含“铁路”，不动。

做法：按 way 逐个 modify（几何不变，只改 name 标签），每批一个独立 changeset。
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

BATCH = 120
COMMENT = "线路命名统一：xx铁路 → xx线（环线铁路→环线、芙德铁路支线→芙德线支线 等）"


def rename(name: str) -> str | None:
    if "铁路" not in name:
        return None
    new = name.replace("铁路支线", "线支线").replace("铁路", "线")
    # 避免出现「环线线」这类重复
    new = new.replace("线线", "线")
    return new if new != name else None


def q(sql: str):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def xml_esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def main():
    ways = q("select w.id, w.version from current_ways w "
             "join current_way_tags t on t.way_id=w.id and t.k='name' "
             "where t.v like '%铁路%' order by w.id;")
    print(f"待改名 way：{len(ways)} 条")
    ids = [r[0] for r in ways]
    tags = defaultdict(list)
    for r in q("select way_id, k, v from current_way_tags where way_id in (%s) order by way_id, k;"
               % ",".join(ids)):
        tags[r[0]].append((r[1], r[2]))
    nodes = defaultdict(list)
    for r in q("select way_id, node_id from current_way_nodes where way_id in (%s) "
               "order by way_id, sequence_id;" % ",".join(ids)):
        nodes[r[0]].append(r[1])

    from collections import Counter
    cnt = Counter(t[1] for tid in ids for t in tags[tid] if t[0] == "name")
    print("改名映射：")
    for old, n in cnt.most_common():
        print(f"  {old} -> {rename(old)}   （{n} 条）")

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    done = 0
    for i in range(0, len(ways), BATCH):
        chunk = ways[i:i + BATCH]
        cs = api.create_changeset({"comment": COMMENT, "created_by": "railway-rename 1.0",
                                   "locale": "zh-CN"})
        elems = []
        for wid, ver in chunk:
            new_tags = [(k, rename(v) if k == "name" else v) for k, v in tags[wid]]
            nds = "".join(f'<nd ref="{r}"/>' for r in nodes[wid])
            tg = "".join(f'<tag k="{xml_esc(k)}" v="{xml_esc(v)}"/>' for k, v in new_tags)
            elems.append(f'<way id="{wid}" version="{ver}" changeset="{cs}">{nds}{tg}</way>')
        t0 = time.time()
        api.upload(cs, O.build_osmchange(modifies=elems))
        api.close_changeset(cs)
        done += len(chunk)
        print(f"  批次 {i//BATCH+1}: {len(chunk)} 条（cs{cs}，{time.time()-t0:.0f}s），累计 {done}",
              flush=True)
    print("全部完成", done)


if __name__ == "__main__":
    main()
