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

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import aiosqlite

from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION, SEGMENT_STATUS_MIGRATED, SegmentRecord
from obs.ringbuffer.store.sqlite_backend import (
    _LEGACY_GID_OFFSET,
    SqliteSegmentStore,
    _safe_getsize,
    _safe_json_decode,
)

# Schwellwerte (Bytes). Klein: klein genug für eine vollständige Einmal-Kopie.
# Groß: ab hier NUR read-only einhängen, nie scannen — eine 20–30-GB-Datei darf
# den Startup nie blockieren. Der Mittelbereich wird chunked/resume-fähig migriert.
SMALL_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
LARGE_MIN_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

# Standard-Batchgröße für die chunked Migration (mittel).
DEFAULT_CHUNK_ROWS = 5_000

# Quell-Scoping der migrierten negativen global_event_ids (#951, Pkt 3).
#
# Der Resume-Floor muss PRO Quelldatei berechnet werden: werden zwei Legacy-DBs
# in denselben Store migriert, dürfen sie sich beim idempotenten Nachziehen aus
# der höchsten materialisierten Legacy-rowid nicht gegenseitig überspringen. Dazu
# bekommt jede Quelldatei einen disjunkten gid-Bucket. Die migrierte gid ist:
#
#     gid = -_LEGACY_GID_OFFSET + rowid - source_bucket * _MIGRATION_SOURCE_STRIDE
#
# * Innerhalb einer Quelle bleibt die Ordnung rowid-monoton (höhere rowid ⇒ höhere,
#   weniger negative gid) – identisch zum read-only-Legacy-Lesepfad.
# * Verschiedene Quellen liegen in disjunkten Wertebereichen (Bucket-Trennung),
#   sodass ``MAX(gid)`` je Bucket den Fortschritt genau EINER Quelle liefert.
# * Alle gids bleiben strikt negativ (unter allen positiven v2-IDs), solange
#   rowid < _MIGRATION_SOURCE_STRIDE und source_bucket < _MIGRATION_SOURCE_BUCKETS.
_MIGRATION_SOURCE_STRIDE = 1 << 40  # bis ~1e12 rowids pro Quelldatei
_MIGRATION_SOURCE_BUCKETS = 1 << 20  # bis ~1e6 unterscheidbare Quelldateien


def _source_bucket_for(legacy_path: Path) -> int:
    """Deterministischer gid-Bucket einer Quelldatei aus ihrem absoluten Pfad (#951, Pkt 3).

    Stabil über Prozess-Neustarts (kein ``hash()``-Salt), damit ein Resume dieselbe
    Quelle demselben Bucket zuordnet. Kollisionen zweier verschiedener Quellpfade auf
    denselben Bucket sind bei ~1e6 Buckets extrem unwahrscheinlich; sie degradieren
    im schlimmsten Fall auf das alte globale Verhalten (kein Datenverlust, nur ein
    theoretisch möglicher Skip), sind aber praktisch ausgeschlossen.
    """
    digest = hashlib.blake2b(str(legacy_path.resolve()).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % _MIGRATION_SOURCE_BUCKETS


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
        # Stabiler, deterministischer gid-Bucket dieser Quelldatei (#951, Pkt 3):
        # aus dem absoluten Pfad abgeleitet, sodass verschiedene Quelldateien in
        # disjunkte gid-Bereiche migrieren und ihr Resume-Floor pro Quelle scopt.
        self._source_bucket = _source_bucket_for(self._legacy_path)

    # ------------------------------------------------------------------
    # Quell-Scoping (#951, Pkt 3)
    # ------------------------------------------------------------------

    def _gid_for_rowid(self, rowid: int) -> int:
        """Negative, quell-gescopte gid einer Legacy-rowid (#951, Pkt 3)."""
        return rowid - _LEGACY_GID_OFFSET - self._source_bucket * _MIGRATION_SOURCE_STRIDE

    def _rowid_for_gid(self, gid: int) -> int:
        """Rechnet eine quell-gescopte gid zurück in die Legacy-rowid."""
        return gid + _LEGACY_GID_OFFSET + self._source_bucket * _MIGRATION_SOURCE_STRIDE

    @property
    def _bucket_gid_bounds(self) -> tuple[int, int]:
        """Halb-offener gid-Bereich ``[low, high)`` dieser Quelle (rowid ≥ 1)."""
        low = self._gid_for_rowid(1)
        high = self._gid_for_rowid(_MIGRATION_SOURCE_STRIDE)
        return low, high

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
            await self._detach_migrated_legacy_segment()
            return 0
        await self._append_with_legacy_gids(rows)
        last_rowid = rows[-1]["id"]
        # done erst markieren, wenn der Batch kleiner als angefordert war (= letzte Seite).
        done = len(rows) < batch_rows
        self._save_state(_ResumeState(last_rowid=last_rowid, done=done))
        if done:
            await self._detach_migrated_legacy_segment()
        return len(rows)

    async def _detach_migrated_legacy_segment(self) -> None:
        """Koppelt den read-only Legacy-Manifest-Eintrag DIESER Datei nach Abschluss ab (#951, Pkt 1).

        Im normalen Upgrade registriert ``_open_segment_store_locked`` die Legacy-
        Single-DB read-only als Legacy-Segment (``attach_readonly``). Migriert ein
        späterer Wartungsjob dieselbe Datei per ``migrate_chunk``/``migrate_small``
        vollständig nach v2, bleibt dieser Legacy-Eintrag OHNE Abkopplung weiterhin
        lesbar – ohne Size-Druck (keine Retention, die ihn droppte) würde damit JEDES
        migrierte Event DOPPELT geliefert: einmal als v2-Zeile, einmal aus dem noch
        eingehängten Legacy-Segment. Nach erfolgreichem Abschluss der Migration wird
        der zur migrierten Datei gehörende Legacy-Eintrag daher aus dem Manifest
        entfernt; die Original-Datei selbst bleibt unangetastet (nur read-only
        gelesen). Idempotent: ist kein passender Eintrag (mehr) vorhanden, passiert
        nichts.
        """
        resolved = str(self._legacy_path.resolve())
        for segment in await self._store.manifest.list_legacy_segments():
            if segment.filename == resolved:
                await self._store.manifest.delete_segment(segment.segment_id)

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
        # id-Ordnung bewahren (#951, Pkt 2): das frühe Paging-Terminieren in
        # ``_collect_rows_across_segments`` verlässt sich darauf, dass ein Segment mit
        # höherer ``segment_id`` ausschließlich höhere ``global_event_id``s hält
        # (Segmentreihenfolge == gid-Ordnung). Migrierte Alt-Zeilen tragen aber
        # NEGATIVE gids. Landeten sie im aktiven Segment, das schon eine echte
        # POSITIVE v2-Zeile enthält, mischte ein Segment positive und negative gids;
        # der ``id desc``-Query bräche früh ab und lieferte die migrierten Alt-Zeilen
        # fälschlich als „neueste". Daher: enthält das aktive Segment bereits
        # positive v2-Zeilen, VOR der Migration einmal rotieren, sodass die negativen
        # Zeilen in ein dediziertes, rein-negatives Segment gehen. Die so befüllten
        # Segmente werden anschließend als ``migrated`` markiert und von
        # ``list_segments_for_query`` – wie Legacy-Segmente – hinter allen positiven
        # Segmenten iteriert; das Early-Termination bleibt korrekt.
        # Nur wenn der Store bereits ECHTE positive v2-Zeilen hält, müssen die
        # migrierten (negativen) Segmente aktiv hinter die positiven sortiert werden
        # (``migrated``-Status). Ist der Store dagegen rein legacy-migriert (keine
        # positiven gids), stimmt die segment_id-Ordnung bereits mit der gid-Ordnung
        # überein (höhere segment_id ⇒ höhere rowid ⇒ höhere gid), und es ist weder
        # ein Vor-Rotate noch ein ``migrated``-Marker nötig – so bleibt der Ein-
        # Segment-Fall ohne Endlos-/Zusatzrotation.
        has_positive = await self._store_has_positive_rows()
        if has_positive and await self._active_segment_has_positive_rows():
            await store.rotate()
        # Segment-ids, die in diesem Batch NEGATIVE Zeilen erhielten (rein-negativ,
        # da vor positiver Mischung rotiert wurde) – nach Abschluss als ``migrated``
        # markieren (nur relevant, wenn positive Daten existieren).
        migrated_ids: set[int] = set()
        # Zeilen im aktiven Segment seit dem letzten Rotate (Basis = bereits materialisierte).
        rows_in_active = await self._active_segment_row_count()
        for row in rows:
            conn = store._active_conn
            gid = self._gid_for_rowid(int(row["id"]))
            await store._insert_event(conn, gid, _row_to_event(row))
            await conn.commit()
            if store._active_segment is not None:
                migrated_ids.add(store._active_segment.segment_id)
            rows_in_active += 1
            if await self._rotation_due(rows_in_active, max_rows, max_bytes):
                await store.rotate()
                rows_in_active = 0
        await store._refresh_active_segment_stats()
        if has_positive:
            # Das zuletzt befüllte (noch aktive) rein-negative Segment schließen, damit
            # es als ``migrated`` markierbar wird und künftige POSITIVE Writes ein
            # frisches, separates aktives Segment (höhere segment_id) bekommen – so
            # mischt nie wieder ein Segment positive und negative gids (#951, Pkt 2).
            if store._active_segment is not None and store._active_segment.segment_id in migrated_ids:
                await store.rotate()
            for segment_id in migrated_ids:
                await store.manifest.mark_migrated(segment_id)
        if max_rows is not None or max_bytes is not None:
            await store.enforce_retention()

    async def _store_has_positive_rows(self) -> bool:
        """True, wenn irgendein v2-Segment des Stores echte positive gids hält (#951, Pkt 2).

        Positive gids stammen ausschließlich aus regulären ``append()``-Writes. Nur
        dann müssen migrierte negative Segmente per ``migrated``-Status hinter die
        positiven sortiert werden; ein rein legacy-migrierter Store braucht das nicht.
        """
        store = self._store
        for segment in await store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION or segment.status == SEGMENT_STATUS_MIGRATED:
                continue
            if segment.segment_id == (store._active_segment.segment_id if store._active_segment else None):
                if await self._active_segment_has_positive_rows():
                    return True
                continue
            path = store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True)
            try:
                async with conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1") as cur:
                    if await cur.fetchone() is not None:
                        return True
            except aiosqlite.Error:
                continue
            finally:
                await conn.close()
        return False

    async def _active_segment_has_positive_rows(self) -> bool:
        """True, wenn das aktive Segment mindestens eine echte v2-Zeile (positive gid) hält (#951, Pkt 2).

        Migrierte Alt-Zeilen tragen negative gids; ein Segment, das positive UND
        negative gids mischt, bricht das frühe Paging-Terminieren des ``id desc``-
        Query. Vor dem Einspielen negativer Zeilen wird daher geprüft, ob im aktiven
        Segment schon positive gids liegen.
        """
        store = self._store
        if store._active_conn is None:
            return False
        async with store._active_conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1") as cur:
            return await cur.fetchone() is not None

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
        """Höchste bereits materialisierte Legacy-rowid DIESER Quelle (0, wenn keine).

        Migrierte Zeilen tragen eine quell-gescopte negative gid
        (``_gid_for_rowid``). Über alle v2-Segmente wird ``MAX(global_event_id)``
        NUR im gid-Bucket DIESER Quelldatei gesucht und zur rowid zurückgerechnet.
        Das macht ``migrate_chunk`` idempotent gegen einen verlorenen/veralteten
        Resume-Cursor (#951, Pkt 3) – **pro Quelle**: werden zwei Legacy-DBs in
        denselben Store migriert, überspringt der Migrator der einen Datei NICHT
        mehr die Zeilen der anderen, weil er einen fremden (höheren) Floor sähe.
        Ohne Bucket-Scoping lieferte ``MAX`` über ALLE negativen gids den
        Fortschritt der zuerst migrierten Datei und ließe die zweite Datei ihre
        ersten rowids still auslassen.
        """
        low, high = self._bucket_gid_bounds
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
                async with conn.execute(
                    "SELECT MAX(global_event_id) AS mx FROM ringbuffer WHERE global_event_id >= ? AND global_event_id < ?",
                    (low, high),
                ) as cur:
                    row = await cur.fetchone()
            except aiosqlite.Error:
                continue
            finally:
                await conn.close()
            if row is not None and row[0] is not None:
                best = max(best, self._rowid_for_gid(int(row[0])))
        return best

    async def _read_batch(self, *, after_rowid: int, limit: int) -> list[aiosqlite.Row]:
        """Liest den nächsten aufsteigenden rowid-Batch read-only aus der Legacy-DB.

        **Dirty-WAL (#951, Pkt 4):** ``immutable=1`` verhindert eine WAL-Recovery
        beim Open, ignoriert damit aber auch committete Frames im ``-wal``. Eine
        kleine Legacy-DB, deren jüngste pre-upgrade-Events noch ungecheckpointet im
        ``-wal`` stehen, migrierte sonst nur den Haupt-DB-Snapshot und markierte den
        Resume-State als „fertig" – die WAL-Frames gingen still verloren. Analog zum
        read-only-Kompatibilitätspfad (``_open_legacy_read_conn`` →
        ``_checkpoint_small_legacy``) wird eine dirty-WAL-Legacy-DB **unter dem
        Small-Schwellwert** daher EINMAL sauber gecheckpointet, bevor read-only
        gelesen wird; committete Frames wandern so in die Haupt-DB und werden
        mitmigriert. Große Dateien bleiben beim ``immutable=1``-Pfad (kein Scan/
        Checkpoint auf 20–30 GB).

        **pre-Metadata-Schema (#951, Pkt 5):** Sehr alte Single-DBs (vor #388) haben
        noch keine ``metadata_version``/``metadata``-Spalten. Ein bedingungsloses
        SELECT dieser Spalten scheiterte mit „no such column" und machte die
        gesamte Alt-Historie unmigrierbar. Die Spalten werden – wie im read-Pfad
        (``_legacy_has_metadata_columns``) – nur selektiert, wenn sie existieren;
        fehlen sie, liefert das SELECT ``NULL`` und ``_row_to_event`` die Defaults.
        """
        legacy_path = self._legacy_path.resolve()
        await self._checkpoint_dirty_wal_if_small(legacy_path)
        uri = f"file:{legacy_path.as_posix()}?mode=ro&immutable=1"
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        try:
            has_meta = await _legacy_has_metadata_columns(conn)
            metadata_select = "metadata_version, metadata" if has_meta else "NULL AS metadata_version, NULL AS metadata"
            async with conn.execute(
                f"""SELECT id, ts, datapoint_id, topic, old_value, new_value,
                           source_adapter, quality, {metadata_select}
                    FROM ringbuffer WHERE id > ? ORDER BY id ASC LIMIT ?""",
                (after_rowid, limit),
            ) as cur:
                return await cur.fetchall()
        finally:
            await conn.close()

    async def _checkpoint_dirty_wal_if_small(self, legacy_path: Path) -> None:
        """Checkpointet eine kleine dirty-WAL-Legacy-DB einmalig (#951, Pkt 4).

        Spiegelt ``SqliteSegmentStore._checkpoint_small_legacy``: nur für Dateien
        unter ``SMALL_MAX_BYTES`` mit nicht-leerem ``-wal`` wird die DB genau einmal
        schreibbar geöffnet und ``wal_checkpoint(TRUNCATE)`` ausgeführt, damit die
        committeten Frames in die Haupt-DB fallen und die anschließende read-only-
        Migration sie sieht. Fehler (read-only-Filesystem o. Ä.) werden geschluckt –
        die Migration degradiert dann auf den ``immutable=1``-Pfad statt zu brechen.
        Große Dateien werden NICHT gecheckpointet (kein Startup-Scan auf 20–30 GB).
        """
        if not _wal_is_dirty(legacy_path):
            return
        if _legacy_disk_size(legacy_path) >= SMALL_MAX_BYTES:
            return
        try:
            conn = await aiosqlite.connect(str(legacy_path))
            try:
                await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await conn.commit()
            finally:
                await conn.close()
        except aiosqlite.Error:
            return

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


async def _legacy_has_metadata_columns(conn: aiosqlite.Connection) -> bool:
    """True, wenn die Legacy-``ringbuffer``-Tabelle die ``metadata``-Spalten trägt (#951, Pkt 5).

    Spiegelt ``SqliteSegmentStore._legacy_has_metadata_columns``: pre-#388-Single-DBs
    haben ``metadata_version``/``metadata`` noch nicht. Erkennung über
    ``PRAGMA table_info``, damit der Migrations-SELECT fehlende Spalten als Defaults
    liefern kann statt mit „no such column" zu brechen.
    """
    async with conn.execute("PRAGMA table_info(ringbuffer)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    return {"metadata_version", "metadata"}.issubset(columns)


def _row_to_event(row: aiosqlite.Row) -> StoreEvent:
    """Übersetzt eine Legacy-v1-Zeile in ein engine-neutrales ``StoreEvent``.

    Die JSON-Spalten ``old_value``/``new_value`` werden **sicher** dekodiert
    (#951, Pkt 6): ein einzelner malformed/non-JSON-Wert wirft hier NICHT mehr eine
    ``JSONDecodeError``, die – vor dem Batch-Commit/Cursor-Vorrücken – die Migration
    dieser Zeile UND aller späteren Alt-Historie dauerhaft blockierte. Stattdessen
    liefert ``_safe_json_decode`` im Fehlerfall den Rohwert – dieselbe Semantik wie
    der read-only-Kompatibilitätspfad (``_legacy_row_to_dict``). ``append`` schreibt
    die Werte im v2-Segment wieder als JSON **und** in die typisierten Spalten.
    """
    return StoreEvent(
        ts=row["ts"],
        datapoint_id=row["datapoint_id"],
        topic=row["topic"],
        old_value=_safe_json_decode(row["old_value"]),
        new_value=_safe_json_decode(row["new_value"]),
        source_adapter=row["source_adapter"],
        quality=row["quality"],
        metadata_version=row["metadata_version"] if row["metadata_version"] is not None else 1,
        metadata=_safe_metadata_decode(row["metadata"]),
    )


def _safe_metadata_decode(raw: object) -> dict:
    """Dekodiert die Legacy-``metadata``-Spalte sicher zu einem dict (#951, Pkt 6).

    Wie ``_legacy_metadata_decode`` im read-Pfad: leerer/fehlender oder
    malformed/non-dict-Wert degradiert auf ``{}`` statt zu werfen, damit eine
    einzelne kaputte Zeile die Migration nicht blockiert.
    """
    if not raw:
        return {}
    decoded = _safe_json_decode(raw)
    return decoded if isinstance(decoded, dict) else {}
