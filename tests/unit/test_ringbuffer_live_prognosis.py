"""Randfälle der vorläufigen Live-Prognose vor dem ersten regulären Segment."""

from pathlib import Path

import pytest

import obs.ringbuffer.store.sqlite_backend as backend
from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: int, second: int = 0) -> StoreEvent:
    return StoreEvent(
        ts=f"2026-01-01T00:00:{second:02d}.000Z",
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="knx",
        quality="good",
    )


@pytest.mark.asyncio
async def test_live_prognosis_starts_after_five_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(backend, "_monotonic", lambda: clock["now"], raising=False)
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1), _event(2, 1)])

        clock["now"] = 104.0
        warming = (await store.stats()).common["prognosis"]
        assert warming["source"] == "active"
        assert warming["provisional"] is True
        assert warming["ready_after_seconds"] == pytest.approx(1.0)
        assert warming["rows_per_hour"] is None

        clock["now"] = 105.0
        ready = (await store.stats()).common["prognosis"]
        assert ready["source"] == "active"
        assert ready["provisional"] is True
        assert ready["observed_seconds"] == pytest.approx(5.0)
        assert ready["observed_rows"] == 2
        assert ready["rows_per_hour"] == pytest.approx(2 / 5 * 3600)
        assert ready["ready_after_seconds"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_live_prognosis_reports_idle_instead_of_zero_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = {"now": 200.0}
    monkeypatch.setattr(backend, "_monotonic", lambda: clock["now"], raising=False)
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        clock["now"] = 205.5
        prognosis = (await store.stats()).common["prognosis"]
        assert prognosis["source"] == "active"
        assert prognosis["idle_seconds"] == pytest.approx(5.5)
        assert prognosis["rows_per_hour"] is None
        assert prognosis["bytes_per_hour"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_live_size_rate_recovers_after_apparent_wal_shrink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = {"now": 300.0}
    monkeypatch.setattr(backend, "_monotonic", lambda: clock["now"], raising=False)
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        sizes = {"now": 1}
        monkeypatch.setattr(store, "_segment_file_size", lambda _filename: sizes["now"])
        clock["now"] = 306.0
        await store.append([_event(1)])
        after_shrink = (await store.stats()).common["prognosis"]
        assert after_shrink["rows_per_hour"] is not None
        assert after_shrink["bytes_per_hour"] is None

        sizes["now"] = 1001
        clock["now"] = 312.0
        await store.append([_event(2, 1)])
        recovered = (await store.stats()).common["prognosis"]
        assert recovered["bytes_per_hour"] == pytest.approx(1000 / 6 * 3600)
        assert recovered["rows_per_hour"] == pytest.approx(2 / 12 * 3600)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_closed_regular_segment_replaces_provisional_live_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = {"now": 400.0}
    monkeypatch.setattr(backend, "_monotonic", lambda: clock["now"], raising=False)
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event(1, 0), _event(2, 10)])
        clock["now"] = 406.0
        assert (await store.stats()).common["prognosis"]["source"] == "active"

        await store.rotate()
        stable = (await store.stats()).common["prognosis"]
        assert stable["source"] == "closed"
        assert stable["provisional"] is False
        assert stable["sample_segment_count"] == 1
        assert stable["rows_per_hour"] == pytest.approx(2 / 10 * 3600)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restart_baseline_does_not_count_existing_active_rows_as_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = {"now": 500.0}
    monkeypatch.setattr(backend, "_monotonic", lambda: clock["now"], raising=False)
    root = tmp_path / "root"
    first = SqliteSegmentStore(root)
    await first.open()
    await first.append([_event(1), _event(2, 1)])
    await first.close()

    clock["now"] = 600.0
    restarted = SqliteSegmentStore(root)
    await restarted.open()
    try:
        clock["now"] = 606.0
        await restarted.append([_event(3, 2)])
        prognosis = (await restarted.stats()).common["prognosis"]
        assert prognosis["source"] == "active"
        assert prognosis["observed_rows"] == 1
        assert prognosis["rows_per_hour"] == pytest.approx(1 / 6 * 3600)
    finally:
        await restarted.close()
