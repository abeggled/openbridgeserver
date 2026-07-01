"""Manifest-Schema, Segment-CRUD und globale Event-ID (#931).

Backend-intern (unter der portablen Store-Grenze). Das Manifest ist eine eigene
kleine SQLite-Datei (``manifest.sqlite``) neben dem ``segments/``-Verzeichnis.
Es hält:

* je Segment die Metadaten (``segment_id``, Dateiname, Status, ``from_ts``/
  ``to_ts``, ``row_count``, ``size_bytes``, ``created_at``/``closed_at``,
  Schema-Version, Integrity-/Recovery-Status),
* einen **prozess-/root-weiten, monoton wachsenden globalen Event-ID-Zähler**.

Der globale Event-ID-Zähler ist die harte Vorbedingung aus #922 für #932: die
per-DB-``rowid`` einzelner Segmente reicht nicht als globale Ordnung, weil zwei
Segmente überlappende lokale IDs haben können. Der Zähler wird persistiert,
damit IDs über Neustarts hinweg nie doppelt vergeben werden.

Das Manifest wird **idempotent** initialisiert: ``open()`` legt das Schema nur
an, wenn es fehlt, und verliert bestehende Segmente nicht.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SEGMENT_STATUS_ACTIVE = "active"
SEGMENT_STATUS_CLOSED = "closed"
SEGMENT_STATUS_QUARANTINED = "quarantined"
SEGMENT_STATUS_CHECKPOINT_PENDING = "checkpoint_pending"

_MANIFEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    segment_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    filename         TEXT    NOT NULL UNIQUE,
    status           TEXT    NOT NULL DEFAULT 'active',
    from_ts          TEXT,
    to_ts            TEXT,
    row_count        INTEGER NOT NULL DEFAULT 0,
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    closed_at        TEXT,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    integrity_status TEXT    NOT NULL DEFAULT 'ok',
    recovery_status  TEXT    NOT NULL DEFAULT 'none'
);
CREATE INDEX IF NOT EXISTS idx_manifest_status ON segments(status);
CREATE INDEX IF NOT EXISTS idx_manifest_from_ts ON segments(from_ts);
CREATE INDEX IF NOT EXISTS idx_manifest_to_ts ON segments(to_ts);

-- Prozess-/root-weiter globaler Event-ID-Zähler. Eine Zeile (id=1) hält den
-- zuletzt vergebenen Wert; reservieren = atomares UPDATE ... RETURNING.
CREATE TABLE IF NOT EXISTS event_id_counter (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    last_value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO event_id_counter (id, last_value) VALUES (1, 0);
"""


@dataclass
class SegmentRecord:
    segment_id: int
    filename: str
    status: str
    from_ts: str | None
    to_ts: str | None
    row_count: int
    size_bytes: int
    created_at: str
    closed_at: str | None
    schema_version: int
    integrity_status: str
    recovery_status: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _row_to_segment(row: aiosqlite.Row) -> SegmentRecord:
    return SegmentRecord(
        segment_id=row["segment_id"],
        filename=row["filename"],
        status=row["status"],
        from_ts=row["from_ts"],
        to_ts=row["to_ts"],
        row_count=row["row_count"],
        size_bytes=row["size_bytes"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        schema_version=row["schema_version"],
        integrity_status=row["integrity_status"],
        recovery_status=row["recovery_status"],
    )


class Manifest:
    """CRUD über das Segment-Manifest und den globalen Event-ID-Zähler."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        directory = Path(self._path).parent
        if str(directory):
            directory.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(_MANIFEST_SCHEMA)
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Manifest is not open")
        return self._conn

    # ------------------------------------------------------------------
    # Segment-CRUD
    # ------------------------------------------------------------------

    async def create_segment(self, *, filename: str, schema_version: int) -> SegmentRecord:
        created_at = _utc_now_iso()
        cursor = await self._db.execute(
            """INSERT INTO segments (filename, status, created_at, schema_version)
               VALUES (?, ?, ?, ?)""",
            (filename, SEGMENT_STATUS_ACTIVE, created_at, schema_version),
        )
        await self._db.commit()
        segment_id = cursor.lastrowid
        return await self.get_segment(segment_id)

    async def get_segment(self, segment_id: int) -> SegmentRecord | None:
        async with self._db.execute("SELECT * FROM segments WHERE segment_id = ?", (segment_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_segment(row) if row else None

    async def list_segments(self) -> list[SegmentRecord]:
        async with self._db.execute("SELECT * FROM segments ORDER BY segment_id ASC") as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

    async def get_active_segment(self) -> SegmentRecord | None:
        async with self._db.execute(
            "SELECT * FROM segments WHERE status = ? ORDER BY segment_id DESC LIMIT 1",
            (SEGMENT_STATUS_ACTIVE,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_segment(row) if row else None

    async def update_segment_stats(
        self,
        segment_id: int,
        *,
        row_count: int,
        size_bytes: int,
        from_ts: str | None,
        to_ts: str | None,
    ) -> None:
        await self._db.execute(
            """UPDATE segments
               SET row_count = ?, size_bytes = ?, from_ts = ?, to_ts = ?
               WHERE segment_id = ?""",
            (row_count, size_bytes, from_ts, to_ts, segment_id),
        )
        await self._db.commit()

    async def close_segment(self, segment_id: int) -> None:
        await self._db.execute(
            "UPDATE segments SET status = ?, closed_at = ? WHERE segment_id = ?",
            (SEGMENT_STATUS_CLOSED, _utc_now_iso(), segment_id),
        )
        await self._db.commit()

    async def mark_checkpoint_pending(self, segment_id: int) -> None:
        await self._db.execute(
            "UPDATE segments SET status = ? WHERE segment_id = ?",
            (SEGMENT_STATUS_CHECKPOINT_PENDING, segment_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Globale Event-ID (Vorbedingung für #932)
    # ------------------------------------------------------------------

    async def reserve_global_event_ids(self, count: int) -> int:
        """Reserviert einen zusammenhängenden Block und liefert die erste ID.

        Der Block ist ``[start, start + count)``. Nachfolgende Reservierungen
        beginnen garantiert danach.
        """
        if count < 1:
            raise ValueError("count must be >= 1")
        async with self._db.execute(
            "UPDATE event_id_counter SET last_value = last_value + ? WHERE id = 1 RETURNING last_value",
            (count,),
        ) as cur:
            row = await cur.fetchone()
        await self._db.commit()
        last_value = row[0]
        return last_value - count + 1

    async def next_global_event_id(self) -> int:
        return await self.reserve_global_event_ids(1)
