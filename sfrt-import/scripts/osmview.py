"""查看本地 OSM 里某个方块坐标附近的要素（含节点坐标、标签、版本）。"""
from __future__ import annotations

import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")
import geohelper as g  # noqa: E402

DB = "openstreetmap"


def psql(sql: str, db: str = DB):
    p = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", db, "-t", "-A", "-F", "\x1f", "-c", sql],
        cwd="/home/aoiaihana/osm", capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    rows = []
    for line in p.stdout.splitlines():
        if line.strip():
            rows.append(line.split("\x1f"))
    return rows


def ways_near(bx: float, bz: float, radius: float = 200.0, only_railway: bool = True):
    """返回 [(way_id, version, changeset, tags{}, [(nid, nbx, nbz)])]。"""
    merc_x = bx - g.ORIGIN_BLOCK_X
    merc_y = -(bz - g.ORIGIN_BLOCK_Z)
    r = radius
    filt = "and exists (select 1 from current_way_tags t where t.way_id=w.id and t.k='railway')" if only_railway else ""
    sql = f"""
    select w.id, w.version, w.changeset_id,
      coalesce((select string_agg(t.k||'='||t.v, ' ' order by t.k) from current_way_tags t where t.way_id=w.id),''),
      n.id, n.latitude, n.longitude, wn.sequence_id
    from current_ways w
    join current_way_nodes wn on wn.way_id=w.id
    join current_nodes n on n.id=wn.node_id
    where w.visible and n.tile in (
        select tile from current_nodes nn where nn.visible and
          (nn.longitude*{g.R}*3.141592653589793/180 - ({merc_x}))^2 < 1
    ) is not null
    {filt}
    order by w.id, wn.sequence_id
    """
    # 简化：直接用经纬度包围盒筛选节点，再回溯 way
    dlat = radius / 111320.0
    lon0, lat0 = g.block_to_lonlat(bx, bz)
    la0, la1 = (lat0 - dlat) * 1e7, (lat0 + dlat) * 1e7
    lo0, lo1 = (lon0 - dlat) * 1e7, (lon0 + dlat) * 1e7
    sql = f"""
    with nb as (select id from current_nodes where visible and latitude between {la0} and {la1}
                 and longitude between {lo0} and {lo1}),
    wb as (select distinct way_id from current_way_nodes where node_id in (select id from nb))
    select w.id, w.version, w.changeset_id,
      coalesce((select string_agg(t.k||'='||t.v, ' ' order by t.k) from current_way_tags t where t.way_id=w.id),''),
      n.id, n.latitude, n.longitude, wn.sequence_id
    from current_ways w
    join current_way_nodes wn on wn.way_id=w.id
    join current_nodes n on n.id=wn.node_id
    where w.id in (select way_id from wb) and w.visible {filt}
    order by w.id, wn.sequence_id
    """
    rows = psql(sql)
    ways: dict[int, dict] = {}
    for r in rows:
        wid, ver, cs, tags, nid, lat, lon, seq = int(r[0]), int(r[1]), int(r[2]), r[3], int(r[4]), float(r[5]), float(r[6]), int(r[7])
        w = ways.setdefault(wid, {"id": wid, "version": ver, "changeset": cs,
                                  "tags": dict(kv.split("=", 1) for kv in tags.split() if "=" in kv), "nodes": []})
        nbx, nbz = g.lonlat_to_block(lon / 1e7, lat / 1e7)
        w["nodes"].append((nid, round(nbx, 1), round(nbz, 1), seq))
    for w in ways.values():
        w["nodes"].sort(key=lambda t: t[3])
    return ways


def fmt(ways, show_nodes=True, maxw=40):
    out = []
    for wid, w in sorted(ways.items()):
        t = w["tags"]
        head = (f"way {wid} v{w['version']} cs{w['changeset']} "
                f"{t.get('railway','?')}/{t.get('name','-')} layer={t.get('layer','-')} "
                f"bridge={t.get('bridge','-')} tunnel={t.get('tunnel','-')} usage={t.get('usage','-')}")
        out.append(head)
        if show_nodes:
            ns = " ".join(f"{nid}@({bx},{bz})" for nid, bx, bz, _ in w["nodes"])
            out.append("    " + ns)
    return "\n".join(out[:maxw])


if __name__ == "__main__":
    bx, bz = float(sys.argv[1]), float(sys.argv[2])
    rad = float(sys.argv[3]) if len(sys.argv) > 3 else 200.0
    print(fmt(ways_near(bx, bz, rad)))
