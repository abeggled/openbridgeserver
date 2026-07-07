"""Codex-Runde #968 (10 Findings am Migrations-Assistenten, #964/#965).

Deckt die zehn Review-Findings der ersten Feature-Review ab: migrating-Status ×
Retention/Guard, Datei-Op-Fehler-Rollback, Overview-/keep-Konsistenz, Disk-
Precheck-Timing, Eskalations-Prognose-Pfade und den Job-Race.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import asyncio
import dataclasses

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION
from obs.ringbuffer.store.offline_migration import OfflineLegacyMigrator, OfflineMigrationError
from obs.ringbuffer.store.sqlite_backend import SEGMENT_SCHEMA_VERSION, SqliteSegmentStore


def _iso(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


def _event(v: int, ts: str) -> StoreEvent:
    return StoreEvent(ts=ts, datapoint_id="dp-1", topic="dp/dp-1/value", old_value=None, new_value=v, source_adapter="api", quality="good")


async def _seed_legacy(path: Path, values: list[int]) -> None:
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, v in enumerate(values):
            await rb.record(
                ts=_iso(i), datapoint_id="dp-leg", topic="dp/dp-leg/value", old_value=None, new_value=v, source_adapter="api", quality="good"
            )
    finally:
        await rb.stop()


def _seg_rb(tmp_path: Path, **kw) -> RingBuffer:
    return RingBuffer(storage="file", disk_path=str(tmp_path / "obs_ringbuffer.db"), max_entries=None, segmented=True, **kw)


# ---------- #10 + #5: migrating-Status aus Retention-Totals + Guard ----------


async def test_migrating_segments_excluded_from_totals_and_guard(tmp_path: Path):
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, _iso(0))])
        await store.rotate()
        await store.append([_event(2, _iso(1))])
        base_size = await store._total_size_bytes()
        base_rows = await store._total_row_count()
        assert await store._has_nonlegacy_data_segment() is True

        # Ein unsichtbares migrating-Segment mit Zeilen anlegen.
        seg = await store.manifest.create_migrating_segment(filename="rb_migrated_x.sqlite", schema_version=2)
        conn = await store._open_segment_conn("rb_migrated_x.sqlite")
        await store._insert_event(conn, -5, _event(99, _iso(2)))
        await conn.commit()
        await conn.close()
        await store.manifest.update_segment_stats(seg.segment_id, row_count=1, size_bytes=99999, from_ts=_iso(2), to_ts=_iso(2))

        # Totals + Guard ignorieren das migrating-Segment.
        assert await store._total_size_bytes() == base_size, "migrating-Bytes zaehlen nicht ins Budget"
        assert await store._total_row_count() == base_rows, "migrating-Rows zaehlen nicht ins Budget"

        # Guard: nur das migrating-Segment mit Zeilen (kein sichtbares) -> False.
        store2 = SqliteSegmentStore(tmp_path / "root2")
        await store2.open()
        try:
            s2 = await store2.manifest.create_migrating_segment(filename="rb_migrated_y.sqlite", schema_version=2)
            await store2.manifest.update_segment_stats(s2.segment_id, row_count=3, size_bytes=1, from_ts=_iso(0), to_ts=_iso(0))
            assert await store2._has_nonlegacy_data_segment() is False, "migrating zaehlt nicht als lesbare Historie"
        finally:
            await store2.close()
    finally:
        await store.close()


# ---------- #2: Legacy-Unlink-Fehler vor Commit propagieren ----------


async def test_unlink_legacy_files_propagates_main_db_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from obs.ringbuffer.store import offline_migration as mig

    legacy = tmp_path / "obs_ringbuffer.db"
    legacy.write_bytes(b"x")

    orig_unlink = Path.unlink

    def _boom(self, *a, **k):
        if self.name == "obs_ringbuffer.db":
            raise PermissionError("locked")
        return orig_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _boom)
    with pytest.raises(PermissionError):
        mig._unlink_legacy_files(legacy)


# ---------- #3: discard finalisiert nicht, wenn Haupt-DB bleibt ----------


async def test_discard_legacy_propagates_when_main_db_cannot_be_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [10, 11])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        orig_unlink = Path.unlink

        def _boom(self, *a, **k):
            if self.name == "obs_ringbuffer.db":
                raise PermissionError("locked")
            return orig_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(PermissionError):
            await rb.discard_legacy()
        # Manifest-Zeile bleibt (kein 'verschwundenes' Legacy), Datei existiert noch.
        assert [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]
        assert legacy.exists()
    finally:
        await rb.stop()


# ---------- #9: quarantäniertes Legacy bleibt im Overview sichtbar ----------


async def test_overview_shows_quarantined_legacy(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [10, 11])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        assert await rb.legacy_migration_overview() is not None
        for seg in [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]:
            await rb._store.manifest.mark_quarantined(seg.segment_id, "corrupt (Test)")
        assert await rb._store.manifest.list_legacy_segments() == []
        ov = await rb.legacy_migration_overview()
        assert ov is not None, "quarantaeniertes Legacy muss im Assistenten sichtbar bleiben"
        assert ov["status"] == "quarantined"
    finally:
        await rb.stop()


# ---------- #4: protect_legacy nach fehlgeschlagener Migration zurückrollen ----------


async def test_failed_migration_restores_previous_protection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [10, 11])
    # keep-Installation: Schutz ist AUS.
    rb = _seg_rb(tmp_path, legacy_retention_protected=False)
    await rb.start()
    try:
        assert rb._legacy_retention_protected is False

        async def _boom(progress):
            raise OfflineMigrationError("precheck failed")

        monkeypatch.setattr(OfflineLegacyMigrator, "run", lambda self, progress: _boom(progress))
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "failed"
        # Vorheriger (ungeschuetzter) Zustand ist wiederhergestellt.
        assert rb._legacy_retention_protected is False, "keep-Schutzzustand muss nach Fehlschlag zurueckgerollt sein"
        assert rb._store._retention_config.protect_legacy is False
    finally:
        await rb.stop()


# ---------- #6 + #7: Disk-Precheck ----------


async def test_disk_precheck_after_calibration_and_stale_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import shutil as _shutil

    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(200)))
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        await rb.record(ts=_iso(50), datapoint_id="dp-1", topic="dp/dp-1/value", old_value=None, new_value=1, source_adapter="api", quality="good")
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)

        # #7: eine stale migrating-Datei simulieren – der Precheck darf sie nicht mitzaehlen.
        stale = await rb._store.manifest.create_migrating_segment(filename="rb_migrated_stale.sqlite", schema_version=2)
        (rb._store._segments_dir / "rb_migrated_stale.sqlite").write_bytes(b"x" * 1024)

        # #6: Disk-Recheck nach Kalibrierung – freien Platz knapp UNTER den v2-Bedarf setzen.
        from collections import namedtuple

        DU = namedtuple("DU", "total used free")
        calls = {"n": 0}
        real = _shutil.disk_usage

        def _fake_du(p):
            calls["n"] += 1
            return DU(total=10**12, used=0, free=10**12)

        monkeypatch.setattr(_shutil, "disk_usage", _fake_du)
        progress: dict = {}
        await mig.run(progress)
        assert progress["phase"] == "done"
        # Der Disk-Check wurde mehrfach aufgerufen (plan + Recheck nach Kalibrierung).
        assert calls["n"] >= 2, "Disk-Check muss auch nach der Kalibrierung laufen"
        # Die stale Kopie wurde vor dem Lauf verworfen.
        assert await rb._store.manifest.get_segment(stale.segment_id) is None
        monkeypatch.setattr(_shutil, "disk_usage", real)
    finally:
        await rb.stop()


# ---------- #1: Eskalations-Prognose liest die korrekten stats-Pfade ----------


async def test_stats_exposes_over_budget_under_store_backend_extra(tmp_path: Path):
    """Der Eskalations-Fall (over-budget durch attachtes Legacy) liegt unter
    ``store.backend_extra.retention_over_budget`` und die Gesamt-Nutzung als
    Top-Level ``file_size_bytes`` – NICHT als Top-Level ``retention_over_budget``/
    ``size_bytes`` (#968, Codex :1999). Ohne die korrekten Pfade eskalierte der
    Banner nie."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(200)))
    # Schutz AN: das attachte Legacy bleibt erhalten und der Store bleibt ueber
    # Budget (retention_over_budget=True), statt es sofort per FIFO zu reclaimen.
    rb = _seg_rb(tmp_path, max_file_size_bytes=1, legacy_retention_protected=True)
    await rb.start()
    try:
        await rb.record(ts=_iso(50), datapoint_id="dp-1", topic="dp/dp-1/value", old_value=None, new_value=1, source_adapter="api", quality="good")
        stats = await rb.stats()
        # Korrekte Pfade (mein Fix liest genau diese):
        assert (stats["store"]["backend_extra"]).get("retention_over_budget") is True
        assert stats["file_size_bytes"] > 0
        # Die ALTEN (falschen) Top-Level-Pfade sind leer -> beweist den Bug:
        assert stats.get("retention_over_budget") is None
        assert stats.get("size_bytes") is None
    finally:
        await rb.stop()


# ---------- Runde 2, Om-l6: geschütztes Legacy zählt als Budget-Druck ----------


async def test_protected_legacy_counts_as_budget_pressure_with_live_data(tmp_path: Path):
    """Upgrade-Fall: großes geschütztes Legacy + kleines Live-Segment über Budget.

    Der No-Zero-History-Guard greift NICHT (Live-Segment hält Zeilen), aber
    ``protect_legacy=True`` macht das Legacy trotzdem unlöschbar. Ohne den Fix
    meldete ``/stats`` ``retention_over_budget=false`` und Dashboard/Config
    eskalierten nie (#968, Codex :2919). Das Budget wird realistisch gewählt
    (Live-Segment passt allein, Legacy sprengt) – NICHT 1 Byte, sonst maskiert
    schon das aktive Segment den Fehler."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(200)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        await rb.record(ts=_iso(50), datapoint_id="dp-1", topic="dp/dp-1/value", old_value=None, new_value=1, source_adapter="api", quality="good")
        assert await rb._store._has_nonlegacy_data_segment() is True, "Live-Segment ist non-legacy Historie"

        segs = await rb._store.manifest.list_segments()
        active = next(s for s in segs if s.status == "active")
        legacy_seg = next(s for s in segs if s.schema_version <= LEGACY_SCHEMA_VERSION)
        # Budget so, dass das Live-Segment allein passt, Legacy zusaetzlich sprengt.
        budget = active.size_bytes + legacy_seg.size_bytes // 2
        rb._store._retention_config = dataclasses.replace(rb._store._retention_config, max_file_size_bytes=budget)

        stats = await rb.stats()
        assert stats["store"]["backend_extra"]["retention_over_budget"] is True, "geschuetztes Legacy muss als Budget-Druck zaehlen"

        # Gegenprobe: ohne Schutz (keep) UND mit non-legacy data ist Legacy loeschbar -> kein Druck.
        rb._store._retention_config = dataclasses.replace(rb._store._retention_config, protect_legacy=False)
        stats2 = await rb.stats()
        assert stats2["store"]["backend_extra"]["retention_over_budget"] is False, "ungeschuetztes Legacy ist per FIFO reclaimbar"
    finally:
        await rb.stop()


# ---------- Runde 2, Om-lx: Row-Cap-Rotation während der Migration ----------


async def test_migration_honors_segment_max_rows(tmp_path: Path):
    """Ein Legacy-DB mit vielen kleinen Zeilen darf kein Segment weit über
    ``segment_max_rows`` erzeugen (#968, Codex :341). Byte-Cap greift bei großem
    Budget nicht – nur der Row-Cap teilt auf."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(2500)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, segment_max_rows=1000, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        progress: dict = {}
        await mig.run(progress)
        assert progress["phase"] == "done"
        assert progress["copied_rows"] == 2500, "grosses Budget -> kein Cutoff"

        v2_with_rows = [s for s in await rb._store.manifest.list_segments() if s.schema_version == SEGMENT_SCHEMA_VERSION and s.row_count > 0]
        assert max(s.row_count for s in v2_with_rows) <= 1000, "kein migriertes Segment ueber dem Row-Cap"
        # Die 2500 migrierten Zeilen wurden auf >=3 Segmente aufgeteilt (statt 1x2500).
        migrated = [s for s in v2_with_rows if s.row_count >= 1]
        assert len(migrated) >= 3, "Row-Cap muss die Migration in mehrere Segmente aufteilen"
    finally:
        await rb.stop()


# ---------- Runde 3, Codex :175: Kalibrierung auch ohne Budget ----------


async def test_calibration_updates_estimate_without_budget(tmp_path: Path):
    """Bei unbegrenztem Budget (``max_file_size_bytes=None``) muss die Kalibrierung
    trotzdem laufen und ``copy_bytes_estimate`` auf die reale v2-Größe heben (#968,
    Codex :175). Ohne den Fix nutzte der Disk-Precheck die zu kleine v1-Schätzung
    aus ``plan()`` und der Job könnte mid-copy die Platte füllen. Der Cutoff bleibt
    aus – alle Zeilen werden kopiert."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(300)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=None, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await mig.plan()
        legacy_seg = await mig._attached_legacy()
        v1_estimate = plan.copy_bytes_estimate
        calibrated = await mig._calibrate_cutoff(plan, legacy_seg)
        # Kein Cutoff bei unbegrenztem Budget: alle Zeilen bleiben.
        assert calibrated.rows_to_copy == plan.rows_to_copy
        assert calibrated.cutoff_rowid == plan.cutoff_rowid
        # Aber die Schätzung ist auf die reale (größere) v2-Zeilengröße angehoben.
        assert calibrated.copy_bytes_estimate > v1_estimate, "v2-Schätzung muss über der v1-Erstschätzung liegen"
    finally:
        await rb.stop()


# ---------- Runde 4, Codex :2819: migrating aus sichtbaren Zeilen-/Zeit-Stats ----------


async def test_migrating_excluded_from_visible_stats(tmp_path: Path):
    """``migrating``-Segmente sind vor Queries versteckt und dürfen die sichtbaren
    Zeilen-/Zeit-Aggregate (``total``/``oldest_ts``/``newest_ts``) nicht double-counten
    (#968, Codex :2819). ``size_bytes`` bleibt physisch (reale Plattennutzung)."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, _iso(1))])
        base = await store.stats()
        base_total = base.as_dict()["common"]["total"]

        seg = await store.manifest.create_migrating_segment(filename="rb_migrated_x.sqlite", schema_version=2)
        await store.manifest.update_segment_stats(
            seg.segment_id, row_count=500, size_bytes=4096, from_ts="2020-01-01T00:00:00.000Z", to_ts="2020-06-01T00:00:00.000Z"
        )

        after = (await store.stats()).as_dict()["common"]
        assert after["total"] == base_total, "migrating-Zeilen zaehlen nicht in die sichtbaren Stats"
        # Die uralte migrating-Zeitspanne verschiebt oldest/newest NICHT.
        assert after["oldest_ts"] != "2020-01-01T00:00:00.000Z"
        assert after["newest_ts"] != "2020-06-01T00:00:00.000Z"
        # size_bytes zaehlt die Kopie dennoch (physische Plattennutzung).
        assert after["size_bytes"] >= 4096
    finally:
        await store.close()


# ---------- Runde 4, Codex :2733: migrierte Chunks aus der Wachstumsprognose ----------


async def test_migrated_segments_excluded_from_prognosis(tmp_path: Path):
    """Offline migrierte ``rb_migrated_*``-Chunks (historische Zeitspannen) dürfen die
    Wachstumsprognose nicht verfälschen (#968, Codex :2733) – sonst schätzte die Rate
    aus jahre-alter Alt-Historie statt der aktuellen Schreibrate."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(400)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        await mig.run({})
        segs = await rb._store.manifest.list_segments()
        migrated = [s for s in segs if s.filename.startswith("rb_migrated_")]
        assert migrated, "Migration muss rb_migrated_-Segmente erzeugt haben"
        prog = rb._store._compute_prognosis(segs)
        # Kein migriertes Segment darf als Prognose-Sample zaehlen (alle sind entweder
        # aktiv/legacy oder migriert – keine echten Live-Rotationen).
        assert prog["sample_segment_count"] == 0, "migrierte Chunks duerfen die Rate nicht speisen"
        assert prog["bytes_per_hour"] is None
    finally:
        await rb.stop()


# ---------- Runde 4, Codex :255: interrupted commit nicht als Retry verwerfen ----------


async def test_run_reconciles_interrupted_commit_instead_of_discarding(tmp_path: Path):
    """Schlägt der Manifest-Commit NACH dem Legacy-Unlink fehl (Legacy-Datei weg, Zeile
    noch da), sind die ``migrating``-Segmente die einzige Kopie. Ein erneuter Lauf muss
    sie reconcilen (promoten), NICHT verwerfen (#968, Codex :255) – sonst permanenter
    Verlust der Alt-Historie."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(50)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)

        # Commit-Abbruch NACH dem Unlink simulieren: commit_offline_migration wirft einmal.
        orig_commit = rb._store.manifest.commit_offline_migration
        calls = {"n": 0}

        async def _boom_once(ids):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("manifest commit failed after unlink")
            return await orig_commit(ids)

        rb._store.manifest.commit_offline_migration = _boom_once
        with pytest.raises(OSError):
            await mig.run({})
        rb._store.manifest.commit_offline_migration = orig_commit

        # Zustand: Legacy-Datei weg, Legacy-Zeile + migrating-Segmente noch da.
        assert not legacy.exists()
        assert await rb._store.manifest.list_migrating_segments(), "migrating-Kopie muss noch existieren"

        # Erneuter Lauf: reconcilet den unterbrochenen Commit (promote) und meldet ``done``
        # statt zu verwerfen oder als ``failed`` abzubrechen (#968, Codex :277).
        migrated_before = {s.filename for s in await rb._store.manifest.list_migrating_segments()}
        progress2: dict = {}
        result = await mig.run(progress2)
        assert result["phase"] == "done", "vollendeter Reconcile muss als done gemeldet werden, nicht failed"
        # Die zuvor migrating-Segmente sind jetzt promoted (closed v2), nicht gelöscht.
        all_segs = await rb._store.manifest.list_segments()
        promoted = {s.filename for s in all_segs if s.status == "closed" and s.filename.startswith("rb_migrated_")}
        assert migrated_before <= promoted, "interrupted commit muss promotet, nicht verworfen werden"
        assert await rb._store.manifest.list_migrating_segments() == []
        # Die Alt-Historie ist sichtbar (negative gids), nichts verloren.
        from obs.ringbuffer.store.interface import StoreQuery

        rows = await rb._store.query(StoreQuery(limit=100))
        assert len([r for r in rows if r["global_event_id"] < 0]) > 0
    finally:
        await rb.stop()


# ---------- Runde 5, Codex :1066: discard räumt verwaiste migrating-Segmente ----------


async def test_discard_legacy_also_removes_orphaned_migrating_segments(tmp_path: Path):
    """``discard`` nach einer gescheiterten Migration muss auch die verwaisten
    ``migrating``-Kopien entfernen (#968, Codex :1066), nicht nur die Legacy-Zeilen –
    sonst belegen sie unsichtbar Platz bis zum nächsten Reconcile-Neustart."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        # Verwaiste migrating-Kopie einer gescheiterten Migration simulieren.
        seg = await rb._store.manifest.create_migrating_segment(filename="rb_migrated_orphan.sqlite", schema_version=2)
        (rb._store._segments_dir / "rb_migrated_orphan.sqlite").write_bytes(b"x" * 512)
        assert await rb._store.manifest.list_migrating_segments()

        await rb.discard_legacy()

        assert await rb._store.manifest.list_migrating_segments() == [], "migrating-Reste muessen mit verworfen werden"
        assert await rb._store.manifest.get_segment(seg.segment_id) is None
        assert not (rb._store._segments_dir / "rb_migrated_orphan.sqlite").exists()
    finally:
        await rb.stop()


# ---------- Runde 5, Codex :1110: quarantäniertes Legacy synchron ablehnen ----------


async def test_start_migration_rejects_quarantined_legacy_synchronously(tmp_path: Path):
    """Ist die einzige Legacy-Quelle quarantäniert (nach Read-Fehler), muss
    ``start_legacy_migration`` synchron abbrechen (#968, Codex :1110) statt einen Job
    zu melden, der im Hintergrund sofort scheitert."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        for seg in [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]:
            await rb._store.manifest.mark_quarantined(seg.segment_id, "corrupt (Test)")
        assert await rb._store.manifest.list_legacy_segments() == []
        with pytest.raises(OfflineMigrationError, match="quarantined|unreadable"):
            await rb.start_legacy_migration()
        # Kein Hintergrund-Task angelegt (synchroner Abbruch).
        assert rb._legacy_migration_task is None
    finally:
        await rb.stop()


# ---------- Runde 5, Codex :496: Reconcile pro fehlender Legacy-Quelle ----------


async def test_reconcile_completes_commit_for_single_missing_legacy_of_many(tmp_path: Path):
    """Bei mehreren registrierten Legacy-Quellen fehlt nach einem Crash nur die gerade
    migrierte Datei. Der Reconciler muss den Commit für DIESE Quelle vollenden (#968,
    Codex :496), statt zu verlangen, dass ALLE Legacy-Dateien fehlen."""
    from obs.ringbuffer.store.offline_migration import reconcile_offline_migration

    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        # Zwei Legacy-Quellen: eine mit vorhandener Datei, eine (migrierte) ohne Datei.
        present = tmp_path / "legacy_present.db"
        present.write_bytes(b"x" * 256)
        missing = tmp_path / "legacy_gone.db"  # existiert bewusst NICHT (unlinkt)
        present_row = await store.manifest.register_legacy_segment(source_path=str(present), size_bytes=256)
        missing_row = await store.manifest.register_legacy_segment(source_path=str(missing), size_bytes=256)

        # Kopie der migrierten (fehlenden) Quelle als migrating-Segment.
        mig = await store.manifest.create_migrating_segment(filename="rb_migrated_x.sqlite", schema_version=2)
        (store._segments_dir / "rb_migrated_x.sqlite").write_bytes(b"y" * 256)

        await reconcile_offline_migration(store)

        # Die fehlende Quelle ist detached, ihre Kopie promotet; die vorhandene bleibt.
        legacy_now = {s.segment_id for s in await store.manifest.list_legacy_segments()}
        assert missing_row.segment_id not in legacy_now, "fehlende Quelle muss detached werden"
        assert present_row.segment_id in legacy_now, "vorhandene Quelle bleibt unangetastet"
        assert await store.manifest.list_migrating_segments() == [], "Kopie muss promotet sein"
        promoted = await store.manifest.get_segment(mig.segment_id)
        assert promoted is not None and promoted.status != "migrating"
    finally:
        await store.close()


# ---------- Runde 6, Codex :1153: Post-Commit-Bookkeeping-Fehler bleibt terminal ----------


async def test_post_commit_bookkeeping_failure_keeps_migration_done(tmp_path: Path):
    """Schlägt das Post-Commit-Bookkeeping (``on_success``) fehl, NACHDEM der destruktive
    Commit durch ist (Legacy weg), darf die Migration nicht als ``failed`` gemeldet und
    der Schutz nicht zurückgerollt werden (#968, Codex :1153) – es gibt keine Quelle mehr
    zum Retry. ``phase`` bleibt ``done``."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(50)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=False)
    await rb.start()
    try:

        async def _boom_success():
            raise RuntimeError("app db locked")

        await rb.start_legacy_migration(on_success=_boom_success)
        await rb._legacy_migration_task
        # Commit ist durch (Legacy weg), trotz bookkeeping-Fehler terminal.
        assert rb.legacy_migration_progress()["phase"] == "done", "committed migration bleibt terminal, nicht failed"
        assert not legacy.exists()
        assert await rb._store.manifest.list_legacy_segments() == []
    finally:
        await rb.stop()


# ---------- Runde 6, Codex :210: Sample-Unlink-Fehler leakt nicht ----------


async def test_calibration_sample_unlink_failure_surfaces_and_keeps_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kann die Kalibrierungs-Sample-Datei nicht entfernt werden, muss der Fehler
    surfacen UND die Manifest-Zeile erhalten bleiben (#968, Codex :210) – sonst leakte
    eine untracked ``rb_migrated_sample_*.sqlite`` dauerhaft Platz."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(300)))
    # Unbegrenztes Budget -> alle Zeilen werden kopiert und die Kalibrierung samplet
    # (bei zu kleinem Budget bliebe rows_to_copy 0 und das Sample würde nie geschrieben).
    rb = _seg_rb(tmp_path, max_file_size_bytes=None, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await mig.plan()
        legacy_seg = await mig._attached_legacy()

        orig_unlink = Path.unlink

        def _boom(self, *a, **k):
            if self.name.startswith("rb_migrated_sample_") and self.name.endswith(".sqlite"):
                raise PermissionError("locked")
            return orig_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(OfflineMigrationError, match="calibration sample"):
            await mig._calibrate_cutoff(plan, legacy_seg)
        # Manifest-Zeile bleibt (als migrating registriert) für späteren Cleanup.
        assert await rb._store.manifest.list_migrating_segments(), "Sample-Zeile darf nicht verwaist geloescht werden"
    finally:
        await rb.stop()


# ---------- Runde 6, Codex :334: Byte-Cap-Batch-Split ohne Row-Cap ----------


async def test_migration_splits_at_byte_cap_without_row_cap(tmp_path: Path):
    """Ist nur ``segment_max_bytes`` (kein ``segment_max_rows``) gesetzt, darf ein Batch
    kein Segment weit über den Byte-Cap füllen (#968, Codex :334). Große Zeilenwerte +
    kleiner Byte-Cap müssen die Migration in mehrere Segmente splitten."""
    import dataclasses

    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    legacy = tmp_path / "obs_ringbuffer.db"
    # Legacy mit großen new_value-Werten (~1 KiB je Zeile).
    big = "x" * 1024
    src = _RB(storage="disk", disk_path=str(legacy), max_entries=None)
    await src.start()
    try:
        for i in range(300):
            await src.record(
                ts=_iso(i % 60),
                datapoint_id="dp-leg",
                topic="dp/dp-leg/value",
                old_value=None,
                new_value=f"{big}-{i}",
                source_adapter="api",
                quality="good",
            )
    finally:
        await src.stop()

    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        # Kleinen Byte-Cap OHNE Row-Cap erzwingen (unter dem 4-MiB-Min der Config, nur für den Test).
        cap = 64 * 1024
        rb._store._segment_config = dataclasses.replace(rb._store._segment_config, segment_max_bytes=cap, segment_max_rows=None)

        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        await mig.run({})

        v2 = [s for s in await rb._store.manifest.list_segments() if s.filename.startswith("rb_migrated_") and s.row_count > 0]
        assert len(v2) >= 2, "Byte-Cap muss die Migration in mehrere Segmente splitten"
        # Kein Segment liegt VIELFACH über dem Cap (ein voller 5000-Batch waere ~300 KiB = ~5x cap).
        assert max(s.size_bytes for s in v2) < cap * 3, "kein Segment darf den Byte-Cap massiv ueberschiessen"
    finally:
        await rb.stop()


# ---------- Runde 7, Codex :442: stale migrating-Unlink-Fehler leakt nicht ----------


async def test_discard_migrating_unlink_failure_surfaces_and_keeps_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kann eine stale ``migrating``-Datei nicht entfernt werden, muss der Fehler surfacen
    UND die Manifest-Zeile erhalten bleiben (#968, Codex :442, analog zum Sample :210) –
    sonst würde sie zur untracked ``rb_migrated_*.sqlite`` und leakte Platz."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        seg = await rb._store.manifest.create_migrating_segment(filename="rb_migrated_stale.sqlite", schema_version=2)
        (rb._store._segments_dir / "rb_migrated_stale.sqlite").write_bytes(b"x" * 256)
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)

        orig_unlink = Path.unlink

        def _boom(self, *a, **k):
            if self.name == "rb_migrated_stale.sqlite":
                raise PermissionError("locked")
            return orig_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(OfflineMigrationError, match="stale migrating"):
            await mig._discard_migrating_segments()
        assert await rb._store.manifest.get_segment(seg.segment_id) is not None, "Manifest-Zeile muss fuer spaeteren Cleanup bleiben"
    finally:
        await rb.stop()


# ---------- Runde 7, Codex :1126: gleichzeitige Migration-Starts serialisiert ----------


async def test_concurrent_migration_starts_are_serialized(tmp_path: Path):
    """Zwei fast-gleichzeitige ``start_legacy_migration`` dürfen nur EINEN Job starten
    (#968, Codex :1126); der zweite scheitert deterministisch, sonst racen zwei Migrator-
    Tasks gegen dieselbe Quelle."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(50)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        results = await asyncio.gather(rb.start_legacy_migration(), rb.start_legacy_migration(), return_exceptions=True)
        errors = [r for r in results if isinstance(r, OfflineMigrationError)]
        oks = [r for r in results if not isinstance(r, Exception)]
        assert len(errors) == 1, "genau ein Doppelstart muss abgelehnt werden"
        assert len(oks) == 1
        if rb._legacy_migration_task is not None:
            await rb._legacy_migration_task
    finally:
        await rb.stop()


# ---------- Runde 8, Codex :495: migrierte Chunks hinter Live-Segmente sortieren ----------


async def test_migrated_chunks_ordered_after_live_segments(tmp_path: Path):
    """Promotete ``rb_migrated_*``-Chunks tragen negative gids (Alt-Historie) und müssen
    in der Read-Ordnung ZULETZT kommen – wie der Legacy-Tail (#968, Codex :495), sonst
    scannt eine Latest-Page-Query nach großer Migration alle migrierten Chunks."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        # Live-Segmente (positive gids).
        await store.append([_event(1, _iso(0))])
        await store.rotate()
        await store.append([_event(2, _iso(1))])
        # Ein migrierter Chunk mit HOHER segment_id (nach den Live-Segmenten promotet).
        mig = await store.manifest.create_migrating_segment(filename="rb_migrated_x_001.sqlite", schema_version=2)
        await store.manifest.update_segment_stats(mig.segment_id, row_count=1, size_bytes=100, from_ts=_iso(0), to_ts=_iso(0))
        await store.manifest.commit_offline_migration([])  # migrating -> closed

        segs = await store.manifest.list_segments_for_query()
        names = [s.filename for s in segs]
        migrated_pos = [i for i, f in enumerate(names) if f.startswith("rb_migrated_")]
        live_pos = [i for i, f in enumerate(names) if not f.startswith("rb_migrated_")]
        assert migrated_pos and live_pos
        assert max(live_pos) < min(migrated_pos), "Live-Segmente müssen VOR den migrierten Chunks iteriert werden"
    finally:
        await store.close()


# ---------- Runde 8, Codex :2078: Entscheidung im Migrations-Startfenster blockieren ----------


async def test_migration_in_progress_covers_startup_reservation(tmp_path: Path):
    """``legacy_migration_in_progress`` deckt das Startfenster ab (#968, Codex :2078):
    zwischen der synchronen Reservierung und ``phase='starting'`` ist die Progress-Phase
    noch idle – eine parallele keep/discard-Entscheidung dürfte hier nicht durchgehen."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        assert rb.legacy_migration_in_progress() is False
        # Startfenster: Flag reserviert, Phase noch idle/failed.
        rb._legacy_migration_starting = True
        assert rb.legacy_migration_in_progress() is True, "Reservierungs-Flag muss als in-progress gelten"
        rb._legacy_migration_starting = False
        assert rb.legacy_migration_in_progress() is False
        # Auch eine aktive Phase zählt.
        rb._legacy_migration_progress = {"phase": "copying"}
        assert rb.legacy_migration_in_progress() is True
    finally:
        await rb.stop()


# ---------- Runde 9, Codex :1575: fehlende Legacy-DB im Checkpoint nicht neu anlegen ----------


async def test_checkpoint_missing_legacy_does_not_recreate_file(tmp_path: Path):
    """``_checkpoint_small_legacy`` darf eine fehlende Legacy-Hauptdatei NICHT neu anlegen
    (#968, Codex :1575): sonst erzeugte ein UI-Poll auf /stats zwischen Offline-Unlink und
    Manifest-Commit eine leere DB und der Reconciler sähe die fehlende Quelle nicht mehr."""
    missing = tmp_path / "gone_legacy.db"
    assert not missing.exists()
    result = await SqliteSegmentStore._checkpoint_small_legacy(missing)
    assert result is False, "kein Checkpoint moeglich ohne Quelldatei"
    assert not missing.exists(), "fehlende Legacy-DB darf nicht neu angelegt werden"


# ---------- Runde 11, Codex :2847: quarantänierte Segmente aus sichtbaren Stats ----------


async def test_quarantined_excluded_from_visible_stats(tmp_path: Path):
    """Quarantänierte Segmente sind aus Reads ausgeschlossen und dürfen die sichtbaren
    Zeilen-/Zeit-Aggregate nicht mitzählen (#968, Codex :2847) – sonst meldete /stats
    Zeilen/Zeitspannen, die kein Query liefern kann."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, _iso(1))])
        await store.rotate()
        await store.append([_event(2, _iso(2))])
        base_total = (await store.stats()).as_dict()["common"]["total"]
        # Ein geschlossenes Segment quarantänieren.
        closed = [s for s in await store.manifest.list_segments() if s.status == "closed"]
        assert closed
        await store.manifest.mark_quarantined(closed[0].segment_id, "corrupt (Test)")

        after = (await store.stats()).as_dict()["common"]
        assert after["total"] == base_total - closed[0].row_count, "quarantänierte Zeilen zaehlen nicht in die sichtbaren Stats"
    finally:
        await store.close()


# ---------- Runde 11, Codex :538: verwaistes migrating bei Unlink-Fehler behalten ----------


async def test_reconcile_orphan_keeps_row_when_unlink_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verwaiste ``migrating``-Segmente (ohne Legacy-Quelle), deren Datei nicht entfernbar
    ist, dürfen ihre Manifest-Zeile im Reconciler NICHT verlieren (#968, Codex :538) –
    sonst untracked Leak. Der Startup-Reconciler bricht dabei NICHT ab."""
    from obs.ringbuffer.store.offline_migration import reconcile_offline_migration

    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        seg = await store.manifest.create_migrating_segment(filename="rb_migrated_orphan.sqlite", schema_version=2)
        (store._segments_dir / "rb_migrated_orphan.sqlite").write_bytes(b"x" * 128)

        orig_unlink = Path.unlink

        def _boom(self, *a, **k):
            if self.name == "rb_migrated_orphan.sqlite":
                raise PermissionError("locked")
            return orig_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _boom)
        # Darf NICHT raisen (Startup muss öffnen) und die Zeile behalten.
        result = await reconcile_offline_migration(store)
        assert result is False
        assert await store.manifest.get_segment(seg.segment_id) is not None, "Manifest-Zeile muss fuer spaeteren Cleanup bleiben"
    finally:
        await store.close()


# ---------- Runde 12, Codex :1109: discard-migrating-Unlink-Fehler propagiert ----------


async def test_discard_migrating_unlink_failure_propagates_and_keeps_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kann eine ``migrating``-Kopie beim ``discard`` nicht unlinkt werden, propagiert der
    Fehler und die Manifest-Zeile bleibt (#968, Codex :1109) – kein untracked Leak, kein
    fälschlich terminales ``discarded``."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        seg = await rb._store.manifest.create_migrating_segment(filename="rb_migrated_x.sqlite", schema_version=2)
        (rb._store._segments_dir / "rb_migrated_x.sqlite").write_bytes(b"x" * 128)

        orig_unlink = Path.unlink

        def _boom(self, *a, **k):
            if self.name == "rb_migrated_x.sqlite":
                raise PermissionError("locked")
            return orig_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(PermissionError):
            await rb.discard_legacy()
        assert await rb._store.manifest.get_segment(seg.segment_id) is not None, "migrating-Zeile muss bleiben"
    finally:
        await rb.stop()


# ---------- Runde 12, Codex :323: Post-Commit-Retention-Fehler bleibt done ----------


async def test_post_commit_retention_failure_keeps_migration_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ein Fehler im Post-Commit-``enforce_retention`` darf die committete Migration nicht
    als ``failed`` melden (#968, Codex :323) – der Commit ist durch, es gibt keine Quelle
    mehr zum Retry."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(50)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)

        async def _boom():
            raise OSError("retention manifest IO")

        monkeypatch.setattr(rb._store, "enforce_retention", _boom)
        result = await mig.run({})
        assert result["phase"] == "done", "committete Migration bleibt done trotz Retention-Fehler"
        assert not legacy.exists()
    finally:
        await rb.stop()


# ---------- Runde 12, Codex :528: zero-copy Commit ohne migrating-Segmente reconcilen ----------


async def test_reconcile_zero_copy_missing_legacy_without_migrating(tmp_path: Path):
    """Eine drop-only Migration (``rows_to_copy == 0``) unlinkt die Legacy-DB, ohne
    migrating-Segmente anzulegen. Stirbt der Prozess vor dem Manifest-Delete, muss der
    Reconciler die Legacy-Zeile mit fehlender Datei AUCH ohne migrating-Segmente detachen
    (#968, Codex :528)."""
    from obs.ringbuffer.store.offline_migration import reconcile_offline_migration

    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        missing = tmp_path / "gone_legacy.db"  # existiert bewusst NICHT (unlinkt)
        row = await store.manifest.register_legacy_segment(source_path=str(missing), size_bytes=100)
        assert await store.manifest.list_migrating_segments() == []

        result = await reconcile_offline_migration(store)
        assert result is True, "zero-copy Commit muss vollendet gemeldet werden"
        assert row.segment_id not in {s.segment_id for s in await store.manifest.list_legacy_segments()}, "fehlende Legacy-Zeile muss detached sein"
    finally:
        await store.close()


# ---------- Runde 12, Codex :1033: Writer-Lease auch bei close-Fehler freigeben ----------


async def test_close_releases_lease_on_manifest_close_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wirft ``manifest.close()`` beim Store-Close, muss die Writer-Lease dennoch fallen
    (#968, Codex :1033) – sonst bliebe die ``writer.lock``-flock gehalten und ein zweiter
    Store auf demselben Root scheiterte mit ``WriterLockHeldError``."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()

    async def _boom():
        raise OSError("manifest close failed")

    monkeypatch.setattr(store.manifest, "close", _boom)
    with pytest.raises(OSError):
        await store.close()

    # Beweis: die Lease ist frei – ein zweiter Store öffnet ohne WriterLockHeldError.
    store2 = SqliteSegmentStore(tmp_path / "root")
    await store2.open()
    await store2.close()


# ---------- Runde 12/13, Codex :441/:2142: migrated nur ohne JEDE verbliebene Quelle ----------


async def test_has_attached_legacy_counts_quarantined(tmp_path: Path):
    """``has_attached_legacy`` steuert den Multi-Quellen-Abschluss (#968, Codex :441/:2142):
    schema-basiert True, solange IRGENDEINE Legacy-Quelle attached ist – auch eine
    quarantänierte. Sie ist zwar nicht migrierbar, aber der Assistent muss sichtbar bleiben,
    damit der Admin sie verwerfen kann; eine terminale ``migrated``-Entscheidung würde das
    verstecken. Erst nach discard (Legacy-Zeile weg) wird der Check False."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        assert await rb.has_attached_legacy() is True
        # Quarantänieren: nicht migrierbar, aber weiterhin attached → Check bleibt True.
        for seg in [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]:
            await rb._store.manifest.mark_quarantined(seg.segment_id, "corrupt (Test)")
        assert await rb.has_attached_legacy() is True, "quarantäniertes Legacy zählt noch (discard-Pfad offen)"
        # Erst nach dem Verwerfen ist keine Quelle mehr da.
        await rb.discard_legacy()
        assert await rb.has_attached_legacy() is False
    finally:
        await rb.stop()


# ---------- Runde 12/15, Codex :449/:326: Startup-Reconcile vollendet den Commit ----------


async def test_startup_reconcile_completes_interrupted_commit(tmp_path: Path):
    """Vollendet der Startup-Reconciler einen unterbrochenen Commit, ist der Store promotet und
    der durable ``has_committed_migration``-Marker True (#968, Codex :449/:326) – der Aufrufer
    (main.py) zieht daraufhin state-basiert die terminale ``migrated``-Entscheidung nach."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(30)))

    # Ersten Buffer starten, einen interrupted commit erzeugen (Commit wirft nach Unlink).
    rb1 = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb1.start()
    try:
        mig = OfflineLegacyMigrator(rb1._store, write_lock=rb1._lock)
        orig_commit = rb1._store.manifest.commit_offline_migration

        async def _boom(ids):
            raise OSError("commit failed after unlink")

        rb1._store.manifest.commit_offline_migration = _boom
        with pytest.raises(OSError):
            await mig.run({})
        rb1._store.manifest.commit_offline_migration = orig_commit
        assert not legacy.exists()  # Legacy-Datei unlinkt, Commit aber nicht vollendet
    finally:
        await rb1.stop()

    # Neustart auf demselben Pfad: der Startup-Reconciler vollendet den Commit.
    rb2 = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb2.start()
    try:
        assert await rb2.has_committed_migration() is True, "vollendeter Startup-Reconcile belegt den Commit durabel"
        # Der Store ist promotet: Alt-Historie sichtbar (negative gids), keine Legacy-Zeile mehr.
        assert await rb2._store.manifest.list_legacy_segments() == []
    finally:
        await rb2.stop()


# ---------- Runde 14, Codex :1239: Cancel NACH dem Commit bleibt done ----------


async def test_cancel_after_commit_keeps_migration_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wird der Job NACH dem destruktiven Commit gecancelt (Shutdown während der Post-
    Commit-Retention), darf er nicht als ``failed`` gemeldet werden (#968, Codex :1239):
    die Migration ist committed, das Post-Commit-Bookkeeping (on_success) läuft."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(40)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:

        async def _cancel():
            raise asyncio.CancelledError()

        # enforce_retention läuft NACH dem Commit (committed-Marker gesetzt) und cancelt.
        monkeypatch.setattr(rb._store, "enforce_retention", _cancel)
        on_success_called = {"v": False}

        async def _on_success():
            on_success_called["v"] = True

        await rb.start_legacy_migration(on_success=_on_success)
        with pytest.raises(asyncio.CancelledError):
            await rb._legacy_migration_task

        assert rb.legacy_migration_progress()["phase"] == "done", "Cancel nach Commit bleibt done, nicht failed"
        assert not legacy.exists(), "Commit ist durch"
        assert on_success_called["v"] is True, "Post-Commit-Bookkeeping (on_success) muss laufen"
    finally:
        await rb.stop()


# ---------- Runde 14, Codex :177: Kalibrierungs-Sample auf rows_to_copy deckeln ----------


async def test_calibration_sample_capped_to_rows_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Das Kalibrierungs-Sample darf höchstens ``plan.rows_to_copy`` Zeilen schreiben
    (#968, Codex :177) – nicht das volle ``COPY_BATCH_ROWS``, wenn der Budget-Cutoff nur
    wenige Zeilen zulässt."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(300)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        # Plan mit einer kleinen geplanten Kopiermenge (Budget-Cutoff): das Sample muss sich
        # daran orientieren, nicht am vollen COPY_BATCH_ROWS.
        plan = dataclasses.replace(await mig.plan(), rows_to_copy=5, cutoff_rowid=295)
        legacy_seg = await mig._attached_legacy()

        inserts = {"n": 0}
        orig_insert = rb._store._insert_event

        async def _spy(conn, gid, event):
            inserts["n"] += 1
            return await orig_insert(conn, gid, event)

        monkeypatch.setattr(rb._store, "_insert_event", _spy)
        await mig._calibrate_cutoff(plan, legacy_seg)
        assert inserts["n"] <= plan.rows_to_copy, "Sample darf nicht mehr als die geplante Kopiermenge schreiben"
    finally:
        await rb.stop()


# ---------- Runde 15, Codex :156: Cutoff aus geordneten Zeilen (Lücken-robust) ----------


async def test_migration_cutoff_survives_id_gaps(tmp_path: Path):
    """Bei nicht-kontinuierlichen ids (z. B. nach Age-Retention) darf der Cutoff keine
    existierende Alt-Zeile ausschließen (#968, Codex :156). ``max_rowid - rows_to_copy`` wäre
    hier die id einer bereits gelöschten Zeile und der Copy-Filter ``id > cutoff`` verlöre echte
    Daten, bevor der Commit die Legacy-DB unlinkt."""
    import aiosqlite

    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [10, 20, 30, 40])  # ids 1..4
    # Lücken erzeugen: ids 1 und 3 löschen -> es bleiben id=2 (Wert 20) und id=4 (Wert 40).
    async with aiosqlite.connect(legacy) as conn:
        await conn.execute("DELETE FROM ringbuffer WHERE id IN (1, 3)")
        await conn.commit()

    rb = _seg_rb(tmp_path, max_file_size_bytes=None, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        plan = await mig.plan()
        assert plan.total_rows == 2
        assert plan.rows_to_copy == 2, "unlimitiertes Budget kopiert alle existierenden Zeilen"
        # Der Bug: max_rowid(4) - rows_to_copy(2) = 2 -> `id > 2` migrierte nur id 4.
        # Korrekt: cutoff 0 (nur 2 Zeilen existieren, keine wird ausgeschlossen).
        assert plan.cutoff_rowid == 0, "cutoff darf keine existierende Zeile ausschließen"

        # End-to-End: die Migration behält BEIDE Zeilen.
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        assert await rb._store._total_row_count() == 2, "keine Zeile durch Lücken-Cutoff verloren"
    finally:
        await rb.stop()


# ---------- Runde 15, Codex :326: Cancel IM Commit-Fenster über State erkennen ----------


async def test_cancel_in_commit_window_detected_by_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Trifft der Shutdown-Cancel das schmale Fenster IM Commit-``await`` – der SQLite-Commit ist
    schon durch (Legacy detached, Kopien promotet), aber ``progress['committed']`` noch nicht
    gesetzt –, muss der Handler den Commit am durablen State erkennen und NICHT auf ``failed``
    zurückrollen (#968, Codex :326)."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(20)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        orig_commit = rb._store.manifest.commit_offline_migration

        async def _commit_then_cancel(ids):
            await orig_commit(ids)  # echter Commit: Legacy detached, Segmente promotet
            raise asyncio.CancelledError()  # Cancel VOR progress["committed"]=True

        monkeypatch.setattr(rb._store.manifest, "commit_offline_migration", _commit_then_cancel)
        on_success = {"v": False}

        async def _os():
            on_success["v"] = True

        await rb.start_legacy_migration(on_success=_os)
        with pytest.raises(asyncio.CancelledError):
            await rb._legacy_migration_task

        assert rb.legacy_migration_progress()["phase"] == "done", "State-Check erkennt den Commit trotz fehlendem Marker"
        assert not legacy.exists(), "Commit ist durch"
        assert await rb.has_committed_migration() is True
        assert on_success["v"] is True, "Post-Commit-Bookkeeping muss laufen"
    finally:
        await rb.stop()


async def test_has_committed_migration_reflects_promoted_segments(tmp_path: Path):
    """``has_committed_migration`` ist der durable Beleg des Commits: False solange nur Legacy
    (bzw. migrating) existiert, True sobald ``rb_migrated_*`` promotet sind (#968, Codex :326)."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(12)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        assert await rb.has_committed_migration() is False, "vor der Migration kein Commit-Beleg"
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        assert await rb.has_committed_migration() is True, "promotete rb_migrated_*-Segmente belegen den Commit"
    finally:
        await rb.stop()


# ---------- Runde 15, Codex :1273/:2423: Post-Commit-Decision state-basiert finalisieren ----------


async def test_finalize_after_persist_failure_retries(tmp_path: Path):
    """Scheiterte die ``on_success``-Persistenz der ``migrated``-Entscheidung nach einem Commit,
    zieht ``finalize_committed_migration_decision`` sie state-basiert nach (#968, Codex :1273)."""
    from obs.db.database import Database
    from obs.ringbuffer.persisted_config import (
        LEGACY_DECISION_MIGRATED,
        LEGACY_DECISION_SKIPPED,
        finalize_committed_migration_decision,
        load_legacy_migration_decision,
        persist_legacy_migration_decision,
    )

    db = Database(":memory:")
    await db.connect()
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(15)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        # Zustand nach einer 'skip'-Entscheidung, deren Migrations-Persistenz später ausblieb.
        await persist_legacy_migration_decision(db, LEGACY_DECISION_SKIPPED)
        # Migration ohne on_success: der Commit läuft, die Entscheidung wird NICHT persistiert.
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        assert not legacy.exists()
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_SKIPPED

        assert await finalize_committed_migration_decision(db, rb) is True
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_MIGRATED
        # Idempotent: ein zweiter Aufruf zieht nichts mehr nach.
        assert await finalize_committed_migration_decision(db, rb) is False
    finally:
        await rb.stop()
        await db.disconnect()


async def test_finalize_committed_migration_decision_branches(tmp_path: Path):
    """No-op-Zweige der Finalisierung: rb None, bereits terminal, noch Legacy attached, kein
    Commit-Beleg – nur committed + keine Legacy + non-terminal zieht ``migrated`` nach."""
    from obs.db.database import Database
    from obs.ringbuffer.persisted_config import (
        LEGACY_DECISION_MIGRATED,
        LEGACY_DECISION_PENDING,
        finalize_committed_migration_decision,
        persist_legacy_migration_decision,
    )

    class _Rb:
        def __init__(self, attached: bool, committed: bool):
            self._attached, self._committed = attached, committed

        async def has_attached_legacy(self):
            return self._attached

        async def has_committed_migration(self):
            return self._committed

    db = Database(":memory:")
    await db.connect()
    try:
        assert await finalize_committed_migration_decision(db, None) is False
        # Bereits terminal -> no-op, auch wenn committed.
        await persist_legacy_migration_decision(db, LEGACY_DECISION_MIGRATED)
        assert await finalize_committed_migration_decision(db, _Rb(False, True)) is False
        # Noch Legacy attached -> no-op (Assistent bleibt sichtbar).
        await persist_legacy_migration_decision(db, LEGACY_DECISION_PENDING)
        assert await finalize_committed_migration_decision(db, _Rb(True, True)) is False
        # Kein Commit-Beleg -> no-op.
        assert await finalize_committed_migration_decision(db, _Rb(False, False)) is False
        # committed + keine Legacy + non-terminal -> migrated.
        assert await finalize_committed_migration_decision(db, _Rb(False, True)) is True
    finally:
        await db.disconnect()


async def test_resolve_cutoff_rowid_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Rand-Fälle der id-geordneten Cutoff-Ableitung (#968, Codex :156): nichts kopieren,
    mehr als vorhanden kopieren, N-te-neueste id, und unlesbare Quelle."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(5)))  # ids 1..5
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        seg = await mig._attached_legacy()
        # rows_to_copy <= 0 -> nichts kopieren -> cutoff = max_rowid (WHERE id > max_rowid = leer).
        assert await mig._resolve_cutoff_rowid(seg, 0, 5) == 5
        # rows_to_copy >= vorhandene Zeilen -> cutoff 0 (alle kopieren).
        assert await mig._resolve_cutoff_rowid(seg, 10, 5) == 0
        # 2 von 5 -> Cutoff ist die id der 3.-neuesten Zeile (3); `id > 3` migriert 4 und 5.
        assert await mig._resolve_cutoff_rowid(seg, 2, 5) == 3

        # Unlesbare Quelle -> sauberer Migrationsfehler.
        async def _none(_seg):
            return None

        monkeypatch.setattr(rb._store, "_connection_for_read", _none)
        with pytest.raises(OfflineMigrationError):
            await mig._resolve_cutoff_rowid(seg, 2, 5)
    finally:
        await rb.stop()


# ---------- Runde 16, Codex :1175: drop-only-Commit gilt als migriert ----------


async def test_drop_only_commit_counts_as_migrated(tmp_path: Path):
    """Ein Commit ohne Kopie (``rows_to_copy == 0``, Budget lässt nichts zu) erzeugt KEIN
    ``rb_migrated_*``-Segment, detacht aber die Legacy-Quelle. Der durable Commit-Zähler belegt
    ihn trotzdem, sodass die Entscheidung terminal wird (#968, Codex :1175)."""
    from obs.db.database import Database
    from obs.ringbuffer.persisted_config import (
        LEGACY_DECISION_MIGRATED,
        LEGACY_DECISION_SKIPPED,
        finalize_committed_migration_decision,
        load_legacy_migration_decision,
        persist_legacy_migration_decision,
    )

    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(30)))
    legacy_size = legacy.stat().st_size
    db = Database(":memory:")
    await db.connect()
    # Sehr kleines Budget -> der Cutoff lässt 0 Zeilen zu (drop-only).
    rb = _seg_rb(tmp_path, max_file_size_bytes=legacy_size // 3, legacy_retention_protected=True)
    await rb.start()
    try:
        mig = OfflineLegacyMigrator(rb._store, write_lock=rb._lock)
        assert (await mig.plan()).rows_to_copy == 0, "Test-Voraussetzung: drop-only"

        await persist_legacy_migration_decision(db, LEGACY_DECISION_SKIPPED)
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        assert not legacy.exists()

        promoted = [s for s in await rb._store.manifest.list_segments() if s.filename.startswith("rb_migrated_")]
        assert promoted == [], "drop-only erzeugt keine migrierten Segmente"
        assert await rb.committed_migration_count() == 1
        assert await rb.has_committed_migration() is True, "durabler Zähler belegt den Commit auch ohne Segment"

        assert await finalize_committed_migration_decision(db, rb) is True
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_MIGRATED
    finally:
        await rb.stop()
        await db.disconnect()


# ---------- Runde 16, Codex :1263: Multi-Quellen-Commit beim Cancel erkennen ----------


async def test_cancel_after_commit_multisource_keeps_protection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Landet ein Cancel nach dem Commit EINER Quelle, während eine weitere Legacy-Quelle
    attached bleibt (``has_attached_legacy`` weiter True), belegt das Zähler-Delta den Commit –
    der Handler folgt dem Post-Commit-Pfad und behält den Schutz der verbleibenden Quelle, statt
    ihn auf den keep-Vorzustand (ungeschützt) zurückzurollen (#968, Codex :1263)."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(20)))
    # keep-Installation: Schutz initial AUS.
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=False)
    await rb.start()
    try:
        orig_commit = rb._store.manifest.commit_offline_migration

        async def _commit_then_cancel(ids):
            await orig_commit(ids)
            raise asyncio.CancelledError()

        monkeypatch.setattr(rb._store.manifest, "commit_offline_migration", _commit_then_cancel)

        # Verbleibende zweite Legacy-Quelle simulieren: has_attached_legacy bleibt True.
        async def _still_attached():
            return True

        monkeypatch.setattr(rb, "has_attached_legacy", _still_attached)

        await rb.start_legacy_migration()
        with pytest.raises(asyncio.CancelledError):
            await rb._legacy_migration_task

        assert rb.legacy_migration_progress()["phase"] == "done", "Zähler-Delta erkennt den Commit trotz verbleibender Quelle"
        assert rb._legacy_retention_protected is True, "Schutz der verbleibenden Quelle bleibt (kein Rollback auf keep-Vorzustand)"
    finally:
        await rb.stop()


# ---------- Runde 16, Codex :547: stop() lässt den Buffer nach close()-Fehler sauber ----------


async def test_stop_swallows_store_close_error(tmp_path: Path):
    """Wirft ``_store.close()`` beim Stop, darf ``stop()`` NICHT propagieren (sonst blieben die
    Aufrufer mit einem enabled Buffer + ``_store is None`` zurück) – der Store wird dennoch
    gelöst (#968, Codex :547)."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [1, 2, 3])
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    orig_close = rb._store.close

    async def _boom():
        await orig_close()
        raise OSError("close failed")

    rb._store.close = _boom
    # Darf nicht werfen:
    await rb.stop()
    assert rb._store is None, "Store wird trotz close()-Fehler gelöst"


async def test_committed_migration_count_zero_without_segmented_store():
    """``committed_migration_count``/``has_committed_migration`` liefern 0/False ohne
    segmentierten Store (#968, Codex :1175)."""
    rb = RingBuffer(storage="memory", max_entries=10)
    assert await rb.committed_migration_count() == 0
    assert await rb.has_committed_migration() is False


# ---------- Runde 16, Codex :2436: finalize-Fehler baut den Runtime-Buffer nicht ab ----------


async def test_config_finalize_failure_keeps_buffer_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wirft die Post-Init-Finalisierung im Config-Endpoint (transienter app-DB-Fehler – genau
    der Fall, den der Retry-Pfad behandelt), darf der frisch gebaute Buffer NICHT abgebaut werden:
    er bleibt subscribed + enabled, der Request scheitert nicht (#968, Codex :2436)."""
    import obs.api.v1.ringbuffer as rb_api
    from obs.db.database import Database
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer, is_ringbuffer_enabled, reset_ringbuffer

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    reset_ringbuffer()

    async def _boom(_db, _rb):
        raise RuntimeError("app-db locked")

    monkeypatch.setattr(rb_api, "finalize_committed_migration_decision", _boom)

    try:
        await rb_api.configure_ringbuffer(
            rb_api.RingBufferConfig(enabled=True, storage="file", segmented=True),
            _user="admin",
            db=db,
        )
        assert is_ringbuffer_enabled() is True, "Buffer bleibt trotz finalize-Fehler enabled"
        assert get_optional_ringbuffer() is not None, "Buffer wurde nicht abgebaut"
    finally:
        rb = get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()
        await db.disconnect()


# ---------- Runde 17, Codex :285: keep wird nicht auto-finalisiert ----------


async def test_finalize_does_not_override_keep(tmp_path: Path):
    """Eine bewusste ``keep``-Entscheidung darf der globale Commit-Zähler NICHT auf ``migrated``
    kippen (#968, Codex :285): bei mehreren Quellen belegt er nur, dass IRGENDEINE frühere Quelle
    migriert wurde – die zuletzt ge-keepte Quelle wurde behalten/gedroppt, nicht migriert."""
    from obs.db.database import Database
    from obs.ringbuffer.persisted_config import (
        LEGACY_DECISION_KEEP,
        finalize_committed_migration_decision,
        load_legacy_migration_decision,
        persist_legacy_migration_decision,
    )

    class _Rb:
        async def has_attached_legacy(self):
            return False  # keep-Quelle wurde von der Retention zurückgewonnen

        async def has_committed_migration(self):
            return True  # frühere Quelle hatte committed

    db = Database(":memory:")
    await db.connect()
    try:
        await persist_legacy_migration_decision(db, LEGACY_DECISION_KEEP)
        assert await finalize_committed_migration_decision(db, _Rb()) is False
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_KEEP, "keep bleibt keep"
    finally:
        await db.disconnect()


# ---------- Runde 17, Codex :459: Zähler-Backfill überlebt die Start-Retention ----------


async def test_commit_counter_backfilled_before_startup_retention(tmp_path: Path):
    """Alt-Manifest ohne Zähler, dessen einziges migriertes Segment beim Start von der Retention
    getrimmt wird: der Backfill zieht den durablen Zähler VOR der Retention aus dem Segment-Beleg,
    sodass ``has_committed_migration`` den Neustart überlebt (#968, Codex :459)."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(20)))
    rb = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb.start()
    try:
        await rb.start_legacy_migration()
        await rb._legacy_migration_task
        assert rb.legacy_migration_progress()["phase"] == "done"
        # Zähler auf 0 zurücksetzen -> Alt-Manifest-Zustand simulieren (nur Segment-Beleg da).
        await rb._store.manifest._db.execute("UPDATE migration_state SET committed_migrations = 0 WHERE id = 1")
        await rb._store.manifest._db.commit()
        assert await rb.committed_migration_count() == 0
    finally:
        await rb.stop()

    # Neustart auf demselben Pfad: der Backfill (vor enforce_retention) zieht den Zähler nach.
    rb2 = _seg_rb(tmp_path, max_file_size_bytes=10**9, legacy_retention_protected=True)
    await rb2.start()
    try:
        assert await rb2.committed_migration_count() == 1, "Backfill aus dem Segment-Beleg"
        assert await rb2.has_committed_migration() is True
    finally:
        await rb2.stop()


# ---------- Runde 18, Codex :1992: Status-Poll-Finalisierung best-effort ----------


async def test_status_finalize_error_does_not_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wirft die Finalisierung im ``GET /migration``-Status (app-DB weiter locked/voll), darf der
    Endpoint NICHT mit 500 antworten – der Frontend-Poller würde sonst stoppen und der Assistent
    bliebe stale (#968, Codex :1992)."""
    import obs.api.v1.ringbuffer as rb_api
    from obs.db.database import Database
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer, reset_ringbuffer

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    reset_ringbuffer()

    async def _boom(_db, _rb):
        raise RuntimeError("app-db locked")

    monkeypatch.setattr(rb_api, "finalize_committed_migration_decision", _boom)
    try:
        await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True, storage="file", segmented=True), _user="admin", db=db)
        # Darf NICHT werfen – der Status kommt trotz Finalisierungsfehler zurück.
        status = await rb_api._legacy_migration_status(db)
        assert status is not None
    finally:
        rb = get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()
        await db.disconnect()


# ---------- Runde 18, Codex :2032: Estimate schließt ALLE Legacy-Quellen aus ----------


async def test_attached_legacy_total_bytes_sums_all_sources(tmp_path: Path):
    """``attached_legacy_total_bytes`` summiert ALLE attachten Legacy-Segmente (#968, Codex :2032),
    Grundlage der Multi-Quellen-korrekten Copy-Estimate."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, list(range(10)))
    rb = _seg_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION

        legacy_segs = [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]
        assert legacy_segs, "eine Legacy-Quelle ist attached"
        expected = sum(s.size_bytes for s in legacy_segs)
        assert await rb.attached_legacy_total_bytes() == expected

        # Zweite Legacy-Quelle simulieren: die Summe wächst um deren Größe.
        second = await rb._store.manifest.register_legacy_segment(source_path=str(tmp_path / "obs_ringbuffer_2.db"), size_bytes=4242)
        assert await rb.attached_legacy_total_bytes() == expected + 4242, "beide Quellen zählen"
        assert second is not None
    finally:
        await rb.stop()
