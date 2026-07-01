"""SQLite-Segment-Backend: implementiert den portablen RingBufferStore (#931).

Deckt ab: genau ein aktives Segment, Rotation öffnet genau ein neues aktives
(löscht nie Daten), globale Event-ID monoton über Rotation hinweg, zweiter
Writer auf derselben Root fail-fast, Checkpoint-busy → checkpoint_pending,
Capability-Deskriptor, stats() mit common/backend_extra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig
from obs.ringbuffer.store.interface import (
    OrderingGuarantee,
    RingBufferStore,
    StoreEvent,
    StoreQuery,
)
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_CHECKPOINT_PENDING
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
    # Typed pushdown und Streaming-Export sind Welle-2 (#933/#932) → noch nicht nativ.
    assert caps.supports_typed_pushdown is False
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


async def test_stats_split_common_and_backend_extra(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    stats = await store.stats()
    assert stats.common["total"] == 1
    assert stats.common["segment_count"] >= 1
    # SQLite-Interna nur unter backend_extra.
    assert "active_segment_id" in stats.backend_extra
    assert "wal_size_bytes" not in stats.common


async def test_enforce_retention_returns_zero_stub(store: SqliteSegmentStore):
    # Welle-2 (#936) fuellt die eigentliche Segment-Loeschung; hier nur die Naht.
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
