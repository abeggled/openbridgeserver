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
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

import aiosqlite

from obs.core.json import json_dumps

logger = logging.getLogger(__name__)
_UNSET = object()
_ALLOWED_STORAGE_MODELS = {"memory", "disk", "file"}

# Ableitung von ``segment_max_bytes`` aus ``max_file_size_bytes`` (#919): die
# Segment-Größe ist von der Rotationszeit entkoppelt und dient nur noch als
# Größen-Notbremse (Safety-Cap). Zeit (``segment_max_age``) ist im Normalbetrieb
# der primäre Rotations-Trigger. Bei unbegrenztem Size-Budget (None) ein fester
# Default von 256 MiB — NICHT budgetabhängig. Bei gesetztem Budget
# ``min(256 MiB, max_file_size_bytes // 3)``: das ``//3`` (RETENTION_SEGMENT_RATIO)
# garantiert die 3-Segment-Regel für jedes positive Budget; KEINE 4-MiB-Untergrenze
# im Auto-Pfad, damit auch winzige Budgets im Auto-Start nie ein 422 auslösen.
_SEGMENT_MAX_BYTES_DEFAULT = 256 * 1024 * 1024  # 256 MiB (fester Default, budget-unabhängig)


def derive_segment_max_bytes(max_file_size_bytes: int | None) -> int:
    """Leitet ``segment_max_bytes`` aus ``max_file_size_bytes`` ab (#919/#951).

    * Budget None (unbegrenztes Size-Budget) → **256 MiB** (fester Default, NICHT
      budgetabhängig).
    * Budget gesetzt → **min(256 MiB, max_file_size_bytes // 3)** (RETENTION_SEGMENT_RATIO).
      Das ``//3`` garantiert die 3-Segment-Regel
      (``max_file_size_bytes >= 3 * segment_max_bytes``) für jedes Budget ab der
      technischen Segment-Untergrenze von ``RETENTION_SEGMENT_RATIO`` (= 3) Byte –
      es gibt bewusst KEINE 4-MiB-Untergrenze im Auto-Pfad, damit auch winzige
      Budgets im Auto-Start nie ein 422 auslösen.

    Technische Untergrenze (#951, P2): ein positives Segment muss mindestens 1 Byte
    groß sein, die 3-Segment-Regel verlangt also ``max_file_size_bytes >= 3``. Für
    degenerierte Budgets von 1 oder 2 Byte (per API-Modell ``ge=1`` zwar gültig, aber
    zu klein für ein einziges SQLite-Segment) ist die Regel mit einem positiven
    Segment mathematisch unerfüllbar; die Ableitung liefert das kleinstmögliche
    positive Segment (1 Byte). Damit die Auto-Ableitung in diesem Fall dennoch keinen
    Startup-Crash über ``validate_store_config`` verursacht, hebt der Aufrufer das an
    den Store weitergereichte Retention-Budget auf diese Untergrenze an
    (siehe ``_effective_store_max_file_size_bytes``).
    """
    from obs.ringbuffer.store.config import RETENTION_SEGMENT_RATIO

    if max_file_size_bytes is None:
        return _SEGMENT_MAX_BYTES_DEFAULT
    return max(1, min(_SEGMENT_MAX_BYTES_DEFAULT, max_file_size_bytes // RETENTION_SEGMENT_RATIO))


def _effective_store_max_file_size_bytes(
    max_file_size_bytes: int | None,
    segment_max_bytes: int,
    *,
    explicit_segment: bool,
) -> int | None:
    """Hebt das Retention-Budget auf die 3-Segment-Untergrenze an, wenn nötig (#951, P2).

    Für degenerierte Budgets (1/2 Byte) ist die 3-Segment-Regel mit einem positiven
    Segment unerfüllbar. Damit ``validate_store_config`` beim (Auto-)Store-Open nicht
    crasht, wird das an den Store gereichte ``max_file_size_bytes`` in genau diesem
    Fall auf ``RETENTION_SEGMENT_RATIO * segment_max_bytes`` angehoben – der kleinste
    Wert, der die Regel erfüllt. Für alle regelkonformen Budgets (>= 3 Byte, der
    Normalfall) bleibt der Wert unverändert.

    Der Uplift greift NUR im auto-abgeleiteten Pfad (``explicit_segment=False``, der
    Tiny-Budget-Clamp aus ``derive_segment_max_bytes``). Ist ``segment_max_bytes``
    EXPLIZIT konfiguriert (Config/Konstruktor, ``explicit_segment=True``), bleibt das
    konfigurierte ``max_file_size_bytes`` ein harter Deckel: eine zu grobe explizite
    Segmentgröße darf das Retention-Budget NICHT still aufblähen, sondern muss die
    3-Segment-Ablehnung in ``validate_store_config`` auslösen (#951, F1). Sonst liefe
    z. B. ``max_file_size_bytes=100 MiB`` mit explizitem ``segment_max_bytes=64 MiB``
    still mit 192 MiB Store-Budget, statt zu scheitern oder 100 MiB zu honorieren.
    """
    from obs.ringbuffer.store.config import RETENTION_SEGMENT_RATIO

    if max_file_size_bytes is None:
        return None
    if explicit_segment:
        return max_file_size_bytes
    return max(max_file_size_bytes, RETENTION_SEGMENT_RATIO * segment_max_bytes)


_SQLITE_CORRUPTION_MARKERS = (
    "database disk image is malformed",
    "file is not a database",
    "integrity_check failed",
)
# Transiente „closed database"-Marker (#951, Pkt 1): schließt der Write-Pfad die
# aktive Segment-Connection (Rotation) genau während ein Read sie hält, wirft
# aiosqlite je nach Zeitpunkt „cannot operate on a closed database" bzw. „no active
# connection". Das ist KEINE Korruption, sondern eine reine Read/Rotate-Kollision,
# die durch einen Retry unter ``self._lock`` (rotationsserialisiert) verschwindet.
_CLOSED_DB_MARKERS = (
    "cannot operate on a closed database",
    "no active connection",
)
_MAX_QUARANTINE_FILES_PER_STORAGE_FILE = 3
_DELETE_OLDEST_BATCH_SIZE = 500
# Bounded-Kandidaten-Cap für den segmentierten Read-Pfad (#919): begrenzt den
# Legacy-Python-Fallback (Value-/Metadaten-Filter ohne typisierte Spalten) und
# entsperrt guarded contains/regex ohne Zeitfenster — ohne unbounded Full-Scan.
_SEGMENTED_CANDIDATE_CAP = 10_000
_enabled = True


class RingBufferStorageDeleteIncompleteError(OSError):
    """Raised when ringbuffer storage deletion fails after unlinking started."""


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
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_entry_id ON ringbuffer_metadata_bindings(entry_id);
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
        *,
        segmented: bool = False,
        segment_max_bytes: int | None = None,
        segment_max_rows: int | None = None,
        segment_max_age: int | None = None,
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
        self._last_recovery_at: str | None = None
        self._last_recovery_files: list[str] = []
        self._lock = asyncio.Lock()
        # Segmentierter Store (#919) — OPT-IN. Solange ``segmented`` False ist,
        # bleibt der gesamte Legacy-Single-File-Pfad unverändert und ``_store``
        # None; keine der Segment-Codepfade unten wird betreten.
        self._segmented = bool(segmented)
        # Roh-Config (``None`` = auto) getrennt vom effektiven Wert halten: nur so
        # kann ein späterer Budget-Wechsel die AUTO-Segmentgröße neu ableiten,
        # statt auf dem einmal abgeleiteten ``budget/3`` einzufrieren (#919).
        self._segment_max_bytes_config = segment_max_bytes
        self._segment_max_bytes = segment_max_bytes
        self._segment_max_rows = segment_max_rows
        self._segment_max_age = segment_max_age
        self._store: Any = None
        self._segment_created_at: str | None = None

    @property
    def segmented(self) -> bool:
        return self._segmented

    @property
    def store(self) -> Any:
        """Der offene ``SqliteSegmentStore`` im segmentierten Modus, sonst ``None``."""
        return self._store

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        async with self._lock:
            if self._segmented:
                await self._open_segment_store_locked()
            else:
                try:
                    await self._open_connection_locked()
                except Exception as exc:
                    if not self._can_recover_from(exc):
                        raise
                    await self._recover_corrupt_storage_locked(exc)
        logger.info(
            "RingBuffer started (%s, segmented=%s, max_entries=%s, max_file_size_bytes=%s, max_age=%s)",
            self._storage,
            self._segmented,
            self._max_entries,
            self._max_file_size_bytes,
            self._max_age,
        )

    async def _open_segment_store_locked(self) -> None:
        """Öffnet den Segment-Store (#919) und hängt eine Legacy-DB read-only ein.

        Startup darf NICHT blockieren: eine bestehende Legacy-``obs_ringbuffer.db``
        wird über den ``LegacyMigrator`` **read-only** als Legacy-Segment
        eingehängt (``attach_readonly`` — kein Startup-Scan, kein Checkpoint auf
        einer ggf. sehr großen Datei). Neue Writes gehen sofort in v2-Segmente.
        """
        from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig
        from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION, SEGMENT_STATUS_QUARANTINED
        from obs.ringbuffer.store.migration import LegacyMigrator
        from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

        # ``segment_max_bytes`` automatisch aus ``max_file_size_bytes`` ableiten,
        # wenn nicht explizit gesetzt (#919). Das abgeleitete Budget erfüllt die
        # 3-Segment-Regel immer → validate_store_config kann beim Auto-Start nicht
        # fehlschlagen. Explizite Werte werden respektiert und weiter validiert.
        if self._segment_max_bytes_config is None:
            self._segment_max_bytes = derive_segment_max_bytes(self._max_file_size_bytes)

        # Degenerierte Budgets (1/2 Byte) auf die 3-Segment-Untergrenze anheben, damit
        # die Auto-Ableitung nie über validate_store_config crasht (#951, P2). Nur im
        # auto-abgeleiteten Pfad: ist ``segment_max_bytes`` explizit gesetzt, bleibt das
        # konfigurierte Budget harter Deckel und die 3-Segment-Validierung greift (#951, F1).
        effective_max_file_size = _effective_store_max_file_size_bytes(
            self._max_file_size_bytes,
            self._segment_max_bytes,
            explicit_segment=self._segment_max_bytes_config is not None,
        )

        root = self._segment_store_root()
        store = SqliteSegmentStore(
            root,
            segments=SegmentConfig(
                segment_max_bytes=self._segment_max_bytes,
                segment_max_rows=self._segment_max_rows,
                segment_max_age=self._segment_max_age,
            ),
            retention=StoreRetentionConfig(
                max_file_size_bytes=effective_max_file_size,
                max_entries=self._max_entries,
                max_age=self._max_age,
            ),
        )
        await store.open()
        # ``store.open()`` hat bereits die Writer-Lease belegt und SQLite-
        # Connections geöffnet. Schlägt ein NACHFOLGENDER Startup-Schritt fehl
        # (Legacy-Attach oder Startup-Retention, z. B. Manifest-/Permission-
        # Fehler), dürfen diese Ressourcen nicht offen zurückbleiben: ohne
        # Cleanup gibt ``start()`` nie einen Store zurück, den ``stop()``
        # schließen könnte — die Lease/Connections leaken und ein späterer Retry
        # scheitert am belegten Segment-Root. Daher best-effort schließen und
        # ``self._store`` zurücksetzen, bevor der Originalfehler propagiert.
        try:
            self._store = store
            # Segment-Alter aus dem Manifest, NICHT ab now() (#264): ``store.open()``
            # kann ein aktives Segment WIEDERVERWENDEN, das lange vor diesem (Neu-)
            # Start angelegt wurde. Würde ``_segment_created_at`` hier auf now()
            # gesetzt, altert ein langlebiges aktives Segment nie über die
            # ``segment_max_age``-Schwelle und wächst unbegrenzt. Daher aus dem
            # ``created_at`` des aktiven Segments initialisieren; nur wenn (noch)
            # kein aktives Segment existiert, ist now() der korrekte Boden.
            active = await store.manifest.get_active_segment()
            self._segment_created_at = active.created_at if active is not None else _isoformat_utc(datetime.now(UTC))

            # Legacy-Single-DB (falls vorhanden) read-only einhängen — ohne Vollscan.
            # Idempotent: bei Neustart darf dieselbe Datei NICHT doppelt eingehängt
            # werden. Erkennung über den absoluten Pfad in den bereits registrierten
            # Legacy-Zeilen. Bewusst SCHEMA-basiert (nicht status-basiert): ein
            # Read-Fehler kann eine attached Legacy-Datei quarantinieren
            # (``mark_quarantined`` behält Dateiname + schema_version, ändert nur den
            # Status). Ein rein status-basierter ``list_legacy_segments()``-Guard
            # (``status='legacy'``) sähe diese Zeile nicht → erneuter Insert desselben
            # Dateinamens → Manifest-``UNIQUE``-Constraint bricht den Startup ab (#951,
            # Pkt 1). ``LEGACY_SCHEMA_VERSION`` erfasst alle Legacy-Zeilen unabhängig
            # vom Status; v2-Segmente (auch ``migrated``) tragen schema_version 2.
            if not _is_sqlite_memory_path(self._disk_path) and Path(_sqlite_filesystem_path(self._disk_path)).exists():
                legacy_fs_path = _sqlite_filesystem_path(self._disk_path)
                resolved_legacy = str(Path(legacy_fs_path).resolve())
                migrator = LegacyMigrator(store, legacy_fs_path, write_lock=self._lock)

                # Stale quarantined Legacy-Zeile revalidieren (#951, Codex Runde 36, F2 :366).
                # Ein corrupt/missing-table-Read kann die attached Legacy-Datei
                # ``quarantined`` markieren (``mark_quarantined`` behält Dateiname +
                # schema_version). Diese Zeile bleibt aber im schema-basierten ``existing``-
                # Guard und wird von ``list_segments_for_query`` gleichzeitig ausgeschlossen –
                # repariert/ersetzt der Operator dieselbe ``obs_ringbuffer.db``, übersprang der
                # Startup bisher ``classify()``/``attach_readonly()`` und die reparierte
                # Historie blieb dauerhaft versteckt.
                #
                # Datei-Identität: dieselbe Definition wie Marker/Resume (Runde 27/29/30) –
                # ``(mtime_ns, size)`` für Haupt-DB UND ``-wal``/``-shm``, via
                # ``migrator._current_identity_fields()``. Bewusst NICHT die Manifest-
                # ``size_bytes``: SQLite prä-allokiert Pages, sodass eine Reparatur die reine
                # Byte-Größe unverändert lassen kann; die mtime dagegen ändert sich bei jedem
                # Write. Die Identität wird beim erfolgreichen Attach in ein Sidecar neben der
                # Quelle geschrieben; ist die Zeile beim Startup ``quarantined`` UND weicht die
                # aktuelle Identität vom Attach-Sidecar ab, gilt die Quelle als repariert/
                # ersetzt → stale Zeile entfernen, damit sie unten neu klassifiziert/attached
                # wird. Unveränderte Datei (oder fehlender/übereinstimmender Sidecar) → Zeile
                # bleibt quarantined (kein Flapping).
                legacy_rows = [seg for seg in await store.manifest.list_segments() if seg.schema_version == LEGACY_SCHEMA_VERSION]
                current_identity = migrator._current_identity_fields()
                for seg in legacy_rows:
                    if (
                        seg.filename == resolved_legacy
                        and seg.status == SEGMENT_STATUS_QUARANTINED
                        and self._quarantined_legacy_file_changed(legacy_fs_path, current_identity)
                    ):
                        logger.info(
                            "RingBuffer: quarantinierte Legacy-Quelle %s hat sich seit dem Attach geändert – re-attach der reparierten Historie",
                            resolved_legacy,
                        )
                        await store.manifest.delete_segment(seg.segment_id)

                existing = {seg.filename for seg in await store.manifest.list_segments() if seg.schema_version == LEGACY_SCHEMA_VERSION}
                if resolved_legacy not in existing:
                    # Write-Lock durchreichen (#951, Runde 23): der Startup-Pfad hier
                    # migriert zwar nicht selbst, aber ein künftiger Wartungstreiber über
                    # denselben Migrator serialisiert die Write-/Hide-Sequenz dann korrekt
                    # gegen Live-``record()``-Appends (No-Op ohne Lock).
                    classification = migrator.classify()
                    if classification is not None:
                        await migrator.attach_readonly(classification)
                        # Attach-Identität für die spätere F2-Revalidierung festhalten.
                        self._write_legacy_attach_identity(legacy_fs_path, migrator._current_identity_fields())

            # Retention einmal beim Start ausführen (manifestbasiert, kein Scan): ein
            # über Budget liegender Legacy-Blob wird so nach dem ersten neuen Segment
            # zügig getrimmt (No-Zero-History-Guard beachtet, siehe Store).
            #
            # ABER: dieselbe Migrations-Deferral wie im Append-Pfad (#951, F2). Startet
            # der Server WÄHREND einer chunked Legacy-Migration neu, kann das Manifest
            # versteckte ``migrating``-v2-Chunks enthalten, während die Original-Legacy-DB
            # noch als einzige vollständige Quelle attached ist. Hätte ein Live-v2-Segment
            # bereits Zeilen (No-Zero-History-Guard erfüllt) und läge die Legacy-Datei über
            # dem Byte-Budget, löschte dieser Startup-Pass die attached Legacy-Quelle, BEVOR
            # die restlichen Legacy-Zeilen kopiert sind → Datenverlust. Solange also
            # ``list_migrating_segments()`` non-empty ist, wird der Startup-Retention-Pass
            # ausgesetzt; die Migration holt die Retention später nach (nach Detach/Abschluss).
            if not await self._migration_in_progress():
                await store.enforce_retention()
        except Exception:
            try:
                await store.close()
            except Exception:
                logger.exception("RingBuffer: Store-Cleanup nach fehlgeschlagenem segmentiertem Startup fehlgeschlagen")
            self._store = None
            self._segment_created_at = None
            raise

    def _segment_store_root(self) -> str:
        """Storage-Root des Segment-Stores neben der Legacy-DB (``<stem>_segments``)."""
        path = Path(_sqlite_filesystem_path(self._disk_path))
        return str(path.with_name(f"{path.stem}_segments"))

    @staticmethod
    def _legacy_attach_identity_path(legacy_fs_path: str) -> Path:
        """Sidecar-Pfad für die Attach-Identität einer Legacy-Quelle (#951, Runde 36, F2).

        Neben der Quelle, damit der Marker die Datei begleitet – analog zum
        ``.migrated``-Marker aus ``LegacyMigrator``.
        """
        p = Path(legacy_fs_path)
        return p.with_name(f"{p.name}.attach_identity")

    def _write_legacy_attach_identity(self, legacy_fs_path: str, identity: dict[str, int] | None) -> None:
        """Persistiert die Datei-Identität der Legacy-Quelle beim Attach (#951, Runde 36, F2).

        Best-effort: schlägt der Sidecar-Write fehl (z. B. read-only Verzeichnis), bleibt
        das Attach gültig; die F2-Revalidierung fällt dann mangels Sidecar auf „nicht
        geändert" zurück (konservativ, kein Flapping). Ein fehlender Sidecar ist damit
        kein Fehler.
        """
        if identity is None:
            return
        try:
            self._legacy_attach_identity_path(legacy_fs_path).write_text(json.dumps(identity), encoding="utf-8")
        except OSError:
            logger.warning(
                "RingBuffer: Attach-Identitaets-Sidecar fuer %s nicht schreibbar – F2-Revalidierung faellt konservativ aus", legacy_fs_path
            )

    def _quarantined_legacy_file_changed(self, legacy_fs_path: str, current_identity: dict[str, int] | None) -> bool:
        """True, wenn die quarantined Legacy-Datei sich seit dem Attach geändert hat (#951, Runde 36, F2).

        Vergleicht die aktuelle Datei-Identität (``mtime_ns``+``size`` für Haupt-DB +
        ``-wal``/``-shm``) gegen den beim Attach geschriebenen Sidecar. Fehlt der Sidecar
        (Alt-Attach vor diesem Feature) oder ist er unlesbar/kaputt, wird KONSERVATIV
        ``False`` geliefert (die Quarantäne bleibt bestehen – kein Flapping). Nur wenn der
        Sidecar existiert UND von der aktuellen Identität abweicht, gilt die Quelle als
        repariert/ersetzt.
        """
        if current_identity is None:
            return False
        try:
            raw = self._legacy_attach_identity_path(legacy_fs_path).read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not raw:
            return False
        try:
            attached = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(attached, dict):
            return False
        return {str(k): int(v) for k, v in attached.items()} != {str(k): int(v) for k, v in current_identity.items()}

    async def stop(self) -> None:
        if self._store is not None:
            await self._store.close()
            self._store = None
        await self._close_connection()

    # ------------------------------------------------------------------
    # Runtime config switch
    # ------------------------------------------------------------------

    async def reconfigure(
        self,
        storage: str,
        max_entries: int | None | object = _UNSET,
        max_file_size_bytes: int | None | object = _UNSET,
        max_age: int | None | object = _UNSET,
        *,
        segment_max_bytes: int | None | object = _UNSET,
        segment_max_rows: int | None | object = _UNSET,
        segment_max_age: int | None | object = _UNSET,
    ) -> None:
        """Switch storage model at runtime.

        Same model: apply config in-place (keeps entries).
        Model switch: restart empty (no migration).

        Segment- und Retention-Config werden im segmentierten Modus (#919/#938)
        live auf den laufenden Store propagiert — Rotation, Retention und Prognose
        greifen sofort ohne Neustart. Ein Wechsel des ``segmented``-Flags ist
        bewusst NICHT über ``reconfigure`` möglich und braucht weiterhin einen
        Neustart (der API-Layer baut den RingBuffer dafür neu auf).
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

            # Segment-Rotations-Config: gesetzte Werte übernehmen, sonst aktuellen
            # Wert behalten. ``segment_max_bytes=None`` (auto) leitet aus dem
            # effektiven ``max_file_size_bytes`` neu ab.
            resolved_segment_max_bytes = self._segment_max_bytes_config if segment_max_bytes is _UNSET else segment_max_bytes
            resolved_segment_max_rows = self._segment_max_rows if segment_max_rows is _UNSET else segment_max_rows
            resolved_segment_max_age = self._segment_max_age if segment_max_age is _UNSET else segment_max_age

            if (
                storage == self._storage
                and resolved_max_entries == self._max_entries
                and resolved_max_file_size == self._max_file_size_bytes
                and resolved_max_age == self._max_age
                and segment_max_bytes is _UNSET
                and segment_max_rows is _UNSET
                and segment_max_age is _UNSET
            ):
                return

            # Same model: apply config in-place and trim.
            if storage == self._storage:
                self._max_entries = resolved_max_entries
                self._max_file_size_bytes = int(resolved_max_file_size) if resolved_max_file_size is not None else None
                self._max_age = int(resolved_max_age) if resolved_max_age is not None else None
                if self._segmented and self._store is not None:
                    await self._apply_segment_config_locked(
                        resolved_segment_max_bytes,
                        resolved_segment_max_rows,
                        resolved_segment_max_age,
                    )
                try:
                    await self._trim()
                except Exception as exc:
                    if not self._can_recover_from(exc):
                        raise
                    await self._recover_corrupt_storage_locked(exc)
                logger.info(
                    "RingBuffer reconfigured in-place → %s, max_entries=%s, max_file_size_bytes=%s, max_age=%s, "
                    "segment_max_bytes=%s, segment_max_rows=%s, segment_max_age=%s",
                    storage,
                    self._max_entries,
                    self._max_file_size_bytes,
                    self._max_age,
                    self._segment_max_bytes,
                    self._segment_max_rows,
                    self._segment_max_age,
                )
                return

            # Model switch: close old connection and start empty without migration.
            old_storage = self._storage
            await self._close_connection()

            self._storage = storage
            self._max_entries = resolved_max_entries
            self._max_file_size_bytes = int(resolved_max_file_size) if resolved_max_file_size is not None else None
            self._max_age = int(resolved_max_age) if resolved_max_age is not None else None

            # Open new connection
            try:
                await self._open_connection_locked()
            except Exception as exc:
                if not self._can_recover_from(exc):
                    raise
                await self._recover_corrupt_storage_locked(exc)
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

    async def _apply_segment_config_locked(
        self,
        segment_max_bytes: int | None,
        segment_max_rows: int | None,
        segment_max_age: int | None,
    ) -> None:
        """Propagiert Segment- + Retention-Config live an den laufenden Store (#919/#938).

        Läuft nur im segmentierten Modus mit offenem Store und unter gehaltenem
        ``self._lock``. ``segment_max_bytes=None`` (auto) wird aus dem bereits
        aktualisierten ``self._max_file_size_bytes`` neu abgeleitet. Anschließend
        werden ``SegmentConfig`` und ``StoreRetentionConfig`` des Stores neu
        gesetzt, damit Rotation, Retention und Prognose sofort die neuen Werte
        nutzen. Ein durch die neuen (kleineren) Schwellen bereits fälliges aktives
        Segment wird unmittelbar rotiert; danach greift die Retention einmal.
        """
        from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig

        # Roh-Config (``None`` = auto) merken, DANN den effektiven Wert ableiten —
        # so folgt die Auto-Segmentgröße auch künftigen Budget-Änderungen und friert
        # nicht auf dem alten ``budget/3`` ein. Explizite Werte bleiben unangetastet.
        self._segment_max_bytes_config = segment_max_bytes
        if segment_max_bytes is None:
            segment_max_bytes = derive_segment_max_bytes(self._max_file_size_bytes)

        self._segment_max_bytes = segment_max_bytes
        self._segment_max_rows = segment_max_rows
        self._segment_max_age = segment_max_age

        # Konsistent zum Startpfad: degenerierte Budgets (1/2 Byte) auf die
        # 3-Segment-Untergrenze anheben, statt einen Store mit budget<3*segment zu
        # hinterlassen (#951, P2). Nur im auto-abgeleiteten Pfad – ein explizit zu grob
        # gesetztes ``segment_max_bytes`` bleibt hart gedeckelt und läuft in die
        # 3-Segment-Validierung (#951, F1).
        effective_max_file_size = _effective_store_max_file_size_bytes(
            self._max_file_size_bytes,
            self._segment_max_bytes,
            explicit_segment=self._segment_max_bytes_config is not None,
        )

        self._store.apply_config(
            segments=SegmentConfig(
                segment_max_bytes=self._segment_max_bytes,
                segment_max_rows=self._segment_max_rows,
                segment_max_age=self._segment_max_age,
            ),
            retention=StoreRetentionConfig(
                max_file_size_bytes=effective_max_file_size,
                max_entries=self._max_entries,
                max_age=self._max_age,
            ),
        )

        # Sofort-Rotation: ein aktives Segment, das durch die neuen Schwellen jetzt
        # über der Grenze liegt, wird unmittelbar rotiert — ohne auf das nächste
        # Event zu warten. Danach Retention einmal anwenden (Budget/Alter kann sich
        # geändert haben).
        if await self._segment_rotation_due():
            await self._store.rotate()
            self._segment_created_at = _isoformat_utc(datetime.now(UTC))
        # Dieselbe Migrations-Deferral wie im Append- (#951, F2) und Startup-Pfad
        # (Runde 22): läuft gerade eine chunked Migration (versteckte ``migrating``-
        # Segmente, noch attached Legacy-Quelle), umgeht dieser reconfigure-getriebene
        # Pass sonst die Guards. Bei positiven v2-Zeilen + Size-/Row-/Age-Druck löschte
        # die Retention die attached Legacy-Quelle, während restliche Chunks noch nicht
        # kopiert sind → Verlust nicht kopierter Legacy-Zeilen. Solange
        # ``_migration_in_progress()`` gilt, wird der Pass ausgesetzt; die Migration holt
        # die Retention nach dem Detach nach.
        if not await self._migration_in_progress():
            await self._store.enforce_retention()

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
        if not _enabled:
            return
        metadata_obj = metadata or {}
        if self._segmented:
            if self._store is None:
                return
            async with self._lock:
                await self._record_segmented_locked(
                    ts,
                    datapoint_id,
                    topic,
                    old_value,
                    new_value,
                    source_adapter,
                    quality,
                    metadata_version,
                    metadata_obj,
                )
            return
        if not self._conn:
            return
        async with self._lock:
            try:
                await self._record_locked(
                    ts,
                    datapoint_id,
                    topic,
                    old_value,
                    new_value,
                    source_adapter,
                    quality,
                    metadata_version,
                    metadata_obj,
                )
            except Exception as exc:
                if not self._can_recover_from(exc):
                    raise
                await self._recover_corrupt_storage_locked(exc)
                await self._record_locked(
                    ts,
                    datapoint_id,
                    topic,
                    old_value,
                    new_value,
                    source_adapter,
                    quality,
                    metadata_version,
                    metadata_obj,
                )

    async def _record_locked(
        self,
        ts: str,
        datapoint_id: str,
        topic: str,
        old_value: Any,
        new_value: Any,
        source_adapter: str,
        quality: str,
        metadata_version: int,
        metadata_obj: dict[str, Any],
    ) -> None:
        if not self._conn:
            return
        cursor = await self._conn.execute(
            """INSERT INTO ringbuffer
               (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, metadata_version, metadata)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ts,
                datapoint_id,
                topic,
                json_dumps(old_value),
                json_dumps(new_value),
                source_adapter,
                quality,
                metadata_version,
                json.dumps(metadata_obj),
            ),
        )
        await self._persist_metadata_indexes(cursor.lastrowid, metadata_obj)
        await self._conn.commit()
        await self._trim(reference_ts=ts)

    async def _record_segmented_locked(
        self,
        ts: str,
        datapoint_id: str,
        topic: str,
        old_value: Any,
        new_value: Any,
        source_adapter: str,
        quality: str,
        metadata_version: int,
        metadata_obj: dict[str, Any],
    ) -> None:
        """Schreibpfad im segmentierten Modus (#919).

        Der Event geht über die portable Store-Grenze (``append``). Danach wird
        auf Rotation (``segment_max_bytes``/``segment_max_rows``/``segment_max_age``)
        geprüft und – falls rotiert wurde – ``enforce_retention`` auf die jetzt
        geschlossenen Segmente angewandt.

        Post-Upgrade-Fenster (#951, Pkt 1): Ist beim Start eine über-budget
        Legacy-Single-DB read-only attached, kann der Startup-Retention-Lauf sie
        noch nicht löschen (No-Zero-History-Guard – es existiert noch keine
        nicht-Legacy-Zeile). Erst NACH dem ersten segmentierten Append ist der
        Guard erfüllt. Würde ``enforce_retention`` nur bei fälliger Rotation
        laufen, bliebe die (u. U. 20–30 GB große) Legacy-Datei bei Default-
        Schwellen (6 h / 256 MiB) bis zur ersten Rotation liegen, obwohl sie
        längst reclaimbar wäre (#919-Kernszenario). Solange also noch ein
        attached Legacy-Segment existiert, wird ``enforce_retention`` auch ohne
        fällige Rotation ausgeführt, damit die über-budget-Legacy zeitnah
        zurückgewonnen wird. Die Kosten sind an dieses transiente Upgrade-Fenster
        gekoppelt: sobald kein attached Legacy mehr existiert (Normalbetrieb),
        läuft KEIN zusätzliches ``enforce_retention`` pro Append.
        """
        from obs.ringbuffer.store.interface import StoreEvent

        # Append-getriebene Retention während laufender Live-Migration aussetzen
        # (#951, Pkt „Defer legacy retention during live migration"): eine chunked
        # Legacy-Migration legt versteckte ``migrating``-Segmente an
        # (``mark_migrating``) und promotet sie erst nach Abkopplung der Quelle
        # (``promote_migrating_segments``). Ein normaler Live-Append erzeugt jedoch
        # bereits eine positive aktive Zeile und erfüllt damit den No-Zero-History-
        # Guard – WÄHREND die Legacy-Quelle noch attached ist. Liefe der append-
        # getriebene ``enforce_retention`` dann unter Size-/Row-Druck durch, löschte
        # er die attached Legacy-Quelle, BEVOR alle Zeilen migriert sind → Datenverlust.
        # Solange also mind. ein ``migrating``-Segment existiert, werden ALLE append-
        # getriebenen enforce-Aufrufe zu No-Ops. Der reguläre (nicht append-getriebene)
        # enforce-Pfad über die API/den Job bleibt ungegated.
        migration_in_progress = await self._migration_in_progress()

        # Alters-Faelligkeit VOR dem Append pruefen (#951, Pkt 1): ist das aktive
        # Segment nach einer Idle-Phase bereits ueber ``segment_max_age``, zuerst
        # rotieren und DANN ins frische Segment schreiben. Andernfalls landete das
        # naechste Event noch im stale Segment und zoege dessen ``to_ts`` auf die neue
        # Event-Zeit – das Segment spannte weit ueber die konfigurierte Age-Grenze
        # hinaus (kuenstlich „jung" gehalten). Groessen-/Row-Faelligkeit, die erst der
        # Append reisst, bleibt korrekterweise NACH dem Append (siehe unten).
        if await self._segment_age_rotation_due():
            await self._store.rotate()
            self._segment_created_at = _isoformat_utc(datetime.now(UTC))
            # Enforce nach der VOR-Append-Age-Rotation (#951, Pkt 1): bei zeit-
            # getriebener Default-Rotation mit niedrigem/gleichmäßigem Traffic ist
            # dies der EINZIGE Rotationspfad, der greift. Ohne enforce_retention()
            # hier liefen geschlossene Segmente nie über die Retention –
            # ``max_file_size_bytes``/``max_age``/``max_entries`` würden verletzt,
            # weil der Post-Append-Rotationszweig (unten) bei diesem Traffic-Profil
            # nie fällig wird. Analog zum Post-Append-Zweig.
            if not migration_in_progress:
                await self._store.enforce_retention()

        await self._store.append(
            [
                StoreEvent(
                    ts=ts,
                    datapoint_id=datapoint_id,
                    topic=topic,
                    old_value=old_value,
                    new_value=new_value,
                    source_adapter=source_adapter,
                    quality=quality,
                    metadata_version=metadata_version,
                    metadata=metadata_obj,
                )
            ]
        )
        if await self._segment_rotation_due():
            await self._store.rotate()
            self._segment_created_at = _isoformat_utc(datetime.now(UTC))
            if not migration_in_progress:
                await self._store.enforce_retention()
        elif not migration_in_progress and await self._has_attached_legacy_segment():
            # Post-Upgrade-Fenster (#951, Pkt 1): über-budget-Legacy zeitnah
            # zurückgewinnen, sobald der No-Zero-History-Guard nach diesem Append
            # erfüllt ist – auch ohne fällige Rotation. Kostenbegrenzt: nur solange
            # ein attached Legacy-Segment existiert; im Normalbetrieb (kein Legacy)
            # läuft dieser Zweig nie.
            await self._store.enforce_retention()

    async def _migration_in_progress(self) -> bool:
        """True, solange eine chunked Legacy-Migration läuft (#951).

        Signalisiert, dass gerade Zeilen aus einer noch attached Legacy-Quelle kopiert
        werden. Solange das der Fall ist, dürfen die append-/startup-getriebenen
        ``enforce_retention``-Aufrufe die Legacy-Quelle NICHT löschen (No-Op), sonst gingen
        noch nicht kopierte Zeilen verloren.

        Zwei Zustände zählen als „in progress":

        1. Mind. ein ``migrating``-Segment existiert (Normalfall der laufenden Migration;
           nach ``promote_migrating_segments`` wieder leer).
        2. Crash-Fenster (#951, Codex Runde 37, F2 :892): ``_append_with_legacy_gids`` hat
           kopierte Legacy-Zeilen bereits committet/rotiert, der Prozess starb aber BEVOR
           diese Segmente ``migrating`` markiert wurden. Dann existiert KEIN
           ``migrating``-Segment, obwohl die Legacy-Quelle noch attached ist UND sichtbare
           (nicht-``migrating``, nicht-``migrated``) v2-Segmente negative gids tragen. Ohne
           diese Erkennung sähen die Retention-Gates die sichtbaren negativen v2-Chunks als
           non-legacy-Daten und löschten die attached Legacy-DB, BEVOR die restlichen Zeilen
           kopiert sind → Datenverlust. Die eigentliche Re-Hide-Recovery läuft danach beim
           nächsten ``migrate_chunk`` über die bestehende Runde-27-Logik.

        Der Normalfall (attached read-only Legacy OHNE negative v2-Chunks = nie migriert)
        wird NICHT über-deferred: es wird nur bei tatsächlich vorhandenen negativen v2-
        Chunks deferred. Ebenso wenig deferren ABGESCHLOSSENE ``migrated``-Chunks einer
        bereits fertig migrierten Quelle (#951, Codex :911) – diese werden in
        ``_has_visible_negative_v2_chunk`` ausgeschlossen, sonst blockierte eine fertige
        Fremd-Quelle die Retention der noch attached Quelle.
        """
        if await self._store.manifest.list_migrating_segments():
            return True
        if await self._has_attached_legacy_segment() and await self._has_visible_negative_v2_chunk():
            return True
        return False

    async def _has_visible_negative_v2_chunk(self) -> bool:
        """True, wenn ein sichtbares, NICHT-``migrated`` v2-Segment negative gids trägt (#951, F2).

        Nur READ-ONLY: iteriert das Manifest und liest je Kandidatensegment ``MIN(global_event_id)``
        über eine read-only Segment-Connection. Legacy-Segmente (``schema_version <= LEGACY``)
        werden übersprungen – ihre synthetischen negativen gids entstehen erst beim Read und
        sind kein Migrations-Fortschritt. ``migrating``/``quarantined`` Segmente sind bereits
        durch Punkt 1 bzw. den unsichtbaren Zustand abgedeckt und zählen hier nicht als
        „sichtbar". Ein zwischenzeitlich weggeräumtes Segment (Race) wird still übersprungen.

        Ausschluss ``migrated`` (#951, Codex :911): ein ABGESCHLOSSENES ``migrated``-Segment
        trägt zwar sichtbare negative gids, gehört aber zu einer bereits FERTIG migrierten
        Quelle. Ist eine ANDERE Legacy-Quelle noch attached, würde ein source-agnostischer
        Zähler diese fertigen Chunks fälschlich als „migration in progress" werten und die
        Retention der attached Quelle deferren, obwohl für sie gerade keine Zeilen kopiert
        werden → über-budget-Legacy bliebe unreklamiert. Nur nicht-``migrated`` negative
        Chunks (das Crash-Fenster: ``closed``/``active`` mit negativer gid, noch nicht
        ``migrating`` markiert) zählen daher als in-progress-Signal.
        """
        from obs.ringbuffer.store.manifest import (
            LEGACY_SCHEMA_VERSION,
            SEGMENT_STATUS_MIGRATED,
            SEGMENT_STATUS_MIGRATING,
            SEGMENT_STATUS_QUARANTINED,
        )

        hidden = {SEGMENT_STATUS_MIGRATING, SEGMENT_STATUS_QUARANTINED, SEGMENT_STATUS_MIGRATED}
        for segment in await self._store.manifest.list_segments():
            if segment.schema_version <= LEGACY_SCHEMA_VERSION or segment.status in hidden:
                continue
            try:
                conn = await self._store._connection_for_read(segment)
            except Exception:
                # Datei zwischenzeitlich weggeräumt (Retention-Delete-Race) o. Ä.
                continue
            if conn is None:
                continue
            try:
                async with conn.execute("SELECT MIN(global_event_id) FROM ringbuffer") as cur:
                    row = await cur.fetchone()
            except Exception:
                continue
            finally:
                await conn.close()
            min_gid = row[0] if row is not None else None
            if min_gid is not None and min_gid < 0:
                return True
        return False

    async def _has_attached_legacy_segment(self) -> bool:
        """True, solange (mind.) ein read-only attached Legacy-Segment existiert.

        Grenzt das zusätzliche Post-Upgrade-``enforce_retention`` auf das transiente
        Upgrade-Fenster ein (#951, Pkt 1). Ein retention-bedingter Legacy-Delete
        entfernt die ``status='legacy'``-Zeile; danach liefert dies ``False`` und der
        Normalbetrieb zahlt keinen zusätzlichen enforce pro Append.
        """
        return bool(await self._store.manifest.list_legacy_segments())

    async def _segment_rotation_due(self) -> bool:
        """True, wenn das aktive Segment eine ``segment_max_*``-Schwelle reißt."""
        active = await self._store.manifest.get_active_segment()
        if active is None:  # pragma: no cover - aktives Segment existiert nach append immer
            return False
        if self._segment_max_rows is not None and active.row_count >= self._segment_max_rows:
            return True
        if self._segment_max_bytes is not None and active.size_bytes >= self._segment_max_bytes:
            return True
        return self._segment_age_due()

    async def _segment_age_rotation_due(self) -> bool:
        """True, wenn das aktive Segment BEREITS über ``segment_max_age`` liegt (#951, Pkt 1).

        Nur der Alters-Teil der Rotations-Fälligkeit und nur für ein nicht-leeres
        aktives Segment: ein leeres Segment vor dem Append zu rotieren brächte lediglich
        ein weiteres leeres Segment. Wird VOR dem Append geprüft, damit ein nach einer
        Idle-Phase überaltertes Segment zuerst geschlossen wird und das Event ins frische
        Segment geht (die ``to_ts`` des stale Segments bleibt so innerhalb der Age-Grenze).
        """
        if not self._segment_age_due():
            return False
        active = await self._store.manifest.get_active_segment()
        return active is not None and active.row_count > 0

    def _segment_age_due(self) -> bool:
        """True, wenn seit ``_segment_created_at`` mindestens ``segment_max_age`` vergangen ist."""
        if self._segment_max_age is None or self._segment_created_at is None:
            return False
        age = (_parse_iso_ts(_isoformat_utc(datetime.now(UTC))) - _parse_iso_ts(self._segment_created_at)).total_seconds()
        return age >= self._segment_max_age

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

        removed_total = 0
        remaining = limit
        while remaining > 0:
            batch_size = min(remaining, _DELETE_OLDEST_BATCH_SIZE)
            cur = await self._conn.execute(
                """
                DELETE FROM ringbuffer
                WHERE id IN (
                    SELECT id FROM ringbuffer ORDER BY id ASC LIMIT ?
                )
                """,
                (batch_size,),
            )
            removed = cur.rowcount
            await cur.close()
            if removed is None or removed < 0:
                async with self._conn.execute("SELECT changes()") as changes_cur:
                    row = await changes_cur.fetchone()
                removed = row[0] if row else 0
            if removed <= 0:
                break
            removed_total += removed
            remaining -= removed

        if removed_total == 0:
            return 0

        await self._conn.commit()
        if self._storage in {"disk", "file"}:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return removed_total

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

    def disk_file_sizes(self) -> dict[str, int]:
        """Physical on-disk sizes of the ringbuffer DB and its ``-wal``/``-shm`` sidecars.

        Reports each file separately (in contrast to ``_current_storage_bytes`` which
        folds the logical used size and the WAL into a single number) so support
        packages can spot a WAL file growing out of proportion to the DB. Returns zeros
        for the in-memory backend. See issue #908.
        """
        sizes = {"db_bytes": 0, "wal_bytes": 0, "shm_bytes": 0, "total_bytes": 0}
        if self._storage == "memory":
            return sizes
        for key, suffix in (("db_bytes", ""), ("wal_bytes", "-wal"), ("shm_bytes", "-shm")):
            candidate = f"{self._disk_path}{suffix}"
            if os.path.exists(candidate):
                sizes[key] = os.path.getsize(candidate)
        sizes["total_bytes"] = sizes["db_bytes"] + sizes["wal_bytes"] + sizes["shm_bytes"]
        return sizes

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
        if not _enabled or (not self._conn and self._store is None):
            return

        dp_id = str(event.datapoint_id)
        dp = None

        # Capture old value from our own tracking (reliable in asyncio)
        old_value = self._last_values.get(dp_id)

        try:
            from obs.core.registry import get_registry

            dp = get_registry().get(event.datapoint_id)
            topic = dp.mqtt_topic if dp else f"dp/{dp_id}/value"
        except RuntimeError:
            topic = f"dp/{dp_id}/value"

        metadata = await build_ringbuffer_metadata_snapshot(
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
        self._last_values[dp_id] = event.value

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
        candidate_cap_override: int | None = None,
        is_export: bool = False,
    ) -> list[RingBufferEntry]:
        if self._segmented:
            return await self._query_v2_segmented(
                q=q,
                adapter_any_of=adapter_any_of,
                datapoint_ids=datapoint_ids,
                value_filters=value_filters,
                datapoint_types=datapoint_types,
                metadata_tags_any_of=metadata_tags_any_of,
                metadata_adapter_types_any_of=metadata_adapter_types_any_of,
                metadata_adapter_instance_ids_any_of=metadata_adapter_instance_ids_any_of,
                metadata_group_addresses_any_of=metadata_group_addresses_any_of,
                metadata_topics_any_of=metadata_topics_any_of,
                metadata_entity_ids_any_of=metadata_entity_ids_any_of,
                metadata_register_types_any_of=metadata_register_types_any_of,
                metadata_register_addresses_any_of=metadata_register_addresses_any_of,
                from_ts=from_ts,
                to_ts=to_ts,
                from_relative_seconds=from_relative_seconds,
                to_relative_seconds=to_relative_seconds,
                limit=limit,
                offset=offset,
                sort_field=sort_field,
                sort_order=sort_order,
                dp_ids_by_name=dp_ids_by_name,
                candidate_cap_override=candidate_cap_override,
                is_export=is_export,
            )

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
        if not value_filters:
            sql += " LIMIT ? OFFSET ?"
            params.append(limit)
            params.append(offset)
        try:
            rows = await self._fetchall(sql, params)
        except Exception as exc:
            if not self._can_recover_from(exc):
                raise
            async with self._lock:
                try:
                    rows = await self._fetchall(sql, params)
                except Exception as locked_exc:
                    if not self._can_recover_from(locked_exc):
                        raise
                    await self._recover_corrupt_storage_locked(locked_exc)
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

    async def _query_v2_segmented(
        self,
        *,
        q: str,
        adapter_any_of: list[str] | None,
        datapoint_ids: list[str] | None,
        value_filters: list[dict[str, Any]] | None,
        datapoint_types: dict[str, str] | None,
        metadata_tags_any_of: list[str] | None,
        metadata_adapter_types_any_of: list[str] | None,
        metadata_adapter_instance_ids_any_of: list[str] | None,
        metadata_group_addresses_any_of: list[str] | None,
        metadata_topics_any_of: list[str] | None,
        metadata_entity_ids_any_of: list[str] | None,
        metadata_register_types_any_of: list[str] | None,
        metadata_register_addresses_any_of: list[str] | None,
        from_ts: str | None,
        to_ts: str | None,
        from_relative_seconds: int | None,
        to_relative_seconds: int | None,
        limit: int,
        offset: int,
        sort_field: str,
        sort_order: str,
        dp_ids_by_name: list[str] | None,
        candidate_cap_override: int | None = None,
        is_export: bool = False,
    ) -> list[RingBufferEntry]:
        """Read-Pfad im segmentierten Modus (#919).

        Routet die **Kern-Query** (Zeitfenster, ein datapoint_id, ein
        source_adapter, quality, value_filters, limit, offset) auf
        ``store.query(StoreQuery(...))`` und mappt das Ergebnis auf die
        bestehende ``query_v2``-Response-Form.

        Feature-Parität mit dem Legacy-``query_v2`` (#919): Freitext-``q``,
        namensaufgelöste ``dp_ids_by_name``, mehrere ``datapoint_ids``/Adapter
        (``any_of``), Metadaten-Tag/Binding-Filter sowie Sortierung nach
        ``id``/``ts`` × ``asc``/``desc`` werden echt und **gebunden** über die
        Store-Grenze bedient (kein unbegrenzter Scan). Die Bounded-Garantie bleibt
        erhalten: der Store liest je Segment höchstens ``offset+limit`` (bzw. für
        Value-/contains/regex-/Metadaten-Fälle einen expliziten Kandidaten-Cap)
        und sortiert/paginiert erst auf dieser gebundenen Kandidatenmenge.
        """
        if self._store is None:
            return []

        if sort_field not in {"id", "ts"}:
            raise ValueError("invalid sort field: expected 'id' or 'ts'")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("invalid sort order: expected 'asc' or 'desc'")
        if limit < 1:
            raise ValueError("invalid pagination: limit must be >= 1")
        if offset < 0:
            raise ValueError("invalid pagination: offset must be >= 0")

        effective_from = _resolve_time_bound(absolute_ts=from_ts, relative_seconds=from_relative_seconds, pick_newer=True)
        effective_to = _resolve_time_bound(absolute_ts=to_ts, relative_seconds=to_relative_seconds, pick_newer=False)
        if effective_from and effective_to and effective_from >= effective_to:
            raise ValueError("invalid time filter: effective 'from' must be earlier than effective 'to'")

        normalized_dps = [dp_id.strip() for dp_id in (datapoint_ids or []) if dp_id.strip()]
        normalized_adapters = [adapter.strip() for adapter in (adapter_any_of or []) if adapter.strip()]
        normalized_names = [dp_id.strip() for dp_id in (dp_ids_by_name or []) if dp_id.strip()]

        metadata_binding_filters = {
            column: values
            for column, values in {
                "adapter_type": _normalize_string_filters(metadata_adapter_types_any_of),
                "adapter_instance_id": _normalize_string_filters(metadata_adapter_instance_ids_any_of),
                "group_address": _normalize_string_filters(metadata_group_addresses_any_of),
                "topic": _normalize_string_filters(metadata_topics_any_of),
                "entity_id": _normalize_string_filters(metadata_entity_ids_any_of),
                "register_type": _normalize_string_filters(metadata_register_types_any_of),
                "register_address": _normalize_string_filters(metadata_register_addresses_any_of),
            }.items()
            if values
        }

        from obs.ringbuffer.store.interface import StoreQuery

        # Value-Filter-Auswertung (#951, Wurzel-Fix): Der segmentierte v2-Pushdown
        # filtert typ-inkompatible Zeilen ueber die typisierten Spalten
        # (``*_value_num``/``*_value_text``) STILL weg, waehrend die kanonische
        # Legacy-Referenz ``_matches_value_filter`` (Memory-Pfad ``query_v2``) den Typ
        # row-lazy aus dem tatsaechlichen Zeilenwert ableitet und bei Inkompatibilitaet
        # 422 wirft. Diese Semantik-Divergenz war die Wurzel der wiederkehrenden
        # Value-Filter-Findings.
        #
        # Aufloesung: Value-Filter laufen NUR dann als typisierter SQL-Pushdown, wenn der
        # Scope EXPLIZIT und AUSSCHLIESSLICH auf bekannte, typkompatible ``datapoint_ids``
        # zeigt (schnell, vollstaendig, bei sauberen Daten nachweislich divergenzfrei).
        # Jeder andere Fall (unbekannter/inkompatibler Typ, Scope-Verbreiterung ueber
        # q/adapter/name-hit/metadata) wird row-lazy ueber die gebundene Kandidatenmenge
        # mit ``_apply_value_filters`` ausgewertet - also EXAKT der Memory-Referenz.
        # Divergenz zu ``segmented=False`` ist damit per Konstruktion ausgeschlossen; die
        # fruehere Discovery-/Vorab-422-Maschinerie entfaellt.
        normalized_metadata_tags = _normalize_string_filters(metadata_tags_any_of)
        pushdown_value_filters = _value_filters_pushable(
            list(value_filters or []),
            datapoint_ids=normalized_dps,
            adapters=normalized_adapters,
            names=normalized_names,
            q=q or "",
            has_metadata=bool(normalized_metadata_tags) or bool(metadata_binding_filters),
            datapoint_types=datapoint_types,
        )
        row_lazy_value_filters = bool(value_filters) and not pushdown_value_filters

        # Bounded-Garantie: ohne engen Zeitrahmen liest der Store hoechstens diese
        # Kandidatenzahl je Segment. Der CSV-Export paginiert mit wachsendem ``offset``
        # und uebergibt daher einen mit ``offset+limit`` mitwachsenden Cap
        # (``candidate_cap_override``); der Monitor-Live-View behaelt den festen Cap.
        effective_cap = candidate_cap_override if candidate_cap_override is not None else _SEGMENTED_CANDIDATE_CAP

        def _build_store_query(*, fetch_limit: int, fetch_offset: int, fetch_value_filters: list[dict[str, Any]]) -> StoreQuery:
            return StoreQuery(
                from_ts=effective_from,
                to_ts=effective_to,
                # Legacy-``query_v2`` behandelt beide Zeitgrenzen exklusiv.
                from_exclusive=True,
                to_exclusive=True,
                datapoint_ids=normalized_dps,
                source_adapters=normalized_adapters,
                q=q or None,
                dp_ids_by_name=normalized_names,
                metadata_tags_any_of=normalized_metadata_tags,
                metadata_binding_filters=metadata_binding_filters,
                limit=fetch_limit,
                offset=fetch_offset,
                sort_field=sort_field,
                sort_order=sort_order,
                value_filters=fetch_value_filters,
                candidate_cap=effective_cap,
                is_export=is_export,
            )

        def _entries_from_rows(rows: list[dict[str, Any]]) -> list[RingBufferEntry]:
            return [
                RingBufferEntry(
                    id=row["global_event_id"],
                    ts=row["ts"],
                    datapoint_id=row["datapoint_id"],
                    topic=row["topic"],
                    old_value=row["old_value"],
                    new_value=row["new_value"],
                    source_adapter=row["source_adapter"],
                    quality=row["quality"],
                    metadata_version=row["metadata_version"],
                    metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                )
                for row in rows
            ]

        # Row-lazy EXPORT (#951, Codex :1583): kann der Value-Filter nicht gepusht
        # werden, darf der Export NICHT bei ``offset+limit`` roh cappen. Sonst liefert
        # ein Chunk, dessen NEUESTE Roh-Kandidaten den Filter nicht matchen, eine leere
        # Seite und die Export-Schleife stoppt, obwohl aeltere matchende Zeilen jenseits
        # des Fensters nie gelesen wurden. Wie der Legacy-Export batch-scannen wir den
        # Scope (feste Batch-Groesse, wachsender Store-``offset``, Value-Filter row-lazy
        # via ``_apply_value_filters``) und akkumulieren GEMATCHTE Zeilen, bis genug fuer
        # ``offset+limit`` vorliegen ODER der Scope erschoepft ist (ein Batch liefert
        # weniger Rohzeilen als angefordert). Ein ``max_batches``-Backstop deckelt
        # pathologische Faelle. Der 422-Fall (inkompatibler Typ) propagiert unveraendert
        # aus ``_apply_value_filters``, der Export bricht ab wie Legacy.
        if row_lazy_value_filters and is_export:
            batch_size = max(1, _SEGMENTED_CANDIDATE_CAP)
            needed = offset + limit
            # Backstop: genug Batches, um ``needed`` Treffer selbst bei sehr duenner
            # Trefferquote zu erreichen, ohne unbegrenzt zu scannen.
            max_batches = max(1, (needed // batch_size) + 1) * 1000
            matched: list[RingBufferEntry] = []
            store_offset = 0
            for _ in range(max_batches):
                rows = await self._store_query_serialized(
                    _build_store_query(fetch_limit=batch_size, fetch_offset=store_offset, fetch_value_filters=[])
                )
                batch_entries = _entries_from_rows(rows)
                matched.extend(
                    await _apply_value_filters(
                        entries=batch_entries,
                        value_filters=list(value_filters or []),
                        datapoint_types=datapoint_types or {},
                    )
                )
                # Scope erschoepft: der Store lieferte weniger Rohzeilen als angefordert.
                if len(rows) < batch_size:
                    break
                # Genug GEMATCHTE Zeilen fuer die angeforderte Export-Seite.
                if len(matched) >= needed:
                    break
                store_offset += batch_size
            return matched[offset : offset + limit]

        # Nicht-Export (Monitor-Live-View) bzw. reiner Pushdown: EINMALIGER gebundener
        # Fetch. Im row-lazy Monitor-Fall den Value-Filter nicht pushen, die gebundene
        # Kandidatenmenge roh holen und in Python filtern+paginieren (wie der Memory-Pfad).
        fetch_limit = effective_cap if row_lazy_value_filters else limit
        fetch_offset = 0 if row_lazy_value_filters else offset
        rows = await self._store_query_serialized(
            _build_store_query(
                fetch_limit=fetch_limit,
                fetch_offset=fetch_offset,
                fetch_value_filters=list(value_filters or []) if pushdown_value_filters else [],
            )
        )
        entries = _entries_from_rows(rows)
        if not row_lazy_value_filters:
            return entries
        # Row-lazy = exakte Memory-/Legacy-Semantik (inkl. 422 bei inkompatiblem Typ),
        # gebunden durch die Kandidatenmenge.
        filtered = await _apply_value_filters(
            entries=entries,
            value_filters=list(value_filters or []),
            datapoint_types=datapoint_types or {},
        )
        return filtered[offset : offset + limit]

    async def _store_query_serialized(self, store_query: Any) -> list[dict[str, Any]]:
        """Führt den segmentierten Store-Read rotationssicher aus (#951, Pkt 1).

        Sperr-/Retry-Strategie: Der Normalfall läuft bewusst **lockfrei**, damit
        parallele Reads geschlossener Segmente nicht unnötig serialisiert werden.
        Kollidiert ein Read jedoch mit einer gleichzeitigen Rotation – der
        Write-Pfad (``_record_segmented_locked``) schließt/tauscht ``_active_conn``
        unter ``self._lock`` –, wirft aiosqlite eine transiente „closed database"/
        „no active connection". Dieser Fall wird **einmal unter ``self._lock``
        retryt**: Da ``rotate()`` denselben Lock hält, kann während des Retries
        keine Rotation dazwischenfunken, die aktive Connection bleibt für die Dauer
        des Reads gültig. Nur der rotationskritische Retry zahlt die Lock-Kosten;
        echte Fehler (keine „closed database"-Marker) werden unverändert propagiert.
        """
        try:
            return await self._store.query(store_query)
        except Exception as exc:
            if not _is_closed_db_error(exc):
                raise
            async with self._lock:
                return await self._store.query(store_query)

    async def stats(self) -> dict:
        def _effective_retention_seconds(oldest_ts: str | None) -> int | None:
            if not oldest_ts:
                return None
            try:
                oldest_dt = _parse_iso_ts(oldest_ts)
            except ValueError:
                return None
            return max(0, int((datetime.now(UTC) - oldest_dt).total_seconds()))

        if self._segmented and self._store is not None:
            store_stats = await self._store.stats()
            common = store_stats.common
            oldest_ts = common.get("oldest_ts")
            # Legacy-Stats-Form additiv um ``store`` erweitern; die bestehenden
            # Felder bleiben unverändert, damit Legacy-Consumer nicht brechen.
            return {
                "total": common.get("total", 0),
                "oldest_ts": oldest_ts,
                "newest_ts": common.get("newest_ts"),
                "storage": self._storage,
                "max_entries": self._max_entries,
                "effective_retention_seconds": _effective_retention_seconds(oldest_ts),
                "max_file_size_bytes": self._max_file_size_bytes,
                "max_age": self._max_age,
                "file_size_bytes": common.get("size_bytes", 0),
                "last_recovery_at": self._last_recovery_at,
                "last_recovery_file_count": len(self._last_recovery_files),
                # Datengetriebene Prognose (#919) — aus den geschlossenen v2-Segmenten
                # im Store berechnet; auf Top-Level gehoben für RingBufferStats.
                "prognosis": common.get("prognosis"),
                "store": store_stats.as_dict(),
            }

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
                "last_recovery_at": self._last_recovery_at,
                "last_recovery_file_count": len(self._last_recovery_files),
            }
        try:
            async with self._conn.execute("SELECT COUNT(*) AS c, MIN(ts) AS oldest, MAX(ts) AS newest FROM ringbuffer") as cur:
                row = await cur.fetchone()
        except Exception as exc:
            if not self._can_recover_from(exc):
                raise
            async with self._lock:
                try:
                    async with self._conn.execute("SELECT COUNT(*) AS c, MIN(ts) AS oldest, MAX(ts) AS newest FROM ringbuffer") as cur:
                        row = await cur.fetchone()
                except Exception as locked_exc:
                    if not self._can_recover_from(locked_exc):
                        raise
                    await self._recover_corrupt_storage_locked(locked_exc)
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
            "last_recovery_at": self._last_recovery_at,
            "last_recovery_file_count": len(self._last_recovery_files),
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

    async def _open_connection_locked(self) -> None:
        path = ":memory:" if self._storage == "memory" else self._disk_path
        if self._storage in {"disk", "file"}:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        conn: aiosqlite.Connection | None = None
        try:
            conn = await aiosqlite.connect(path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            if self._storage in {"disk", "file"}:
                await conn.execute("PRAGMA journal_mode=WAL")
            self._conn = conn
            await self._conn.executescript(_SCHEMA)
            await self._ensure_compat_schema()
            if self._storage in {"disk", "file"}:
                await self._assert_integrity_ok()
            await self._conn.commit()
        except Exception:
            if conn:
                await conn.close()
            if self._conn is conn:
                self._conn = None
            raise

    async def _assert_integrity_ok(self) -> None:
        if not self._conn:
            return
        async with self._conn.execute("PRAGMA integrity_check") as cur:
            row = await cur.fetchone()
        result = row[0] if row else ""
        if str(result).lower() != "ok":
            raise aiosqlite.DatabaseError(f"SQLite integrity_check failed: {result}")

    async def _close_connection(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _recover_corrupt_storage_locked(self, exc: Exception) -> None:
        logger.warning("RingBuffer SQLite database is corrupt; quarantining and recreating empty database: %s", exc)
        await self._close_connection()
        moved_paths = self._quarantine_storage_files()
        self._cleanup_quarantine_files()
        await self._open_connection_locked()
        self._last_values.clear()
        self._last_recovery_at = _isoformat_utc(datetime.now(UTC))
        self._last_recovery_files = moved_paths
        logger.warning("RingBuffer recovered with empty database; quarantined files=%s", moved_paths)

    def _quarantine_storage_files(self) -> list[str]:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        moved: list[str] = []
        for path in (self._disk_path, f"{self._disk_path}-wal", f"{self._disk_path}-shm"):
            if not os.path.exists(path):
                continue
            target = f"{path}.corrupt-{stamp}"
            os.replace(path, target)
            moved.append(target)
        return moved

    def _cleanup_quarantine_files(self) -> None:
        for path in (self._disk_path, f"{self._disk_path}-wal", f"{self._disk_path}-shm"):
            directory = os.path.dirname(path) or "."
            prefix = f"{os.path.basename(path)}.corrupt-"
            try:
                candidates = [os.path.join(directory, name) for name in os.listdir(directory) if name.startswith(prefix)]
            except FileNotFoundError:
                continue
            stale = sorted(candidates, reverse=True)[_MAX_QUARANTINE_FILES_PER_STORAGE_FILE:]
            for candidate in stale:
                try:
                    os.remove(candidate)
                except FileNotFoundError:
                    pass

    def _can_recover_from(self, exc: Exception) -> bool:
        return self._storage in {"disk", "file"} and _is_sqlite_corruption(exc)


def _is_sqlite_memory_path(database_path: str) -> bool:
    if database_path == ":memory:":
        return True
    if not database_path.startswith("file:"):
        return False
    parsed = urlsplit(database_path)
    if parsed.path == ":memory:":
        return True
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return query.get("mode", "").lower() == "memory"


def _sqlite_filesystem_path(database_path: str) -> str:
    if not database_path.startswith("file:"):
        return database_path
    parsed = urlsplit(database_path)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return database_path
    return unquote(parsed.path)


def default_ringbuffer_disk_path(database_path: str) -> str:
    if _is_sqlite_memory_path(database_path):
        return database_path
    database_path = _sqlite_filesystem_path(database_path)
    path = Path(database_path)
    return str(path.with_name(f"{path.stem}_ringbuffer.db"))


def delete_ringbuffer_storage_files(disk_path: str) -> None:
    """Remove the file-backed ringbuffer database and SQLite sidecar files.

    Im segmentierten Store (#919) liegen Manifest und Segment-DBs NICHT in der
    Legacy-Single-DB, sondern in einem ``<stem>_segments``-Verzeichnis neben ihr.
    Ohne dessen Löschung würde ein Monitor-Disable nur die Legacy-DB entfernen und
    den Speicher der Segmente belegt lassen; ein Re-Enable öffnete die alten Daten
    wieder statt frei zu starten. Daher zusätzlich das Segment-Store-Root
    rekursiv entfernen (best effort — schlägt es fehl, blockiert das nicht die
    Legacy-Löschung).
    """
    if _is_sqlite_memory_path(disk_path):
        return
    disk_path = _sqlite_filesystem_path(disk_path)
    storage_paths = (f"{disk_path}-wal", f"{disk_path}-shm", disk_path)
    existing_paths = [Path(path) for path in storage_paths if Path(path).exists()]
    renamed_paths: list[tuple[Path, Path]] = []
    delete_suffix = f".deleting-{os.getpid()}-{uuid4().hex}"

    try:
        for path in existing_paths:
            delete_path = path.with_name(f"{path.name}{delete_suffix}")
            os.replace(path, delete_path)
            renamed_paths.append((delete_path, path))
    except Exception:
        for delete_path, original_path in reversed(renamed_paths):
            with suppress(Exception):
                os.replace(delete_path, original_path)
        raise

    unlinked_any = False
    for delete_path, _original_path in renamed_paths:
        try:
            os.remove(delete_path)
            unlinked_any = True
        except FileNotFoundError:
            unlinked_any = True
        except OSError as exc:
            if unlinked_any:
                raise RingBufferStorageDeleteIncompleteError(str(exc)) from exc
            for rollback_path, original_path in reversed(renamed_paths):
                with suppress(Exception):
                    if rollback_path.exists() and not original_path.exists():
                        os.replace(rollback_path, original_path)
            raise

    # Segment-Store-Root (#919) erst NACH dem erfolgreichen Legacy-Teil entfernen (#951):
    # Solange der rename/remove-Rollback der Legacy-DB noch fehlschlagen und den Monitor
    # wieder auf enabled zurückstellen kann, dürfen die v2-Segmentdateien nicht bereits
    # unwiderruflich weg sein. Ab hier ist der Legacy-Teil abgeschlossen.
    #
    # Fehler-Sichtbarkeit (#951, Codex :1521): eine unvollständige/fehlgeschlagene
    # Löschung des Segment-Roots (gelockte Datei/Permissions) darf NICHT still
    # geschluckt werden. Ein ``ignore_errors=True`` ließe die API weitermachen, als
    # wäre der Storage gelöscht, während die Segmentdaten auf der Platte bleiben – ein
    # späteres Re-Enable öffnete die vermeintlich verworfene Historie wieder. Analog
    # zum Legacy-Datei-Löschpfad wird eine verbliebene Segment-Root daher als
    # ``RingBufferStorageDeleteIncompleteError`` gemeldet, sodass der Aufrufer den
    # unvollständigen Zustand erkennt. Bei Erfolg bleibt das saubere Abräumen unverändert.
    segments_root = Path(disk_path).with_name(f"{Path(disk_path).stem}_segments")
    if segments_root.exists():
        rmtree_errors: list[BaseException] = []
        shutil.rmtree(segments_root, onexc=lambda _func, _path, exc: rmtree_errors.append(exc))
        if segments_root.exists():
            detail = str(rmtree_errors[0]) if rmtree_errors else str(segments_root)
            raise RingBufferStorageDeleteIncompleteError(detail)


def is_ringbuffer_enabled() -> bool:
    return _enabled


def set_ringbuffer_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


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


def _is_sqlite_corruption(exc: Exception) -> bool:
    if not isinstance(exc, aiosqlite.Error):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _SQLITE_CORRUPTION_MARKERS)


def _is_closed_db_error(exc: Exception) -> bool:
    """True bei transienter „closed database"-Race durch Rotation (#951, Pkt 1).

    aiosqlite meldet eine während des Reads geschlossene Connection als ``ValueError``
    (``no active connection``) bzw. ``aiosqlite``-``ProgrammingError``
    (``cannot operate on a closed database``) – beide sind reine Read/Rotate-Kollisionen,
    keine Korruption. Erkennung über die stabilen Meldungsmarker.
    """
    return any(marker in str(exc).lower() for marker in _CLOSED_DB_MARKERS)


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


async def build_ringbuffer_metadata_snapshot(
    *,
    dp_id: str,
    source_adapter: str,
    datapoint: Any,
) -> dict[str, Any]:
    bindings: list[dict[str, Any]] = []
    hierarchy_nodes: list[dict[str, Any]] = []
    try:
        from obs.db.database import get_db

        db = get_db()
        rows = await db.fetchall(
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
        hierarchy_rows = await db.fetchall(
            """WITH RECURSIVE ancestors(node_id, tree_id, ancestor_id, parent_id, depth) AS (
                   SELECT hn.id, hn.tree_id, hn.id, hn.parent_id, 0
                   FROM hierarchy_datapoint_links hdl
                   JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
                   WHERE hdl.datapoint_id = ?
                   UNION ALL
                   SELECT ancestors.node_id, ancestors.tree_id, hn.id, hn.parent_id, ancestors.depth + 1
                   FROM ancestors
                   JOIN hierarchy_nodes hn ON hn.id = ancestors.parent_id
               )
               SELECT node_id, tree_id, ancestor_id
               FROM ancestors
               ORDER BY tree_id, node_id, depth""",
            (dp_id,),
        )
        ancestors_by_node: dict[tuple[str, str], list[str]] = {}
        for row in hierarchy_rows:
            tree_id = str(row["tree_id"] or "")
            node_id = str(row["node_id"] or "")
            ancestor_id = str(row["ancestor_id"] or "")
            if not tree_id or not node_id or not ancestor_id:
                continue
            ancestors_by_node.setdefault((tree_id, node_id), []).append(ancestor_id)
        hierarchy_nodes = [
            {
                "tree_id": tree_id,
                "node_id": node_id,
                "ancestor_node_ids": ancestor_ids,
            }
            for (tree_id, node_id), ancestor_ids in ancestors_by_node.items()
        ]
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
        "hierarchy_nodes": hierarchy_nodes,
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


def get_optional_ringbuffer() -> RingBuffer | None:
    return _rb


def reset_ringbuffer() -> None:
    """Reset the RingBuffer singleton. For testing only."""
    global _rb, _enabled
    _rb = None
    _enabled = True


async def init_ringbuffer(
    storage: str,
    max_entries: int | None,
    disk_path: str,
    max_file_size_bytes: int | None = None,
    max_age: int | None = None,
    *,
    segmented: bool = False,
    segment_max_bytes: int | None = None,
    segment_max_rows: int | None = None,
    segment_max_age: int | None = None,
) -> RingBuffer:
    global _rb, _enabled
    rb = RingBuffer(
        storage,
        max_entries,
        disk_path,
        max_file_size_bytes,
        max_age,
        segmented=segmented,
        segment_max_bytes=segment_max_bytes,
        segment_max_rows=segment_max_rows,
        segment_max_age=segment_max_age,
    )
    await rb.start()
    _rb = rb
    _enabled = True
    return rb


_NUMERIC_TYPES = {"FLOAT", "INTEGER"}
_BOOLEAN_TYPES = {"BOOLEAN"}
_STRING_TYPES = {"STRING"}
_REGEX_MAX_TARGET_LEN = 4096
_REGEX_TIMEOUT_SECONDS = 0.5


def _value_filters_pushable(
    value_filters: list[dict[str, Any]],
    *,
    datapoint_ids: list[str],
    adapters: list[str],
    names: list[str],
    q: str,
    has_metadata: bool,
    datapoint_types: dict[str, str] | None,
) -> bool:
    """True, wenn die Value-Filter sicher als typisierter SQL-Pushdown laufen duerfen (#951).

    Nur wenn der Scope EXPLIZIT und AUSSCHLIESSLICH auf bekannte, typkompatible
    ``datapoint_ids`` zeigt, ist der v2-Pushdown nachweislich divergenzfrei zur
    row-lazy Legacy-Referenz ``_matches_value_filter``. Jede Scope-Verbreiterung
    (``q``/adapter/name-hit/metadata) koennte Datapoints einbeziehen, deren Typ hier
    nicht bekannt ist; ein unbekannter oder zum Operator inkompatibler Typ wuerde vom
    Pushdown still weggefiltert statt - wie der Memory-Pfad - row-lazy ausgewertet und
    ggf. mit 422 abgelehnt. Alle solchen Faelle laufen daher row-lazy.
    """
    if not value_filters:
        return True
    if not datapoint_ids or adapters or names or q.strip() or has_metadata:
        return False
    if not datapoint_types:
        return False
    for dp in datapoint_ids:
        data_type = (datapoint_types.get(dp) or "").strip().upper()
        if not data_type:
            return False
        for spec in value_filters:
            operator = str(spec.get("operator", "")).strip().lower()
            if operator in {"eq", "ne"}:
                continue
            if operator in {"gt", "gte", "lt", "lte", "between"}:
                if data_type not in _NUMERIC_TYPES:
                    return False
            elif operator in {"contains", "regex"}:
                if data_type not in _STRING_TYPES:
                    return False
            else:
                return False
    return True


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
    # Eine Quelle der Wahrheit für das Safe-Regex-Gate (#951, Codex :1678): der row-lazy
    # Pfad nutzt dasselbe gehärtete, nesting-aware ``_assert_safe_regex`` wie Store- und
    # Legacy-Fallback. Die frühere schwache Vorprüfung (``_RE_UNSAFE_NESTED_QUANTIFIERS``)
    # ließ katastrophale Wrapper-Muster wie ``((a+))+b`` und quantifizierte Alternationen
    # wie ``(a|aa){30}b`` durch; die liefen dann gegen jeden Kandidatenstring und
    # verbrannten bei einem langen Non-Match den Worker/GIL, statt ein 422-taugliches
    # ``ValueError`` zu liefern. Ein laufender ``re.search`` ist in CPython (GIL) nicht per
    # Timeout abbrechbar, daher ist die Muster-Ablehnung VOR der Ausführung der einzige
    # wirksame Schutz. Die Ziel-Längen-Ablehnung (``_REGEX_MAX_TARGET_LEN``) bleibt erhalten.
    from obs.ringbuffer.store.sqlite_backend import _assert_safe_regex

    pattern = vf["pattern"]
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("filters.values[].pattern must be a non-empty string")
    _assert_safe_regex(pattern)
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
