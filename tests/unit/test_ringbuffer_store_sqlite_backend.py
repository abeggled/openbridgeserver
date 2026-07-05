"""SQLite-Segment-Backend: implementiert den portablen RingBufferStore (#931).

Deckt ab: genau ein aktives Segment, Rotation öffnet genau ein neues aktives
(löscht nie Daten), globale Event-ID monoton über Rotation hinweg, zweiter
Writer auf derselben Root fail-fast, Checkpoint-busy → checkpoint_pending,
Capability-Deskriptor, stats() mit common/backend_extra.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig
from obs.ringbuffer.store.interface import (
    OrderingGuarantee,
    RingBufferStore,
    StoreEvent,
    StoreQuery,
)
from obs.ringbuffer.store.manifest import (
    SEGMENT_STATUS_ACTIVE,
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_QUARANTINED,
)
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore
from obs.ringbuffer.store.writer_lock import WriterLockHeldError


def _event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={
            "datapoint": {"tags": ["t"]},
            "bindings": [{"adapter_type": "KNX", "normalized": {"group_address": "1/2/3"}}],
        },
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_backend_is_a_ringbuffer_store(store: SqliteSegmentStore):
    assert isinstance(store, RingBufferStore)


async def test_capabilities_describe_sqlite_backend(store: SqliteSegmentStore):
    caps = store.capabilities()
    assert caps.supports_native_retention is True
    assert caps.ordering_guarantee is OrderingGuarantee.GLOBAL_MONOTONIC
    # Typed pushdown ist mit #933 nativ; Streaming-Export bleibt Welle-2 (#932).
    assert caps.supports_typed_pushdown is True
    assert caps.supports_streaming_export is False


async def test_open_creates_exactly_one_active_segment(store: SqliteSegmentStore):
    segments = await store.manifest.list_segments()
    active = [s for s in segments if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1


async def test_append_then_query_roundtrip(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z"), _event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    assert len(rows) == 2
    assert {r["new_value"] for r in rows} == {1, 2}
    assert all("global_event_id" in r for r in rows)


async def test_append_is_append_only_and_assigns_monotonic_global_ids(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    by_value = {r["new_value"]: r["global_event_id"] for r in rows}
    assert by_value[2] > by_value[1]


async def test_rotate_opens_exactly_one_new_active_and_keeps_data(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    old_active = await store.manifest.get_active_segment()

    await store.rotate()

    segments = await store.manifest.list_segments()
    active = [s for s in segments if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1
    assert active[0].segment_id != old_active.segment_id
    # Rotation loescht keine Daten.
    rows = await store.query(StoreQuery(limit=10))
    assert len(rows) == 1


async def test_global_event_id_is_monotonic_across_rotation(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    by_value = {r["new_value"]: r["global_event_id"] for r in rows}
    # Trotz per-Segment-rowid muss die globale ID über die Segmentgrenze wachsen.
    assert by_value[2] > by_value[1]


async def test_query_orders_by_global_event_id_desc_across_segments(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    # Neueste zuerst.
    assert [r["new_value"] for r in rows] == [2, 1]


async def test_second_writer_on_same_root_fails_fast(tmp_path: Path):
    first = SqliteSegmentStore(tmp_path / "root")
    await first.open()
    try:
        second = SqliteSegmentStore(tmp_path / "root")
        with pytest.raises(WriterLockHeldError):
            await second.open()
    finally:
        await first.close()


async def test_close_marks_segment_checkpoint_pending_when_busy(store: SqliteSegmentStore, monkeypatch):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    seg_id = (await store.manifest.get_active_segment()).segment_id

    # Simuliert wal_checkpoint(TRUNCATE) busy durch aktive Reader.
    async def _busy_checkpoint(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy_checkpoint)
    await store.rotate()

    seg = await store.manifest.get_segment(seg_id)
    assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING


async def test_busy_checkpoint_never_persists_transient_closed(store: SqliteSegmentStore, monkeypatch):
    """Busy-Checkpoint-Rotation schreibt NIE ein transientes ``closed`` (#951, Runde 47).

    Der alte Ablauf persistierte erst ``closed`` (retention-eligible) und stufte
    dann auf ``checkpoint_pending`` um. Ein Crash zwischen beiden Writes ließe ein
    Segment mit nicht-getruncatetem WAL als sauber geschlossen zurück – die
    Retention dürfte es löschen und der Read-Pfad hielte es für konsistent. Der
    Statuswechsel muss daher in EINEM durablen Write erfolgen; ``closed_at`` wird
    dabei trotzdem gesetzt (das Segment ist für Writes geschlossen).
    """
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    seg_id = (await store.manifest.get_active_segment()).segment_id

    async def _busy_checkpoint(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy_checkpoint)

    close_calls: list[int] = []
    original_close = store.manifest.close_segment

    async def _spy_close(segment_id):
        close_calls.append(segment_id)
        await original_close(segment_id)

    monkeypatch.setattr(store.manifest, "close_segment", _spy_close)
    await store.rotate()

    seg = await store.manifest.get_segment(seg_id)
    assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING
    assert seg.closed_at is not None, "auch pending Segmente sind fuer Writes geschlossen"
    assert seg_id not in close_calls, "kein transienter closed-Write im Busy-Pfad"


async def test_stats_split_common_and_backend_extra(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    stats = await store.stats()
    assert stats.common["total"] == 1
    assert stats.common["segment_count"] >= 1
    # SQLite-Interna nur unter backend_extra.
    assert "active_segment_id" in stats.backend_extra
    assert "wal_size_bytes" not in stats.common


async def test_enforce_retention_is_noop_without_config(store: SqliteSegmentStore):
    # Ohne konfigurierte Retention-Limits wird nichts freigegeben.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    removed = await store.enforce_retention()
    assert removed == 0


async def test_retention_config_is_accepted_and_validated(tmp_path: Path):
    # Zu grobe Segmentierung im Verhaeltnis zu Retention wird beim open abgelehnt.
    store = SqliteSegmentStore(
        tmp_path / "root",
        segments=SegmentConfig(segment_max_bytes=1000),
        retention=StoreRetentionConfig(max_file_size_bytes=1000),  # < 3*1000
    )
    with pytest.raises(ValueError, match="max_file_size_bytes"):
        await store.open()


async def test_query_applies_all_filter_clauses(store: SqliteSegmentStore):
    await store.append(
        [
            StoreEvent(
                ts="2026-01-01T00:00:00.000Z",
                datapoint_id="dp-a",
                topic="dp/dp-a/value",
                old_value=None,
                new_value=1,
                source_adapter="api",
                quality="good",
            ),
            StoreEvent(
                ts="2026-01-02T00:00:00.000Z",
                datapoint_id="dp-b",
                topic="dp/dp-b/value",
                old_value=None,
                new_value=2,
                source_adapter="knx",
                quality="bad",
            ),
        ]
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-01T00:00:00.000Z",
            to_ts="2026-01-01T12:00:00.000Z",
            datapoint_id="dp-a",
            source_adapter="api",
            quality="good",
            limit=10,
        )
    )
    assert len(rows) == 1
    assert rows[0]["datapoint_id"] == "dp-a"


async def test_query_offset_paginates_across_segments(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=1, offset=1))
    # neueste zuerst → offset 1 überspringt Wert 2 und liefert Wert 1.
    assert [r["new_value"] for r in rows] == [1]


async def test_append_noop_when_events_empty(store: SqliteSegmentStore):
    await store.append([])
    stats = await store.stats()
    assert stats.common["total"] == 0


async def test_open_releases_lease_when_manifest_open_fails(tmp_path: Path, monkeypatch):
    store = SqliteSegmentStore(tmp_path / "root")

    async def _boom():
        raise RuntimeError("manifest boom")

    monkeypatch.setattr(store.manifest, "open", _boom)
    with pytest.raises(RuntimeError, match="manifest boom"):
        await store.open()

    # Lease muss freigegeben sein → ein sauberer zweiter Store kann öffnen.
    recovered = SqliteSegmentStore(tmp_path / "root")
    await recovered.open()
    await recovered.close()


async def test_persist_metadata_indexes_ignores_invalid_entry_id(store: SqliteSegmentStore):
    # Defensive Guard: entry_id <= 0 fuehrt zu keinem Insert.
    await store._persist_metadata_indexes(store._active_conn, 0, {"datapoint": {"tags": ["x"]}})
    rows = await store.query(StoreQuery(limit=10))
    assert rows == []


async def test_refresh_stats_noop_without_active_segment(store: SqliteSegmentStore):
    store._active_segment = None
    # Darf ohne aktives Segment nicht werfen.
    await store._refresh_active_segment_stats()


# ----------------------------------------------------------------------
# #919: Isolation quarantänierter Segmente + Robustheit gegen ein WIRKLICH
# defektes Segment-File.
# ----------------------------------------------------------------------


def _destroy_segment_file(store: SqliteSegmentStore, filename: str) -> None:
    """Zerstört eine geschlossene Segment-Datei physisch (Müll-Bytes über den Header)."""
    path = store._segments_dir / filename
    with open(path, "r+b") as handle:
        handle.seek(0)
        handle.write(b"GARBAGE-NOT-A-DATABASE" * 64)


async def _store_with_corrupt_closed_segment(tmp_path: Path):
    """Store mit zwei geschlossenen Segmenten; das erste ist auf Platte zerstört.

    Liefert (store, corrupt_segment_id, corrupt_filename). Segment 1 hält Wert 1,
    Segment 2 hält Wert 2 (gesund), aktives Segment 3 hält Wert 3.
    """
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    corrupt = await store.manifest.get_active_segment()
    await store.rotate()  # schließt Segment 1
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    await store.rotate()  # schließt Segment 2
    await store.append([_event(3, "2026-01-01T00:00:02.000Z")])
    # Datei des geschlossenen Segments 1 physisch zerstören.
    _destroy_segment_file(store, corrupt.filename)
    return store, corrupt.segment_id, corrupt.filename


async def test_stats_survive_corrupt_segment_file_from_manifest(tmp_path: Path):
    store, corrupt_id, _ = await _store_with_corrupt_closed_segment(tmp_path)
    try:
        # stats() öffnet nie eine Segment-DB → liefert ALLE Segment-Infos aus dem
        # Manifest, auch für das kaputte Segment, ohne Exception.
        stats = await store.stats()
        seg_ids = {s["segment_id"] for s in stats.backend_extra["segments"]}
        assert corrupt_id in seg_ids
        # Row-Count/from_ts stammen aus dem Manifest, nicht aus der Datei.
        corrupt_stat = next(s for s in stats.backend_extra["segments"] if s["segment_id"] == corrupt_id)
        assert corrupt_stat["row_count"] == 1
        assert corrupt_stat["from_ts"] is not None
    finally:
        await store.close()


async def test_query_quarantines_corrupt_segment_on_the_fly_and_returns_rest(tmp_path: Path):
    store, corrupt_id, _ = await _store_with_corrupt_closed_segment(tmp_path)
    try:
        # Query, die das kaputte Segment einschließen würde, darf nicht brechen.
        rows = await store.query(StoreQuery(limit=10))
        values = {r["new_value"] for r in rows}
        # Werte der gesunden Segmente (2, 3) kommen zurück; Wert 1 (kaputt) fehlt.
        assert values == {2, 3}
        # Das kaputte Segment wurde on-the-fly quarantäniert.
        seg = await store.manifest.get_segment(corrupt_id)
        assert seg.status == SEGMENT_STATUS_QUARANTINED
        assert seg.integrity_status == "corrupt"
        assert seg.quarantine_reason
    finally:
        await store.close()


async def test_check_segment_integrity_quarantines_corrupt_without_raising(tmp_path: Path):
    store, corrupt_id, _ = await _store_with_corrupt_closed_segment(tmp_path)
    try:
        ok = await store.check_segment_integrity(corrupt_id)
        assert ok is False
        seg = await store.manifest.get_segment(corrupt_id)
        assert seg.status == SEGMENT_STATUS_QUARANTINED
        assert seg.integrity_status == "corrupt"
    finally:
        await store.close()


async def test_quarantined_segment_excluded_from_query_selection(tmp_path: Path):
    store, corrupt_id, _ = await _store_with_corrupt_closed_segment(tmp_path)
    try:
        # Nach expliziter Integritätsprüfung ist das Segment quarantäniert.
        await store.check_segment_integrity(corrupt_id)
        # list_segments_for_query schließt es aus → Query öffnet es gar nicht mehr.
        selected = await store.manifest.list_segments_for_query()
        assert corrupt_id not in {s.segment_id for s in selected}
        rows = await store.query(StoreQuery(limit=10))
        assert {r["new_value"] for r in rows} == {2, 3}
    finally:
        await store.close()


async def test_healthy_segments_query_unchanged_regression(tmp_path: Path):
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
        await store.rotate()
        await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
        rows = await store.query(StoreQuery(limit=10))
        assert [r["new_value"] for r in rows] == [2, 1]
        # Keine gesunden Segmente werden quarantäniert.
        segments = await store.manifest.list_segments()
        assert all(s.status != SEGMENT_STATUS_QUARANTINED for s in segments)
    finally:
        await store.close()


async def test_check_segment_integrity_healthy_segment_stays_ok(tmp_path: Path):
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
        closed = await store.manifest.get_active_segment()
        await store.rotate()
        ok = await store.check_segment_integrity(closed.segment_id)
        assert ok is True
        seg = await store.manifest.get_segment(closed.segment_id)
        assert seg.status != SEGMENT_STATUS_QUARANTINED
    finally:
        await store.close()


async def test_check_segment_integrity_missing_file_not_recreated(tmp_path: Path):
    """On-demand-Integritaetscheck auf ein geloeschtes Segment (#951, Pkt 2).

    Ein schreibendes ``aiosqlite.connect`` legte am Segmentpfad still eine leere
    DB an; ``PRAGMA integrity_check`` meldete dann ``ok``, das Manifest bewarb
    weiter die alten Zeilen, aber spaetere Queries sahen die neue DB ohne
    ``ringbuffer``-Tabelle und scheiterten. Read-only-Open (``mode=ro``) darf die
    Datei NICHT neu anlegen und muss das fehlende Segment als nicht-ok /
    quarantaeniert behandeln.
    """
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
        closed = await store.manifest.get_active_segment()
        await store.rotate()
        await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
        # Datei des geschlossenen Segments loeschen (Retention/Move-Race).
        seg_path = store._segments_dir / closed.filename
        seg_path.unlink()

        ok = await store.check_segment_integrity(closed.segment_id)
        assert ok is False
        # Keine leere Ersatz-DB wurde am Segmentpfad angelegt.
        assert not seg_path.exists()
        # Segment als fehlend/nicht-ok quarantaeniert, statt faelschlich ``ok``.
        seg = await store.manifest.get_segment(closed.segment_id)
        assert seg.status == SEGMENT_STATUS_QUARANTINED
    finally:
        await store.close()


async def test_safe_getsize_returns_zero_for_missing_file(store: SqliteSegmentStore):
    # _sidecar_size/_segment_file_size dürfen bei fehlender Datei nie werfen.
    assert store._sidecar_size("does-not-exist.sqlite", "-wal") == 0
    assert store._segment_file_size("does-not-exist.sqlite") == 0


async def test_read_segment_quarantines_when_open_fails_with_corruption(tmp_path: Path, monkeypatch):
    store, corrupt_id, _ = await _store_with_corrupt_closed_segment(tmp_path)
    try:
        # Simuliert Korruption bereits beim Öffnen der Read-Connection.
        async def _boom_open(_segment):
            raise aiosqlite.DatabaseError("database disk image is malformed")

        monkeypatch.setattr(store, "_connection_for_read", _boom_open)
        corrupt = await store.manifest.get_segment(corrupt_id)
        rows = await store._read_segment_rows(corrupt, StoreQuery(limit=10))
        assert rows is None
        seg = await store.manifest.get_segment(corrupt_id)
        assert seg.status == SEGMENT_STATUS_QUARANTINED
    finally:
        await store.close()


async def test_read_segment_reraises_non_corruption_error(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    closed = await store.manifest.get_active_segment()
    await store.rotate()
    # Ein Nicht-Korruptions-Fehler (z. B. Programmierfehler) wird NICHT als
    # Korruption maskiert, sondern propagiert; das Segment bleibt unangetastet.
    seg = await store.manifest.get_segment(closed.segment_id)
    with pytest.raises(aiosqlite.OperationalError):
        await store._quarantine_corrupt_read(seg, aiosqlite.OperationalError("no such column: bogus"))
    assert (await store.manifest.get_segment(closed.segment_id)).status != SEGMENT_STATUS_QUARANTINED


async def test_read_segment_never_quarantines_active_segment(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    active = store._active_segment
    # Selbst bei einer Korruptions-Exception wird das aktive Segment nie
    # quarantäniert (es wird nie getrimmt) → Exception propagiert.
    with pytest.raises(aiosqlite.DatabaseError):
        await store._quarantine_corrupt_read(active, aiosqlite.DatabaseError("database disk image is malformed"))
    assert (await store.manifest.get_segment(active.segment_id)).status != SEGMENT_STATUS_QUARANTINED
