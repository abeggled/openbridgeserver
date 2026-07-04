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
    _LEGACY_SOURCE_BUCKETS,
    SqliteSegmentStore,
    _safe_getsize,
    _safe_json_decode,
    _sqlite_ro_uri,
)

logger = logging.getLogger(__name__)

# Schwellwerte (Bytes). Klein: klein genug für eine vollständige Einmal-Kopie.
# Groß: ab hier NUR read-only einhängen, nie scannen — eine 20–30-GB-Datei darf
# den Startup nie blockieren. Der Mittelbereich wird chunked/resume-fähig migriert.
SMALL_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
LARGE_MIN_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

# Standard-Batchgröße für die chunked Migration (mittel).
DEFAULT_CHUNK_ROWS = 5_000

# Quell-Scoping der migrierten negativen global_event_ids (#951, Pkt 3; Runde 29, Finding 1).
#
# Jede Quelldatei bekommt einen disjunkten gid-„Bucket" (den *source_factor*), der in
# jede migrierte gid als Ordnungs-Komponente eingeht:
#
#     gid = rowid - _LEGACY_GID_OFFSET - source_factor * _MIGRATION_SOURCE_STRIDE
#
# * Innerhalb einer Quelle bleibt die Ordnung rowid-monoton (höhere rowid ⇒ höhere,
#   weniger negative gid) – identisch zum read-only-Legacy-Lesepfad.
# * Verschiedene Quellen liegen in disjunkten Wertebereichen (Bucket-Trennung),
#   sodass ``MAX(gid)`` je Bucket den Fortschritt genau EINER Quelle liefert.
# * Alle gids bleiben strikt negativ (unter allen positiven v2-IDs), solange
#   rowid < _MIGRATION_SOURCE_STRIDE und source_factor < _MIGRATION_SOURCE_BUCKETS.
#
# CROSS-SOURCE-ORDNUNG (Runde 29, Finding 1): der ``source_factor`` wird – WENN die
# Quelle als Legacy-Segment attached ist – aus dem Manifest-``segment_id`` der Quelle
# abgeleitet und dabei GENAU SO an der Bucket-Schranke gespiegelt wie der attached-
# read-Pfad in ``sqlite_backend._legacy_row_to_dict``:
#
#     source_factor = _MIGRATION_SOURCE_BUCKETS - 1 - (segment_id % _MIGRATION_SOURCE_BUCKETS)
#
# So bleibt die Cross-Source-``id desc``-Ordnung NACH dem Detach beider Quellen
# konsistent mit dem attached-Zustand: eine NEUERE Quelle (höherer segment_id) trägt
# den WENIGER negativen Block und sortiert vor der älteren – nicht mehr abhängig von
# einem Dateinamen-Hash. Ist die Quelle (noch) NICHT attached (reiner Wartungs-
# Migrationspfad ohne vorheriges ``attach_readonly``), gibt es kein attached-Ordnungs-
# äquivalent, das gebrochen werden könnte; dann fällt der Faktor auf den stabilen
# blake2b-Pfad-Hash (``_source_factor_from_path``) zurück – disjunkt und unverändert
# zum bisherigen never-attached-Verhalten.
#
# TRENNUNG Ordnung vs. Resume-Keying (Runde 29, Finding 1, Punkt b): die
# Resume-STATE-DATEI wird weiterhin über ``_source_state_token`` (blake2b des Pfads)
# gekeyt – unabhängig vom Ordnungs-Faktor. Die Resume-KORREKTHEIT (idempotentes
# Nachziehen aus ``MAX(gid)`` je Bucket in ``_max_migrated_rowid`` und die
# Eigen-Segment-Erkennung) hängt allein an der DISJUNKTHEIT der Buckets, nicht an der
# Herkunft des Faktors; sie bleibt damit intakt, egal ob der Faktor aus segment_id
# oder Pfad-Hash stammt.
#
# JS-/JSON-Sicherheit (#951, Runde 23): der Stride läuft strukturell parallel zum
# read-only-Legacy-Stride (``_LEGACY_GID_STRIDE`` in sqlite_backend.py) und teilt
# denselben ``_LEGACY_GID_OFFSET``, damit beide Pfade ohne Divergieren im
# JS-sicheren Band ``±(2**53-1)`` bleiben. ``1 << 32`` (~4,29e9 rowids/Quelle) deckt
# jede reale Legacy-DB ab; zusammen mit ``OFFSET = 1<<52`` bleibt der Worst-Case-
# Betrag bei bis zu ``_MIGRATION_SOURCE_BUCKETS`` (= ``1<<20``, ~1 Mio) Quellen/segment_ids
# unter ``2**53`` (Worst-Case: rowid 1, Faktor ``B-1`` ⇒ ``1 - (1<<52) - (2**20-1)*(1<<32)
# == -9_007_194_959_773_695`` > ``-(2**53-1)``).
_MIGRATION_SOURCE_STRIDE = _LEGACY_GID_STRIDE  # == 1 << 32; parallel zum read-only-Legacy-Stride
# Identisch zu ``sqlite_backend._LEGACY_SOURCE_BUCKETS`` (dort importiert): dieselbe
# Bucket-Schranke ``B``, an der beide Pfade den segment_id spiegeln – MÜSSEN gleich sein.
_MIGRATION_SOURCE_BUCKETS = _LEGACY_SOURCE_BUCKETS  # bis ~1e6 unterscheidbare Quellen/segment_ids


def _mirror_segment_id(segment_id: int) -> int:
    """Spiegelt einen Legacy-``segment_id`` an der Bucket-Schranke zum ``source_factor`` (Runde 29, Finding 1).

    IDENTISCH zur read-Pfad-Formel (``sqlite_backend._legacy_row_to_dict``):
    ``B - 1 - (segment_id % B)``. Höherer segment_id (neuere Quelle) ⇒ kleinerer Faktor
    ⇒ weniger negative gid ⇒ ``id desc`` zuerst. So sind attached-read- und migrierte
    Cross-Source-Ordnung deckungsgleich.
    """
    return _MIGRATION_SOURCE_BUCKETS - 1 - (int(segment_id) % _MIGRATION_SOURCE_BUCKETS)


def _source_factor_from_path(legacy_path: Path) -> int:
    """Deterministischer gid-Faktor einer Quelldatei aus ihrem absoluten Pfad (Fallback, #951, Pkt 3).

    Nur für den never-attached-Wartungspfad (keine Manifest-``segment_id`` verfügbar):
    stabil über Prozess-Neustarts (kein ``hash()``-Salt), damit ein Resume dieselbe
    Quelle demselben Bucket zuordnet. Kollisionen zweier verschiedener Quellpfade auf
    denselben Bucket sind bei ~1e6 Buckets extrem unwahrscheinlich; sie degradieren im
    schlimmsten Fall auf das alte globale Verhalten (kein Datenverlust, nur ein
    theoretisch möglicher Skip), sind aber praktisch ausgeschlossen.
    """
    digest = hashlib.blake2b(str(legacy_path.resolve()).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % _MIGRATION_SOURCE_BUCKETS


# Rückwärtskompatibler Alias (Runde 29, Finding 1): der Pfad-Hash-Faktor hieß früher
# ``_source_bucket_for`` und war die einzige Bucket-Quelle. Bestehende Aufrufer/Tests,
# die die never-attached-Zuordnung prüfen, greifen weiter darauf zu.
_source_bucket_for = _source_factor_from_path


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
    """Persistierter Resume-Zustand einer chunked Migration (Cursor = letzte rowid).

    Datei-Identität (#951, P2, migration.py:610): der ``done``-State wird an dieselbe
    Datei-Identität gebunden wie der ``.migrated``-Marker (``_current_identity_fields``:
    mtime+size der Hauptdatei UND ``-wal``/``-shm``). ``identity is None`` bedeutet ENTWEDER
    ein Alt-State ohne Identitätsfeld (vor diesem Fix geschrieben, rückwärtskompatibel
    behandelt) ODER ein Zwischen-Stand ``done=False`` (die Identität ist erst bei Abschluss
    aussagekräftig). Bindet ``migrate_chunk`` den ``done``-Kurzschluss: weicht die aktuelle
    Datei-Identität von der gespeicherten ab (neue Legacy-Zeilen seit dem ``done``), gilt der
    ``done``-State als STALE und wird als „nicht fertig" behandelt, damit die neuen Zeilen
    ab der materialisierten Grenze eingefaltet werden (siehe ``done_is_stale``).
    """

    last_rowid: int
    done: bool
    identity: dict[str, int] | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"last_rowid": self.last_rowid, "done": self.done}
        if self.identity is not None:
            payload["identity"] = self.identity
        return payload

    def done_is_stale(self, current_identity: dict[str, int] | None) -> bool:
        """True, wenn ein ``done``-State durch eine geänderte Quelldatei ungültig geworden ist.

        Semantik – bewusst konservativ, konsistent zur Marker-Staleness (``_marker_suppresses_
        attach``), sodass „Marker stale ⟺ done stale" gilt:

        * ``done`` nicht gesetzt → nie stale (Zwischen-Stand, ``migrate_chunk`` läuft ohnehin).
        * ``done`` gesetzt, aber KEINE gespeicherte Identität (Alt-State vor diesem Fix) →
          NICHT stale: der ``done``-Kurzschluss bleibt wie bisher wirksam (Rückwärtskompat,
          keine unnötige Re-Migration bestehender Installs).
        * ``done`` + gespeicherte Identität, aktuelle Datei-Identität nicht ermittelbar
          (Hauptdatei fehlt) → NICHT stale: keine neuen Zeilen ohne Datei.
        * ``done`` + gespeicherte Identität, die von der aktuellen ABWEICHT (neue Zeilen /
          Rollback / ``segmented=false`` + Re-Insert, auch reine ``-wal``-Änderung) → STALE.
          Verglichen werden NUR die im State vorhandenen Felder gegen ihr aktuelles Äquivalent,
          analog zum Marker-Vergleich.
        """
        if not self.done or self.identity is None:
            return False
        if current_identity is None:
            return False
        return any(current_identity.get(key) != value for key, value in self.identity.items())


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
        # gid-Ordnungs-Faktor dieser Quelldatei (Runde 29, Finding 1). LAZY aufgelöst:
        # ist die Quelle als Legacy-Segment attached, wird der Faktor aus ihrem
        # Manifest-``segment_id`` gespiegelt (deckungsgleich zum attached-read-Pfad,
        # ``_source_factor``); sonst fällt er auf den blake2b-Pfad-Hash zurück. ``None``
        # bis zur ersten Auflösung; danach stabil gecacht (die Quelle bleibt bis zum
        # Detach attached, der segment_id ändert sich nicht).
        self._source_factor: int | None = None
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

    async def _ensure_source_factor(self) -> int:
        """Löst den gid-Ordnungs-Faktor dieser Quelle auf und cacht ihn (Runde 29, Finding 1).

        Bevorzugt den aus dem Manifest-``segment_id`` gespiegelten Faktor (deckungsgleich
        zum attached-read-Pfad), solange die Quelle als Legacy-Segment attached ist. Ist
        sie NICHT (mehr) attached (reiner Wartungspfad ohne vorheriges ``attach_readonly``),
        fällt der Faktor auf den stabilen blake2b-Pfad-Hash zurück. Einmal aufgelöst,
        bleibt der Wert konstant (die Quelle bleibt bis zum Detach attached; ihr
        segment_id ändert sich nicht) – so tragen ALLE Zeilen eines Migrationslaufs
        denselben Bucket, auch der letzte (post-detach) Batch.
        """
        if self._source_factor is not None:
            return self._source_factor
        segment_id = await self._attached_legacy_segment_id()
        if segment_id is not None:
            self._source_factor = _mirror_segment_id(segment_id)
        else:
            self._source_factor = _source_factor_from_path(self._legacy_path)
        return self._source_factor

    async def _attached_legacy_segment_id(self) -> int | None:
        """``segment_id`` des read-only attached Legacy-Segments DIESER Quelle (oder ``None``)."""
        resolved = str(self._legacy_path.resolve())
        for segment in await self._store.manifest.list_legacy_segments():
            if segment.filename == resolved:
                return segment.segment_id
        return None

    def _gid_for_rowid(self, rowid: int) -> int:
        """Negative, quell-gescopte gid einer Legacy-rowid (#951, Pkt 3; Runde 29, Finding 1).

        Erfordert einen bereits aufgelösten ``_source_factor`` (via
        ``_ensure_source_factor``); alle Aufrufer lösen ihn vor der ersten gid-Rechnung auf.
        """
        assert self._source_factor is not None, "source_factor nicht aufgelöst (fehlt _ensure_source_factor)"
        return rowid - _LEGACY_GID_OFFSET - self._source_factor * _MIGRATION_SOURCE_STRIDE

    def _rowid_for_gid(self, gid: int) -> int:
        """Rechnet eine quell-gescopte gid zurück in die Legacy-rowid."""
        assert self._source_factor is not None, "source_factor nicht aufgelöst (fehlt _ensure_source_factor)"
        return gid + _LEGACY_GID_OFFSET + self._source_factor * _MIGRATION_SOURCE_STRIDE

    async def _bucket_gid_bounds(self) -> tuple[int, int]:
        """Halb-offener gid-Bereich ``[low, high)`` dieser Quelle (rowid ≥ 1).

        Löst den Ordnungs-Faktor bei Bedarf auf (``_ensure_source_factor``), damit die
        Bereichsgrenzen mit den in ``_append_with_legacy_gids`` vergebenen gids
        übereinstimmen.
        """
        await self._ensure_source_factor()
        low = self._gid_for_rowid(1)
        high = self._gid_for_rowid(_MIGRATION_SOURCE_STRIDE)
        return low, high

    # ------------------------------------------------------------------
    # Klassifikation
    # ------------------------------------------------------------------

    @property
    def _migrated_marker_path(self) -> Path:
        """Persistenter „migriert"-Marker neben der Quelldatei (#951, Pkt 2, Runde 27 Finding 3).

        Ein Sidecar ``<quelle>.migrated`` direkt neben der Legacy-DB, das die Datei-
        Identität ``(mtime_ns, size)`` der Quelle bei Migrations-Abschluss festhält (JSON;
        ein leerer Alt-Marker gilt weiterhin als „migriert", siehe
        ``_marker_suppresses_attach``). Bewusst neben der QUELLE (nicht in der Store-Root),
        damit der Marker die Quelle begleitet und ausschließlich diese eine Datei als
        vollständig migriert markiert – auch wenn mehrere Quellen mit gleichem Basename in
        denselben Store migriert wurden.
        """
        return self._legacy_path.with_name(f"{self._legacy_path.name}.migrated")

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        """``(mtime_ns, size)`` einer Datei; fehlt sie, ``(0, 0)`` (Runde 29, Finding 2)."""
        try:
            st = path.stat()
        except OSError:
            return (0, 0)
        return (st.st_mtime_ns, st.st_size)

    def _legacy_identity(self) -> tuple[int, int] | None:
        """Aktuelle Datei-Identität der Legacy-Quelle inkl. WAL/SHM-Sidecars (#951, Runde 27/29, Finding 3/2).

        Dient dem Marker-Invalidierungs-Check: ändert sich die Legacy-Datei nach dem
        Marker (neue Zeilen), weicht diese Identität vom im Marker gespeicherten Wert ab.
        Nur Dateisystem-Metadaten – die DB wird NICHT geöffnet (kein Startup-Scan). Fehlt
        die Hauptdatei, ``None``.

        WAL/SHM-Erfassung (Runde 29, Finding 2): kehrt ein Operator temporär in den
        legacy-file-backed Modus zurück, werden neue Legacy-Zeilen per SQLite-WAL
        committet – die Haupt-``obs_ringbuffer.db`` (mtime/size) kann dabei IDENTISCH
        bleiben, während sich nur ``obs_ringbuffer.db-wal`` (und ``-shm``) ändert. Deckte
        die Marker-Identität nur die Hauptdatei ab, hielte der nächste Startup den Marker
        für aktuell, skippte das Re-Attach und versteckte diese WAL-only-Zeilen still. Die
        Sidecar-``(mtime_ns, size)`` fließen daher mit ein, sodass eine WAL-only-Änderung
        den Marker als STALE erkennt → Re-Attach → neue Zeilen sichtbar.
        """
        main = self._legacy_path
        try:
            st = main.stat()
        except OSError:
            return None
        wal_mtime, wal_size = self._file_identity(Path(f"{main}-wal"))
        shm_mtime, shm_size = self._file_identity(Path(f"{main}-shm"))
        return (st.st_mtime_ns, st.st_size, wal_mtime, wal_size, shm_mtime, shm_size)

    def _mark_source_migrated(self) -> None:
        """Vermerkt die Quelle persistent als vollständig migriert (#951, Pkt 2, Runde 27 Finding 3).

        Idempotent: legt/aktualisiert das Marker-Sidecar neben der Quelldatei an. In den
        Marker wird die Datei-Identität ``(mtime_ns, size)`` der Legacy-DL geschrieben
        (JSON), damit ``classify()`` erkennt, ob die Datei NACH dem Marker verändert wurde
        (Finding 3: Rollback/``segmented=false`` + neue Zeilen dürfen nicht still ignoriert
        werden).

        Der Marker ist die Re-Attach-Schutzschicht (#951, P2): ``_detach_migrated_
        legacy_segment`` darf den Legacy-Manifest-Eintrag NUR entfernen, WENN dieser
        Marker erfolgreich geschrieben wurde. Ist das Legacy-Verzeichnis nicht
        schreibbar (aber die Store-Root schon), scheitert der Write – ein
        geschluckter Fehler + trotzdem entfernte Manifest-Zeile führte beim nächsten
        Restart zum Re-Attach der bereits migrierten Quelle (``classify()`` sähe die
        Datei OHNE Marker) und damit zur DOPPELTEN Lieferung jedes migrierten Events.
        Der Fehler wird daher als ``OSError`` PROPAGIERT, damit der Aufrufer NICHT
        detacht; die Legacy-Quelle bleibt registriert (kein Doppel-Delivery) und ein
        späterer Lauf kann es erneut versuchen.
        """
        identity = self._legacy_identity()
        if identity is None:
            payload: dict[str, int] = {}
        else:
            # Runde 29, Finding 2: WAL/SHM-Sidecar-Identität mitschreiben, damit eine
            # WAL-only-Änderung (Hauptdatei unverändert) den Marker als stale erkennt.
            payload = {
                "mtime_ns": identity[0],
                "size": identity[1],
                "wal_mtime_ns": identity[2],
                "wal_size": identity[3],
                "shm_mtime_ns": identity[4],
                "shm_size": identity[5],
            }
        try:
            self._migrated_marker_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.error("RingBuffer: konnte Migrations-Marker fuer %s nicht schreiben – Legacy bleibt eingehaengt", self._legacy_path)
            raise

    def _marker_suppresses_attach(self) -> bool:
        """True, wenn der Marker das Re-Attach unterdrückt – False, wenn er STALE ist (#951, Runde 27, Finding 3).

        Marker-Semantik (Rückwärtskompat bewusst konservativ):

        * **Kein Marker** → False (nicht unterdrückt; normal klassifizieren).
        * **Leerer / Alt-Marker ohne Identität** (erzeugt vor Runde 27, z. B. ``touch``):
          weiterhin ``suppress`` – ein bestehendes Upgrade darf durch das neue Format
          nicht plötzlich re-attachen. So bleiben bestehende Tests/Installs unverändert.
        * **Marker mit Identität** (``mtime_ns``+``size`` [+ WAL/SHM ab Runde 29]): stimmt
          die aktuelle Datei-Identität überein → ``suppress`` (Datei unverändert seit
          Migration). Weicht sie ab (neue Zeilen / Rollback / ``segmented=false`` +
          Re-Insert – auch WENN nur der ``-wal`` sich änderte) → Marker ist STALE, NICHT
          mehr unterdrücken; die Datei wird wieder normal klassifiziert/attached, damit die
          neuen Legacy-Zeilen NICHT still verloren gehen.

        WAL/SHM-Vergleich (Runde 29, Finding 2): ein Marker im NEUEN Format trägt zusätzlich
        die Sidecar-Identität. Vergleicht werden dann NUR die im Marker vorhandenen Felder
        gegen ihr aktuelles Äquivalent – so bleibt ein Marker im ALTEN Format (nur
        ``mtime_ns``+``size``, Runde 27) mit der bisherigen Haupt-nur-Semantik gültig
        (Rückwärtskompat), während ein neuer Marker auch eine reine ``-wal``-Änderung als
        stale erkennt.

        Tradeoff (bewusst, siehe ``classify``): das Re-Attach der geänderten Datei kann
        bereits migrierte (in v2 kopierte) Alt-Zeilen transient erneut sichtbar machen
        (Doppel-Delivery der Alt-Zeilen), was strikt besser ist als stiller Verlust der
        NEUEN Zeilen. Die Migration ist rowid-idempotent und faltet die neuen Zeilen bei
        einem späteren Lauf sauber ein.
        """
        try:
            raw = self._migrated_marker_path.read_text(encoding="utf-8")
        except OSError:
            return False  # Marker nicht (mehr) lesbar → nicht unterdrücken
        raw = raw.strip()
        if not raw:
            return True  # Alt-Marker (leer, vor Runde 27) → konservativ weiterhin suppress
        try:
            data = json.loads(raw)
            # Pflicht-Kern (Runde 27): Haupt-mtime+size müssen vorhanden sein.
            expected: dict[str, int] = {"mtime_ns": int(data["mtime_ns"]), "size": int(data["size"])}
            # Optionaler WAL/SHM-Anteil (Runde 29): nur vergleichen, wenn im Marker vorhanden
            # (ein Alt-Marker ohne diese Felder behält die Haupt-nur-Semantik).
            for key in ("wal_mtime_ns", "wal_size", "shm_mtime_ns", "shm_size"):
                if key in data:
                    expected[key] = int(data[key])
        except (ValueError, TypeError, KeyError):
            return True  # unlesbarer/legacy Inhalt → konservativ suppress (Rückwärtskompat)
        current = self._current_identity_fields()
        if current is None:
            return False  # Hauptdatei fehlt → nicht unterdrücken
        return all(current.get(key) == value for key, value in expected.items())

    def _current_identity_fields(self) -> dict[str, int] | None:
        """Aktuelle Identitätsfelder als benanntes dict (Runde 29, Finding 2) – ``None`` ohne Hauptdatei."""
        identity = self._legacy_identity()
        if identity is None:
            return None
        return {
            "mtime_ns": identity[0],
            "size": identity[1],
            "wal_mtime_ns": identity[2],
            "wal_size": identity[3],
            "shm_mtime_ns": identity[4],
            "shm_size": identity[5],
        }

    def classify(self) -> LegacyClassification | None:
        """Klassifiziert die Quelle – ODER liefert ``None``, wenn bereits migriert (#951, Pkt 2, Runde 27 Finding 3).

        Der Startup-Attach-Pfad (``_open_segment_store_locked``) hängt eine physisch
        vorhandene Legacy-Quelle nur ein, wenn ``classify()`` eine Klassifikation
        liefert. Trägt die Quelle den persistenten „migriert"-Marker (aus
        ``_detach_migrated_legacy_segment``) UND ist die Datei seit dem Marker
        unverändert (``_marker_suppresses_attach``), gilt sie als vollständig nach v2
        kopiert und darf NICHT erneut read-only eingehängt werden – sonst würde jedes
        bereits migrierte Event doppelt geliefert. Die Original-Datei bleibt physisch
        erhalten (Datenerhalt); nur das Wieder-Einhängen wird unterdrückt.

        Marker-Invalidierung (Finding 3): der path-only-Marker unterdrückte das Attachen
        FÜR IMMER, obwohl ``_detach_migrated_legacy_segment`` die Originaldatei bewusst
        liegen lässt. Rollt ein Operator zurück oder setzt ``segmented=false`` und dieselbe
        ``obs_ringbuffer.db`` bekommt NACH dem Marker neue Zeilen, würden diese still
        ignoriert (Datenverlust). Der Marker ist daher an die Datei-Identität
        (mtime+size) gebunden: weicht sie ab, gilt der Marker als STALE und die Datei wird
        wieder normal klassifiziert (siehe ``_marker_suppresses_attach`` für die Semantik
        und den bewussten Doppel-Delivery-Tradeoff).
        """
        if self._migrated_marker_path.exists() and self._marker_suppresses_attach():
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
        stale = state.done and state.done_is_stale(self._current_identity_fields())
        if state.done and not stale:
            # Fertig UND Quelldatei seit dem ``done`` unverändert → kein Re-Scan.
            return 0
        # Ist der ``done``-State STALE (Quelldatei nach dem ``done`` verändert, #951, P2,
        # migration.py:610), NICHT hier kurzschließen: die neuen Legacy-Zeilen werden ab der
        # materialisierten Grenze (``_max_migrated_rowid``) eingefaltet. Die bereits kopierten
        # Zeilen liegen unter dieser Grenze und werden dadurch idempotent übersprungen; nur die
        # nach dem ``done`` hinzugekommenen rowids landen (erneut) in v2.
        # Effektiver Cursor = max(JSON-Cursor, höchste bereits in v2 materialisierte
        # Legacy-rowid). Deckt einen veralteten/verlorenen State nach Crash ab.
        materialized = await self._max_migrated_rowid()
        after_rowid = max(state.last_rowid, materialized)
        # Stale-Cursor-Reset bei ersetzter/getruncateter Quelle (#951, P2, Codex :658/:680):
        # ``done_is_stale`` erlaubt zwar das Weiterlaufen, aber der oben gefaltete Cursor
        # (JSON-``last_rowid`` UND die materialisierte ``_max_migrated_rowid``-Grenze) gehört
        # noch zur ALTEN Datei-Generation. Ersetzt/truncatet/restauriert ein Operator die
        # Legacy-DB nach dem ``done`` durch eine ANDERE Datei, gehört ihr rowid-Raum zu einer
        # NEUEN Generation, in der die Zeilen ``1..alter Cursor`` andere Daten tragen –
        # überspränge ``_read_batch(after_rowid=alter Cursor)`` sie, schriebe
        # ``_finalize_and_detach`` einen frischen Marker und entfernte die attached Quelle: die
        # neuen Legacy-Zeilen wären PERMANENT versteckt. Das passiert unabhängig davon, ob die
        # neue Quelle WENIGER (Cursor >= neuer MAX → ``_read_batch`` leer) ODER MEHR Zeilen als
        # der alte Cursor hat (neuer MAX > Cursor → ``_read_batch`` liest nur ``Cursor+1..MAX``
        # und lässt ``1..Cursor`` der neuen Generation aus). Die frühere Bedingung
        # ``after_rowid >= _legacy_max_rowid()`` deckte nur den Weniger-/Gleich-Fall ab und ließ
        # den Mehr-Fall durchrutschen.
        # Gegenmaßnahme: Ist die Identität stale, wird der Cursor generations-frisch auf 0
        # zurückgesetzt (Cursor UND materialisierter Floor ignoriert), sodass ``_read_batch``
        # die neue Generation ab Beginn liest – ES SEI DENN, die Änderung ist BEWEISBAR
        # append-only (``_cursor_is_append_only``: die bereits migrierte Boundary-Zeile bei
        # ``after_rowid`` liegt in der aktuellen Quelle unverändert vor). Nur dann bleiben die
        # bereits kopierten Zeilen ``1..after_rowid`` derselben Generation und der Cursor bleibt
        # gültig (reiner ``append``/Grow-Fall, Runde 30: neue rowids OBERHALB des Cursors, kein
        # Re-Scan/Duplikat). Der akzeptierte Tradeoff bei einer ersetzten Quelle mit
        # kollidierenden rowids ist – wie bei der Marker-Staleness (siehe
        # ``_marker_suppresses_attach``) – ein transientes Doppel-Delivery bereits migrierter
        # Alt-Zeilen, strikt besser als stiller Verlust der NEUEN Zeilen.
        if stale and not await self._cursor_is_append_only(after_rowid):
            after_rowid = 0
        # Invarianten-Recovery (#951, P2, :596): bevor ein neuer Batch läuft, einen nach
        # Crash zurückgebliebenen inkonsistenten Zustand heilen – sichtbare, rein aus
        # DIESER noch attached Quelle stammende (rein-negative) Segmente re-hidden.
        await self._recover_visible_migrated_while_attached()
        rows = await self._read_batch(after_rowid=after_rowid, limit=batch_rows)
        if not rows:
            await self._finalize_and_detach()
            self._save_state(_ResumeState(last_rowid=after_rowid, done=True, identity=self._current_identity_fields()))
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
            self._save_state(_ResumeState(last_rowid=last_rowid, done=True, identity=self._current_identity_fields()))
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

        1. ``_finalize_migrated_segments`` – die ``migrating``-Segmente DIESER Quelle in ihren
           finalen, query-sichtbaren Status promoten. Die IDs der zu promotenden Segmente werden
           VORHER erfasst (``_own_migrating_segment_ids``), damit ein Rollback möglich ist.

        Source-Scoping (#951, Runde 26, Finding 2): promotet/detacht/rollbackt werden
        AUSSCHLIESSLICH die migrierten Segmente DIESER Quelle (gid-Bucket). Sind zwei
        Legacy-Quellen attached und eine ANDERE Quelle hat bereits ``migrating``-Chunks
        versteckt, würde eine source-agnostische Promotion deren Historie query-sichtbar
        machen, während deren Original-Legacy noch attached ist → dieselben Zeilen doppelt
        (v2 + attached Legacy). Daher werden fremde ``migrating``-Segmente hier weder
        promotet noch rollbackt.
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
        # NUR die migrierten Segmente DIESER Quelle (gid-Bucket) – VOR der Promotion erfasst,
        # solange sie noch ``migrating`` sind (#951, Runde 26, Finding 2). Fremde
        # ``migrating``-Segmente einer anderen, noch attached Quelle bleiben unberührt.
        own_migrating_before = await self._own_migrating_segment_ids()
        to_migrated = await self._finalize_migrated_segments(own_migrating_before)
        try:
            await self._detach_migrated_legacy_segment()
        except (OSError, sqlite3.Error):
            # Detach fehlgeschlagen: die in Schritt 1 promoteten (``closed``) Segmente DIESER
            # Quelle wieder verstecken, damit sie nicht ZUSAMMEN mit der noch attached Legacy-
            # Quelle doppelt geliefert werden. Danach re-raise, der done-Mark unterbleibt und ein
            # Retry setzt sauber fort. Abgedeckt werden ALLE realistischen transienten Detach-Fehler:
            #   * ``OSError`` – ``_mark_source_migrated`` kann den ``.migrated``-Marker im
            #     read-only Legacy-Verzeichnis nicht schreiben (touch).
            #   * ``sqlite3.Error`` (== ``aiosqlite.Error``, inkl. ``OperationalError``/
            #     ``DatabaseError``) – der finale Manifest-Delete (``delete_segment``) scheitert
            #     NACH erfolgreichem Marker an einem transienten SQLite-I/O-/Locking-Fehler.
            #     Ohne diesen Zweig blieben die Chunks sichtbar promotet, waehrend die Legacy
            #     noch attached ist → dieselbe Historie doppelt bis zum naechsten Retry (#951,
            #     P2, :653). Bewusst NICHT das breite ``Exception`` – nur die real auftretenden
            #     Fehlerklassen des Detach-Schritts. Der Rollback ist ebenfalls source-gescopt:
            #     nur die eigenen Segmente werden re-hidden, fremde ``migrating``-Chunks bleiben
            #     unangetastet (#951, Runde 26, Finding 2).
            for segment_id in own_migrating_before:
                await self._store.manifest.mark_migrating(segment_id)
            raise
        # Detach erfolgreich (Quelle abgekoppelt): die nun ausschließlich in v2 vorhandenen
        # migrierten Segmente DIESER Quelle ggf. in den Trailing-Rang (``migrated``) heben – erst
        # jetzt gefahrlos, weil die Legacy-Quelle nicht mehr dieselben Zeilen liefert.
        if to_migrated:
            for segment_id in own_migrating_before:
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
        low, high = await self._bucket_gid_bounds()
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
        uri = _sqlite_ro_uri(path, params="mode=ro")
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

    async def _own_migrating_segment_ids(self) -> list[int]:
        """``migrating``-Segment-IDs, die AUSSCHLIESSLICH Zeilen DIESER Quelle halten (#951, Runde 26).

        Source-Scoping der Promotion (Finding 2): sind zwei Legacy-Quellen attached und
        eine ANDERE Quelle hat bereits kopierte Chunks als ``migrating`` versteckt, darf der
        Abschluss DIESER Quelle nur ihre EIGENEN migrierten Segmente promoten/detachen –
        nie die der Fremdquelle, deren Original-Legacy noch attached ist (sonst würde deren
        Historie query-sichtbar UND weiterhin aus dem attached Legacy geliefert → Doppel-
        Delivery). „Eigen" heißt: alle Zeilen des Segments liegen im gid-Bucket dieser Quelle
        (``_bucket_gid_bounds`` via ``_segment_is_own_migrated_only``). Reihenfolge älteste
        zuerst (``list_migrating_segments`` ist ``segment_id ASC``).
        """
        low, high = await self._bucket_gid_bounds()
        own: list[int] = []
        for segment in await self._store.manifest.list_migrating_segments():
            if await self._segment_is_own_migrated_only(segment, low, high):
                own.append(segment.segment_id)
        return own

    async def _run_retention_after_detach(self) -> None:
        """Zieht die während der Migration deferrte Retention nach Abkopplung der Quelle nach (#951, Pkt 3, 3. Runde).

        Während die Quell-Legacy-DB attached ist, unterdrückt
        ``_append_with_legacy_gids`` die Retention (sonst löschte sie die Quelle). Nach
        der Abkopplung (Migrations-Abschluss) darf/muss Retention einmal regulär greifen,
        damit ein konfiguriertes Byte-/Row-Budget über den nun rein-v2-Segmenten wieder
        eingehalten wird. ``enforce_retention`` ist selbst ge-guarded (No-op ohne
        konfigurierte Retention-Schwellen), daher unbedingter Aufruf.

        **Multi-Source-Deferral (#951, Runde 27, Finding 1):** Sind Migrationen mehrerer
        Legacy-Quellen verschränkt, ruft der Abschluss EINER Quelle diesen Pass auf, obwohl
        eine ANDERE Quelle noch versteckte ``migrating``-Chunks UND ihr Original-Legacy-
        Segment attached hat. Die frisch finalisierten (nun sichtbaren) rein-v2-Zeilen
        DIESER Quelle erfüllen den No-Zero-History-Guard – unter Byte-/Row-/Age-Druck
        könnte die Retention dann die noch attached Legacy-Datei der ANDEREN Quelle als
        ältestes Segment wählen und löschen, BEVOR deren restliche Zeilen kopiert sind
        (Datenverlust). Solange also global IRGENDEINE ``migrating``-Quelle in Arbeit ist
        (``list_migrating_segments`` non-empty), wird der Pass übersprungen – konsistent zu
        den ``_migration_in_progress()``-Guards im Append-/Startup-/reconfigure-Pfad, hier
        aber im Migrations-eigenen post-detach-Pfad. Der letzte Quell-Abschluss (dann keine
        ``migrating``-Segmente mehr) zieht die deferrte Retention nach.
        """
        if await self._store.manifest.list_migrating_segments():
            return
        await self._store.enforce_retention()

    async def _finalize_migrated_segments(self, own_migrating_ids: list[int] | None = None) -> bool:
        """Macht die kopierten Chunks DIESER Quelle sichtbar (``closed``) – VOR Detach (#951, :375, :507, P2 :386).

        Reihenfolge (läuft VOR ``_detach_migrated_legacy_segment``):

        1. Das aktive rein-negative Segment versiegeln (``_seal_pure_migrated_active_
           segment``), damit spätere Live-Positives nicht hineingemischt werden.
        2. Die während der laufenden Migration ausgeblendeten ``migrating``-Segmente DIESER
           Quelle (In-Progress-Kopien, ``own_migrating_ids``) nach ``closed`` promoten. Bewusst
           NUR nach ``closed`` – nicht direkt nach ``migrated``: solange der Detach (Marker) noch
           fehlschlagen kann, müssen die Segmente re-hidebar bleiben (``mark_migrating`` akzeptiert
           nur ``closed``/``checkpoint_pending``). Das endgültige Anheben in den Trailing-Rang
           (``migrated``) erfolgt erst NACH erfolgreichem Detach in ``_finalize_and_detach``.

        Source-Scoping (#951, Runde 26, Finding 2): promotet werden AUSSCHLIESSLICH die
        Segment-IDs DIESER Quelle (``own_migrating_ids``, im eigenen gid-Bucket), NICHT die
        source-agnostische ``promote_migrating_segments``. Sind zwei Legacy-Quellen attached
        und eine andere Quelle hält bereits ``migrating``-Chunks, blieben deren Chunks so
        weiter versteckt (kein Doppel-Delivery), solange deren Original-Legacy attached ist.

        Liefert ``to_migrated`` zurück: ob der Store echte Positive oder Zeilen einer anderen
        Quelle hält und die migrierten Segmente daher nach dem Detach in den ``migrated``-
        Trailing-Rang müssen (sonst genügt ``closed``, segment_id-Ordnung == gid-Ordnung).

        Idempotent (No-op ohne eigene ``migrating``-Segmente), sodass ein Retry nach
        Teil-Abschluss unschädlich bleibt.
        """
        await self._seal_pure_migrated_active_segment()
        # ``own_migrating_ids`` wird von ``_finalize_and_detach`` VOR dem Seal erfasst (solange
        # die Segmente noch ``migrating`` sind); wird die Methode ohne Argument aufgerufen,
        # selbst bestimmen (Source-Scoping bleibt erhalten).
        if own_migrating_ids is None:
            own_migrating_ids = await self._own_migrating_segment_ids()
        if not own_migrating_ids:
            return False
        to_migrated = await self._store_has_positive_rows() or await self._store_has_foreign_migrated_rows()
        for segment_id in own_migrating_ids:
            await self._store.manifest.close_segment(segment_id)
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

        Serialisierung gegen Live-Appends (Runde 29, Finding 3): dieser finale Seal läuft
        aus ``migrate_chunk`` NACH der Freigabe des Write-Locks in
        ``_append_with_legacy_gids``. Ohne eigene Serialisierung könnte ein Live-``record()``
        zwischen dem Rein-Negativ-Check (``_store_has_positive_rows`` / ``MIN(gid)``) und dem
        ``store.rotate()`` eine POSITIVE Zeile ins aktive Segment schieben → gemischtes
        NORMALES Segment, dessen negative Legacy-Zeilen im positiven Query-Präfix sitzen und
        eine latest-page-``id desc``-Query VOR älteren positiven Segmenten erreichen. Der
        gesamte Check+Rotate läuft daher unter demselben ``write_lock``, den ``record()``
        hält. ``None`` = No-Op (bestehender Pfad ohne Lock). Keine Reentrancy: der Seal läuft
        über ``migrate_chunk`` → ``_finalize_and_detach``, NICHT unter ``record()``.
        """
        if self._write_lock is None:
            await self._seal_pure_migrated_active_segment_inner()
            return
        async with self._write_lock:
            await self._seal_pure_migrated_active_segment_inner()

    async def _seal_pure_migrated_active_segment_inner(self) -> None:
        """Check+Rotate des rein-negativen aktiven Segments (unter Lock, Runde 29, Finding 3)."""
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
        # Ordnungs-Faktor auflösen, BEVOR die erste gid vergeben wird (Runde 29, Finding 1):
        # solange die Quelle attached ist, bindet das den migrierten Bucket an den
        # segment_id der Quelle – deckungsgleich zum attached-read-Pfad.
        await self._ensure_source_factor()
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
            uri = _sqlite_ro_uri(path, params="mode=ro")
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
        low, high = await self._bucket_gid_bounds()
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

        Fremde ``migrating``-Chunks versteckt halten (#951, Runde 27, Finding 2): wird
        eine zweite Legacy-Quelle migriert, während eine ANDERE Quelle noch
        ``migrating``-Chunks (unterbrochene chunked Migration) mit weiterhin attached
        Original-Legacy hält, passierte deren verstecktes rein-negatives Segment die
        Positive-/Negative-Checks unten und würde ``migrated`` markiert → query-sichtbar,
        WÄHREND deren Original-Legacy-Quelle noch attached ist → dieselben Legacy-Zeilen
        DOPPELT. ``migrating``-Segmente werden daher – wie das aktive und das im aktuellen
        Batch kopierte – NIE hier promotet; ihre eigene Quelle hebt sie erst nach ihrem
        eigenen Detach (``_finalize_and_detach``) in den sichtbaren Rang.
        """
        store = self._store
        exclude = exclude_ids or set()
        active_id = store._active_segment.segment_id if store._active_segment else None
        for segment in await store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue
            if segment.status == SEGMENT_STATUS_MIGRATED:
                continue
            if segment.status == SEGMENT_STATUS_MIGRATING:
                continue  # fremde (oder eigene) versteckte In-Progress-Chunks NIE promoten (#951, Runde 27, Finding 2)
            if segment.segment_id == active_id:
                continue
            if segment.segment_id in exclude:
                continue
            path = store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = _sqlite_ro_uri(path, params="mode=ro")
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
        uri = _sqlite_ro_uri(path, params="mode=ro")
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
        low, high = await self._bucket_gid_bounds()
        best = 0
        for segment in await self._store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue  # read-only eingehängte Legacy-Segmente haben keine v2-Tabelle
            path = self._store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = _sqlite_ro_uri(path, params="mode=ro")
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

    async def _legacy_max_rowid(self) -> int:
        """Höchste ``id`` (rowid) der aktuellen Legacy-Quelle read-only (0, wenn leer/fehlend).

        Dient dem Stale-Cursor-Reset (#951, P2, Codex :658): nur wenn der aus der ALTEN
        Generation gefaltete Cursor diese aktuelle ``MAX(id)`` bereits abdeckt (Cursor >=
        MAX), würde ``_read_batch`` leer liefern und die neuen Zeilen permanent verstecken;
        dann wird generations-frisch ab 0 gelesen. Rein read-only wie ``_read_batch``
        (``immutable=1`` nach etwaigem Small-WAL-Checkpoint), ohne die Datei zu verändern.
        """
        legacy_path = self._legacy_path.resolve()
        if not legacy_path.exists():
            return 0
        await self._checkpoint_dirty_wal_if_small(legacy_path)
        uri = _sqlite_ro_uri(legacy_path, params="mode=ro&immutable=1")
        conn = await aiosqlite.connect(uri, uri=True)
        try:
            async with conn.execute("SELECT MAX(id) FROM ringbuffer") as cur:
                row = await cur.fetchone()
        except aiosqlite.Error:
            return 0
        finally:
            await conn.close()
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    async def _cursor_is_append_only(self, after_rowid: int) -> bool:
        """True, wenn der Cursor ``after_rowid`` beweisbar zur AKTUELLEN Datei-Generation gehört.

        Genutzt vom Stale-Cursor-Reset (#951, P2, Codex :680): Ist die Datei-Identität stale,
        muss der Cursor generations-frisch auf 0 zurückgesetzt werden, ES SEI DENN, die
        Änderung ist ein reiner ``append``/Grow (neue rowids OBERHALB des Cursors, die bereits
        migrierten Zeilen ``1..after_rowid`` unverändert). Nur ``_legacy_max_rowid() > cursor``
        zu prüfen genügt NICHT: eine GRÖSSERE Ersatzdatei erfüllt das ebenfalls, obwohl ihre
        Zeilen ``1..cursor`` andere Daten tragen.

        Beweis für append-only – bewusst gid-derivations-UNABHÄNGIG (der ``_source_factor`` wird
        pro Attach neu aus der ``segment_id`` gespiegelt und ist über Re-Attaches hinweg NICHT
        stabil, deshalb wird NICHT über eine berechnete gid gesucht):

        * Es wurden genau ``after_rowid`` Zeilen dieser Quelle migriert (Anzahl der negativen,
          quell-migrierten v2-Events == Cursor). Eine kleinere/getruncatete Ersatzquelle bricht
          das (Cursor > migrierte Menge der aktuellen Generation ist hier nicht messbar, aber
          die Boundary-Zeile fehlt, s. u.).
        * Die zuletzt migrierte Zeile (höchste = jüngste negative gid) trägt IDENTISCHES
          ``ts``/``new_value`` wie die aktuelle Quell-Zeile bei rowid ``after_rowid``. Trifft das
          zu, ist ``1..after_rowid`` nachweislich der unveränderte Kopf der aktuellen Datei und
          die neue Generation beginnt OBERHALB des Cursors → Cursor gültig. Weicht die
          Boundary-Zeile ab oder fehlt sie (replace/truncate), ist die Quelle ersetzt → Reset.

        Ein ``after_rowid`` von 0 (nichts migriert) ist trivial nicht append-only-schützbar; der
        Reset-Effekt (ab 0 lesen) ist dort ohnehin ein No-op. Rein read-only, ohne die Quelle zu
        verändern.
        """
        if after_rowid <= 0:
            return False
        source_row = await self._read_row_at(after_rowid)
        if source_row is None:
            return False
        count, last_migrated = await self._migrated_count_and_last()
        if count != after_rowid or last_migrated is None:
            return False
        return source_row == last_migrated

    async def _read_row_at(self, rowid: int) -> tuple[object, object] | None:
        """Liest ``(ts, new_value)`` der Legacy-Zeile bei ``rowid`` read-only (``None``, wenn fehlend)."""
        legacy_path = self._legacy_path.resolve()
        if not legacy_path.exists():
            return None
        await self._checkpoint_dirty_wal_if_small(legacy_path)
        uri = _sqlite_ro_uri(legacy_path, params="mode=ro&immutable=1")
        conn = await aiosqlite.connect(uri, uri=True)
        try:
            async with conn.execute("SELECT ts, new_value FROM ringbuffer WHERE id = ?", (rowid,)) as cur:
                row = await cur.fetchone()
        except aiosqlite.Error:
            return None
        finally:
            await conn.close()
        if row is None:
            return None
        return (row[0], row[1])

    async def _migrated_count_and_last(self) -> tuple[int, tuple[object, object] | None]:
        """Anzahl migrierter (negativer) v2-Events + ``(ts, new_value)`` des jüngsten.

        „Jüngste" = höchste (am wenigsten negative) negative gid, entspricht der zuletzt
        migrierten Legacy-rowid. Bewusst über den REINEN Vorzeichen-Filter ``global_event_id < 0``
        statt über einen quell-gescopten Bereich: der ``_source_factor`` wird pro Attach neu aus
        der ``segment_id`` gespiegelt und ist über Re-Attaches NICHT stabil, ein berechneter
        Bereich verfehlte die tatsächlich geschriebenen gids. Der Vorzeichen-Filter deckt den
        Ein-Quell-Fall (Regelfall der Migration) exakt ab. Sind mehrere Quellen in denselben
        Store migriert, überzählt er FREMDE migrierte Zeilen → ``count`` weicht ab → die Prüfung
        liefert konservativ ``False`` (Reset statt Cursor-Erhalt); der akzeptierte Tradeoff ist
        dort ein transientes Doppel-Delivery, nie stiller Verlust. Read-only über alle v2-Segmente.
        """
        count = 0
        best_gid: int | None = None
        best_fields: tuple[object, object] | None = None
        for segment in await self._store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION:
                continue  # read-only eingehängte Legacy-Segmente haben keine v2-Tabelle
            path = self._store._segments_dir / segment.filename
            if not path.exists():
                continue
            uri = _sqlite_ro_uri(path, params="mode=ro")
            conn = await aiosqlite.connect(uri, uri=True)
            try:
                async with conn.execute("SELECT COUNT(*), MAX(global_event_id) FROM ringbuffer WHERE global_event_id < 0") as cur:
                    agg = await cur.fetchone()
                if agg is None or agg[0] == 0:
                    continue
                count += int(agg[0])
                seg_max = int(agg[1])
                if best_gid is None or seg_max > best_gid:
                    async with conn.execute(
                        "SELECT ts, new_value FROM ringbuffer WHERE global_event_id = ?",
                        (seg_max,),
                    ) as cur:
                        row = await cur.fetchone()
                    if row is not None:
                        best_gid = seg_max
                        best_fields = (row[0], row[1])
            except aiosqlite.Error:
                continue
            finally:
                await conn.close()
        return count, best_fields

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
        uri = _sqlite_ro_uri(legacy_path, params="mode=ro&immutable=1")
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
        # Datei-Identität (#951, P2, migration.py:610): ein Alt-State ohne ``identity`` liefert
        # ``None`` und wird in ``done_is_stale`` konservativ als „nicht stale" behandelt
        # (Rückwärtskompat). Nur wohlgeformte int-Felder werden übernommen; unlesbare Werte
        # degradieren auf ``None`` (= wie Alt-State).
        identity = self._parse_state_identity(data.get("identity"))
        return _ResumeState(
            last_rowid=int(data.get("last_rowid", 0)),
            done=bool(data.get("done", False)),
            identity=identity,
        )

    @staticmethod
    def _parse_state_identity(raw: object) -> dict[str, int] | None:
        """Liest das gespeicherte Identitäts-dict aus dem State-JSON – ``None``, wenn fehlend/unlesbar."""
        if not isinstance(raw, dict):
            return None
        try:
            return {str(key): int(value) for key, value in raw.items()}
        except (ValueError, TypeError):
            return None

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
