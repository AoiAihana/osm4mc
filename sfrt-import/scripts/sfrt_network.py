"""从 railwaymap 的 geojson 里抽出舒芙蕾地铁（SFRT）系统的物理轨道链。

与国有铁路那次（network.py）的区别：
* scope 系统是 SFRT + contact，但 contact 只取 owner 属于 SFRT 线路的那部分。
* 保留每条边的 layer（用于判断高架层数）。
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))


def base_line_name(name: str) -> str:
    if not name:
        return name
    for sep in ("(", "（"):
        i = name.find(sep)
        if i > 0:
            name = name[:i]
    return name.strip()


@dataclass
class Chain:
    coords: list
    node_ids: list
    edge_ids: list
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
    with open(path or os.path.join(HERE, "geojson.json")) as fh:
        return json.load(fh)


def load_line_names(path: str | None = None) -> dict:
    with open(path or os.path.join(HERE, "lines.json")) as fh:
        lines = json.load(fh)
    return {l["id"]: base_line_name(l["name"]) for l in lines}


def load_lines(path: str | None = None) -> list:
    with open(path or os.path.join(HERE, "lines.json")) as fh:
        return json.load(fh)


def build_chains(data, sf_line_ids: set[str], *, contact_owner_prefix: str = "SF"):
    feats = data["features"]
    nodes = {f["properties"]["id"]: f for f in feats if f["geometry"]["type"] == "Point"}
    all_edges = [f for f in feats if f["geometry"]["type"] == "LineString"]
    line_names = load_line_names()

    def in_scope(f):
        p = f["properties"]
        if p.get("length", 0) <= 0:
            return False
        if p.get("railwaySystemId") == "SFRT" and p.get("lineId") in sf_line_ids:
            return True
        if p.get("lineId") == "contact" and str(p.get("owner", "")).startswith(contact_owner_prefix):
            return True
        return False

    scope = [f for f in all_edges if in_scope(f)]
    scope_ids = {f["properties"]["id"] for f in scope}

    deg_all = Counter()
    for f in all_edges:
        p = f["properties"]
        deg_all[p["from"]] += 1
        deg_all[p["to"]] += 1
    foreign_nodes = set()
    for f in all_edges:
        if f["properties"]["id"] in scope_ids:
            continue
        p = f["properties"]
        foreign_nodes.add(p["from"])
        foreign_nodes.add(p["to"])

    adj = defaultdict(list)
    for f in scope:
        p = f["properties"]
        adj[p["from"]].append(f)
        adj[p["to"]].append(f)

    def key_of(f):
        p = f["properties"]
        return (p["railwaySystemId"], p["lineId"], p["owner"], p.get("layer"))

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
        return len({key_of(f) for f in adj[nid]}) > 1

    def walk(start_edge, from_end):
        seq = []
        cur = start_edge
        cur_key = key_of(cur)
        entry = cur["properties"]["from"] if from_end == "from" else cur["properties"]["to"]
        visited = {cur["properties"]["id"]}
        guard = 0
        while True:
            guard += 1
            if guard > 100000:
                break
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

    used = set()
    chains = []
    for f in scope:
        fid = f["properties"]["id"]
        if fid in used:
            continue
        fwd, vis_f = walk(f, "from")
        bwd, vis_b = walk(f, "to")
        back = [e for e in bwd if e[0]["properties"]["id"] != fid]
        visited = vis_f | vis_b
        used |= visited
        ordered = list(reversed(back)) + fwd
        coords = []
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
        chains.append(Chain(
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
        ))
    return chains, nodes, scope


if __name__ == "__main__":
    import collections
    d = load()
    lines = load_lines()
    sf_ids = {l["id"] for l in lines if l["systemId"] == "SFRT"}
    print("SFRT line ids:", sorted(sf_ids))
    chains, nodes, scope = build_chains(d, sf_ids)
    print(f"scope edges={len(scope)} chains={len(chains)} km={sum(c.length for c in chains)/1000:.1f}")
    agg = collections.defaultdict(lambda: [0, 0.0])
    for c in chains:
        k = (c.line_id, c.owner)
        agg[k][0] += 1
        agg[k][1] += c.length
    for k in sorted(agg, key=lambda k: str(k)):
        print(f"  {str(k[0]):10s} owner={str(k[1]):9s} chains={agg[k][0]:3d} {agg[k][1]/1000:7.2f} km")
