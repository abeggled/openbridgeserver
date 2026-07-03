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

import asyncio
import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import aiosqlite

from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import (
    LEGACY_SCHEMA_VERSION,
    SEGMENT_STATUS_MIGRATED,
    SEGMENT_STATUS_MIGRATING,
    SegmentRecord,
)
from obs.ringbuffer.store.sqlite_backend import (
    _LEGACY_GID_OFFSET,
    _LEGACY_GID_STRIDE,
    SqliteSegmentStore,
    _safe_getsize,
    _safe_json_decode,
)

logger = logging.getLogger(__name__)

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
#
# JS-/JSON-Sicherheit (#951, Runde 23): der Stride läuft strukturell parallel zum
# read-only-Legacy-Stride (``_LEGACY_GID_STRIDE`` in sqlite_backend.py) und teilt
# denselben ``_LEGACY_GID_OFFSET``, damit beide Pfade ohne Divergieren im
# JS-sicheren Band ``±(2**53-1)`` bleiben. ``1 << 32`` (~4,29e9 rowids/Quelle) deckt
# jede reale Legacy-DB ab; zusammen mit ``OFFSET = 1<<52`` bleibt der Worst-Case-
# Betrag bei bis zu ``_MIGRATION_SOURCE_BUCKETS`` (= ``1<<20``, ~1 Mio) Quellen unter
# ``2**53``.
_MIGRATION_SOURCE_STRIDE = _LEGACY_GID_STRIDE  # == 1 << 32; parallel zum read-only-Legacy-Stride
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


def _source_state_token(legacy_path: Path) -> str:
    """Eindeutiger, dateisystem-sicherer Resume-State-Token pro absolutem Quellpfad (#951, Pkt 1, 3. Runde).

    Verschiedene Quellpfade mit GLEICHEM Basename (typisch ``obs_ringbuffer.db``)
    dürfen sich keinen Resume-State teilen, sonst überspringt die zweite Quelle ihre
    Historie still. Der Token kombiniert den Basename (menschenlesbar im Dateinamen)
    mit einem stabilen blake2b-Hex-Digest des ABSOLUTEN Pfads – konsistent zum bereits
    verwendeten ``_source_bucket_for``. Stabil über Prozess-Neustarts (kein ``hash()``-
    Salt), damit ein Resume dieselbe State-Datei findet.
    """
    digest = hashlib.blake2b(str(legacy_path.resolve()).encode("utf-8"), digest_size=8).hexdigest()
    return f"{legacy_path.name}_{digest}"


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

    def __init__(
        self,
        store: SqliteSegmentStore,
        legacy_path: str | Path,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._legacy_path = Path(legacy_path)
        # Serialisierung der Write-/Hide-Sequenz gegen Live-Appends (#951, Runde 23):
        # ein Wartungs-``migrate_chunk`` schreibt direkt in ``store._active_conn`` –
        # OHNE den Write-Lock, den ``RingBuffer.record()`` hält. Ein Live-Append kann
        # sonst NACH dem Positive-Row-Check/Rotate, aber BEVOR die negativen Zeilen als
        # ``migrating``/``migrated`` markiert sind, ins aktive Segment landen → das
        # gemischte Segment wird versteckt/in den Legacy-Tail verschoben und frische
        # Live-Events verschwinden oder sortieren als alt. Wird derselbe Lock übergeben,
        # den ``record()`` nutzt, serialisiert die kritische Sektion mit Live-Appends.
        # ``None`` = No-Op (bestehende Tests/Startup-Pfad ohne Lock laufen unverändert).
        self._write_lock = write_lock
        # Stabiler, deterministischer gid-Bucket dieser Quelldatei (#951, Pkt 3):
        # aus dem absoluten Pfad abgeleitet, sodass verschiedene Quelldateien in
        # disjunkte gid-Bereiche migrieren und ihr Resume-Floor pro Quelle scopt.
        self._source_bucket = _source_bucket_for(self._legacy_path)
        # Resume-State liegt neben der Store-Root, nicht in der Legacy-Datei (die
        # bleibt read-only/unangetastet). Ein State pro ABSOLUTEM Quellpfad (#951,
        # Pkt 1, 3. Runde): zwei Legacy-DBs mit gleichem Basename (der Regelfall ist
        # ``obs_ringbuffer.db``) in denselben Store migriert dürfen sich NICHT
        # denselben ``legacy_migration_<name>.json``-State teilen – sonst läse die
        # zweite Quelle den ``done``-State der ersten und ``migrate_chunk`` lieferte 0
        # zurück, bevor eine Zeile gelesen wird (stiller Skip der zweiten Historie).
        # Der Pfad-Hash ist derselbe stabile blake2b-Digest, der bereits den gid-
        # Bucket bestimmt (``_source_bucket_for``), sodass State-Datei und Bucket
        # konsistent aus demselben absoluten Pfad hervorgehen.
        self._state_path = Path(store._root) / f"legacy_migration_{_source_state_token(self._legacy_path)}.json"

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

    @property
    def _migrated_marker_path(self) -> Path:
        """Persistenter „migriert"-Marker neben der Quelldatei (#951, Pkt 2).

        Ein leeres Sidecar ``<quelle>.migrated`` direkt neben der Legacy-DB. Bewusst
        neben der QUELLE (nicht in der Store-Root), damit der Marker die Quelle
        begleitet und ausschließlich diese eine Datei als vollständig migriert
        markiert – auch wenn mehrere Quellen mit gleichem Basename in denselben Store
        migriert wurden.
        """
        return self._legacy_path.with_name(f"{self._legacy_path.name}.migrated")

    def _mark_source_migrated(self) -> None:
        """Vermerkt die Quelle persistent als vollständig migriert (#951, Pkt 2).

        Idempotent: legt das leere Marker-Sidecar an (bzw. lässt es bestehen).

        Der Marker ist die Re-Attach-Schutzschicht (#951, P2): ``_detach_migrated_
        legacy_segment`` darf den Legacy-Manifest-Eintrag NUR entfernen, WENN dieser
        Marker erfolgreich geschrieben wurde. Ist das Legacy-Verzeichnis nicht
        schreibbar (aber die Store-Root schon), scheitert ``touch()`` – ein
        geschluckter Fehler + trotzdem entfernte Manifest-Zeile führte beim nächsten
        Restart zum Re-Attach der bereits migrierten Quelle (``classify()`` sähe die
        Datei OHNE Marker) und damit zur DOPPELTEN Lieferung jedes migrierten Events.
        Der Fehler wird daher als ``OSError`` PROPAGIERT, damit der Aufrufer NICHT
        detacht; die Legacy-Quelle bleibt registriert (kein Doppel-Delivery) und ein
        späterer Lauf kann es erneut versuchen.
        """
        try:
            self._migrated_marker_path.touch(exist_ok=True)
        except OSError:
            logger.error("RingBuffer: konnte Migrations-Marker fuer %s nicht schreiben – Legacy bleibt eingehaengt", self._legacy_path)
            raise

    def classify(self) -> LegacyClassification | None:
        """Klassifiziert die Quelle – ODER liefert ``None``, wenn bereits migriert (#951, Pkt 2).

        Der Startup-Attach-Pfad (``_open_segment_store_locked``) hängt eine physisch
        vorhandene Legacy-Quelle nur ein, wenn ``classify()`` eine Klassifikation
        liefert. Trägt die Quelle den persistenten „migriert"-Marker (aus
        ``_detach_migrated_legacy_segment``), gilt sie als vollständig nach v2 kopiert
        und darf NICHT erneut read-only eingehängt werden – sonst würde jedes bereits
        migrierte Event doppelt geliefert. Die Original-Datei bleibt physisch erhalten
        (Datenerhalt); nur das Wieder-Einhängen wird unterdrückt.
        """
        if self._migrated_marker_path.exists():
            return None
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
        # Invarianten-Recovery (#951, P2, :596): bevor ein neuer Batch läuft, einen nach
        # Crash zurückgebliebenen inkonsistenten Zustand heilen – sichtbare, rein aus
        # DIESER noch attached Quelle stammende (rein-negative) Segmente re-hidden.
        await self._recover_visible_migrated_while_attached()
        rows = await self._read_batch(after_rowid=after_rowid, limit=batch_rows)
        if not rows:
            await self._finalize_and_detach()
            self._save_state(_ResumeState(last_rowid=after_rowid, done=True))
            await self._run_retention_after_detach()
            return 0
        await self._append_with_legacy_gids(rows)
        last_rowid = rows[-1]["id"]
        # done erst markieren, wenn der Batch kleiner als angefordert war (= letzte Seite).
        done = len(rows) < batch_rows
        if done:
            # Der Zwischen-Cursor (last_rowid) wurde durch den Append bereits idempotent
            # materialisiert; ein Abbruch in ``_finalize_and_detach`` lässt den nächsten
            # Lauf ab last_rowid fortsetzen und erneut finalisieren.
            await self._finalize_and_detach()
            self._save_state(_ResumeState(last_rowid=last_rowid, done=True))
            await self._run_retention_after_detach()
        else:
            self._save_state(_ResumeState(last_rowid=last_rowid, done=False))
        return len(rows)

    async def _finalize_and_detach(self) -> None:
        """Schließt die Migration ab: Promote ZUERST, dann Marker/Detach mit Rollback (#951, P2, :392).

        Leitinvariante: kopierte v2-Chunks sind query-sichtbar GENAU DANN, wenn die
        Legacy-Quelle detached ist – nie beides (Doppel-Delivery), nie beides versteckt +
        Marker publiziert (verdeckter Verlust).

        Reihenfolge (Option B):

        1. ``_finalize_migrated_segments`` – die ``migrating``-Segmente in ihren finalen,
           query-sichtbaren Status promoten. Die IDs der zu promotenden Segmente werden
           VORHER erfasst (``list_migrating_segments``), damit ein Rollback möglich ist.
        2. ``_detach_migrated_legacy_segment`` – schreibt den ``.migrated``-Marker
           (``_mark_source_migrated``) UND entfernt die Legacy-Manifest-Zeile. Der
           Marker-``touch`` ist der einzige fail-prone Schritt (evtl. read-only Legacy-
           Verzeichnis).
        3. Schlägt Schritt 2 fehl, wird die Promotion aus Schritt 1 ZURÜCKGEROLLT: die
           gerade promoteten Segmente werden wieder ``migrating`` (versteckt) markiert und der
           Fehler RE-RAISED. Ergebnis: Chunks wieder versteckt + Legacy noch attached →
           Single-Delivery, kein done-Mark, retry-sicher (Finding 1). Gefangen werden ALLE
           realistischen transienten Detach-Fehler: ``OSError`` (Marker-``touch`` scheitert im
           read-only Legacy-Verzeichnis) UND ``sqlite3.Error`` (== ``aiosqlite.Error``, inkl.
           ``OperationalError``/``DatabaseError``) aus dem finalen Manifest-Delete
           (``delete_segment``) NACH erfolgreichem Marker (#951, P2, :653) – nicht das breite
           ``Exception``.

        Warum Promote ZUERST (und nicht Marker zuerst): ``classify()`` unterdrückt ein
        Re-Attach allein anhand des Marker-Sidecars. Würde der Marker VOR der Promotion
        geschrieben, hinterließe ein Crash zwischen Marker und Promote den Zustand „Marker
        gesetzt + Chunks versteckt + Legacy nicht re-attachbar" → unsichtbare Historie
        (verdeckter Datenverlust). Mit Promote-zuerst kann ein Crash NUR die Zustände
        „promotet, aber Legacy noch attached" (transientes Doppel-Delivery, von
        ``_recover_visible_migrated_while_attached`` beim nächsten Lauf re-hidden) oder
        „promotet + Marker gesetzt + Legacy detached" (sichtbar, korrekt) erzeugen – nie
        verdeckten Verlust.

        Promotion erfolgt in ZWEI Phasen, damit der Rollback verlustfrei möglich bleibt:
        ``_finalize_migrated_segments`` promotet ``migrating`` → ``closed`` (re-hidebar via
        ``mark_migrating``). Erst NACH erfolgreichem Detach hebt ``_promote_closed_to_migrated_
        if_needed`` diese Segmente ggf. in den Trailing-Rang (``migrated``). Würde bereits vor
        dem Detach nach ``migrated`` promotet, ließe der ``mark_migrating``-Guard (nur
        ``closed``/``checkpoint_pending``) den Rollback ins Leere laufen und die migrierten
        Zeilen kämen trotz Marker-Fehler doppelt.
        """
        migrating_before = [seg.segment_id for seg in await self._store.manifest.list_migrating_segments()]
        to_migrated = await self._finalize_migrated_segments()
        try:
            await self._detach_migrated_legacy_segment()
        except (OSError, sqlite3.Error):
            # Detach fehlgeschlagen: die in Schritt 1 promoteten (``closed``) Segmente wieder
            # verstecken, damit sie nicht ZUSAMMEN mit der noch attached Legacy-Quelle doppelt
            # geliefert werden. Danach re-raise, der done-Mark unterbleibt und ein Retry setzt
            # sauber fort. Abgedeckt werden ALLE realistischen transienten Detach-Fehler:
            #   * ``OSError`` – ``_mark_source_migrated`` kann den ``.migrated``-Marker im
            #     read-only Legacy-Verzeichnis nicht schreiben (touch).
            #   * ``sqlite3.Error`` (== ``aiosqlite.Error``, inkl. ``OperationalError``/
            #     ``DatabaseError``) – der finale Manifest-Delete (``delete_segment``) scheitert
            #     NACH erfolgreichem Marker an einem transienten SQLite-I/O-/Locking-Fehler.
            #     Ohne diesen Zweig blieben die Chunks sichtbar promotet, waehrend die Legacy
            #     noch attached ist → dieselbe Historie doppelt bis zum naechsten Retry (#951,
            #     P2, :653). Bewusst NICHT das breite ``Exception`` – nur die real auftretenden
            #     Fehlerklassen des Detach-Schritts.
            for segment_id in migrating_before:
                await self._store.manifest.mark_migrating(segment_id)
            raise
        # Detach erfolgreich (Quelle abgekoppelt): die nun ausschließlich in v2 vorhandenen
        # migrierten Segmente ggf. in den Trailing-Rang (``migrated``) heben – erst jetzt
        # gefahrlos, weil die Legacy-Quelle nicht mehr dieselben Zeilen liefert.
        if to_migrated:
            for segment_id in migrating_before:
                await self._store.manifest.mark_migrated(segment_id)

    async def _recover_visible_migrated_while_attached(self) -> None:
        """Stellt die Sichtbarkeits-Invariante nach einem Crash während der Migration her (#951, P2, :596).

        Crasht der Prozess in ``_append_with_legacy_gids`` NACH dem Row-Commit, aber BEVOR
        die frisch befüllten (rein-negativen) Batch-Segmente als ``migrating`` versteckt
        wurden, bleiben sie durabel UND query-sichtbar (``closed``/``migrated``/``active``),
        während die Original-Legacy-Quelle noch attached ist. ``list_segments_for_query``
        lieferte dann dieselben Alt-Zeilen DOPPELT (einmal v2, einmal aus der attached
        Legacy). Solange die Quelle attached ist, MÜSSEN alle rein aus DIESER Quelle
        migrierten (rein-negativen, im eigenen gid-Bucket liegenden) NICHT-aktiven Segmente
        versteckt (``migrating``) sein.

        Läuft am Anfang jedes ``migrate_chunk`` (also auch beim ersten Lauf nach einem
        Startup/Neustart, sobald der Migrationsjob wieder greift), bevor der neue Batch
        gelesen wird. Idempotent: ohne inkonsistente Segmente ein No-op. Ist die Quelle
        nicht (mehr) attached, ist die Sichtbarkeit korrekt und es passiert nichts.

        Das aktive Segment kann nicht versteckt werden (``mark_migrating``-Guard); enthält
        es rein-negative Zeilen dieser Quelle, wird es zuerst rotiert (nur wenn nicht leer),
        sodass die Zeilen in ein verstecktbares, geschlossenes Segment wandern.
        """
        store = self._store
        if not await self._source_is_attached():
            return
        low, high = self._bucket_gid_bounds
        # Aktives Segment prüfen/rotieren unter dem geteilten Write-Lock (#951, Runde 24):
        # der Check ``_active_segment_has_own_migrated_only_rows`` + ``store.rotate()`` läuft
        # sonst OHNE den Lock, den ``RingBuffer.record()`` hält. Ein Live-Append könnte
        # zwischen Check und Rotate eine POSITIVE Zeile ins aktive Segment schieben; das jetzt
        # gemischte Segment erfüllt ``_segment_is_own_migrated_only`` nicht mehr und bliebe
        # query-sichtbar, während die Legacy-Quelle noch attached ist → Doppel-Delivery +
        # dieselbe aktive-Connection-Rotation-Race, die der Lock in ``_append_with_legacy_gids``
        # verhindert. ``None`` = No-Op. Der Lock wird hier und in ``_append_with_legacy_gids``
        # je EINZELN (nacheinander, nicht verschachtelt) genommen – Recovery läuft am Anfang von
        # ``migrate_chunk``, nicht unter ``record()``, also kein re-entrantes Acquire im selben Task.
        active_id = await self._recover_active_segment_locked(low, high)
        for segment in await store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue
            if segment.status == SEGMENT_STATUS_MIGRATING:
                continue  # bereits versteckt
            if segment.segment_id == active_id:
                continue  # aktives Segment bleibt appendfähig
            if await self._segment_is_own_migrated_only(segment, low, high):
                await store.manifest.mark_migrating(segment.segment_id)

    async def _recover_active_segment_locked(self, low: int, high: int) -> int | None:
        """Serialisiert Check+Rotate des aktiven Segments gegen Live-Appends und delegiert (#951, Runde 24).

        Die Race-kritische Sektion (``_active_segment_has_own_migrated_only_rows`` →
        ``store.rotate()``) läuft unter dem optionalen ``write_lock`` – demselben Lock, den
        ``RingBuffer.record()`` hält. Ohne Lock (``None``) ist der ``async with`` ein No-Op.
        Liefert die (ggf. nach Rotation aktualisierte) aktive segment_id zurück.
        """
        if self._write_lock is None:
            return await self._recover_active_segment_inner(low, high)
        async with self._write_lock:
            return await self._recover_active_segment_inner(low, high)

    async def _recover_active_segment_inner(self, low: int, high: int) -> int | None:
        """Rotiert das aktive Segment, wenn es rein-negative Zeilen DIESER Quelle hält (#951, Runde 24).

        Hält das aktive Segment ausschließlich die migrierten (rein-negativen) Zeilen dieser
        Quelle, wird es rotiert, damit die Zeilen in ein versteckbares, geschlossenes Segment
        wandern. Liefert die danach aktive segment_id.
        """
        store = self._store
        active_id = store._active_segment.segment_id if store._active_segment else None
        if active_id is not None and store._active_conn is not None:
            if await self._active_segment_has_own_migrated_only_rows(low, high):
                await store.rotate()
                active_id = store._active_segment.segment_id if store._active_segment else None
        return active_id

    async def _active_segment_has_own_migrated_only_rows(self, low: int, high: int) -> bool:
        """True, wenn das aktive Segment ausschließlich rein-negative Zeilen DIESER Quelle hält (#951, P2, :596)."""
        conn = self._store._active_conn
        if conn is None:
            return False
        async with conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1") as cur:
            if await cur.fetchone() is not None:
                return False
        async with conn.execute(
            "SELECT 1 FROM ringbuffer WHERE NOT (global_event_id >= ? AND global_event_id < ?) LIMIT 1",
            (low, high),
        ) as cur:
            if await cur.fetchone() is not None:
                return False  # fremde negative Zeilen → nicht rein DIESER Quelle
        async with conn.execute(
            "SELECT 1 FROM ringbuffer WHERE global_event_id >= ? AND global_event_id < ? LIMIT 1",
            (low, high),
        ) as cur:
            return await cur.fetchone() is not None

    async def _segment_is_own_migrated_only(self, segment: SegmentRecord, low: int, high: int) -> bool:
        """True, wenn ein geschlossenes v2-Segment ausschließlich rein-negative Zeilen DIESER Quelle hält (#951, P2, :596)."""
        path = self._store._segments_dir / segment.filename
        if not path.exists():
            return False
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = await aiosqlite.connect(uri, uri=True)
        try:
            async with conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1") as cur:
                if await cur.fetchone() is not None:
                    return False
            async with conn.execute(
                "SELECT 1 FROM ringbuffer WHERE NOT (global_event_id >= ? AND global_event_id < ?) LIMIT 1",
                (low, high),
            ) as cur:
                if await cur.fetchone() is not None:
                    return False  # fremde negative Zeilen → nicht rein DIESER Quelle
            async with conn.execute(
                "SELECT 1 FROM ringbuffer WHERE global_event_id >= ? AND global_event_id < ? LIMIT 1",
                (low, high),
            ) as cur:
                return await cur.fetchone() is not None
        except aiosqlite.Error:
            return False
        finally:
            await conn.close()

    async def _run_retention_after_detach(self) -> None:
        """Zieht die während der Migration deferrte Retention nach Abkopplung der Quelle nach (#951, Pkt 3, 3. Runde).

        Während die Quell-Legacy-DB attached ist, unterdrückt
        ``_append_with_legacy_gids`` die Retention (sonst löschte sie die Quelle). Nach
        der Abkopplung (Migrations-Abschluss) darf/muss Retention einmal regulär greifen,
        damit ein konfiguriertes Byte-/Row-Budget über den nun rein-v2-Segmenten wieder
        eingehalten wird. ``enforce_retention`` ist selbst ge-guarded (No-op ohne
        konfigurierte Retention-Schwellen), daher unbedingter Aufruf.
        """
        await self._store.enforce_retention()

    async def _finalize_migrated_segments(self) -> bool:
        """Macht die kopierten Chunks sichtbar (``closed``) – VOR Detach (#951, :375, :507, P2 :386).

        Reihenfolge (läuft VOR ``_detach_migrated_legacy_segment``):

        1. Das aktive rein-negative Segment versiegeln (``_seal_pure_migrated_active_
           segment``), damit spätere Live-Positives nicht hineingemischt werden.
        2. Die während der laufenden Migration ausgeblendeten ``migrating``-Segmente
           (In-Progress-Kopien) nach ``closed`` promoten (``promote_migrating_segments(
           to_migrated=False)``). Bewusst NUR nach ``closed`` – nicht direkt nach
           ``migrated``: solange der Detach (Marker) noch fehlschlagen kann, müssen die
           Segmente re-hidebar bleiben (``mark_migrating`` akzeptiert nur ``closed``/
           ``checkpoint_pending``). Das endgültige Anheben in den Trailing-Rang (``migrated``)
           erfolgt erst NACH erfolgreichem Detach in ``_finalize_and_detach``.

        Liefert ``to_migrated`` zurück: ob der Store echte Positive oder Zeilen einer anderen
        Quelle hält und die migrierten Segmente daher nach dem Detach in den ``migrated``-
        Trailing-Rang müssen (sonst genügt ``closed``, segment_id-Ordnung == gid-Ordnung).

        ``promote_migrating_segments`` ist idempotent (No-op ohne ``migrating``-Segmente),
        sodass ein Retry nach Teil-Abschluss unschädlich bleibt.
        """
        await self._seal_pure_migrated_active_segment()
        migrating = await self._store.manifest.list_migrating_segments()
        if not migrating:
            return False
        to_migrated = await self._store_has_positive_rows() or await self._store_has_foreign_migrated_rows()
        await self._store.manifest.promote_migrating_segments(to_migrated=False)
        return to_migrated

    async def _seal_pure_migrated_active_segment(self) -> None:
        """Schließt das aktive rein-negative Segment nach Abschluss der ERSTEN migrierten Quelle (#951, :507).

        Wird die erste Legacy-Quelle migriert, BEVOR irgendein v2-Write existiert, ist
        in ``_append_with_legacy_gids`` ``segregate`` false: die negativen Zeilen dieser
        Quelle bleiben im aktiven NORMALEN Segment (keine Vor-Rotation, kein
        ``migrated``-Marker – im Ein-Quell-Fall genügt die segment_id-Ordnung == gid-
        Ordnung). Bliebe dieses Segment nach Migrations-Abschluss aber weiter ``active``,
        mischte ein nachfolgender Live-Append POSITIVE gids HINEIN. Migrierte dann eine
        ZWEITE Quelle, ließe sich dieses gemischte Segment nicht mehr als ``migrated``
        markieren (es enthielte positive Zeilen) und die Multi-Source-Trailing-Ordnung
        bräche.

        Fix: nach Abschluss der ersten rein-migrierten Quelle (Detach) das aktive
        Segment EINMAL rotieren, sodass es geschlossen wird und Live-Positives (bzw. eine
        weitere Quelle) ein frisches, separates aktives Segment mit höherer segment_id
        bekommen – nie wieder mischt ein Segment positive und negative gids. Nur relevant,
        wenn der Store NOCH keine Positive hält (sonst hat ``_append_with_legacy_gids``
        via ``segregate`` bereits rotiert und markiert) und das aktive Segment auch
        wirklich rein-negativ und nicht leer ist (kein Rotieren leerer Segmente).
        """
        store = self._store
        if store._active_conn is None or store._active_segment is None:
            return
        if await self._store_has_positive_rows():
            return  # segregate-Pfad hat bereits rotiert/markiert
        async with store._active_conn.execute("SELECT MIN(global_event_id) FROM ringbuffer") as cur:
            row = await cur.fetchone()
        min_gid = row[0] if row is not None else None
        if min_gid is None or min_gid >= 0:
            return  # leer oder keine negativen Zeilen → nichts zu versiegeln
        await store.rotate()

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

        **Re-Attach-Schutz (#951, Pkt 2):** VOR dem Entfernen der Manifest-Zeile wird
        die Quelle persistent als migriert markiert (``_mark_source_migrated``). Sonst
        sähe der schema-basierte Startup-Attach-Guard beim nächsten Restart nur noch
        eine physisch vorhandene Legacy-Datei OHNE Legacy-Manifest-Zeile und hängte
        genau dieselbe – bereits vollständig nach v2 migrierte – Quelle erneut
        read-only ein → Doppel-Lieferung jedes migrierten Events. Der Marker sorgt
        dafür, dass ``classify()`` die Quelle danach als ``None`` meldet und der
        Attach-Pfad sie überspringt (konsistent zum Idempotenz-/Attach-Guard des
        Startups), ohne die Original-Datei zu löschen.

        Detach NUR bei Marker-Erfolg (#951, P2): ``_mark_source_migrated`` PROPAGIERT
        einen Schreibfehler (z. B. read-only Legacy-Verzeichnis). Die Manifest-Zeile
        wird daher erst NACH erfolgreichem Marker-Schreiben entfernt – schlägt es fehl,
        bleibt die Legacy-Quelle registriert (kein Doppel-Delivery), der Fehler wird
        gemeldet und ``migrate_chunk`` bricht ab (kein done-Mark; späterer Retry).
        """
        self._mark_source_migrated()
        resolved = str(self._legacy_path.resolve())
        for segment in await self._store.manifest.list_legacy_segments():
            if segment.filename == resolved:
                await self._store.manifest.delete_segment(segment.segment_id)

    async def _append_with_legacy_gids(self, rows: list[aiosqlite.Row]) -> None:
        """Serialisiert die Write-/Hide-Sequenz gegen Live-Appends und delegiert (#951, Runde 23).

        Die eigentliche Write-kritische Sektion (Positive-Check/Rotate →
        ``_insert_event`` → ``mark_migrating``/``mark_migrated``) läuft unter dem
        optionalen ``write_lock`` – demselben Lock, den ``RingBuffer.record()`` hält –,
        damit ein Live-Append NICHT zwischen Positive-Check/Rotate und dem Verstecken der
        negativen Zeilen ins aktive Segment einschlägt. Ohne Lock (``None``) ist der
        ``async with`` ein No-Op; das ``rows`` sind bereits gelesen, die kritische
        Sektion umfasst also ausschließlich die Write-/Hide-Sequenz, nicht den
        Legacy-Read.

        Reentrancy: ``record()`` hält ``self._lock`` bereits; die Migration läuft als
        eigener Job NICHT unter ``record()`` – es gibt kein verschachteltes Acquire
        desselben Locks im selben Task.
        """
        if self._write_lock is None:
            await self._append_with_legacy_gids_locked(rows)
            return
        async with self._write_lock:
            await self._append_with_legacy_gids_locked(rows)

    async def _append_with_legacy_gids_locked(self, rows: list[aiosqlite.Row]) -> None:
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
        #
        # Multi-Source (#951, Pkt 2, 3. Runde): sobald eine ZWEITE Quelle in denselben
        # Store migriert, sind bereits negative gids einer FREMDEN Quelle materialisiert.
        # Deren gid-Bereich ist über den quell-gescopten Bucket
        # (``-source_bucket * _MIGRATION_SOURCE_STRIDE``) von der Segment-Erzeugungs-
        # reihenfolge ENTKOPPELT – höhere segment_id ⇒ NICHT mehr zwingend höhere gid.
        # Blieben die rein-migrierten Segmente dann als ``active``/``closed`` im
        # POSITIVEN Query-Rang stehen, iterierte ``list_segments_for_query`` sie nach
        # ``segment_id DESC`` VOR/zwischen den echten Segmenten und der ``id desc``-
        # Frühabbruch könnte migrierte Alt-Zeilen als „neueste" liefern. Werden sie –
        # wie im gemischten Fall – als ``migrated`` markiert, wandern sie in den
        # Legacy-/Migrated-Trailing-Rang (zuletzt iteriert); der Frühabbruch über die
        # echten Segmente bleibt korrekt und die migrierten Zeilen sortieren
        # deterministisch dahinter. Ein Store mit GENAU EINER migrierten Quelle und
        # ohne Positive braucht das nicht (segment_id-Ordnung == gid-Ordnung).
        has_positive = await self._store_has_positive_rows()
        has_foreign_migrated = await self._store_has_foreign_migrated_rows()
        segregate = has_positive or has_foreign_migrated
        if segregate and await self._active_segment_row_count() > 0:
            # Vor der Migration einmal rotieren, damit die negativen Zeilen DIESER
            # Quelle in ein frisches, isoliertes Segment gehen – nie gemischt mit
            # positiven v2-Zeilen oder negativen Zeilen einer anderen Quelle.
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
            # Rollback-on-error wie ``SqliteSegmentStore.append()`` (#951, Runde 24): scheitert
            # ``_insert_event`` oder das folgende ``commit`` (z. B. disk-full/I/O NACH der Haupt-
            # ``ringbuffer``-Zeile, aber vor Metadaten-Indizes/Commit), bliebe die partielle Zeile
            # sonst uncommittet in der offenen Transaktion der aktiven Connection und würde von einer
            # späteren Operation auf derselben Connection (nächster Live-Append/Retry) fremd-committet.
            # Der Import überspränge sie dann via ``_max_migrated_rowid`` mit fehlenden Metadaten-Indizes
            # ODER retryte sie in ein Duplikat (``global_event_id`` ist nicht unique). Daher die aktive
            # Transaktion bei jedem Fehler zurückrollen und die Exception propagieren – so bleibt die
            # Connection in sauberem Zustand (keine halbe, fremd-committbare Zeile).
            try:
                await store._insert_event(conn, gid, _row_to_event(row))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
            if store._active_segment is not None:
                migrated_ids.add(store._active_segment.segment_id)
            rows_in_active += 1
            if await self._rotation_due(rows_in_active, max_rows, max_bytes):
                await store.rotate()
                rows_in_active = 0
        await store._refresh_active_segment_stats()
        # Ist die aktuell migrierte Quelle noch attached, DÜRFEN die in DIESEM Batch
        # kopierten Segmente NICHT vorzeitig ``migrated`` werden (Codex #951 :597).
        # ``migrated`` ist ein SICHTBARER (Trailing-)Rang; solange die Quelle attached
        # ist und dieselben Alt-Zeilen zusätzlich read-only liefert, käme jede kopierte
        # Zeile DOPPELT. Der In-Progress-Block unten versteckt die Segmente stattdessen
        # als ``migrating``; ``mark_migrating`` stuft aber nur ``closed``/``checkpoint_
        # pending`` um – ein vorher gesetztes ``migrated`` bliebe sichtbar. Daher hier
        # bei noch attached Quelle KEIN ``mark_migrated`` auf die Batch-Segmente. Die
        # Promotion zu ``migrated`` erfolgt erst nach dem Detach über
        # ``_finalize_migrated_segments`` → ``promote_migrating_segments``.
        source_attached = await self._source_is_attached()
        if segregate:
            # Das zuletzt befüllte (noch aktive) rein-negative Segment schließen, damit
            # es (nach Detach) als ``migrated`` markierbar wird und künftige POSITIVE
            # Writes (bzw. eine weitere Quelle) ein frisches, separates aktives Segment
            # (höhere segment_id) bekommen – so mischt nie wieder ein Segment positive
            # und negative gids und nie zwei Quellen (#951, Pkt 2).
            if store._active_segment is not None and store._active_segment.segment_id in migrated_ids:
                await store.rotate()
            if not source_attached:
                # Quelle bereits abgekoppelt (z. B. finaler Batch): direkt ``migrated``.
                for segment_id in migrated_ids:
                    await store.manifest.mark_migrated(segment_id)
            # Eine frühere Ein-Quell-Migration (ohne Positive/Fremdquelle) ließ ihr
            # Segment absichtlich unmarkiert als ``closed``/``active`` stehen. Kommt
            # jetzt eine zweite Quelle hinzu, muss dieses fremde rein-migrierte Segment
            # nachträglich als ``migrated`` markiert werden, damit ALLE migrierten
            # Segmente gemeinsam im Trailing-Rang liegen (#951, Pkt 2, 3. Runde). Die im
            # laufenden Batch kopierten Segmente sind dabei ausgenommen (bleiben
            # ``migrating``, solange die Quelle attached ist).
            await self._mark_foreign_migrated_segments(exclude_ids=migrated_ids if source_attached else None)
        # In-Progress-Schutz gegen Doppel-Delivery (#951, :375): solange die Quell-
        # Legacy-DB noch attached (voll abfragbar) ist, liefert sie ALLE Alt-Zeilen –
        # inklusive der in DIESEM/vorigen Batches bereits nach v2 kopierten. Deren v2-
        # Segmente tragen andere synthetische gids als der read-only-Legacy-Pfad
        # (Quell-Bucket vs. segment_id), sodass keine gid-Dedup greift und jede kopierte
        # Zeile DOPPELT käme. Daher werden die im laufenden Batch mit Negativen befüllten
        # Segmente – nach Rotation des aktiven Segments, damit KEINE Negative im
        # (unausblendbaren) aktiven Segment verbleiben – als ``migrating`` markiert und
        # von ``list_segments_for_query`` ausgeblendet, bis die Quelle abgekoppelt ist.
        if source_attached:
            # Aktives Segment mit Negativen schließen (rotate), damit ALLE kopierten
            # Zeilen in ausblendbaren (nicht-aktiven) Segmenten liegen. Das rotierte
            # Segment wird dann ebenfalls als ``migrating`` markiert.
            if store._active_segment is not None and store._active_segment.segment_id in migrated_ids:
                await store.rotate()
            for segment_id in migrated_ids:
                await store.manifest.mark_migrating(segment_id)
        # Quellschutz während der Migration (#951, Pkt 3, 3. Runde): solange die
        # aktuell migrierte Quell-Legacy-DB noch read-only eingehängt ist, DARF hier
        # keine Retention laufen. Ist der Store über dem Byte-Budget, würde
        # ``_next_size_retention_victim`` das (älteste, größte) Legacy-Segment ZUERST
        # wählen – sobald nach dem ersten Batch eine nicht-Legacy-Datenquelle
        # existiert, greift der No-Zero-History-Guard nicht mehr – und
        # ``_delete_segment`` löschte die ORIGINAL-Quelldatei mitten in der Migration.
        # Spätere Chunks fänden dann nichts mehr zu lesen (Datenverlust). Solange die
        # Quelle attached ist, könnte Size-Retention ohnehin NUR sie treffen (sie ist
        # das global älteste Segment), nie die wachsenden v2-Segmente – Deferral
        # verliert also nichts. Retention läuft daher erst nach Abkopplung der Quelle
        # (Abschluss der Migration, siehe ``migrate_chunk`` → ``_run_retention_after_detach``).
        if (max_rows is not None or max_bytes is not None) and not source_attached:
            await store.enforce_retention()

    async def _source_is_attached(self) -> bool:
        """True, wenn die aktuell migrierte Quell-Legacy-DB noch als Legacy-Segment eingehängt ist (#951, Pkt 3, 3. Runde)."""
        resolved = str(self._legacy_path.resolve())
        for segment in await self._store.manifest.list_legacy_segments():
            if segment.filename == resolved:
                return True
        return False

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

    async def _store_has_foreign_migrated_rows(self) -> bool:
        """True, wenn ein v2-Segment migrierte Zeilen einer ANDEREN Quelle hält (#951, Pkt 2, 3. Runde).

        Migrierte Zeilen tragen quell-gescopte negative gids. „Fremd" heißt: eine
        negative gid AUSSERHALB des gid-Bereichs DIESER Quelle (``_bucket_gid_bounds``).
        Sobald eine zweite Quelle in denselben Store migriert, ist die
        segment_id-Ordnung von der gid-Ordnung entkoppelt und die migrierten Segmente
        müssen – wie im gemischten Fall – als ``migrated`` in den Trailing-Rang.
        """
        low, high = self._bucket_gid_bounds
        for segment in await self._iter_v2_segments():
            path, own_active = segment
            conn, close_after = await self._open_v2_segment_read(path, own_active)
            if conn is None:
                continue
            try:
                async with conn.execute(
                    "SELECT 1 FROM ringbuffer WHERE global_event_id < 0 AND NOT (global_event_id >= ? AND global_event_id < ?) LIMIT 1",
                    (low, high),
                ) as cur:
                    if await cur.fetchone() is not None:
                        return True
            except aiosqlite.Error:
                continue
            finally:
                if close_after:
                    await conn.close()
        return False

    async def _mark_foreign_migrated_segments(self, *, exclude_ids: set[int] | None = None) -> None:
        """Markiert bereits geschlossene, rein-migrierte Fremdquell-Segmente als ``migrated``.

        Eine frühere Ein-Quell-Migration ließ ihr Segment als ``closed``/``active``
        stehen (segment_id-Ordnung == gid-Ordnung genügte). Kommt eine zweite Quelle
        hinzu, muss dieses Segment nachträglich in den Trailing-Rang, damit ALLE
        migrierten Segmente gemeinsam hinter den echten v2-Segmenten iteriert werden.
        Nur geschlossene Segmente werden umgestuft (``mark_migrated``-Guard); ein
        aktives Segment bleibt unangetastet.

        ``exclude_ids`` (#951 :597): die im LAUFENDEN Batch kopierten Segmente, solange
        die Quelle noch attached ist. Sie sollen ``migrating`` (versteckt) bleiben und
        NICHT vorzeitig in den sichtbaren ``migrated``-Rang gehoben werden – sonst käme
        jede kopierte Zeile im Migrationsfenster doppelt (v2 + attached Legacy).
        """
        store = self._store
        exclude = exclude_ids or set()
        active_id = store._active_segment.segment_id if store._active_segment else None
        for segment in await store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue
            if segment.status == SEGMENT_STATUS_MIGRATED:
                continue
            if segment.segment_id == active_id:
                continue
            if segment.segment_id in exclude:
                continue
            path = store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True)
            try:
                # Rein-migriert = ausschließlich negative gids, keine einzige positive.
                async with conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1") as cur:
                    if await cur.fetchone() is not None:
                        continue
                async with conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id < 0 LIMIT 1") as cur:
                    if await cur.fetchone() is None:
                        continue
            except aiosqlite.Error:
                continue
            finally:
                await conn.close()
            await store.manifest.mark_migrated(segment.segment_id)

    async def _iter_v2_segments(self) -> list[tuple[Path, bool]]:
        """Existierende v2-Segment-Dateien als ``(Pfad, ist_aktiv)`` (Legacy ausgeschlossen)."""
        store = self._store
        active_id = store._active_segment.segment_id if store._active_segment else None
        result: list[tuple[Path, bool]] = []
        for segment in await store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue
            if segment.segment_id == active_id:
                result.append((store._segments_dir / segment.filename, True))
                continue
            path = store._segments_dir / segment.filename
            if path.exists():
                result.append((path, False))
        return result

    async def _open_v2_segment_read(self, path: Path, own_active: bool) -> tuple[aiosqlite.Connection | None, bool]:
        """Öffnet ein v2-Segment read-only; das aktive Segment über die gehaltene Connection."""
        store = self._store
        if own_active:
            return store._active_conn, False
        uri = f"file:{path.as_posix()}?mode=ro"
        try:
            return await aiosqlite.connect(uri, uri=True), True
        except aiosqlite.Error:
            return None, False

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
        mitmigriert. Ist der dirty WAL dagegen **zu gross zum Checkpointen** (DB+WAL
        >= ``SMALL_MAX_BYTES``), BRICHT die Migration ab (kein Scan/Checkpoint auf
        20–30 GB, aber auch kein stiller ``immutable=1``-Datenverlust) – siehe
        ``_checkpoint_dirty_wal_if_small`` (#951, P1). Nur ein SAUBERER WAL (kein
        dirty ``-wal``) grosser Dateien darf regulär über ``immutable=1`` gelesen
        werden, da dann keine committeten Frames ausserhalb der Haupt-DB liegen.

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
        Migration sie sieht. Ein Checkpoint-FEHLER (read-only-Filesystem o. Ä.) wird
        NICHT geschluckt: der MIGRATIONS-Pfad darf dann NICHT auf ``immutable=1``
        degradieren, weil das die committeten WAL-Frames ignorierte und ein
        Batch-Ende ein falsches ``done`` samt Detach materialisierte (#951, P1) –
        stattdessen bricht die Migration mit ``RuntimeError`` ab (späterer Retry).
        Große Dateien werden NICHT gecheckpointet (kein Startup-Scan auf 20–30 GB).

        WICHTIG (#951, Codex :802, P1): ``wal_checkpoint(TRUNCATE)`` wirft bei einem
        BUSY-Checkpoint NICHT, sondern meldet den Fall in der ERGEBNIS-ZEILE
        ``(busy, log, checkpointed)`` mit ``busy=1``. Anders als der read-only-
        Kompatibilitätspfad (``_checkpoint_small_legacy``) darf der MIGRATIONS-Pfad
        einen busy Checkpoint NICHT stillschweigend als „gelesen" behandeln: die
        committeten WAL-Frames stünden dann noch im ``-wal``, das folgende
        ``immutable=1``-Open ignorierte sie und ``migrate_chunk`` markierte den
        Resume-State fälschlich als ``done`` – Datenverlust bei der Migration. Daher
        gilt nur ``busy == 0`` als Erfolg; bei ``busy != 0`` bricht die Migration mit
        einem ``RuntimeError`` ab (kein done-Mark), ein späterer Lauf versucht es
        erneut, sobald der Reader den WAL freigibt. Die Ergebnis-Zeile wird identisch
        zu ``_checkpoint_small_legacy`` ausgewertet.
        """
        if not _wal_is_dirty(legacy_path):
            return
        if _legacy_disk_size(legacy_path) >= SMALL_MAX_BYTES:
            # Dirty WAL, aber DB+WAL >= SMALL_MAX_BYTES → zu gross zum Checkpointen
            # (kein Scan/Checkpoint auf 20–30 GB). Anders als der read-only-
            # Kompatibilitätspfad (der auf ``immutable=1`` degradieren DARF, weil er nur
            # liest) würde der MIGRATIONS-Pfad hier via ``immutable=1`` die committeten
            # WAL-Frames ignorieren und – erreicht der Batch das Ende der Haupt-DB –
            # ``migrate_chunk`` fälschlich als ``done`` markieren: die jüngsten
            # committeten Frames gingen still verloren (#951, P1). Daher hart abbrechen
            # (kein done-Mark), konsistent zum busy-Abbruch weiter unten; ein späterer
            # Lauf kann es erneut versuchen, sobald der WAL gecheckpointet/kleiner ist.
            raise RuntimeError(
                f"Dirty WAL der Legacy-DB {legacy_path} ist zu gross zum Checkpointen "
                f"(DB+WAL >= {SMALL_MAX_BYTES} Bytes) – Migration abgebrochen, um committete "
                "WAL-Frames nicht via immutable=1 zu verlieren (spaeterer Retry)."
            )
        try:
            conn = await aiosqlite.connect(str(legacy_path))
            try:
                async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
                    row = await cur.fetchone()
                await conn.commit()
            finally:
                await conn.close()
        except aiosqlite.Error as exc:
            # Checkpoint-EXCEPTION (z. B. read-only-FS/Permissions, #951, P1): der frühere
            # ``return`` ließ ``_read_batch`` mit ``immutable=1`` fortfahren, das committete
            # WAL-Frames ignoriert – erreichte der Batch das Ende der Haupt-DB, markierte
            # ``migrate_chunk`` fälschlich ``done``/detachte und die jüngsten committeten
            # Frames gingen dauerhaft verloren. Anders als der read-only-Kompatibilitätspfad
            # (der rein liest und daher auf ``immutable=1`` degradieren DARF) darf der
            # MIGRATIONS-Fortschritt keine committeten Frames verlieren. Daher hart abbrechen
            # (kein done-Mark), konsistent zum busy-/zu-gross-Abbruch; ein späterer Lauf kann
            # es erneut versuchen, sobald das Filesystem wieder schreibbar ist.
            raise RuntimeError(
                f"WAL-Checkpoint der Legacy-DB {legacy_path} ist fehlgeschlagen ({exc}) – Migration "
                "abgebrochen, um committete WAL-Frames nicht via immutable=1 zu verlieren (spaeterer Retry)."
            ) from exc
        # Ergebnis-Zeile (busy, log, checkpointed): busy != 0 → committete WAL-Frames
        # NICHT in die Haupt-DB übernommen. Nicht als gelesen behandeln – hart abbrechen,
        # statt via immutable=1 committete Frames zu verlieren (#951, P1).
        if row is not None and row[0] != 0:
            raise RuntimeError(
                f"WAL-Checkpoint der Legacy-DB {legacy_path} war busy – Migration abgebrochen, "
                "um committete WAL-Frames nicht zu verlieren (späterer Retry)."
            )

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
