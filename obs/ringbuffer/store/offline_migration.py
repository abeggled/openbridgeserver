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
from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION, SEGMENT_STATUS_MIGRATING, SegmentRecord
from obs.ringbuffer.store.sqlite_backend import (
    _LEGACY_GID_OFFSET,
    _LEGACY_GID_STRIDE,
    _LEGACY_SOURCE_BUCKETS,
    MIGRATED_FILENAME_PREFIX,
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

    def _check_disk_free(self, copy_bytes_estimate: int) -> int:
        """Prueft freien Platz gegen das (mit Sicherheitsfaktor skalierte) Kopiervolumen.

        Gemeinsam genutzt vom Precheck in ``plan()`` UND vom Recheck NACH der
        Sample-Kalibrierung (#968, Codex :257): die reale v2-Zeilengroesse kann
        ``copy_bytes_estimate`` deutlich ueber die v1-Erstschaetzung heben, sonst
        passierte ein knapper Datentraeger den Precheck und scheiterte erst beim
        Kopieren mit ENOSPC statt mit einem sauberen Precheck-Fehler.
        """
        disk_free = shutil.disk_usage(str(self._store._segments_dir)).free
        if disk_free < copy_bytes_estimate * DISK_SAFETY_FACTOR:
            raise OfflineMigrationError(
                f"not enough free disk space for migration copy: need ~{int(copy_bytes_estimate * DISK_SAFETY_FACTOR)} bytes, free {disk_free}"
            )
        return disk_free

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
        if budget is None or avg_row_bytes <= 0:
            rows_to_copy = total_rows
        else:
            target_volume = await self._target_copy_volume(budget)
            rows_to_copy = min(total_rows, int(target_volume / avg_row_bytes))
        # Cutoff über die ORDNUNG der existierenden Zeilen (#968, Codex :156): bei Lücken in
        # den ids (Age-Retention löscht nach ts, nicht nach id; jede frühere Lücke) ist
        # ``max_rowid - rows_to_copy`` NICHT die id der N-ten-neuesten existierenden Zeile.
        # Der Copy-Filter ``id > cutoff`` verlöre sonst noch existierende Alt-Zeilen, bevor der
        # Commit die Legacy-DB unlinkt. Die tatsächliche Cutoff-id kommt aus ``ORDER BY id DESC``.
        cutoff_rowid = await self._resolve_cutoff_rowid(legacy, rows_to_copy, max_rowid)
        copy_bytes_estimate = int(rows_to_copy * avg_row_bytes)

        disk_free = self._check_disk_free(copy_bytes_estimate)
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

    async def _resolve_cutoff_rowid(self, legacy: SegmentRecord, rows_to_copy: int, max_rowid: int) -> int:
        """Cutoff-id über die ORDNUNG der existierenden Zeilen statt ``MAX(id) - count`` (#968, Codex :156).

        Liefert die id der ERSTEN nicht mehr zu kopierenden Zeile, sodass der Copy-Filter
        ``WHERE id > cutoff`` genau die neuesten ``rows_to_copy`` EXISTIERENDEN Zeilen migriert –
        korrekt auch bei nicht-kontinuierlichen ids (z. B. nach Age-Retention, die nach ts löscht).
        ``0`` (alle kopieren), wenn weniger als ``rows_to_copy + 1`` Zeilen existieren; ``max_rowid``
        (nichts kopieren) bei ``rows_to_copy <= 0``.
        """
        if rows_to_copy <= 0:
            return max_rowid
        conn = await self._store._connection_for_read(legacy)
        if conn is None:
            raise OfflineMigrationError("legacy source is not readable")
        try:
            async with conn.execute("SELECT id FROM ringbuffer ORDER BY id DESC LIMIT 1 OFFSET ?", (rows_to_copy,)) as cur:
                row = await cur.fetchone()
        finally:
            await conn.close()
        return int(row[0]) if row and row[0] is not None else 0

    async def _calibrate_cutoff(self, plan: MigrationPlan, legacy: SegmentRecord) -> MigrationPlan:
        """Misst die reale v2-Zeilengröße über ein Wegwerf-Sample und passt den Cutoff an."""
        budget = self._store._retention_config.max_file_size_bytes
        # Auch einen drop-only-Plan (rows_to_copy == 0) noch kalibrieren, WENN ein Budget gesetzt ist
        # (#968, Codex :201): die v1-Erstschätzung (avg über ``legacy.size_bytes`` inkl. WAL/Sidecars)
        # überschätzt die Zeilengröße und kann ``rows_to_copy`` fälschlich auf 0 drücken. Die reale,
        # kleinere v2-Zeilengröße erlaubt bei positivem Ziel-Volumen evtl. doch das Behalten der
        # neuesten Zeilen. Ohne Budget ist ``rows_to_copy == total_rows``; ``== 0`` bedeutet dann
        # ``total_rows == 0`` (nichts zu messen).
        if plan.total_rows == 0 or (plan.rows_to_copy <= 0 and budget is None):
            return plan
        # Einen drop-only-Plan (rows_to_copy == 0) nur kalibrieren, wenn das Ziel-Volumen ÜBERHAUPT
        # Platz für Zeilen lässt (#968, Codex :206): ist ``budget - headroom - live_bytes`` bereits 0,
        # kann keine gemessene v2-Größe rows_to_copy positiv machen – die Kalibrierung schriebe nur
        # ein unnötiges Sample (das auf voller Platte scheitern könnte), statt die Legacy einfach zu
        # unlinken.
        if plan.rows_to_copy <= 0 and budget is not None and await self._target_copy_volume(budget) <= 0:
            return plan
        # Sample auf die tatsächlich geplante Kopiermenge deckeln (#968, Codex :177): bei budget-
        # gebundenen Migrationen kann ``plan.rows_to_copy`` weit unter ``COPY_BATCH_ROWS`` liegen
        # (nach dem Cutoff evtl. nur eine Handvoll Zeilen). Ein 5.000-Zeilen-Sample verbrauchte dann
        # deutlich mehr Platz/Zeit als die eigentliche Kopie. Bei einem drop-only-Plan (== 0) auf die
        # Gesamtmenge basieren, damit überhaupt gemessen wird (#968, Codex :201).
        sample_rows = min(COPY_BATCH_ROWS, plan.rows_to_copy if plan.rows_to_copy > 0 else plan.total_rows)
        source = await self._store._connection_for_read(legacy)
        if source is None:
            raise OfflineMigrationError("legacy source became unreadable during calibration")
        sample_filename = f"{MIGRATED_FILENAME_PREFIX}sample_{_utc_now_compact()}.sqlite"
        sample_segment = await self._store.manifest.create_migrating_segment(filename=sample_filename, schema_version=SEGMENT_SCHEMA_VERSION)
        conn = await self._store._open_segment_conn(sample_filename)
        copied = 0
        try:
            has_metadata_cols = await self._legacy_has_metadata_columns(source)
            metadata_select = "metadata_version, metadata" if has_metadata_cols else "NULL AS metadata_version, NULL AS metadata"
            # Die neuesten ``sample_rows`` Zeilen über die id-Ordnung greifen (#968, Codex :156):
            # ``id > max_rowid - sample_rows`` läse bei Lücken in den ids evtl. weniger Zeilen und
            # verzerrte die v2-Zeilengrößen-Schätzung. ``ORDER BY id DESC LIMIT`` ist lückenrobust;
            # die Reihenfolge ist für das Wegwerf-Sample irrelevant.
            async with source.execute(
                f"SELECT id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, {metadata_select} "
                "FROM ringbuffer ORDER BY id DESC LIMIT ?",
                (sample_rows,),
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
        # Wegwerf-Sample entfernen. Die Manifest-Zeile NUR löschen, wenn die Hauptdatei
        # wirklich weg ist (#968, Codex :210): scheiterte der Unlink (Permission/EBUSY)
        # und die Zeile würde trotzdem entfernt, bliebe eine untracked
        # ``rb_migrated_sample_*.sqlite`` auf der Platte – weder in ``/stats`` noch in
        # Retention/Retry-Cleanup sichtbar – und leakte dauerhaft Platz. Der Fehler wird
        # deshalb surfaced (Migration bricht sauber ab); das Sample bleibt als
        # ``migrating``-Segment registriert und der nächste Job-Start
        # (``_discard_migrating_segments``) bzw. der Startup-Reconciler räumt es auf.
        base = self._store._segments_dir / sample_filename
        for candidate in (Path(f"{base}-wal"), Path(f"{base}-shm")):
            with contextlib.suppress(OSError):
                candidate.unlink()
        try:
            base.unlink(missing_ok=True)
        except OSError as exc:
            # Die gemessene Sample-Größe ins Manifest schreiben, BEVOR der Cleanup-Fehler propagiert
            # und die Zeile für den Retry behalten wird (#968, Codex :260): sonst meldete /stats das
            # behaltene ``migrating``-Segment mit 0 Bytes, während die Datei weiter Platz belegt – die
            # beabsichtigte Sichtbarkeit der Retry-Zeile ginge verloren.
            with contextlib.suppress(Exception):
                await self._store.manifest.update_segment_stats(
                    sample_segment.segment_id, row_count=copied, size_bytes=sample_size, from_ts=None, to_ts=None
                )
            raise OfflineMigrationError(f"could not remove calibration sample {sample_filename}: {exc}") from exc
        await self._store.manifest.delete_segment(sample_segment.segment_id)
        if copied == 0 or sample_size <= 0:
            return plan
        v2_row_bytes = sample_size / copied
        if budget is None:
            # Unbegrenztes Budget (#968, Codex :175): kein Cutoff – alle Zeilen bleiben –,
            # aber ``copy_bytes_estimate`` auf die reale v2-Größe heben. Sonst nutzten
            # beide Disk-Checks die zu kleine v1-Schätzung aus ``plan()`` und der Job
            # könnte mid-copy die Platte füllen, statt sauber "not enough space" zu melden.
            return replace(plan, copy_bytes_estimate=int(plan.rows_to_copy * v2_row_bytes))
        target_volume = await self._target_copy_volume(budget)
        rows_to_copy = min(plan.total_rows, int(target_volume / v2_row_bytes))
        # Cutoff wie in ``plan()`` über die id-Ordnung ableiten (#968, Codex :156).
        cutoff_rowid = await self._resolve_cutoff_rowid(legacy, rows_to_copy, plan.max_rowid)
        return replace(
            plan,
            rows_to_copy=rows_to_copy,
            cutoff_rowid=cutoff_rowid,
            copy_bytes_estimate=int(rows_to_copy * v2_row_bytes),
        )

    async def _target_copy_volume(self, budget: int) -> int:
        """Ziel-Kopiervolumen: Budget minus Headroom minus LIVE-Bestand (#965).

        Der bereits vorhandene Live-Bestand (nicht-Legacy-, nicht-``migrating``-
        Segmente) belegt seinen Budget-Anteil nach dem Commit weiter – Zeilen
        darüber hinaus zu kopieren wäre verschwendete Arbeit, die die
        Post-Commit-Retention sofort wieder trimmt. Konsequenz für Admins
        (dokumentiert in Wizard + RELEASENOTES): nach einer Budget-Erhöhung
        zeitnah migrieren – wachsender Live-Bestand verdrängt 1:1 Alt-Events.
        """
        headroom = self._store._segment_config.segment_max_bytes or 0
        segments = await self._store.manifest.list_segments()
        # Schema-basiert (#968, Codex :289): ein quarantäniertes Legacy hat status != 'legacy'
        # (z. B. 'quarantined'), aber schema_version <= LEGACY_SCHEMA_VERSION. Der reine status-
        # Filter zählte es fälschlich als Live-Bestand, sodass ``target_volume`` zu klein wurde und
        # mehr migrierbare Alt-Zeilen droppte als das Budget verlangt. Retention-Guard und Status-
        # Endpoint schließen ALLE schema-legacy Quellen aus – hier ebenso.
        live_bytes = sum(s.size_bytes for s in segments if s.schema_version > LEGACY_SCHEMA_VERSION and s.status != SEGMENT_STATUS_MIGRATING)
        return max(0, budget - headroom - live_bytes)

    # ------------------------------------------------------------------
    # Job
    # ------------------------------------------------------------------

    async def run(self, progress: dict[str, Any]) -> dict[str, Any]:
        """Precheck → Copy-Phase → atomarer Commit. Mutiert ``progress`` live."""
        progress.update(phase="precheck", error=None)
        # Einen IN-PROCESS unterbrochenen Commit ZUERST reconcilen (#968, Codex :255):
        # schlug ``commit_offline_migration`` NACH ``_unlink_legacy_files`` fehl (Legacy-
        # Datei weg, Manifest-Zeile noch da), sind die ``migrating``-Segmente die EINZIGE
        # verbliebene Kopie. Ohne diesen Aufruf löschte das folgende
        # ``_discard_migrating_segments`` sie und machte aus einem recoverbaren Commit
        # permanenten Verlust der Alt-Historie. Der Reconciler promotet genau diesen Fall
        # (Legacy-Zeile mit fehlender Datei) und lässt nur echte Copy-Phase-Reste stehen.
        if await reconcile_offline_migration(self._store):
            # Der Reconciler hat einen unterbrochenen Commit VOLLENDET (Kopien promotet,
            # Legacy detached) – die Migration ist fertig (#968, Codex :277). NICHT
            # weiterplanen (es gibt keine Quelle mehr; ``plan()`` meldete sonst ``failed``
            # und der Aufrufer persistierte nie ``migrated``). Als ``done`` melden, damit
            # das Post-Commit-Bookkeeping (``on_success``) im Aufrufer läuft.
            progress.update(phase="done", copied_rows=progress.get("copied_rows", 0), dropped_rows=0, error=None)
            return progress
        # Stale ``migrating``-Reste einer frueher abgebrochenen Copy-Phase verwerfen
        # (#968, Codex :233), BEVOR ``plan()`` den freien Platz prueft – sonst zaehlte der
        # Precheck genau die Dateien mit, die dieser Lauf ohnehin loescht, und ein Retry
        # scheiterte grundlos an "not enough free disk space".
        await self._discard_migrating_segments()

        plan = await self.plan()
        progress.update(
            phase="copying",
            total_rows=plan.rows_to_copy,
            copied_rows=0,
            copied_bytes=0,
            dropped_rows=plan.total_rows - plan.rows_to_copy,
        )

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
        # Disk-Recheck NACH der Kalibrierung (#968, Codex :257): die reale v2-Groesse
        # kann das Kopiervolumen deutlich erhoeht haben.
        self._check_disk_free(plan.copy_bytes_estimate)
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
            # Marker DIREKT nach dem Unlink (#968, Codex :1356): ab hier ist die Legacy-Quelle weg
            # und die (noch unsichtbaren) migrating-Segmente sind die EINZIGE verbliebene Kopie.
            # Scheitert ``commit_offline_migration`` jetzt, darf der Failure-Handler den Retention-
            # Schutz NICHT auf den keep-Vorzustand zurückrollen – sonst löschte die nächste
            # Retention die missing-legacy-Row als ungeschütztes Opfer und der Reconciler verwürfe
            # die Kopien als orphan. Der Zustand ist recoverbar (Reconciler promotet ihn).
            progress["legacy_unlinked"] = True
            await self._store.manifest.commit_offline_migration([plan.legacy_segment_id])
        # Marker DIREKT nach dem Commit (#968, Codex :1239): ab hier ist die Migration
        # terminal. Wird der Job danach gecancelt (Shutdown während der Post-Commit-
        # Retention), muss der ``_run``-Wrapper das am Marker erkennen und dem
        # Post-Commit-Bookkeeping-Pfad folgen statt ``failed`` zu melden – ein CancelledError
        # (BaseException) wird von den ``except Exception``-Pfaden nicht gefangen.
        progress["committed"] = True
        # AB HIER ist der destruktive Commit durch (Legacy weg, Kopien promotet) – die
        # Migration ist terminal. Das Retention-Nachziehen (Ränder trimmen, Cutoff ist eine
        # Schätzung) ist reine Aufräumarbeit; ein Fehler darf die committete Migration NICHT
        # als ``failed`` melden (#968, Codex :323): ``_run()`` finge ihn sonst, rollte den
        # Schutz zurück und überspränge ``on_success`` – die Entscheidung bliebe nicht-
        # terminal, obwohl keine Quelle mehr zum Retry existiert. Best-effort, nur loggen.
        try:
            await self._store.enforce_retention()
        except Exception:
            logger.exception("RingBuffer: Post-Commit-Retention der Migration fehlgeschlagen (Migration ist dennoch committed)")
        progress.update(phase="done")
        return progress

    async def _copy_phase(self, plan: MigrationPlan, legacy: SegmentRecord, progress: dict[str, Any]) -> None:
        source = await self._store._connection_for_read(legacy)
        if source is None:
            raise OfflineMigrationError("legacy source became unreadable during migration")
        segment_max_bytes = self._store._segment_config.segment_max_bytes
        segment_max_rows = self._store._segment_config.segment_max_rows
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
            # Kalibrierte reale v2-Zeilengröße für die Byte-Cap-Begrenzung des Batches.
            est_row_bytes = (plan.copy_bytes_estimate / plan.rows_to_copy) if plan.rows_to_copy > 0 else 0.0
            while True:
                # Batch am Row-Cap deckeln, damit ein legacy-DB mit vielen kleinen
                # Zeilen kein Segment weit ueber ``segment_max_rows`` fuellt (#968,
                # Codex :341). Bei offenem Segment nur die Restkapazitaet, sonst ein
                # volles Batch (das naechste Segment startet leer).
                batch_limit = COPY_BATCH_ROWS
                if segment_max_rows is not None:
                    capacity = segment_max_rows if target_conn is None else segment_max_rows - seg_rows
                    batch_limit = min(batch_limit, max(1, capacity))
                if segment_max_bytes is not None and est_row_bytes > 0:
                    # Batch auch am Byte-Cap deckeln (#968, Codex :334): sonst schriebe ein
                    # voller Batch ein Segment mit großen JSON/Metadaten-Werten weit über
                    # den (evtl. kleinen) ``segment_max_bytes``, bevor der Rollover-Check
                    # nach dem Batch greift. Der size-Check unten bleibt als exakte Sicherung.
                    used_bytes = seg_rows * est_row_bytes if target_conn is not None else 0
                    byte_capacity_rows = max(1, int((segment_max_bytes - used_bytes) / est_row_bytes))
                    batch_limit = min(batch_limit, byte_capacity_rows)
                batch_limit = max(1, batch_limit)
                async with source.execute(
                    f"SELECT id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, {metadata_select} "
                    "FROM ringbuffer WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (cursor_rowid, batch_limit),
                ) as cur:
                    rows = await cur.fetchall()
                if not rows:
                    break
                if target_conn is None:
                    seg_index += 1
                    target_filename = f"{MIGRATED_FILENAME_PREFIX}{_utc_now_compact()}_{seg_index:03d}.sqlite"
                    # Segment der kopierten Quelle zuordnen (#968, Codex :354), damit der Commit
                    # bei mehreren Quellen nur diese Kopien promotet.
                    target_segment = await self._store.manifest.create_migrating_segment(
                        filename=target_filename, schema_version=SEGMENT_SCHEMA_VERSION, legacy_source_id=legacy.segment_id
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
                # Segment-Rollover am Größen- ODER Zeilen-Cap (Parität zum Live-Store
                # ``_segment_rotation_due``): Stats finalisieren, Datei schließen.
                bytes_full = segment_max_bytes is not None and size_now >= segment_max_bytes
                rows_full = segment_max_rows is not None and seg_rows >= segment_max_rows
                if bytes_full or rows_full:
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
            for candidate in (Path(f"{base}-wal"), Path(f"{base}-shm")):
                with contextlib.suppress(OSError):
                    candidate.unlink()
            # Die Manifest-Zeile NUR entfernen, wenn die Hauptdatei wirklich weg ist
            # (#968, Codex :442, analog zum Kalibrierungs-Sample :210): bleibt sie liegen
            # (Permission/IO) und die Zeile würde trotzdem gelöscht, wäre es eine untracked
            # ``rb_migrated_*.sqlite`` – aus /stats, Retention und Retry-Cleanup verschwunden,
            # dauerhaft Platz belegend und künftige Prechecks scheiternd. Fehler surfacen
            # (Migration bricht sauber ab); die Zeile bleibt für den nächsten Cleanup.
            try:
                base.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise OfflineMigrationError(f"could not remove stale migrating segment {segment.filename}: {exc}") from exc
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
    """Entfernt Legacy-Haupt-DB (Fehler PROPAGIERT), WAL/SHM + Attach-Sidecar (best-effort).

    Die Haupt-DB MUSS weg sein, BEVOR der aufrufende Commit die Legacy-Manifest-Zeile
    entfernt (#968, Codex :410): bleibt sie liegen (Permission/Lock), attached der
    naechste Startup sie als NEUES Legacy-Segment neben den promoteten migrierten
    Segmenten – migrierte Zeilen erschienen doppelt und die budget-bedingt gedroppten
    Alt-Zeilen kaemen zurueck. Ein Unlink-Fehler der Haupt-DB propagiert daher, sodass
    der Commit (``commit_offline_migration``) NICHT laeuft und die Legacy autoritativ
    bleibt. Die Sidecars sind unkritisch und bleiben best-effort.
    """
    legacy_path.unlink(missing_ok=True)
    for candidate in (
        Path(f"{legacy_path}-wal"),
        Path(f"{legacy_path}-shm"),
        legacy_path.with_name(f"{legacy_path.name}.attach_identity"),
    ):
        with contextlib.suppress(OSError):
            candidate.unlink()


async def reconcile_offline_migration(store: SqliteSegmentStore) -> bool:
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

    Rückgabe: ``True`` NUR, wenn ein unterbrochener Commit vollendet wurde
    (Fall 1). Der Job-Pfad (#968, Codex :277) meldet dann ``done`` statt weiter zu
    planen – die Migration ist fertig, es gibt keine Quelle mehr.
    """
    migrating = await store.manifest.list_migrating_segments()
    # Fehlgeschlagene ``discard``-Reste zu Ende verwerfen (#968, Codex :1148): eine ``discarding``-
    # Zeile ist ein im discard unterbrochener Zustand, KEIN Migrations-Commit – Datei (falls noch da)
    # + Sidecars + Manifest-Zeile entfernen, damit sie nicht dauerhaft bleibt und der schema-legacy-
    # Filter unten sie nicht als unterbrochenen Commit fehldeutet.
    for row in await store.manifest.list_discarding_segments():
        rbase = Path(row.filename)
        for candidate in (Path(f"{rbase}-wal"), Path(f"{rbase}-shm"), rbase.with_name(f"{rbase.name}.attach_identity")):
            with contextlib.suppress(OSError):
                candidate.unlink()
        with contextlib.suppress(OSError):
            rbase.unlink()
        # Manifest-Zeile NUR entfernen, wenn die Haupt-DB wirklich weg ist (#968, Codex :621): bleibt
        # sie liegen (Permission/Lock) und die Zeile würde trotzdem gelöscht, attached der nächste
        # Start die vermeintlich verworfene Legacy-DB wieder – und die discard-Retry-Garantie wäre
        # umgangen. Andernfalls die ``discarding``-Zeile für einen weiteren Retry behalten.
        if not rbase.exists():
            with contextlib.suppress(Exception):
                await store.manifest.delete_segment(row.segment_id)
    # Schema-basiert (#968, Codex :583), ``discarding``-Reste ausgeschlossen (#968, Codex :1148):
    # wird eine Legacy-Quelle vor dem Commit-Crash quarantäniert (status != 'legacy', aber schema-
    # legacy), verpasste der reine status-Filter die fehlende Quelle und verwürfe die Kopien.
    legacy_rows = await store.manifest.list_schema_legacy_segments()
    missing_file_rows = [s for s in legacy_rows if not Path(s.filename).exists()]
    # Fall 1 – unterbrochener Commit (Legacy-Datei unlinkt, Manifest-Delete fehlte noch).
    # ZUERST prüfen, damit auch drop-only/ZERO-COPY-Migrationen erkannt werden (#968,
    # Codex :528): ``rows_to_copy == 0`` legt gar keine ``migrating``-Segmente an, unlinkt
    # aber die Legacy-DB. Stirbt der Prozess vor dem Manifest-Delete, gibt es eine
    # Legacy-Zeile mit fehlender Datei UND keine migrating-Segmente – der frühere
    # ``if not migrating: return`` überspränge das für immer (Zeile auf fehlende Datei,
    # Retries unlesbar, ``migrated`` nie persistiert). Nur die fehlenden Zeilen detachen;
    # etwaige migrating-Kopien werden mit-promotet (bei mehreren Quellen fehlt nur die
    # gerade migrierte Datei – #968, Codex :496 – die anderen bleiben unangetastet).
    if missing_file_rows:
        logger.info(
            "RingBuffer: unterbrochenen Offline-Migrations-Commit vollenden (%d migrating-Segmente, %d fehlende Quelle(n))",
            len(migrating),
            len(missing_file_rows),
        )
        # Sidecars (-wal/-shm/attach_identity) jeder fehlenden Quelle mit-aufräumen (#968, Codex
        # :594): starb der Prozess NACH dem Unlink der Haupt-DB, aber VOR den Sidecars, blieben
        # potenziell sehr große dirty-WAL-Dateien liegen – nach dem Detach nicht mehr in
        # stats/Retention sichtbar (untracked Leak). Die Haupt-DB fehlt bereits (``missing_ok``),
        # der Unlink propagiert hier also nicht.
        for row in missing_file_rows:
            _unlink_legacy_files(Path(row.filename))
        # ``promote_unscoped=True`` (#968, Codex :369/:378): ein Alt-Manifest-Commit hinterließ
        # migrating-Kopien OHNE ``legacy_source_id``. Werden sie hier nicht mit-promotet, löscht das
        # folgende Detach die einzige Quelle, die den unterbrochenen Commit identifizierte, und die
        # bereits unlinkte Historie ginge verloren.
        await store.manifest.commit_offline_migration([s.segment_id for s in missing_file_rows], promote_unscoped=True)
        return True
    if not migrating:
        return False
    if not legacy_rows:
        logger.warning("RingBuffer: %d verwaiste Offline-Migrations-Segmente ohne Legacy-Quelle – werden verworfen", len(migrating))
        for segment in migrating:
            base = store._segments_dir / segment.filename
            for candidate in (Path(f"{base}-wal"), Path(f"{base}-shm")):
                with contextlib.suppress(OSError):
                    candidate.unlink()
            # Manifest-Zeile NUR entfernen, wenn die Hauptdatei wirklich weg ist (#968,
            # Codex :538, analog :442/:210): bleibt sie liegen (Permission/Lock) und die
            # Zeile würde trotzdem gelöscht, wäre es eine untracked ``rb_migrated_*.sqlite``
            # (aus /stats/Retention/Cleanup verschwunden, dauerhafter Leak). Im Startup-
            # Reconciler NICHT raisen (der Store muss öffnen), sondern die Zeile behalten –
            # der nächste Start versucht den Cleanup erneut.
            try:
                base.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning(
                    "RingBuffer: verwaistes migrating-Segment %s nicht entfernbar – Manifest-Zeile bleibt für spaeteren Cleanup", segment.filename
                )
                continue
            await store.manifest.delete_segment(segment.segment_id)
        return False
    # Copy-Phase-Crash: Legacy-Datei existiert noch → Reste bleiben unsichtbar
    # liegen; der nächste Job-Start räumt sie weg.
    logger.info("RingBuffer: %d unsichtbare Offline-Migrations-Segmente einer unterbrochenen Kopie gefunden", len(migrating))
    return False
