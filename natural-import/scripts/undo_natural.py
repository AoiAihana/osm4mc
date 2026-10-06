"""撤销上一次自然要素导入：删除 changeset 75/76 创建的全部路径与节点。

先删路径再删节点；changeset 若被超时关闭会自动重开一个继续。
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


def q(sql: str):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [tuple(map(int, r.split("|"))) for r in out.split("\n") if r]


class Deleter:
    def __init__(self):
        self.api = O.OsmApi(base="http://localhost:3001", timeout=7200)
        self.cs = None
        self.n = 0

    def ensure_cs(self):
        if self.cs is None:
            self.cs = self.api.create_changeset({
                "comment": "撤销自然要素导入（删除 changeset 75/76 的全部路径与节点）",
                "created_by": "natural-import 1.0",
                "locale": "zh-CN",
            })
            print("changeset", self.cs, flush=True)

    def send(self, items, kind, tag):
        """items: [(id, version)]；kind: 'way' | 'node'"""
        for attempt in (1, 2):
            self.ensure_cs()
            elems = [f'<{kind} id="{i}" version="{v}" changeset="{self.cs}"/>' for i, v in items]
            try:
                self.api.upload(self.cs, O.build_osmchange(deletes=elems))
            except RuntimeError as e:
                if attempt == 1 and ("closed" in str(e) or "Changeset mismatch" in str(e)):
                    print("  changeset 不可用，重开一个继续", flush=True)
                    self.cs = None
                    continue
                raise
            self.n += len(items)
            print(f"  {tag}: 累计 -{self.n}", flush=True)
            return

    def close(self):
        if self.cs is not None:
            self.api.close_changeset(self.cs)
            print("changeset", self.cs, "closed", flush=True)


def main():
    ways = q("select id, version from current_ways where changeset_id in (%s) order by id;"
             % ",".join(map(str, TARGET_CS)))
    nodes = q("select id, version from current_nodes where changeset_id in (%s) order by id;"
              % ",".join(map(str, TARGET_CS)))
    print(f"待删路径 {len(ways)} 条，节点 {len(nodes)} 个")
    d = Deleter()
    for i in range(0, len(ways), 400):
        d.send(ways[i:i + 400], "way", "路径")
    for i in range(0, len(nodes), 2000):
        d.send(nodes[i:i + 2000], "node", "节点")
    d.close()
    left_w = q("select count(*) from current_ways where changeset_id in (%s);" % ",".join(map(str, TARGET_CS)))
    left_n = q("select count(*) from current_nodes where changeset_id in (%s);" % ",".join(map(str, TARGET_CS)))
    print("剩余:", left_w, left_n)


if __name__ == "__main__":
    main()
