"""把舒芙蕾地铁（SFRT）的平滑轨道链 + 可见性判定变成 OSM 数据。

规则（用户确认）
* 数字线（1/2/3/4/5 号线、湖区 1 号线）→ railway=subway；世界树线、西海岸线 → railway=light_rail
* 3 号线雪山庄园以北与湖区 1 号线共线：只画一次（湖区地铁1号线），不写 3 号线信息
* 高架/地下：影像可见 → bridge=yes；不可见 → tunnel=yes
* 层数：用数据里的 layer 换算 osm_layer = max(1, src_layer - 1)（这样丰饶台地北
  国铁3 / 5号线2 / 3号线1，与用户已画好的联络线一致）；地下统一 layer=-1
* 端点吸附到既有 OSM 铁路节点（保证与用户已画的联络线拓扑相连）
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from collections import defaultdict

import geohelper as g

HERE = os.path.dirname(os.path.abspath(__file__))

NETWORK = "舒芙蕾地铁"
SNAP_DIST = 3.0
MIN_WAY_LEN = 5.0

# line id -> (显示名, railway 值, 线种)
LINE_SPEC = {
    "SF1e": ("舒芙蕾地铁1号线", "subway"),
    "SF1w": ("舒芙蕾地铁1号线", "subway"),
    "SF2n": ("舒芙蕾地铁2号线", "subway"),
    "SF2s": ("舒芙蕾地铁2号线", "subway"),
    "SF3n": ("舒芙蕾地铁3号线", "subway"),
    "SF3s": ("舒芙蕾地铁3号线", "subway"),
    "SF4n": ("舒芙蕾地铁4号线", "subway"),
    "SF4s": ("舒芙蕾地铁4号线", "subway"),
    "SF5e": ("舒芙蕾地铁5号线", "subway"),
    "SF5w": ("舒芙蕾地铁5号线", "subway"),
    "SFW1n": ("湖区地铁1号线", "subway"),
    "SFW1s": ("湖区地铁1号线", "subway"),
    "SFWCn": ("西海岸线", "light_rail"),
    "SFWCs": ("西海岸线", "light_rail"),
    "SFwte": ("世界树线", "light_rail"),
    "SFwtw": ("世界树线", "light_rail"),
    "contact": ("联络线", "subway"),
}


def xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def usage_of(name: str) -> str:
    return "branch" if name == "联络线" else "main"


def osm_layer(src_layer, visible: bool):
    """返回 (layer_str, structure) —— structure ∈ {'bridge','tunnel'}。"""
    if not visible:
        return "-1", "tunnel"
    L = src_layer if isinstance(src_layer, int) and 0 <= src_layer <= 10 else 0
    return str(max(1, L - 1)), "bridge"


# ---------------------------------------------------------------------------
# 既有 OSM 数据
# ---------------------------------------------------------------------------
def _psql(sql: str, db: str = "openstreetmap"):
    p = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
                        "-d", db, "-t", "-A", "-F", "\x1f", "-c", sql],
                       cwd="/home/aoiaihana/osm", capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return [ln.split("\x1f") for ln in p.stdout.splitlines() if ln.strip()]


def load_existing_stations():
    """[(name, bx, bz)] —— 本地已有 railway=station 的节点。"""
    rows = _psql("""
        select t.v, n.latitude, n.longitude
        from current_nodes n
        join current_node_tags t on t.node_id = n.id and t.k = 'name'
        join current_node_tags r on r.node_id = n.id and r.k = 'railway' and r.v = 'station'
        where n.visible;""")
    out = []
    for r in rows:
        bx, bz = g.lonlat_to_block(float(r[2]) / 1e7, float(r[1]) / 1e7)
        out.append((r[0], bx, bz))
    return out


def load_existing_rail_nodes():
    """[(node_id, bx, bz)]，所有挂在 railway=* way 上的节点。"""
    rows = _psql("""
        select distinct n.id, n.latitude, n.longitude
        from current_nodes n
        join current_way_nodes wn on wn.node_id = n.id
        join current_way_tags t on t.way_id = wn.way_id and t.k = 'railway'
        where n.visible;""")
    out = []
    for r in rows:
        bx, bz = g.lonlat_to_block(float(r[2]) / 1e7, float(r[1]) / 1e7)
        out.append((int(r[0]), bx, bz))
    return out


# ---------------------------------------------------------------------------
# 主构建器
# ---------------------------------------------------------------------------
class Builder:
    def __init__(self, visibility, existing_nodes, *, snap_dist=SNAP_DIST):
        self.V = visibility
        self.existing_nodes = existing_nodes
        self._grid = defaultdict(list)
        for nid, bx, bz in existing_nodes:
            self._grid[(int(bx // 16), int(bz // 16))].append((nid, bx, bz))
        self.snap_dist = snap_dist

        self.nodes = {}      # key -> dict
        self.ways = []
        self._next = 0
        self._rm_key = {}
        self.stats = defaultdict(int)

    # -- 节点 ------------------------------------------------------------
    def node_key(self, bx, bz, key=None):
        if key is not None and key in self.nodes:
            return key
        self._next += 1
        k = key or f"n{self._next}"
        lon, lat = g.block_to_lonlat(bx, bz)
        self.nodes[k] = {"lat": lat, "lon": lon, "bx": bx, "bz": bz, "tags": {}}
        return k

    def set_tags(self, key, tags):
        self.nodes[key]["tags"].update(tags)

    def snap_key(self, bx, bz):
        cx, cy = int(bx // 16), int(bz // 16)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid, nx, nz in self._grid.get((cx + dx, cy + dy), ()):
                    d = math.hypot(bx - nx, bz - nz)
                    if d <= self.snap_dist and (best is None or d < best[0]):
                        best = (d, nid, nx, nz)
        if best is None:
            return None
        key = f"existing:{best[1]}"
        if key not in self.nodes:
            self.nodes[key] = {"lat": None, "lon": None, "bx": best[2], "bz": best[3],
                               "tags": {}, "existing": best[1]}
        return key

    # -- 链 --------------------------------------------------------------
    def process_chain(self, chain, smooth, line_name, railway, *, skip_nodes=()):
        coords = [tuple(p) for p in chain.coords]
        sm = smooth.smooth_chain(coords)
        pts = [(p[0], p[1]) for p in sm]
        cum = _cumlen(pts)
        total = cum[-1]
        if total < 3.0:
            self.stats["skip_short"] += 1
            return
        scores = self.V.score_polyline(sm, step=4.0)
        runs = self.V.classify(scores)
        spans = []
        for a, b, v in runs:
            s0 = scores[a]["s"] if a < len(scores) else 0.0
            s1 = scores[b]["s"] if b < len(scores) else total
            spans.append([max(0.0, s0), min(total, s1), v])
        if spans:
            spans[0][0] = 0.0
            spans[-1][1] = total

        # 整条链的顶点各建一个节点 key（只建一次，供各 span 复用）
        vkeys = []
        for i, p in enumerate(pts):
            k = None
            if i == 0 or i == len(pts) - 1:
                k = self.snap_key(p[0], p[1])
                if k is None:
                    nid = chain.node_ids[0] if i == 0 else chain.node_ids[1]
                    if nid:
                        k = self.node_key(p[0], p[1], key="rm:" + nid)
                        self._rm_key[nid] = k
            if k is None:
                k = self.node_key(p[0], p[1])
            vkeys.append(k)

        # span 之间的切点：相邻 span 复用同一个节点
        cid = chain.edge_ids[0] if chain.edge_ids else str(id(chain))
        bounds = []
        n = len(spans)
        for j in range(n + 1):
            s = spans[j][0] if j < n else total
            if s <= 1e-9:
                bounds.append(vkeys[0])
            elif s >= total - 1e-9:
                bounds.append(vkeys[-1])
            else:
                p = _point_at(pts, cum, s)
                bounds.append(self.node_key(p[0], p[1], key=f"cut:{cid}:{j}"))

        for j, (s0, s1, visible) in enumerate(spans):
            if s1 - s0 < MIN_WAY_LEN:
                self.stats["drop_sliver"] += 1
                continue
            keys = [bounds[j]]
            for i, c in enumerate(cum):
                if s0 + 1e-9 < c < s1 - 1e-9:
                    keys.append(vkeys[i])
            keys.append(bounds[j + 1])
            # 过长的段打断（正常不会发生，兜底）
            out = [keys[0]]
            for a, b in zip(keys, keys[1:]):
                pa = (self.nodes[a]["bx"], self.nodes[a]["bz"])
                pb = (self.nodes[b]["bx"], self.nodes[b]["bz"])
                d = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
                if d > 200.0:
                    k = int(math.ceil(d / 200.0))
                    for t in range(1, k):
                        r = t / k
                        out.append(self.node_key(pa[0] + (pb[0] - pa[0]) * r,
                                                 pa[1] + (pb[1] - pa[1]) * r))
                out.append(b)
            dedup = [out[0]]
            for k in out[1:]:
                if k != dedup[-1]:
                    dedup.append(k)
            if len(dedup) < 2:
                continue
            layer, structure = osm_layer(chain.layer, visible)
            tags = {
                "railway": railway,
                "name": line_name,
                "network": NETWORK,
                "layer": layer,
                structure: "yes",
            }
            self.ways.append({"keys": dedup, "tags": tags})
            self.stats["ways"] += 1
            self.stats["ways_bridge" if visible else "ways_tunnel"] += 1
            self.stats[f"layer_{layer}"] += 1

    # -- 车站 ------------------------------------------------------------
    def process_stations(self, station_features, line_names, sfrt_lines=frozenset(),
                         existing_stations=(), near_dup=10.0):
        groups = defaultdict(list)
        for f in station_features:
            p = f["properties"]
            coord = f["geometry"]["coordinates"]
            groups[p["name"]].append((p["id"], coord[0], coord[1], tuple(p.get("lineIds") or [])))
        self.station_report = []
        for name, items in sorted(groups.items()):
            keys = []
            sf_lids = [l for _, _, _, lids in items for l in (lids or []) if not sfrt_lines or l in sfrt_lines]
            is_light = bool(sf_lids) and all(line_names.get(l, "") in ("西海岸线", "世界树线") for l in sf_lids)
            is_subway = not is_light
            for nid, bx, bz, lids in items:
                k = self._rm_key.get(nid)
                if k is not None:
                    keys.append(k)
            if not keys:
                self.stats["stations_skipped"] += 1
                continue
            xs = [self.nodes[k]["bx"] for k in keys]
            zs = [self.nodes[k]["bz"] for k in keys]
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            layers = [self._way_layer_of(k) for k in keys]
            layers = [int(x) for x in layers if x is not None]
            layer = max(layers) if layers else 1
            stop_tags = {"railway": "stop", "public_transport": "stop_position",
                         "name": name + "站", "network": NETWORK}
            for k in keys:
                if k.startswith("existing:"):
                    continue
                self.set_tags(k, stop_tags)
            dup = min((math.hypot(cx - ex, cz - ez) for nm, ex, ez in existing_stations
                       if nm == name + "站"), default=1e9)
            if dup <= near_dup:
                # 同名的国铁车站就在旁边（甚至同一点），不再建第二个车站节点
                self.stats["stations_merged"] += 1
                self.station_report.append({"name": name, "nodes": len(keys), "layer": layer,
                                            "x": round(cx), "z": round(cz), "station": "merged",
                                            "merged_into_dist": round(dup, 1)})
                continue
            skey = self.node_key(cx, cz)
            st_tags = {"railway": "station", "public_transport": "station",
                       "name": name + "站", "network": NETWORK,
                       "station": "subway" if is_subway else "light_rail",
                       "layer": str(layer)}
            self.set_tags(skey, st_tags)
            self.station_report.append({"name": name, "nodes": len(keys), "layer": layer,
                                        "x": round(cx), "z": round(cz),
                                        "station": st_tags["station"]})
            self.stats["stations"] += 1

    def _way_layer_of(self, node_key):
        for w in self.ways:
            if node_key in w["keys"]:
                return w["tags"].get("layer")
        return None

    # -- 输出 ------------------------------------------------------------
    def prune_unused_nodes(self):
        """删掉既没有标签、也没有被任何路径引用的节点（避免空白孤立点）。"""
        used = set()
        for w in self.ways:
            used.update(w["keys"])
        drop = [k for k, n in self.nodes.items()
                if k not in used and not n["tags"] and "existing" not in n]
        for k in drop:
            del self.nodes[k]
        self.stats["pruned_nodes"] = len(drop)
        return len(drop)

    def parts(self, changeset_id: int):
        idmap = {}
        node_elems = []
        nid = 0
        for key, node in self.nodes.items():
            if "existing" in node:
                idmap[key] = node["existing"]
                continue
            nid -= 1
            idmap[key] = nid
            tags = "".join(f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>'
                           for k, v in sorted(node["tags"].items()))
            node_elems.append((key, nid,
                f'<node id="{nid}" lat="{node["lat"]:.7f}" lon="{node["lon"]:.7f}" '
                f'version="0" changeset="{changeset_id}">{tags}</node>'))
        way_elems = []
        wid = 0
        for w in self.ways:
            wid -= 1
            way_elems.append((wid, w["keys"], w["tags"]))
        return idmap, node_elems, way_elems


# ---------------------------------------------------------------------------
def _cumlen(points):
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum


def _point_at(points, cum, s):
    if s <= 0:
        return points[0]
    if s >= cum[-1]:
        return points[-1]
    lo, hi = 0, len(cum) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[lo + 1] - cum[lo]
    t = 0.0 if seg <= 1e-12 else (s - cum[lo]) / seg
    a, b = points[lo], points[lo + 1]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _sub_polyline(points, cum, s0, s1, step_max=200.0):
    out = [_point_at(points, cum, s0)]
    for p, c in zip(points, cum):
        if s0 + 1e-9 < c < s1 - 1e-9:
            out.append(p)
    end = _point_at(points, cum, s1)
    if math.hypot(end[0] - out[-1][0], end[1] - out[-1][1]) > 1e-9:
        out.append(end)
    out2 = [out[0]]
    for a, b in zip(out, out[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d > step_max:
            k = int(math.ceil(d / step_max))
            for i in range(1, k):
                t = i / k
                out2.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out2.append(b)
    return out2
