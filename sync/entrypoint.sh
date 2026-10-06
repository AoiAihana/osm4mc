#!/bin/bash
#
# Entry point for the synchronisation service.
#
# With no arguments it runs the sync loop (bootstrapping the render database on
# first start). Any arguments are passed straight through to osm_sync.py, so
# one-off commands work too:
#
#   docker compose run --rm sync status
#   docker compose run --rm sync bootstrap
#   docker compose run --rm sync update
#
set -euo pipefail

log() { printf '[sync] %s\n' "$*"; }

wait_for_database() {
    local host="${PGHOST:-db}"
    local port="${PGPORT:-5432}"
    local user="${GIS_USER:-postgres}"
    for _ in $(seq 1 60); do
        if pg_isready -h "${host}" -p "${port}" -U "${user}" >/dev/null 2>&1; then
            log "database at ${host}:${port} is ready"
            return 0
        fi
        sleep 2
    done
    log "FATAL: database at ${host}:${port} never became ready"
    return 1
}

wait_for_database

if [ "$#" -gt 0 ]; then
    exec python3 /usr/local/bin/osm_sync.py "$@"
fi

exec python3 /usr/local/bin/osm_sync.py loop
