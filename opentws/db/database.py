"""
SQLite Database Layer — Phase 1

Uses aiosqlite for async access.
Includes a simple version-based migration system.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

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
    """
    Multi-Instance Support:
    - Neue Tabelle adapter_instances (UUID PK, N Instanzen pro Typ)
    - adapter_bindings bekommt adapter_instance_id Spalte
    - Bestehende adapter_configs Daten werden migriert
    - Bestehende Bindings erhalten die passende adapter_instance_id
    """
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

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
        await conn.execute(
            "ALTER TABLE adapter_bindings ADD COLUMN adapter_instance_id TEXT"
        )
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
                row["adapter_type"],   # Name = Typ-String als Default
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

# List of (version, sql_or_callable) tuples — append new migrations here
MIGRATIONS: list[tuple[int, str | Callable]] = [
    (1, _MIGRATION_V1),
    (2, _MIGRATION_V2),
    (3, _MIGRATION_V3),
    (4, _MIGRATION_V4),
    (5, _migration_v5),
    (6, _MIGRATION_V6),
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

        async with self._conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ) as cur:
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
                await self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
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


async def init_db(path: str) -> Database:
    """Initialize and connect the singleton Database. Call once at startup."""
    global _db
    _db = Database(path)
    await _db.connect()
    return _db
