"""Legacy-Single-DB-Migration und -Kompatibilität für den Segment-Store (#934).

Bestehende OBS-Installationen haben eine einzelne ``obs_ringbuffer.db`` im
**alten** Format (Tabelle ``ringbuffer`` ohne ``global_event_id`` und ohne
typisierte Wertspalten — ein v1-artiges Schema mit segment-lokaler rowid). Der
segmentierte Store (v2) muss diese Datei weiter lesbar halten und darf sie
**niemals** im kritischen Startup vollständig scannen oder migrieren — eine
20–30-GB-Datei würde den Start sonst blockieren.

Dieses Modul entscheidet je nach Größe (und Dirty-WAL-Zustand), *wie* eine
Legacy-Datei behandelt wird:

* **klein** (``< SMALL_MAX_BYTES``): darf optional in einem Wartungsjob
  vollständig in v2-Segmente kopiert werden (``migrate_small``).
* **mittel** (``< LARGE_MIN_BYTES``): chunked/lazy Migration mit persistiertem
  Resume-State (``migrate_chunk``), nie im Startup, jederzeit fortsetzbar.
* **groß** (``>= LARGE_MIN_BYTES`` oder unbekannter Rowcount): Legacy-Datei
  **read-only** als Legacy-Segment ins Manifest einhängen (``attach_readonly``);
  neue Writes gehen sofort in v2-Segmente. KEIN Startup-Vollscan, KEIN
  ``integrity_check``/Checkpoint auf der großen Datei.

Grundgebot: Bei Fehlern werden **keine** Legacy-Daten gelöscht; die alte DB
bleibt unangetastet erhalten. Die Migration ist optional, lazy und resume-fähig.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import aiosqlite

from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import SegmentRecord
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

# Schwellwerte (Bytes). Klein: klein genug für eine vollständige Einmal-Kopie.
# Groß: ab hier NUR read-only einhängen, nie scannen — eine 20–30-GB-Datei darf
# den Startup nie blockieren. Der Mittelbereich wird chunked/resume-fähig migriert.
SMALL_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
LARGE_MIN_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

# Standard-Batchgröße für die chunked Migration (mittel).
DEFAULT_CHUNK_ROWS = 5_000


class LegacyClass(str, Enum):
    """Migrationsklasse einer Legacy-Single-DB (rein größen-/WAL-basiert)."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


@dataclass(frozen=True)
class LegacyClassification:
    """Klassifikation einer Legacy-Datei OHNE Vollscan (nur Dateisystem-Metadaten)."""

    path: str
    size_bytes: int
    klass: LegacyClass
    dirty_wal: bool


def _wal_is_dirty(db_path: Path) -> bool:
    """True, wenn neben der Legacy-DB ein nicht-leeres ``-wal`` liegt.

    Ein dirty ``-wal`` auf einer großen Legacy-Datei würde beim ersten normalen
    Open eine WAL-Recovery/Checkpoint auslösen — genau der unbounded Startup-Scan,
    den #934/#936 vermeiden. Erkennung bewusst nur über die Dateigröße, ohne die
    DB zu öffnen.
    """
    wal = Path(f"{db_path}-wal")
    try:
        return wal.exists() and wal.stat().st_size > 0
    except OSError:
        return False


def classify_legacy_db(path: str | Path) -> LegacyClassification | None:
    """Klassifiziert eine bestehende Legacy-Single-DB ODER liefert ``None``.

    ``None`` bedeutet: keine Legacy-Datei am Pfad vorhanden. Es wird ausschließlich
    auf Dateisystem-Metadaten geschaut — die DB wird NICHT geöffnet, damit auch eine
    riesige Datei mit dirty WAL ohne Startup-Scan klassifiziert werden kann.
    """
    db_path = Path(path)
    try:
        size = db_path.stat().st_size
    except OSError:
        return None
    dirty_wal = _wal_is_dirty(db_path)
    if size < SMALL_MAX_BYTES:
        klass = LegacyClass.SMALL
    elif size < LARGE_MIN_BYTES:
        klass = LegacyClass.MEDIUM
    else:
        klass = LegacyClass.LARGE
    return LegacyClassification(path=str(db_path), size_bytes=size, klass=klass, dirty_wal=dirty_wal)


@dataclass
class _ResumeState:
    """Persistierter Resume-Zustand einer chunked Migration (Cursor = letzte rowid)."""

    last_rowid: int
    done: bool

    def as_dict(self) -> dict[str, object]:
        return {"last_rowid": self.last_rowid, "done": self.done}


class LegacyMigrator:
    """Behandelt genau eine Legacy-Single-DB gegenüber einem offenen Segment-Store.

    Der Store muss bereits ``open()``-et sein (ein aktives v2-Segment existiert).
    Der Migrator kopiert Legacy-Zeilen über die reguläre ``append``-Grenze in v2-
    Segmente — kein Direktzugriff auf Segmentdateien — und hängt große Dateien
    additiv read-only als Legacy-Segment ein.
    """

    def __init__(self, store: SqliteSegmentStore, legacy_path: str | Path) -> None:
        self._store = store
        self._legacy_path = Path(legacy_path)
        # Resume-State liegt neben der Store-Root, nicht in der Legacy-Datei (die
        # bleibt read-only/unangetastet). Ein State pro Legacy-Datei.
        self._state_path = Path(store._root) / f"legacy_migration_{self._legacy_path.name}.json"

    # ------------------------------------------------------------------
    # Klassifikation
    # ------------------------------------------------------------------

    def classify(self) -> LegacyClassification | None:
        return classify_legacy_db(self._legacy_path)

    # ------------------------------------------------------------------
    # groß: read-only einhängen (kein Scan)
    # ------------------------------------------------------------------

    async def attach_readonly(self, classification: LegacyClassification) -> SegmentRecord:
        """Hängt die Legacy-Datei read-only als Legacy-Segment ein — ohne Vollscan.

        Für große Dateien (und generell als sichere Kompatibilitäts-Route): das
        Manifest bekommt einen additiven Legacy-Eintrag; der Read-Pfad degradiert
        beim Lesen auf den v1-Zweig. Bei dirty WAL wird der Fall geflaggt und NICHT
        im Startup gecheckpointet.
        """
        return await self._store.manifest.register_legacy_segment(
            source_path=str(self._legacy_path.resolve()),
            size_bytes=classification.size_bytes,
            dirty_wal=classification.dirty_wal,
        )

    # ------------------------------------------------------------------
    # klein: vollständige Kopie (Wartungsjob)
    # ------------------------------------------------------------------

    async def migrate_small(self, *, batch_rows: int = DEFAULT_CHUNK_ROWS) -> int:
        """Kopiert eine kleine Legacy-DB vollständig in v2-Segmente. Liefert Zeilenzahl.

        Resume-fähig über denselben Cursor wie ``migrate_chunk``: ein wiederholter
        Aufruf setzt fort statt zu duplizieren. Auf Fehler bleibt die Legacy-Datei
        unangetastet (nur gelesen).
        """
        total = 0
        while True:
            copied = await self.migrate_chunk(batch_rows=batch_rows)
            total += copied
            if self._load_state().done:
                break
        return total

    # ------------------------------------------------------------------
    # mittel: chunked/lazy mit Resume-State
    # ------------------------------------------------------------------

    async def migrate_chunk(self, *, batch_rows: int = DEFAULT_CHUNK_ROWS) -> int:
        """Migriert den nächsten Batch Legacy-Zeilen in v2-Segmente (resume-fähig).

        Cursor = zuletzt kopierte rowid (``id``), persistiert neben der Store-Root.
        Liefert die Anzahl in diesem Aufruf kopierter Zeilen; ``0`` bedeutet fertig.
        Die Legacy-Datei wird nur gelesen (read-only) und nie verändert/gelöscht.
        """
        state = self._load_state()
        if state.done:
            return 0
        rows = await self._read_batch(after_rowid=state.last_rowid, limit=batch_rows)
        if not rows:
            self._save_state(_ResumeState(last_rowid=state.last_rowid, done=True))
            return 0
        events = [_row_to_event(row) for row in rows]
        await self._store.append(events)
        last_rowid = rows[-1]["id"]
        # done erst markieren, wenn der Batch kleiner als angefordert war (= letzte Seite).
        done = len(rows) < batch_rows
        self._save_state(_ResumeState(last_rowid=last_rowid, done=done))
        return len(rows)

    async def _read_batch(self, *, after_rowid: int, limit: int) -> list[aiosqlite.Row]:
        """Liest den nächsten aufsteigenden rowid-Batch read-only aus der Legacy-DB.

        ``immutable=1`` verhindert eine WAL-Recovery beim Open — auch eine dirty-WAL-
        Legacy-Datei wird so ohne Checkpoint gelesen.
        """
        uri = f"file:{self._legacy_path.resolve().as_posix()}?mode=ro&immutable=1"
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        try:
            async with conn.execute(
                """SELECT id, ts, datapoint_id, topic, old_value, new_value,
                          source_adapter, quality, metadata_version, metadata
                   FROM ringbuffer WHERE id > ? ORDER BY id ASC LIMIT ?""",
                (after_rowid, limit),
            ) as cur:
                return await cur.fetchall()
        finally:
            await conn.close()

    # ------------------------------------------------------------------
    # Resume-State (JSON neben der Store-Root)
    # ------------------------------------------------------------------

    def _load_state(self) -> _ResumeState:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _ResumeState(last_rowid=0, done=False)
        return _ResumeState(last_rowid=int(data.get("last_rowid", 0)), done=bool(data.get("done", False)))

    def _save_state(self, state: _ResumeState) -> None:
        self._state_path.write_text(json.dumps(state.as_dict()), encoding="utf-8")


def _row_to_event(row: aiosqlite.Row) -> StoreEvent:
    """Übersetzt eine Legacy-v1-Zeile in ein engine-neutrales ``StoreEvent``.

    Die JSON-Spalten ``old_value``/``new_value`` werden dekodiert; ``append``
    schreibt sie im v2-Segment wieder als JSON **und** in die typisierten Spalten.
    """
    return StoreEvent(
        ts=row["ts"],
        datapoint_id=row["datapoint_id"],
        topic=row["topic"],
        old_value=json.loads(row["old_value"]) if row["old_value"] is not None else None,
        new_value=json.loads(row["new_value"]) if row["new_value"] is not None else None,
        source_adapter=row["source_adapter"],
        quality=row["quality"],
        metadata_version=row["metadata_version"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
