"""把 iD 导出的 changes.osc 恢复上传到本地 OSM。

背景：你在 iD 里保存时一直被 409 冲突卡住（我批量改名把 4 条 way 的版本从 1 提到了 2）。
这里把 OSC 里版本号过期、且只是被我改过名的元素**对齐到服务器当前版本**再上传，
并且保留新的线路名（赫蒙线），不会把名字改回「赫蒙铁路」。

用法：
    python3 apply_id_osc.py --osc ~/下载/changes.osc --dry-run
    python3 apply_id_osc.py --osc ~/下载/changes.osc --upload
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "railwaymap"))

import osm_api as O  # noqa: E402


def q(sql: str):
    out = subprocess.run(["docker", "exec", "osm-db-1", "psql", "-U", "openstreetmap",
                          "-d", "openstreetmap", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True).stdout.strip()
    return [r.split("|") for r in out.split("\n") if r]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--osc", default=os.path.expanduser("~/下载/changes.osc"))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--comment", default="恢复 iD 中未能保存的编辑（由 changes.osc 重新上传）")
    args = ap.parse_args()

    tree = ET.parse(args.osc)
    root = tree.getroot()
    stats = {}
    patches = []
    for blk in root:
        for el in blk:
            kind = el.tag
            if kind not in ("node", "way", "relation"):
                continue
            stats[(blk.tag, kind)] = stats.get((blk.tag, kind), 0) + 1
            if blk.tag not in ("modify", "delete"):
                continue
            eid = int(el.get("id"))
            fver = int(el.get("version", "1"))
            table = {"node": "current_nodes", "way": "current_ways", "relation": "current_relations"}[kind]
            row = q(f"select version, visible from {table} where id={eid};")
            if not row:
                patches.append((blk.tag, kind, eid, fver, None, "服务器上不存在"))
                continue
            sver, vis = int(row[0][0]), row[0][1]
            if sver != fver:
                el.set("version", str(sver))
                patches.append((blk.tag, kind, eid, fver, sver, "版本对齐"))
                # 我改过名的铁路线：保留服务器上的新名字（赫蒙线），别改回赫蒙铁路
                if kind == "way":
                    cur_name = q(f"select v from current_way_tags where way_id={eid} and k='name';")
                    cur_name = cur_name[0][0] if cur_name else None
                    for tag in el.findall("tag"):
                        if tag.get("k") == "name" and cur_name and tag.get("v") != cur_name:
                            print(f"    名字保留服务器版本：way {eid} {tag.get('v')} -> {cur_name}")
                            tag.set("v", cur_name)
    print("OSC 内容：", {f"{a} {b}": n for (a, b), n in sorted(stats.items())})
    print("需要对齐的元素：")
    for a, k, eid, fv, sv, why in patches:
        print(f"  {a} {k} {eid}: 文件 v{fv} -> 服务器 v{sv}（{why}）")

    if not args.upload:
        print("（dry-run，未上传）")
        return

    api = O.OsmApi(base="http://localhost:3001", timeout=7200)
    cs = api.create_changeset({
        "comment": args.comment,
        "created_by": "iD 2.40.0（经 dsh 代为上传）",
        "locale": "zh-CN",
    })
    print("changeset", cs, flush=True)
    # 每个元素都要带 changeset 属性，否则 API 拒绝
    for blk in root:
        for el in blk:
            if el.tag in ("node", "way", "relation"):
                el.set("changeset", str(cs))
    xml = ET.tostring(root, encoding="utf-8")
    t0 = time.time()
    res = api.upload(cs, xml.decode("utf-8"))
    d = O.parse_diff_result(res)
    print(f"上传成功：+{len(d['node'])} 节点 +{len(d['way'])} 路径 +{len(d['relation'])} 关系"
          f"（{time.time()-t0:.0f}s）")
    api.close_changeset(cs)
    print("changeset", cs, "已关闭")


if __name__ == "__main__":
    main()
