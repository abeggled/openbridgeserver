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
# Read-only eingehängte Legacy-Single-DB (#934): nie aktiv, nie writable, nie
# rowweise löschbar. Bleibt lesbar, bis die segmentgenaue FIFO-Retention sie als
# global ältestes Segment zurückgewinnt (No-Zero-History-Guard beachtet).
SEGMENT_STATUS_LEGACY = "legacy"

# Schema-Version einer Legacy-Single-DB: kein ``global_event_id``, keine
# typisierten Wertspalten, segment-lokale rowid. Der Read-Pfad degradiert für
# diese Version kontrolliert (Ordering aus ts+rowid, kein typed pushdown).
LEGACY_SCHEMA_VERSION = 1

# Für Retention löschbare Status (#919): ein Segment ist freigebbar, wenn sein
# DB/WAL/SHM-Zustand konsistent behandelt wurde (nicht mehr ``active`` oder
# ``checkpoint_pending``). Quarantänierte Segmente sind ebenfalls löschbar: nur
# ihre Segment-DATEI ist korrupt, die Manifest-Metadaten (from_ts/to_ts/
# row_count) bleiben intakt, also lässt sich FIFO-/Age-Retention sicher über die
# Metadaten fahren. Sie werden damit nicht mehr für immer behalten, sondern in
# normaler FIFO-Reihenfolge (ältestes zuerst) mitgelöscht, wenn sie an der Reihe
# sind.
SEGMENT_STATUS_RETENTION_ELIGIBLE = (SEGMENT_STATUS_CLOSED, SEGMENT_STATUS_QUARANTINED)

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
    recovery_status  TEXT    NOT NULL DEFAULT 'none',
    quarantine_reason TEXT
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
    quarantine_reason: str | None = None


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
        quarantine_reason=row["quarantine_reason"] if "quarantine_reason" in row.keys() else None,
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
        await self._migrate_add_quarantine_reason(conn)
        await conn.commit()
        self._conn = conn

    @staticmethod
    async def _migrate_add_quarantine_reason(conn: aiosqlite.Connection) -> None:
        """Idempotente Migration: ``quarantine_reason`` in Alt-Manifests nachziehen."""
        async with conn.execute("PRAGMA table_info(segments)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if "quarantine_reason" not in columns:
            await conn.execute("ALTER TABLE segments ADD COLUMN quarantine_reason TEXT")

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

    async def register_legacy_segment(
        self,
        *,
        source_path: str,
        size_bytes: int,
        dirty_wal: bool = False,
    ) -> SegmentRecord:
        """Hängt eine bestehende Legacy-Single-DB additiv als read-only Segment ein (#934).

        Das Segment bekommt Status ``legacy`` und ``schema_version=LEGACY_SCHEMA_VERSION``.
        Es ist damit nie aktiv, nie writable und nie retention-/checkpoint-fähig; der
        Read-Pfad erkennt es an der Schema-Version und degradiert kontrolliert.

        ``source_path`` ist der **absolute Pfad** der Legacy-Datei; sie wird NICHT
        nach ``segments/`` verschoben, sondern in place read-only gelesen. ``dirty_wal``
        markiert eine große Legacy-Datei mit dirty ``-wal`` (aus dem #936-Kommentar),
        die beim ersten Open NICHT im Startup gecheckpointet werden darf; der Fall wird
        in ``recovery_status`` als ``dirty_wal`` festgehalten.
        """
        created_at = _utc_now_iso()
        recovery_status = "dirty_wal" if dirty_wal else "none"
        cursor = await self._db.execute(
            """INSERT INTO segments (filename, status, created_at, schema_version, size_bytes, recovery_status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_path, SEGMENT_STATUS_LEGACY, created_at, LEGACY_SCHEMA_VERSION, size_bytes, recovery_status),
        )
        await self._db.commit()
        return await self.get_segment(cursor.lastrowid)

    async def list_legacy_segments(self) -> list[SegmentRecord]:
        """Read-only eingehängte Legacy-Single-DBs, älteste zuerst."""
        async with self._db.execute(
            "SELECT * FROM segments WHERE status = ? ORDER BY segment_id ASC",
            (SEGMENT_STATUS_LEGACY,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

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

    async def update_segment_size(self, segment_id: int, *, size_bytes: int) -> None:
        """Aktualisiert nur die ``size_bytes`` eines Segments (#951, Codex :1346).

        Nach einem erfolgreichen ``wal_checkpoint(TRUNCATE)`` beim Rotieren ist die
        WAL/SHM real getruncatet; die reale post-checkpoint-Größe wird hier ohne
        die übrigen Statistik-Felder nachgezogen, damit die direkt folgende
        Retention die Disk-Nutzung nicht überschätzt.
        """
        await self._db.execute(
            "UPDATE segments SET size_bytes = ? WHERE segment_id = ?",
            (size_bytes, segment_id),
        )
        await self._db.commit()

    async def mark_legacy_wal_recovered(self, segment_id: int, *, size_bytes: int) -> None:
        """Frischt Größe + Recovery-Status eines Legacy-Segments nach WAL-Checkpoint auf (#951, Codex :758).

        Nach einem erfolgreichen ``wal_checkpoint(TRUNCATE)`` einer kleinen dirty-WAL-
        Legacy-DB sind die ``-wal``-Bytes real in die Haupt-DB gefaltet/getruncatet.
        Die reale post-checkpoint-Größe wird nachgezogen und ``recovery_status`` von
        ``dirty_wal`` auf ``none`` zurückgesetzt – sonst berichteten Stats/Size-Retention
        Phantom-WAL-Bytes und der Checkpoint liefe bei jedem Read erneut.
        """
        await self._db.execute(
            "UPDATE segments SET size_bytes = ?, recovery_status = 'none' WHERE segment_id = ?",
            (size_bytes, segment_id),
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

    async def close_segment_with_size(self, segment_id: int, *, size_bytes: int) -> None:
        """Schließt ein Segment und schreibt die post-checkpoint ``size_bytes`` atomar (#951, R49).

        Nach erfolgreichem ``wal_checkpoint(TRUNCATE)`` beim Rotieren: Status
        ``closed`` + ``closed_at`` + reale post-checkpoint-Größe in EINEM durablen
        Write. Ein separates ``close_segment`` gefolgt von ``update_segment_size``
        liesse bei einem Crash dazwischen ein retention-eligible ``closed`` Segment
        mit der alten WAL-schweren Größe zurück (die Size-Retention löschte dann zu
        viele ältere Segmente). Analog zu ``close_segment_checkpoint_pending`` für den
        Busy-Pfad.
        """
        await self._db.execute(
            "UPDATE segments SET status = ?, closed_at = ?, size_bytes = ? WHERE segment_id = ?",
            (SEGMENT_STATUS_CLOSED, _utc_now_iso(), size_bytes, segment_id),
        )
        await self._db.commit()

    async def close_segment_checkpoint_pending(self, segment_id: int) -> None:
        """Schließt ein Segment mit busy WAL-Checkpoint direkt als ``checkpoint_pending``.

        EIN durabler Write für Status + ``closed_at`` (#951, Runde 47): würde die
        Rotation erst ``closed`` persistieren und dann auf ``checkpoint_pending``
        umstufen, ließe ein Crash zwischen beiden Writes ein Segment mit
        nicht-getruncatetem WAL als retention-eligible ``closed`` zurück.
        """
        await self._db.execute(
            "UPDATE segments SET status = ?, closed_at = ? WHERE segment_id = ?",
            (SEGMENT_STATUS_CHECKPOINT_PENDING, _utc_now_iso(), segment_id),
        )
        await self._db.commit()

    async def mark_checkpoint_done(self, segment_id: int) -> None:
        """Räumt ein ``checkpoint_pending``-Segment nach erfolgreichem Truncate ab.

        Das Segment wird wieder als sauber ``closed`` markiert und damit erst
        jetzt retention-fähig (DB/WAL/SHM konsistent).
        """
        await self._db.execute(
            "UPDATE segments SET status = ? WHERE segment_id = ? AND status = ?",
            (SEGMENT_STATUS_CLOSED, segment_id, SEGMENT_STATUS_CHECKPOINT_PENDING),
        )
        await self._db.commit()

    async def mark_quarantined(self, segment_id: int, reason: str) -> None:
        """Markiert ein geschlossenes, korruptes Segment als ``quarantined``."""
        await self._db.execute(
            "UPDATE segments SET status = ?, integrity_status = ?, quarantine_reason = ? WHERE segment_id = ?",
            (SEGMENT_STATUS_QUARANTINED, "corrupt", reason, segment_id),
        )
        await self._db.commit()

    async def delete_segment(self, segment_id: int) -> None:
        """Entfernt einen Segment-Eintrag aus dem Manifest (Retention)."""
        await self._db.execute("DELETE FROM segments WHERE segment_id = ?", (segment_id,))
        await self._db.commit()

    async def list_closed_segments(self) -> list[SegmentRecord]:
        """Nur sauber geschlossene Segmente, älteste zuerst."""
        async with self._db.execute(
            "SELECT * FROM segments WHERE status = ? ORDER BY segment_id ASC",
            (SEGMENT_STATUS_CLOSED,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

    async def list_retention_eligible_segments(self) -> list[SegmentRecord]:
        """Für Retention löschbare Segmente, älteste zuerst (FIFO, #919).

        Das sind sauber geschlossene **und** quarantänierte Segmente
        (``SEGMENT_STATUS_RETENTION_ELIGIBLE``). Quarantänierte werden nicht mehr
        für immer behalten, sondern in normaler FIFO-Reihenfolge mitgelöscht,
        wenn sie an der Reihe sind — ihre Manifest-Metadaten bleiben intakt,
        nur die Segment-Datei ist korrupt. ``active`` und ``checkpoint_pending``
        sind bewusst nicht enthalten und werden nie über diese Liste gelöscht.
        """
        placeholders = ", ".join("?" for _ in SEGMENT_STATUS_RETENTION_ELIGIBLE)
        async with self._db.execute(
            f"SELECT * FROM segments WHERE status IN ({placeholders}) ORDER BY segment_id ASC",
            SEGMENT_STATUS_RETENTION_ELIGIBLE,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

    async def list_segments_for_query(
        self,
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> list[SegmentRecord]:
        """Wählt Segmente für eine Read-Query aus, **neueste zuerst** (#932).

        Segmentauswahl geschieht zuerst hier über die Manifest-Metadaten, statt
        blind über alle Segment-Dateien zu mergen:

        * **Mit Zeitfilter** werden nur Segmente zurückgegeben, deren
          ``[from_ts, to_ts]`` das angefragte Fenster überlappt. Ein Segment ohne
          bekannte Grenzen (``from_ts``/``to_ts`` NULL — z. B. frisch angelegt,
          noch ohne Append) wird konservativ **immer** einbezogen, weil sein
          Inhalt nicht ausgeschlossen werden kann.
        * **Ohne Zeitfilter** werden alle Segmente geliefert.

        Quarantänierte Segmente (Status ``quarantined``) werden **nie**
        zurückgegeben (#919): ein als korrupt isoliertes Segment darf im Read-Pfad
        nicht mehr geöffnet werden. ``checkpoint_pending`` bleibt dagegen lesbar.

        Sortierung ist ``segment_id DESC`` (neueste zuerst). Da ``segment_id``
        AUTOINCREMENT ist und globale Event-IDs beim Append streng monoton
        vergeben werden, hält ein später angelegtes Segment ausschließlich höhere
        ``global_event_id``-Werte als jedes ältere — die Segmentreihenfolge nach
        ``segment_id DESC`` entspricht damit exakt der ``global_event_id``-DESC-
        Ordnung über Segmentgrenzen. Das trägt das frühe Paging-Terminieren in
        #932.
        """
        # Quarantänierte (korrupte, isolierte) Segmente sind für Reads tabu (#919).
        clauses: list[str] = [
            f"status != '{SEGMENT_STATUS_QUARANTINED}'",
        ]
        params: list[str] = []
        if to_ts is not None:
            # Segment beginnt nicht nach dem Fensterende (oder Beginn unbekannt).
            clauses.append("(from_ts IS NULL OR from_ts <= ?)")
            params.append(to_ts)
        if from_ts is not None:
            # Segment endet nicht vor dem Fensterbeginn (oder Ende unbekannt).
            clauses.append("(to_ts IS NULL OR to_ts >= ?)")
            params.append(from_ts)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # Legacy-Segmente (#934) tragen synthetische, streng negative
        # global_event_ids und sind per Definition älter als jedes echte
        # v2-Segment – unabhängig von ihrer segment_id (sie werden ggf. NACH dem
        # aktiven v2-Segment eingehängt). Sie müssen daher immer ZULETZT iteriert
        # werden, sonst bricht die neueste-zuerst-Ordnung und das bounded
        # Early-Termination in #932. Primär also nach Legacy-Zugehörigkeit, dann
        # segment_id DESC.
        async with self._db.execute(
            f"SELECT * FROM segments{where} ORDER BY CASE WHEN status = '{SEGMENT_STATUS_LEGACY}' THEN 1 ELSE 0 END, segment_id DESC",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

    async def list_checkpoint_pending_segments(self) -> list[SegmentRecord]:
        """Segmente, deren WAL-Truncate beim Close busy war, älteste zuerst."""
        async with self._db.execute(
            "SELECT * FROM segments WHERE status = ? ORDER BY segment_id ASC",
            (SEGMENT_STATUS_CHECKPOINT_PENDING,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_segment(row) for row in rows]

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
