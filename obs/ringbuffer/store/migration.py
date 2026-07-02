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
from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION, SegmentRecord
from obs.ringbuffer.store.sqlite_backend import _LEGACY_GID_OFFSET, SqliteSegmentStore, _safe_getsize

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


def _legacy_disk_size(db_path: Path) -> int:
    """Reale Disk-Nutzung einer Legacy-Single-DB inkl. ``-wal``/``-shm`` (#951, Pkt 1).

    Analog zur WAL/SHM-Erfassung aktiver v2-Segmente (``_segment_file_size``): eine
    Legacy-DB, deren Hauptdatei klein ist, deren noch nicht gecheckpointeter ``-wal``
    aber groß ist, belegt real deutlich mehr Platz. Da diese Größe als Manifest-
    ``size_bytes`` in ``/stats``, ``_total_size_bytes()`` und die Size-Budget-Retention
    fließt, müssen die Sidecars mitgezählt werden, sonst wird die Legacy-DB
    unterschätzt. Fehlende Sidecars zählen als 0 (``_safe_getsize``).
    """
    return _safe_getsize(db_path) + _safe_getsize(Path(f"{db_path}-wal")) + _safe_getsize(Path(f"{db_path}-shm"))


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

        Die ins Manifest geschriebene ``size_bytes`` erfasst die REALE Disk-Nutzung
        inkl. ``-wal``/``-shm`` (#951, Pkt 1) – analog zu aktiven v2-Segmenten.
        ``/stats``, ``_total_size_bytes()`` und die Size-Budget-Retention lesen genau
        dieses Feld; zählte man nur die Hauptdatei, würde eine Legacy-DB mit kleiner
        Hauptdatei aber großem, noch nicht gecheckpointetem WAL unterschätzt.
        """
        return await self._store.manifest.register_legacy_segment(
            source_path=str(self._legacy_path.resolve()),
            size_bytes=_legacy_disk_size(self._legacy_path),
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

        Cursor = zuletzt kopierte Legacy-rowid (``id``), persistiert neben der
        Store-Root. Liefert die Anzahl in diesem Aufruf kopierter Zeilen; ``0``
        bedeutet fertig. Die Legacy-Datei wird nur gelesen (read-only) und nie
        verändert/gelöscht.

        **Ordnung (#951, Pkt 2):** Migrierte Alt-Zeilen bekommen – wie der
        read-only-Legacy-Lesepfad – synthetische **negative** ``global_event_id``s
        (``legacy_rowid - _LEGACY_GID_OFFSET``), NICHT frische positive gids aus
        ``append()``. Läuft die Migration also NACH den ersten v2-Writes, sortieren
        die historischen Alt-Zeilen weiterhin HINTER echten neueren v2-Events im
        Default-``id desc``-Query, statt fälschlich davor.

        **Atomarität/Idempotenz (#951, Pkt 3):** Der Resume-Cursor allein ist kein
        atomarer Partner des Append-Commits (separate Datei). Statt auf einen
        atomaren Zwei-Datei-Commit zu bauen, ist der Import **idempotent** gemacht:
        Da jede migrierte Zeile ihre Legacy-rowid deterministisch als negative gid
        trägt, wird der effektive Fortschritt aus der höchsten bereits in v2
        materialisierten Legacy-rowid abgeleitet und mit dem JSON-Cursor gemergt.
        Crasht der Prozess zwischen Append-Commit und State-Write (oder scheitert
        der State-Write), überspringt der nächste Lauf die schon persistierten
        Zeilen anhand dieser materialisierten Grenze – kein Doppel-Import.

        **Rotation/Retention (#951):** Der Append respektiert die Segment-Schwellen
        (``segment_max_rows``/``segment_max_bytes``) und rotiert bei Erreichen, gefolgt
        von ``enforce_retention()`` – Details siehe ``_append_with_legacy_gids``.
        """
        state = self._load_state()
        if state.done:
            return 0
        # Effektiver Cursor = max(JSON-Cursor, höchste bereits in v2 materialisierte
        # Legacy-rowid). Deckt einen veralteten/verlorenen State nach Crash ab.
        materialized = await self._max_migrated_rowid()
        after_rowid = max(state.last_rowid, materialized)
        rows = await self._read_batch(after_rowid=after_rowid, limit=batch_rows)
        if not rows:
            self._save_state(_ResumeState(last_rowid=after_rowid, done=True))
            return 0
        await self._append_with_legacy_gids(rows)
        last_rowid = rows[-1]["id"]
        # done erst markieren, wenn der Batch kleiner als angefordert war (= letzte Seite).
        done = len(rows) < batch_rows
        self._save_state(_ResumeState(last_rowid=last_rowid, done=done))
        return len(rows)

    async def _append_with_legacy_gids(self, rows: list[aiosqlite.Row]) -> None:
        """Fügt Legacy-Zeilen mit negativen gids ein und hält dabei die Rotations-/Retention-Schwellen ein.

        Umgeht bewusst ``store.append()`` (das positive gids reserviert) und schreibt
        stattdessen direkt über ``store._insert_event`` mit ``legacy_rowid -
        _LEGACY_GID_OFFSET`` – derselbe Ordnungsmechanismus wie der read-only-
        Legacy-Lesepfad.

        **Rotations-/Retention-Strategie (#951):** Ein Legacy-Batch kann größer sein
        als ``segment_max_rows``/``segment_max_bytes``; ein einziger Low-Level-Append
        über den ganzen Batch würde ein übergroßes Segment hinterlassen und die
        Segmentierungs-Invariante des normalen Schreibpfads verletzen. Deshalb wird
        der Batch – wie der reguläre Schreibpfad in ``RingBuffer._segment_rotation_due``
        – in schwellengerechten Häppchen appended: Nach jedem committeten Insert wird
        geprüft, ob das aktive Segment ``segment_max_rows`` oder (via aufgefrischter
        Stats) ``segment_max_bytes`` erreicht; ist eine Schwelle gerissen und das
        Segment nicht leer, wird über ``store.rotate()`` ein frisches aktives Segment
        geöffnet (kein Rotieren leerer Segmente → keine Endlos-Rotation). Nach dem
        gesamten Batch läuft ``store.enforce_retention()``, damit auch das
        Byte-/Row-Budget eingehalten wird. Ohne konfigurierte Schwellen bleibt das
        Verhalten ein einzelner Commit über den ganzen Batch.
        """
        store = self._store
        if store._active_conn is None or store._active_segment is None:
            return
        cfg = store._segment_config
        max_rows = cfg.segment_max_rows
        max_bytes = cfg.segment_max_bytes
        # Zeilen im aktiven Segment seit dem letzten Rotate (Basis = bereits materialisierte).
        rows_in_active = await self._active_segment_row_count()
        for row in rows:
            conn = store._active_conn
            gid = int(row["id"]) - _LEGACY_GID_OFFSET
            await store._insert_event(conn, gid, _row_to_event(row))
            await conn.commit()
            rows_in_active += 1
            if await self._rotation_due(rows_in_active, max_rows, max_bytes):
                await store.rotate()
                rows_in_active = 0
        await store._refresh_active_segment_stats()
        if max_rows is not None or max_bytes is not None:
            await store.enforce_retention()

    async def _active_segment_row_count(self) -> int:
        """Aktueller Zeilen-Zähler des aktiven Segments (aus dem Manifest, 0 wenn keins)."""
        active = await self._store.manifest.get_active_segment()
        return active.row_count if active is not None else 0

    async def _rotation_due(self, rows_in_active: int, max_rows: int | None, max_bytes: int | None) -> bool:
        """True, wenn das aktive Segment eine Schwelle reißt (analog ``_segment_rotation_due``).

        Ein leeres Segment (``rows_in_active == 0``) rotiert nie, um Endlos-Rotation
        zu vermeiden. Der Byte-Check frischt die Segment-Stats auf, damit die reale
        Disk-Nutzung (inkl. WAL/SHM) gegen ``segment_max_bytes`` geprüft wird.
        """
        if rows_in_active <= 0:
            return False
        if max_rows is not None and rows_in_active >= max_rows:
            return True
        if max_bytes is not None:
            store = self._store
            await store._refresh_active_segment_stats()
            active = await store.manifest.get_active_segment()
            if active is not None and active.size_bytes >= max_bytes:
                return True
        return False

    async def _max_migrated_rowid(self) -> int:
        """Höchste bereits in v2 materialisierte Legacy-rowid (0, wenn keine).

        Migrierte Zeilen tragen ``global_event_id = legacy_rowid - _LEGACY_GID_OFFSET``
        (streng negativ). Über alle v2-Segmente wird ``MAX(global_event_id)`` unter den
        negativen gids gesucht und zur rowid zurückgerechnet. Das macht ``migrate_chunk``
        idempotent gegen einen verlorenen/veralteten Resume-Cursor (#951, Pkt 3).
        """
        best = 0
        for segment in await self._store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue  # read-only eingehängte Legacy-Segmente haben keine v2-Tabelle
            path = self._store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True)
            try:
                async with conn.execute("SELECT MAX(global_event_id) AS mx FROM ringbuffer WHERE global_event_id < 0") as cur:
                    row = await cur.fetchone()
            except aiosqlite.Error:
                continue
            finally:
                await conn.close()
            if row is not None and row[0] is not None:
                best = max(best, int(row[0]) + _LEGACY_GID_OFFSET)
        return best

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
