#!/usr/bin/env bash
#
# Switch the combined openstreetmap-website + osm-carto4mc stack between
# the TEST environment and the PRODUCTION environment.
#
#   ./tools/switch-env.sh                    # same as `status`
#   ./tools/switch-env.sh status             # what is running, and where
#   ./tools/switch-env.sh test               # switch to the test stack
#   ./tools/switch-env.sh prod               # switch to the production stack
#   ./tools/switch-env.sh prod --init        # first time: db, admin, OAuth ids
#   ./tools/switch-env.sh restart <env>      # restart one environment
#   ./tools/switch-env.sh logs <env> [svc]   # follow logs
#   ./tools/switch-env.sh passwd <env> [user] [password]
#                                            # reset an account password
#                                            # (generated and printed if omitted)
#   ./tools/switch-env.sh down               # stop both environments
#
# `test` and `dev` are the same thing (the test stack runs Rails in the
# development environment); both spellings are accepted everywhere.
#
# Why switching rather than running both: they publish the same host ports
# (3000 edge / 3001 web / 8080 tiles / 54321 db), so each command stops the
# other stack first. Nothing is
# shared - production has its own PostgreSQL container and volume, its own
# render/sync/tile state under ./data/prod, and its own settings file at
# openstreetmap-website/config/settings/production.local.yml. Switching back and
# forth therefore never loses data.
#
# Environment variables:
#   WAIT_TIMEOUT   seconds to wait for the site to answer (default 600, because
#                  a production start precompiles assets and migrates the DB)
#   PROJECT_TEST   compose project name of the test stack       (default osm)
#   PROJECT_PROD   compose project name of the production stack (default osm-prod)
#
set -euo pipefail

cd "$(dirname "$0")/.."

# In sandboxed environments where $HOME is read-only the Docker CLI cannot write
# its state directory, so this repository keeps one inside the workspace.
if [ -z "${DOCKER_CONFIG:-}" ] && [ -d "${PWD}/.docker" ]; then
    export DOCKER_CONFIG="${PWD}/.docker"
fi

PROJECT_TEST="${PROJECT_TEST:-osm}"
PROJECT_PROD="${PROJECT_PROD:-osm-prod}"
ENV_FILE_TEST=".env"
ENV_FILE_PROD=".env.prod"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-600}"

say() { printf '\n=== %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# --- environment plumbing ---------------------------------------------------
# Canonicalise the user facing name: test/dev/development -> test, prod/... -> prod
canon_env() {
    case "${1:-}" in
    test | dev | development) echo "test" ;;
    prod | production) echo "prod" ;;
    *) die "unknown environment '${1:-}' (expected 'test' or 'prod')" ;;
    esac
}

env_file_for() { [ "$1" = "prod" ] && echo "${ENV_FILE_PROD}" || echo "${ENV_FILE_TEST}"; }

env_label_for() { [ "$1" = "prod" ] && echo "production" || echo "test"; }

# Run docker compose for one environment: ec <test|prod> <compose args...>
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

# Read KEY from an env file, falling back to a default.
read_setting() {
    local file="$1" key="$2" default="$3" value=""
    if [ -f "${file}" ]; then
        value="$(grep -E "^${key}=" "${file}" | tail -1 | cut -d= -f2- || true)"
    fi
    printf '%s\n' "${value:-${default}}"
}

# EDGE_PORT is the front door (reverse proxy: site + /tile/ + /tiles/); it is the
# only port that has to be forwarded or tunnelled. WEB_PORT is Rails' direct
# port, kept for debugging - tile URLs in the pages are root-relative, so a
# browser talking to Rails directly cannot load any tile.
edge_port_for() { read_setting "$(env_file_for "$1")" EDGE_PORT 3000; }
web_port_for() { read_setting "$(env_file_for "$1")" WEB_PORT 3001; }
tile_port_for() { read_setting "$(env_file_for "$1")" TILE_PORT 8080; }

# --- pre-flight -------------------------------------------------------------
preflight() {
    command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
    docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
    docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon (is it running?)"
    [ -f docker-compose.yml ] || die "docker-compose.yml not found - run me from the repository"
}

require_images() {
    local missing=()
    for image in osm-db:latest osm-web:latest osm-sync:latest osm-tileserver:latest; do
        docker image inspect "${image}" >/dev/null 2>&1 || missing+=("${image}")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        warn "missing images: ${missing[*]}"
        info "building them now (this can take a while the first time):"
        DOCKER_BUILDKIT=1 docker compose build sync tileserver
    fi
    # The edge proxy is the only service that runs a stock public image, so it is
    # not built here - pull it instead of letting `up` fail on a slow first run.
    if ! docker image inspect nginx:alpine >/dev/null 2>&1; then
        info "pulling nginx:alpine (the edge reverse proxy)"
        docker pull nginx:alpine
    fi
}

# .env.prod is gitignored because it holds SECRET_KEY_BASE; create it from the
# tracked template and generate the secret on first use.
ensure_prod_env() {
    if [ ! -f "${ENV_FILE_PROD}" ]; then
        [ -f "${ENV_FILE_PROD}.example" ] || die "${ENV_FILE_PROD}.example is missing"
        cp "${ENV_FILE_PROD}.example" "${ENV_FILE_PROD}"
        info "created ${ENV_FILE_PROD} from ${ENV_FILE_PROD}.example"
    fi
    if ! grep -qE '^SECRET_KEY_BASE=.+' "${ENV_FILE_PROD}"; then
        local secret
        secret="$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        if grep -qE '^SECRET_KEY_BASE=' "${ENV_FILE_PROD}"; then
            # -i is GNU specific, which is what Linux containers/hosts give us
            sed -i "s|^SECRET_KEY_BASE=.*|SECRET_KEY_BASE=${secret}|" "${ENV_FILE_PROD}"
        else
            printf 'SECRET_KEY_BASE=%s\n' "${secret}" >>"${ENV_FILE_PROD}"
        fi
        chmod 600 "${ENV_FILE_PROD}"
        info "generated SECRET_KEY_BASE into ${ENV_FILE_PROD}"
    fi
}

# --- readiness --------------------------------------------------------------
wait_for_web() {
    local env="$1" port url waited=0
    port="$(web_port_for "${env}")"
    url="http://localhost:${port}/api/0.6/capabilities"
    printf '  waiting for the %s stack to answer on %s ' "$(env_label_for "${env}")" "${url}"
    while :; do
        if curl -fsS -o /dev/null --max-time 3 "${url}" 2>/dev/null; then
            printf ' ok\n'
            return 0
        fi
        if [ "${waited}" -ge "${WAIT_TIMEOUT}" ]; then
            printf '\n'
            warn "no answer after ${WAIT_TIMEOUT}s"
            return 1
        fi
        printf '.'
        sleep 3
        waited=$((waited + 3))
    done
}

# --- reporting --------------------------------------------------------------
tile_status() {
    local env="$1" port code waited=0
    port="$(tile_port_for "${env}")"
    # The tile server comes up on its own schedule (it waits for the render
    # database to be bootstrapped), so give it a moment instead of reporting a
    # connection failure as a result.
    while :; do
        code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
            "http://localhost:${port}/tile/0/0/0.png" 2>/dev/null || true)"
        case "${code}" in
        200 | 404) break ;;
        esac
        if [ "${waited}" -ge 30 ]; then
            break
        fi
        sleep 3
        waited=$((waited + 3))
    done
    case "${code}" in
    200) echo "200 (rendering)" ;;
    404) echo "404 (tile server is up, render database not bootstrapped yet)" ;;
    "" | 000) echo "not answering yet" ;;
    *) echo "${code}" ;;
    esac
}

report() {
    local env="$1" wp tp ep
    wp="$(web_port_for "${env}")"
    tp="$(tile_port_for "${env}")"
    ep="$(edge_port_for "${env}")"
    say "$(env_label_for "${env}") stack"
    ec "${env}" ps --format '{{.Service}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
    info "入口  : http://localhost:${ep}/            <- 站点 / iD / 瓦片，内网穿透只需映射这一个端口"
    info "  /tile/{z}/{x}/{y}.png  osm-carto4mc 瓦片（renderd + mod_tile）"
    info "  /tiles/{z}/{x}/{y}.png Minecraft dyn2xyz 瓦片"
    info "web   : http://localhost:${wp}/            (Rails 直连；瓦片地址是相对路径，走这里地图没图)"
    info "tiles : http://localhost:${tp}/tile/{z}/{x}/{y}.png (tileserver 直连) -> $(tile_status "${env}")"
}

production_configured() {
    local settings="openstreetmap-website/config/settings/production.local.yml"
    [ -f "${settings}" ] && grep -qE '^id_application:\s*".+"' "${settings}"
}

# --- commands ---------------------------------------------------------------
stop_env() {
    local env="$1"
    if [ -n "$(ec "${env}" ps -q 2>/dev/null || true)" ]; then
        say "stopping the $(env_label_for "${env}") stack"
        ec "${env}" down || true
    fi
}

switch_to() {
    local env="$1" other
    other="$([ "${env}" = "prod" ] && echo "test" || echo "prod")"

    [ "${env}" = "prod" ] && ensure_prod_env
    preflight
    require_images

    # Both environments publish the same host ports, so the other one has to go
    # first. `down` is synchronous, so the ports are free when it returns.
    stop_env "${other}"

    say "starting the $(env_label_for "${env}") stack"
    ec "${env}" up -d

    if ! wait_for_web "${env}"; then
        report "${env}"
        die "the $(env_label_for "${env}") stack did not become ready - check:
       ./tools/switch-env.sh logs ${env}"
    fi
    report "${env}"
}

init_prod() {
    ensure_prod_env
    preflight
    require_images
    stop_env test

    say "starting the production database"
    ec prod up -d --wait db

    say "preparing the production database (rails db:prepare)"
    ec prod run --rm -T web bundle exec rails db:prepare

    say "creating the administrator and registering the OAuth identifiers"
    ec prod run --rm -T web bundle exec rails runner - <tools/prod-init.rb

    say "starting the rest of the production stack"
    ec prod up -d

    if wait_for_web prod; then
        report prod
    else
        report prod
        warn "the site is not answering yet; the first start has to precompile"
        warn "assets and migrate the database. Follow it with:"
        info "./tools/switch-env.sh logs prod web"
    fi

    cat <<EOF

Next steps

  * The render database (gis) is bootstrapped by the sync service on first
    start and takes a while (fonts are shared with the test stack, but the
    ~1 GB of Natural Earth / water shapefiles are not). Watch it with:

        ./tools/switch-env.sh logs prod sync

  * Set server_url / server_protocol for this instance in
    openstreetmap-website/config/settings/production.local.yml, then re-run
    ./tools/switch-env.sh prod --init so the OAuth redirect URI matches.
EOF
}

cmd_status() {
    preflight
    local env running
    for env in test prod; do
        running="$(ec "${env}" ps -q 2>/dev/null | wc -l | tr -d ' ')"
        if [ "${running}" = "0" ]; then
            say "$(env_label_for "${env}") stack - not running"
            info "start it with: ./tools/switch-env.sh ${env}"
            [ "${env}" = "prod" ] && ! production_configured &&
                info "not initialised yet - use './tools/switch-env.sh prod --init'"
        else
            report "${env}"
        fi
    done
    say "note"
    info "both stacks use the same host ports, so only one can run at a time"
}

cmd_logs() {
    local env
    env="$(canon_env "${1:-}")"
    shift || true
    ec "${env}" logs -f "$@"
}

cmd_restart() {
    local env
    env="$(canon_env "${1:-}")"
    # drop the environment name so the remaining arguments are compose services
    shift || true
    preflight
    if [ -z "$(ec "${env}" ps -q 2>/dev/null || true)" ]; then
        info "the $(env_label_for "${env}") stack is not running - starting it instead"
        switch_to "${env}"
        return
    fi

    say "restarting the $(env_label_for "${env}") stack"
    ec "${env}" restart "$@"
    wait_for_web "${env}" || die "the $(env_label_for "${env}") stack did not come back"
    report "${env}"
}

cmd_passwd() {
    local env name password generated=0
    env="$(canon_env "${1:-}")"
    name="${2:-admin}"
    password="${3:-}"
    preflight

    if [ -z "$(ec "${env}" ps -q 2>/dev/null || true)" ]; then
        die "the $(env_label_for "${env}") stack is not running - start it first:
       ./tools/switch-env.sh ${env}"
    fi

    if [ -z "${password}" ]; then
        password="$(head -c 15 /dev/urandom | base64 | tr -d '/+=' | head -c 18)"
        generated=1
    fi

    say "resetting the password of '${name}' in the $(env_label_for "${env}") environment"
    ec "${env}" run --rm -T \
        -e ADMIN_NAME="${name}" -e ADMIN_PASSWORD="${password}" \
        web bundle exec rails runner - <tools/reset-password.rb

    if [ "${generated}" = "1" ]; then
        cat <<EOF

    ======================================================================
    New password for '${name}' (shown once, store it now):

      ${password}

    ======================================================================
EOF
    fi
}

cmd_down() {
    preflight
    stop_env test
    stop_env prod
    say "both stacks are stopped (data and volumes are untouched)"
}

usage() {
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
}

# --- entry point ------------------------------------------------------------
command="${1:-status}"
[ "$#" -gt 0 ] && shift || true

case "${command}" in
status) cmd_status ;;
test | dev | development) switch_to test ;;
prod | production)
    case "${1:-}" in
    --init) init_prod ;;
    "") switch_to prod ;;
    *) die "unknown option '${1}' for 'prod' (expected --init)" ;;
    esac
    ;;
logs) cmd_logs "$@" ;;
restart) cmd_restart "$@" ;;
passwd) cmd_passwd "$@" ;;
down | stop) cmd_down ;;
help | -h | --help) usage ;;
*) die "unknown command '${command}' - try './tools/switch-env.sh help'" ;;
esac
