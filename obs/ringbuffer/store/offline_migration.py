"""Budget-gebundene Offline-Migration der Legacy-Single-DB in v2-Segmente (#965).

Ersetzt die entfernte Live-Migration (#963) durch einen admin-getriggerten Job
mit zwei **harten Invarianten**:

1. Migrierte Zeilen berühren **nie** das aktive Segment – sie werden
   ausschließlich in eigene, als ``migrating`` markierte Segmentdateien kopiert.
2. Vor dem atomaren Commit ist **nichts** davon query-sichtbar
   (``list_segments_for_query`` blendet ``migrating`` aus); die attachte
   Legacy-Quelle bleibt bis zum Commit autoritativ.

**Budget-gebunden:** kopiert wird höchstens, was die Retention ohnehin behalten
würde – ``min(Legacy-Volumen, max_file_size_bytes − Headroom)``. Weil v2-Zeilen
(typisierte Spalten, Metadaten-Indexe) deutlich größer sind als ihre v1-Quelle,
wird die reale v2-Zeilengröße vor der Copy-Phase über ein SAMPLE kalibriert
(neueste Zeilen in ein Wegwerf-Segment kopieren, messen, verwerfen) und der
Cutoff daraus berechnet. Ältere Zeilen unterhalb des Cutoffs werden bewusst
verworfen (exakt das FIFO-Verhalten, nur vorgezogen). Damit sind Kopierzeit und
Platz-Peak budget- statt legacy-gebunden.

**Crash-Modell** (jede Phasengrenze):

* Crash während der Copy-Phase → Legacy unangetastet + autoritativ; die
  unsichtbaren ``migrating``-Reste werden beim nächsten Job-Start verworfen
  (Neustart der Kopie; ein gid-Range-Resume ist als Optimierung möglich, für
  die Korrektheit aber nicht nötig).
* Crash zwischen Legacy-Unlink und Manifest-Commit → der Startup-Reconciler
  (``reconcile_offline_migration``) erkennt „Legacy-Zeile ohne Datei + fertige
  ``migrating``-Segmente" und vollendet den Commit deterministisch.
* Crash nach dem Commit → Endzustand erreicht; der Entscheidungszustand wird
  vom Aufrufer (API) auf ``migrated`` gesetzt.

**Commit-only-Pause:** die Copy-Phase läuft parallel zum Live-Betrieb (kein
gemeinsamer veränderlicher Zustand mit dem Append-Pfad außer dem Manifest).
Nur der atomare Schlusspunkt (Unlink + Promote/Detach-Transaktion) läuft unter
dem Write-Lock des RingBuffers – ein Sub-Sekunden-Fenster.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_LEGACY, SegmentRecord
from obs.ringbuffer.store.sqlite_backend import (
    _LEGACY_GID_OFFSET,
    _LEGACY_GID_STRIDE,
    _LEGACY_SOURCE_BUCKETS,
    SEGMENT_SCHEMA_VERSION,
    SqliteSegmentStore,
    _safe_json_decode,
    _utc_now_compact,
)

logger = logging.getLogger(__name__)

# Rohzeilen pro Lese-/Schreib-Batch der Copy-Phase.
COPY_BATCH_ROWS = 5_000

# Sicherheitsfaktor des Disk-Prechecks: der Kopie-Peak (Legacy + Kopie) braucht
# mindestens das geschätzte Kopiervolumen an freiem Platz, plus Reserve für
# WAL/Index-Overhead der Segmentdateien.
DISK_SAFETY_FACTOR = 1.2


class OfflineMigrationError(RuntimeError):
    """Precheck-/Ablauffehler der Offline-Migration (für die API → 409/507)."""


@dataclass(frozen=True)
class MigrationPlan:
    """Ergebnis des Prechecks – Grundlage für Wizard-Vorschau und Copy-Phase."""

    legacy_segment_id: int
    legacy_path: str
    legacy_size_bytes: int
    total_rows: int
    max_rowid: int
    cutoff_rowid: int  # kopiert werden Zeilen mit rowid > cutoff_rowid
    rows_to_copy: int
    copy_bytes_estimate: int
    disk_free_bytes: int


def _legacy_gid(rowid: int, legacy_segment_id: int) -> int:
    """Synthetische gid einer migrierten Zeile – IDENTISCH zur attached-Read-Formel.

    Dieselbe Spiegelung wie ``_legacy_row_to_dict`` (sqlite_backend): damit tragen
    die kopierten Zeilen nach dem Commit exakt die IDs, unter denen Clients sie
    vor der Migration aus dem attachten Legacy-Segment gesehen haben – Paging/
    Dedupe über den Migrationszeitpunkt hinweg bleibt stabil.
    """
    source_factor = _LEGACY_SOURCE_BUCKETS - 1 - (legacy_segment_id % _LEGACY_SOURCE_BUCKETS)
    return rowid - _LEGACY_GID_OFFSET - source_factor * _LEGACY_GID_STRIDE


class OfflineLegacyMigrator:
    """Führt genau EINE budget-gebundene Offline-Migration gegen einen offenen Store aus."""

    def __init__(self, store: SqliteSegmentStore, *, write_lock: asyncio.Lock) -> None:
        self._store = store
        self._write_lock = write_lock

    # ------------------------------------------------------------------
    # Precheck / Plan
    # ------------------------------------------------------------------

    async def plan(self) -> MigrationPlan:
        legacy = await self._attached_legacy()
        if legacy is None:
            raise OfflineMigrationError("no attached legacy source to migrate")
        conn = await self._store._connection_for_read(legacy)
        if conn is None:
            raise OfflineMigrationError("legacy source is not readable")
        try:
            async with conn.execute("SELECT MAX(id), COUNT(*) FROM ringbuffer") as cur:
                row = await cur.fetchone()
        finally:
            await conn.close()
        max_rowid = int(row[0]) if row and row[0] is not None else 0
        total_rows = int(row[1]) if row and row[1] is not None else 0

        # Erste Schätzung über die v1-Zeilengröße; die Copy-Phase kalibriert die
        # reale v2-Zeilengröße vor dem eigentlichen Lauf über ein Sample nach.
        avg_row_bytes = (legacy.size_bytes / total_rows) if total_rows else 0.0
        budget = self._store._retention_config.max_file_size_bytes
        headroom = self._store._segment_config.segment_max_bytes or 0
        if budget is None or avg_row_bytes <= 0:
            rows_to_copy = total_rows
        else:
            target_volume = max(0, budget - headroom)
            rows_to_copy = min(total_rows, int(target_volume / avg_row_bytes))
        # Cutoff über die rowid-Ordnung: die NEUESTEN ``rows_to_copy`` Zeilen bleiben.
        cutoff_rowid = max_rowid if rows_to_copy == 0 else max(0, max_rowid - rows_to_copy)
        copy_bytes_estimate = int(rows_to_copy * avg_row_bytes)

        disk_free = shutil.disk_usage(str(self._store._segments_dir)).free
        if disk_free < copy_bytes_estimate * DISK_SAFETY_FACTOR:
            raise OfflineMigrationError(
                f"not enough free disk space for migration copy: need ~{int(copy_bytes_estimate * DISK_SAFETY_FACTOR)} bytes, free {disk_free}"
            )
        return MigrationPlan(
            legacy_segment_id=legacy.segment_id,
            legacy_path=legacy.filename,
            legacy_size_bytes=legacy.size_bytes,
            total_rows=total_rows,
            max_rowid=max_rowid,
            cutoff_rowid=cutoff_rowid,
            rows_to_copy=rows_to_copy,
            copy_bytes_estimate=copy_bytes_estimate,
            disk_free_bytes=disk_free,
        )

    async def _calibrate_cutoff(self, plan: MigrationPlan, legacy: SegmentRecord) -> MigrationPlan:
        """Misst die reale v2-Zeilengröße über ein Wegwerf-Sample und passt den Cutoff an."""
        budget = self._store._retention_config.max_file_size_bytes
        if budget is None or plan.rows_to_copy <= 0 or plan.total_rows == 0:
            return plan
        sample_rows = min(COPY_BATCH_ROWS, plan.total_rows)
        source = await self._store._connection_for_read(legacy)
        if source is None:
            raise OfflineMigrationError("legacy source became unreadable during calibration")
        sample_filename = f"rb_migrated_sample_{_utc_now_compact()}.sqlite"
        sample_segment = await self._store.manifest.create_migrating_segment(filename=sample_filename, schema_version=SEGMENT_SCHEMA_VERSION)
        conn = await self._store._open_segment_conn(sample_filename)
        copied = 0
        try:
            has_metadata_cols = await self._legacy_has_metadata_columns(source)
            metadata_select = "metadata_version, metadata" if has_metadata_cols else "NULL AS metadata_version, NULL AS metadata"
            async with source.execute(
                f"SELECT id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, {metadata_select} "
                "FROM ringbuffer WHERE id > ? ORDER BY id ASC LIMIT ?",
                (max(0, plan.max_rowid - sample_rows), sample_rows),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                await self._store._insert_event(conn, _legacy_gid(row["id"], plan.legacy_segment_id), _legacy_row_to_event(row))
                copied += 1
            await conn.commit()
            with contextlib.suppress(Exception):
                await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            with contextlib.suppress(Exception):
                await conn.close()
            await source.close()
        sample_size = self._store._segment_file_size(sample_filename)
        # Wegwerf-Sample entfernen (Datei + Manifest-Zeile).
        base = self._store._segments_dir / sample_filename
        for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
            with contextlib.suppress(OSError):
                candidate.unlink()
        await self._store.manifest.delete_segment(sample_segment.segment_id)
        if copied == 0 or sample_size <= 0:
            return plan
        v2_row_bytes = sample_size / copied
        headroom = self._store._segment_config.segment_max_bytes or 0
        target_volume = max(0, budget - headroom)
        rows_to_copy = min(plan.total_rows, int(target_volume / v2_row_bytes))
        cutoff_rowid = plan.max_rowid if rows_to_copy == 0 else max(0, plan.max_rowid - rows_to_copy)
        return replace(
            plan,
            rows_to_copy=rows_to_copy,
            cutoff_rowid=cutoff_rowid,
            copy_bytes_estimate=int(rows_to_copy * v2_row_bytes),
        )

    # ------------------------------------------------------------------
    # Job
    # ------------------------------------------------------------------

    async def run(self, progress: dict[str, Any]) -> dict[str, Any]:
        """Precheck → Copy-Phase → atomarer Commit. Mutiert ``progress`` live."""
        progress.update(phase="precheck", error=None)
        plan = await self.plan()
        progress.update(
            phase="copying",
            total_rows=plan.rows_to_copy,
            copied_rows=0,
            copied_bytes=0,
            dropped_rows=plan.total_rows - plan.rows_to_copy,
        )

        # Reste einer früher abgebrochenen Migration verwerfen (Crash-Modell:
        # Neustart der Kopie; Legacy war und bleibt bis zum Commit autoritativ).
        await self._discard_migrating_segments()

        legacy = await self._attached_legacy()
        if legacy is None or legacy.segment_id != plan.legacy_segment_id:
            raise OfflineMigrationError("legacy source changed during migration precheck")

        # Kalibrierung (#965): v2-Segmente speichern dieselben Zeilen deutlich
        # größer als die v1-Quelle (typisierte Spalten, Metadaten-Indexe). Die
        # reale v2-Zeilengröße wird über ein Wegwerf-Sample gemessen und der
        # Cutoff neu berechnet – sonst kopierte der Job Zeilen, die die
        # Post-Commit-Retention sofort wieder löscht (verschwendete Arbeit,
        # unnötiger Platz-Peak).
        plan = await self._calibrate_cutoff(plan, legacy)
        progress.update(total_rows=plan.rows_to_copy, dropped_rows=plan.total_rows - plan.rows_to_copy)

        if plan.rows_to_copy > 0:
            await self._copy_phase(plan, legacy, progress)

        # Atomarer Commit unter dem Write-Lock (Commit-only-Pause):
        # 1) Legacy-Dateien unlinken (die Kopie ist durabel; ab jetzt gilt der
        #    Reconciler-Pfad, falls der Prozess vor Schritt 2 stirbt),
        # 2) Promote aller ``migrating``-Segmente + Detach der Legacy-Zeile in
        #    EINER Manifest-Transaktion.
        progress.update(phase="committing")
        async with self._write_lock:
            _unlink_legacy_files(Path(plan.legacy_path))
            await self._store.manifest.commit_offline_migration([plan.legacy_segment_id])
        # Retention einmal nachziehen: der Store ist jetzt budget-konform bzw.
        # trimmt Ränder (Cutoff ist eine Schätzung über die mittlere Zeilengröße).
        await self._store.enforce_retention()
        progress.update(phase="done")
        return progress

    async def _copy_phase(self, plan: MigrationPlan, legacy: SegmentRecord, progress: dict[str, Any]) -> None:
        source = await self._store._connection_for_read(legacy)
        if source is None:
            raise OfflineMigrationError("legacy source became unreadable during migration")
        segment_max_bytes = self._store._segment_config.segment_max_bytes
        target_conn = None
        target_filename: str | None = None
        target_segment: SegmentRecord | None = None
        seg_rows = 0
        seg_from_ts: str | None = None
        seg_to_ts: str | None = None
        seg_index = 0
        try:
            has_metadata_cols = await self._legacy_has_metadata_columns(source)
            metadata_select = "metadata_version, metadata" if has_metadata_cols else "NULL AS metadata_version, NULL AS metadata"
            cursor_rowid = plan.cutoff_rowid
            while True:
                async with source.execute(
                    f"SELECT id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, {metadata_select} "
                    "FROM ringbuffer WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (cursor_rowid, COPY_BATCH_ROWS),
                ) as cur:
                    rows = await cur.fetchall()
                if not rows:
                    break
                if target_conn is None:
                    seg_index += 1
                    target_filename = f"rb_migrated_{_utc_now_compact()}_{seg_index:03d}.sqlite"
                    target_segment = await self._store.manifest.create_migrating_segment(
                        filename=target_filename, schema_version=SEGMENT_SCHEMA_VERSION
                    )
                    target_conn = await self._store._open_segment_conn(target_filename)
                    seg_rows = 0
                    seg_from_ts = None
                    seg_to_ts = None
                for row in rows:
                    event = _legacy_row_to_event(row)
                    await self._store._insert_event(target_conn, _legacy_gid(row["id"], plan.legacy_segment_id), event)
                    seg_rows += 1
                    seg_from_ts = event.ts if seg_from_ts is None or event.ts < seg_from_ts else seg_from_ts
                    seg_to_ts = event.ts if seg_to_ts is None or event.ts > seg_to_ts else seg_to_ts
                await target_conn.commit()
                cursor_rowid = rows[-1]["id"]
                progress["copied_rows"] = progress.get("copied_rows", 0) + len(rows)
                size_now = self._store._segment_file_size(target_filename)
                progress["copied_bytes"] = size_now if seg_index == 1 else progress.get("copied_bytes", 0)
                # Segment-Rollover am Größen-Cap: Stats finalisieren, Datei schließen.
                if segment_max_bytes is not None and size_now >= segment_max_bytes:
                    await self._finalize_target(target_conn, target_segment, target_filename, seg_rows, seg_from_ts, seg_to_ts)
                    target_conn = None
            if target_conn is not None:
                await self._finalize_target(target_conn, target_segment, target_filename, seg_rows, seg_from_ts, seg_to_ts)
                target_conn = None
        except BaseException:
            if target_conn is not None:
                with contextlib.suppress(Exception):
                    await target_conn.close()
            raise
        finally:
            await source.close()

    async def _finalize_target(
        self,
        conn: Any,
        segment: SegmentRecord | None,
        filename: str | None,
        rows: int,
        from_ts: str | None,
        to_ts: str | None,
    ) -> None:
        # Best-effort WAL-Truncate: die Kopie ist frisch geschrieben, kein Reader
        # hält sie (unsichtbar) – ein busy-Checkpoint ist hier nicht zu erwarten;
        # scheitert er dennoch, bleibt die Datei mit WAL korrekt lesbar.
        with contextlib.suppress(Exception):
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await conn.close()
        if segment is not None and filename is not None:
            await self._store.manifest.update_segment_stats(
                segment.segment_id,
                row_count=rows,
                size_bytes=self._store._segment_file_size(filename),
                from_ts=from_ts,
                to_ts=to_ts,
            )

    # ------------------------------------------------------------------
    # Helfer
    # ------------------------------------------------------------------

    async def _attached_legacy(self) -> SegmentRecord | None:
        legacy_segments = await self._store.manifest.list_legacy_segments()
        return legacy_segments[0] if legacy_segments else None

    async def _discard_migrating_segments(self) -> None:
        for segment in await self._store.manifest.list_migrating_segments():
            base = self._store._segments_dir / segment.filename
            for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
                with contextlib.suppress(OSError):
                    candidate.unlink()
            await self._store.manifest.delete_segment(segment.segment_id)

    @staticmethod
    async def _legacy_has_metadata_columns(conn: Any) -> bool:
        async with conn.execute("PRAGMA table_info(ringbuffer)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        return "metadata" in columns and "metadata_version" in columns


def _legacy_row_to_event(row: Any) -> StoreEvent:
    """Dekodiert eine v1-Legacy-Zeile in ein StoreEvent (JSON-Werte row-lazy)."""
    metadata = _safe_json_decode(row["metadata"]) if row["metadata"] else {}
    return StoreEvent(
        ts=row["ts"],
        datapoint_id=row["datapoint_id"],
        topic=row["topic"],
        old_value=_safe_json_decode(row["old_value"]),
        new_value=_safe_json_decode(row["new_value"]),
        source_adapter=row["source_adapter"],
        quality=row["quality"],
        metadata_version=row["metadata_version"] if row["metadata_version"] is not None else 1,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _unlink_legacy_files(legacy_path: Path) -> None:
    """Entfernt Legacy-Haupt-DB, WAL/SHM-Sidecars und den Attach-Identity-Sidecar."""
    for candidate in (
        legacy_path,
        Path(f"{legacy_path}-wal"),
        Path(f"{legacy_path}-shm"),
        legacy_path.with_name(f"{legacy_path.name}.attach_identity"),
    ):
        with contextlib.suppress(OSError):
            candidate.unlink()


async def reconcile_offline_migration(store: SqliteSegmentStore) -> None:
    """Startup-Reconciler (#965): vollendet oder neutralisiert eine unterbrochene Migration.

    Deterministische Regeln beim Öffnen des Stores:

    * ``migrating``-Segmente + Legacy-Zeile, deren DATEI FEHLT → der Prozess starb
      im Commit-Fenster (nach Unlink, vor der Manifest-Transaktion). Der Commit
      wird vollendet: promote + detach (atomar) – die kopierte Historie wird
      sichtbar, nichts geht verloren.
    * ``migrating``-Segmente OHNE Legacy-Zeile → verwaiste Kopie ohne Quelle
      (z. B. Crash einer alten Kopie und späterer ``discard``): verwerfen – ein
      Promote könnte eine PARTIELLE Alt-Historie als vollständig ausgeben.
    * ``migrating``-Segmente + Legacy-Zeile mit vorhandener Datei → Crash während
      der Copy-Phase: nichts tun (unsichtbar, Legacy autoritativ); der nächste
      Job-Start verwirft die Reste und kopiert neu.
    """
    migrating = await store.manifest.list_migrating_segments()
    if not migrating:
        return
    legacy_rows = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_LEGACY]
    if not legacy_rows:
        logger.warning("RingBuffer: %d verwaiste Offline-Migrations-Segmente ohne Legacy-Quelle – werden verworfen", len(migrating))
        for segment in migrating:
            base = store._segments_dir / segment.filename
            for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
                with contextlib.suppress(OSError):
                    candidate.unlink()
            await store.manifest.delete_segment(segment.segment_id)
        return
    missing_file_rows = [s for s in legacy_rows if not Path(s.filename).exists()]
    if missing_file_rows and len(missing_file_rows) == len(legacy_rows):
        logger.info("RingBuffer: unterbrochenen Offline-Migrations-Commit vollenden (%d Segmente)", len(migrating))
        await store.manifest.commit_offline_migration([s.segment_id for s in legacy_rows])
        return
    # Copy-Phase-Crash: Legacy-Datei existiert noch → Reste bleiben unsichtbar
    # liegen; der nächste Job-Start räumt sie weg.
    logger.info("RingBuffer: %d unsichtbare Offline-Migrations-Segmente einer unterbrochenen Kopie gefunden", len(migrating))
