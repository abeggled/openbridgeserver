"""Manifest-Schema + CRUD + globale Event-ID (#931, harte Vorbedingung aus #922).

Das Manifest ist SQLite-Backend-intern (unter der portablen Grenze). Es hält je
Segment die Metadaten und einen prozess-/root-weiten monoton wachsenden globalen
Event-ID-Zähler, damit #932 segmentübergreifend stabil sortieren/paginieren kann.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.manifest import (
    SEGMENT_STATUS_ACTIVE,
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_CLOSED,
    Manifest,
    SegmentRecord,
)


@pytest.fixture
async def manifest(tmp_path: Path) -> Manifest:
    m = Manifest(tmp_path / "manifest.sqlite")
    await m.open()
    try:
        yield m
    finally:
        await m.close()


async def test_manifest_init_is_idempotent(tmp_path: Path):
    path = tmp_path / "manifest.sqlite"
    first = Manifest(path)
    await first.open()
    seg = await first.create_segment(filename="rb_0001.sqlite", schema_version=1)
    await first.close()

    # Zweites open() darf weder Schema neu anlegen (Fehler) noch Daten verlieren.
    second = Manifest(path)
    await second.open()
    try:
        segments = await second.list_segments()
        assert [s.filename for s in segments] == [seg.filename]
    finally:
        await second.close()


async def test_create_segment_persists_all_manifest_fields(manifest: Manifest):
    seg = await manifest.create_segment(filename="rb_0001.sqlite", schema_version=2)
    assert isinstance(seg, SegmentRecord)
    assert seg.segment_id >= 1
    assert seg.filename == "rb_0001.sqlite"
    assert seg.status == SEGMENT_STATUS_ACTIVE
    assert seg.schema_version == 2
    assert seg.row_count == 0
    assert seg.size_bytes == 0
    assert seg.created_at is not None
    assert seg.closed_at is None
    assert seg.from_ts is None
    assert seg.to_ts is None
    assert seg.integrity_status == "ok"


async def test_segment_ids_are_monotonic(manifest: Manifest):
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    b = await manifest.create_segment(filename="b.sqlite", schema_version=1)
    c = await manifest.create_segment(filename="c.sqlite", schema_version=1)
    assert a.segment_id < b.segment_id < c.segment_id


async def test_update_and_close_segment(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.update_segment_stats(
        seg.segment_id,
        row_count=10,
        size_bytes=2048,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T01:00:00.000Z",
    )
    await manifest.close_segment(seg.segment_id)
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_CLOSED
    assert reloaded.row_count == 10
    assert reloaded.size_bytes == 2048
    assert reloaded.from_ts == "2026-01-01T00:00:00.000Z"
    assert reloaded.to_ts == "2026-01-01T01:00:00.000Z"
    assert reloaded.closed_at is not None


async def test_active_segment_lookup(manifest: Manifest):
    assert await manifest.get_active_segment() is None
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    assert (await manifest.get_active_segment()).segment_id == a.segment_id
    await manifest.close_segment(a.segment_id)
    assert await manifest.get_active_segment() is None


async def test_mark_checkpoint_pending(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_checkpoint_pending(seg.segment_id)
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_CHECKPOINT_PENDING


async def test_global_event_id_is_monotonic_and_persistent(tmp_path: Path):
    path = tmp_path / "manifest.sqlite"
    m = Manifest(path)
    await m.open()
    first_batch = [await m.next_global_event_id() for _ in range(3)]
    assert first_batch == sorted(set(first_batch))
    assert first_batch[0] >= 1
    await m.close()

    # Nach Reopen darf keine ID doppelt vergeben werden (Persistenz).
    m2 = Manifest(path)
    await m2.open()
    try:
        next_id = await m2.next_global_event_id()
        assert next_id > first_batch[-1]
    finally:
        await m2.close()


async def test_reserve_global_event_ids_returns_contiguous_block(manifest: Manifest):
    start = await manifest.reserve_global_event_ids(5)
    following = await manifest.next_global_event_id()
    # Der reservierte Block [start, start+5) darf sich nicht mit späteren IDs überschneiden.
    assert following >= start + 5
