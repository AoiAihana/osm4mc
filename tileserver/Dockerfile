FROM ubuntu:noble

ARG DEBIAN_FRONTEND=noninteractive

# renderd renders the tiles, mod_tile (an Apache module) serves and caches them.
# carto compiles osm-carto4mc's project.mml into the Mapnik XML that
# renderd loads.
#
# Fonts: osm-carto4mc asks for Noto faces. Mapnik refuses to load the
# style at all if not a single requested face can be resolved, so the Noto
# packages from the distribution are installed here as a guaranteed baseline.
# The sync service additionally downloads the exact set the project expects
# into ./data/fonts (mounted under /usr/share/fonts/truetype/osm-carto), which
# only improves script coverage on top of this.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apache2 \
        libapache2-mod-tile \
        renderd \
        nodejs \
        npm \
        ca-certificates \
        fonts-noto-core \
        fonts-noto-extra \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-noto-unhinted \
        fonts-dejavu-core \
        fonts-dejavu-extra \
        fonts-unifont \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# carto is the CartoCSS -> Mapnik XML compiler used by osm-carto4mc.
# Kept in its own layer, directly above, so that unrelated tweaks to the apt
# list above do not force a re-download from the npm registry (which is slow and
# not always reachable).
RUN npm install -g carto@1.2.0 \
    && npm cache clean --force \
    && rm -rf /root/.npm

# Only needed by the entrypoint, to wait for the sync service to finish
# bootstrapping the render database before renderd loads the style.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod tile headers \
    && mkdir -p /var/lib/mod_tile /run/renderd /data

COPY renderd.conf /etc/renderd.conf
COPY apache-tiles.conf /etc/apache2/sites-available/000-default.conf
COPY ports.conf /etc/apache2/ports.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080

CMD ["/usr/local/bin/entrypoint.sh"]
