FROM ubuntu:noble

ARG DEBIAN_FRONTEND=noninteractive

# osm2pgsql imports the API database export into the render database and then
# applies changes incrementally. The python/gdal packages are what
# osm-carto4mc's scripts/get-external-data.py and get-fonts.py need.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        osm2pgsql \
        postgresql-client \
        python3 \
        python3-psycopg2 \
        python3-yaml \
        python3-requests \
        gdal-bin \
        curl \
        unzip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY osm_sync.py /usr/local/bin/osm_sync.py
COPY reliable_run.py /usr/local/bin/reliable_run.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/osm_sync.py /usr/local/bin/reliable_run.py /usr/local/bin/entrypoint.sh

WORKDIR /osm-carto4mc

# ENTRYPOINT rather than CMD, so that arguments are forwarded to osm_sync.py:
#   docker compose run --rm sync status
# With no arguments at all the entry point runs the sync loop.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
