"""SQLite Database Layer — Phase 1

Uses aiosqlite for async access.
Includes a simple version-based migration system.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

logger = logging.getLogger(__name__)

# Busy timeout for the dedicated checkpoint connection. Zero → non-waiting: if a reader
# (DB export/backup) or writer holds the DB, the checkpoint gives up immediately and is
# retried on the next maintenance tick rather than stalling application write traffic
# behind maintenance. See issue #908.
_CHECKPOINT_BUSY_TIMEOUT_SECONDS = 0.0

# ---------------------------------------------------------------------------
# Migration SQL
# ---------------------------------------------------------------------------

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS datapoints (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    data_type   TEXT NOT NULL DEFAULT 'UNKNOWN',
    unit        TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    mqtt_topic  TEXT NOT NULL,
    mqtt_alias  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adapter_bindings (
    id              TEXT PRIMARY KEY,
    datapoint_id    TEXT NOT NULL REFERENCES datapoints(id) ON DELETE CASCADE,
    adapter_type    TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('SOURCE', 'DEST', 'BOTH')),
    config          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(config)),
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dp_name         ON datapoints(name);
CREATE INDEX IF NOT EXISTS idx_dp_data_type    ON datapoints(data_type);
CREATE INDEX IF NOT EXISTS idx_bind_datapoint  ON adapter_bindings(datapoint_id);
CREATE INDEX IF NOT EXISTS idx_bind_adapter    ON adapter_bindings(adapter_type);
"""

_MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS adapter_configs (
    adapter_type  TEXT PRIMARY KEY,
    config        TEXT NOT NULL DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

_MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS history_values (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    datapoint_id TEXT    NOT NULL,
    value        TEXT    NOT NULL,
    unit         TEXT,
    quality      TEXT    NOT NULL,
    ts           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_dp_ts ON history_values(datapoint_id, ts);
"""

_MIGRATION_V4 = """
ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;
UPDATE users SET is_admin=1 WHERE username='admin';
"""


async def _migration_v5(conn: aiosqlite.Connection) -> None:
    """Multi-Instance Support:
    - Neue Tabelle adapter_instances (UUID PK, N Instanzen pro Typ)
    - adapter_bindings bekommt adapter_instance_id Spalte
    - Bestehende adapter_configs Daten werden migriert
    - Bestehende Bindings erhalten die passende adapter_instance_id
    """
    import uuid
    from datetime import datetime

    now = datetime.now(UTC).isoformat()

    # 1. adapter_instances Tabelle erstellen
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS adapter_instances (
            id           TEXT PRIMARY KEY,
            adapter_type TEXT NOT NULL,
            name         TEXT NOT NULL,
            config       TEXT NOT NULL DEFAULT '{}',
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_type ON adapter_instances(adapter_type);
    """)

    # 2. adapter_instance_id Spalte zu adapter_bindings hinzufügen (ignoriere Fehler wenn schon vorhanden)
    try:
        await conn.execute("ALTER TABLE adapter_bindings ADD COLUMN adapter_instance_id TEXT")
        await conn.commit()
    except Exception:
        pass  # Spalte existiert bereits

    # 3. adapter_configs → adapter_instances migrieren
    async with conn.execute("SELECT * FROM adapter_configs") as cur:
        configs = await cur.fetchall()

    type_to_instance_id: dict[str, str] = {}
    for row in configs:
        instance_id = str(uuid.uuid4())
        type_to_instance_id[row["adapter_type"]] = instance_id
        await conn.execute(
            """INSERT OR IGNORE INTO adapter_instances
               (id, adapter_type, name, config, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                instance_id,
                row["adapter_type"],
                row["adapter_type"],  # Name = Typ-String als Default
                row["config"],
                row["enabled"],
                now,
                now,
            ),
        )
    await conn.commit()

    # 4. Bestehende Bindings mit adapter_instance_id verknüpfen
    for adapter_type, instance_id in type_to_instance_id.items():
        await conn.execute(
            """UPDATE adapter_bindings
               SET adapter_instance_id=?
               WHERE adapter_type=? AND adapter_instance_id IS NULL""",
            (instance_id, adapter_type),
        )
    await conn.commit()
    logger.info(
        "Migration V5: %d adapter instance(s) created from adapter_configs",
        len(type_to_instance_id),
    )


_MIGRATION_V6 = """
CREATE TABLE IF NOT EXISTS knx_group_addresses (
    address     TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    dpt         TEXT,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_ga_name ON knx_group_addresses(name);
"""

_MIGRATION_V7 = """
ALTER TABLE adapter_bindings ADD COLUMN send_throttle_ms INTEGER;
"""

_MIGRATION_V8 = """
ALTER TABLE adapter_bindings ADD COLUMN send_on_change      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE adapter_bindings ADD COLUMN send_min_delta      REAL;
ALTER TABLE adapter_bindings ADD COLUMN send_min_delta_pct  REAL;
"""

_MIGRATION_V9 = """
ALTER TABLE adapter_bindings ADD COLUMN value_formula TEXT;
"""

_MIGRATION_V10 = """
ALTER TABLE users ADD COLUMN mqtt_enabled      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN mqtt_password_hash TEXT;
"""

_MIGRATION_V11 = """
ALTER TABLE api_keys ADD COLUMN owner TEXT NOT NULL DEFAULT '';
"""

_MIGRATION_V12 = """
CREATE TABLE IF NOT EXISTS logic_graphs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    flow_data   TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_MIGRATION_V13 = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('timezone', 'Europe/Zurich');
"""

_MIGRATION_V14 = """
ALTER TABLE logic_graphs ADD COLUMN node_state TEXT NOT NULL DEFAULT '{}';
"""

_MIGRATION_V15 = """
ALTER TABLE datapoints ADD COLUMN persist_value INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS datapoint_last_values (
    datapoint_id  TEXT PRIMARY KEY REFERENCES datapoints(id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    unit          TEXT,
    ts            TEXT NOT NULL
);
"""

_MIGRATION_V16 = """
CREATE TABLE IF NOT EXISTS visu_nodes (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES visu_nodes(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'PAGE' CHECK (type IN ('LOCATION', 'PAGE')),
    node_order   INTEGER NOT NULL DEFAULT 0,
    icon         TEXT,
    access       TEXT CHECK (access IN ('readonly', 'public', 'protected', 'private')),
    access_pin   TEXT,
    page_config  TEXT NOT NULL DEFAULT '{"grid_cols":12,"grid_row_height":80,"background":null,"widgets":[]}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visu_nodes_parent ON visu_nodes(parent_id);
"""

_MIGRATION_V17 = """
ALTER TABLE history_values ADD COLUMN source_adapter TEXT;
"""

_MIGRATION_V20 = """
ALTER TABLE adapter_bindings ADD COLUMN value_map TEXT CHECK (value_map IS NULL OR json_valid(value_map));
"""

_MIGRATION_V21 = """
ALTER TABLE datapoints ADD COLUMN record_history INTEGER NOT NULL DEFAULT 1;
"""

_MIGRATION_V18 = """
CREATE TABLE visu_nodes_new (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES visu_nodes(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'PAGE' CHECK (type IN ('LOCATION', 'PAGE')),
    node_order   INTEGER NOT NULL DEFAULT 0,
    icon         TEXT,
    access       TEXT CHECK (access IN ('readonly', 'public', 'protected', 'private')),
    access_pin   TEXT,
    page_config  TEXT NOT NULL DEFAULT '{"grid_cols":12,"grid_row_height":80,"background":null,"widgets":[]}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
INSERT INTO visu_nodes_new SELECT * FROM visu_nodes;
DROP TABLE visu_nodes;
ALTER TABLE visu_nodes_new RENAME TO visu_nodes;
CREATE INDEX IF NOT EXISTS idx_visu_nodes_parent ON visu_nodes(parent_id);
"""

_MIGRATION_V19 = """
CREATE TABLE visu_nodes_v19 (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES visu_nodes_v19(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'PAGE' CHECK (type IN ('LOCATION', 'PAGE')),
    node_order   INTEGER NOT NULL DEFAULT 0,
    icon         TEXT,
    access       TEXT CHECK (access IN ('readonly', 'public', 'protected', 'user')),
    access_pin   TEXT,
    page_config  TEXT NOT NULL DEFAULT '{"grid_cols":12,"grid_row_height":80,"background":null,"widgets":[]}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
INSERT INTO visu_nodes_v19
    SELECT id, parent_id, name, type, node_order, icon,
           CASE WHEN access = 'private' THEN 'user' ELSE access END,
           access_pin, page_config, created_at, updated_at
    FROM visu_nodes;
DROP TABLE visu_nodes;
ALTER TABLE visu_nodes_v19 RENAME TO visu_nodes;
CREATE INDEX IF NOT EXISTS idx_visu_nodes_parent ON visu_nodes(parent_id);

CREATE TABLE IF NOT EXISTS visu_node_users (
    node_id  TEXT NOT NULL REFERENCES visu_nodes(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    PRIMARY KEY (node_id, username)
);
CREATE INDEX IF NOT EXISTS idx_vnu_node ON visu_node_users(node_id);
CREATE INDEX IF NOT EXISTS idx_vnu_user ON visu_node_users(username);
"""

_MIGRATION_V22 = """
CREATE TABLE IF NOT EXISTS nav_links (
    id           TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    url          TEXT NOT NULL,
    icon         TEXT NOT NULL DEFAULT '',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    open_new_tab INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
"""

_MIGRATION_V23 = """
CREATE TABLE IF NOT EXISTS hierarchy_trees (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hierarchy_nodes (
    id          TEXT PRIMARY KEY,
    tree_id     TEXT NOT NULL REFERENCES hierarchy_trees(id) ON DELETE CASCADE,
    parent_id   TEXT REFERENCES hierarchy_nodes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    node_order  INTEGER NOT NULL DEFAULT 0,
    icon        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hn_tree   ON hierarchy_nodes(tree_id);
CREATE INDEX IF NOT EXISTS idx_hn_parent ON hierarchy_nodes(parent_id);

CREATE TABLE IF NOT EXISTS hierarchy_datapoint_links (
    id           TEXT PRIMARY KEY,
    node_id      TEXT NOT NULL REFERENCES hierarchy_nodes(id) ON DELETE CASCADE,
    datapoint_id TEXT NOT NULL REFERENCES datapoints(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    UNIQUE(node_id, datapoint_id)
);
CREATE INDEX IF NOT EXISTS idx_hdl_node ON hierarchy_datapoint_links(node_id);
CREATE INDEX IF NOT EXISTS idx_hdl_dp   ON hierarchy_datapoint_links(datapoint_id);
"""

_MIGRATION_V24 = """
ALTER TABLE knx_group_addresses ADD COLUMN main_group_name TEXT NOT NULL DEFAULT '';
ALTER TABLE knx_group_addresses ADD COLUMN mid_group_name  TEXT NOT NULL DEFAULT '';
"""

_MIGRATION_V25 = """
CREATE TABLE IF NOT EXISTS knx_locations (
    id          TEXT PRIMARY KEY,
    parent_id   TEXT,
    name        TEXT NOT NULL DEFAULT '',
    space_type  TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knx_loc_parent ON knx_locations(parent_id);

CREATE TABLE IF NOT EXISTS knx_functions (
    id          TEXT PRIMARY KEY,
    space_id    TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    usage_text  TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knx_fn_space ON knx_functions(space_id);

CREATE TABLE IF NOT EXISTS knx_function_ga_links (
    function_id TEXT NOT NULL,
    ga_address  TEXT NOT NULL,
    PRIMARY KEY (function_id, ga_address)
);
CREATE INDEX IF NOT EXISTS idx_knx_fga_fn ON knx_function_ga_links(function_id);
CREATE INDEX IF NOT EXISTS idx_knx_fga_ga ON knx_function_ga_links(ga_address);
"""

_MIGRATION_V26 = """
CREATE TABLE IF NOT EXISTS knx_trades (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);
"""

_MIGRATION_V27 = """
ALTER TABLE knx_functions ADD COLUMN trade_id TEXT;
CREATE INDEX IF NOT EXISTS idx_knx_fn_trade ON knx_functions(trade_id);
"""

_MIGRATION_V28 = """
ALTER TABLE knx_trades ADD COLUMN parent_id TEXT;
CREATE INDEX IF NOT EXISTS idx_knx_trade_parent ON knx_trades(parent_id);
"""

_MIGRATION_V29 = """
ALTER TABLE hierarchy_trees ADD COLUMN display_depth INTEGER NOT NULL DEFAULT 0;
"""

_MIGRATION_V34 = """
CREATE TABLE IF NOT EXISTS knx_devices (
    id                       TEXT PRIMARY KEY,
    individual_address       TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL DEFAULT '',
    description              TEXT NOT NULL DEFAULT '',
    product_name             TEXT NOT NULL DEFAULT '',
    product_refid            TEXT NOT NULL DEFAULT '',
    hardware2program_refid   TEXT NOT NULL DEFAULT '',
    imported_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knx_devices_pa ON knx_devices(individual_address);
CREATE INDEX IF NOT EXISTS idx_knx_devices_product_refid ON knx_devices(product_refid);

CREATE TABLE IF NOT EXISTS knx_comm_objects (
    id              TEXT PRIMARY KEY,
    device_id       TEXT NOT NULL REFERENCES knx_devices(id) ON DELETE CASCADE,
    number          TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    text            TEXT NOT NULL DEFAULT '',
    function_text   TEXT NOT NULL DEFAULT '',
    datapoint_type  TEXT NOT NULL DEFAULT '',
    imported_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knx_co_device ON knx_comm_objects(device_id);
CREATE INDEX IF NOT EXISTS idx_knx_co_dpt ON knx_comm_objects(datapoint_type);

CREATE TABLE IF NOT EXISTS knx_co_ga_links (
    comm_object_id  TEXT NOT NULL REFERENCES knx_comm_objects(id) ON DELETE CASCADE,
    ga_address      TEXT NOT NULL REFERENCES knx_group_addresses(address) ON DELETE CASCADE,
    PRIMARY KEY (comm_object_id, ga_address)
);
CREATE INDEX IF NOT EXISTS idx_knx_coga_ga ON knx_co_ga_links(ga_address);

CREATE TABLE IF NOT EXISTS knx_space_device_links (
    space_id   TEXT NOT NULL REFERENCES knx_locations(id) ON DELETE CASCADE,
    device_id  TEXT NOT NULL REFERENCES knx_devices(id) ON DELETE CASCADE,
    PRIMARY KEY (space_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_knx_space_device_device ON knx_space_device_links(device_id);
"""


async def _migration_v36(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute("ALTER TABLE hierarchy_trees ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    except aiosqlite.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_trees_source ON hierarchy_trees(source)")


async def _migration_v32(conn: aiosqlite.Connection) -> None:
    """Consolidated flat-filterset schema (was epic V29+V30+V31) plus a
    display_depth fixup for epic dev DBs.

    Background — three schema histories converge here:
      - Fresh DBs (post #462 merge): run V29 (display_depth on hierarchy_trees)
        then V32 (build filtersets fresh).
      - Upstream pre-#462 dev DBs at schema_version=28: run V29 then V32 — V32
        creates the filterset table from scratch since it never existed.
      - Epic dev DBs at schema_version=31: V29 is already marked applied (with
        the OLD in-place content that built filtersets), so the new V29
        (display_depth) does NOT re-run for them. V32 adds display_depth via
        the idempotent ALTER at the end, and its other steps are no-ops
        because filtersets already has the final schema.

    Every step here is idempotent (CREATE IF NOT EXISTS, duplicate-column /
    no-such-column guards, DROP IF EXISTS).

    Epic V30 and V31 were intentionally dropped from the MIGRATIONS list —
    they only ever shipped to a handful of dev DBs, and their effect is folded
    into this migration. The version numbers 30 and 31 are skipped on fresh
    installs, which the monotonic-MAX migration runner handles fine.
    """
    # 1. Filtersets table — create if missing (fresh DBs + upstream pre-#462).
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ringbuffer_filtersets (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            dsl_version   INTEGER NOT NULL DEFAULT 2,
            is_active     INTEGER NOT NULL DEFAULT 1,
            color         TEXT NOT NULL DEFAULT '#3b82f6',
            topbar_active INTEGER NOT NULL DEFAULT 0,
            topbar_order  INTEGER NOT NULL DEFAULT 0,
            filter_json   TEXT NOT NULL DEFAULT '{}',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )

    # 2. Ensure all columns are present (idempotent for older epic dev DBs).
    async def _add(column: str, definition: str) -> None:
        try:
            await conn.execute(f"ALTER TABLE ringbuffer_filtersets ADD COLUMN {column} {definition}")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    await _add("color", "TEXT NOT NULL DEFAULT '#3b82f6'")
    await _add("topbar_active", "INTEGER NOT NULL DEFAULT 0")
    await _add("topbar_order", "INTEGER NOT NULL DEFAULT 0")
    await _add("filter_json", "TEXT NOT NULL DEFAULT '{}'")

    # 3. Drop the obsolete is_default column if present (epic dev DBs that ran
    # an early V29 variant, before the in-place rewrite removed is_default).
    try:
        await conn.execute("ALTER TABLE ringbuffer_filtersets DROP COLUMN is_default")
    except aiosqlite.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise

    # 4. Drop legacy groups/rules helper tables (#431 flattening).
    await conn.execute("DROP TABLE IF EXISTS ringbuffer_filterset_rules")
    await conn.execute("DROP TABLE IF EXISTS ringbuffer_filterset_groups")

    # 5. Indexes.
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_active ON ringbuffer_filtersets(is_active)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_topbar_active ON ringbuffer_filtersets(topbar_active)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_topbar_order ON ringbuffer_filtersets(topbar_order)")
    await conn.execute("DROP INDEX IF EXISTS idx_rb_fs_default")

    # 6. Epic dev DB display_depth fixup. Those DBs ran the OLD epic V29
    # (filtersets CREATE) instead of the new upstream V29 (display_depth) and
    # therefore never received the new column. duplicate-column for everyone
    # else.
    try:
        await conn.execute("ALTER TABLE hierarchy_trees ADD COLUMN display_depth INTEGER NOT NULL DEFAULT 0")
    except aiosqlite.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


async def _migration_v33(conn: aiosqlite.Connection) -> None:
    """Fine-grained filterset ownership (#478).

    Adds the ``created_by`` owner column to ``ringbuffer_filtersets`` and a
    per-user state table that overrides the topbar pinning and ordering. The
    global ``topbar_active`` / ``topbar_order`` columns on
    ``ringbuffer_filtersets`` are no longer read by the API; they remain in
    place for backward-compat-friendly schema diffs only.

    Existing rows keep ``created_by = NULL`` and are treated as shared,
    admin-only-editable by the API.
    """
    try:
        await conn.execute("ALTER TABLE ringbuffer_filtersets ADD COLUMN created_by TEXT")
    except aiosqlite.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_created_by ON ringbuffer_filtersets(created_by)")

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ringbuffer_filterset_user_state (
            username       TEXT NOT NULL,
            filterset_id   TEXT NOT NULL REFERENCES ringbuffer_filtersets(id) ON DELETE CASCADE,
            is_active      INTEGER NOT NULL DEFAULT 1,
            topbar_active  INTEGER NOT NULL DEFAULT 0,
            topbar_order   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (username, filterset_id)
        )
        """
    )
    # ``is_active`` was added after the initial V33 draft. Guard the column-add
    # for any DB that may have been created against the early shape.
    try:
        await conn.execute("ALTER TABLE ringbuffer_filterset_user_state ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    except aiosqlite.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_user_state_active ON ringbuffer_filterset_user_state(username, topbar_active)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_user_state_order ON ringbuffer_filterset_user_state(username, topbar_order)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rb_fs_user_state_is_active ON ringbuffer_filterset_user_state(username, is_active)")


_MIGRATION_V35 = """
CREATE TABLE IF NOT EXISTS audit_log_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    details_json  TEXT NOT NULL DEFAULT '{}',
    request_id    TEXT,
    remote_addr   TEXT,
    user_agent    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_entries_created_at ON audit_log_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_entries_action     ON audit_log_entries(action);
"""


_MIGRATION_V37 = """
CREATE TABLE IF NOT EXISTS authz_node_roles (
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'api_key')),
    principal_id   TEXT NOT NULL,
    node_type      TEXT NOT NULL,
    node_id        TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('owner', 'resident', 'operator', 'guest')),
    effect         TEXT NOT NULL DEFAULT 'allow' CHECK (effect IN ('allow', 'deny')),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (principal_type, principal_id, node_type, node_id)
);
CREATE INDEX IF NOT EXISTS idx_authz_node_roles_principal
    ON authz_node_roles(principal_type, principal_id);
CREATE INDEX IF NOT EXISTS idx_authz_node_roles_node
    ON authz_node_roles(node_type, node_id);
CREATE INDEX IF NOT EXISTS idx_authz_node_roles_role
    ON authz_node_roles(role);
"""


_MIGRATION_V38 = """
CREATE TABLE IF NOT EXISTS hierarchy_device_links (
    id         TEXT PRIMARY KEY,
    node_id    TEXT NOT NULL REFERENCES hierarchy_nodes(id) ON DELETE CASCADE,
    device_id  TEXT NOT NULL REFERENCES knx_devices(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(node_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_hierarchy_device_links_node
    ON hierarchy_device_links(node_id);
CREATE INDEX IF NOT EXISTS idx_hierarchy_device_links_device
    ON hierarchy_device_links(device_id);
"""

_MIGRATION_V39 = _MIGRATION_V38


async def _migration_v40(conn: aiosqlite.Connection) -> None:
    """Add nullable ownership without attributing legacy rows."""
    for table in ("logic_graphs", "visu_nodes"):
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if not columns:
            continue
        if "created_by" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN created_by TEXT")
        await conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_created_by ON {table}(created_by)")


_MIGRATION_V41 = """
CREATE TABLE IF NOT EXISTS api_key_capability_sets (
    key_id      TEXT PRIMARY KEY REFERENCES api_keys(id) ON DELETE CASCADE,
    revision    INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS api_key_capabilities (
    key_id      TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    capability  TEXT NOT NULL CHECK (capability IN ('visu.page_config.write', 'datapoint.metadata.write')),
    PRIMARY KEY (key_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_api_key_capabilities_key ON api_key_capabilities(key_id);
"""


async def _migration_v42(conn: aiosqlite.Connection) -> None:
    """Move Visu page access policy and user assignments into central AuthZ storage."""
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS authz_visu_page_policies (
            node_id      TEXT PRIMARY KEY REFERENCES visu_nodes(id) ON DELETE CASCADE,
            access_mode  TEXT NOT NULL CHECK (access_mode IN ('readonly', 'public', 'protected', 'user')),
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_authz_visu_page_policies_mode
            ON authz_visu_page_policies(access_mode);

        CREATE TABLE IF NOT EXISTS authz_visu_page_credentials (
            node_id      TEXT PRIMARY KEY REFERENCES authz_visu_page_policies(node_id) ON DELETE CASCADE,
            pin_hash     TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
    """)

    async with conn.execute("PRAGMA table_info(visu_nodes)") as cur:
        visu_columns = {row["name"] for row in await cur.fetchall()}
    if {"access", "access_pin"} <= visu_columns:
        # A policy without a usable protected credential remains protected and
        # therefore fails closed. PIN hashes are deliberately stored only in the
        # credential table, never in grants or audit payloads.
        await conn.execute(
            """
            INSERT OR IGNORE INTO authz_visu_page_policies (node_id, access_mode)
            SELECT id, access
            FROM visu_nodes
            WHERE access IN ('readonly', 'public', 'protected', 'user')
            """,
        )
        await conn.execute(
            """
            INSERT OR IGNORE INTO authz_visu_page_credentials (node_id, pin_hash)
            SELECT id, access_pin
            FROM visu_nodes
            WHERE access = 'protected'
              AND access_pin IS NOT NULL
              AND access_pin != ''
            """,
        )

    async with conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visu_node_users'") as cur:
        has_legacy_users = await cur.fetchone() is not None
    if has_legacy_users:
        # Only unambiguous assignments survive: the defining legacy node must
        # explicitly be user-scoped and the non-admin user must already exist.
        await conn.execute(
            """
            INSERT OR IGNORE INTO authz_node_roles
                (principal_type, principal_id, node_type, node_id, role, effect)
            SELECT 'user', vnu.username, 'visu_page', vnu.node_id, 'guest', 'allow'
            FROM visu_node_users AS vnu
            JOIN visu_nodes AS vn ON vn.id = vnu.node_id AND vn.access = 'user'
            JOIN users AS u ON u.username = vnu.username AND u.is_admin = 0
            """,
        )
        await conn.execute("DROP TABLE visu_node_users")

    if {"access", "access_pin"} <= visu_columns:
        await conn.execute("UPDATE visu_nodes SET access = NULL, access_pin = NULL WHERE access IS NOT NULL OR access_pin IS NOT NULL")


async def _migration_v43(conn: aiosqlite.Connection) -> None:
    """Snapshot legacy filterset access into central role grants.

    Before V43 every authenticated principal could read every filterset, while
    a valid ``created_by`` user was its effective owner.  Materialize that
    exact population once: future principals intentionally receive no grant.
    Owner rows are inserted first so the subsequent read snapshot cannot
    downgrade them.  Empty and orphaned owner names never become principals.
    """
    table_rows = await (await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    tables = {row[0] for row in table_rows}
    if not {"authz_node_roles", "ringbuffer_filtersets", "users", "api_keys"} <= tables:
        return

    await conn.execute(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        SELECT 'user', users.username, 'ringbuffer_filterset', filtersets.id, 'owner', 'allow'
        FROM ringbuffer_filtersets AS filtersets
        JOIN users ON users.username = filtersets.created_by
        WHERE filtersets.created_by IS NOT NULL AND trim(filtersets.created_by) != ''
        ON CONFLICT(principal_type, principal_id, node_type, node_id) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        SELECT 'user', users.username, 'ringbuffer_filterset', filtersets.id, 'guest', 'allow'
        FROM users CROSS JOIN ringbuffer_filtersets AS filtersets
        WHERE true
        ON CONFLICT(principal_type, principal_id, node_type, node_id) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        SELECT 'api_key', api_keys.id, 'ringbuffer_filterset', filtersets.id, 'guest', 'allow'
        FROM api_keys CROSS JOIN ringbuffer_filtersets AS filtersets
        WHERE true
        ON CONFLICT(principal_type, principal_id, node_type, node_id) DO NOTHING
        """
    )


async def _migration_v44(conn: aiosqlite.Connection) -> None:
    """Materialize legacy bound-datapoint scope as adapter-instance grants.

    The former adapter scope was implicit in the effective authorization of an
    instance's bound datapoints.  Only unambiguous effective access is copied:
    any readable bound datapoint yields ``guest`` while write access to every
    bound datapoint yields ``operator``.  Unbound or malformed instances and
    conflicting API-key aliases remain default-deny.
    """
    table_rows = await (await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    tables = {row[0] for row in table_rows}
    if not {"authz_node_roles", "adapter_instances", "adapter_bindings", "users", "api_keys"} <= tables:
        return

    from obs.api.auth import Principal
    from obs.api.authz import AuthzAction, AuthzTarget, RoleGrant, authorize

    grant_rows = await (
        await conn.execute(
            """
        SELECT principal_type, principal_id, node_type, node_id, role, effect
        FROM authz_node_roles
        WHERE node_type != 'adapter_instance'
        ORDER BY principal_type, principal_id, node_type, node_id
        """,
        )
    ).fetchall()
    if not grant_rows:
        return

    user_rows = await (await conn.execute("SELECT username FROM users WHERE is_admin=0")).fetchall()
    key_rows = await (await conn.execute("SELECT id FROM api_keys")).fetchall()
    valid_principals = {
        *(("user", row["username"]) for row in user_rows),
        *(("api_key", row["id"]) for row in key_rows),
    }

    def canonical_principal(principal_type: str, principal_id: str) -> tuple[str, str]:
        if principal_type == "api_key":
            return principal_type, principal_id.removeprefix("api_key:")
        return principal_type, principal_id

    grants_by_principal: dict[tuple[str, str], dict[tuple[str, str], RoleGrant]] = {}
    ambiguous_principals: set[tuple[str, str]] = set()

    hierarchy_rows = await (await conn.execute("SELECT id, parent_id FROM hierarchy_nodes")).fetchall()
    parents = {row["id"]: row["parent_id"] for row in hierarchy_rows}

    def ancestors(node_id: str) -> tuple[str, ...] | None:
        if node_id not in parents:
            return None
        result: list[str] = []
        seen = {node_id}
        current = parents[node_id]
        while current is not None:
            if current in seen or current not in parents:
                return None
            result.append(current)
            seen.add(current)
            current = parents[current]
        result.reverse()
        return tuple(result)

    for row in grant_rows:
        principal_key = canonical_principal(row["principal_type"], row["principal_id"])
        if principal_key not in valid_principals:
            ambiguous_principals.add(principal_key)
            continue
        target_key = (row["node_type"], row["node_id"])
        grant_ancestors: tuple[str, ...] = ()
        if row["node_type"] == "hierarchy":
            resolved = ancestors(row["node_id"])
            if resolved is None:
                ambiguous_principals.add(principal_key)
                continue
            grant_ancestors = resolved
        grant = RoleGrant(
            principal_type=row["principal_type"],
            principal_id=principal_key[1],
            node_type=row["node_type"],
            node_id=row["node_id"],
            role=row["role"],
            effect=row["effect"],
            ancestors=grant_ancestors,
        )
        previous = grants_by_principal.setdefault(principal_key, {}).get(target_key)
        if previous is not None and (previous.role != grant.role or previous.effect != grant.effect):
            ambiguous_principals.add(principal_key)
            continue
        grants_by_principal[principal_key][target_key] = grant

    instance_rows = await (await conn.execute("SELECT id, adapter_type FROM adapter_instances")).fetchall()
    instance_types = {row["id"]: row["adapter_type"] for row in instance_rows}
    binding_rows = await (
        await conn.execute(
            """
        SELECT adapter_instance_id, adapter_type, datapoint_id
        FROM adapter_bindings
        WHERE adapter_instance_id IS NOT NULL
        ORDER BY adapter_instance_id, datapoint_id
        """,
        )
    ).fetchall()
    datapoint_rows = await (await conn.execute("SELECT id FROM datapoints")).fetchall()
    datapoint_ids = {row["id"] for row in datapoint_rows}
    bindings_by_instance: dict[str, set[str]] = {}
    ambiguous_instances: set[str] = set()
    for row in binding_rows:
        instance_id = row["adapter_instance_id"]
        if instance_id not in instance_types or row["datapoint_id"] not in datapoint_ids:
            ambiguous_instances.add(instance_id)
            continue
        if row["adapter_type"] != instance_types[instance_id]:
            ambiguous_instances.add(instance_id)
            continue
        bindings_by_instance.setdefault(instance_id, set()).add(row["datapoint_id"])

    link_rows = await (
        await conn.execute(
            "SELECT datapoint_id, node_id FROM hierarchy_datapoint_links ORDER BY datapoint_id, node_id",
        )
    ).fetchall()
    node_ids_by_datapoint: dict[str, list[str]] = {}
    ambiguous_datapoints: set[str] = set()
    for row in link_rows:
        if ancestors(row["node_id"]) is None:
            ambiguous_datapoints.add(row["datapoint_id"])
            continue
        node_ids_by_datapoint.setdefault(row["datapoint_id"], []).append(row["node_id"])

    def datapoint_targets(datapoint_id: str, grants: list[RoleGrant], *, write: bool) -> list[AuthzTarget]:
        min_role = "operator" if write else None
        targets = [
            AuthzTarget(
                node_type="hierarchy",
                node_id=node_id,
                ancestors=ancestors(node_id) or (),
                min_role=min_role,
            )
            for node_id in node_ids_by_datapoint.get(datapoint_id, [])
        ]
        if not write and any(grant.node_type == "datapoint" and grant.node_id == datapoint_id for grant in grants):
            targets.append(AuthzTarget(node_type="datapoint", node_id=datapoint_id, min_role=min_role))
        return targets

    def datapoint_write_allowed(
        principal: Principal,
        datapoint_id: str,
        targets: list[AuthzTarget],
        grants: list[RoleGrant],
    ) -> bool:
        hierarchy_decision = authorize(
            principal=principal,
            action=AuthzAction.WRITE,
            targets=targets,
            grants=grants,
        )
        direct_grants = [grant for grant in grants if grant.node_type == "datapoint" and grant.node_id == datapoint_id]
        if not direct_grants:
            return hierarchy_decision.allowed
        direct_decision = authorize(
            principal=principal,
            action=AuthzAction.WRITE,
            targets=[AuthzTarget(node_type="datapoint", node_id=datapoint_id)],
            grants=grants,
        )
        if hierarchy_decision.reason == "explicit_deny" or direct_decision.reason == "explicit_deny":
            return False
        return hierarchy_decision.allowed or direct_decision.allowed

    inserts: list[tuple[str, str, str, str, str, str]] = []
    for principal_key, grants_by_target in grants_by_principal.items():
        if principal_key in ambiguous_principals:
            continue
        principal_type, principal_id = principal_key
        principal = Principal(
            subject=f"api_key:{principal_id}" if principal_type == "api_key" else principal_id,
            type=principal_type,
            is_admin=False,
        )
        grants = list(grants_by_target.values())
        for instance_id, bound_datapoints in bindings_by_instance.items():
            if instance_id in ambiguous_instances or not bound_datapoints:
                continue
            if any(datapoint_id in ambiguous_datapoints for datapoint_id in bound_datapoints):
                continue

            readable = False
            writable = True
            for datapoint_id in bound_datapoints:
                read_targets = datapoint_targets(datapoint_id, grants, write=False)
                read_grants = [
                    grant for grant in grants if any(grant.node_type == target.node_type and grant.node_id in target.path for target in read_targets)
                ]
                if authorize(
                    principal=principal,
                    action=AuthzAction.READ,
                    targets=read_targets,
                    grants=read_grants,
                ).allowed:
                    readable = True

                write_targets = datapoint_targets(datapoint_id, grants, write=True)
                if not datapoint_write_allowed(principal, datapoint_id, write_targets, grants):
                    writable = False

            role = "operator" if writable else "guest" if readable else None
            if role is not None:
                inserts.append((principal_type, principal_id, "adapter_instance", instance_id, role, "allow"))

    if inserts:
        await conn.executemany(
            """
            INSERT OR IGNORE INTO authz_node_roles
                (principal_type, principal_id, node_type, node_id, role, effect)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            inserts,
        )


async def _migration_v45(conn: aiosqlite.Connection) -> None:
    """Remove central grants whose concrete resource no longer exists.

    Central grants deliberately have no polymorphic foreign key.  Resource
    lifecycle cleanup now removes them with each resource, while this one-time
    reconciliation repairs rows left by older deletion and reset paths.  A
    missing resource table in a partial historical schema is not evidence that
    every grant of that type is orphaned, so that type is left untouched.
    """
    table_rows = await (await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    tables = {row[0] for row in table_rows}
    if "authz_node_roles" not in tables:
        return

    resources = {
        "datapoint": "datapoints",
        "hierarchy": "hierarchy_nodes",
        "visu_page": "visu_nodes",
        "logic_graph": "logic_graphs",
        "ringbuffer_filterset": "ringbuffer_filtersets",
        "adapter_instance": "adapter_instances",
    }
    for node_type, table in resources.items():
        if table not in tables:
            continue
        await conn.execute(
            f"""DELETE FROM authz_node_roles
                WHERE node_type=?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table}
                      WHERE {table}.id=authz_node_roles.node_id
                  )""",
            (node_type,),
        )


async def _migration_v46(conn: aiosqlite.Connection) -> None:
    """Add fail-closed central-control metadata without widening existing access."""
    for table in ("datapoints", "logic_graphs"):
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if columns and "control_class" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN control_class TEXT NOT NULL DEFAULT 'room_local' "
                "CHECK (control_class IN ('room_local', 'central_plant'))"
            )

    async with conn.execute("PRAGMA table_info(authz_node_roles)") as cur:
        grant_columns = {row["name"] for row in await cur.fetchall()}
    if grant_columns and "central_control" not in grant_columns:
        await conn.execute("ALTER TABLE authz_node_roles ADD COLUMN central_control INTEGER NOT NULL DEFAULT 0 CHECK (central_control IN (0, 1))")


# List of (version, sql_or_callable) tuples — append new migrations here
MIGRATIONS: list[tuple[int, str | Callable]] = [
    (1, _MIGRATION_V1),
    (2, _MIGRATION_V2),
    (3, _MIGRATION_V3),
    (4, _MIGRATION_V4),
    (5, _migration_v5),
    (6, _MIGRATION_V6),
    (7, _MIGRATION_V7),
    (8, _MIGRATION_V8),
    (9, _MIGRATION_V9),
    (10, _MIGRATION_V10),
    (11, _MIGRATION_V11),
    (12, _MIGRATION_V12),
    (13, _MIGRATION_V13),
    (14, _MIGRATION_V14),
    (15, _MIGRATION_V15),
    (16, _MIGRATION_V16),
    (17, _MIGRATION_V17),
    (18, _MIGRATION_V18),
    (19, _MIGRATION_V19),
    (20, _MIGRATION_V20),
    (21, _MIGRATION_V21),
    (22, _MIGRATION_V22),
    (23, _MIGRATION_V23),
    (24, _MIGRATION_V24),
    (25, _MIGRATION_V25),
    (26, _MIGRATION_V26),
    (27, _MIGRATION_V27),
    (28, _MIGRATION_V28),
    (29, _MIGRATION_V29),
    # V30 and V31 were epic-only follow-ups to the original V29; their effect
    # is consolidated into V32 below. Version numbers 30 and 31 are deliberately
    # skipped so fresh DBs jump 29→32, while epic dev DBs at schema_version=31
    # see V32 as the next applicable migration.
    (32, _migration_v32),
    (33, _migration_v33),
    (34, _MIGRATION_V34),
    (35, _MIGRATION_V35),
    (36, _migration_v36),
    (37, _MIGRATION_V37),
    (38, _MIGRATION_V38),
    (39, _MIGRATION_V39),
    (40, _migration_v40),
    (41, _MIGRATION_V41),
    (42, _migration_v42),
    (43, _migration_v43),
    (44, _migration_v44),
    (45, _migration_v45),
    (46, _migration_v46),
]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """Async SQLite database wrapper with built-in migration support."""

    def __init__(self, path: str) -> None:
        self._path = path
        if path == ":memory:":
            self._connection_path = f"file:obs-{uuid4().hex}?mode=memory&cache=shared"
            self._connection_uri = True
        else:
            self._connection_path = path
            self._connection_uri = path.startswith("file:")
        self._conn: aiosqlite.Connection | None = None
        # Serializes WAL checkpoints and pairs them with disconnect: a restore
        # (POST /config/import/db) disconnects the DB and rewrites the file, and
        # cancelling asyncio.to_thread does not stop the worker thread — so disconnect
        # must wait for any in-flight checkpoint to finish before returning. See #908.
        self._checkpoint_lock = asyncio.Lock()
        self._transaction_connections: dict[asyncio.Task, aiosqlite.Connection] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._path not in (":memory:", "file::memory:?cache=shared"):
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await self._open_connection()
        self._conn.row_factory = aiosqlite.Row

        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        # Bound the -wal sidecar: auto-checkpoint roughly every 1000 pages and, on each
        # checkpoint, truncate the WAL file back down to 64 MiB instead of leaving it at
        # its high-water mark. Combined with the periodic TRUNCATE checkpoint driven by
        # obs/db/maintenance.py this prevents the unbounded WAL growth that could fill the
        # disk under continuous history writes. See issue #908.
        await self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        await self._conn.execute("PRAGMA journal_size_limit=67108864")
        await self._conn.commit()

        # Reclaim any oversized WAL left by a previous run *before* migrations or other
        # startup writes: on a restart recovering from the full-disk condition behind
        # issue #908, this frees space so the following writes don't hit ENOSPC. Best
        # effort — a failure here must not block startup.
        try:
            await self.checkpoint()
        except Exception:
            logger.exception("Initial WAL checkpoint on connect failed")

        await self._run_migrations()
        logger.info("Database connected: %s", self._path)

    async def disconnect(self) -> None:
        # Hold the checkpoint lock across the whole teardown: this waits for any
        # in-flight maintenance checkpoint to finish (its worker thread cannot be
        # cancelled) before closing, so a restore that rewrites the file right after
        # disconnect never races a checkpoint still holding locks on it. See #908.
        async with self._checkpoint_lock:
            if self._conn is not None:
                # Leave the -wal sidecar bounded on graceful shutdown.
                try:
                    await self._run_checkpoint()
                except Exception:
                    logger.exception("WAL checkpoint on disconnect failed")
                await self._conn.close()
                self._conn = None
                logger.info("Database disconnected")

    async def checkpoint(self) -> bool:
        """Force a TRUNCATE WAL checkpoint to keep the ``-wal`` sidecar bounded.

        The default PASSIVE auto-checkpoint writes WAL pages back into the DB but never
        shrinks the WAL file on disk, so under continuous history writes it can grow
        without bound. A TRUNCATE checkpoint resets the file once no read snapshot is
        pinning it.

        The checkpoint runs on a short-lived private ``sqlite3`` connection off the
        event loop (via a worker thread) rather than the shared ``aiosqlite``
        connection. That way it never commits another coroutine's in-flight
        transaction/savepoint and never blocks the shared connection's operation queue
        behind SQLite's busy timeout. Runs are serialized with ``disconnect()`` via
        ``_checkpoint_lock`` so a restore that disconnects and rewrites the file never
        races an in-flight checkpoint (the worker thread cannot be cancelled). Returns
        ``True`` only when the WAL was actually checkpointed, ``False`` for in-memory
        databases, a disconnected ``Database``, or a busy/locked result. See issue #908.
        """
        from obs.ringbuffer.ringbuffer import _is_sqlite_memory_path

        if self._conn is None or _is_sqlite_memory_path(self._path):
            return False
        async with self._checkpoint_lock:
            # Re-check under the lock: disconnect may have closed the DB while we waited.
            if self._conn is None:
                return False
            return await self._run_checkpoint()

    async def _run_checkpoint(self) -> bool:
        """Execute the TRUNCATE checkpoint. Caller must hold ``_checkpoint_lock``.

        Skips in-memory databases (including named ``file:…?mode=memory`` URIs, which
        would otherwise be normalized to a real on-disk filename). The checkpoint runs in
        a worker thread that cannot be cancelled; if this coroutine is cancelled
        (shutdown), we still wait for that worker to finish before propagating the
        cancellation, so the caller keeps ``_checkpoint_lock`` until the thread has
        released its SQLite locks and a following disconnect/restore can't race it. See
        issue #908.
        """
        from obs.ringbuffer.ringbuffer import _is_sqlite_memory_path, _sqlite_filesystem_path

        if _is_sqlite_memory_path(self._path):
            return False
        fs_path = _sqlite_filesystem_path(self._path)

        def _run() -> bool:
            conn = sqlite3.connect(fs_path, timeout=_CHECKPOINT_BUSY_TIMEOUT_SECONDS)
            try:
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.OperationalError as exc:
                # Non-waiting busy timeout surfaces contention as "database is locked";
                # treat it as a skipped checkpoint rather than an error to retry later.
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    return False
                raise
            finally:
                conn.close()
            # row = (busy, log_pages, checkpointed_pages); busy != 0 → WAL not reset.
            return bool(row is not None and row[0] == 0)

        fut = asyncio.get_running_loop().run_in_executor(None, _run)
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            # The worker thread can't be cancelled — wait for it to finish (releasing its
            # SQLite locks) before we unwind and release _checkpoint_lock.
            await asyncio.wait({fut})
            raise

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    async def _current_version(self) -> int:
        await self._conn.execute(_SCHEMA_VERSION_DDL)
        await self._conn.commit()

        async with self._conn.execute("SELECT MAX(version) AS v FROM schema_version") as cur:
            row = await cur.fetchone()
            return row["v"] if row["v"] is not None else 0

    async def _run_migrations(self) -> None:
        current = await self._current_version()
        for version, migration in MIGRATIONS:
            if version > current:
                logger.info("Applying DB migration v%d …", version)
                if callable(migration):
                    await migration(self._conn)
                else:
                    await self._conn.executescript(migration)
                await self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                await self._conn.commit()
                logger.info("DB migration v%d applied", version)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def _open_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self._connection_path, uri=self._connection_uri)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Run task-local helper calls on an isolated SQLite connection."""
        task = asyncio.current_task()
        assert task is not None
        if task in self._transaction_connections:
            raise RuntimeError("Nested database transactions are not supported")

        # Checkpoints, disconnect/restore, and explicit transactions all hold this
        # lifecycle guard. A restore therefore cannot close or replace the database
        # file while a dedicated transaction is still using it.
        async with self._checkpoint_lock:
            if self._conn is None:
                raise RuntimeError("Database.connect() has not been called")
            transaction_conn = await self._open_connection()
            began = False
            try:
                self._transaction_connections[task] = transaction_conn
                await transaction_conn.execute("BEGIN IMMEDIATE")
                began = True
                yield
                await transaction_conn.commit()
            except BaseException:
                if began:
                    await transaction_conn.rollback()
                raise
            finally:
                self._transaction_connections.pop(task, None)
                await transaction_conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            transaction_conn = self._transaction_connections.get(task)
            if transaction_conn is not None:
                return transaction_conn
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._conn

    async def execute(self, sql: str, params: Any = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def executemany(self, sql: str, params: Any) -> aiosqlite.Cursor:
        return await self.conn.executemany(sql, params)

    async def commit(self) -> None:
        await self.conn.commit()

    async def rollback(self) -> None:
        await self.conn.rollback()

    async def fetchall(self, sql: str, params: Any = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetchone(self, sql: str, params: Any = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def execute_and_commit(self, sql: str, params: Any = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_db: Database | None = None


def get_db() -> Database:
    """Return the initialized Database singleton."""
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() at startup")
    return _db


def reset_db() -> None:
    """Reset the Database singleton. For testing only."""
    global _db
    _db = None


async def init_db(path: str) -> Database:
    """Initialize and connect the singleton Database. Call once at startup."""
    global _db
    _db = Database(path)
    await _db.connect()
    return _db
