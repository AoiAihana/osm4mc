"""去掉所有 railway=station / railway=stop 节点名字末尾的「站」字。"""
from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")
import osm_api as O  # noqa: E402

HERE = "/home/aoiaihana/osm/.work/sfrt"
BATCH = 300


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def psql(sql, db="openstreetmap"):
    p = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
                        "-d", db, "-t", "-A", "-F", "\x1f", "-c", sql],
                       cwd="/home/aoiaihana/osm", capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return [ln.split("\x1f") for ln in p.stdout.splitlines() if ln.strip()]


def main(upload=False):
    rows = psql("""
        select n.id, n.version, n.latitude, n.longitude, k.v, r.v
        from current_nodes n
        join current_node_tags t on t.node_id = n.id and t.k = 'name' and t.v like '%站'
        join current_node_tags r on r.node_id = n.id and r.k = 'railway' and r.v in ('station','stop')
        join current_node_tags k on k.node_id = n.id and k.k = 'name'
        where n.visible
        order by n.id;""")
    print(f"待改名节点 {len(rows)} 个")
    targets = []
    for nid, ver, lat, lon, name, rail in rows:
        new = name[:-1]
        if not new:
            print(f"  跳过（改后为空）: {nid} {name}")
            continue
        targets.append((int(nid), ver, float(lat) / 1e7, float(lon) / 1e7, name, new, rail))

    print("样例:", [(t[4], "->", t[5]) for t in targets[:5]])
    if not upload:
        print("（未上传；加 --upload）")
        return

    api = O.OsmApi(base="http://localhost:3001", timeout=3600)
    cs = api.create_changeset({
        "comment": "车站/停车点名称去掉末尾的「站」字",
        "created_by": "sfrt-import 1.0",
        "locale": "zh-CN",
    })
    print("changeset", cs, flush=True)

    done = 0
    for i in range(0, len(targets), BATCH):
        chunk = targets[i:i + BATCH]
        elems = []
        for nid, ver, lat, lon, old, new, rail in chunk:
            tags = psql(f"select k, v from current_node_tags where node_id={nid} order by k;")
            body = []
            for k, v in tags:
                v = new if k == "name" else v
                body.append(f'<tag k="{esc(k)}" v="{esc(v)}"/>')
            elems.append(f'<node id="{nid}" lat="{lat:.7f}" lon="{lon:.7f}" version="{ver}" '
                         f'changeset="{cs}">{"".join(body)}</node>')
        t0 = time.time()
        res = api.upload(cs, O.build_osmchange(modifies=elems))
        n = len(O.parse_diff_result(res)["node"])
        done += n
        print(f"  批次 {i//BATCH+1}: {n} 个（{time.time()-t0:.0f}s）", flush=True)
    api.close_changeset(cs)
    print(f"changeset {cs} 完成：改名 {done} 个")


if __name__ == "__main__":
    main("--upload" in sys.argv)
