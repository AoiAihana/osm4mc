#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osm_sync.py
===========

Keeps the osm2pgsql rendering database used by **osm-carto4mc** in step
with the **openstreetmap-website** API database.

The API database (the rails port "apidb" schema: ``nodes``/``ways``/``relations``
history tables plus the ``current_*`` tables) is the single source of truth,
because that is where the built-in iD editor writes its edits.  This program
exports OSM XML from it and feeds ``osm2pgsql``, which populates the
``planet_osm_{point,line,polygon,roads}`` tables that osm-carto4mc
renders.

Both databases live inside the *same* PostgreSQL/PostGIS instance, so the two
projects really do share one database server and one data volume - this script
is what keeps the two logical databases on the same page.

Commands
--------
``ensure-gis``
    Create the render database, its extensions and the sync bookkeeping table.
``bootstrap``
    Full export from the API database followed by the initial osm2pgsql import,
    rendering indexes/functions, external shapefiles and fonts.
``export``
    Only write the OSM XML files (useful for debugging).
``update``
    Export and apply one batch of changes.
``loop``
    Run ``update`` forever, sleeping ``SYNC_INTERVAL`` seconds in between.
``status``
    Print the current sync watermark.
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2

log = logging.getLogger("osm-sync")

# ---------------------------------------------------------------------------
# Schema notes (rails port "apidb" schema)
# ---------------------------------------------------------------------------
# History tables hold every version of every element:
#   nodes(node_id, latitude, longitude, changeset_id, visible,
#         "timestamp", tile, version, redaction_id)
#   ways(way_id, changeset_id, "timestamp", version, visible, redaction_id)
#   relations(relation_id, changeset_id, "timestamp", version, visible,
#             redaction_id)
# Versioned tags:
#   node_tags(node_id, version, k, v), way_tags(way_id, k, v, version),
#   relation_tags(relation_id, k, v, version)
# Versioned members:
#   way_nodes(way_id, node_id, version, sequence_id)
#   relation_members(relation_id, member_type, member_id, member_role,
#                    version, sequence_id)
#
# Current tables (what the API/iD actually read and write):
#   current_nodes(id, latitude, longitude, changeset_id, visible,
#                 "timestamp", tile, version)
#   current_ways(id, changeset_id, "timestamp", visible, version)
#   current_relations(id, changeset_id, "timestamp", visible, version)
#   current_node_tags(node_id, k, v), current_way_tags(way_id, k, v),
#   current_relation_tags(relation_id, k, v)
#   current_way_nodes(way_id, node_id, sequence_id)
#   current_relation_members(relation_id, member_type, member_id,
#                            member_role, sequence_id)
#
# changesets.closed_at is set to a time in the FUTURE while a changeset is open
# (see Changeset#update_closed_at), and to the real close time once closed.
# Therefore ``closed_at <= now()`` selects exactly the closed changesets, which
# is what we want: an edit becomes visible on the map once its changeset closes.

ELEMENT_KINDS = ("node", "way", "relation")

# XML ids/versions are plain integers coming from the database; only tag keys,
# values and roles are free text and need escaping.
_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


def esc(value) -> str:
    text = "" if value is None else str(value)
    for needle, replacement in _ESCAPES:
        if needle in text:
            text = text.replace(needle, replacement)
    return text


def iso(ts) -> str:
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------
def run(cmd, cwd=None, env=None, check=True, label=None):
    log.info("running: %s", " ".join(shlex.quote(str(c)) for c in cmd))
    merged = dict(os.environ)
    if env:
        merged.update(env)
    proc = subprocess.run(cmd, cwd=cwd, env=merged)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{label or cmd[0]} failed with exit code {proc.returncode}")
    return proc.returncode


def psql_script(args, path):
    cmd = [
        "psql", "-v", "ON_ERROR_STOP=1",
        "-h", args.host, "-p", str(args.port),
        "-U", args.gis_user, "-d", args.gis_db,
        "-f", path,
    ]
    run(cmd)


# ---------------------------------------------------------------------------
# Database connections
# ---------------------------------------------------------------------------
def connect(dbname, user, password=None, host="db", port=5432):
    return psycopg2.connect(
        dbname=dbname, user=user, password=password or None,
        host=host, port=port,
    )


def api_conn(args):
    return connect(args.api_db, args.api_user, args.api_password, args.host, args.port)


def gis_conn(args):
    return connect(args.gis_db, args.gis_user, None, args.host, args.port)


def ensure_gis(args):
    """Create the render database, extensions and bookkeeping table if missing."""
    admin = connect("postgres", args.gis_user, None, args.host, args.port)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.gis_db,))
            if cur.fetchone() is None:
                log.info("creating render database %s", args.gis_db)
                cur.execute(f'CREATE DATABASE "{args.gis_db}"')
            else:
                log.info("render database %s already exists", args.gis_db)
    finally:
        admin.close()

    conn = gis_conn(args)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            cur.execute("CREATE EXTENSION IF NOT EXISTS hstore")
            # osm-carto4mc asks for JIT to be off; scope it to this db.
            cur.execute(f'ALTER DATABASE "{args.gis_db}" SET jit = off')
            cur.execute(
                f'ALTER DATABASE "{args.gis_db}" SET work_mem = %s',
                (os.environ.get("PG_WORK_MEM", "16MB"),),
            )
            cur.execute(
                f'ALTER DATABASE "{args.gis_db}" SET maintenance_work_mem = %s',
                (os.environ.get("PG_MAINTENANCE_WORK_MEM", "256MB"),),
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.osm_sync_state (
                    id             boolean PRIMARY KEY DEFAULT true CHECK (id),
                    watermark_ts   timestamp without time zone,
                    watermark_id   bigint,
                    sequence       bigint NOT NULL DEFAULT 0,
                    bootstrap_ts   timestamp without time zone,
                    updated_at     timestamp without time zone
                )
                """
            )
            cur.execute(
                "INSERT INTO public.osm_sync_state (id) VALUES (true) "
                "ON CONFLICT (id) DO NOTHING"
            )
    finally:
        conn.close()


@contextmanager
def db_cursor(conn):
    """Yield a cursor for `conn`, then commit and close the connection.

    psycopg2's own connection context manager only commits (or rolls back) - it
    does *not* close the connection. Using it directly in the sync loop would
    leak one connection per iteration until PostgreSQL ran out of slots.
    """
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_state(args):
    with db_cursor(gis_conn(args)) as cur:
        cur.execute(
            "SELECT watermark_ts, watermark_id, sequence, bootstrap_ts "
            "FROM public.osm_sync_state WHERE id"
        )
        row = cur.fetchone()
    if row is None:
        return None, None, 0, None
    return row


def set_state(args, watermark_ts, watermark_id, sequence, bootstrap_ts=None):
    with db_cursor(gis_conn(args)) as cur:
        if bootstrap_ts is None:
            cur.execute(
                """
                UPDATE public.osm_sync_state
                   SET watermark_ts = %s, watermark_id = %s, sequence = %s,
                       updated_at = %s
                 WHERE id
                """,
                (watermark_ts, watermark_id, sequence, utcnow()),
            )
        else:
            cur.execute(
                """
                UPDATE public.osm_sync_state
                   SET watermark_ts = %s, watermark_id = %s, sequence = %s,
                       bootstrap_ts = %s, updated_at = %s
                 WHERE id
                """,
                (watermark_ts, watermark_id, sequence, bootstrap_ts, utcnow()),
            )


def gis_is_initialised(args) -> bool:
    try:
        with db_cursor(gis_conn(args)) as cur:
            cur.execute("SELECT to_regclass('public.planet_osm_point') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except psycopg2.Error as exc:
        log.warning("could not inspect render database: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------
def tags_by_version(conn, table, id_column, ids):
    """Return {(id, version): [(k, v), ...]} for the given element ids."""
    if not ids:
        return {}
    sql = f"SELECT {id_column}, version, k, v FROM {table} WHERE {id_column} = ANY(%s)"
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for eid, version, key, value in cur:
            out.setdefault((eid, version), []).append((key, value))
    return out


def current_tags(conn, table, id_column, ids):
    """Return {id: [(k, v), ...]} from one of the current_*_tags tables."""
    if not ids:
        return {}
    sql = f"SELECT {id_column}, k, v FROM {table} WHERE {id_column} = ANY(%s)"
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for eid, key, value in cur:
            out.setdefault(eid, []).append((key, value))
    return out


def refs_by_version(conn, table, id_column, ids):
    """Way node references, keyed by (way_id, version)."""
    if not ids:
        return {}
    sql = (
        f"SELECT {id_column}, version, node_id FROM {table} "
        f"WHERE {id_column} = ANY(%s) ORDER BY {id_column}, version, sequence_id"
    )
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for eid, version, node_id in cur:
            out.setdefault((eid, version), []).append(node_id)
    return out


def current_refs(conn, table, id_column, ids):
    """Current way node references, keyed by way_id."""
    if not ids:
        return {}
    sql = (
        f"SELECT {id_column}, node_id FROM {table} "
        f"WHERE {id_column} = ANY(%s) ORDER BY {id_column}, sequence_id"
    )
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for eid, node_id in cur:
            out.setdefault(eid, []).append(node_id)
    return out


def members_by_version(conn, ids):
    if not ids:
        return {}
    sql = (
        "SELECT relation_id, version, member_type, member_id, member_role "
        "FROM relation_members WHERE relation_id = ANY(%s) "
        "ORDER BY relation_id, version, sequence_id"
    )
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for rid, version, mtype, mid, role in cur:
            out.setdefault((rid, version), []).append((mtype.lower(), mid, role))
    return out


def current_members(conn, ids):
    if not ids:
        return {}
    sql = (
        "SELECT relation_id, member_type, member_id, member_role "
        "FROM current_relation_members WHERE relation_id = ANY(%s) "
        "ORDER BY relation_id, sequence_id"
    )
    out = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ids,))
        for rid, mtype, mid, role in cur:
            out.setdefault(rid, []).append((mtype.lower(), mid, role))
    return out


def stream_chunks(conn, name, sql, size=20000):
    """Yield row batches from a fresh server side cursor.

    psycopg2 named cursors may only be executed once, so every query needs a
    cursor of its own; that also keeps memory bounded for large databases.
    """
    cursor = conn.cursor(name=name)
    try:
        cursor.itersize = size
        cursor.execute(sql)
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                return
            yield rows
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# XML writing
# ---------------------------------------------------------------------------
def write_element_open(out, kind, eid, version, changeset, timestamp, extra=""):
    out.write(
        f'  <{kind} id="{eid}" version="{version}"'
        f' changeset="{changeset}" timestamp="{iso(timestamp)}"{extra}'
    )


def write_body(out, kind, tags, children=None):
    """Close an element, emitting tags and children if there are any."""
    if not tags and not children:
        out.write("/>\n")
        return
    out.write(">\n")
    for key, value in tags or ():
        out.write(f'    <tag k="{esc(key)}" v="{esc(value)}"/>\n')
    for child in children or ():
        out.write(child)
    out.write(f"  </{kind}>\n")


def write_children_refs(ids):
    return [f'    <nd ref="{nid}"/>\n' for nid in ids]


def write_children_members(members):
    return [
        f'    <member type="{esc(t)}" ref="{mid}" role="{esc(role)}"/>\n'
        for t, mid, role in members
    ]


# ---------------------------------------------------------------------------
# Full export (bootstrap)
# ---------------------------------------------------------------------------
def export_full(stream_conn, work_conn, out_path, chunk_size=20000):
    """Write every visible element of the API database as an OSM XML file."""
    log.info("exporting full API database to %s", out_path)
    started = time.time()
    counts = {kind: 0 for kind in ELEMENT_KINDS}

    with open(out_path, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<osm version="0.6" generator="osm-sync">\n')

        # ---- nodes -------------------------------------------------------
        node_sql = (
            'SELECT id, latitude, longitude, version, "timestamp", changeset_id '
            "FROM current_nodes WHERE visible ORDER BY id"
        )
        for rows in stream_chunks(stream_conn, "osm_sync_nodes", node_sql, chunk_size):
            ids = [r[0] for r in rows]
            tags = current_tags(work_conn, "current_node_tags", "node_id", ids)
            for nid, lat, lon, version, ts, cs in rows:
                extra = f' lat="{lat / 1e7:.7f}" lon="{lon / 1e7:.7f}"'
                write_element_open(out, "node", nid, version, cs, ts, extra)
                write_body(out, "node", tags.get(nid))
                counts["node"] += 1
            log.info("  nodes: %d", counts["node"])

        # ---- ways --------------------------------------------------------
        way_sql = (
            'SELECT id, version, "timestamp", changeset_id '
            "FROM current_ways WHERE visible ORDER BY id"
        )
        for rows in stream_chunks(stream_conn, "osm_sync_ways", way_sql, chunk_size):
            ids = [r[0] for r in rows]
            tags = current_tags(work_conn, "current_way_tags", "way_id", ids)
            refs = current_refs(work_conn, "current_way_nodes", "way_id", ids)
            for wid, version, ts, cs in rows:
                write_element_open(out, "way", wid, version, cs, ts)
                write_body(out, "way", tags.get(wid), write_children_refs(refs.get(wid, ())))
                counts["way"] += 1
            log.info("  ways: %d", counts["way"])

        # ---- relations ---------------------------------------------------
        relation_sql = (
            'SELECT id, version, "timestamp", changeset_id '
            "FROM current_relations WHERE visible ORDER BY id"
        )
        for rows in stream_chunks(
            stream_conn, "osm_sync_relations", relation_sql, chunk_size
        ):
            ids = [r[0] for r in rows]
            tags = current_tags(work_conn, "current_relation_tags", "relation_id", ids)
            members = current_members(work_conn, ids)
            for rid, version, ts, cs in rows:
                write_element_open(out, "relation", rid, version, cs, ts)
                write_body(
                    out, "relation", tags.get(rid),
                    write_children_members(members.get(rid, ())),
                )
                counts["relation"] += 1
            log.info("  relations: %d", counts["relation"])

        out.write("</osm>\n")

    log.info(
        "full export done in %.1fs: %d nodes, %d ways, %d relations",
        time.time() - started, counts["node"], counts["way"], counts["relation"],
    )
    return counts


# ---------------------------------------------------------------------------
# Incremental export
# ---------------------------------------------------------------------------
def find_changesets(conn, watermark_ts, watermark_id, limit):
    """Closed changesets strictly after the watermark, in a stable order."""
    with conn.cursor() as cur:
        if watermark_ts is None:
            cur.execute(
                "SELECT id, closed_at FROM changesets "
                "WHERE closed_at <= now() ORDER BY closed_at, id LIMIT %s",
                (limit,),
            )
        else:
            cur.execute(
                "SELECT id, closed_at FROM changesets "
                "WHERE (closed_at, id) > (%s, %s) AND closed_at <= now() "
                "ORDER BY closed_at, id LIMIT %s",
                (watermark_ts, watermark_id or 0, limit),
            )
        return cur.fetchall()


def _latest_sql(table, id_column, action, extra_columns=""):
    """Latest version per element for a set of changesets, filtered by action.

    The DISTINCT ON picks the newest version *within this batch* first and only
    then filters create/modify/delete, so an element that was created and
    deleted again inside the same batch is correctly reported as a delete.
    """
    if action == "create":
        condition = "visible AND version = 1"
    elif action == "modify":
        condition = "visible AND version > 1"
    else:
        condition = "NOT visible"
    return (
        f"SELECT * FROM ("
        f"  SELECT DISTINCT ON ({id_column}) {id_column} AS eid, version, visible,"
        f'         changeset_id, "timestamp"{extra_columns}'
        f"    FROM {table} WHERE changeset_id = ANY(%s)"
        f"   ORDER BY {id_column}, version DESC"
        f") latest WHERE {condition} ORDER BY eid"
    )


def export_changes(args, work_conn, out_path, changesets, chunk_size=5000):
    """Write an osmChange document covering the given changesets.

    osm2pgsql's input reader (src/input.cpp, check_input()) enforces two rules
    and it enforces them across the **whole document**, not per
    <create>/<modify>/<delete> block:

      1. element types must appear in the order node -> way -> relation, so a
         node may never follow a way;
      2. within one element type, ids must strictly increase.

    So the obvious layout - one block per action with the elements grouped by
    type inside it, and deletes reversed to go "deepest first" - is wrong: as
    soon as a changeset mixes nodes and ways it produces `<create><way>` followed
    by `<modify><node>` and osm2pgsql aborts with

        ERROR: Input data is not ordered: node after way.

    We therefore emit type-major: all nodes first (ascending id, starting a new
    action block wherever the action changes), then all ways, then all
    relations. Repeated/interleaved <create>/<modify>/<delete> blocks are fine -
    libosmium handles those as a streaming state change, not a fixed sequence.

    Returns the number of elements written.
    """
    cs_ids = [c[0] for c in changesets]
    written = 0
    actions = ("create", "modify", "delete")

    def collect(kind, action):
        table = {"node": "nodes", "way": "ways", "relation": "relations"}[kind]
        id_column = {"node": "node_id", "way": "way_id", "relation": "relation_id"}[kind]
        extra = ", latitude, longitude" if kind == "node" else ""
        sql = _latest_sql(table, id_column, action, extra)
        with work_conn.cursor() as cur:
            cur.execute(sql, (cs_ids,))
            return cur.fetchall()

    def emit_block(out, kind, action, rows):
        out.write(f"  <{action}>\n")
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start:start + chunk_size]
            ids = [r[0] for r in chunk]
            tags = tags_by_version(work_conn, f"{kind}_tags", f"{kind}_id", ids)
            refs = {}
            members = {}
            if kind == "way":
                refs = refs_by_version(work_conn, "way_nodes", "way_id", ids)
            elif kind == "relation":
                members = members_by_version(work_conn, ids)

            for row in chunk:
                eid, version, _visible, cs, ts = row[:5]
                key = (eid, version)
                if kind == "node":
                    lat, lon = row[5], row[6]
                    extra = f' lat="{lat / 1e7:.7f}" lon="{lon / 1e7:.7f}"'
                    children = None
                else:
                    extra = ""
                    if kind == "way":
                        children = write_children_refs(refs.get(key, ()))
                    else:
                        children = write_children_members(members.get(key, ()))
                write_element_open(out, kind, eid, version, cs, ts, extra)
                write_body(out, kind, tags.get(key), children)
        out.write(f"  </{action}>\n")

    with open(out_path, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<osmChange version="0.6" generator="osm-sync">\n')

        for kind in ELEMENT_KINDS:
            by_action = {}
            for action in actions:
                by_action[action] = collect(kind, action)
                log.info("  %s %ss: %d", action, kind, len(by_action[action]))

            # Merge the three action buckets and sort by id. Rule 2 is global
            # per type, so an element modified in the middle of a run of created
            # ids still has to be emitted in id order, not action order.
            merged = sorted(
                ((row[0], action, row)
                 for action in actions
                 for row in by_action[action]),
                key=lambda item: item[0],
            )

            # groupby() only merges *consecutive* equal keys, which is exactly
            # "start a new block whenever the action changes".
            for action, group in itertools.groupby(merged, key=lambda item: item[1]):
                rows = [item[2] for item in group]
                emit_block(out, kind, action, rows)
                written += len(rows)

        out.write("</osmChange>\n")

    return written


def write_replication_files(args, sequence, timestamp, osc_path, count):
    """Keep a standards shaped replication directory for other consumers."""
    if not args.keep_replication_files:
        return
    directory = args.replication_dir
    if not directory:
        return
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, f"{sequence:09d}.osc.gz")
    with open(osc_path, "rb") as src, gzip.open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    stamp = timestamp.strftime("%Y-%m-%dT%H\\:%M\\:%SZ")
    state = os.path.join(directory, "state.txt")
    tmp = state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(f"sequenceNumber={sequence}\n")
        handle.write(f"timestamp={stamp}\n")
    os.replace(tmp, state)
    log.info("replication: wrote %s (%d elements)", target, count)


# ---------------------------------------------------------------------------
# osm2pgsql
# ---------------------------------------------------------------------------
def osm2pgsql_base(args):
    return [
        "osm2pgsql",
        "--output=flex",
        f"--style={args.style}",
        "--database", args.gis_db,
        "--host", args.host,
        "--port", str(args.port),
        # osm2pgsql spells this --user (not --username).
        "--user", args.gis_user,
        "--number-processes", str(args.numproc),
    ]


def osm2pgsql_import(args, osm_file):
    # NOTE: deliberately no --drop here. The middle tables created by --slim are
    # what makes incremental --append updates possible later on.
    # The options must also match osm2pgsql_append(): osm2pgsql records the
    # import properties and refuses to append with a different configuration.
    cmd = osm2pgsql_base(args) + [
        "--create", "--slim",
        f"--cache={args.cache}",
        osm_file,
    ]
    run(cmd)


def osm2pgsql_append(args, osc_file):
    cmd = osm2pgsql_base(args) + ["--append", "--slim", osc_file]
    run(cmd)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def load_auxiliary_sql(args):
    for name in ("indexes.sql", "functions.sql", "common-values.sql"):
        path = os.path.join(args.cartodir, name)
        if not os.path.exists(path):
            log.warning("skipping missing %s", path)
            continue
        log.info("applying %s", name)
        psql_script(args, path)


def run_download_script(script, cwd, env, label, timeout, attempts):
    """Run one of osm-carto4mc's download scripts with a safety net.

    The scripts are executed through reliable_run.py, which adds a per-request
    timeout and retries (upstream issues HTTP requests with no timeout at all,
    so one stalled connection hangs them forever). On top of that each whole
    attempt is bounded and repeated: get-external-data.py skips tables it has
    already loaded and get-fonts.py overwrites the files it already has, so a
    retry makes progress rather than starting from nothing.
    """
    wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reliable_run.py")
    merged = dict(os.environ)
    merged.update(env)
    for attempt in range(1, attempts + 1):
        log.info("%s (attempt %d/%d, %ds timeout)", label, attempt, attempts, timeout)
        try:
            proc = subprocess.run(
                [sys.executable, wrapper, script], cwd=cwd, env=merged, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            log.warning("%s timed out after %ds, retrying", label, timeout)
            continue
        if proc.returncode == 0:
            if attempt > 1:
                log.info("%s succeeded on attempt %d", label, attempt)
            return True
        log.warning("%s exited with code %d, retrying", label, proc.returncode)
    log.error("%s failed after %d attempts", label, attempts)
    return False


def load_external_data(args):
    script = os.path.join(args.cartodir, "scripts", "get-external-data.py")
    if not os.path.exists(script):
        log.warning("no external data script at %s", script)
        return
    env = {
        "PGHOST": args.host,
        "PGPORT": str(args.port),
        "PGUSER": args.gis_user,
        "PGDATABASE": args.gis_db,
    }
    run_download_script(
        script, args.cartodir, env,
        "downloading and loading Natural Earth / water polygon shapefiles",
        args.external_data_timeout, args.download_attempts,
    )


def count_font_files(directory):
    if not directory or not os.path.isdir(directory):
        return 0
    total = 0
    for _root, _dirs, files in os.walk(directory):
        total += sum(1 for name in files if name.endswith((".ttf", ".otf")))
    return total


def load_fonts(args):
    script = os.path.join(args.cartodir, "scripts", "get-fonts.py")
    if not os.path.exists(script):
        log.warning("no font script at %s", script)
        return
    os.makedirs(args.fontdir, exist_ok=True)

    # The font set is ~100 MB and, in a multi environment setup, lives in a
    # directory shared between environments (see FONTS_DIR). Re-downloading it
    # on every bootstrap is pointless - and on a network that cannot reach
    # raw.githubusercontent.com it is also slow, because get-fonts.py aborts on
    # the first font it fails to fetch and we retry the whole run.
    existing = count_font_files(args.fontdir)
    if not args.force_fonts and existing >= args.fonts_min_files:
        log.info(
            "%s already holds %d font file(s) (>= %d), skipping the download; "
            "pass --force-fonts to refresh it",
            args.fontdir, existing, args.fonts_min_files,
        )
        return

    run_download_script(
        script, args.cartodir, {"FONTDIR": args.fontdir},
        f"downloading Noto fonts into {args.fontdir}",
        args.font_timeout, args.download_attempts,
    )


def cmd_fonts(args):
    load_fonts(args)
    return 0


def cmd_external_data(args):
    load_external_data(args)
    return 0


def purge_tiles(args):
    if not args.purge_tiles:
        return
    directory = args.tiles_dir
    if not directory or not os.path.isdir(directory):
        return
    removed = 0
    for entry in os.listdir(directory):
        path = os.path.join(directory, entry)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)
    if removed:
        log.info("expired mod_tile cache (%d entries removed)", removed)


def cmd_ensure_gis(args):
    ensure_gis(args)
    log.info("render database %s is ready", args.gis_db)


def cmd_export(args):
    """Write the full OSM XML export without importing it (debugging aid)."""
    os.makedirs(args.state_dir, exist_ok=True)
    out_path = os.path.join(args.state_dir, "full.osm")
    stream_conn = api_conn(args)
    work_conn = api_conn(args)
    try:
        counts = export_full(stream_conn, work_conn, out_path)
    finally:
        stream_conn.close()
        work_conn.close()
    log.info("wrote %s: %s", out_path, counts)
    return 0


def cmd_bootstrap(args):
    ensure_gis(args)

    stream_conn = api_conn(args)
    work_conn = api_conn(args)
    try:
        full_path = os.path.join(args.state_dir, "full.osm")
        os.makedirs(args.state_dir, exist_ok=True)
        export_full(stream_conn, work_conn, full_path)

        osm2pgsql_import(args, full_path)

        load_auxiliary_sql(args)

        # Fonts are downloaded first: the tile server blocks on them before it
        # can start renderd, so getting them in early unblocks the stack while
        # the (much larger) external shapefiles are still downloading.
        if args.load_fonts:
            load_fonts(args)
        if args.load_external_data:
            load_external_data(args)

        with db_cursor(api_conn(args)) as cur:
            cur.execute(
                "SELECT id, closed_at FROM changesets "
                "WHERE closed_at <= now() ORDER BY closed_at DESC, id DESC LIMIT 1"
            )
            row = cur.fetchone()
        watermark_ts = row[1] if row else None
        watermark_id = row[0] if row else None
        set_state(args, watermark_ts, watermark_id, 0, bootstrap_ts=utcnow())
        log.info(
            "bootstrap complete; watermark = (%s, %s)",
            watermark_ts, watermark_id,
        )
    finally:
        stream_conn.close()
        work_conn.close()

    # The tile cache may hold empty tiles rendered before the import.
    purge_tiles(args)


def _export_and_apply(args, changesets):
    """Export one batch of changesets and feed it to osm2pgsql."""
    work_conn = api_conn(args)
    try:
        osc_path = os.path.join(args.state_dir, "changes.osc")
        os.makedirs(args.state_dir, exist_ok=True)
        count = export_changes(args, work_conn, osc_path, changesets)
    finally:
        work_conn.close()

    last_ts = changesets[-1][1]
    last_id = changesets[-1][0]
    _, _, sequence, _ = get_state(args)

    if count == 0:
        log.info("changesets %s..%s contained no elements", changesets[0][0], last_id)
        set_state(args, last_ts, last_id, sequence)
        return 0

    osm2pgsql_append(args, osc_path)

    sequence += 1
    set_state(args, last_ts, last_id, sequence)
    write_replication_files(args, sequence, last_ts, osc_path, count)
    purge_tiles(args)
    return count


def cmd_update(args):
    watermark_ts, watermark_id, _, _ = get_state(args)
    conn = api_conn(args)
    try:
        changesets = find_changesets(
            conn, watermark_ts, watermark_id, args.max_changesets
        )
    finally:
        conn.close()
    if not changesets:
        log.debug("no new changesets since (%s, %s)", watermark_ts, watermark_id)
        return 0
    log.info(
        "applying %d changeset(s): %s .. %s",
        len(changesets), changesets[0][0], changesets[-1][0],
    )
    return _export_and_apply(args, changesets)


def needs_bootstrap(args) -> bool:
    """Whether the render database still has to be built from scratch.

    `auto` does not only check for the existence of planet_osm_point: a previous
    bootstrap may have failed halfway (for instance because the API database had
    not been migrated yet), so the recorded bootstrap timestamp is the real
    completion marker. This makes the loop retry until it succeeds, which is
    what makes a first `docker compose up` of a fresh environment work without
    ordering the services by hand.
    """
    if args.bootstrap == "never":
        return False
    if args.bootstrap == "force":
        return True
    if not gis_is_initialised(args):
        return True
    return get_state(args)[3] is None


def cmd_loop(args):
    log.info(
        "sync loop starting (interval %ss, gis=%s, api=%s)",
        args.interval, args.gis_db, args.api_db,
    )
    while True:
        try:
            if needs_bootstrap(args):
                log.info("running bootstrap")
                cmd_bootstrap(args)
            while cmd_update(args):
                pass
        except Exception as exc:  # keep the service alive and retry
            log.exception("sync iteration failed: %s", exc)
        time.sleep(args.interval)


def cmd_status(args):
    ts, cid, sequence, bootstrap_ts = get_state(args)
    print(f"gis database      : {args.gis_db} @ {args.host}:{args.port}")
    print(f"api database      : {args.api_db}")
    print(f"bootstrapped at   : {bootstrap_ts}")
    print(f"watermark         : closed_at={ts} changeset_id={cid}")
    print(f"batches applied   : {sequence}")
    print(f"render db ready   : {gis_is_initialised(args)}")
    with db_cursor(api_conn(args)) as cur:
        cur.execute("SELECT count(*) FROM changesets WHERE closed_at <= now()")
        closed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM changesets WHERE closed_at > now()")
        open_ = cur.fetchone()[0]
    print(f"closed changesets : {closed}")
    print(f"open changesets   : {open_}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name, default):
    value = env(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def add_common_arguments(parser):
    parser.add_argument("--host", default=env("PGHOST", "db"))
    parser.add_argument("--port", type=int, default=int(env("PGPORT", "5432")))
    parser.add_argument("--api-db", default=env("API_DB", "openstreetmap"))
    parser.add_argument("--api-user", default=env("API_USER", "openstreetmap"))
    parser.add_argument("--api-password", default=env("API_PASSWORD", "openstreetmap"))
    parser.add_argument("--gis-db", default=env("GIS_DB", "gis"))
    parser.add_argument("--gis-user", default=env("GIS_USER", "postgres"))
    parser.add_argument("--cartodir", default=env("CARTODIR", "/osm-carto4mc"))
    parser.add_argument(
        "--style",
        default=env("STYLE", "/osm-carto4mc/openstreetmap-carto-flex.lua"),
    )
    parser.add_argument("--state-dir", default=env("STATE_DIR", "/state"))
    parser.add_argument("--replication-dir", default=env("REPLICATION_DIR", "/replication"))
    parser.add_argument("--tiles-dir", default=env("TILES_DIR", "/tiles"))
    parser.add_argument("--fontdir", default=env("FONTDIR", "/fonts"))
    parser.add_argument("--interval", type=int, default=int(env("SYNC_INTERVAL", "60")))
    parser.add_argument("--cache", type=int, default=int(env("OSM2PGSQL_CACHE", "1024")))
    parser.add_argument("--numproc", type=int, default=int(env("OSM2PGSQL_NUMPROC", "4")))
    parser.add_argument(
        "--max-changesets", type=int, default=int(env("MAX_CHANGESETS_PER_BATCH", "500"))
    )
    parser.add_argument(
        "--bootstrap", choices=("auto", "never", "force"), default=env("BOOTSTRAP", "auto")
    )
    parser.add_argument(
        "--load-external-data",
        action=argparse.BooleanOptionalAction,
        default=env_bool("LOAD_EXTERNAL_DATA", True),
    )
    parser.add_argument(
        "--load-fonts",
        action=argparse.BooleanOptionalAction,
        default=env_bool("LOAD_FONTS", True),
    )
    parser.add_argument(
        "--keep-replication-files",
        action=argparse.BooleanOptionalAction,
        default=env_bool("KEEP_REPLICATION_FILES", True),
    )
    parser.add_argument(
        "--purge-tiles",
        action=argparse.BooleanOptionalAction,
        default=env_bool("PURGE_TILES_ON_SYNC", True),
    )
    # get-fonts.py / get-external-data.py issue requests without a timeout, so a
    # stalled connection hangs them forever; bound each attempt and retry.
    parser.add_argument(
        "--font-timeout", type=int, default=int(env("FONT_DOWNLOAD_TIMEOUT", "1800"))
    )
    parser.add_argument(
        "--external-data-timeout",
        type=int,
        default=int(env("EXTERNAL_DATA_TIMEOUT", "7200")),
    )
    parser.add_argument(
        "--download-attempts",
        type=int,
        default=int(env("DOWNLOAD_ATTEMPTS", "3")),
    )
    # Skip re-downloading the ~100 MB Noto font set when it is already there
    # (typical when several environments share FONTS_DIR).
    parser.add_argument(
        "--fonts-min-files",
        type=int,
        default=int(env("FONTS_MIN_FILES", "40")),
        help="treat the font directory as complete when it holds this many files",
    )
    parser.add_argument(
        "--force-fonts",
        action=argparse.BooleanOptionalAction,
        default=env_bool("FORCE_FONTS", False),
        help="download the font set even if it already looks complete",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="osm-sync",
        description="Sync the openstreetmap-website API database into the "
                    "osm-carto4mc render database.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("ensure-gis", cmd_ensure_gis, "create the render database if missing"),
        ("export", cmd_export, "only write the full OSM XML export"),
        ("bootstrap", cmd_bootstrap, "full export + initial osm2pgsql import"),
        ("fonts", cmd_fonts, "only download the Noto fonts"),
        ("external-data", cmd_external_data,
         "only download and load the external shapefiles"),
        ("update", cmd_update, "apply one batch of changes"),
        ("loop", cmd_loop, "continuously apply changes"),
        ("status", cmd_status, "print sync state"),
    ):
        child = sub.add_parser(name, help=help_text)
        add_common_arguments(child)
        child.set_defaults(func=handler)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stdout,
    )
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    except Exception as exc:
        log.error("%s", exc)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
