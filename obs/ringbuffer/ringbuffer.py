"""RingBuffer Debug Log — Phase 6 (Storage v2)

Zeichnet jede Werteänderung auf. Storage-Modelle:
  file    — SQLite WAL-Mode (überlebt Neustarts)
  disk    — Legacy-Modellname (file-basiert)
  memory  — Legacy-Modellname (:memory:, nur für Altpfade/Tests)

Filterfunktionen:
  q       — Substring in datapoint_id oder source_adapter
  adapter — exakt source_adapter
  from_ts — ISO-8601 Timestamp (exkl.)
  limit   — max. Einträge (default: 100)

Bei Modellwechsel wird der RingBuffer leer neu gestartet (keine Migration).
Älteste Einträge werden überschrieben, wenn max_entries erreicht.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)
_UNSET = object()
_ALLOWED_STORAGE_MODELS = {"memory", "disk", "file"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ringbuffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    datapoint_id   TEXT    NOT NULL,
    topic          TEXT    NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    source_adapter TEXT    NOT NULL,
    quality        TEXT    NOT NULL,
    metadata_version INTEGER NOT NULL DEFAULT 1,
    metadata       TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rb_ts  ON ringbuffer(ts);
CREATE INDEX IF NOT EXISTS idx_rb_dp  ON ringbuffer(datapoint_id);
CREATE INDEX IF NOT EXISTS idx_rb_adp ON ringbuffer(source_adapter);

CREATE TABLE IF NOT EXISTS ringbuffer_metadata_tags (
    entry_id INTEGER NOT NULL REFERENCES ringbuffer(id) ON DELETE CASCADE,
    tag      TEXT    NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_rb_meta_tag ON ringbuffer_metadata_tags(tag);

CREATE TABLE IF NOT EXISTS ringbuffer_metadata_bindings (
    entry_id             INTEGER NOT NULL REFERENCES ringbuffer(id) ON DELETE CASCADE,
    adapter_type         TEXT    NOT NULL DEFAULT '',
    adapter_instance_id  TEXT    NOT NULL DEFAULT '',
    group_address        TEXT    NOT NULL DEFAULT '',
    topic                TEXT    NOT NULL DEFAULT '',
    entity_id            TEXT    NOT NULL DEFAULT '',
    register_type        TEXT    NOT NULL DEFAULT '',
    register_address     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_adapter_type ON ringbuffer_metadata_bindings(adapter_type);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_adapter_instance ON ringbuffer_metadata_bindings(adapter_instance_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_group_address ON ringbuffer_metadata_bindings(group_address);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_topic ON ringbuffer_metadata_bindings(topic);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_entity_id ON ringbuffer_metadata_bindings(entity_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_register_type ON ringbuffer_metadata_bindings(register_type);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_register_address ON ringbuffer_metadata_bindings(register_address);
"""


@dataclass
class RingBufferEntry:
    id: int
    ts: str
    datapoint_id: str
    topic: str
    old_value: Any
    new_value: Any
    source_adapter: str
    quality: str
    metadata_version: int
    metadata: dict[str, Any]


class RingBuffer:
    """Async RingBuffer backed by SQLite.

    Lifecycle:
        rb = RingBuffer("file", max_entries=10000)
        await rb.start()
        bus.subscribe(DataValueEvent, rb.handle_value_event)
        ...
        await rb.stop()
    """

    def __init__(
        self,
        storage: str = "file",
        max_entries: int | None = 10000,
        disk_path: str = "/data/obs_ringbuffer.db",
        max_file_size_bytes: int | None = None,
        max_age: int | None = None,
    ) -> None:
        if storage not in _ALLOWED_STORAGE_MODELS:
            raise ValueError("storage must be one of: file, disk, memory")
        self._storage = storage
        self._max_entries = int(max_entries) if max_entries is not None else None
        if self._max_entries is not None and self._max_entries < 1:
            raise ValueError("max_entries must be >= 1 or null")
        self._disk_path = disk_path
        self._max_file_size_bytes = max_file_size_bytes
        self._max_age = max_age
        self._conn: aiosqlite.Connection | None = None
        self._last_values: dict[str, Any] = {}  # dp_id → last recorded value
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        path = ":memory:" if self._storage == "memory" else self._disk_path
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        if self._storage in {"disk", "file"}:
            await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._ensure_compat_schema()
        await self._conn.commit()
        logger.info(
            "RingBuffer started (%s, max_entries=%s, max_file_size_bytes=%s, max_age=%s)",
            self._storage,
            self._max_entries,
            self._max_file_size_bytes,
            self._max_age,
        )

    async def stop(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Runtime config switch
    # ------------------------------------------------------------------

    async def reconfigure(
        self,
        storage: str,
        max_entries: int | None | object = _UNSET,
        max_file_size_bytes: int | None | object = _UNSET,
        max_age: int | None | object = _UNSET,
    ) -> None:
        """Switch storage model at runtime.

        Same model: apply config in-place (keeps entries).
        Model switch: restart empty (no migration).
        """
        if storage not in _ALLOWED_STORAGE_MODELS:
            raise ValueError("storage must be one of: file, disk, memory")

        async with self._lock:
            resolved_max_entries = self._max_entries if max_entries is _UNSET else max_entries
            if resolved_max_entries is not None:
                resolved_max_entries = int(resolved_max_entries)
                if resolved_max_entries < 1:
                    raise ValueError("max_entries must be >= 1 or null")
            resolved_max_file_size = self._max_file_size_bytes if max_file_size_bytes is _UNSET else max_file_size_bytes
            resolved_max_age = self._max_age if max_age is _UNSET else max_age

            if (
                storage == self._storage
                and resolved_max_entries == self._max_entries
                and resolved_max_file_size == self._max_file_size_bytes
                and resolved_max_age == self._max_age
            ):
                return

            # Same model: apply config in-place and trim.
            if storage == self._storage:
                self._max_entries = resolved_max_entries
                self._max_file_size_bytes = int(resolved_max_file_size) if resolved_max_file_size is not None else None
                self._max_age = int(resolved_max_age) if resolved_max_age is not None else None
                await self._trim()
                logger.info(
                    "RingBuffer reconfigured in-place → %s, max_entries=%s, max_file_size_bytes=%s, max_age=%s",
                    storage,
                    self._max_entries,
                    self._max_file_size_bytes,
                    self._max_age,
                )
                return

            # Model switch: close old connection and start empty without migration.
            old_storage = self._storage
            if self._conn:
                await self._conn.close()

            self._storage = storage
            self._max_entries = resolved_max_entries
            self._max_file_size_bytes = int(resolved_max_file_size) if resolved_max_file_size is not None else None
            self._max_age = int(resolved_max_age) if resolved_max_age is not None else None

            # Open new connection
            path = ":memory:" if storage == "memory" else self._disk_path
            self._conn = await aiosqlite.connect(path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys=ON")
            if storage in {"disk", "file"}:
                await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.executescript(_SCHEMA)
            await self._ensure_compat_schema()
            await self._conn.execute("DELETE FROM ringbuffer")
            await self._conn.commit()
            self._last_values.clear()
            logger.info(
                "RingBuffer model switch: %s -> %s, restarted empty (max_entries=%s, max_file_size_bytes=%s, max_age=%s)",
                old_storage,
                storage,
                self._max_entries,
                self._max_file_size_bytes,
                self._max_age,
            )

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    async def record(
        self,
        ts: str,
        datapoint_id: str,
        topic: str,
        old_value: Any,
        new_value: Any,
        source_adapter: str,
        quality: str,
        metadata_version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._conn:
            return
        metadata_obj = metadata or {}
        async with self._lock:
            cursor = await self._conn.execute(
                """INSERT INTO ringbuffer
                   (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, metadata_version, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    datapoint_id,
                    topic,
                    json.dumps(old_value),
                    json.dumps(new_value),
                    source_adapter,
                    quality,
                    metadata_version,
                    json.dumps(metadata_obj),
                ),
            )
            await self._persist_metadata_indexes(cursor.lastrowid, metadata_obj)
            await self._conn.commit()
            await self._trim(reference_ts=ts)

    async def _trim(self, reference_ts: str | None = None) -> None:
        """Apply retention rules and keep max_entries compatibility."""
        if not self._conn:
            return

        while True:
            # Retention rule 1: disk size hard limit (oldest-first)
            if self._max_file_size_bytes is not None:
                current_size = await self._current_storage_bytes()
                if current_size > self._max_file_size_bytes:
                    removed = await self._delete_oldest(limit=1)
                    if removed == 0:
                        logger.warning(
                            "RingBuffer size trim blocked: size=%d limit=%d",
                            current_size,
                            self._max_file_size_bytes,
                        )
                        break
                    new_size = await self._current_storage_bytes()
                    await self._log_trim_event(
                        reason="size",
                        removed=removed,
                        before_value=current_size,
                        after_value=new_size,
                    )
                    continue

            # Retention rule 2: max age in seconds (strictly older than cutoff)
            removed_by_age = await self._trim_by_age(reference_ts=reference_ts)
            if removed_by_age > 0:
                continue

            # Legacy behavior from #383: count-based trim stays in place.
            removed_by_count = await self._trim_by_count()
            if removed_by_count > 0:
                continue
            break

    async def _trim_by_count(self) -> int:
        if not self._conn or self._max_entries is None:
            return 0
        async with self._conn.execute("SELECT COUNT(*) FROM ringbuffer") as cur:
            row = await cur.fetchone()
        count = row[0] if row else 0
        if count <= self._max_entries:
            return 0

        excess = count - self._max_entries
        removed = await self._delete_oldest(limit=excess)
        if removed:
            await self._log_trim_event(
                reason="count",
                removed=removed,
                before_value=count,
                after_value=count - removed,
            )
        return removed

    async def _trim_by_age(self, reference_ts: str | None) -> int:
        if not self._conn or self._max_age is None:
            return 0

        ref_ts = reference_ts
        if not ref_ts:
            async with self._conn.execute("SELECT MAX(ts) FROM ringbuffer") as cur:
                row = await cur.fetchone()
            ref_ts = row[0] if row else None
        if not ref_ts:
            return 0

        cutoff_dt = _parse_iso_ts(ref_ts) - timedelta(seconds=self._max_age)
        cutoff = _isoformat_utc(cutoff_dt)
        async with self._conn.execute("SELECT COUNT(*) FROM ringbuffer WHERE ts < ?", (cutoff,)) as cur:
            row = await cur.fetchone()
        remove_count = row[0] if row else 0
        if remove_count <= 0:
            return 0

        await self._conn.execute("DELETE FROM ringbuffer WHERE ts < ?", (cutoff,))
        await self._conn.commit()
        await self._log_trim_event(
            reason="age",
            removed=remove_count,
            before_value=ref_ts,
            after_value=cutoff,
        )
        return remove_count

    async def _delete_oldest(self, limit: int) -> int:
        if not self._conn or limit <= 0:
            return 0

        async with self._conn.execute(
            "SELECT id FROM ringbuffer ORDER BY id ASC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return 0

        ids = [row[0] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        await self._conn.execute(
            f"DELETE FROM ringbuffer WHERE id IN ({placeholders})",
            ids,
        )
        await self._conn.commit()
        if self._storage in {"disk", "file"}:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return len(ids)

    async def _current_storage_bytes(self) -> int:
        if not self._conn or self._storage == "memory":
            return 0

        async with self._conn.execute("PRAGMA page_size") as cur:
            page_size_row = await cur.fetchone()
        async with self._conn.execute("PRAGMA page_count") as cur:
            page_count_row = await cur.fetchone()
        async with self._conn.execute("PRAGMA freelist_count") as cur:
            freelist_row = await cur.fetchone()

        page_size = page_size_row[0] if page_size_row else 0
        page_count = page_count_row[0] if page_count_row else 0
        freelist_count = freelist_row[0] if freelist_row else 0
        used_bytes = max(page_count - freelist_count, 0) * page_size

        wal_bytes = 0
        wal_path = f"{self._disk_path}-wal"
        if os.path.exists(wal_path):
            wal_bytes = os.path.getsize(wal_path)
        return used_bytes + wal_bytes

    async def _log_trim_event(
        self,
        reason: str,
        removed: int,
        before_value: Any,
        after_value: Any,
    ) -> None:
        total = await self._count_entries()
        logger.info(
            "RingBuffer trim reason=%s removed=%d total=%d before=%s after=%s",
            reason,
            removed,
            total,
            before_value,
            after_value,
        )

    async def _count_entries(self) -> int:
        if not self._conn:
            return 0
        async with self._conn.execute("SELECT COUNT(*) FROM ringbuffer") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def _ensure_compat_schema(self) -> None:
        """Backfill columns for pre-#388 ringbuffer databases."""
        if not self._conn:
            return
        async with self._conn.execute("PRAGMA table_info(ringbuffer)") as cur:
            rows = await cur.fetchall()
        columns = {row["name"] for row in rows}
        if "metadata_version" not in columns:
            await self._conn.execute("ALTER TABLE ringbuffer ADD COLUMN metadata_version INTEGER NOT NULL DEFAULT 1")
        if "metadata" not in columns:
            await self._conn.execute("ALTER TABLE ringbuffer ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    # ------------------------------------------------------------------
    # EventBus handler
    # ------------------------------------------------------------------

    async def handle_value_event(self, event: Any) -> None:
        """Record a DataValueEvent into the ring buffer."""
        dp_id = str(event.datapoint_id)
        dp = None

        # Capture old value from our own tracking (reliable in asyncio)
        old_value = self._last_values.get(dp_id)
        self._last_values[dp_id] = event.value

        try:
            from obs.core.registry import get_registry

            dp = get_registry().get(event.datapoint_id)
            topic = dp.mqtt_topic if dp else f"dp/{dp_id}/value"
        except RuntimeError:
            topic = f"dp/{dp_id}/value"

        metadata = await self._build_metadata_snapshot(
            dp_id=dp_id,
            source_adapter=str(event.source_adapter),
            datapoint=dp,
        )
        await self.record(
            ts=event.ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            datapoint_id=dp_id,
            topic=topic,
            old_value=old_value,
            new_value=event.value,
            source_adapter=event.source_adapter,
            quality=event.quality,
            metadata_version=1,
            metadata=metadata,
        )

    async def _build_metadata_snapshot(
        self,
        *,
        dp_id: str,
        source_adapter: str,
        datapoint: Any,
    ) -> dict[str, Any]:
        bindings: list[dict[str, Any]] = []
        try:
            from obs.db.database import get_db

            rows = await get_db().fetchall(
                """SELECT adapter_type, adapter_instance_id, direction, config
                   FROM adapter_bindings
                   WHERE datapoint_id=? AND enabled=1
                   ORDER BY created_at, id""",
                (dp_id,),
            )
            for row in rows:
                raw_config = _safe_loads(row["config"])
                config = raw_config if isinstance(raw_config, dict) else {}
                bindings.append(
                    {
                        "adapter_type": str(row["adapter_type"] or ""),
                        "adapter_instance_id": str(row["adapter_instance_id"] or ""),
                        "direction": str(row["direction"] or ""),
                        "normalized": _normalize_binding_metadata(config),
                    }
                )
        except RuntimeError:
            pass
        except Exception:
            logger.exception("RingBuffer metadata snapshot for dp=%s failed", dp_id)

        tags = list(datapoint.tags) if datapoint and isinstance(getattr(datapoint, "tags", None), list) else []
        return {
            "source": {"adapter": source_adapter},
            "datapoint": {
                "id": dp_id,
                "name": getattr(datapoint, "name", None),
                "data_type": getattr(datapoint, "data_type", None),
                "tags": tags,
            },
            "bindings": bindings,
        }

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        q: str = "",
        adapter: str = "",
        from_ts: str = "",
        limit: int = 100,
        dp_ids: list[str] | None = None,
    ) -> list[RingBufferEntry]:
        return await self.query_v2(
            q=q,
            adapter_any_of=[adapter] if adapter else None,
            datapoint_ids=None,
            from_ts=from_ts or None,
            limit=limit,
            offset=0,
            sort_field="id",
            sort_order="desc",
            dp_ids_by_name=dp_ids,
        )

    async def query_v2(
        self,
        *,
        q: str = "",
        adapter_any_of: list[str] | None = None,
        datapoint_ids: list[str] | None = None,
        value_filters: list[dict[str, Any]] | None = None,
        metadata_tags_any_of: list[str] | None = None,
        metadata_adapter_types_any_of: list[str] | None = None,
        metadata_adapter_instance_ids_any_of: list[str] | None = None,
        metadata_group_addresses_any_of: list[str] | None = None,
        metadata_topics_any_of: list[str] | None = None,
        metadata_entity_ids_any_of: list[str] | None = None,
        metadata_register_types_any_of: list[str] | None = None,
        metadata_register_addresses_any_of: list[str] | None = None,
        datapoint_types: dict[str, str] | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        from_relative_seconds: int | None = None,
        to_relative_seconds: int | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_field: str = "id",
        sort_order: str = "desc",
        dp_ids_by_name: list[str] | None = None,
    ) -> list[RingBufferEntry]:
        if not self._conn:
            return []

        sql = "SELECT * FROM ringbuffer WHERE 1=1"
        params: list[Any] = []

        if q or dp_ids_by_name:
            parts: list[str] = []
            if q:
                parts += ["datapoint_id LIKE ?", "source_adapter LIKE ?"]
                params += [f"%{q}%", f"%{q}%"]
            if dp_ids_by_name:
                placeholders = ",".join("?" * len(dp_ids_by_name))
                parts.append(f"datapoint_id IN ({placeholders})")
                params += dp_ids_by_name
            sql += f" AND ({' OR '.join(parts)})"

        normalized_adapters = [adapter.strip() for adapter in (adapter_any_of or []) if adapter.strip()]
        if normalized_adapters:
            placeholders = ",".join("?" * len(normalized_adapters))
            sql += f" AND source_adapter IN ({placeholders})"
            params.extend(normalized_adapters)

        if datapoint_ids:
            normalized_dp_ids = [dp_id.strip() for dp_id in datapoint_ids if dp_id.strip()]
            if normalized_dp_ids:
                placeholders = ",".join("?" * len(normalized_dp_ids))
                sql += f" AND datapoint_id IN ({placeholders})"
                params.extend(normalized_dp_ids)

        normalized_meta_tags = _normalize_string_filters(metadata_tags_any_of)
        if normalized_meta_tags:
            placeholders = ",".join("?" * len(normalized_meta_tags))
            sql += f" AND EXISTS (SELECT 1 FROM ringbuffer_metadata_tags rmt WHERE rmt.entry_id = ringbuffer.id AND rmt.tag IN ({placeholders}))"
            params.extend(normalized_meta_tags)

        binding_clauses: list[str] = []
        binding_params: list[str] = []
        normalized_binding_filters = {
            "adapter_type": _normalize_string_filters(metadata_adapter_types_any_of),
            "adapter_instance_id": _normalize_string_filters(metadata_adapter_instance_ids_any_of),
            "group_address": _normalize_string_filters(metadata_group_addresses_any_of),
            "topic": _normalize_string_filters(metadata_topics_any_of),
            "entity_id": _normalize_string_filters(metadata_entity_ids_any_of),
            "register_type": _normalize_string_filters(metadata_register_types_any_of),
            "register_address": _normalize_string_filters(metadata_register_addresses_any_of),
        }
        for column, values in normalized_binding_filters.items():
            if not values:
                continue
            placeholders = ",".join("?" * len(values))
            binding_clauses.append(f"rmb.{column} IN ({placeholders})")
            binding_params.extend(values)
        if binding_clauses:
            sql += (
                f" AND EXISTS (SELECT 1 FROM ringbuffer_metadata_bindings rmb WHERE rmb.entry_id = ringbuffer.id AND {' AND '.join(binding_clauses)})"
            )
            params.extend(binding_params)

        effective_from = _resolve_time_bound(
            absolute_ts=from_ts,
            relative_seconds=from_relative_seconds,
            pick_newer=True,
        )
        effective_to = _resolve_time_bound(
            absolute_ts=to_ts,
            relative_seconds=to_relative_seconds,
            pick_newer=False,
        )
        if effective_from:
            sql += " AND ts > ?"
            params.append(effective_from)
        if effective_to:
            sql += " AND ts < ?"
            params.append(effective_to)
        if effective_from and effective_to and effective_from >= effective_to:
            raise ValueError("invalid time filter: effective 'from' must be earlier than effective 'to'")

        if sort_field not in {"id", "ts"}:
            raise ValueError("invalid sort field: expected 'id' or 'ts'")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("invalid sort order: expected 'asc' or 'desc'")
        if limit < 1:
            raise ValueError("invalid pagination: limit must be >= 1")
        if offset < 0:
            raise ValueError("invalid pagination: offset must be >= 0")

        direction = "ASC" if sort_order == "asc" else "DESC"
        if sort_field == "ts":
            sql += f" ORDER BY ts {direction}, id {direction}"
        else:
            sql += f" ORDER BY id {direction}"

        rows: list[Any]
        if value_filters:
            rows = await self._fetchall(sql, params)
        else:
            sql += " LIMIT ? OFFSET ?"
            params.append(limit)
            params.append(offset)
            rows = await self._fetchall(sql, params)

        entries = [
            RingBufferEntry(
                id=r["id"],
                ts=r["ts"],
                datapoint_id=r["datapoint_id"],
                topic=r["topic"],
                old_value=_safe_loads(r["old_value"]),
                new_value=_safe_loads(r["new_value"]),
                source_adapter=r["source_adapter"],
                quality=r["quality"],
                metadata_version=int(r["metadata_version"]) if "metadata_version" in r.keys() else 1,
                metadata=_safe_loads_dict(r["metadata"]) if "metadata" in r.keys() else {},
            )
            for r in rows
        ]
        if not value_filters:
            return entries

        filtered = await _apply_value_filters(
            entries=entries,
            value_filters=value_filters,
            datapoint_types=datapoint_types or {},
        )
        return filtered[offset : offset + limit]

    async def stats(self) -> dict:
        def _effective_retention_seconds(oldest_ts: str | None) -> int | None:
            if not oldest_ts:
                return None
            try:
                oldest_dt = _parse_iso_ts(oldest_ts)
            except ValueError:
                return None
            return max(0, int((datetime.now(UTC) - oldest_dt).total_seconds()))

        if not self._conn:
            return {
                "total": 0,
                "oldest_ts": None,
                "newest_ts": None,
                "storage": self._storage,
                "max_entries": self._max_entries,
                "effective_retention_seconds": None,
                "max_file_size_bytes": self._max_file_size_bytes,
                "max_age": self._max_age,
                "file_size_bytes": 0,
            }
        async with self._conn.execute("SELECT COUNT(*) AS c, MIN(ts) AS oldest, MAX(ts) AS newest FROM ringbuffer") as cur:
            row = await cur.fetchone()
        oldest_ts = row[1] if row else None
        return {
            "total": row[0] if row else 0,
            "oldest_ts": oldest_ts,
            "newest_ts": row[2] if row else None,
            "storage": self._storage,
            "max_entries": self._max_entries,
            "effective_retention_seconds": _effective_retention_seconds(oldest_ts),
            "max_file_size_bytes": self._max_file_size_bytes,
            "max_age": self._max_age,
            "file_size_bytes": await self._current_storage_bytes(),
        }

    async def _persist_metadata_indexes(self, entry_id: int, metadata: dict[str, Any]) -> None:
        if not self._conn or entry_id <= 0:
            return

        tags = _extract_metadata_tags(metadata)
        if tags:
            await self._conn.executemany(
                "INSERT OR IGNORE INTO ringbuffer_metadata_tags (entry_id, tag) VALUES (?, ?)",
                [(entry_id, tag) for tag in tags],
            )

        binding_rows = _extract_metadata_binding_index_rows(metadata)
        if binding_rows:
            await self._conn.executemany(
                """INSERT INTO ringbuffer_metadata_bindings
                   (entry_id, adapter_type, adapter_instance_id, group_address, topic, entity_id, register_type, register_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(entry_id, *row) for row in binding_rows],
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetchall(self, sql: str, params: list = []) -> list:
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()


def _safe_loads(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def _safe_loads_dict(s: str | None) -> dict[str, Any]:
    loaded = _safe_loads(s)
    return loaded if isinstance(loaded, dict) else {}


def _normalize_string_filters(values: list[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_binding_metadata(config: dict[str, Any]) -> dict[str, Any]:
    def _str_or_empty(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    return {
        "group_address": _str_or_empty(config.get("group_address")),
        "state_group_address": _str_or_empty(config.get("state_group_address")),
        "topic": _str_or_empty(config.get("topic")),
        "entity_id": _str_or_empty(config.get("entity_id")),
        "register_type": _str_or_empty(config.get("register_type")),
        "register_address": _str_or_empty(config.get("address")),
        "unit_id": _str_or_empty(config.get("unit_id")),
    }


def _extract_metadata_tags(metadata: dict[str, Any]) -> list[str]:
    datapoint = metadata.get("datapoint")
    if not isinstance(datapoint, dict):
        return []
    tags = datapoint.get("tags")
    if not isinstance(tags, list):
        return []
    return _normalize_string_filters([str(tag) for tag in tags])


def _extract_metadata_binding_index_rows(metadata: dict[str, Any]) -> list[tuple[str, str, str, str, str, str, str]]:
    raw_bindings = metadata.get("bindings")
    if not isinstance(raw_bindings, list):
        return []

    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for binding in raw_bindings:
        if not isinstance(binding, dict):
            continue
        normalized = binding.get("normalized")
        normalized_dict = normalized if isinstance(normalized, dict) else {}
        rows.append(
            (
                str(binding.get("adapter_type", "")).strip().lower(),
                str(binding.get("adapter_instance_id", "")).strip().lower(),
                str(normalized_dict.get("group_address", "")).strip().lower(),
                str(normalized_dict.get("topic", "")).strip().lower(),
                str(normalized_dict.get("entity_id", "")).strip().lower(),
                str(normalized_dict.get("register_type", "")).strip().lower(),
                str(normalized_dict.get("register_address", "")).strip().lower(),
            )
        )
    return rows


def _parse_iso_ts(value: str) -> datetime:
    raw_value = value
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {raw_value}") from exc


def _isoformat_utc(value: datetime) -> str:
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _resolve_time_bound(
    *,
    absolute_ts: str | None,
    relative_seconds: int | None,
    pick_newer: bool,
) -> str | None:
    absolute_value = _parse_iso_ts(absolute_ts) if absolute_ts else None
    relative_value = None
    if relative_seconds is not None:
        relative_value = datetime.now(UTC) + timedelta(seconds=relative_seconds)

    if absolute_value and relative_value:
        selected = max(absolute_value, relative_value) if pick_newer else min(absolute_value, relative_value)
        return _isoformat_utc(selected)
    if absolute_value:
        return _isoformat_utc(absolute_value)
    if relative_value:
        return _isoformat_utc(relative_value)
    return None


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_rb: RingBuffer | None = None


def get_ringbuffer() -> RingBuffer:
    if _rb is None:
        raise RuntimeError("RingBuffer not initialized")
    return _rb


def reset_ringbuffer() -> None:
    """Reset the RingBuffer singleton. For testing only."""
    global _rb
    _rb = None


async def init_ringbuffer(
    storage: str,
    max_entries: int | None,
    disk_path: str,
    max_file_size_bytes: int | None = None,
    max_age: int | None = None,
) -> RingBuffer:
    global _rb
    _rb = RingBuffer(storage, max_entries, disk_path, max_file_size_bytes, max_age)
    await _rb.start()
    return _rb


_NUMERIC_TYPES = {"FLOAT", "INTEGER"}
_BOOLEAN_TYPES = {"BOOLEAN"}
_STRING_TYPES = {"STRING"}
_REGEX_MAX_PATTERN_LEN = 256
_REGEX_MAX_TARGET_LEN = 4096
_REGEX_TIMEOUT_SECONDS = 0.05
_RE_UNSAFE_NESTED_QUANTIFIERS = re.compile(r"\((?:[^()\\]|\\.)*[+*][^()]*\)[+*]")


async def _apply_value_filters(
    *,
    entries: list[RingBufferEntry],
    value_filters: list[dict[str, Any]],
    datapoint_types: dict[str, str],
) -> list[RingBufferEntry]:
    normalized_filters = [_normalize_value_filter(spec) for spec in value_filters]
    result: list[RingBufferEntry] = []
    for entry in entries:
        data_type = (datapoint_types.get(entry.datapoint_id) or "").strip().upper()
        match = True
        for vf in normalized_filters:
            if not await _matches_value_filter(entry.new_value, data_type, vf):
                match = False
                break
        if match:
            result.append(entry)
    return result


def _normalize_value_filter(spec: dict[str, Any]) -> dict[str, Any]:
    operator = str(spec.get("operator", "")).strip().lower()
    if operator not in {"eq", "ne", "gt", "gte", "lt", "lte", "between", "contains", "regex"}:
        raise ValueError(f"invalid value filter operator: {operator!r}")
    return {
        "operator": operator,
        "value": spec.get("value"),
        "lower": spec.get("lower"),
        "upper": spec.get("upper"),
        "pattern": spec.get("pattern"),
        "ignore_case": bool(spec.get("ignore_case", False)),
    }


async def _matches_value_filter(value: Any, data_type: str, vf: dict[str, Any]) -> bool:
    operator = vf["operator"]
    if operator in {"eq", "ne"}:
        expected = vf["value"]
        is_equal = value == expected
        return is_equal if operator == "eq" else not is_equal

    if _is_numeric_type(data_type, value):
        return _match_numeric_operator(value, vf)
    if _is_string_type(data_type, value):
        return await _match_string_operator(value, vf)
    if _is_boolean_type(data_type, value):
        raise ValueError(f"operator '{operator}' is not supported for data_type 'BOOLEAN'")

    raise ValueError(f"operator '{operator}' is not supported for data_type '{data_type or 'UNKNOWN'}'")


def _is_numeric_type(data_type: str, value: Any) -> bool:
    if data_type in _NUMERIC_TYPES:
        return True
    return not data_type and isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_string_type(data_type: str, value: Any) -> bool:
    if data_type in _STRING_TYPES:
        return True
    return not data_type and isinstance(value, str)


def _is_boolean_type(data_type: str, value: Any) -> bool:
    if data_type in _BOOLEAN_TYPES:
        return True
    return not data_type and isinstance(value, bool)


def _to_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _match_numeric_operator(value: Any, vf: dict[str, Any]) -> bool:
    operator = vf["operator"]
    if operator not in {"gt", "gte", "lt", "lte", "between"}:
        raise ValueError(f"operator '{operator}' is not supported for data_type 'FLOAT'")

    actual = _to_number(value, field="row value")
    if operator == "gt":
        return actual > _to_number(vf["value"], field="filters.values[].value")
    if operator == "gte":
        return actual >= _to_number(vf["value"], field="filters.values[].value")
    if operator == "lt":
        return actual < _to_number(vf["value"], field="filters.values[].value")
    if operator == "lte":
        return actual <= _to_number(vf["value"], field="filters.values[].value")

    lower = _to_number(vf["lower"], field="filters.values[].lower")
    upper = _to_number(vf["upper"], field="filters.values[].upper")
    if lower > upper:
        raise ValueError("filters.values[].lower must be <= filters.values[].upper")
    return lower <= actual <= upper


async def _match_string_operator(value: Any, vf: dict[str, Any]) -> bool:
    operator = vf["operator"]
    if not isinstance(value, str):
        raise ValueError("row value must be string")

    if operator == "contains":
        needle = vf["value"]
        if not isinstance(needle, str):
            raise ValueError("filters.values[].value must be string")
        haystack = value.lower() if vf["ignore_case"] else value
        probe = needle.lower() if vf["ignore_case"] else needle
        return probe in haystack

    if operator == "regex":
        return await _match_regex(value, vf)

    raise ValueError(f"operator '{operator}' is not supported for data_type 'STRING'")


async def _match_regex(value: str, vf: dict[str, Any]) -> bool:
    pattern = vf["pattern"]
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("filters.values[].pattern must be a non-empty string")
    if len(pattern) > _REGEX_MAX_PATTERN_LEN:
        raise ValueError("unsafe regex pattern: pattern too long")
    if _RE_UNSAFE_NESTED_QUANTIFIERS.search(pattern):
        raise ValueError("unsafe regex pattern: nested quantifiers are not allowed")
    if len(value) > _REGEX_MAX_TARGET_LEN:
        raise ValueError("unsafe regex pattern: target value too long")

    flags = re.IGNORECASE if vf["ignore_case"] else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:  # pragma: no cover - python versions differ in message details
        raise ValueError(f"invalid regex pattern: {exc}") from exc

    try:
        return await asyncio.wait_for(asyncio.to_thread(lambda: bool(compiled.search(value))), timeout=_REGEX_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise ValueError("unsafe regex pattern: timeout") from exc
