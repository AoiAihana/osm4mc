#!/usr/bin/env bash
#
# gis 渲染库定时备份（osm-carto4mc / osm2pgsql 那一侧的库）。
#
#   ./tools/backup-db.sh                 # 同 status
#   ./tools/backup-db.sh status          # 配置 / 目标环境 / 已有备份清单
#   ./tools/backup-db.sh run             # 前台调度循环，周期读 backup/backup.conf
#   ./tools/backup-db.sh now             # 立刻备份一次
#   ./tools/backup-db.sh list            # 列出已有备份
#   ./tools/backup-db.sh prune           # 立刻按 KEEP 清理旧备份
#   ./tools/backup-db.sh verify <文件>   # 校验某个备份能不能读出来
#   ./tools/backup-db.sh restore <文件>  # 打印恢复步骤（只打印，不动数据）
#   ./tools/backup-db.sh help
#
# 只备份 `gis`（渲染库），不碰 rails 的 `openstreetmap`（API 库）。
# 备份期间不需要停服务：pg_dump 拿的是一致性快照。
#
# 为什么必须用**容器里**的 pg_dump：宿主的 pg_dump 18 写出的归档是
# archive version 1.16，而容器里的 pg_restore 14 会直接拒绝
# （"unsupported version (1.16) in file header"）—— 那样备份就是废纸。
# 用容器内 14.23 的 pg_dump，产物同时能被 pg_restore 14 和 18 读出来。
#
# 所有可调项都在 backup/backup.conf 里，脚本每一轮都会重新读取。
#
# 环境变量：
#   BACKUP_CONF   配置文件路径（默认 <仓库根>/backup/backup.conf）
#   DOCKER_CONFIG 受限沙箱里 Docker CLI 的可写配置目录（会自动探测 ./.docker）
#
set -euo pipefail

# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

CONF_FILE="${BACKUP_CONF:-${REPO_ROOT}/backup/backup.conf}"

# 受限沙箱里 $HOME 不可写，Docker CLI 需要自己的配置目录
if [ -z "${DOCKER_CONFIG:-}" ] && [ -d "${REPO_ROOT}/.docker" ]; then
    export DOCKER_CONFIG="${REPO_ROOT}/.docker"
fi

# 等待数据库可连接的秒数（栈刚起来时 sync/loop 可能先于 db 就绪）
readonly DB_WAIT_TIMEOUT=120

LOG_TO_FILE=0
LOG_FILE_ABS=""

# 外部数据表：osmdata.openstreetmap.de 上的 shapefile，可以随时重新生成，
# 却是 gis 库里 99% 的体积。EXCLUDE_EXTERNAL_DATA=1 时排除掉它们。
readonly EXTERNAL_TABLES=(
    water_polygons
    simplified_water_polygons
    icesheet_polygons
    icesheet_outlines
)

say() { printf '\n=== %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# log <LEVEL> <消息...>：带时间戳；LOG_TO_FILE=1 时同时追加到日志文件
log() {
    local level="$1"
    shift
    local line
    line="$(printf '[%s] %-5s %s' "$(date '+%Y-%m-%d %H:%M:%S')" "${level}" "$*")"
    printf '%s\n' "${line}"
    if [ "${LOG_TO_FILE}" = "1" ] && [ -n "${LOG_FILE_ABS}" ]; then
        printf '%s\n' "${line}" >>"${LOG_FILE_ABS}" 2>/dev/null || true
    fi
}

# 把 stdin 的每一行按日志格式输出（用于展开 pg_restore 的报错）
log_block() {
    local level="$1" line
    while IFS= read -r line; do
        log "${level}" "  ${line}"
    done
}

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
config_defaults() {
    INTERVAL="6h"
    RUN_ON_START="1"
    ENV_NAME="auto"
    BACKUP_DIR="backup"
    GIS_DB="gis"
    GIS_USER="postgres"
    KEEP="0"
    EXCLUDE_EXTERNAL_DATA="0"
    COMPRESS="6"
    DUMP_TIMEOUT="5400"
    CHECKSUM="1"
    LOG_FILE="backup/backup.log"
    LOG_MAX_BYTES="5242880"
    # 环境变量优先于内置默认值，但 backup.conf 里的赋值最后生效
    PROJECT_TEST="${PROJECT_TEST:-osm}"
    PROJECT_PROD="${PROJECT_PROD:-osm-prod}"
}

require_int() { # <键名> <值> [最小值] [最大值]
    local name="$1" value="$2" min="${3:-0}" max="${4:-}"
    case "${value}" in
    '' | *[!0-9]*) die "${name} 必须是非负整数，当前是 '${value}'" ;;
    esac
    if [ "${value}" -lt "${min}" ]; then
        die "${name} 不能小于 ${min}（当前 ${value}）"
    fi
    if [ -n "${max}" ] && [ "${value}" -gt "${max}" ]; then
        die "${name} 不能大于 ${max}（当前 ${value}）"
    fi
}

require_ident() { # <键名> <值>：只允许 SQL 标识符，避免拼进命令行时出意外
    local name="$1" value="$2"
    case "${value}" in
    [A-Za-z_]*) ;;
    *) die "${name} 必须以字母或下划线开头，当前是 '${value}'" ;;
    esac
    case "${value}" in
    *[!A-Za-z0-9_]*) die "${name} 只能包含字母、数字和下划线，当前是 '${value}'" ;;
    esac
}

require_plain() { # <键名> <值>：compose 项目名，只允许常见安全字符
    local name="$1" value="$2"
    case "${value}" in
    '' | *[!A-Za-z0-9_.-]*) die "${name} 只能包含字母、数字、下划线、点和连字符，当前是 '${value}'" ;;
    esac
}

# 把 "6h" 解析成秒；失败返回非 0
parse_duration() {
    local v="$1" n mult=1
    case "${v}" in
    *[sS]) n="${v%?}" ;;
    *[mM]) n="${v%?}" mult=60 ;;
    *[hH]) n="${v%?}" mult=3600 ;;
    *[dD]) n="${v%?}" mult=86400 ;;
    *) n="${v}" ;;
    esac
    case "${n}" in
    '' | *[!0-9]*) return 1 ;;
    esac
    printf '%s\n' "$((n * mult))"
}

# 21600 -> 6h；用于把周期显示得好看一点
human_time() {
    local s="$1" d h m out=""
    d=$((s / 86400))
    s=$((s % 86400))
    h=$((s / 3600))
    s=$((s % 3600))
    m=$((s / 60))
    s=$((s % 60))
    if [ "${d}" -gt 0 ]; then out="${out}${d}d"; fi
    if [ "${h}" -gt 0 ]; then out="${out}${h}h"; fi
    if [ "${m}" -gt 0 ]; then out="${out}${m}m"; fi
    if [ "${s}" -gt 0 ]; then out="${out}${s}s"; fi
    printf '%s\n' "${out:-0s}"
}

# 1276480184 -> 1217.0 MiB
human_bytes() {
    local b="$1" i=0 dec=0 rem=0
    local units=(B KiB MiB GiB TiB)
    while [ "${b}" -ge 1024 ] && [ "${i}" -lt 4 ]; do
        rem=$((b % 1024))
        b=$((b / 1024))
        dec=$((rem * 10 / 1024))
        i=$((i + 1))
    done
    if [ "${i}" -eq 0 ]; then
        printf '%s B\n' "${b}"
    else
        printf '%s.%s %s\n' "${b}" "${dec}" "${units[$i]}"
    fi
}

validate_config() {
    command -v docker >/dev/null 2>&1 || die "找不到 docker 命令"
    [ -n "${BACKUP_DIR}" ] || die "BACKUP_DIR 不能为空"
    require_ident GIS_DB "${GIS_DB}"
    require_ident GIS_USER "${GIS_USER}"
    require_int RUN_ON_START "${RUN_ON_START}" 0 1
    require_int KEEP "${KEEP}" 0
    require_int EXCLUDE_EXTERNAL_DATA "${EXCLUDE_EXTERNAL_DATA}" 0 1
    require_int COMPRESS "${COMPRESS}" 0 9
    require_int DUMP_TIMEOUT "${DUMP_TIMEOUT}" 0
    require_int CHECKSUM "${CHECKSUM}" 0 1
    require_int LOG_MAX_BYTES "${LOG_MAX_BYTES}" 0
    require_plain PROJECT_TEST "${PROJECT_TEST}"
    require_plain PROJECT_PROD "${PROJECT_PROD}"

    INTERVAL_SECS="$(parse_duration "${INTERVAL}")" ||
        die "INTERVAL 无法解析：'${INTERVAL}'（支持 30s / 10m / 6h / 1d，或纯秒数）"
    if [ "${INTERVAL_SECS}" -lt 1 ]; then
        die "INTERVAL 至少 1 秒（当前解析为 ${INTERVAL_SECS} 秒）"
    fi

    case "$(canon_env "${ENV_NAME}")" in
    auto | test | prod) ;;
    *) die "ENV_NAME 只能是 auto / test / prod，当前是 '${ENV_NAME}'" ;;
    esac
}

canon_env() {
    case "${1:-}" in
    auto | AUTO) echo "auto" ;;
    test | dev | development) echo "test" ;;
    prod | production) echo "prod" ;;
    *) echo "?" ;;
    esac
}

# 每一轮循环都重新读一遍配置，这样改 INTERVAL 不用重启调度器
load_config() {
    config_defaults
    [ -f "${CONF_FILE}" ] || die "找不到配置文件 ${CONF_FILE}"
    if ! . "${CONF_FILE}"; then
        die "解析配置文件失败：${CONF_FILE}"
    fi
    validate_config
}

# 绝对化 BACKUP_DIR / LOG_FILE，并建好目录
prepare_dirs() {
    case "${BACKUP_DIR}" in
    /*) BACKUP_DIR_ABS="${BACKUP_DIR}" ;;
    *) BACKUP_DIR_ABS="${REPO_ROOT}/${BACKUP_DIR}" ;;
    esac
    TMP_DIR="${BACKUP_DIR_ABS}/.tmp"
    LOCK_FILE="${BACKUP_DIR_ABS}/.lock"
    PID_FILE="${BACKUP_DIR_ABS}/.scheduler.pid"
    mkdir -p "${TMP_DIR}"

    if [ -n "${LOG_FILE}" ]; then
        case "${LOG_FILE}" in
        /*) LOG_FILE_ABS="${LOG_FILE}" ;;
        *) LOG_FILE_ABS="${REPO_ROOT}/${LOG_FILE}" ;;
        esac
        mkdir -p "$(dirname "${LOG_FILE_ABS}")"
    else
        LOG_FILE_ABS=""
    fi
}

rotate_log() {
    [ -n "${LOG_FILE_ABS}" ] || return 0
    [ "${LOG_MAX_BYTES}" -gt 0 ] || return 0
    [ -f "${LOG_FILE_ABS}" ] || return 0
    local size
    size="$(wc -c <"${LOG_FILE_ABS}")"
    if [ "${size}" -gt "${LOG_MAX_BYTES}" ]; then
        mv -f "${LOG_FILE_ABS}" "${LOG_FILE_ABS}.1"
    fi
}

# --------------------------------------------------------------------------
# 找到要备份的数据库容器
#
# 两个环境用的是同一个宿主端口 54321，光看端口分不出测试还是生产；能区分的
# 只有 compose 项目名，所以这里按 compose 的 label 找在跑的 db 容器。
# --------------------------------------------------------------------------
db_containers() { # 输出 "项目名|容器ID|容器名"，一行一个
    docker ps --filter "label=com.docker.compose.service=db" \
        --format '{{.Label "com.docker.compose.project"}}|{{.ID}}|{{.Names}}' 2>/dev/null || true
}

# 项目名 -> 环境名（文件名里用）
project_label() {
    local project="$1"
    if [ "${project}" = "${PROJECT_TEST}" ]; then
        printf 'test\n'
    elif [ "${project}" = "${PROJECT_PROD}" ]; then
        printf 'prod\n'
    else
        printf '%s\n' "${project}" | tr -c 'A-Za-z0-9_.-' '-'
    fi
}

# 成功时设置 DB_PROJECT / DB_CONTAINER / TARGET_ENV，失败返回 1（不退出，
# 因为调度循环里「栈没起来」是正常情况，不该把循环打死）
resolve_env() {
    DB_PROJECT=""
    DB_CONTAINER=""
    TARGET_ENV=""

    local rows
    rows="$(db_containers)"
    if [ -z "${rows}" ]; then
        log ERROR "没有找到正在运行的数据库容器（栈还没起来？）"
        log ERROR "  先启动一个环境：./tools/switch-env.sh test   （或 prod）"
        return 1
    fi

    local wanted
    wanted="$(canon_env "${ENV_NAME}")"
    if [ "${wanted}" = "auto" ]; then
        local count
        count="$(printf '%s\n' "${rows}" | wc -l | tr -d ' ')"
        if [ "${count}" -gt 1 ]; then
            log ERROR "同时有 ${count} 个数据库容器在跑，无法自动判断。把 ENV_NAME 写死："
            printf '%s\n' "${rows}" | while IFS='|' read -r p _ n; do
                log ERROR "  项目 ${p} / 容器 ${n}  ->  ENV_NAME=$(project_label "${p}")"
            done
            return 1
        fi
    else
        local project
        if [ "${wanted}" = "prod" ]; then project="${PROJECT_PROD}"; else project="${PROJECT_TEST}"; fi
        rows="$(printf '%s\n' "${rows}" | grep -F "${project}|" || true)"
        if [ -z "${rows}" ]; then
            log ERROR "${wanted} 环境（compose 项目 ${project}）的数据库没有在运行。"
            log ERROR "  当前在跑的是："
            db_containers | while IFS='|' read -r p _ n; do
                log ERROR "    项目 ${p} / 容器 ${n}"
            done
            return 1
        fi
    fi

    IFS='|' read -r DB_PROJECT _ DB_CONTAINER <<<"${rows%%$'\n'*}"
    TARGET_ENV="$(project_label "${DB_PROJECT}")"
    return 0
}

# 等数据库真的能连（健康检查通过之前 pg_dump 会立刻失败）
wait_for_db() {
    local waited=0
    while :; do
        if docker exec "${DB_CONTAINER}" pg_isready -q -U "${GIS_USER}" -d "${GIS_DB}" >/dev/null 2>&1; then
            return 0
        fi
        if [ "${waited}" -ge "${DB_WAIT_TIMEOUT}" ]; then
            return 1
        fi
        sleep 2
        waited=$((waited + 2))
    done
}

# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------
# 备份文件按时间升序写进全局数组 BACKUPS
collect_backups() {
    BACKUPS=()
    local -a found=()
    shopt -s nullglob
    found=("${BACKUP_DIR_ABS}/${GIS_DB}-"*.dump)
    shopt -u nullglob
    if [ "${#found[@]}" -gt 0 ]; then
        mapfile -t BACKUPS < <(printf '%s\n' "${found[@]}" | LC_ALL=C sort)
    fi
}

# 打印归档目录（pg_restore -l）。
# 先用宿主 pg_restore：它直接读文件，最快；但版本低于 14 时读不了 archive 1.14，
# 那就退回容器里那个和 pg_dump 同版本的 pg_restore。
archive_list() {
    local f="$1" err="${TMP_DIR}/restore-list.err"
    : >"${err}"
    if command -v pg_restore >/dev/null 2>&1 && pg_restore -l "${f}" 2>"${err}"; then
        return 0
    fi
    if [ -n "${DB_CONTAINER}" ] &&
        docker exec -i "${DB_CONTAINER}" pg_restore -l <"${f}" 2>"${err}"; then
        return 0
    fi
    return 1
}

# 校验归档：pg_restore -l 能读出目录就算好
validate_archive() {
    local f="$1"
    if archive_list "${f}" >/dev/null; then
        return 0
    fi
    log ERROR "备份文件校验失败（pg_restore 读不出归档目录）："
    log_block ERROR <"${TMP_DIR}/restore-list.err"
    return 1
}

toc_count() {
    archive_list "$1" 2>/dev/null | grep -c ';' || true
}

# 真正干活：dump -> 校验 -> 改名 -> 校验和 -> 清理旧文件
do_backup() {
    if ! wait_for_db; then
        log ERROR "数据库 ${DB_CONTAINER} 在 ${DB_WAIT_TIMEOUT}s 内没有就绪，放弃这一轮"
        return 1
    fi

    local stamp base final partial
    stamp="$(date '+%Y%m%d-%H%M%S')"
    base="${GIS_DB}-${TARGET_ENV}-${stamp}"
    final="${BACKUP_DIR_ABS}/${base}.dump"
    partial="${TMP_DIR}/${base}.dump.partial"
    # 同一秒内连跑两次也不覆盖（极少见，但改名逻辑必须确定）
    local n=1
    while [ -e "${final}" ] || [ -e "${partial}" ]; do
        base="${GIS_DB}-${TARGET_ENV}-${stamp}-${n}"
        final="${BACKUP_DIR_ABS}/${base}.dump"
        partial="${TMP_DIR}/${base}.dump.partial"
        n=$((n + 1))
    done

    # 注意：--no-owner / --no-acl 对 -Fc 归档在**dump**这一侧是无效的（归档里仍
    # 存着 OWNER TO / GRANT），它们只在 pg_restore 那一侧生效。所以这里不加，
    # 而是在 `restore` 子命令打印的恢复命令里带上。
    local -a opts=(-Fc -Z "${COMPRESS}")
    if [ "${EXCLUDE_EXTERNAL_DATA}" = "1" ]; then
        local t
        for t in "${EXTERNAL_TABLES[@]}"; do
            opts+=(-T "${t}")
        done
    fi

    log INFO "开始备份 ${GIS_DB}@${DB_CONTAINER}（环境 ${TARGET_ENV}）"
    log INFO "  目标：${final}"

    local started ended rc=0
    started="$(date '+%s')"
    # 注意这里是**容器里的** pg_dump，见文件头的说明
    if [ "${DUMP_TIMEOUT}" -gt 0 ]; then
        timeout "${DUMP_TIMEOUT}" docker exec "${DB_CONTAINER}" \
            pg_dump -U "${GIS_USER}" -d "${GIS_DB}" "${opts[@]}" >"${partial}" || rc=$?
    else
        docker exec "${DB_CONTAINER}" \
            pg_dump -U "${GIS_USER}" -d "${GIS_DB}" "${opts[@]}" >"${partial}" || rc=$?
    fi
    ended="$(date '+%s')"

    if [ "${rc}" -eq 124 ]; then
        mv -f "${partial}" "${partial%.partial}.failed" 2>/dev/null || true
        log ERROR "pg_dump 超时（DUMP_TIMEOUT=${DUMP_TIMEOUT}s），半成品保留为 *.failed"
        return 1
    fi
    if [ "${rc}" -ne 0 ]; then
        mv -f "${partial}" "${partial%.partial}.failed" 2>/dev/null || true
        log ERROR "pg_dump 失败（exit ${rc}），半成品保留为 *.failed"
        return 1
    fi
    if ! validate_archive "${partial}"; then
        mv -f "${partial}" "${partial%.partial}.failed" 2>/dev/null || true
        log ERROR "半成品保留为 *.failed，请检查后删除"
        return 1
    fi

    # 只有校验通过才改名到正式文件，避免 *.dump 里混进坏文件
    mv -f "${partial}" "${final}"
    local size
    size="$(stat -c %s "${final}")"
    log INFO "完成：$(human_bytes "${size}")，用时 $((ended - started))s，共 $(toc_count "${final}") 个归档条目"

    if [ "${CHECKSUM}" = "1" ]; then
        (cd "${BACKUP_DIR_ABS}" && sha256sum "$(basename "${final}")" >"$(basename "${final}").sha256")
        log INFO "校验和：$(basename "${final}").sha256"
    fi

    prune_backups
    return 0
}

prune_backups() {
    if [ "${KEEP}" -le 0 ]; then
        log INFO "KEEP=0，保留全部备份（当前 $(collect_backups; printf '%s' "${#BACKUPS[@]}") 个）"
        return 0
    fi
    collect_backups
    local total="${#BACKUPS[@]}"
    if [ "${total}" -le "${KEEP}" ]; then
        log INFO "现有 ${total} 个备份，KEEP=${KEEP}，无需清理"
        return 0
    fi
    local remove=$((total - KEEP)) f
    for f in "${BACKUPS[@]}"; do
        if [ "${remove}" -le 0 ]; then break; fi
        log INFO "清理旧备份：$(basename "${f}")"
        rm -f -- "${f}" "${f}.sha256"
        remove=$((remove - 1))
    done
}

# 独占锁只在 dump 期间持有，所以调度器睡觉时手动 `now` 不会被挡；
# 反过来两个 dump 也不会撞在一起。
do_backup_locked() {
    (
        exec 9>"${LOCK_FILE}"
        if ! flock -n 9; then
            log WARN "已经有一个备份在跑，跳过这一轮"
            exit 75
        fi
        # 持有锁就说明没有别的 dump 在跑，那么 .partial 一定是上次失败留下的
        find "${TMP_DIR}" -maxdepth 1 -name '*.partial' -delete 2>/dev/null || true
        do_backup
    )
}

lock_is_held() {
    [ -f "${LOCK_FILE}" ] || return 1
    (
        exec 9>>"${LOCK_FILE}"
        if flock -n 9; then
            exit 1 # 拿到了 -> 没人在跑
        fi
        exit 0 # 没拿到 -> 有人在跑
    ) 2>/dev/null
}

scheduler_pid() {
    [ -f "${PID_FILE}" ] || return 1
    local pid
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    case "${pid}" in
    '' | *[!0-9]*) return 1 ;;
    esac
    kill -0 "${pid}" 2>/dev/null || return 1
    # 防止 PID 被复用认错进程
    grep -qs 'backup-db\.sh' "/proc/${pid}/cmdline" 2>/dev/null || return 1
    printf '%s\n' "${pid}"
}

# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
cmd_now() {
    LOG_TO_FILE=1
    load_config
    prepare_dirs
    rotate_log
    resolve_env || die "没有可备份的数据库"
    do_backup_locked || {
        local rc=$?
        if [ "${rc}" -eq 75 ]; then
            warn "已经有备份在跑，本次未执行"
            return 0
        fi
        die "备份失败，详见上面的日志"
    }
    say "备份目录"
    ls -lh "${BACKUP_DIR_ABS}"/*.dump 2>/dev/null | tail -5 || true
}

# 可被信号打断的 sleep：bash 在 wait 时收到信号会立刻返回并执行 trap
nap() {
    local secs="$1" pid
    sleep "${secs}" &
    pid=$!
    wait "${pid}" 2>/dev/null || true
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
}

cmd_run() {
    LOG_TO_FILE=1
    load_config
    prepare_dirs
    rotate_log

    local pid
    if pid="$(scheduler_pid)"; then
        die "调度器已经在跑了（pid ${pid}，${PID_FILE}）"
    fi
    printf '%s\n' "$$" >"${PID_FILE}"

    local stop=0
    trap 'stop=1' INT TERM HUP
    trap 'rm -f "${PID_FILE}"' EXIT

    log INFO "gis 定时备份已启动（pid $$）"
    log INFO "  周期：${INTERVAL}（$(human_time "${INTERVAL_SECS}")）"
    log INFO "  目录：${BACKUP_DIR_ABS}"
    log INFO "  配置：${CONF_FILE}"
    [ -n "${LOG_FILE_ABS}" ] && log INFO "  日志：${LOG_FILE_ABS}"
    log INFO "  停止：Ctrl-C，或 kill $$"

    local do_it="${RUN_ON_START}"
    while :; do
        if [ "${stop}" -eq 1 ]; then break; fi

        # 每一轮都重读配置：改 INTERVAL / KEEP / ENV_NAME 不用重启
        load_config
        prepare_dirs

        if [ "${do_it}" = "1" ]; then
            local rc=0
            resolve_env || rc=1
            if [ "${rc}" -eq 0 ]; then
                do_backup_locked || rc=$?
                case "${rc}" in
                0) ;;
                75) log WARN "跳过这一轮（已有备份在跑）" ;;
                *) log ERROR "这一轮备份失败（exit ${rc}），继续按周期重试" ;;
                esac
            fi
        fi
        do_it=1

        if [ "${stop}" -eq 1 ]; then break; fi
        log INFO "下一轮：$(date -d "+${INTERVAL_SECS} seconds" '+%Y-%m-%d %H:%M:%S')（${INTERVAL} 之后）"
        nap "${INTERVAL_SECS}"
    done

    log INFO "调度器已停止"
}

cmd_status() {
    load_config
    prepare_dirs

    say "备份配置（${CONF_FILE}）"
    info "周期 INTERVAL     : ${INTERVAL}（$(human_time "${INTERVAL_SECS}")）"
    info "启动时先备份      : RUN_ON_START=${RUN_ON_START}"
    info "保留策略          : KEEP=${KEEP}$([ "${KEEP}" -eq 0 ] && printf '（全部保留）' || printf '（保留最新 %s 个）' "${KEEP}")"
    info "排除外部数据      : EXCLUDE_EXTERNAL_DATA=${EXCLUDE_EXTERNAL_DATA}$([ "${EXCLUDE_EXTERNAL_DATA}" = "1" ] && printf '（dump 会小很多，恢复后需补跑 sync external-data）')"
    info "压缩 / 校验和     : -Z ${COMPRESS} / CHECKSUM=${CHECKSUM}"
    info "dump 超时         : DUMP_TIMEOUT=${DUMP_TIMEOUT}s"
    info "输出目录          : ${BACKUP_DIR_ABS}"
    info "日志文件          : ${LOG_FILE_ABS:-（不写文件）}"

    say "目标环境（ENV_NAME=${ENV_NAME}）"
    if resolve_env >/dev/null 2>&1; then
        info "compose 项目      : ${DB_PROJECT}"
        info "环境名            : ${TARGET_ENV}"
        info "数据库容器        : ${DB_CONTAINER}"
        local status size
        status="$(docker inspect -f '{{.State.Status}}{{if .State.Health}} / {{.State.Health.Status}}{{end}}' "${DB_CONTAINER}" 2>/dev/null || echo '?')"
        info "容器状态          : ${status}"
        size="$(docker exec "${DB_CONTAINER}" psql -U "${GIS_USER}" -d "${GIS_DB}" -tAc \
            'SELECT pg_size_pretty(pg_database_size(current_database()))' 2>/dev/null || true)"
        info "库 ${GIS_DB} 大小       : ${size:-?}（${GIS_USER}@${GIS_DB}）"
    else
        info "当前没有可备份的栈在运行"
        info "  两个环境都停了？先 ./tools/switch-env.sh test（或 prod）"
    fi

    say "已有备份"
    collect_backups
    local total="${#BACKUPS[@]}"
    if [ "${total}" -eq 0 ]; then
        info "还没有备份"
    else
        info "数量              : ${total} 个"
        info "占用              : $(du -sh "${BACKUP_DIR_ABS}" 2>/dev/null | cut -f1)"
        info "最新              : $(basename "${BACKUPS[$((total - 1))]}")  ($(stat -c %y "${BACKUPS[$((total - 1))]}" | cut -d. -f1))"
        info "最旧              : $(basename "${BACKUPS[0]}")  ($(stat -c %y "${BACKUPS[0]}" | cut -d. -f1))"
    fi

    say "调度器"
    local pid
    if pid="$(scheduler_pid)"; then
        info "正在运行          : pid ${pid}（${PID_FILE}）"
    elif lock_is_held; then
        info "没有调度器，但此刻有备份正在执行"
    else
        info "没有在运行"
    fi
    info "启动              : ./tools/backup-db.sh run"
    info "立刻备份一次      : ./tools/backup-db.sh now"
    if [ -f "${LOG_FILE_ABS}" ]; then
        info "最近日志          : tail -n 20 ${LOG_FILE_ABS#"${REPO_ROOT}/"}"
    fi
}

cmd_list() {
    load_config
    prepare_dirs
    collect_backups
    if [ "${#BACKUPS[@]}" -eq 0 ]; then
        info "还没有备份（目录 ${BACKUP_DIR_ABS}）"
        return 0
    fi
    info "目录：${BACKUP_DIR_ABS}"
    info "共 ${#BACKUPS[@]} 个，新 -> 旧："
    local -a list=()
    mapfile -t list < <(printf '%s\n' "${BACKUPS[@]}" | tac)
    local f
    for f in "${list[@]}"; do
        printf '  %-42s %12s  %s\n' \
            "$(basename "${f}")" \
            "$(human_bytes "$(stat -c %s "${f}")")" \
            "$(stat -c %y "${f}" | cut -d. -f1)"
    done
    local -a failed=()
    shopt -s nullglob
    failed=("${TMP_DIR}"/*.failed)
    shopt -u nullglob
    if [ "${#failed[@]}" -gt 0 ]; then
        say "注意"
        info "${TMP_DIR} 下有 ${#failed[@]} 个失败残留（*.failed），确认没用后删掉即可"
    fi
}

cmd_prune() {
    load_config
    prepare_dirs
    LOG_TO_FILE=1
    rotate_log
    collect_backups
    info "现有 ${#BACKUPS[@]} 个备份，KEEP=${KEEP}"
    prune_backups
    collect_backups
    info "清理后 ${#BACKUPS[@]} 个，占用 $(du -sh "${BACKUP_DIR_ABS}" 2>/dev/null | cut -f1)"
}

cmd_verify() {
    load_config
    prepare_dirs
    local f="${1:-}"
    [ -n "${f}" ] || die "用法：./tools/backup-db.sh verify <备份文件>"
    [ -f "${f}" ] || die "找不到文件：${f}"
    # 校验只用到 pg_restore，栈没在跑也能退化成宿主那个
    DB_CONTAINER=""
    TARGET_ENV="?"
    resolve_env >/dev/null 2>&1 || true
    say "校验 $(basename "${f}")"
    info "大小：$(human_bytes "$(stat -c %s "${f}")")"
    validate_archive "${f}" || die "这个备份不可用"
    info "归档目录可读，共 $(toc_count "${f}") 个条目"
    if [ -n "${DB_CONTAINER}" ]; then
        info "已用容器 ${DB_CONTAINER} 里的 pg_restore $(docker exec "${DB_CONTAINER}" pg_restore --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1) 校验"
    else
        info "已用宿主的 pg_restore 校验（当前没有栈在跑）"
    fi
    if [ -f "${f}.sha256" ]; then
        if (cd "$(dirname "${f}")" && sha256sum -c "$(basename "${f}").sha256" >/dev/null 2>&1); then
            info "sha256 校验通过"
        else
            die "sha256 不匹配，文件可能被改动过"
        fi
    fi
}

cmd_restore() {
    load_config
    prepare_dirs
    local f="${1:-}"
    [ -n "${f}" ] || die "用法：./tools/backup-db.sh restore <备份文件>"
    [ -f "${f}" ] || die "找不到文件：${f}"
    local ctr
    resolve_env >/dev/null 2>&1 || true
    ctr="${DB_CONTAINER:-<db 容器>}"
    local rel="${f#"${REPO_ROOT}/"}"
    cat <<EOF

恢复 $(basename "${f}") 的步骤（本命令只打印，不会动任何数据）

  0) 先确认这个归档是好的：
       ./tools/backup-db.sh verify ${rel}

  1) 停掉会写库的服务（渲染库是完整的、可重建的，直接重建最干净）：
       ./tools/switch-env.sh down
     （只想停一部分时用 ./tools/switch-env.sh restart … 里的服务名，或
       docker stop osm-sync-1 osm-tileserver-1）

  2) 重建一个空的 gis 库：
       docker exec ${ctr} psql -U ${GIS_USER} -d postgres -c 'DROP DATABASE IF EXISTS ${GIS_DB} WITH (FORCE);'
       docker exec ${ctr} psql -U ${GIS_USER} -d postgres -c 'CREATE DATABASE ${GIS_DB};'
       docker exec ${ctr} psql -U ${GIS_USER} -d ${GIS_DB} -c 'CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS hstore;'

  3) 恢复（用容器里的 pg_restore 14，与 dump 同版本）：
       docker exec -i ${ctr} pg_restore -U ${GIS_USER} -d ${GIS_DB} \\
         --no-owner --no-acl --exit-on-error < ${rel}

  4) 如果当初用的是 EXCLUDE_EXTERNAL_DATA=1，补回外部 shapefile：
       docker compose run --rm sync external-data

  5) 把栈拉起来：
       ./tools/switch-env.sh test    （或 prod）

  说明：pg_restore 会重建索引，1.2 GB 的归档大约需要几分钟；结束后
  ./tools/backup-db.sh status 应该能看到库大小恢复正常。
EOF
}

usage() {
    sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
command="${1:-status}"
[ "$#" -gt 0 ] && shift || true

case "${command}" in
status) cmd_status ;;
run | loop | daemon) cmd_run ;;
now | once | --now | -n) cmd_now ;;
list | ls) cmd_list ;;
prune | cleanup) cmd_prune ;;
verify) cmd_verify "$@" ;;
restore) cmd_restore "$@" ;;
help | -h | --help) usage ;;
*) die "未知命令 '${command}' —— 试试 ./tools/backup-db.sh help" ;;
esac
