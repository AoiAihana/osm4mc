#!/usr/bin/env bash
#
# 检查「关系成员被悄悄删掉」—— 比对每个关系各版本的成员表。
#
# 为什么需要它：OSM API 对 relation 的更新是**整表替换**，请求体里没列出的成员会被
# 删除。而 iD 2.40.0 有两处会重写关系成员：
#
#   * 合并多边形（actionMergePolygon）：用 osmJoinWays(members) 重建成员表，
#     而 osmJoinWays 会 filter 掉「非 way 成员」与「未加载进 graph 的成员」，
#     于是关系里的 node / 子 relation 成员被静默删除；
#     （vendored iD: node_modules/@openstreetmap/id/dist/iD.js:99752）
#   * 合并线（actionJoin）：只用 survivor 顶替已合并的 way，并且 #5085 的
#     「解散单成员 multipolygon」对多成员关系直接 return，于是可能留下
#     「关系 + 已带同样标签的 way」两份 → 渲染重复。
#
# 本脚本只读数据库，不做任何修改，可以随时跑、也可以放进 CI/cron。
#
# 用法：
#   ./tools/check-relation-member-loss.sh              # 看全部历史
#   ./tools/check-relation-member-loss.sh --id 7       # 只看某个关系
#   ./tools/check-relation-member-loss.sh --since 2026-10-01
#   ./tools/check-relation-member-loss.sh --fail-on-id   # 发现 iD 造成的丢失就退出 1
#
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${DOCKER_CONFIG:-}" ] && [ -d "${PWD}/.docker" ]; then
    export DOCKER_CONFIG="${PWD}/.docker"
fi

PROJECT="${PROJECT:-osm}"
REL_ID=""
SINCE=""
FAIL_ON_ID=0

while [ $# -gt 0 ]; do
    case "$1" in
    --id) REL_ID="$2"; shift 2 ;;
    --since) SINCE="$2"; shift 2 ;;
    --fail-on-id) FAIL_ON_ID=1; shift ;;
    -h | --help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

FILTER=""
[ -n "${REL_ID}" ] && FILTER="${FILTER} AND d.relation_id = ${REL_ID}"
[ -n "${SINCE}" ] && FILTER="${FILTER} AND c.created_at >= '${SINCE}'"

say() { printf '\n=== %s\n' "$*"; }

SQL=$(
    cat <<EOSQL
WITH v AS (
    SELECT relation_id, version,
           array_agg(member_type || ':' || member_id ORDER BY member_type, member_id) AS mem
      FROM relation_members GROUP BY relation_id, version
), d AS (
    SELECT relation_id, version, mem,
           lag(mem) OVER (PARTITION BY relation_id ORDER BY version) AS prev
      FROM v
)
SELECT d.relation_id || '|' || d.version
       || '|' || coalesce(array_length(d.prev, 1), 0)
       || '|' || coalesce(array_length(d.mem, 1), 0)
       || '|' || coalesce(array_to_string(ARRAY(SELECT unnest(d.prev) EXCEPT SELECT unnest(d.mem)), ' '), '')
       || '|' || r.changeset_id
       || '|' || coalesce((SELECT v FROM changeset_tags t WHERE t.changeset_id = r.changeset_id AND t.k = 'created_by'), '?')
       || '|' || coalesce((SELECT v FROM changeset_tags t WHERE t.changeset_id = r.changeset_id AND t.k = 'comment'), '')
  FROM d
  JOIN relations r ON r.relation_id = d.relation_id AND r.version = d.version
  JOIN changesets c ON c.id = r.changeset_id
 WHERE d.prev IS NOT NULL
   AND coalesce(array_length(d.mem, 1), 0) < coalesce(array_length(d.prev, 1), 0)
   ${FILTER}
 ORDER BY d.relation_id, d.version;
EOSQL
)

ROWS="$(docker compose -p "${PROJECT}" exec -T db \
    psql -U postgres -d openstreetmap -At -F '|' -c "${SQL}")"

if [ -z "${ROWS}" ]; then
    say "没有发现成员数下降的关系版本"
    exit 0
fi

say "成员数下降的关系版本（API 是整表替换，所以没列出的成员等于被删）"
printf '  %-6s %-5s %-10s %-28s %-8s %s\n' 关系 版本 "成员数" "消失的成员" changeset 提交者
printf '  %s\n' "--------------------------------------------------------------------------------------------"

ID_HITS=0
while IFS='|' read -r rel ver before after dropped cs author comment; do
    [ -z "${rel}" ] && continue
    mark=""
    case "${author}" in
    iD*) mark="  <-- iD 造成"; ID_HITS=$((ID_HITS + 1)) ;;
    esac
    printf '  %-6s %-5s %-10s %-28s %-8s %s%s\n' \
        "r${rel}" "v${ver}" "${before} → ${after}" "${dropped}" "${cs}" "${author}" "${mark}"
    [ -n "${comment}" ] && printf '         %s\n' "comment: ${comment}"
done <<<"${ROWS}"

say "小结"
echo "  共 $(printf '%s\n' "${ROWS}" | wc -l | tr -d ' ') 个版本出现成员减少，其中 iD 造成的 ${ID_HITS} 个"
cat <<'EOF'

  注意：成员数下降**不一定**是 bug —— 用 iD 删掉一个成员 way、或用 Join 把多个
  成员 way 合成一条，成员数本来就会下降。要区分的话看「消失的成员」那一列：
  被合成的那条会出现在 survivor 里，而**凭空消失且当前已不存在**的成员才是丢数据。

  想确认某个关系现在是否仍然自洽：
    ./tools/check-relation-member-loss.sh --id <关系id>
    docker compose exec db psql -U postgres -d gis \
      -c "SELECT osm_id, ST_NumGeometries(way) FROM planet_osm_polygon WHERE osm_id = -<关系id>"
EOF

if [ "${FAIL_ON_ID}" = "1" ] && [ "${ID_HITS}" -gt 0 ]; then
    exit 1
fi
