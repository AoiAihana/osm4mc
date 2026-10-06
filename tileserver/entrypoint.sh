#!/bin/bash
#
# Starts the osm-carto4mc tile server:
#   1. compile project.mml (CartoCSS) into a Mapnik XML stylesheet with carto
#   2. start renderd, which renders tiles from the shared `gis` database
#   3. start Apache with mod_tile, which caches and serves the tiles
#
set -euo pipefail

log() { printf '[tileserver] %s\n' "$*"; }

CARTODIR="${CARTODIR:-/osm-carto4mc}"
MAPNIK_XML="${MAPNIK_XML:-/data/mapnik.xml}"
RENDERD_THREADS="${RENDERD_THREADS:-4}"
# Where ./data/fonts is mounted; osm-carto4mc refuses to load its style if
# Mapnik cannot find a single font, so this must be populated before renderd
# starts. The sync service downloads them during bootstrap.
FONT_DIR="${FONT_DIR:-/usr/share/fonts/truetype/osm-carto}"
FONT_WAIT_TIMEOUT="${FONT_WAIT_TIMEOUT:-1800}"
# How long to wait for the sync service to finish bootstrapping the render
# database before starting renderd anyway.
RENDER_WAIT_TIMEOUT="${RENDER_WAIT_TIMEOUT:-1800}"

mkdir -p "$(dirname "${MAPNIK_XML}")" /var/lib/mod_tile /run/renderd

if [ ! -f "${CARTODIR}/project.mml" ]; then
    log "FATAL: ${CARTODIR}/project.mml not found - is osm-carto4mc mounted?"
    exit 1
fi

# --- 0. wait until Mapnik will be able to build the style's FontSet ---------
# osm-carto4mc asks for Noto faces and Mapnik aborts with "no valid fonts
# could be loaded in FontSet" when it cannot resolve a single one of them. The
# image therefore ships the distribution's Noto packages as a guaranteed
# baseline, and the sync service additionally downloads the exact set the
# project expects into FONT_DIR. Only block while neither is available.
downloaded_font_count() {
    find "${FONT_DIR}" -maxdepth 3 -type f \( -name '*.ttf' -o -name '*.otf' \) \
        2>/dev/null | wc -l
}

base_face_available() {
    find "${FONT_DIR}" /usr/share/fonts -type f -iname 'NotoSans-Regular*' \
        2>/dev/null | grep -q .
}

wait_for_fonts() {
    local waited=0
    while :; do
        if base_face_available; then
            log "usable fonts available (${FONT_DIR}: $(downloaded_font_count) file(s)" \
                "downloaded by sync, plus the distribution Noto packages)"
            return 0
        fi
        if [ "${waited}" -ge "${FONT_WAIT_TIMEOUT}" ]; then
            log "WARNING: no usable Noto font after ${FONT_WAIT_TIMEOUT}s." \
                "renderd will very likely fail to load the style."
            return 1
        fi
        if [ $((waited % 60)) -eq 0 ]; then
            log "waiting for fonts in ${FONT_DIR} (downloaded by the sync service" \
                "during bootstrap), ${waited}s elapsed ..."
        fi
        sleep 10
        waited=$((waited + 10))
    done
}

wait_for_fonts || true

# --- 1. compile the CartoCSS style into Mapnik XML -------------------------
log "compiling ${CARTODIR}/project.mml -> ${MAPNIK_XML}"
carto_tmp="${MAPNIK_XML}.tmp.$$"
carto_err="$(mktemp)"
if ! carto "${CARTODIR}/project.mml" >"${carto_tmp}" 2>"${carto_err}"; then
    log "carto failed:"
    sed 's/^/    /' "${carto_err}" >&2
    rm -f "${carto_tmp}" "${carto_err}"
    exit 1
fi
rm -f "${carto_err}"
mv "${carto_tmp}" "${MAPNIK_XML}"
log "stylesheet ready: ${MAPNIK_XML} ($(stat -c %s "${MAPNIK_XML}") bytes)"

# carto always emits resource paths relative to project.mml (symbols/...,
# patterns/...) and Mapnik resolves those against the directory the stylesheet
# itself lives in. Since we compile into $(dirname MAPNIK_XML) rather than into
# the checkout, mirror each referenced top level directory next to it - without
# this every tile fails with "file could not be found: '/data/symbols/...'".
style_dir="$(cd "$(dirname "${MAPNIK_XML}")" && pwd)"
grep -o 'file="[A-Za-z0-9_][^"/]*/' "${MAPNIK_XML}" 2>/dev/null \
    | sed 's/^file="//; s:/$::' | sort -u | while read -r resource_dir; do
        if [ -d "${CARTODIR}/${resource_dir}" ]; then
            ln -sfn "${CARTODIR}/${resource_dir}" "${style_dir}/${resource_dir}"
            log "linked ${style_dir}/${resource_dir} -> ${CARTODIR}/${resource_dir}"
        fi
    done

# --- 2. wait until the render database is actually usable ------------------
# renderd loads the Mapnik stylesheet exactly once, at startup, and Mapnik
# validates every layer's datasource while doing so. If the sync service has
# not finished importing yet, a layer like icesheet_polygons or water_polygons
# is still missing, the map fails to load, and *every* tile request returns a
# plain Apache 404 for the lifetime of the container - silently, since renderd
# itself stays up. So block until the bootstrap is recorded as complete.
#
# A database that has no osm_sync_state table at all was built by hand (no sync
# service), and only needs the planet_osm tables.
render_db_ready() {
    psql -tAc "
        SELECT to_regclass('public.planet_osm_point') IS NOT NULL
           AND (to_regclass('public.osm_sync_state') IS NULL
                OR EXISTS (SELECT 1 FROM public.osm_sync_state
                            WHERE bootstrap_ts IS NOT NULL))
    " 2>/dev/null | grep -q '^t$'
}

wait_for_render_db() {
    local waited=0
    while :; do
        if render_db_ready; then
            log "render database is ready (bootstrap complete)"
            return 0
        fi
        if [ "${waited}" -ge "${RENDER_WAIT_TIMEOUT}" ]; then
            log "WARNING: render database not ready after ${RENDER_WAIT_TIMEOUT}s;" \
                "tiles will 404 until the sync service has bootstrapped it and" \
                "this container is restarted."
            return 1
        fi
        if [ $((waited % 60)) -eq 0 ]; then
            log "waiting for the sync service to finish bootstrapping the render" \
                "database, ${waited}s elapsed ..."
        fi
        sleep 10
        waited=$((waited + 10))
    done
}

wait_for_render_db || true

# --- 3. renderd ------------------------------------------------------------
if [ "${RENDERD_THREADS}" != "4" ]; then
    sed -i "s/^num_threads=.*/num_threads=${RENDERD_THREADS}/" /etc/renderd.conf
fi

log "starting renderd"
renderd -c /etc/renderd.conf -f &
renderd_pid=$!

for _ in $(seq 1 60); do
    [ -S /run/renderd/renderd.sock ] && break
    if ! kill -0 "${renderd_pid}" 2>/dev/null; then
        log "FATAL: renderd exited during startup"
        exit 1
    fi
    sleep 0.5
done

if [ -S /run/renderd/renderd.sock ]; then
    # Apache runs as www-data and has to be able to write to the socket, which
    # renderd (running as root here) created with restrictive permissions.
    chmod 0777 /run/renderd/renderd.sock || true
    log "renderd is listening on /run/renderd/renderd.sock"
else
    log "WARNING: renderd socket did not appear within 30s"
fi

# --- 4. apache + mod_tile -------------------------------------------------
log "starting apache and mod_tile on port 8080"
exec apache2ctl -D FOREGROUND
