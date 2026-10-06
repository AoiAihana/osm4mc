#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关系成员守卫：抓住「成员被静默丢弃」并（可选）自动补回。

背景
----
OSM API 对 relation 的更新是**整表替换**：请求体里没列出的成员会被删除。而 iD
有多处会**重建**成员表，且都基于它本地已下载的那部分图（graph）：

  * merge_polygon.js  —— osmJoinWays 会 filter 掉非 way 成员与未加载成员（补丁 A）
  * split.js isArea   —— 把 way 成员换成 relation 成员（补丁 B）
  * add_member.js     —— 注释自己写明：
        // `joined` might not contain all of the way members,
        // But will contain only the completed (downloaded) members

后果就是：往一个关系里加几条线，**别的线会莫名其妙掉出关系**，而它们本身既没被
删除也没被改动。实测本实例里 G104 道路关系（relation 117）因此一次掉了 20 条
`ref=G104` 的 way（共约 9.6 km），其中 14 条变成完全孤儿。

判据
----
对每个关系版本 V（V>1），比较 V 与 V-1 的成员表。某个成员「疑似被静默丢弃」当：

  1. 它在 V-1 里、不在 V 里（被移除）；
  2. 它**仍然存在**（node/way/relation 都还在，没有被删）；
  3. 它的最新版本 changeset **不是** V 所在的那个 changeset（即这次编辑根本没碰它）。

条件 3 是关键的区分点：如果你在 iD 里**故意**把一个成员移出关系，那条 way 通常也
不会被改动 —— 所以本工具默认只**报告**，要真正补回必须显式加 `--apply`。

用法
----
    python3 tools/guard-relation-members.py                # 只报告
    python3 tools/guard-relation-members.py --since 160    # 只看 changeset > 160
    python3 tools/guard-relation-members.py --apply        # 报告并自动补回
    python3 tools/guard-relation-members.py --fail-on-drop # 有丢弃就退出码 1（CI 门禁）
"""
import argparse
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "http://localhost:3000/api/0.6"
PROJECT = os.environ.get("PROJECT", "osm")


def psql(sql):
    cmd = ["docker", "compose", "-p", PROJECT, "exec", "-T", "db",
           "psql", "-U", "postgres", "-d", "openstreetmap", "-At", "-F", "|", "-c", sql]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("psql 失败：%s" % r.stderr[:400])
    return [l.split("|") for l in r.stdout.splitlines() if l]


def token():
    with open(os.path.join(ROOT, "coastline", ".token"), encoding="utf-8") as f:
        return f.read().strip()


def api(method, path, tok, data=None, ctype="application/xml", timeout=300):
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


SQL = """
WITH v AS (
    SELECT relation_id, version,
           array_agg(member_type || ':' || member_id ORDER BY member_type, member_id) AS mem,
           array_agg(member_type || ':' || member_id || ':' || coalesce(member_role, '')
                     ORDER BY member_type, member_id) AS mem_role
      FROM relation_members GROUP BY relation_id, version
), d AS (
    SELECT relation_id, version, mem, mem_role,
           lag(mem)      OVER (PARTITION BY relation_id ORDER BY version) AS prev,
           lag(mem_role) OVER (PARTITION BY relation_id ORDER BY version) AS prev_role
      FROM v
)
SELECT d.relation_id, d.version, r.changeset_id,
       coalesce(array_to_string(ARRAY(SELECT unnest(d.prev) EXCEPT SELECT unnest(d.mem)), ' '), ''),
       coalesce(array_to_string(d.mem_role, ' '), ''),
       coalesce(array_to_string(d.prev_role, ' '), '')
  FROM d JOIN relations r ON r.relation_id = d.relation_id AND r.version = d.version
 WHERE d.prev IS NOT NULL
   AND coalesce(array_length(d.mem, 1), 0) < coalesce(array_length(d.prev, 1), 0)
   AND r.changeset_id > %d
   -- 只处理关系的**当前版本**：更早版本的丢弃早已被后续版本取代，
   -- 把它们补回去是错的（例如 r117 v3 丢的那几条，路线后来已重构）。
   -- 本工具是「每次编辑后立刻跑」的守卫，抓的就是最新版本上刚发生的丢弃。
   AND d.version = (SELECT max(version) FROM relations r2 WHERE r2.relation_id = d.relation_id)
   AND EXISTS (SELECT 1 FROM current_relations cr WHERE cr.id = d.relation_id)
 ORDER BY d.relation_id, d.version
"""

EXISTS_SQL = {
    "node": "SELECT id, (SELECT max(changeset_id) FROM nodes n2 WHERE n2.node_id = n.id) FROM current_nodes n WHERE id IN (%s)",
    "way": "SELECT id, (SELECT max(changeset_id) FROM ways w2 WHERE w2.way_id = w.id) FROM current_ways w WHERE id IN (%s)",
    "relation": "SELECT id, (SELECT max(changeset_id) FROM relations r2 WHERE r2.relation_id = r.id) FROM current_relations r WHERE id IN (%s)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=0, help="只看 changeset 大于该值的版本")
    ap.add_argument("--apply", action="store_true", help="真正补回（默认只报告）")
    ap.add_argument("--fail-on-drop", action="store_true", help="发现静默丢弃时退出码 1")
    a = ap.parse_args()

    rows = psql(SQL % a.since)
    if not rows:
        print("没有发现成员数下降的关系版本")
        return 0

    # 每个被移除的成员：查它是否还存在、最后改动在哪个 changeset
    # 注意：库里 member_type 是首字母大写的 Way/Node/Relation，统一转小写
    alive = {}
    for r in rows:
        for tok_ in r[3].split():
            t, mid = tok_.split(":")
            alive.setdefault((t.lower(), int(mid)), None)

    by_type = {}
    for (t, mid) in alive:
        by_type.setdefault(t, []).append(mid)
    for t, ids in by_type.items():
        ids = sorted(set(ids))
        for chunk in [ids[i:i + 500] for i in range(0, len(ids), 500)]:
            for mid, last_cs in psql(EXISTS_SQL[t] % ",".join(str(x) for x in chunk)):
                alive[(t, int(mid))] = int(last_cs)

    suspicious = []
    for rid, ver, cs, dropped, cur_roles, prev_roles in rows:
        rid, ver, cs = int(rid), int(ver), int(cs)
        prev = {}
        for item in prev_roles.split():
            t, mid, role = item.split(":", 2)
            prev[(t.lower(), int(mid))] = role
        cur = set()
        for item in cur_roles.split():
            t, mid = item.split(":", 2)[:2]
            cur.add((t.lower(), int(mid)))
        for tok_ in dropped.split():
            t, mid = tok_.split(":")
            key = (t.lower(), int(mid))
            if key in cur:
                continue
            last_cs = alive.get(key)
            if last_cs is None:
                continue                      # 已被删除 —— 属于正常收缩
            if last_cs == cs:
                continue                      # 本次编辑动过它 —— 视为有意
            suspicious.append(dict(rid=rid, ver=ver, cs=cs, key=key,
                                   role=prev.get(key, ""), last_cs=last_cs))

    if not suspicious:
        print("没有发现「成员仍存在、却被移出关系」的静默丢弃")
        return 0

    print("=" * 96)
    print("疑似静默丢弃：成员仍存在，且它的最新改动不在该 changeset 里")
    print("=" * 96)
    print("  %-6s %-5s %-8s %-14s %-8s %s" % ("关系", "版本", "changeset", "成员", "最后改动", "角色"))
    print("  " + "-" * 92)
    for s in suspicious:
        t, mid = s["key"]
        print("  r%-5d v%-4d %-8d %-14s %-8d %s" % (s["rid"], s["ver"], s["cs"],
                                                    "%s %d" % (t, mid), s["last_cs"], s["role"] or "(空)"))
    print("\n  共 %d 个成员疑似被静默丢弃" % len(suspicious))

    if not a.apply:
        print("\n（只报告，未修改任何数据；加 --apply 自动补回）")
    else:
        tok = token()
        by_rel = {}
        for s in suspicious:
            by_rel.setdefault(s["rid"], []).append(s)
        for rid, items in sorted(by_rel.items()):
            st, rx = api("GET", "/relation/%d" % rid, tok)
            if st != 200:
                print("  r%d 读取失败 %s" % (rid, st)); continue
            ver = int(re.search(r'<relation\b[^>]*\bversion="(\d+)"', rx).group(1))
            tags = re.findall(r'<tag\s+k="([^"]*)"\s+v="([^"]*)"', rx)
            cur = re.findall(r'<member\s+type="(\w+)"\s+ref="(\d+)"\s+role="([^"]*)"', rx)
            have = {(t, int(r)) for t, r, _ in cur}
            add = [s for s in items if s["key"] not in have]
            if not add:
                continue
            st, cs = api("PUT", "/changeset/create", tok,
                         ('<osm><changeset>'
                          '<tag k="comment" v="osm-carto4mc: 守卫补回被静默丢弃的关系成员（r%d，%d 个）"/>'
                          '<tag k="created_by" v="osm-carto4mc-guard"/></changeset></osm>'
                          % (rid, len(add))).encode())
            if st != 200:
                print("  r%d 建 changeset 失败 %s" % (rid, st)); continue
            cs = cs.strip()
            mem = "".join('  <member type="%s" ref="%s" role="%s"/>\n' % (t, r, role or "")
                          for t, r, role in cur)
            mem += "".join('  <member type="%s" ref="%d" role="%s"/>\n' % (s["key"][0], s["key"][1], s["role"] or "")
                           for s in add)
            body = ('<osm><relation id="%d" version="%d" changeset="%s">\n%s%s</relation></osm>'
                    % (rid, ver, cs, mem,
                       "".join('  <tag k="%s" v="%s"/>\n' % (k, v) for k, v in tags)))
            st, resp = api("PUT", "/relation/%d" % rid, tok, body.encode())
            api("PUT", "/changeset/%s/close" % cs, tok)
            ok = st == 200 and "<remark" not in resp
            print("  r%-5d %s 补回 %d 个成员 (changeset %s)" % (rid, "OK" if ok else "失败", len(add), cs))
            if not ok:
                print("     %s" % resp[:200])

    return 1 if a.fail_on_drop else 0


if __name__ == "__main__":
    sys.exit(main())
