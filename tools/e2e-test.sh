#!/usr/bin/env bash
#
# End-to-end check of the combined stack:
#
#   OSM API (what iD uses)  ->  openstreetmap db  ->  osm_sync  ->  gis db  ->  tiles
#
# It creates a real changeset and node through the public API - exactly the code
# path the built-in iD editor uses - then waits for the sync service to push the
# edit into the carto render database and confirms the tile covering the new
# node can be rendered.
#
# Usage:
#   TOKEN=<oauth2 access token> ./tools/e2e-test.sh
#   ./tools/e2e-test.sh --cleanup          # delete the test node again
#
# Creating a token (development instances only):
#   docker compose exec -T web bundle exec rails runner - <<'RUBY'
#   app = Oauth2Application.find_by(uid: Settings.id_application)
#   user = User.find_by(display_name: "mapper")
#   t = Doorkeeper::AccessToken.create!(application: app, resource_owner_id: user.id,
#         scopes: "write_api read_prefs", expires_in: 3600)
#   puts t.token
#   RUBY
#
set -euo pipefail

cd "$(dirname "$0")/.."

# In sandboxed environments where $HOME is read-only the Docker CLI cannot write
# its state directory, so this repository keeps one inside the workspace.
if [ -z "${DOCKER_CONFIG:-}" ] && [ -d "${PWD}/.docker" ]; then
    export DOCKER_CONFIG="${PWD}/.docker"
fi

WEB="${WEB:-http://localhost:3000}"
# Both go through the `edge` reverse proxy on purpose: that is the address the
# browser actually uses, so the test covers the proxy as well. Use
# TILE=http://localhost:8080/tile to test the tile server on its own.
TILE="${TILE:-http://localhost:3000/tile}"
TOKEN="${TOKEN:-}"

LAT="${LAT:-0.0010000}"
LON="${LON:-0.0010000}"

# Which stack to inspect: OSM_ENV=dev (default) or OSM_ENV=prod.
# Both environments use the same host ports, so WEB/TILE above apply to either.
OSM_ENV="${OSM_ENV:-dev}"
if [ "${OSM_ENV}" = "prod" ]; then
    dc() {
        docker compose -p osm-prod --env-file .env.prod \
            -f docker-compose.yml -f docker-compose.prod.yml "$@"
    }
else
    dc() { docker compose -p osm "$@"; }
fi

say() { printf '\n=== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

api() {
    local method="$1" path="$2" data="${3:-}"
    if [ -n "$data" ]; then
        curl -sS -X "$method" -H "Authorization: Bearer ${TOKEN}" \
             --data-binary "$data" "${WEB}${path}"
    else
        curl -sS -X "$method" -H "Authorization: Bearer ${TOKEN}" "${WEB}${path}"
    fi
}

# Extract the (first) value of an XML attribute from stdin.
xattr() {
    python3 -c '
import re, sys
attr = sys.argv[1]
text = sys.stdin.read()
m = re.search(r"\b%s=\"([^\"]*)\"" % re.escape(attr), text)
print(m.group(1) if m else "")
' "$1"
}

# The object creation endpoints (changeset/create, node/create, ...) return the
# new id as plain text, not as XML.
created_id() {
    tr -d '[:space:]' | grep -E '^[0-9]+$'
}

[ -n "${TOKEN}" ] || die "set TOKEN to an OAuth2 access token (see the header of this script)"

# ---------------------------------------------------------------------------
if [ "${1:-}" = "--cleanup" ]; then
    say "cleanup: deleting the e2e test node"
    node_id="$(dc exec -T db psql -U postgres -d openstreetmap -tAc \
        "select node_id from current_nodes c join current_node_tags t on t.node_id = c.id
          where t.k = 'created_by' and t.v = 'e2e-test' limit 1")"
    [ -n "${node_id}" ] || die "no e2e test node found"
    version="$(dc exec -T db psql -U postgres -d openstreetmap -tAc \
        "select version from current_nodes where id = ${node_id}")"
    cs="$(api PUT "/api/0.6/changeset/create" \
        '<osm><changeset><tag k="comment" v="e2e cleanup"/><tag k="created_by" v="e2e-test"/></changeset></osm>' | created_id)"
    api PUT "/api/0.6/node/${node_id}" \
        "<osm><node id=\"${node_id}\" lat=\"${LAT}\" lon=\"${LON}\" version=\"${version}\" changeset=\"${cs}\" visible=\"false\"/></osm>" >/dev/null
    api PUT "/api/0.6/changeset/${cs}/close" >/dev/null
    echo "deleted node ${node_id} in changeset ${cs}; the sync service will remove it from gis"
    exit 0
fi

# ---------------------------------------------------------------------------
say "0. checking the stack is up"
curl -sS -o /dev/null "${WEB}/api/0.6/capabilities" || die "web/API not reachable at ${WEB}"
dc ps --format '{{.Service}}\t{{.Status}}'

say "1. authenticated as"
api GET "/api/0.6/user/details" | xattr display_name

say "2. creating a changeset (this is what iD does on save)"
changeset="$(api PUT "/api/0.6/changeset/create" \
    '<osm><changeset><tag k="comment" v="e2e sync test"/><tag k="created_by" v="e2e-test"/></changeset></osm>' | created_id)"
[ -n "${changeset}" ] || die "could not create a changeset"
echo "changeset ${changeset}"

say "3. creating a node at ${LAT},${LON} inside it"
node_xml="<osm><node lat=\"${LAT}\" lon=\"${LON}\" changeset=\"${changeset}\" version=\"0\">
  <tag k=\"amenity\" v=\"pub\"/>
  <tag k=\"name\" v=\"e2e sync test\"/>
  <tag k=\"created_by\" v=\"e2e-test\"/>
</node></osm>"
node="$(api PUT "/api/0.6/node/create" "${node_xml}" | created_id)"
[ -n "${node}" ] || die "could not create the node"
echo "node ${node}"

say "4. closing the changeset (this is what makes the edit visible to the sync)"
api PUT "/api/0.6/changeset/${changeset}/close" >/dev/null
echo "closed"

say "5. waiting for the sync service to push it into the gis database"
found=0
for _ in $(seq 1 90); do
    count="$(dc exec -T db psql -U postgres -d gis -tAc \
        "select count(*) from planet_osm_point where osm_id = ${node}" | tr -d '[:space:]')"
    if [ "${count}" = "1" ]; then found=1; break; fi
    sleep 2
done
if [ "${found}" = "1" ]; then
    echo "planet_osm_point now contains osm_id ${node}:"
    dc exec -T db psql -U postgres -d gis -c \
        "select osm_id, amenity, name from planet_osm_point where osm_id = ${node}"
else
    die "node ${node} did not reach the gis database - check 'docker compose logs sync'"
fi

say "6. rendering the tile that covers the new node"
coords="$(python3 - "${LAT}" "${LON}" <<'PY'
import math, sys
lat, lon = float(sys.argv[1]), float(sys.argv[2])
z = 16
n = 2 ** z
x = int((lon + 180.0) / 360.0 * n)
y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
print(f"{z} {x} {y}")
PY
)"
z="$(echo "${coords}" | cut -d' ' -f1)"
x="$(echo "${coords}" | cut -d' ' -f2)"
y="$(echo "${coords}" | cut -d' ' -f3)"
echo "tile z=${z} x=${x} y=${y}"
code="$(curl -sS -o /tmp/osm-e2e-tile.png -w '%{http_code}' "${TILE}/${z}/${x}/${y}.png")"
size="$(stat -c %s /tmp/osm-e2e-tile.png)"
echo "HTTP ${code}, ${size} bytes -> /tmp/osm-e2e-tile.png"
[ "${code}" = "200" ] || die "tile server returned ${code}"

say "done"
echo "The edit made through the API/iD is now visible in the carto render database and in the rendered tile."
echo "Run '$0 --cleanup' to remove the test node again."
