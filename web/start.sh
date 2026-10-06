#!/bin/sh
#
# Startup wrapper for the openstreetmap-website container.
#
# Its job is to make sure the front-end bundles are current before Rails serves
# them. They are NOT derived from app/assets alone: osm.js.erb inlines all of
# config/layers.yml (plus legend.yml and the settings files) while Sprockets
# compiles it.
#
# Sprockets does track those files - osm.js.erb declares
# `//= depend_on layers.yml` and config/ is on the asset load path - but
# something still has to *ask* it to recompile. This container serves the
# precompiled set in public/assets, resolved through tmp/manifest.json, and that
# manifest is only ever rewritten by an explicit `assets:precompile`.
#
# Getting this wrong is silent and confusing: the browser keeps loading whatever
# tileUrl was in layers.yml at the time of the last precompile. For a self
# hosted instance that means the map still fetches the official
# openstreetmap.org tiles instead of the local ones, no matter what layers.yml
# says. So: rebuild whenever an input is newer than the manifest.
#
set -e

cd /app

RAILS_ENV="${RAILS_ENV:-development}"

# ---------------------------------------------------------------------------
# Patch the vendored iD bundle (idempotent).
#
# iD's "merge polygons" action (modules/actions/merge_polygon.js) rebuilds a
# relation's member list from osmJoinWays(), and osmJoinWays silently ignores
# non-way members and any member that is not loaded into iD's graph
# (modules/osm/multipolygon.js). The OSM API replaces a relation's members
# wholesale, so every member that is dropped there is deleted for good - this
# instance really lost data that way (relation 3 lost way 3540, which is still
# alive but now belongs to no relation). See tools/check-relation-member-loss.sh.
#
# docker/patch-id-member-loss.py only ever *adds* ignored members back, so it
# cannot introduce loss of its own. It is idempotent and tells us whether it
# changed anything, which means the compiled asset below is stale.
# ---------------------------------------------------------------------------
ID_BUNDLE="node_modules/@openstreetmap/id/dist/iD.js"
ID_PATCHER="docker/patch-id-member-loss.py"
PATCHED_ID=0

if [ -f "${ID_BUNDLE}" ] && [ -f "${ID_PATCHER}" ] && command -v python3 >/dev/null 2>&1; then
    if python3 "${ID_PATCHER}" "${ID_BUNDLE}" | grep -q '^PATCHED=1'; then
        echo "[web] patched ${ID_BUNDLE} (relation member loss) - bundles are stale"
        PATCHED_ID=1
    fi
fi

id_asset_patched() {
    [ -f tmp/manifest.json ] || return 1
    _target=$(ruby -rjson -e '
        files = JSON.parse(File.read("tmp/manifest.json"))["files"] || {}
        entry = files.find { |_, v| v["logical_path"] == "id.js" }
        print entry ? entry[0] : ""
    ' 2>/dev/null || true)
    [ -n "$_target" ] || return 1
    [ -f "public/assets/${_target}" ] || return 1
    # 查**代码**而不是注释：这里要确认真正被服务的字节里有没有补丁。
    #   A: actionMergePolygon 回写成员前的补回逻辑
    #   B: split 的 isArea 分支不再出现原锚点
    grep -q "seen.add(m.type" "public/assets/${_target}" 2>/dev/null || return 1
    grep -q "splitWayMember(graph, relation.id, wayA, wayB)" "public/assets/${_target}" 2>/dev/null || return 1
    if grep -q "replaceMember(wayA, multipolygon)" "public/assets/${_target}" 2>/dev/null; then
        return 1
    fi
    return 0
}

id_asset_target() {
    ruby -rjson -e '
        files = JSON.parse(File.read("tmp/manifest.json"))["files"] || {}
        entry = files.find { |_, v| v["logical_path"] == "id.js" }
        print entry ? entry[0] : ""
    ' 2>/dev/null || true
}

# 把带补丁的 id.js 覆盖到 public/assets 里**所有**历史 id-*.js 上。
#
# 为什么需要：资源文件名带内容哈希，浏览器可能还持有引用旧哈希的 HTML（或旧的
# 页面状态），而旧文件仍留在磁盘上会被照常服务 —— 于是又跑起没补丁的 iD，成员
# 继续丢。删掉旧文件只会 404 白屏，更糟；覆盖成带补丁的内容则既不白屏、也不可
# 能再发出未打补丁的代码。
id_harden_stale_assets() {
    _good="public/assets/$1"
    [ -f "${_good}" ] || return 0
    for _f in public/assets/id-*.js; do
        [ -f "${_f}" ] || continue
        [ "${_f}" = "${_good}" ] && continue
        if grep -q "seen.add(m.type" "${_f}" 2>/dev/null &&
           ! grep -q "replaceMember(wayA, multipolygon)" "${_f}" 2>/dev/null; then
            continue    # 已经是完整补丁
        fi
        cp -f "${_good}" "${_f}" && echo "[web] hardened stale asset $(basename "${_f}")"
    done
    return 0
}

needs_build() {
    [ "${FORCE_ASSETS:-}" = "1" ] && return 0
    [ "${PATCHED_ID}" = "1" ] && return 0
    [ -f tmp/manifest.json ] || return 0

    # 源 bundle 的 mtime 说明不了问题：node_modules 是匿名卷，容器重建时会把它
    # 重置成**未打补丁**的副本，而 tmp/manifest.json 仍指向更早编译出来的
    # **未打补丁**的 id.js。浏览器于是照旧加载没补丁的 iD，成员继续丢。
    # 所以直接检查 manifest 指向的那个资源的内容 —— 这才是真正被服务的东西。
    id_asset_patched || return 0

    # The asset pool in public/assets is shared between the development and the
    # production stack (both mount the same checkout). If it is ever cleaned
    # while a manifest survives, that manifest points at files that no longer
    # exist and the site would come up without JS/CSS - rebuild instead.
    manifest_target=$(ruby -rjson -e '
        files = JSON.parse(File.read("tmp/manifest.json"))["files"] || {}
        entry = files.find { |_, v| v["logical_path"] == "application.js" }
        print entry ? entry[0] : ""
    ' 2>/dev/null || true)
    [ -n "$manifest_target" ] || return 0
    [ -f "public/assets/${manifest_target}" ] || return 0

    for source in \
        config/layers.yml \
        config/legend.yml \
        config/settings.yml \
        config/settings.local.yml \
        "config/settings/${RAILS_ENV}.local.yml" \
        "${ID_BUNDLE}"
    do
        [ -f "$source" ] || continue
        # an input newer than the manifest means the bundles are stale
        [ "$source" -nt tmp/manifest.json ] && return 0
    done

    return 1
}

if needs_build; then
    # 关键：manifest 指向的资源没补丁时，**单跑 assets:precompile 修不好** ——
    # 实测它会沿用旧 manifest 里的 id.js 条目，继续产出未打补丁的映射（这正是
    # 「补丁明明在源码里、浏览器却一直在跑没补丁的 iD」的原因，成员因此反复丢）。
    # 必须把 manifest 与 Sprockets 缓存一并清掉再编。
    if ! id_asset_patched; then
        echo "[web] served id.js lacks the member-loss patches: resetting manifest + Sprockets cache"
        rm -f tmp/manifest.json
        rm -rf tmp/cache/assets
    fi
    echo "[web] rebuilding bundles"
    bundle exec i18n export
    bundle exec rails assets:precompile
else
    echo "[web] precompiled assets are up to date"
fi

if id_asset_patched; then
    echo "[web] verified: served id.js carries the relation member-loss patches"
    # 把所有历史 id-*.js 也覆盖成带补丁的内容：浏览器若还持有旧 HTML（引用旧
    # 哈希），那条路也必须拿到带补丁的 iD，否则成员照旧会被丢。
    id_harden_stale_assets "$(id_asset_target)"
else
    echo "[web] WARNING: served id.js does NOT carry the member-loss patches - iD will keep dropping relation members" >&2
    echo "[web] WARNING: check ${ID_PATCHER} and tmp/manifest.json" >&2
fi

if [ "$RAILS_ENV" = "production" ]; then
    echo "[web] preparing the production database"
    bundle exec rails db:prepare
fi

# ---------------------------------------------------------------------------
# 关系成员守卫（后台循环）。
#
# iD 有多处会基于「只下载了一部分」的本地图重建关系成员表（merge_polygon、
# split 的 isArea、add_member），而 API 对 relation 是整表替换 —— 于是往关系里
# 加几条线时，别的线会被静默踢出关系。实测本实例里 G104 道路关系一次掉了 20 条
# ref=G104 的 way（约 9.6 km，其中 14 条变成完全孤儿），海岸线关系也丢过多次。
#
# 守卫的判据：某成员被移出关系，但它**仍然存在**、且它的最新版本**不是**在这个
# changeset 里被改动的 —— 那就是静默丢弃，自动补回（changeset 记在
# osm-carto4mc-guard 名下）。见 docker/guard-relation-members.py。
#
#   GUARD_RELATION_MEMBERS=0   关掉守卫
#   GUARD_INTERVAL=30          检查间隔（秒）
#   GUARD_SKIP=117,123         这些关系永不自动补回（确实想移除成员时用）
# ---------------------------------------------------------------------------
GUARD_PY="docker/guard-relation-members.py"
if [ "${GUARD_RELATION_MEMBERS:-1}" = "1" ] && [ -f "${GUARD_PY}" ]; then
    if bundle exec rails runner docker/provision-guard-token.rb 2>/dev/null | grep -q '^GUARD_TOKEN=\(ok\|created\)$'; then
        echo "[web] starting relation member guard (interval ${GUARD_INTERVAL:-30}s, skip '${GUARD_SKIP:-}')"
        python3 "${GUARD_PY}" --apply --interval "${GUARD_INTERVAL:-30}" &
    else
        echo "[web] WARNING: guard token unavailable - relation member guard disabled" >&2
    fi
fi

exec bundle exec rails s -p 3000 -b '0.0.0.0'
