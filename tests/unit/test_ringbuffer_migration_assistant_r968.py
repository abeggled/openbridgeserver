"""Codex-Runde #968 (10 Findings am Migrations-Assistenten, #964/#965).

Deckt die zehn Review-Findings der ersten Feature-Review ab: migrating-Status ×
Retention/Guard, Datei-Op-Fehler-Rollback, Overview-/keep-Konsistenz, Disk-
Precheck-Timing, Eskalations-Prognose-Pfade und den Job-Race.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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

        # Erneuter Lauf: darf die Kopie NICHT verwerfen, sondern den Commit vollenden.
        migrated_before = {s.filename for s in await rb._store.manifest.list_migrating_segments()}
        with pytest.raises(OfflineMigrationError):
            # Nach dem Reconcile-Promote gibt es kein Legacy mehr -> plan/run bricht sauber ab.
            await mig.run({})
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
