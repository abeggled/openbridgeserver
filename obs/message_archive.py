"""Persistent message archive storage and service.

Message archives intentionally use a SQLite database separate from the normal
OBS configuration/runtime database. The default location is derived from the
main DB path: ``<main-db-dir>/archives/messages.sqlite3``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from obs.config import Settings, get_settings
from obs.core.json import json_dumps

logger = logging.getLogger(__name__)

ArchiveStatus = Literal["ok", "degraded"]

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS message_archives (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    tags                  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    default_type          TEXT,
    color                 TEXT NOT NULL DEFAULT '#3b82f6',
    retention_max_entries INTEGER,
    retention_max_age_days INTEGER,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_archive_entries (
    id              TEXT PRIMARY KEY,
    archive_id      TEXT NOT NULL REFERENCES message_archives(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'system',
    severity        TEXT NOT NULL DEFAULT 'info',
    status          TEXT NOT NULL DEFAULT 'new',
    source          TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    acknowledged_at TEXT,
    acknowledged_by TEXT
);

CREATE TABLE IF NOT EXISTS message_archive_read_states (
    entry_id  TEXT NOT NULL REFERENCES message_archive_entries(id) ON DELETE CASCADE,
    username  TEXT NOT NULL,
    read_at   TEXT NOT NULL,
    hidden_at TEXT,
    PRIMARY KEY (entry_id, username)
);

CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_archive_created
    ON message_archive_entries(archive_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_created
    ON message_archive_entries(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_status
    ON message_archive_entries(status);
CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_type
    ON message_archive_entries(type);
CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_severity
    ON message_archive_entries(severity);
CREATE INDEX IF NOT EXISTS idx_msg_archive_entries_source
    ON message_archive_entries(source);
CREATE INDEX IF NOT EXISTS idx_msg_archive_read_user
    ON message_archive_read_states(username, entry_id);
"""

MIGRATIONS: list[tuple[int, str]] = [(1, _MIGRATION_V1)]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_entry_timestamp(value: str | None, *, field_name: str = "created_at") -> str:
    if value is None or not value.strip():
        return utc_now()
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{field_name} must be a valid ISO timestamp") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat()


def default_message_archive_db_path(database_path: str) -> str:
    return str(Path(database_path).expanduser().parent / "archives" / "messages.sqlite3")


def resolve_message_archive_db_path(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    configured = cfg.message_archive.path
    if configured:
        return str(Path(configured).expanduser())
    return default_message_archive_db_path(cfg.database.path)


def _json_loads_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _normalize_archive_id(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("archive_id must not be empty")
    if len(normalized) > 80:
        raise ValueError("archive_id must be at most 80 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if any(ch not in allowed for ch in normalized):
        raise ValueError("archive_id may only contain letters, numbers, '.', '_' and '-'")
    return normalized


def _default_archive_name(archive_id: str) -> str:
    if archive_id == "system":
        return "System"
    return archive_id


@dataclass(frozen=True)
class ArchiveInput:
    id: str
    name: str
    description: str = ""
    tags: list[str] | None = None
    default_type: str | None = None
    color: str = "#3b82f6"
    retention_max_entries: int | None = None
    retention_max_age_days: int | None = None


@dataclass(frozen=True)
class ArchivePatch:
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    default_type: str | None = None
    color: str | None = None
    retention_max_entries: int | None = None
    retention_max_age_days: int | None = None
    fields_set: set[str] | None = None


@dataclass(frozen=True)
class EntryInput:
    archive_id: str
    type: str | None = None
    severity: str = "info"
    status: str = "new"
    source: str = ""
    title: str = ""
    message: str = ""
    payload: dict[str, Any] | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class EntryPatch:
    type: str | None = None
    severity: str | None = None
    status: str | None = None
    source: str | None = None
    title: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class EntryPredicate:
    archive_ids: list[str] | None = None
    types: list[str] | None = None
    severities: list[str] | None = None
    statuses: list[str] | None = None
    sources: list[str] | None = None


@dataclass(frozen=True)
class EntryQuery:
    archive_ids: list[str] | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    status: str | None = None
    statuses: list[str] | None = None
    read_state: Literal["read", "unread"] | None = None
    type: str | None = None
    types: list[str] | None = None
    severity: str | None = None
    severities: list[str] | None = None
    source: str | None = None
    sources: list[str] | None = None
    q: str | None = None
    limit: int = 100
    offset: int = 0
    sort: Literal["asc", "desc"] = "desc"
    username: str | None = None
    predicates: list[EntryPredicate] | None = None


class MessageArchiveStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self.status: ArchiveStatus = "ok"
        self.last_error: str | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.commit()
        await self._run_migrations()
        self.status = "ok"
        self.last_error = None
        logger.info("Message archive database connected: %s", self.path)

    async def disconnect(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Message archive database disconnected")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("MessageArchiveStore.connect() has not been called")
        return self._conn

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    async def _current_version(self) -> int:
        await self.conn.execute(_SCHEMA_VERSION_DDL)
        await self.conn.commit()
        async with self.conn.execute("SELECT MAX(version) AS v FROM schema_version") as cur:
            row = await cur.fetchone()
            return int(row["v"]) if row and row["v"] is not None else 0

    async def _run_migrations(self) -> None:
        current = await self._current_version()
        for version, sql in MIGRATIONS:
            if version > current:
                await self.conn.executescript(sql)
                await self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                await self.conn.commit()

    async def _fetchone(self, sql: str, params: Any = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Any = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def ensure_archive(self, archive_id: str, *, name: str | None = None) -> None:
        archive_id = _normalize_archive_id(archive_id)
        now = utc_now()
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO message_archives
                (id, name, description, tags, default_type, color, retention_max_entries, retention_max_age_days, created_at, updated_at)
            VALUES (?, ?, '', '[]', NULL, '#3b82f6', NULL, NULL, ?, ?)
            """,
            (archive_id, name or _default_archive_name(archive_id), now, now),
        )
        if archive_id == "system":
            await self.conn.execute(
                "UPDATE message_archives SET name=?, updated_at=? WHERE id=? AND name=?",
                ("System", now, archive_id, "system"),
            )
        await self.conn.commit()

    async def create_archive(self, body: ArchiveInput) -> dict[str, Any]:
        archive_id = _normalize_archive_id(body.id)
        now = utc_now()
        await self.conn.execute(
            """
            INSERT INTO message_archives
                (id, name, description, tags, default_type, color, retention_max_entries, retention_max_age_days, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_id,
                body.name.strip() or archive_id,
                body.description,
                json_dumps(body.tags or []),
                body.default_type,
                body.color,
                body.retention_max_entries,
                body.retention_max_age_days,
                now,
                now,
            ),
        )
        await self.conn.commit()
        return await self.get_archive(archive_id) or {}

    async def list_archives(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            """
            SELECT
                a.*,
                COUNT(e.id) AS entry_count,
                MIN(e.created_at) AS oldest_entry_at,
                MAX(e.created_at) AS newest_entry_at
            FROM message_archives a
            LEFT JOIN message_archive_entries e ON e.archive_id = a.id
            GROUP BY a.id
            ORDER BY lower(a.name), a.id
            """
        )
        return [self._archive_row(row) for row in rows]

    async def get_archive(self, archive_id: str) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        row = await self._fetchone(
            """
            SELECT
                a.*,
                COUNT(e.id) AS entry_count,
                MIN(e.created_at) AS oldest_entry_at,
                MAX(e.created_at) AS newest_entry_at
            FROM message_archives a
            LEFT JOIN message_archive_entries e ON e.archive_id = a.id
            WHERE a.id = ?
            GROUP BY a.id
            """,
            (archive_id,),
        )
        return self._archive_row(row) if row else None

    async def update_archive(self, archive_id: str, body: ArchivePatch) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        updates: list[str] = []
        params: list[Any] = []
        fields_set = body.fields_set
        for field in (
            "name",
            "description",
            "default_type",
            "color",
            "retention_max_entries",
            "retention_max_age_days",
        ):
            value = getattr(body, field)
            if value is not None or (fields_set is not None and field in fields_set):
                updates.append(f"{field}=?")
                params.append(value)
        if body.tags is not None or (fields_set is not None and "tags" in fields_set):
            updates.append("tags=?")
            params.append(json_dumps(body.tags or []))
        if updates:
            updates.append("updated_at=?")
            params.append(utc_now())
            params.append(archive_id)
            await self.conn.execute(f"UPDATE message_archives SET {', '.join(updates)} WHERE id=?", params)
            await self.conn.commit()
            if fields_set and {"retention_max_entries", "retention_max_age_days"} & fields_set:
                await self.enforce_retention(archive_id)
        return await self.get_archive(archive_id)

    async def delete_archive(self, archive_id: str) -> int:
        archive_id = _normalize_archive_id(archive_id)
        row = await self._fetchone("SELECT COUNT(*) AS c FROM message_archive_entries WHERE archive_id=?", (archive_id,))
        count = int(row["c"]) if row else 0
        cur = await self.conn.execute("DELETE FROM message_archives WHERE id=?", (archive_id,))
        await self.conn.commit()
        return count if cur.rowcount else -1

    async def clear_archive(self, archive_id: str) -> int:
        archive_id = _normalize_archive_id(archive_id)
        row = await self._fetchone("SELECT COUNT(*) AS c FROM message_archive_entries WHERE archive_id=?", (archive_id,))
        count = int(row["c"]) if row else 0
        await self.conn.execute("DELETE FROM message_archive_entries WHERE archive_id=?", (archive_id,))
        await self.conn.commit()
        return count

    async def create_entry(self, body: EntryInput) -> dict[str, Any]:
        archive_id = _normalize_archive_id(body.archive_id)
        await self.ensure_archive(archive_id)
        archive = await self.get_archive(archive_id)
        entry_id = str(uuid.uuid4())
        created_at = _normalize_entry_timestamp(body.created_at)
        entry_type = body.type or (archive or {}).get("default_type") or "system"
        await self.conn.execute(
            """
            INSERT INTO message_archive_entries
                (id, archive_id, created_at, updated_at, type, severity, status, source, title, message, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                archive_id,
                created_at,
                created_at,
                entry_type,
                body.severity,
                body.status,
                body.source,
                body.title,
                body.message,
                json_dumps(body.payload or {}),
            ),
        )
        await self.conn.commit()
        await self.enforce_retention(archive_id)
        entry = await self.get_entry(archive_id, entry_id)
        if entry is None:
            raise ValueError("Message archive entry was removed by retention before it could be returned")
        return entry

    async def update_entry(self, archive_id: str, entry_id: str, body: EntryPatch) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        updates: list[str] = []
        params: list[Any] = []
        for field in ("type", "severity", "status", "source", "title", "message"):
            value = getattr(body, field)
            if value is not None:
                updates.append(f"{field}=?")
                params.append(value)
        if body.payload is not None:
            updates.append("payload=?")
            params.append(json_dumps(body.payload))
        if updates:
            updates.append("updated_at=?")
            params.extend([utc_now(), archive_id, entry_id])
            await self.conn.execute(
                f"UPDATE message_archive_entries SET {', '.join(updates)} WHERE archive_id=? AND id=?",
                params,
            )
            await self.conn.commit()
        return await self.get_entry(archive_id, entry_id)

    async def acknowledge_entry(self, archive_id: str, entry_id: str, username: str) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        exists = await self._fetchone(
            "SELECT id FROM message_archive_entries WHERE archive_id=? AND id=?",
            (archive_id, entry_id),
        )
        if not exists:
            return None
        now = utc_now()
        await self.conn.execute(
            """
            UPDATE message_archive_entries
            SET status='acknowledged', acknowledged_at=?, acknowledged_by=?, updated_at=?
            WHERE archive_id=? AND id=?
            """,
            (now, username, now, archive_id, entry_id),
        )
        await self.conn.execute(
            """
            INSERT INTO message_archive_read_states (entry_id, username, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(entry_id, username) DO UPDATE SET read_at=excluded.read_at
            """,
            (entry_id, username, now),
        )
        await self.conn.commit()
        return await self.get_entry(archive_id, entry_id, username=username)

    async def mark_read(self, archive_id: str, entry_id: str, username: str) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        exists = await self._fetchone(
            "SELECT id, status FROM message_archive_entries WHERE archive_id=? AND id=?",
            (archive_id, entry_id),
        )
        if not exists:
            return None
        now = utc_now()
        if exists["status"] == "new":
            await self.conn.execute(
                """
                UPDATE message_archive_entries
                SET status='open', updated_at=?
                WHERE archive_id=? AND id=?
                """,
                (now, archive_id, entry_id),
            )
        await self.conn.execute(
            """
            INSERT INTO message_archive_read_states (entry_id, username, read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(entry_id, username) DO UPDATE SET read_at=excluded.read_at
            """,
            (entry_id, username, now),
        )
        await self.conn.commit()
        return await self.get_entry(archive_id, entry_id, username=username)

    async def get_entry(self, archive_id: str, entry_id: str, username: str | None = None) -> dict[str, Any] | None:
        archive_id = _normalize_archive_id(archive_id)
        read_join = ""
        read_select = "NULL AS read_at"
        params: list[Any] = []
        if username:
            read_select = "rs.read_at AS read_at"
            read_join = "LEFT JOIN message_archive_read_states rs ON rs.entry_id=e.id AND rs.username=?"
            params.append(username)
        params.extend([archive_id, entry_id])
        row = await self._fetchone(
            f"""
            SELECT e.*, a.name AS archive_name, a.color AS archive_color, {read_select}
            FROM message_archive_entries e
            JOIN message_archives a ON a.id=e.archive_id
            {read_join}
            WHERE e.archive_id=? AND e.id=?
            """,
            params,
        )
        return self._entry_row(row) if row else None

    async def query_entries(self, query: EntryQuery) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        read_join = ""
        read_select = "NULL AS read_at"
        if query.username:
            read_select = "rs.read_at AS read_at"
            read_join = "LEFT JOIN message_archive_read_states rs ON rs.entry_id=e.id AND rs.username=?"
            params.append(query.username)

        if query.predicates is not None:
            predicate_sql: list[str] = []
            for predicate in query.predicates:
                group: list[str] = []
                for field, attr, normalize in (
                    ("archive_id", "archive_ids", True),
                    ("type", "types", False),
                    ("severity", "severities", False),
                    ("status", "statuses", False),
                    ("source", "sources", False),
                ):
                    values = getattr(predicate, attr)
                    if values is None:
                        continue
                    cleaned = [_normalize_archive_id(value) if normalize else value for value in values if value]
                    if not cleaned:
                        group.append("0")
                        continue
                    placeholders = ",".join("?" for _ in cleaned)
                    group.append(f"e.{field} IN ({placeholders})")
                    params.extend(cleaned)
                predicate_sql.append(f"({' AND '.join(group)})" if group else "1")
            where.append(f"({' OR '.join(predicate_sql)})" if predicate_sql else "0")

        if query.archive_ids:
            query_archive_ids = [_normalize_archive_id(archive_id) for archive_id in query.archive_ids]
            placeholders = ",".join("?" for _ in query.archive_ids)
            where.append(f"e.archive_id IN ({placeholders})")
            params.extend(query_archive_ids)
        if query.from_ts:
            where.append("e.created_at >= ?")
            params.append(_normalize_entry_timestamp(query.from_ts, field_name="from_ts"))
        if query.to_ts:
            where.append("e.created_at <= ?")
            params.append(_normalize_entry_timestamp(query.to_ts, field_name="to_ts"))
        for field, single_name, multi_name in (
            ("status", "status", "statuses"),
            ("type", "type", "types"),
            ("severity", "severity", "severities"),
            ("source", "source", "sources"),
        ):
            values = getattr(query, multi_name)
            if values:
                placeholders = ",".join("?" for _ in values)
                where.append(f"e.{field} IN ({placeholders})")
                params.extend(values)
                continue
            value = getattr(query, single_name)
            if value:
                where.append(f"e.{field} = ?")
                params.append(value)
        if query.q:
            where.append("(e.title LIKE ? OR e.message LIKE ?)")
            like = f"%{query.q}%"
            params.extend([like, like])
        if query.read_state == "read":
            where.append("rs.read_at IS NOT NULL")
        elif query.read_state == "unread":
            where.append("rs.read_at IS NULL")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_params = params.copy()
        if read_join and query.username:
            # count query has the same leading username parameter as data query.
            pass
        total_row = await self._fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM message_archive_entries e
            JOIN message_archives a ON a.id=e.archive_id
            {read_join}
            {where_sql}
            """,
            count_params,
        )
        order = "ASC" if query.sort == "asc" else "DESC"
        data_params = params + [query.limit, query.offset]
        rows = await self._fetchall(
            f"""
            SELECT e.*, a.name AS archive_name, a.color AS archive_color, {read_select}
            FROM message_archive_entries e
            JOIN message_archives a ON a.id=e.archive_id
            {read_join}
            {where_sql}
            ORDER BY e.created_at {order}, e.id {order}
            LIMIT ? OFFSET ?
            """,
            data_params,
        )
        return {
            "items": [self._entry_row(row) for row in rows],
            "total": int(total_row["c"]) if total_row else 0,
            "limit": query.limit,
            "offset": query.offset,
        }

    async def enforce_retention(self, archive_id: str) -> int:
        archive_id = _normalize_archive_id(archive_id)
        archive = await self.get_archive(archive_id)
        if not archive:
            return 0
        deleted = 0
        max_age_days = archive.get("retention_max_age_days")
        if isinstance(max_age_days, int) and max_age_days > 0:
            cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
            cur = await self.conn.execute(
                "DELETE FROM message_archive_entries WHERE archive_id=? AND created_at < ?",
                (archive_id, cutoff),
            )
            deleted += max(cur.rowcount or 0, 0)
        max_entries = archive.get("retention_max_entries")
        if isinstance(max_entries, int) and max_entries > 0:
            cur = await self.conn.execute(
                """
                DELETE FROM message_archive_entries
                WHERE archive_id=?
                  AND id NOT IN (
                    SELECT id FROM message_archive_entries
                    WHERE archive_id=?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                  )
                """,
                (archive_id, archive_id, max_entries),
            )
            deleted += max(cur.rowcount or 0, 0)
        await self.conn.commit()
        return deleted

    async def integrity_check(self) -> dict[str, Any]:
        try:
            row = await self._fetchone("PRAGMA integrity_check")
            result = str(row[0]) if row else "unknown"
            ok = result.lower() == "ok"
            self.status = "ok" if ok else "degraded"
            self.last_error = None if ok else result
            return {"ok": ok, "result": result, "path": self.path, "status": self.status}
        except Exception:
            self.status = "degraded"
            self.last_error = "integrity_check_failed"
            logger.exception("Message archive integrity check failed")
            return {"ok": False, "result": "integrity_check_failed", "path": self.path, "status": self.status}

    async def sqlite_snapshot(self, target_path: str | Path) -> Path:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = await aiosqlite.connect(str(target))
        try:
            await self.conn.backup(backup)
            await backup.commit()
        finally:
            await backup.close()
        return target

    async def export_jsonl(self, archive_id: str | None = None) -> str:
        rows = await self._export_entries(archive_id)
        return "\n".join(json_dumps(item) for item in rows)

    async def export_csv(self, archive_id: str | None = None) -> str:
        rows = await self._export_entries(archive_id)
        output = io.StringIO()
        fieldnames = [
            "id",
            "archive_id",
            "created_at",
            "type",
            "severity",
            "status",
            "source",
            "title",
            "message",
            "acknowledged_at",
            "acknowledged_by",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows:
            writer.writerow({field: item.get(field) for field in fieldnames})
        return output.getvalue()

    async def _export_entries(self, archive_id: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if archive_id:
            where = "WHERE e.archive_id=?"
            params.append(_normalize_archive_id(archive_id))
        rows = await self._fetchall(
            f"""
            SELECT e.*, a.name AS archive_name, a.color AS archive_color, NULL AS read_at
            FROM message_archive_entries e
            JOIN message_archives a ON a.id=e.archive_id
            {where}
            ORDER BY e.created_at ASC, e.id ASC
            """,
            params,
        )
        return [self._entry_row(row) for row in rows]

    def _archive_row(self, row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "tags": _json_loads_list(row["tags"]),
            "default_type": row["default_type"],
            "color": row["color"],
            "retention_max_entries": row["retention_max_entries"],
            "retention_max_age_days": row["retention_max_age_days"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "entry_count": int(row["entry_count"] or 0),
            "oldest_entry_at": row["oldest_entry_at"],
            "newest_entry_at": row["newest_entry_at"],
            "db_status": self.status,
            "db_path": self.path,
        }

    def _entry_row(self, row: aiosqlite.Row) -> dict[str, Any]:
        read_at = row["read_at"]
        return {
            "id": row["id"],
            "archive_id": row["archive_id"],
            "archive_name": row["archive_name"],
            "archive_color": row["archive_color"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "type": row["type"],
            "severity": row["severity"],
            "status": row["status"],
            "source": row["source"],
            "title": row["title"],
            "message": row["message"],
            "payload": _json_loads_object(row["payload"]),
            "acknowledged_at": row["acknowledged_at"],
            "acknowledged_by": row["acknowledged_by"],
            "read_at": read_at,
            "is_read": read_at is not None,
        }


class MessageArchiveService:
    def __init__(self, store: MessageArchiveStore) -> None:
        self.store = store

    async def record(
        self,
        archive_id: str,
        *,
        type: str = "system",
        severity: str = "info",
        source: str = "",
        title: str = "",
        message: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "new",
    ) -> dict[str, Any]:
        entry = await self.store.create_entry(
            EntryInput(
                archive_id=archive_id,
                type=type,
                severity=severity,
                status=status,
                source=source,
                title=title,
                message=message,
                payload=payload,
            )
        )
        await broadcast_message_archive_entry(entry)
        return entry


async def broadcast_message_archive_entry(entry: dict[str, Any], previous_entry: dict[str, Any] | None = None) -> None:
    try:
        from obs.api.v1.websocket import get_ws_manager

        manager = get_ws_manager()
    except RuntimeError:
        return
    except Exception:
        logger.exception("Message archive WebSocket manager unavailable")
        return

    try:
        await manager.broadcast_message_archive_entry(entry, previous_entry=previous_entry)
    except Exception:
        logger.exception("Message archive WebSocket broadcast failed")


_store: MessageArchiveStore | None = None
_service: MessageArchiveService | None = None


async def init_message_archive_store(settings: Settings | None = None) -> MessageArchiveStore:
    global _store, _service
    path = resolve_message_archive_db_path(settings)
    _store = MessageArchiveStore(path)
    try:
        await _store.connect()
    except Exception as exc:
        _store.status = "degraded"
        _store.last_error = str(exc)
        _service = None
        logger.error("Message archive database degraded: %s", exc)
    else:
        _service = MessageArchiveService(_store)
    return _store


def get_message_archive_store() -> MessageArchiveStore:
    if _store is None:
        raise RuntimeError("Message archive store not initialized")
    return _store


def activate_message_archive_service(store: MessageArchiveStore) -> None:
    global _store, _service
    if store.status != "ok" or not store.is_connected:
        raise RuntimeError("Message archive store is not connected")
    _store = store
    _service = MessageArchiveService(store)


def get_message_archive_service() -> MessageArchiveService:
    if _service is None or _store is None or not _store.is_connected or _store.status != "ok":
        raise RuntimeError("Message archive service not initialized")
    return _service


async def close_message_archive_store() -> None:
    global _store, _service
    if _store is not None:
        await _store.disconnect()
    _store = None
    _service = None


def reset_message_archive_store() -> None:
    global _store, _service
    _store = None
    _service = None
