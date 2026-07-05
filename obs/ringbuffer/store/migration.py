"""Legacy-Single-DB-Kompatibilität für den Segment-Store (#934).

Bestehende OBS-Installationen haben eine einzelne ``obs_ringbuffer.db`` im
**alten** Format (Tabelle ``ringbuffer`` ohne ``global_event_id`` und ohne
typisierte Wertspalten — ein v1-artiges Schema mit segment-lokaler rowid). Der
segmentierte Store (v2) muss diese Datei weiter lesbar halten und darf sie
**niemals** im kritischen Startup vollständig scannen oder migrieren — eine
20–30-GB-Datei würde den Start sonst blockieren.

Die Legacy-Datei wird daher beim Startup ausschließlich **read-only** als
Legacy-Segment ins Manifest eingehängt (``attach_readonly``): kein Vollscan,
kein ``integrity_check``/Checkpoint auf einer ggf. großen Datei. Neue Writes
gehen sofort in v2-Segmente; der Read-Pfad degradiert für das Legacy-Segment
kontrolliert auf den v1-Zweig. Zurückgewonnen wird die Datei ausschließlich
über die segmentgenaue FIFO-Retention (als global ältestes Segment, No-Zero-
History-Guard beachtet).

Grundgebot: Es werden **keine** Legacy-Daten verändert oder gelöscht; die alte
DB bleibt unangetastet erhalten. Eine geführte, budget-gebundene Offline-
Migration der Legacy-Historie in v2-Segmente ist als eigenständiges Folge-
Feature geplant (Migrations-Assistent) und bewusst NICHT Teil dieses Moduls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from obs.ringbuffer.store.manifest import SegmentRecord
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore, _safe_getsize

logger = logging.getLogger(__name__)

# Schwellwerte (Bytes) der reinen Größen-Klassifikation. ``SMALL_MAX_BYTES``
# steuert zusätzlich, ob der Legacy-Lesepfad einen einmaligen sauberen
# WAL-Checkpoint riskieren darf (``sqlite_backend._legacy_is_small``); große
# Dateien werden nie angefasst.
SMALL_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB
LARGE_MIN_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


class LegacyClass(str, Enum):
    """Größenklasse einer Legacy-Single-DB (rein größen-/WAL-basiert)."""

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


class LegacyMigrator:
    """Behandelt genau eine Legacy-Single-DB gegenüber einem offenen Segment-Store.

    Der Store muss bereits ``open()``-et sein (ein aktives v2-Segment existiert).
    Der Migrator klassifiziert die Legacy-Datei (``classify``) und hängt sie
    additiv **read-only** als Legacy-Segment ein (``attach_readonly``) — er
    kopiert keine Daten.
    """

    def __init__(
        self,
        store: SqliteSegmentStore,
        legacy_path: str | Path,
    ) -> None:
        self._store = store
        self._legacy_path = Path(legacy_path)

    # ------------------------------------------------------------------
    # Datei-Identität (F2-Revalidierung quarantänierter Legacy-Zeilen)
    # ------------------------------------------------------------------

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        """``(mtime_ns, size)`` einer Datei; fehlt sie, ``(0, 0)`` (Runde 29, Finding 2)."""
        try:
            st = path.stat()
        except OSError:
            return (0, 0)
        return (st.st_mtime_ns, st.st_size)

    def _legacy_identity(self) -> tuple[int, int, int, int, int, int] | None:
        """Aktuelle Datei-Identität der Legacy-Quelle inkl. WAL/SHM-Sidecars (#951, Runde 36, F2).

        Dient der Revalidierung quarantänierter Legacy-Manifest-Zeilen: wird die
        Datei nach dem Attach repariert/ersetzt, weicht diese Identität vom beim
        Attach persistierten Sidecar ab und die reparierte Historie wird wieder
        eingehängt. Nur Dateisystem-Metadaten – die DB wird NICHT geöffnet (kein
        Startup-Scan). Fehlt die Hauptdatei, ``None``.

        WAL/SHM-Erfassung: neue Legacy-Zeilen können per SQLite-WAL committet
        werden, während die Haupt-``obs_ringbuffer.db`` (mtime/size) identisch
        bleibt und sich nur ``-wal``/``-shm`` ändern. Die Sidecar-``(mtime_ns,
        size)`` fließen daher mit ein.
        """
        main = self._legacy_path
        try:
            st = main.stat()
        except OSError:
            return None
        wal_mtime, wal_size = self._file_identity(Path(f"{main}-wal"))
        shm_mtime, shm_size = self._file_identity(Path(f"{main}-shm"))
        return (st.st_mtime_ns, st.st_size, wal_mtime, wal_size, shm_mtime, shm_size)

    def _current_identity_fields(self) -> dict[str, int] | None:
        """Aktuelle Identitätsfelder als benanntes dict – ``None`` ohne Hauptdatei."""
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

    # ------------------------------------------------------------------
    # Klassifikation + read-only einhängen (kein Scan)
    # ------------------------------------------------------------------

    def classify(self) -> LegacyClassification | None:
        """Klassifiziert die Quelle – ODER liefert ``None``, wenn keine Datei existiert."""
        return classify_legacy_db(self._legacy_path)

    async def attach_readonly(self, classification: LegacyClassification) -> SegmentRecord:
        """Hängt die Legacy-Datei read-only als Legacy-Segment ein — ohne Vollscan.

        Das Manifest bekommt einen additiven Legacy-Eintrag; der Read-Pfad
        degradiert beim Lesen auf den v1-Zweig. Bei dirty WAL wird der Fall
        geflaggt und NICHT im Startup gecheckpointet.

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
