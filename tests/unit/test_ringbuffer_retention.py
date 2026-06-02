"""Retention tests for ringbuffer (issue #384)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


async def _record_value(rb: RingBuffer, value: int, ts: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id="dp-retention",
        topic="dp/dp-retention/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


@pytest.mark.asyncio
async def test_size_trim_boundary_equal_limit_keeps_entries(tmp_path: Path):
    rb = RingBuffer(
        storage="disk",
        max_entries=100,
        disk_path=str(tmp_path / "rb-size-eq.db"),
    )
    await rb.start()
    try:
        for i in range(3):
            await _record_value(rb, i, f"2026-01-01T00:00:0{i}.000Z")

        rb._max_file_size_bytes = 100  # noqa: SLF001

        async def _fake_size() -> int:
            return 100

        rb._current_storage_bytes = _fake_size  # type: ignore[method-assign]
        await rb._trim(reference_ts="2026-01-01T00:00:10.000Z")

        entries = await rb.query(q="dp-retention", limit=10)
        assert [entry.new_value for entry in entries] == [2, 1, 0]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_size_trim_boundary_above_limit_drops_oldest_first(tmp_path: Path):
    rb = RingBuffer(
        storage="disk",
        max_entries=100,
        disk_path=str(tmp_path / "rb-size-gt.db"),
    )
    await rb.start()
    try:
        for i in range(3):
            await _record_value(rb, i, f"2026-01-01T00:00:0{i}.000Z")

        rb._max_file_size_bytes = 100  # noqa: SLF001

        async def _fake_size() -> int:
            if not hasattr(_fake_size, "_first"):
                _fake_size._first = True  # type: ignore[attr-defined]
                return 101
            return 100

        rb._current_storage_bytes = _fake_size  # type: ignore[method-assign]
        await rb._trim(reference_ts="2026-01-01T00:00:10.000Z")

        entries = await rb.query(q="dp-retention", limit=10)
        assert [entry.new_value for entry in entries] == [2, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_age_trim_boundary_equal_limit_keeps_entries():
    rb = RingBuffer(storage="memory", max_entries=100, max_age=10)
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        await rb._trim(reference_ts="2026-01-01T00:00:10.000Z")

        entries = await rb.query(q="dp-retention", limit=10)
        assert [entry.new_value for entry in entries] == [1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_age_trim_boundary_above_limit_removes_older_entries():
    rb = RingBuffer(storage="memory", max_entries=100, max_age=10)
    await rb.start()
    try:
        await _record_value(rb, 0, "2026-01-01T00:00:00.000Z")
        await _record_value(rb, 1, "2026-01-01T00:00:05.000Z")
        await _record_value(rb, 2, "2026-01-01T00:00:20.000Z")

        await rb._trim(reference_ts="2026-01-01T00:00:20.000Z")

        entries = await rb.query(q="dp-retention", limit=10)
        assert [entry.new_value for entry in entries] == [2]
    finally:
        await rb.stop()
