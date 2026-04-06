"""
RingBuffer Debug Log — Phase 5

Zeichnet jede Werteänderung auf. Speicher umschaltbar zur Laufzeit:
  memory  — SQLite :memory: (verschwindet bei Neustart)
  disk    — SQLite WAL-Mode (überlebt Neustarts)

Filterfunktionen:
  q       — Substring in datapoint_id oder source_adapter
  adapter — exakt source_adapter
  from_ts — ISO-8601 Timestamp (exkl.)
  limit   — max. Einträge (default: 100)

Überschreibt älteste Einträge wenn max_entries erreicht.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ringbuffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    datapoint_id   TEXT    NOT NULL,
    topic          TEXT    NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    source_adapter TEXT    NOT NULL,
    quality        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rb_ts  ON ringbuffer(ts);
CREATE INDEX IF NOT EXISTS idx_rb_dp  ON ringbuffer(datapoint_id);
CREATE INDEX IF NOT EXISTS idx_rb_adp ON ringbuffer(source_adapter);
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


class RingBuffer:
    """
    Async RingBuffer backed by SQLite.

    Lifecycle:
        rb = RingBuffer("memory", max_entries=10000)
        await rb.start()
        bus.subscribe(DataValueEvent, rb.handle_value_event)
        ...
        await rb.stop()
    """

    def __init__(
        self,
        storage: str = "memory",
        max_entries: int = 10000,
        disk_path: str = "/data/obs_ringbuffer.db",
    ) -> None:
        self._storage = storage
        self._max_entries = max_entries
        self._disk_path = disk_path
        self._conn: aiosqlite.Connection | None = None
        self._last_values: dict[str, Any] = {}     # dp_id → last recorded value
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        path = ":memory:" if self._storage == "memory" else self._disk_path
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        if self._storage == "disk":
            await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("RingBuffer started (%s, max=%d)", self._storage, self._max_entries)

    async def stop(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Runtime config switch (memory ↔ disk)
    # ------------------------------------------------------------------

    async def reconfigure(self, storage: str, max_entries: int) -> None:
        """Switch storage mode at runtime. Copies existing entries."""
        async with self._lock:
            if storage == self._storage and max_entries == self._max_entries:
                return

            # Export current entries
            rows = await self._fetchall("SELECT * FROM ringbuffer ORDER BY id")
            existing: list[dict] = [dict(r) for r in rows]

            # Close old connection
            if self._conn:
                await self._conn.close()

            self._storage = storage
            self._max_entries = max_entries

            # Open new connection
            path = ":memory:" if storage == "memory" else self._disk_path
            self._conn = await aiosqlite.connect(path)
            self._conn.row_factory = aiosqlite.Row
            if storage == "disk":
                await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()

            # Re-import (keep only the newest max_entries)
            to_import = existing[-max_entries:]
            for row in to_import:
                await self._conn.execute(
                    """INSERT INTO ringbuffer
                       (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality)
                       VALUES (?,?,?,?,?,?,?)""",
                    (row["ts"], row["datapoint_id"], row["topic"],
                     row["old_value"], row["new_value"],
                     row["source_adapter"], row["quality"]),
                )
            await self._conn.commit()
            logger.info("RingBuffer reconfigured → %s, max=%d (%d entries kept)",
                        storage, max_entries, len(to_import))

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
    ) -> None:
        if not self._conn:
            return
        async with self._lock:
            await self._conn.execute(
                """INSERT INTO ringbuffer
                   (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality)
                   VALUES (?,?,?,?,?,?,?)""",
                (ts, datapoint_id, topic,
                 json.dumps(old_value), json.dumps(new_value),
                 source_adapter, quality),
            )
            await self._conn.commit()
            await self._trim()

    async def _trim(self) -> None:
        """Delete oldest entries to stay within max_entries."""
        async with self._conn.execute("SELECT COUNT(*) FROM ringbuffer") as cur:
            row = await cur.fetchone()
        count = row[0] if row else 0
        if count > self._max_entries:
            excess = count - self._max_entries
            await self._conn.execute(
                "DELETE FROM ringbuffer WHERE id IN "
                "(SELECT id FROM ringbuffer ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
            await self._conn.commit()

    # ------------------------------------------------------------------
    # EventBus handler
    # ------------------------------------------------------------------

    async def handle_value_event(self, event: Any) -> None:
        """Record a DataValueEvent into the ring buffer."""
        dp_id = str(event.datapoint_id)

        # Capture old value from our own tracking (reliable in asyncio)
        old_value = self._last_values.get(dp_id)
        self._last_values[dp_id] = event.value

        try:
            from obs.core.registry import get_registry
            dp = get_registry().get(event.datapoint_id)
            topic = dp.mqtt_topic if dp else f"dp/{dp_id}/value"
        except RuntimeError:
            topic = f"dp/{dp_id}/value"

        await self.record(
            ts=event.ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            datapoint_id=dp_id,
            topic=topic,
            old_value=old_value,
            new_value=event.value,
            source_adapter=event.source_adapter,
            quality=event.quality,
        )

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
        if not self._conn:
            return []

        sql = "SELECT * FROM ringbuffer WHERE 1=1"
        params: list[Any] = []

        if q or dp_ids:
            parts: list[str] = []
            if q:
                parts += ["datapoint_id LIKE ?", "source_adapter LIKE ?"]
                params += [f"%{q}%", f"%{q}%"]
            if dp_ids:
                placeholders = ",".join("?" * len(dp_ids))
                parts.append(f"datapoint_id IN ({placeholders})")
                params += dp_ids
            sql += f" AND ({' OR '.join(parts)})"
        if adapter:
            sql += " AND source_adapter=?"
            params.append(adapter)
        if from_ts:
            sql += " AND ts > ?"
            params.append(from_ts)

        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = await self._fetchall(sql, params)
        return [
            RingBufferEntry(
                id=r["id"],
                ts=r["ts"],
                datapoint_id=r["datapoint_id"],
                topic=r["topic"],
                old_value=_safe_loads(r["old_value"]),
                new_value=_safe_loads(r["new_value"]),
                source_adapter=r["source_adapter"],
                quality=r["quality"],
            )
            for r in rows
        ]

    async def stats(self) -> dict:
        if not self._conn:
            return {"total": 0, "oldest_ts": None, "newest_ts": None,
                    "storage": self._storage, "max_entries": self._max_entries}
        async with self._conn.execute(
            "SELECT COUNT(*) AS c, MIN(ts) AS oldest, MAX(ts) AS newest FROM ringbuffer"
        ) as cur:
            row = await cur.fetchone()
        return {
            "total": row[0] if row else 0,
            "oldest_ts": row[1] if row else None,
            "newest_ts": row[2] if row else None,
            "storage": self._storage,
            "max_entries": self._max_entries,
        }

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


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_rb: RingBuffer | None = None


def get_ringbuffer() -> RingBuffer:
    if _rb is None:
        raise RuntimeError("RingBuffer not initialized")
    return _rb


async def init_ringbuffer(storage: str, max_entries: int, disk_path: str) -> RingBuffer:
    global _rb
    _rb = RingBuffer(storage, max_entries, disk_path)
    await _rb.start()
    return _rb
