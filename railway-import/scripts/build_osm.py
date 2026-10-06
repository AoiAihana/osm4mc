"""把「平滑后的链 + 可见性判定」变成 OSM 数据（节点 / 路径），并处理：
  * 与既有手绘线路重叠的区段：跳过不导入（用户选择"保留现有、跳过重叠"）
  * 端点吸附到既有节点，保证拓扑连接
  * 车站节点 / 停车点（railway=station / railway=stop）
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict

import geohelper as g
import smooth
from classify import Visibility

HERE = os.path.dirname(os.path.abspath(__file__))

NETWORK = "帕拉伦国家铁路"
OPERATOR = None  # 与既有数据保持一致，只写 network


# ---------------------------------------------------------------------------
# 基础几何工具
# ---------------------------------------------------------------------------
def cumlen(points):
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum


def point_at(points, cum, s):
    """按弧长取点（线性插值）。"""
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


def sub_polyline(points, cum, s0, s1, step_max=200.0):
    """取出 [s0, s1] 的子折线，包含落在区间内的原始顶点，并在两端插值。"""
    out = [point_at(points, cum, s0)]
    for p, c in zip(points, cum):
        if s0 + 1e-9 < c < s1 - 1e-9:
            out.append(p)
    end = point_at(points, cum, s1)
    if math.hypot(end[0] - out[-1][0], end[1] - out[-1][1]) > 1e-9:
        out.append(end)
    # 保证没有超长段
    out2 = [out[0]]
    for a, b in zip(out, out[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d > step_max:
            n = int(math.ceil(d / step_max))
            for k in range(1, n):
                t = k / n
                out2.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out2.append(b)
    return out2


def dist_point_seg(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_polylines(p, polys):
    best = 1e18
    for poly in polys:
        for a, b in zip(poly, poly[1:]):
            d = dist_point_seg(p, a, b)
            if d < best:
                best = d
    return best


# ---------------------------------------------------------------------------
# 既有线路
# ---------------------------------------------------------------------------
def load_existing_rails(path=None):
    """返回 [{way_id, nodes:[(node_id, bx, bz)]}]（方块坐标）。"""
    path = path or os.path.join(HERE, "existing_rail_nodes.txt")
    ways = defaultdict(list)
    with open(path) as fh:
        for line in fh:
            wid, seq, nid, lat, lon = line.strip().split("|")
            bx, bz = g.lonlat_to_block(float(lon), float(lat))
            ways[int(wid)].append((int(seq), int(nid), bx, bz))
    out = []
    for wid, rows in ways.items():
        rows.sort()
        out.append({"way_id": wid, "nodes": [(nid, bx, bz) for _, nid, bx, bz in rows]})
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
class Builder:
    def __init__(self, chains, stations, existing, visibility, *, overlap_dist=2.5, snap_dist=25.0):
        self.chains = chains
        self.stations = stations          # 车站节点（geojson Point）
        self.existing = existing
        self.V = visibility
        self.overlap_polys = [[(n[1], n[2]) for n in w["nodes"]] for w in existing]
        self.existing_nodes = [(n[0], n[1], n[2]) for w in existing for n in w["nodes"]]
        self.overlap_dist = overlap_dist
        self.snap_dist = snap_dist

        self.nodes = {}      # key -> {lat, lon, tags, key}
        self.ways = []       # {keys: [...], tags: {...}}
        self.rm_key = {}     # railwaymap 节点 id -> OSM 节点 key
        self._next_key = 0
        self.stats = defaultdict(int)

    # -- 节点登记 --------------------------------------------------------
    def node_key(self, bx, bz, key=None):
        """按坐标（或显式 key）登记一个节点，返回内部 key。"""
        if key is not None and key in self.nodes:
            return key
        self._next_key += 1
        k = key if key is not None else f"n{self._next_key}"
        lon, lat = g.block_to_lonlat(bx, bz)
        self.nodes[k] = {"lat": lat, "lon": lon, "tags": {}, "bx": bx, "bz": bz}
        return k

    def set_tags(self, key, tags):
        self.nodes[key]["tags"].update(tags)

    # -- 处理一条链 ------------------------------------------------------
    def process_chain(self, chain):
        coords = [tuple(p) for p in chain.coords]
        sm = smooth.smooth_chain(coords)
        pts = [(p[0], p[1]) for p in sm]
        cum = cumlen(pts)
        total = cum[-1]
        if total < 5.0:
            self.stats["skip_short"] += 1
            return
        scores = self.V.score_polyline(sm, step=4.0)
        runs = self.V.classify(scores)
        # s 区间（可见/不可见）
        spans = []
        for a, b, v in runs:
            s0 = scores[a]["s"] if a < len(scores) else 0.0
            s1 = scores[b]["s"] if b < len(scores) else total
            spans.append([max(0.0, s0), min(total, s1), v])
        if spans:
            spans[0][0] = 0.0
            spans[-1][1] = total
        # 与既有线路重叠的部分：切掉
        keep = []
        for s0, s1, v in spans:
            keep.extend(self._subtract_existing(pts, cum, s0, s1, v))
        # 组装
        for s0, s1, v in keep:
            if s1 - s0 < 6.0:
                self.stats["drop_sliver"] += 1
                continue
            sub = sub_polyline(pts, cum, s0, s1)
            if len(sub) < 2:
                continue
            self._emit_way(chain, sm, cum, sub, s0, s1, v, total)

    def _subtract_existing(self, pts, cum, s0, s1, visible):
        """把 [s0,s1] 中与既有线路重叠的区段挖掉。"""
        if not self.overlap_polys:
            return [(s0, s1, visible)]
        # 稠密采样判断重叠
        n = max(2, int((s1 - s0) / 4.0) + 1)
        marks = []
        for i in range(n + 1):
            s = s0 + (s1 - s0) * i / n
            p = point_at(pts, cum, s)
            d = dist_to_polylines(p, self.overlap_polys)
            marks.append((s, d < self.overlap_dist))
        # 合并成区段
        out = []
        cur_start = None
        cur_state = False
        for s, covered in marks:
            if covered and cur_start is None:
                cur_start = s
                cur_state = True
            elif not covered and cur_start is not None:
                out.append((cur_start, s, False))
                cur_start = None
        if cur_start is not None:
            out.append((cur_start, s1, False))
        # 取反：保留非重叠
        keep = []
        prev = s0
        for a, b, _ in out:
            if a > prev:
                keep.append((prev, min(a, s1), visible))
            prev = max(prev, b)
        if prev < s1:
            keep.append((prev, s1, visible))
        removed = sum(b - a for a, b, _ in out)
        if removed > 0:
            self.stats["overlap_removed_blocks"] += removed
        return keep

    def _emit_way(self, chain, sm, cum, sub, s0, s1, visible, total):
        # 起终点尽量复用铁路图节点 / 既有节点
        keys = []
        for i, p in enumerate(sub):
            snap = None
            if i == 0 or i == len(sub) - 1:
                snap = self._snap_key(p)
            if snap is not None:
                keys.append(snap)
            elif i == 0 and abs(s0) < 1e-6 and chain.node_ids[0]:
                k = self.node_key(p[0], p[1], key="rm:" + chain.node_ids[0])
                self.rm_key[chain.node_ids[0]] = k
                keys.append(k)
            elif i == len(sub) - 1 and abs(s1 - total) < 1e-6 and chain.node_ids[1]:
                k = self.node_key(p[0], p[1], key="rm:" + chain.node_ids[1])
                self.rm_key[chain.node_ids[1]] = k
                keys.append(k)
            else:
                keys.append(self.node_key(p[0], p[1]))
        # 去重（相邻重复）
        dedup = [keys[0]]
        for k in keys[1:]:
            if k != dedup[-1]:
                dedup.append(k)
        if len(dedup) < 2:
            return
        tags = {
            "railway": "rail",
            "name": chain.name,
            "network": NETWORK,
            "usage": "branch" if ("支线" in chain.name or chain.name == "联络线") else "main",
        }
        if visible:
            tags["bridge"] = "yes"
            tags["layer"] = "1"
        else:
            tags["tunnel"] = "yes"
            tags["layer"] = "-1"
        self.ways.append({"keys": dedup, "tags": tags, "visible": visible,
                          "chain_edge_ids": chain.edge_ids, "s0": s0, "s1": s1})
        self.stats["ways"] += 1
        if visible:
            self.stats["ways_bridge"] += 1
        else:
            self.stats["ways_tunnel"] += 1

    def _snap_key(self, p):
        """如果端点靠近既有线路的某个节点，就复用它。"""
        best = None
        for nid, bx, bz in self.existing_nodes:
            d = math.hypot(p[0] - bx, p[1] - bz)
            if d < self.snap_dist and (best is None or d < best[0]):
                best = (d, nid)
        if best is None:
            return None
        key = f"existing:{best[1]}"
        if key not in self.nodes:
            nid = best[1]
            for n in self.existing_nodes:
                if n[0] == nid:
                    self.nodes[key] = {"lat": None, "lon": None, "tags": {}, "existing": nid,
                                       "bx": n[1], "bz": n[2]}
                    break
        return key

    # -- 车站 ------------------------------------------------------------
    def process_stations(self, station_nodes):
        """station_nodes: geojson Point feature 列表（type=station）。"""
        groups = defaultdict(list)
        for f in station_nodes:
            p = f["properties"]
            coord = f["geometry"]["coordinates"]
            groups[p["name"]].append((p["id"], coord[0], coord[1]))
        self.station_report = []
        for name, items in sorted(groups.items()):
            keys = []
            vis_flags = []
            for nid, bx, bz in items:
                k = self.rm_key.get(nid)
                if k is None:
                    continue
                keys.append(k)
                for w in self.ways:
                    if k in w["keys"]:
                        vis_flags.append(w["visible"])
            if not keys:
                self.stats["stations_skipped"] += 1
                continue
            xs = [self.nodes[k]["bx"] for k in keys]
            zs = [self.nodes[k]["bz"] for k in keys]
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            visible = any(vis_flags)
            layer = "1" if visible else "-1"
            # 停车点
            for k in keys:
                if k.startswith("existing:"):
                    continue
                self.set_tags(k, {
                    "railway": "stop",
                    "public_transport": "stop_position",
                    "train": "yes",
                    "name": name + "站",
                })
            # 车站本体
            skey = self.node_key(cx, cz)
            self.set_tags(skey, {
                "railway": "station",
                "public_transport": "station",
                "train": "yes",
                "name": name + "站",
                "network": NETWORK,
                "layer": layer,
            })
            self.station_report.append({"name": name, "nodes": len(keys), "layer": layer,
                                        "x": round(cx), "z": round(cz)})
            self.stats["stations"] += 1

    # -- 输出 XML --------------------------------------------------------
    def to_osmchange(self, changeset_id: int, indent: str = "") -> tuple[str, dict]:
        """返回 (osmChange XML, id 映射占位)。占位 id 从 -1 开始。"""
        idmap = {}
        parts = []
        nid = 0
        for key, node in self.nodes.items():
            if "existing" in node:
                idmap[key] = node["existing"]
                continue
            nid -= 1
            idmap[key] = nid
            tags = "".join(
                f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>' for k, v in sorted(node["tags"].items())
            )
            parts.append(
                f'<node id="{nid}" lat="{node["lat"]:.7f}" lon="{node["lon"]:.7f}" version="0" '
                f'changeset="{changeset_id}">{tags}</node>'
            )
        wid = 0
        for w in self.ways:
            wid -= 1
            nds = "".join(f'<nd ref="{idmap[k]}"/>' for k in w["keys"])
            tags = "".join(
                f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>' for k, v in sorted(w["tags"].items())
            )
            parts.append(
                f'<way id="{wid}" version="0" changeset="{changeset_id}">{nds}{tags}</way>'
            )
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<osmChange version="0.6" generator="railway-import">\n<create>\n'
               + "\n".join(parts) + "\n</create>\n</osmChange>")
        return xml, idmap


    def parts(self, changeset_id: int):
        """返回 (idmap, node_elems, way_elems)。
        node_elems: [(key, placeholder_id, xml)]
        way_elems:  [(placeholder_id, keys, tags)]
        """
        idmap = {}
        node_elems = []
        nid = 0
        for key, node in self.nodes.items():
            if "existing" in node:
                idmap[key] = node["existing"]
                continue
            nid -= 1
            idmap[key] = nid
            tags = "".join(
                f'<tag k="{xml_escape(k)}" v="{xml_escape(v)}"/>' for k, v in sorted(node["tags"].items())
            )
            node_elems.append((key, nid,
                f'<node id="{nid}" lat="{node["lat"]:.7f}" lon="{node["lon"]:.7f}" version="0" '
                f'changeset="{changeset_id}">{tags}</node>'))
        way_elems = []
        wid = 0
        for w in self.ways:
            wid -= 1
            way_elems.append((wid, w["keys"], w["tags"]))
        return idmap, node_elems, way_elems

def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))

    # -- 分阶段输出（便于分批上传） ---------------------------------------
