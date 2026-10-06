"""从 railwaymap 的 geojson 里抽出帕拉伦国有铁路（+联络线）的物理轨道链。

概念
----
* 图：节点 = geojson 里的 Point（station / switch），边 = LineString（一段轨道）。
* 边的 properties.owner = 这段轨道属于哪条线路，lineId = 属于哪条运行线
  （联络线的 lineId=contact，owner 则是它物理上属于的那条线）。
* 物理轨道链：把「同一条线、在度为 2 的中间节点上首尾相连」的边串成一条，
  遇到下列情况断开：交叉点（图度≥3）、尽头（度≤1）、车站节点、
  与其他系统（地铁/公交等）相接的节点、或相邻边属于不同的线。
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPE_SYSTEMS = ("paralon-railway", "contact")


def base_line_name(name: str) -> str:
    """「芙德铁路(鹅城方向)」-> 「芙德铁路」"""
    if not name:
        return name
    for sep in ("(", "（"):
        i = name.find(sep)
        if i > 0:
            name = name[:i]
    return name.strip()


@dataclass
class Chain:
    coords: list[tuple[float, float, float]]
    node_ids: list[str]          # [起点节点, 终点节点]
    edge_ids: list[str]
    length: float
    name: str
    system: str
    line_id: str
    owner: str
    layer: int
    color: str = ""

    def to_json(self):
        return {
            "coords": [list(c) for c in self.coords],
            "node_ids": self.node_ids,
            "edge_ids": self.edge_ids,
            "length": self.length,
            "name": self.name,
            "system": self.system,
            "line_id": self.line_id,
            "owner": self.owner,
            "layer": self.layer,
            "color": self.color,
        }


def load(path: str | None = None):
    path = path or os.path.join(HERE, "geojson.json")
    with open(path) as fh:
        return json.load(fh)


def load_line_names(path: str | None = None) -> dict[str, str]:
    path = path or os.path.join(HERE, "lines.json")
    with open(path) as fh:
        lines = json.load(fh)
    return {l["id"]: base_line_name(l["name"]) for l in lines}


def build_chains(data, scope_systems=SCOPE_SYSTEMS):
    """返回 (chains, nodes, scope_edges)"""
    feats = data["features"]
    nodes = {f["properties"]["id"]: f for f in feats if f["geometry"]["type"] == "Point"}
    all_edges = [f for f in feats if f["geometry"]["type"] == "LineString"]
    line_names = load_line_names()

    scope = [
        f
        for f in all_edges
        if f["properties"].get("railwaySystemId") in scope_systems and f["properties"]["length"] > 0
    ]

    deg_all = Counter()
    for f in all_edges:
        p = f["properties"]
        deg_all[p["from"]] += 1
        deg_all[p["to"]] += 1
    foreign_nodes = set()
    for f in all_edges:
        if f["properties"].get("railwaySystemId") not in scope_systems:
            p = f["properties"]
            foreign_nodes.add(p["from"])
            foreign_nodes.add(p["to"])

    adj: dict[str, list] = defaultdict(list)
    for f in scope:
        p = f["properties"]
        adj[p["from"]].append(f)
        adj[p["to"]].append(f)

    def key_of(f):
        p = f["properties"]
        return (p["railwaySystemId"], p["lineId"], p["owner"])

    def is_station(nid):
        n = nodes.get(nid)
        return bool(n) and n["properties"]["type"] == "station"

    def is_split(nid):
        if is_station(nid):
            return True
        if deg_all[nid] != 2:
            return True
        if nid in foreign_nodes:
            return True
        keys = {key_of(f) for f in adj[nid]}
        return len(keys) > 1

    def walk(start_edge, from_end):
        """从 start_edge 的 from/to 端出发，沿度为 2 的节点前进，收集边（按行进方向排列）。"""
        seq = []
        cur = start_edge
        cur_key = key_of(cur)
        # 起点端：行进方向的入口节点
        entry = cur["properties"]["from"] if from_end == "from" else cur["properties"]["to"]
        visited = {cur["properties"]["id"]}
        while True:
            exit_node = cur["properties"]["to"] if entry == cur["properties"]["from"] else cur["properties"]["from"]
            if is_split(exit_node):
                break
            nxt = [e for e in adj[exit_node] if key_of(e) == cur_key and e["properties"]["id"] not in visited]
            if len(nxt) != 1:
                break
            seq.append((cur, entry))
            cur = nxt[0]
            visited.add(cur["properties"]["id"])
            entry = exit_node
        seq.append((cur, entry))
        return seq, visited

    used: set[str] = set()
    chains: list[Chain] = []
    for f in scope:
        fid = f["properties"]["id"]
        if fid in used:
            continue
        fwd, vis_f = walk(f, "from")
        bwd, vis_b = walk(f, "to")
        # fwd: 从 from 走到 to；bwd: 从 to 走到 from（第一段就是 f，重复）
        back = [e for e in bwd if e[0]["properties"]["id"] != fid]
        visited = vis_f | vis_b
        used |= visited
        ordered = list(reversed(back)) + fwd
        coords: list[tuple[float, float, float]] = []
        for e, entry in ordered:
            cs = [tuple(c) for c in e["geometry"]["coordinates"]]
            if entry == e["properties"]["to"]:
                cs = list(reversed(cs))
            for c in cs:
                if coords and abs(coords[-1][0] - c[0]) < 1e-9 and abs(coords[-1][1] - c[1]) < 1e-9:
                    continue
                coords.append(c)
        if len(coords) < 2:
            continue
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(coords, coords[1:]))
        p = f["properties"]
        first_entry = ordered[0][1]
        start_node = first_entry
        last_edge, last_entry = ordered[-1]
        end_node = last_edge["properties"]["to"] if last_entry == last_edge["properties"]["from"] else last_edge["properties"]["from"]
        layers = Counter(e["properties"].get("layer") for e, _ in ordered)
        chains.append(
            Chain(
                coords=coords,
                node_ids=[start_node, end_node],
                edge_ids=sorted(visited),
                length=L,
                name=line_names.get(p["lineId"], p["lineId"]),
                system=p["railwaySystemId"],
                line_id=p["lineId"],
                owner=p["owner"],
                layer=layers.most_common(1)[0][0] if layers else 0,
                color=p.get("color") or "",
            )
        )
    return chains, nodes, scope


if __name__ == "__main__":
    data = load()
    chains, nodes, scope = build_chains(data)
    print(f"scope edges={len(scope)} chains={len(chains)}")
    print("total km", round(sum(c.length for c in chains) / 1000, 1))
    print("by name:")
    for name, n in Counter(c.name for c in chains).most_common():
        km = sum(c.length for c in chains if c.name == name) / 1000
        print(f"  {name:10s} chains={n:4d} {km:8.1f} km")
    print("chain length dist:")
    ls = sorted(c.length for c in chains)
    print("  min", round(ls[0], 1), "median", round(ls[len(ls)//2], 1), "max", round(ls[-1], 1))
    with open(os.path.join(HERE, "chains_raw.json"), "w") as fh:
        json.dump([c.to_json() for c in chains], fh, ensure_ascii=False)
    print("wrote chains_raw.json")
