"""检查每条 SFRT 轨道链在本地 OSM 里是否已经存在（用户手工画过 / 之前导入过）。

输出每条链的覆盖率：链上采样点落在某个既有 railway=* way 的 2 格以内的比例。
"""
from __future__ import annotations

import math
import subprocess
import sys
import collections

sys.path.insert(0, "/home/aoiaihana/osm/.work/sfrt")
import sfrt_network as SN  # noqa: E402

ORIGIN_X, ORIGIN_Z = 11168.0, -5568.0
CELL = 32.0


def load_osm_railways():
    """返回 [(osm_id, [(bx,bz), ...], tags_str)]，坐标已换算成方块坐标。"""
    sql = """
    select osm_id, coalesce(name,''), coalesce(railway,''), coalesce(layer::text,''), coalesce(bridge,''), coalesce(tunnel,''),
           ST_AsText(way)
    from planet_osm_line where railway is not null;
    """
    p = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "gis",
                        "-t", "-A", "-F", "\x1f", "-c", sql],
                       cwd="/home/aoiaihana/osm", capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    out = []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) < 7:
            continue
        oid, name, rail, layer, bridge, tunnel, wkt = parts[:7]
        coords = []
        body = wkt.split("(", 1)[1].rsplit(")", 1)[0]
        for pair in body.split(","):
            xs, ys = pair.split()
            mx, my = float(xs), float(ys)
            coords.append((mx + ORIGIN_X, -my + ORIGIN_Z))
        tags = f"{rail}|{name}|layer={layer}|bridge={bridge}|tunnel={tunnel}"
        out.append((int(oid), coords, tags))
    return out


def build_index(ways):
    idx = collections.defaultdict(list)
    for oid, coords, tags in ways:
        for q, r in zip(coords, coords[1:]):
            x0, x1 = sorted((q[0], r[0]))
            y0, y1 = sorted((q[1], r[1]))
            for cx in range(int(x0 // CELL), int(x1 // CELL) + 1):
                for cy in range(int(y0 // CELL), int(y1 // CELL) + 1):
                    idx[(cx, cy)].append((q, r, oid, tags))
    return idx


def dist_pt_seg(p, q, r):
    qx, qy = q
    rx, ry = r
    px, py = p
    dx, dy = rx - qx, ry - qy
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.hypot(px - qx, py - qy)
    t = max(0.0, min(1.0, ((px - qx) * dx + (py - qy) * dy) / L2))
    return math.hypot(px - (qx + t * dx), py - (qy + t * dy))


def nearest(p, idx, tol=2.0):
    cx, cy = int(p[0] // CELL), int(p[1] // CELL)
    best = (1e9, None, None)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for q, r, oid, tags in idx.get((cx + dx, cy + dy), ()):
                d = dist_pt_seg(p, q, r)
                if d < best[0]:
                    best = (d, oid, tags)
    return best if best[0] <= tol else (best[0], None, None)


def chain_coverage(chain, idx, tol=2.0):
    tot = 0.0
    cov = 0.0
    hits = collections.Counter()
    for q, r in zip(chain.coords, chain.coords[1:]):
        d = math.hypot(r[0] - q[0], r[1] - q[1])
        n = max(2, int(d / 8) + 1)
        L = d / n
        for i in range(n):
            t = (i + 0.5) / n
            p = (q[0] + (r[0] - q[0]) * t, q[1] + (r[1] - q[1]) * t)
            tot += L
            dd, oid, tags = nearest(p, idx, tol)
            if oid is not None:
                cov += L
                hits[oid] += L
    return cov, tot, hits


if __name__ == "__main__":
    d = SN.load()
    lines = SN.load_lines()
    sf_ids = {l["id"] for l in lines if l["systemId"] == "SFRT"}
    chains, nodes, scope = SN.build_chains(d, sf_ids)
    ways = load_osm_railways()
    print(f"OSM railway ways: {len(ways)}")
    idx = build_index(ways)
    rows = []
    for c in chains:
        cov, tot, hits = chain_coverage(c, idx)
        rows.append((cov / tot if tot else 0, c, hits))
    rows.sort(key=lambda r: r[0])
    print(f"{'cov':>5} {'line':8s} {'len_m':>8} {'layer':>5}  detail")
    for frac, c, hits in rows:
        if frac > 0.02:
            detail = ", ".join(f"{oid}({int(l)}m)" for oid, l in hits.most_common(3))
        else:
            detail = ""
        print(f"{frac*100:4.0f}% {c.line_id:8s} {c.length:8.1f} {str(c.layer):>5}  {detail}")
