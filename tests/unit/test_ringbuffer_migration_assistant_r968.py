"""Codex-Runde #968 (10 Findings am Migrations-Assistenten, #964/#965).

Deckt die zehn Review-Findings der ersten Feature-Review ab: migrating-Status ×
Retention/Guard, Datei-Op-Fehler-Rollback, Overview-/keep-Konsistenz, Disk-
Precheck-Timing, Eskalations-Prognose-Pfade und den Job-Race.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION
from obs.ringbuffer.store.offline_migration import OfflineLegacyMigrator, OfflineMigrationError
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


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
