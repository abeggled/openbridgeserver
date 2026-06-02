"""Storage-v2 integration tests for ringbuffer (issue #385)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer

pytestmark = pytest.mark.integration


async def _record_value(rb: RingBuffer, value: int, ts: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id="dp-storage-v2",
        topic="dp/dp-storage-v2/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


async def test_file_storage_restart_persists_entries(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-restart.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
    finally:
        await rb.stop()

    rb2 = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb2.start()
    try:
        entries = await rb2.query(q="dp-storage-v2", limit=10)
        assert [entry.new_value for entry in entries] == [1]
    finally:
        await rb2.stop()


async def test_reconfigure_model_switch_restarts_empty_without_migration(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-model-switch.db"
    rb = RingBuffer(storage="disk", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        await _record_value(rb, 2, "2026-01-01T00:00:01.000Z")

        # Storage v2 requirement: model switch must not migrate existing entries.
        await rb.reconfigure("file", 100)

        entries = await rb.query(q="dp-storage-v2", limit=10)
        assert entries == []
    finally:
        await rb.stop()


async def test_reconfigure_same_model_keeps_entries(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-same-model.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        await _record_value(rb, 2, "2026-01-01T00:00:01.000Z")

        await rb.reconfigure("file", 100, max_file_size_bytes=1_000_000, max_age=60)

        entries = await rb.query(q="dp-storage-v2", limit=10)
        assert [entry.new_value for entry in entries] == [2, 1]
        stats = await rb.stats()
        assert stats["max_file_size_bytes"] == 1_000_000
        assert stats["max_age"] == 60
    finally:
        await rb.stop()
