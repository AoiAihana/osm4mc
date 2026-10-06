"""构建舒芙蕾地铁的 OSM 数据（默认只构建不上传）。

用法：
    python3 run_sfrt.py --dry-run          # 生成 osmchange + 预览
    python3 run_sfrt.py --upload           # 构建并上传
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")

import build_sfrt as B          # noqa: E402
import sfrt_network as SN       # noqa: E402
import smooth                   # noqa: E402
import l0imagery as L0           # noqa: E402
from classify import Visibility  # noqa: E402

SHARED_COVER = 0.6      # 与湖区1号线重合超过这个比例 → 3号线不重复画
CONTACT_KEEP = 0.6      # 联络线已存在的比例超过这个 → 跳过
SNAPPED_NODES_FILE = os.path.join(HERE, "snapped_nodes.txt")


# ---------------------------------------------------------------------------
def chain_coverage(chain, others_by_line, tol=1.5):
    """链上有多少比例落在 others_by_line 的折线附近。"""
    segs = [(q, r) for c in others_by_line for q, r in zip(c.coords, c.coords[1:])]
    if not segs:
        return 0.0
    def dmin(p):
        best = 1e9
        for q, r in segs:
            qx, qy = q[0], q[1]
            rx, ry = r[0], r[1]
            dx, dy = rx - qx, ry - qy
            L2 = dx * dx + dy * dy
            if L2 <= 1e-12:
                dd = math.hypot(p[0] - qx, p[1] - qy)
            else:
                t = max(0.0, min(1.0, ((p[0] - qx) * dx + (p[1] - qy) * dy) / L2))
                dd = math.hypot(p[0] - (qx + t * dx), p[1] - (qy + t * dy))
            if dd < best:
                best = dd
        return best
    tot = cov = 0.0
    for q, r in zip(chain.coords, chain.coords[1:]):
        d = math.hypot(r[0] - q[0], r[1] - q[1])
        n = max(2, int(d / 8) + 1)
        L = d / n
        for i in range(n):
            t = (i + 0.5) / n
            p = (q[0] + (r[0] - q[0]) * t, q[1] + (r[1] - q[1]) * t)
            tot += L
            if dmin(p) <= tol:
                cov += L
    return cov / tot if tot else 0.0


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "sfrt.osmchange.xml"))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条链（调试）")
    args = ap.parse_args()

    t0 = time.time()
    data = SN.load()
    lines = SN.load_lines()
    line_names = {l["id"]: SN.base_line_name(l["name"]) for l in lines}
    sf_ids = {l["id"] for l in lines if l["systemId"] == "SFRT"}
    print(f"SFRT 运行线 {len(sf_ids)} 条: {sorted(sf_ids)}")

    chains, nodes, scope = SN.build_chains(data, sf_ids)
    print(f"原始链 {len(chains)} 条 / {sum(c.length for c in chains)/1000:.1f} km")

    # --- 共线区段：3号线雪山庄园以北并入湖区1号线 -----------------------
    # 判据一：链的两个端点都是湖区1号线也用到的图节点（同一组轨道）
    # 判据二：链有 >60% 落在湖区1号线折线 1.5 格以内
    w1 = [c for c in chains if c.line_id in ("SFW1n", "SFW1s")]
    w1_by_dir = defaultdict(list)
    for c in w1:
        w1_by_dir[c.line_id[-1]].append(c)
    w1_nodes = defaultdict(set)
    for c in w1:
        w1_nodes[c.line_id[-1]].update(c.node_ids)
    kept, dropped_shared = [], []
    for c in chains:
        if c.line_id in ("SF3n", "SF3s"):
            d = c.line_id[-1]
            both = all(n in w1_nodes[d] for n in c.node_ids)
            frac = chain_coverage(c, w1_by_dir[d])
            if both or frac > SHARED_COVER:
                dropped_shared.append((c, 1.0 if both else frac))
                continue
        kept.append(c)
    print(f"共线段剔除：{len(dropped_shared)} 条链 "
          f"({sum(c.length for c, _ in dropped_shared)/1000:.2f} km)，"
          f"并入湖区地铁1号线")
    chains = kept

    # --- 联络线：已经在 OSM 里的跳过 -------------------------------------
    import coverage as COV
    osm_ways = COV.load_osm_railways()
    idx = COV.build_index(osm_ways)
    kept, skipped_contact = [], []
    for c in chains:
        if c.line_id == "contact":
            cov, tot, hits = COV.chain_coverage(c, idx)
            frac = cov / tot if tot else 0
            if frac >= CONTACT_KEEP:
                skipped_contact.append((c, frac, hits))
                continue
        kept.append(c)
    print(f"联络线已存在跳过 {len(skipped_contact)} 条 "
          f"({sum(c.length for c, _, _ in skipped_contact)/1000:.2f} km)；"
          f"待画 {sum(1 for c in kept if c.line_id=='contact')} 条")
    chains = kept

    if args.limit:
        chains = chains[: args.limit]

    # --- 既有节点（吸附用） ----------------------------------------------
    existing = B.load_existing_rail_nodes()
    print(f"既有 OSM 铁路节点 {len(existing)} 个")

    V = Visibility()
    # --- 先把所有需要的 L0 瓦片并行抓下来（串行抓每张要 2 秒）------------
    all_chains = list(chains) + [c for c, _, _ in skipped_contact] \
        + [c for c, _ in dropped_shared] + list(w1)
    tiles = set()
    for c in all_chains:
        for q, r in zip(c.coords, c.coords[1:]):
            d = math.hypot(r[0] - q[0], r[1] - q[1])
            n = max(1, int(d / 16))
            for i in range(n + 1):
                t = i / n
                bx = q[0] + (r[0] - q[0]) * t
                bz = q[1] + (r[1] - q[1]) * t
                nx0, ny0 = L0.tile_index(bx - 40, bz - 40)
                nx1, ny1 = L0.tile_index(bx + 40, bz + 40)
                for nx in range(nx0, nx1 + 1):
                    for ny in range(ny0, ny1 + 1):
                        tiles.add((nx, ny))
    print(f"需要 L0 瓦片 {len(tiles)} 张，开始并行预取…", flush=True)
    V.c.prefetch(sorted(tiles))
    print(f"预取完成，缓存统计 {V.c.stats}", flush=True)

    builder = B.Builder(V, existing)
    builder.station_report = []

    chains_sorted = sorted(chains, key=lambda c: (c.line_id, -c.length))
    for i, c in enumerate(chains_sorted):
        disp, rail = B.LINE_SPEC.get(c.line_id, (line_names.get(c.line_id, c.line_id), "subway"))
        before = len(builder.nodes)
        builder.process_chain(c, smooth, disp, rail)
        if i % 20 == 0:
            print(f"  [{i+1}/{len(chains_sorted)}] {c.line_id} {c.length:.0f} m "
                  f"(ways={builder.stats['ways']}, nodes={len(builder.nodes)})", flush=True)

    # --- 车站 -------------------------------------------------------------
    st_feats = [f for f in data["features"]
                if f["properties"].get("type") == "station"
                and any(x in sf_ids for x in (f["properties"].get("lineIds") or []))]
    existing_stations = B.load_existing_stations()
    print(f"既有 OSM 车站 {len(existing_stations)} 座")
    builder.process_stations(st_feats, line_names, sfrt_lines=sf_ids,
                             existing_stations=existing_stations)

    pruned = builder.prune_unused_nodes()
    print(f"清理未使用的空白节点 {pruned} 个")
    print("构建完成：", dict(builder.stats))
    print(f"节点 {len(builder.nodes)} / 路径 {len(builder.ways)}")
    print(f"车站 {len(builder.station_report)} 座")

    # --- 输出 -------------------------------------------------------------
    idmap, node_elems, way_elems = builder.parts(0)
    with open(args.out, "w") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<osmChange version="0.6" '
                 'generator="sfrt-import">\n<create>\n')
        for _, _, xml in node_elems:
            fh.write(xml + "\n")
        for wid, keys, tags in way_elems:
            t = "".join(f'<tag k="{B.xml_escape(k)}" v="{B.xml_escape(v)}"/>'
                        for k, v in sorted(tags.items()))
            nds = "".join(f'<nd ref="{idmap[k]}"/>' for k in keys)
            fh.write(f'<way id="{wid}" version="0" changeset="0">{nds}{t}</way>\n')
        fh.write("</create>\n</osmChange>\n")
    print(f"写出 {args.out}  ({os.path.getsize(args.out)/1e6:.2f} MB)")

    # 元数据存档
    meta = {
        "nodes": len(node_elems),
        "ways": len(way_elems),
        "stations": builder.station_report,
        "stats": dict(builder.stats),
        "dropped_shared_km": round(sum(c.length for c, _ in dropped_shared) / 1000, 3),
        "skipped_contact": [{"owner": c.owner, "len": round(c.length, 1),
                             "nodes": c.node_ids} for c, _, _ in skipped_contact],
        "elapsed": round(time.time() - t0, 1),
    }
    with open(os.path.join(HERE, "build_meta.json"), "w") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    # 吸附报告
    with open(SNAPPED_NODES_FILE, "w") as fh:
        for k in builder.nodes:
            if k.startswith("existing:"):
                fh.write(k + "\n")
    print(f"复用既有节点 {sum(1 for k in builder.nodes if k.startswith('existing:'))} 个")

    if args.upload:
        import upload_sfrt
        upload_sfrt.upload(args.out)
    else:
        print("（未上传；加 --upload 才会写进本地 OSM）")


if __name__ == "__main__":
    main()
