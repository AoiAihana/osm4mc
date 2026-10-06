#!/usr/bin/env bash
#
# 把一个环境的**数据**复制到另一个环境（默认 test -> prod），源环境的数据原样保留。
#
#   ./tools/copy-env-data.sh                 # 同 status
#   ./tools/copy-env-data.sh status          # 只读：两个环境各有多少数据、要搬多少
#   ./tools/copy-env-data.sh run             # 执行复制（默认 test -> prod）
#   ./tools/copy-env-data.sh run --start     # 复制完顺便把目标环境拉起来
#   ./tools/copy-env-data.sh help
#
# 选项：
#   --from <env>    源环境，test 或 prod（默认 test）
#   --to <env>      目标环境（默认 prod）
#   --start         复制完成后启动目标环境并等它就绪
#   --keep-dumps    保留中转 dump（默认就保留在 backup/copy-<时间戳>/）
#   --no-dumps      复制成功后删掉中转 dump 目录
#   --yes           不交互确认
#
# 复制的是「内容数据」，有两样东西**故意不覆盖**，见下面「不复制什么」：
#   * 目标环境自己的 OAuth 应用（按**名字**保留目标那几条，其余应用照常复制过来）
#   * 目标环境的设置文件（config/settings/<env>.local.yml）
#
# ---------------------------------------------------------------------------
# 复制什么
# ---------------------------------------------------------------------------
#   1. API 库 openstreetmap  —— 用户、changeset、nodes/ways/relations 全版本历史、
#      current_* 表、notes、OAuth 令牌……（rails port 那一侧）
#   2. 渲染库 gis           —— osm2pgsql 的 planet_osm_* 表、external_data、
#      以及 **osm_sync_state**（同步水位线；它就在 gis 库里，所以水位线自动跟着走）
#   3. ./data/<src>/replication -> ./data/<dst>/replication
#      （state.txt + NNN.osc.gz，给外部消费者的增量目录；不搬就会出现序号断档）
#   4. 清空目标环境的 mod_tile 缓存 ./data/<dst>/tiles
#      （瓦片是从旧数据渲染的，不清就会继续端出旧图）
#
# ---------------------------------------------------------------------------
# 不复制什么（以及为什么）
# ---------------------------------------------------------------------------
#   * oauth_applications：目标环境用自己的一套标识，所以**按名字**把目标那几条
#     写回去（先在复制前存下来，恢复后替换同名的），目标的
#     config/settings/<env>.local.yml 里那三个 id/secret 依然有效，外部客户端
#     （JOSM 等）不用重配。源环境里目标没有的应用（例如 railway-import）
#     作为数据照常复制过来。想改成「和目标完全一致」，复制完自己跑：
#         ./tools/switch-env.sh prod --init          # 复用同名应用并改写设置文件
#     （注意应用密钥是哈希存储的，读不回来；要换新密钥用 ROTATE_SECRETS=true）
#   * ./data/<dst>/carto：mapnik.xml 由 tileserver 每次启动时用 carto 重新编译，
#     不必也不该手搬（里面有指向 checkout 的符号链接）。字体目录本来就是共享的。
#
# ---------------------------------------------------------------------------
# 为什么中途要停源环境
# ---------------------------------------------------------------------------
# 两个环境共用宿主端口（54321 等），同一时刻只能有一个在跑。脚本的顺序是：
#   源库在线 → pg_dump（只读，不写源）→ 停源栈 → 起目标库 → 恢复 → 再按需启动
# pg_dump 拿的是一致性快照，且**从不写源库**，所以源环境的数据是原样保留的。
#
set -euo pipefail

cd "$(dirname "$0")/.."

# 受限沙箱里 $HOME 不可写，Docker CLI 需要自己的配置目录
if [ -z "${DOCKER_CONFIG:-}" ] && [ -d "${PWD}/.docker" ]; then
    export DOCKER_CONFIG="${PWD}/.docker"
fi

PROJECT_TEST="${PROJECT_TEST:-osm}"
PROJECT_PROD="${PROJECT_PROD:-osm-prod}"
ENV_FILE_TEST=".env"
ENV_FILE_PROD=".env.prod"
# 一个已经有本地镜像的小容器，用来以 root 身份操作 nobody 拥有的数据目录
# （./data/prod 是容器里的 nobody 建的，宿主用户删不动里面的文件）
HELPER_IMAGE="${HELPER_IMAGE:-nginx:alpine}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-600}"

# 每个库需要的扩展（pg_dump 里带了 CREATE EXTENSION，但先建好更稳，
# 也保证 fresh 库上 postgis 的 spatial_ref_sys 一定存在）
EXTENSIONS_openstreetmap="postgis btree_gist"
EXTENSIONS_gis="postgis hstore"

say() { printf '\n=== %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# --- 环境管道 ---------------------------------------------------------------
canon_env() {
    case "${1:-}" in
    test | dev | development) echo "test" ;;
    prod | production) echo "prod" ;;
    *) die "unknown environment '${1:-}' (expected 'test' or 'prod')" ;;
    esac
}

env_file_for() { [ "$1" = "prod" ] && echo "${ENV_FILE_PROD}" || echo "${ENV_FILE_TEST}"; }
env_label_for() { [ "$1" = "prod" ] && echo "production" || echo "test"; }
project_for() { [ "$1" = "prod" ] && echo "${PROJECT_PROD}" || echo "${PROJECT_TEST}"; }
# 运行时的数据根目录，和 .env / .env.prod 里的 DATA_DIR 保持一致
data_dir_for() {
    local v
    v="$(read_setting "$(env_file_for "$1")" DATA_DIR "")"
    echo "${v:-./data}"
}
db_port_for() { read_setting "$(env_file_for "$1")" DB_PORT 54321; }

ec() {
    local env="$1"
    shift
    if [ "$env" = "prod" ]; then
        docker compose -p "${PROJECT_PROD}" --env-file "${ENV_FILE_PROD}" \
            -f docker-compose.yml -f docker-compose.prod.yml "$@"
    else
        docker compose -p "${PROJECT_TEST}" "$@"
    fi
}

read_setting() {
    local file="$1" key="$2" default="$3" value=""
    if [ -f "${file}" ]; then
        value="$(grep -E "^${key}=" "${file}" | tail -1 | cut -d= -f2- || true)"
    fi
    printf '%s\n' "${value:-${default}}"
}

# 环境里的服务容器名形如 <project>-db-1
db_container_for() { echo "$(project_for "$1")-db-1"; }

preflight() {
    command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
    docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
    docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon (is it running?)"
    [ -f docker-compose.yml ] || die "docker-compose.yml not found - run me from the repository"
}

# --- 数据库 ----------------------------------------------------------------
db_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$(db_container_for "$1")" 2>/dev/null || echo false)" = "true" ]
}

db_exists() {
    docker inspect -f '{{.Id}}' "$(db_container_for "$1")" >/dev/null 2>&1
}

wait_for_db() {
    local env="$1" c waited=0
    c="$(db_container_for "${env}")"
    while :; do
        if docker exec "${c}" pg_isready -U postgres >/dev/null 2>&1; then return 0; fi
        if [ "${waited}" -ge 120 ]; then die "数据库 ${c} 120 秒内没有就绪"; fi
        sleep 2
        waited=$((waited + 2))
    done
}

# 确保源库在线（可能只是容器停了 —— 数据都在卷里）
ensure_db_up() {
    local env="$1" c
    c="$(db_container_for "${env}")"
    if db_running "${env}"; then
        info "$(env_label_for "${env}") 的数据库已经在跑（${c}）"
        return 0
    fi
    say "启动 $(env_label_for "${env}") 的数据库（${c}）"
    if db_exists "${env}"; then
        docker start "${c}" >/dev/null
    else
        ec "${env}" up -d db
    fi
    wait_for_db "${env}"
    info "就绪"
}

db_size() {
    local env="$1" db="$2"
    docker exec "$(db_container_for "${env}")" psql -U postgres -Atc \
        "select pg_size_pretty(pg_database_size('${db}'))" 2>/dev/null || echo "-"
}

db_counts() {
    local env="$1"
    docker exec "$(db_container_for "${env}")" psql -U postgres -d openstreetmap -Atc "
        select 'users='||count(*) from users
        union all select 'changesets='||count(*) from changesets
        union all select 'current_nodes='||count(*) from current_nodes
        union all select 'current_ways='||count(*) from current_ways
        union all select 'current_relations='||count(*) from current_relations
        union all select 'node_versions='||count(*) from nodes
        union all select 'oauth_applications='||count(*) from oauth_applications
        order by 1;" 2>/dev/null | tr '\n' ' '
    docker exec "$(db_container_for "${env}")" psql -U postgres -d gis -Atc "
        select 'planet_osm_point='||count(*) from planet_osm_point
        union all select 'planet_osm_line='||count(*) from planet_osm_line
        union all select 'planet_osm_polygon='||count(*) from planet_osm_polygon
        union all select 'sequence='||coalesce(max(sequence),0) from osm_sync_state
        order by 1;" 2>/dev/null | tr '\n' ' '
    echo
}

# --- 复制 -------------------------------------------------------------------
dump_dir=""

dump_databases() {
    local src="$1" c
    c="$(db_container_for "${src}")"
    say "从 $(env_label_for "${src}") 导出数据库（只读，不动源数据）"
    for db in openstreetmap gis; do
        local out="${dump_dir}/${src}-${db}.dump"
        info "${db} -> ${out}"
        docker exec "${c}" pg_dump -U postgres -Fc -d "${db}" >"${out}"
        # 归档目录能列出来才算好 dump，坏文件不许往下走
        docker exec -i "${c}" pg_restore -l <"${out}" >/dev/null ||
            die "${out} 不是合法的 pg_dump 归档"
        info "  $(du -h "${out}" | cut -f1) （归档校验通过）"
    done
}

stop_env() {
    local env="$1"
    if [ -n "$(ec "${env}" ps -q 2>/dev/null || true)" ]; then
        say "停掉 $(env_label_for "${env}") 的整套服务（数据与卷不动）"
        ec "${env}" down >/dev/null 2>&1 || true
    fi
}

start_target_db() {
    local dst="$1"
    say "启动 $(env_label_for "${dst}") 的数据库"
    ec "${dst}" up -d db >/dev/null
    wait_for_db "${dst}"
    info "就绪"
}

# 把目标环境自己的 OAuth 应用先存下来（目标保留自己那套标识）。
#
# 存成**不含 id 的 INSERT**：numeric id 对谁都没意义（设置文件里记的是 uid），
# 不带 id 就不会和复制过来的其它应用撞主键，序列也交给默认值。
save_target_oauth() {
    local dst="$1" c
    c="$(db_container_for "${dst}")"
    say "保存 $(env_label_for "${dst}") 自己的 OAuth 应用（复制后按名字写回）"
    docker exec "${c}" psql -U postgres -d openstreetmap -Atc "
        select format(
          'INSERT INTO public.oauth_applications'
          ' (owner_type, owner_id, name, uid, secret, redirect_uri, scopes, confidential, created_at, updated_at)'
          ' VALUES (%L,%L,%L,%L,%L,%L,%L,%L,%L,%L);',
          owner_type, owner_id, name, uid, secret, redirect_uri, scopes, confidential, created_at, updated_at)
        from oauth_applications order by id;" \
        >"${dump_dir}/${dst}-oauth_applications.sql"
    docker exec "${c}" psql -U postgres -d openstreetmap -Atc \
        "select name from oauth_applications order by id;" \
        >"${dump_dir}/${dst}-oauth_names.txt"
    info "已保存 $(wc -l <"${dump_dir}/${dst}-oauth_names.txt" | tr -d ' ') 个应用：$(tr '\n' ' ' <"${dump_dir}/${dst}-oauth_names.txt")"
}

restore_databases() {
    local src="$1" dst="$2" c
    c="$(db_container_for "${dst}")"
    say "把数据恢复进 $(env_label_for "${dst}")"
    for db in openstreetmap gis; do
        info "${db}：重建空库 + 扩展"
        docker exec "${c}" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
            -c "DROP DATABASE IF EXISTS ${db} WITH (FORCE);" \
            -c "CREATE DATABASE ${db};"
        local ext=""
        local var="EXTENSIONS_${db}"
        ext="${!var}"
        for e in ${ext}; do
            docker exec "${c}" psql -U postgres -d "${db}" -v ON_ERROR_STOP=1 -q \
                -c "CREATE EXTENSION IF NOT EXISTS ${e};"
        done
        info "${db}：pg_restore（$(du -h "${dump_dir}/${src}-${db}.dump" | cut -f1)，要几分钟）"
        docker exec -i "${c}" pg_restore -U postgres -d "${db}" \
            --no-owner --no-acl --exit-on-error \
            <"${dump_dir}/${src}-${db}.dump"
    done
}

restore_target_oauth() {
    local dst="$1" c names
    c="$(db_container_for "${dst}")"
    names="$(cat "${dump_dir}/${dst}-oauth_names.txt")"
    say "按名字写回 $(env_label_for "${dst}") 自己的 OAuth 应用"
    # 只替换**同名**的那几个应用：源环境里目标没有的应用（JOSM、railway-import
    # 之类的小工具）作为数据照常留下。外键是 NO ACTION，先删引用再删应用。
    docker exec -i "${c}" psql -U postgres -d openstreetmap -v ON_ERROR_STOP=1 -q \
        -v names="${names}" <<'SQL'
DELETE FROM oauth_access_tokens
 WHERE application_id IN (SELECT id FROM oauth_applications
                           WHERE name = ANY(string_to_array(:'names', E'\n')));
DELETE FROM oauth_access_grants
 WHERE application_id IN (SELECT id FROM oauth_applications
                           WHERE name = ANY(string_to_array(:'names', E'\n')));
DELETE FROM oauth_applications WHERE name = ANY(string_to_array(:'names', E'\n'));
SQL
    docker exec -i "${c}" psql -U postgres -d openstreetmap -v ON_ERROR_STOP=1 -q \
        <"${dump_dir}/${dst}-oauth_applications.sql"
    info "现在库里的应用："
    docker exec "${c}" psql -U postgres -d openstreetmap -Atc \
        "select '    '||name||'  uid='||uid from oauth_applications order by id;"
}

# 目标环境的运行态：清瓦片缓存 + 搬 replication 目录
sync_runtime_state() {
    local src="$1" dst="$2" sdir ddir
    sdir="$(data_dir_for "${src}")"
    ddir="$(data_dir_for "${dst}")"
    say "整理 $(env_label_for "${dst}") 的运行态"
    [ -d "${ddir}" ] || die "目标数据目录不存在：${ddir}"
    [ -d "${sdir}" ] || die "源数据目录不存在：${sdir}"

    # 这些目录属于容器里的 nobody，宿主机用户写不动，所以借一个小容器以 root 执行
    local src_rep dst_rep dst_tiles
    src_rep="$(cd "${sdir}" && pwd)/replication"
    dst_rep="$(cd "${ddir}" && pwd)/replication"
    dst_tiles="$(cd "${ddir}" && pwd)/tiles"

    info "清空瓦片缓存 ${dst_tiles}"
    docker run --rm -v "${dst_tiles}:/tiles" "${HELPER_IMAGE}" \
        sh -c 'rm -rf /tiles/* /tiles/.[!.]* 2>/dev/null || true; ls -A /tiles | wc -l'

    if [ -d "${src_rep}" ]; then
        info "复制增量目录 ${src_rep} -> ${dst_rep}"
        docker run --rm -v "${src_rep}:/src:ro" -v "${dst_rep}:/dst" "${HELPER_IMAGE}" \
            sh -c 'rm -rf /dst/* /dst/.[!.]* 2>/dev/null || true; cp -a /src/. /dst/; ls /dst | wc -l'
    else
        warn "源环境没有 ${src_rep}，跳过"
    fi
}

start_target_stack() {
    local dst="$1" port url waited=0
    port="$(read_setting "$(env_file_for "${dst}")" WEB_PORT 3001)"
    url="http://localhost:${port}/api/0.6/capabilities"
    say "启动 $(env_label_for "${dst}") 整套服务"
    ec "${dst}" up -d
    printf '  等站点就绪 %s ' "${url}"
    while :; do
        if curl -fsS -o /dev/null --max-time 3 "${url}" 2>/dev/null; then printf ' ok\n'; return 0; fi
        if [ "${waited}" -ge "${WAIT_TIMEOUT}" ]; then
            printf '\n'
            warn "站点 ${WAIT_TIMEOUT}s 内没应答，看 ./tools/switch-env.sh logs ${dst}"
            return 1
        fi
        printf '.'
        sleep 3
        waited=$((waited + 3))
    done
}

# --- 命令 -------------------------------------------------------------------
cmd_status() {
    preflight
    local src="${OPT_FROM}" dst="${OPT_TO}"
    say "源：$(env_label_for "${src}")（项目 $(project_for "${src}")）"
    if db_running "${src}"; then
        info "openstreetmap $(db_size "${src}" openstreetmap)   gis $(db_size "${src}" gis)"
        info "$(db_counts "${src}")"
    else
        info "数据库没在跑 —— status 只能看到卷；要读计数先让它起来："
        info "  docker start $(db_container_for "${src}")"
    fi
    say "目标：$(env_label_for "${dst}")（项目 $(project_for "${dst}")）"
    if db_running "${dst}"; then
        info "openstreetmap $(db_size "${dst}" openstreetmap)   gis $(db_size "${dst}" gis)"
        info "$(db_counts "${dst}")"
    else
        info "数据库没在跑；运行态目录 $(data_dir_for "${dst}")"
        info "  tiles:       $(find "$(data_dir_for "${dst}")/tiles" -type f 2>/dev/null | wc -l) 个文件"
        info "  replication: $(find "$(data_dir_for "${dst}")/replication" -type f 2>/dev/null | wc -l) 个文件"
    fi
    say "说明"
    info "run 会把「源的两个库 + replication 目录」复制到目标，并清空目标的瓦片缓存"
    info "目标的 OAuth 应用（按名字保留目标那几条）与设置文件不动"
    info "源环境全程只被 pg_dump 读，数据不会变"
}

cmd_run() {
    local src="${OPT_FROM}" dst="${OPT_TO}"
    [ "${src}" != "${dst}" ] || die "源和目标不能是同一个环境"
    preflight
    if [ "${dst}" = "test" ] && ! [ -f "openstreetmap-website/config/settings.local.yml" ]; then
        warn "目标环境的设置文件不存在"
    fi

    say "将要执行"
    info "源   ：$(env_label_for "${src}") （数据保持不变）"
    info "目标 ：$(env_label_for "${dst}") （两个数据库会被整个覆盖）"
    info "保留 ：目标的 OAuth 应用、设置文件"
    info "中转 ：${dump_dir}"
    if [ "${OPT_YES}" != "1" ]; then
        printf '\n确认继续？输入 yes 回车：'
        local reply=""
        read -r reply || true
        [ "${reply}" = "yes" ] || die "已取消"
    fi

    # 1) 源库在线 → 导出（只读）
    ensure_db_up "${src}"
    say "复制前的源数据"
    info "$(db_counts "${src}")"
    dump_databases "${src}"

    # 2) 停源栈（端口要腾给目标），起目标库
    stop_env "${src}"
    stop_env "${dst}"
    start_target_db "${dst}"

    # 3) 存目标的 OAuth 行 → 恢复两库 → 写回 OAuth
    save_target_oauth "${dst}"
    restore_databases "${src}" "${dst}"
    restore_target_oauth "${dst}"

    # 4) 运行态
    sync_runtime_state "${src}" "${dst}"

    # 5) 结果
    say "复制后的目标数据"
    info "$(db_counts "${dst}")"

    if [ "${OPT_START}" = "1" ]; then
        start_target_stack "${dst}"
    else
        say "下一步"
        info "启动目标环境：./tools/switch-env.sh ${dst}"
    fi

    if [ "${OPT_NO_DUMPS}" = "1" ]; then
        say "删除中转 dump"
        rm -rf "${dump_dir}"
        info "${dump_dir} 已删除"
    else
        say "中转 dump 保留在"
        info "${dump_dir}"
        info "确认目标没问题后可以自己删掉"
    fi
}

usage() { sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; }

# --- 参数 -------------------------------------------------------------------
OPT_FROM="test"
OPT_TO="prod"
OPT_START="0"
OPT_YES="0"
OPT_NO_DUMPS="0"

command="${1:-status}"
[ "$#" -gt 0 ] && shift || true

while [ "$#" -gt 0 ]; do
    case "$1" in
    --from) OPT_FROM="$(canon_env "${2:-}")"; shift 2 ;;
    --to) OPT_TO="$(canon_env "${2:-}")"; shift 2 ;;
    --start) OPT_START="1"; shift ;;
    --keep-dumps) OPT_NO_DUMPS="0"; shift ;;
    --no-dumps) OPT_NO_DUMPS="1"; shift ;;
    --yes | -y) OPT_YES="1"; shift ;;
    *) die "unknown option '$1' - try './tools/copy-env-data.sh help'" ;;
    esac
done

case "${command}" in
status) cmd_status ;;
run)
    dump_dir="backup/copy-$(date '+%Y%m%d-%H%M%S')"
    mkdir -p "${dump_dir}"
    cmd_run
    ;;
help | -h | --help) usage ;;
*) die "unknown command '${command}' - try './tools/copy-env-data.sh help'" ;;
esac
