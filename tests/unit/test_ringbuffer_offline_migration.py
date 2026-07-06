"""Offline-Migration Phase 3 (#965): budget-gebunden, unsichtbar bis Commit, crash-fest.

Abgedeckt:

* **Happy Path + gid-Parität**: nach dem Commit sind die migrierten Zeilen unter
  EXAKT den synthetischen IDs sichtbar, unter denen sie vorher aus dem attachten
  Legacy-Segment gelesen wurden (stabile Client-Pagination über die Migration).
* **Budget-Cutoff**: nur die neuesten Zeilen, die ins Budget passen, werden
  kopiert; Älteres wird bewusst verworfen (vorgezogene FIFO-Retention).
* **Invariante Unsichtbarkeit**: während der Copy-Phase liefert die Query
  KEINE Duplikate (Kopien unsichtbar, Legacy autoritativ) und das aktive
  Segment bleibt unberührt.
* **Crash-Injection an den Phasengrenzen** (Copy-Crash, Commit-Fenster,
  verwaiste Kopie) über den Startup-Reconciler.
* **Parallel-Append**: Live-``record()`` zwischen Copy-Batches – Ordnung und
  Vollständigkeit bleiben korrekt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.offline_migration import (
    OfflineLegacyMigrator,
    OfflineMigrationError,
    reconcile_offline_migration,
)


def _iso(i: int) -> str:
    return f"2026-01-01T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}.000Z"


async def _seed_legacy_db(path: Path, count: int) -> None:
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i in range(count):
            await rb.record(
                ts=_iso(i),
                datapoint_id="dp-leg",
                topic="dp/dp-leg/value",
                old_value=None,
                new_value=100 + i,
                source_adapter="api",
                quality="good",
            )
    finally:
        await rb.stop()


def _segmented_rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        max_entries=None,
        segmented=True,
        legacy_retention_protected=True,
        **kwargs,
    )


async def _record_live(rb: RingBuffer, value: int, second: int) -> None:
    await rb.record(
        ts=_iso(second),
        datapoint_id="dp-live",
        topic="dp/dp-live/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


async def test_full_migration_preserves_ids_and_history(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 6)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        # Referenz: IDs, unter denen Clients die Legacy-Zeilen VOR der Migration sehen.
        before = await rb._store.query(StoreQuery(limit=50, sort_field="id", sort_order="desc"))
        ids_before = {r["new_value"]: r["global_event_id"] for r in before}

        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        progress: dict = {}
        await migrator.run(progress)
        assert progress["phase"] == "done"
        assert progress["copied_rows"] == 6
        assert progress["dropped_rows"] == 0

        # Legacy-Dateien physisch weg, keine Legacy-/migrating-Zeilen mehr.
        assert not legacy.exists()
        assert await rb._store.manifest.list_legacy_segments() == []
        assert await rb._store.manifest.list_migrating_segments() == []

        # Vollständigkeit + gid-Parität: identische IDs wie vor der Migration.
        after = await rb._store.query(StoreQuery(limit=50, sort_field="id", sort_order="desc"))
        ids_after = {r["new_value"]: r["global_event_id"] for r in after}
        assert ids_after == ids_before
        assert {r["new_value"] for r in after} == {1, 100, 101, 102, 103, 104, 105}
        # Migrierte Zeilen strikt negativ, Live-Zeile positiv, Ordnung id desc korrekt.
        assert ids_after[1] > 0
        assert all(ids_after[100 + i] < 0 for i in range(6))
        values_desc = [r["new_value"] for r in after]
        assert values_desc[0] == 1, "Live-Zeile (positive gid) sortiert vor allen migrierten"
    finally:
        await rb.stop()


async def test_budget_bounded_cutoff_drops_oldest(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    # Groß genug, dass die Nutzdaten den SQLite-Datei-Overhead pro Segment klar
    # dominieren – sonst frisst der Fixkosten-Anteil das kleine Test-Budget auf.
    total = 3000
    await _seed_legacy_db(legacy, total)
    legacy_size = legacy.stat().st_size

    # Budget ≈ halbe Legacy-Größe; segment_max_bytes wird auto-abgeleitet (Budget/3),
    # sodass die 3-Segment-Regel hält und das Headroom klein bleibt.
    rb = _segmented_rb(tmp_path, max_file_size_bytes=legacy_size // 2)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        progress: dict = {}
        await migrator.run(progress)
        assert progress["phase"] == "done"
        assert 0 < progress["copied_rows"] < total, "Cutoff muss greifen"
        assert progress["dropped_rows"] == total - progress["copied_rows"]

        rows = await rb._store.query(StoreQuery(limit=total + 10, sort_field="id", sort_order="asc"))
        migrated_values = [r["new_value"] for r in rows if r["global_event_id"] < 0]
        # Es überleben die NEUESTEN Legacy-Zeilen (höchste Werte) als lückenloses
        # Suffix. Der Post-Commit-Retention-Pass darf am ÄLTEREN Rand weiter trimmen
        # (der Cutoff schätzt über die mittlere Zeilengröße) – nie am neuen.
        assert 0 < len(migrated_values) <= progress["copied_rows"]
        assert migrated_values == list(range(100 + total - len(migrated_values), 100 + total))
    finally:
        await rb.stop()


async def test_copies_invisible_until_commit_and_active_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 5)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await migrator.plan()
        legacy_seg = (await rb._store.manifest.list_legacy_segments())[0]
        progress: dict = {"copied_rows": 0}
        await migrator._copy_phase(plan, legacy_seg, progress)

        # Kopien existieren als ``migrating`` …
        migrating = await rb._store.manifest.list_migrating_segments()
        assert migrating, "Copy-Phase muss migrating-Segmente anlegen"
        # … sind aber unsichtbar: Query liefert jede Legacy-Zeile GENAU einmal.
        rows = await rb._store.query(StoreQuery(limit=50))
        values = [r["new_value"] for r in rows]
        assert sorted(values) == [1, 100, 101, 102, 103, 104], f"Duplikate/Fehlende: {values}"
        # Aktives Segment hält ausschließlich die Live-Zeile (nie migrierte Zeilen).
        active = await rb._store.manifest.get_active_segment()
        assert active is not None
        assert all(s.segment_id != active.segment_id for s in migrating)
    finally:
        await rb.stop()


async def test_reconciler_completes_commit_window_crash(tmp_path: Path):
    """Crash NACH Legacy-Unlink, VOR Manifest-Commit → Reconciler vollendet."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 4)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await migrator.plan()
        legacy_seg = (await rb._store.manifest.list_legacy_segments())[0]
        await migrator._copy_phase(plan, legacy_seg, {"copied_rows": 0})
        # Simulierter Crash im Commit-Fenster: Dateien weg, Manifest-Txn fehlt.
        for candidate in (legacy, Path(f"{legacy}-wal"), Path(f"{legacy}-shm")):
            candidate.unlink(missing_ok=True)

        await reconcile_offline_migration(rb._store)

        assert await rb._store.manifest.list_legacy_segments() == []
        assert await rb._store.manifest.list_migrating_segments() == []
        rows = await rb._store.query(StoreQuery(limit=50))
        assert sorted(r["new_value"] for r in rows) == [1, 100, 101, 102, 103]
    finally:
        await rb.stop()


async def test_reconciler_keeps_copy_crash_state_and_job_restart_heals(tmp_path: Path):
    """Crash WÄHREND der Copy-Phase → Reste bleiben unsichtbar, Job-Neustart räumt auf."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 4)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await migrator.plan()
        legacy_seg = (await rb._store.manifest.list_legacy_segments())[0]
        await migrator._copy_phase(plan, legacy_seg, {"copied_rows": 0})
        leftover = await rb._store.manifest.list_migrating_segments()
        assert leftover

        # Reconciler (Startup): Legacy-Datei existiert → Vorzustand bleibt.
        await reconcile_offline_migration(rb._store)
        assert len(await rb._store.manifest.list_legacy_segments()) == 1
        assert len(await rb._store.manifest.list_migrating_segments()) == len(leftover)

        # Vollständiger Job-Neustart verwirft die Reste und migriert sauber.
        progress: dict = {}
        await migrator.run(progress)
        assert progress["phase"] == "done"
        rows = await rb._store.query(StoreQuery(limit=50))
        assert sorted(r["new_value"] for r in rows) == [1, 100, 101, 102, 103]
        # Keine doppelten gids (die Reste wurden verworfen, nicht doppelt promotet).
        gids = [r["global_event_id"] for r in rows]
        assert len(gids) == len(set(gids))
    finally:
        await rb.stop()


async def test_reconciler_discards_orphan_copies(tmp_path: Path):
    """``migrating``-Segmente OHNE Legacy-Zeile → verwerfen (nie partiell promoten)."""
    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        seg = await rb._store.manifest.create_migrating_segment(filename="rb_migrated_orphan.sqlite", schema_version=2)
        conn = await rb._store._open_segment_conn("rb_migrated_orphan.sqlite")
        await conn.close()

        await reconcile_offline_migration(rb._store)

        assert await rb._store.manifest.list_migrating_segments() == []
        assert not (rb._store._segments_dir / "rb_migrated_orphan.sqlite").exists()
        assert (await rb._store.manifest.get_segment(seg.segment_id)) is None
    finally:
        await rb.stop()


async def test_parallel_append_during_copy_stays_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Live-``record()`` zwischen den Copy-Batches: Ordnung + Vollständigkeit halten."""
    import obs.ringbuffer.store.offline_migration as mig_mod

    monkeypatch.setattr(mig_mod, "COPY_BATCH_ROWS", 2)

    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 6)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)

        # Deterministisches Interleaving: vor jedem Batch-Finalize ein Live-Append.
        original = OfflineLegacyMigrator._finalize_target
        live_counter = {"n": 1}

        async def _finalize_with_live_append(self, *args, **kwargs):
            live_counter["n"] += 1
            await _record_live(rb, live_counter["n"], 7200 + live_counter["n"])
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(OfflineLegacyMigrator, "_finalize_target", _finalize_with_live_append)

        progress: dict = {}
        await migrator.run(progress)
        assert progress["phase"] == "done"

        rows = await rb._store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
        values = [r["new_value"] for r in rows]
        live_values = [v for v in values if v < 100]
        migrated_values = [v for v in values if v >= 100]
        # Alle Live-Zeilen (positive gids) VOR allen migrierten (negative gids).
        assert values[: len(live_values)] == live_values
        assert sorted(migrated_values, reverse=True) == migrated_values
        assert set(migrated_values) == {100, 101, 102, 103, 104, 105}
        gids = [r["global_event_id"] for r in rows]
        assert len(gids) == len(set(gids))
    finally:
        await rb.stop()


async def test_latest_page_after_migration_shows_live_rows_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kleine latest-Page NACH Migration + weiteren Appends zeigt Live-Zeilen zuerst (#965-Fix).

    Migrierte Segmente tragen HÖHERE segment_ids als ältere Live-Segmente, aber
    NEGATIVE gids – die segment_id-DESC-Iterationsannahme des ``id desc``-
    Frühabbruchs gilt für sie nicht. Ohne Gegenmaßnahme füllte eine kleine
    latest-Page ihre Zeilen aus den migrierten Alt-Daten und terminierte, bevor
    ältere Live-Segmente gelesen wurden → frische Events unsichtbar (Feldbefund
    aus dem Demo-Betrieb). Der Reader muss den Frühabbruch sperren, sobald ein
    gelesenes Segment negative gids liefert (R42-Mechanismus).
    """
    import obs.ringbuffer.store.offline_migration as mig_mod

    monkeypatch.setattr(mig_mod, "COPY_BATCH_ROWS", 3)

    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 8)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        # Live-Zeile VOR der Migration + Rotation → liegt in einem Segment mit
        # NIEDRIGERER segment_id als die späteren migrierten Segmente.
        await _record_live(rb, 1, 7200)
        await rb._store.rotate()

        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        progress: dict = {}
        await migrator.run(progress)
        assert progress["phase"] == "done"

        # Weitere Live-Zeile nach der Migration (aktives Segment).
        await _record_live(rb, 2, 7300)

        # Kleine latest-Page: MUSS die beiden Live-Zeilen zuerst liefern.
        rows = await rb._store.query(StoreQuery(limit=2, sort_field="id", sort_order="desc"))
        assert [r["new_value"] for r in rows] == [2, 1], f"Live-Zeilen verdeckt: {[r['new_value'] for r in rows]}"
        assert all(r["global_event_id"] > 0 for r in rows)
    finally:
        await rb.stop()


async def test_run_requires_attached_legacy(tmp_path: Path):
    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        migrator = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        with pytest.raises(OfflineMigrationError):
            await migrator.plan()
    finally:
        await rb.stop()


async def test_start_legacy_migration_task_and_callback(tmp_path: Path):
    """RingBuffer-Job-API: Task läuft, Callback nach Erfolg, kein Doppelstart."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, 3)

    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        await _record_live(rb, 1, 7200)
        called = {"n": 0}

        async def _on_success():
            called["n"] += 1

        await rb.start_legacy_migration(on_success=_on_success)
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        assert called["n"] == 1
        assert not legacy.exists()
        # Schutz nach erfolgreichem Commit aufgehoben.
        assert rb._store._retention_config.protect_legacy is False
        # Ohne Legacy-Quelle ist ein weiterer Start ein Fehler.
        with pytest.raises(OfflineMigrationError):
            await rb.start_legacy_migration()
    finally:
        await rb.stop()
