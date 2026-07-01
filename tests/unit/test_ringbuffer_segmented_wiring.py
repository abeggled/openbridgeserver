"""Opt-in-Verdrahtung des Segment-Stores in den RingBuffer (#919).

Kerninvariante: solange ``segmented=False`` (Default), ändert sich das Verhalten
des RingBuffers in keiner Weise — der Legacy-Single-File-Pfad bleibt aktiv. Diese
Suite deckt den *eingeschalteten* Pfad ab (Konstruktion, Schreiben→Segment,
Read-back, Rotation nach ``segment_max_*``, Retention nach Rotation, Legacy-DB
read-only attach + gemischte Ordnung, Stats mit Segmentzahl) sowie die bewusst
deklariert-unsupported query_v2-Features (ValueError → 422 im API-Layer).

Die Flag-AUS-Regression selbst wird durch die unveränderten bestehenden
Ringbuffer-Unit-Tests abgesichert; hier wird zusätzlich explizit geprüft, dass im
Default kein Store gebaut wird.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-seg", adapter: str = "api") -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter=adapter,
        quality="good",
        metadata_version=1,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Default (Flag AUS): kein Store, unveränderter Legacy-Pfad
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_is_not_segmented_and_builds_no_store(tmp_path: Path):
    rb = _rb(tmp_path)
    assert rb.segmented is False
    await rb.start()
    try:
        assert rb.store is None
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        entries = await rb.query_v2()
        assert [e.new_value for e in entries] == [1]
        stats = await rb.stats()
        assert "store" not in stats
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Schreiben → Segment, Read-back über den Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_query_returns_empty_when_not_started(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    # Kein start() → kein Store; Query darf nicht crashen, sondern liefert [].
    assert await rb.query_v2() == []


@pytest.mark.asyncio
async def test_segmented_write_goes_to_store_and_reads_back(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        assert rb.store is not None
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        entries = await rb.query_v2(limit=10)
        # newest-first
        assert [e.new_value for e in entries] == [2, 1, 0]
        # Store trägt die Zeilen, nicht die Legacy-Connection.
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["total"] == 3
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Rotation nach segment_max_rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_rows(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        for value in range(5):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        # 5 rows, rotate every 2 → mehrere Segmente.
        assert store_stats["common"]["segment_count"] >= 2
        # Read-back bleibt korrekt segmentübergreifend geordnet.
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [4, 3, 2, 1, 0]
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: enforce_retention nach Rotation (max_entries segmentgenau)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_retention_drops_closed_segments(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2, max_entries=6)
    await rb.start()
    try:
        for value in range(10):
            await _record(rb, value, f"2026-01-01T00:00:{value:02d}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        # Retention hält segmentgenau unter/nahe max_entries — jedenfalls < 10.
        assert store_stats["common"]["total"] < 10
        # Das jüngste Event bleibt erhalten.
        entries = await rb.query_v2(limit=1)
        assert entries[0].new_value == 9
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Legacy-DB beim Start read-only attached; gemischte Ordnung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_attaches_legacy_db_and_merges_ordered(tmp_path: Path):
    disk_path = tmp_path / "obs_ringbuffer.db"
    # 1) Legacy-Single-File-RingBuffer befüllen und schließen.
    legacy = RingBuffer(storage="file", disk_path=str(disk_path))
    await legacy.start()
    for value in range(3):
        await legacy.record(
            ts=f"2025-01-01T00:00:0{value}.000Z",
            datapoint_id="dp-seg",
            topic="dp/dp-seg/value",
            old_value=None,
            new_value=100 + value,
            source_adapter="api",
            quality="good",
        )
    await legacy.stop()

    # 2) Segmentierter RingBuffer auf demselben disk_path: Legacy read-only attach.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb.start()
    try:
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")
        entries = await rb.query_v2(limit=10)
        values = [e.new_value for e in entries]
        # Neuer v2-Wert zuerst, danach die Legacy-Werte (newest-first).
        assert values[0] == 200
        assert set(values) == {200, 102, 101, 100}
        assert values == [200, 102, 101, 100]
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Stats zeigen Segmentzahl / aktives Segment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_stats_expose_segment_info(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        stats = await rb.stats()
        assert stats["storage"] == "file"
        assert "store" in stats
        assert stats["store"]["common"]["segment_count"] >= 2
        assert stats["store"]["backend_extra"]["active_segment_id"] is not None
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: deklariert-unsupported query_v2-Features → ValueError (422-tauglich)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"q": "foo"},
        {"dp_ids_by_name": ["dp-a"]},
        {"adapter_any_of": ["a", "b"]},
        {"datapoint_ids": ["dp-a", "dp-b"]},
        {"metadata_tags_any_of": ["t1"]},
        {"metadata_group_addresses_any_of": ["1/2/3"]},
        {"sort_field": "ts"},
        {"sort_order": "asc"},
    ],
)
async def test_segmented_query_unsupported_features_raise(tmp_path: Path, kwargs: dict):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match="segmented"):
            await rb.query_v2(**kwargs)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_bytes(tmp_path: Path):
    # Sehr kleines Byte-Budget → Rotation greift schon nach wenigen Zeilen.
    rb = _rb(tmp_path, segmented=True, segment_max_bytes=1)
    await rb.start()
    try:
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["segment_count"] >= 2
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_age(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_age=3600)
    await rb.start()
    try:
        # Aktives Segment künstlich altern lassen → nächster Write rotiert.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        await _record(rb, 2, "2026-01-01T00:00:01.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["segment_count"] >= 2
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_rejects_invalid_pagination_and_time(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await rb.query_v2(limit=0)
        with pytest.raises(ValueError, match="offset must be >= 0"):
            await rb.query_v2(offset=-1)
        with pytest.raises(ValueError, match="effective 'from' must be earlier"):
            await rb.query_v2(from_ts="2026-01-01T00:00:10.000Z", to_ts="2026-01-01T00:00:00.000Z")
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_handle_value_event_records_to_store(tmp_path: Path):
    from datetime import UTC, datetime

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:

        class _Evt:
            datapoint_id = "dp-evt"
            value = 42
            source_adapter = "api"
            quality = "good"
            ts = datetime(2026, 1, 1, tzinfo=UTC)

        await rb.handle_value_event(_Evt())
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [42]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_supports_core_filters(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z", datapoint_id="dp-a", adapter="api")
        await _record(rb, 2, "2026-01-01T00:00:01.000Z", datapoint_id="dp-b", adapter="knx")
        # Single datapoint_id + single adapter + time window: unterstützt.
        entries = await rb.query_v2(datapoint_ids=["dp-a"], limit=10)
        assert [e.new_value for e in entries] == [1]
        entries = await rb.query_v2(adapter_any_of=["knx"], limit=10)
        assert [e.new_value for e in entries] == [2]
        # value_filter (Kernfeld) wird an den Store gepusht.
        entries = await rb.query_v2(value_filters=[{"operator": "gte", "value": 2}], limit=10)
        assert [e.new_value for e in entries] == [2]
    finally:
        await rb.stop()
