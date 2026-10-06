"""删除与既有国铁车站同名且几乎重合的 3 个地铁车站节点。"""
import sys, time
sys.path.insert(0, "/home/aoiaihana/osm/railway-import/scripts")
import osm_api as O

NODES = [141479, 141506, 141515]

api = O.OsmApi(base="http://localhost:3001", timeout=600)
cs = api.create_changeset({
    "comment": "删除与既有国铁车站同名且重合的舒芙蕾地铁车站节点（避免同名重复标注）",
    "created_by": "sfrt-import 1.0",
    "locale": "zh-CN",
})
print("changeset", cs, flush=True)
elems = []
for nid in NODES:
    st, txt = api._req("GET", f"/api/0.6/node/{nid}")
    ver = txt.split('version="', 1)[1].split('"', 1)[0]
    lat = txt.split('lat="', 1)[1].split('"', 1)[0]
    lon = txt.split('lon="', 1)[1].split('"', 1)[0]
    print(f"  node {nid} v{ver} ({lat},{lon})")
    elems.append(f'<node id="{nid}" lat="{lat}" lon="{lon}" version="{ver}" changeset="{cs}"/>')
res = api.upload(cs, O.build_osmchange(deletes=elems))
print("delete result:", [f"{e.tag} {e.get('old_id')}" for e in res])
api.close_changeset(cs)
print("done cs", cs)
