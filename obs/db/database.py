"""SQLite Database Layer — Phase 1

Uses aiosqlite for async access.
Includes a simple version-based migration system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

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
    config          TEXT NOT NULL DEFAULT '{}',
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
ALTER TABLE adapter_bindings ADD COLUMN value_map TEXT;
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
]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """Async SQLite database wrapper with built-in migration support."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._path not in (":memory:", "file::memory:?cache=shared"):
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row

        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.commit()

        await self._run_migrations()
        logger.info("Database connected: %s", self._path)

    async def disconnect(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Database disconnected")

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

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._conn

    async def execute(self, sql: str, params: Any = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def executemany(self, sql: str, params: Any) -> aiosqlite.Cursor:
        return await self.conn.executemany(sql, params)

    async def commit(self) -> None:
        await self.conn.commit()

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
