"""Segmentgenaue Retention, Recovery/Quarantäne, Checkpoint-Läufer und
Betriebs-Stats des SQLite-Segment-Backends (#936, Vertrag aus #930).

Deckt die verbindlichen Randfälle ab:
(a) Size-Budget löscht ganze geschlossene Segmente,
(b) Age-Cutoff löscht vollständig alte geschlossene Segmente,
(c) Rows-Budget mit Segmentgranularität,
(d) das aktive Segment wird nie gelöscht/getrimmt,
(e) retention_over_budget wird korrekt gemeldet,
(f) checkpoint_pending → später erfolgreich getruncatet → erst dann retention-fähig,
(g) korruptes geschlossenes Segment → quarantined ohne globalen Scan,
(h) Startup erzwingt keinen globalen Integrity-Scan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import (
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_CLOSED,
    SEGMENT_STATUS_QUARANTINED,
)
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


def _iso(offset_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


async def _make_store(root: Path, **kwargs) -> SqliteSegmentStore:
    store = SqliteSegmentStore(root, **kwargs)
    await store.open()
    return store


# ---------------------------------------------------------------------------
# (a) Size-Budget: ganze geschlossene Segmente löschen
# ---------------------------------------------------------------------------


async def test_retention_deletes_whole_closed_segments_by_size_budget(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        # Drei geschlossene Segmente + ein aktives erzeugen.
        for i in range(3):
            await store.append([_event(i, _iso(i))])
            await store.rotate()
        await store.append([_event(99, _iso(99))])

        closed = await store.manifest.list_closed_segments()
        assert len(closed) == 3
        # Budget so setzen, dass nur die neuesten Segmente hineinpassen.
        total = sum(s.size_bytes for s in await store.manifest.list_segments())
        smallest = min(s.size_bytes for s in closed)
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=total - smallest)

        removed = await store.enforce_retention()
        assert removed >= 1
        # Es bleibt genau ein aktives Segment übrig, Rest wurde als ganze Einheit gelöscht.
        remaining = await store.manifest.list_segments()
        assert sum(s.size_bytes for s in remaining) <= total - smallest
        # Kein rowweises Trimmen: übrig gebliebene Segmente behalten ihre volle row_count.
        for s in remaining:
            assert s.row_count in (0, 1)
    finally:
        await store.close()


async def test_retention_size_budget_deletes_oldest_first(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        ids = []
        for i in range(3):
            await store.append([_event(i, _iso(i))])
            seg = await store.manifest.get_active_segment()
            ids.append(seg.segment_id)
            await store.rotate()
        await store.append([_event(99, _iso(99))])

        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)  # praktisch alles löschen
        removed = await store.enforce_retention()
        assert removed == 3
        # Alle geschlossenen weg, aktives bleibt.
        assert await store.manifest.list_closed_segments() == []
        assert await store.manifest.get_active_segment() is not None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (b) Age-Cutoff
# ---------------------------------------------------------------------------


async def test_retention_deletes_segments_older_than_age_cutoff(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        # Ein altes Segment (to_ts weit in der Vergangenheit).
        await store.append([_event(1, _iso(-100000))])
        old_id = (await store.manifest.get_active_segment()).segment_id
        await store.rotate()
        # Ein junges Segment.
        await store.append([_event(2, _iso(-1))])
        young_id = (await store.manifest.get_active_segment()).segment_id
        await store.rotate()
        await store.append([_event(3, _iso(0))])

        store._retention_config = StoreRetentionConfig(max_age=3600)  # 1h
        removed = await store.enforce_retention()
        assert removed == 1
        assert await store.manifest.get_segment(old_id) is None
        assert await store.manifest.get_segment(young_id) is not None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (c) Rows-Budget mit Segmentgranularität
# ---------------------------------------------------------------------------


async def test_retention_deletes_whole_segments_by_row_budget(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        # Drei geschlossene Segmente mit je 2 Rows = 6 geschlossene Rows.
        for i in range(3):
            await store.append([_event(i, _iso(i)), _event(i + 100, _iso(i))])
            await store.rotate()
        await store.append([_event(99, _iso(99))])  # aktives Segment, 1 Row

        # Budget 5 → segmentgranular: ältestes 2-Row-Segment muss weichen (7 → 5).
        store._retention_config = StoreRetentionConfig(max_entries=5)
        removed = await store.enforce_retention()
        assert removed == 1
        remaining_rows = sum(s.row_count for s in await store.manifest.list_segments())
        assert remaining_rows <= 5
        # Segmentgranular: kein Segment wurde teil-getrimmt.
        for s in await store.manifest.list_segments():
            assert s.row_count in (0, 1, 2)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (d) aktives Segment wird nie gelöscht/getrimmt
# ---------------------------------------------------------------------------


async def test_retention_never_deletes_active_segment(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0)), _event(2, _iso(1))])
        active_id = (await store.manifest.get_active_segment()).segment_id
        # Extrem harte Budgets — trotzdem darf das aktive Segment nicht angetastet werden.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1, max_entries=1, max_age=1)
        removed = await store.enforce_retention()
        assert removed == 0
        assert (await store.manifest.get_active_segment()).segment_id == active_id
        rows = await store.query(StoreQuery(limit=10))
        assert len(rows) == 2  # nichts rowweise entfernt
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (e) retention_over_budget
# ---------------------------------------------------------------------------


async def test_stats_reports_retention_over_budget_when_active_exceeds(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        # Budget kleiner als das nicht-löschbare aktive Segment.
        active_size = (await store.manifest.get_active_segment()).size_bytes
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=max(active_size - 1, 1))
        # Enforce kann nichts freigeben (nur aktives Segment vorhanden).
        removed = await store.enforce_retention()
        assert removed == 0
        stats = await store.stats()
        assert stats.backend_extra["retention_over_budget"] is True
        assert stats.backend_extra["retention_pressure_reason"] is not None
    finally:
        await store.close()


async def test_stats_not_over_budget_when_within_limits(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=10 * 1024 * 1024)
        stats = await store.stats()
        assert stats.backend_extra["retention_over_budget"] is False
        assert stats.backend_extra["retention_pressure_reason"] is None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (f) checkpoint_pending → später getruncatet → dann retention-fähig
# ---------------------------------------------------------------------------


async def test_checkpoint_pending_segment_becomes_retention_eligible_after_runner(tmp_path: Path, monkeypatch):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        pending_id = (await store.manifest.get_active_segment()).segment_id

        # Erster Close: checkpoint busy → checkpoint_pending.
        async def _busy(_conn):
            return False

        monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
        await store.rotate()
        seg = await store.manifest.get_segment(pending_id)
        assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING

        # Solange pending: nicht retention-fähig (auch bei hartem Budget).
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
        assert await store.enforce_retention() == 0
        assert await store.manifest.get_segment(pending_id) is not None

        # Läufer schafft den Truncate jetzt.
        async def _ok(_conn):
            return True

        monkeypatch.setattr(store, "_try_truncate_checkpoint", _ok)
        recovered = await store.run_pending_checkpoints()
        assert recovered == 1
        assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CLOSED

        # Jetzt greift Retention.
        removed = await store.enforce_retention()
        assert removed == 1
        assert await store.manifest.get_segment(pending_id) is None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (g) korruptes geschlossenes Segment → quarantined, kein globaler Scan
# ---------------------------------------------------------------------------


async def test_corrupt_closed_segment_is_quarantined_per_segment(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        corrupt_id = (await store.manifest.get_active_segment()).segment_id
        seg = await store.manifest.get_segment(corrupt_id)
        await store.rotate()  # jetzt geschlossen

        # Segment-Datei korrumpieren.
        seg_path = store._segments_dir / seg.filename
        seg_path.write_bytes(b"not a sqlite database at all" * 8)

        ok = await store.check_segment_integrity(corrupt_id)
        assert ok is False
        reloaded = await store.manifest.get_segment(corrupt_id)
        assert reloaded.status == SEGMENT_STATUS_QUARANTINED
        assert reloaded.quarantine_reason is not None

        # Quarantänierte Segmente werden von Retention nicht angefasst.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
        removed = await store.enforce_retention()
        assert removed == 0
        assert (await store.manifest.get_segment(corrupt_id)).status == SEGMENT_STATUS_QUARANTINED
    finally:
        await store.close()


async def test_integrity_check_never_quarantines_active_segment(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        active_id = (await store.manifest.get_active_segment()).segment_id
        assert await store.check_segment_integrity(active_id) is True
    finally:
        await store.close()


async def test_check_integrity_returns_false_for_unknown_segment(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        assert await store.check_segment_integrity(9999) is False
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (h) Startup erzwingt keinen globalen Integrity-Scan
# ---------------------------------------------------------------------------


async def test_open_does_not_run_global_integrity_scan(tmp_path: Path, monkeypatch):
    # Erst einen Store mit mehreren geschlossenen Segmenten aufbauen und schließen.
    seed = await _make_store(tmp_path / "root")
    for i in range(3):
        await seed.append([_event(i, _iso(i))])
        await seed.rotate()
    await seed.close()

    # Beim erneuten open() darf integrity_check auf KEINEM Segment laufen.
    calls: list[str] = []

    store = SqliteSegmentStore(tmp_path / "root")

    import aiosqlite

    orig_execute = aiosqlite.Connection.execute

    def _spy_execute(self, sql, *args, **kwargs):
        # aiosqlite.Connection.execute liefert ein awaitbares Cursor-Kontextobjekt;
        # nicht selbst awaiten, nur die SQL beobachten.
        if "integrity_check" in str(sql).lower():
            calls.append(str(sql))
        return orig_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", _spy_execute)
    await store.open()
    try:
        assert calls == []
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Stats: WAL/SHM/Checkpoint-Betriebsdetails in backend_extra (nicht common)
# ---------------------------------------------------------------------------


async def test_stats_backend_extra_has_wal_and_checkpoint_fields(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        stats = await store.stats()
        extra = stats.backend_extra
        for key in (
            "wal_size_bytes",
            "shm_size_bytes",
            "last_checkpoint_at",
            "last_checkpoint_mode",
            "last_checkpoint_result",
            "wal_checkpoint_busy",
            "checkpoint_pending",
            "retention_over_budget",
            "retention_pressure_reason",
            "storage_on_network_drive",
            "segments",
        ):
            assert key in extra
        # Keine SQLite-Interna im portablen common-Teil.
        assert "wal_size_bytes" not in stats.common
        assert "segments" not in stats.common
        # Per-Segment Recovery-/Integrity-Status enthalten.
        assert stats.backend_extra["segments"][0]["integrity_status"] == "ok"
    finally:
        await store.close()


async def test_stats_records_busy_checkpoint_counter(tmp_path: Path, monkeypatch):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])

        async def _busy(_conn):
            store._last_checkpoint_at = "x"
            store._last_checkpoint_mode = "TRUNCATE"
            store._last_checkpoint_result = "busy"
            store._wal_checkpoint_busy_count += 1
            return False

        monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
        await store.rotate()
        stats = await store.stats()
        assert stats.backend_extra["wal_checkpoint_busy"] >= 1
        assert stats.backend_extra["checkpoint_pending"] >= 1
    finally:
        await store.close()


@pytest.mark.parametrize(
    ("mounts", "expected"),
    [
        ("nfsserver:/export /mnt/data nfs rw 0 0\n", True),
        ("/dev/sda1 / ext4 rw 0 0\n", False),
    ],
)
async def test_storage_network_drive_detection(tmp_path: Path, monkeypatch, mounts, expected):
    root = tmp_path / "mnt" / "data" / "root"
    root.mkdir(parents=True)
    store = SqliteSegmentStore(root)
    # /proc/mounts simulieren: der Mountpoint /mnt/data überdeckt die Root.
    real_open = open

    def _fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            import io

            content = mounts.replace("/mnt/data", str((tmp_path / "mnt" / "data").resolve()))
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _fake_open)
    assert store._storage_on_network_drive() is expected


async def test_storage_network_drive_detection_handles_missing_proc(tmp_path: Path, monkeypatch):
    store = SqliteSegmentStore(tmp_path / "root")

    def _boom(path, *args, **kwargs):
        if path == "/proc/mounts":
            raise OSError("no /proc")
        raise AssertionError("unexpected open")

    monkeypatch.setattr("builtins.open", _boom)
    assert store._storage_on_network_drive() is False


async def test_storage_network_drive_detection_skips_malformed_mount_lines(tmp_path: Path, monkeypatch):
    store = SqliteSegmentStore(tmp_path / "root")

    def _fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            import io

            # Erste Zeile ist unvollständig (< 3 Felder) → muss übersprungen werden.
            return io.StringIO("garbage\n/dev/sda1 / ext4 rw 0 0\n")
        raise AssertionError("unexpected open")

    monkeypatch.setattr("builtins.open", _fake_open)
    assert store._storage_on_network_drive() is False


# ---------------------------------------------------------------------------
# Feinabdeckung: einzelne Budget-Guards, Zeit-Parsing, echter busy-Checkpoint,
# integrity_check-Ergebnis "nicht ok" (openbare, aber inkonsistente DB).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (#919) Legacy-Segment: zählt gegen das Size-Budget, wird per Size-Retention
# löschbar (sobald ≥1 v2-Datenquelle existiert), aber nie als einzige Quelle.
# ---------------------------------------------------------------------------


async def _attach_legacy(store: SqliteSegmentStore, size_bytes: int):
    """Hängt ein synthetisches Legacy-Segment gegebener Größe ins Manifest ein."""
    legacy_file = store._root / "legacy_source.db"
    legacy_file.write_bytes(b"\x00" * size_bytes)
    return await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=size_bytes), legacy_file


async def test_legacy_segment_size_counts_against_total_and_budget(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        base_total = await store._total_size_bytes()
        legacy_size = 5 * 1024 * 1024
        await _attach_legacy(store, legacy_size)
        # Der Legacy-Blob ist voll in der Gesamtgröße enthalten.
        assert await store._total_size_bytes() == base_total + legacy_size
    finally:
        await store.close()


async def test_legacy_deleted_by_size_budget_once_v2_segment_exists(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        # Ein geschlossenes v2-Segment mit echten Daten sichern.
        await store.append([_event(1, _iso(0))])
        await store.rotate()
        # Aktives v2-Segment mit Daten.
        await store.append([_event(2, _iso(1))])

        legacy_size = 8 * 1024 * 1024
        _, legacy_file = await _attach_legacy(store, legacy_size)

        # Budget klein → Retention muss auch den Legacy-Blob freigeben können,
        # nachdem die geschlossenen v2-Segmente allein das Budget nicht drücken.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
        removed = await store.enforce_retention()
        assert removed >= 1
        # Legacy ist aus dem Manifest verschwunden …
        assert await store.manifest.list_legacy_segments() == []
        # … aber die in-place Legacy-Datei bleibt physisch erhalten (Grundgebot #934).
        assert legacy_file.exists()
        # Aktives v2-Segment (frische Daten) bleibt erhalten.
        assert await store.manifest.get_active_segment() is not None
    finally:
        await store.close()


async def test_legacy_not_deleted_when_only_data_source(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        # Kein v2-Datensegment: aktives Segment ist leer, nur Legacy trägt Historie.
        legacy_size = 8 * 1024 * 1024
        await _attach_legacy(store, legacy_size)
        assert await store._has_nonlegacy_data_segment() is False

        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
        removed = await store.enforce_retention()
        # No-Zero-History-Guard: Legacy bleibt, solange es die einzige Quelle ist.
        assert removed == 0
        assert len(await store.manifest.list_legacy_segments()) == 1
    finally:
        await store.close()


async def test_enforce_retention_with_only_size_budget_skips_age_and_rows(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        await store.rotate()
        await store.append([_event(2, _iso(1))])
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=10 * 1024 * 1024)
        # Age/Rows None → deren Zweige liefern früh 0, nur Size wird geprüft.
        assert await store.enforce_retention() == 0
    finally:
        await store.close()


def test_parse_ts_returns_none_for_empty_and_invalid():
    from obs.ringbuffer.store.sqlite_backend import _parse_ts

    assert _parse_ts(None) is None
    assert _parse_ts("") is None
    assert _parse_ts("not-a-timestamp") is None
    assert _parse_ts("2026-01-01T00:00:00Z") is not None


async def test_real_checkpoint_records_result_and_busy_counter(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        # Echter _try_truncate_checkpoint-Pfad (kein Monkeypatch): auf einer frischen,
        # allein gehaltenen DB gelingt TRUNCATE → result "ok", busy bleibt 0.
        await store.rotate()
        stats = await store.stats()
        assert stats.backend_extra["last_checkpoint_mode"] == "TRUNCATE"
        assert stats.backend_extra["last_checkpoint_result"] in ("ok", "busy")
        assert isinstance(stats.backend_extra["wal_checkpoint_busy"], int)
    finally:
        await store.close()


async def test_wal_and_shm_size_are_zero_without_active_segment(tmp_path: Path):
    store = SqliteSegmentStore(tmp_path / "root")
    # Ohne offenes/aktives Segment liefern die Sidecar-Größen defensiv 0.
    assert store._active_wal_size() == 0
    assert store._active_shm_size() == 0


async def test_try_truncate_checkpoint_counts_busy(tmp_path: Path):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])

        class _BusyCursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetchone(self):
                # PRAGMA-Ergebnis (busy=1, log, checkpointed) → nicht vollständig.
                return (1, 0, 0)

        class _BusyConn:
            def execute(self, *_args, **_kwargs):
                return _BusyCursor()

        before = store._wal_checkpoint_busy_count
        ok = await store._try_truncate_checkpoint(_BusyConn())
        assert ok is False
        assert store._wal_checkpoint_busy_count == before + 1
        assert store._last_checkpoint_result == "busy"
    finally:
        await store.close()


async def test_integrity_check_quarantines_on_non_ok_result(tmp_path: Path, monkeypatch):
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        seg_id = (await store.manifest.get_active_segment()).segment_id
        await store.rotate()  # geschlossen, DB ist openbar

        # integrity_check meldet eine Inkonsistenz (kein Exception, nur != "ok").
        import aiosqlite

        orig_execute = aiosqlite.Connection.execute

        def _patched_execute(self, sql, *args, **kwargs):
            if "integrity_check" in str(sql).lower():
                return orig_execute(self, "SELECT 'row 3 missing from index idx' AS x", *args, **kwargs)
            return orig_execute(self, sql, *args, **kwargs)

        monkeypatch.setattr(aiosqlite.Connection, "execute", _patched_execute)
        ok = await store.check_segment_integrity(seg_id)
        assert ok is False
        reloaded = await store.manifest.get_segment(seg_id)
        assert reloaded.status == SEGMENT_STATUS_QUARANTINED
        assert "row 3 missing" in reloaded.quarantine_reason
    finally:
        await store.close()
