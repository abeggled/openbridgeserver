"""Integration retention tests for ringbuffer (issue #384)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer

pytestmark = pytest.mark.integration


async def _record_payload(rb: RingBuffer, seq: int, ts: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id="dp-retention-int",
        topic="dp/dp-retention-int/value",
        old_value=None,
        new_value={"seq": seq, "payload": "x" * 700},
        source_adapter="api",
        quality="good",
    )


async def test_retention_trims_by_size_on_disk(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-size.db"
    rb = RingBuffer(storage="disk", max_entries=10_000, disk_path=str(db_path))
    await rb.start()
    try:
        baseline_size = await rb._current_storage_bytes()  # noqa: SLF001

        for i in range(25):
            await _record_payload(rb, i, f"2026-01-01T00:00:{i:02d}.000Z")

        before_entries = await rb.query(q="dp-retention-int", limit=100)
        assert len(before_entries) == 25
        before_size = await rb._current_storage_bytes()  # noqa: SLF001

        limit = max(baseline_size + 4096, before_size - 8192)
        await rb.reconfigure("disk", 10_000, max_file_size_bytes=limit)

        entries = await rb.query(q="dp-retention-int", limit=100)
        assert entries
        seqs_desc = [entry.new_value["seq"] for entry in entries]

        assert len(entries) < len(before_entries)
        assert seqs_desc[0] == 24

        seqs_asc = sorted(seqs_desc)
        assert seqs_asc == list(range(seqs_asc[0], 25))
        assert await rb._current_storage_bytes() <= limit  # noqa: SLF001
    finally:
        await rb.stop()


async def test_retention_trims_by_age():
    rb = RingBuffer(storage="memory", max_entries=10_000, max_age=5)
    await rb.start()
    try:
        await rb.record(
            ts="2026-01-01T00:00:00.000Z",
            datapoint_id="dp-age-int",
            topic="dp/dp-age-int/value",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
        )
        await rb.record(
            ts="2026-01-01T00:00:06.000Z",
            datapoint_id="dp-age-int",
            topic="dp/dp-age-int/value",
            old_value=1,
            new_value=2,
            source_adapter="api",
            quality="good",
        )

        entries = await rb.query(q="dp-age-int", limit=10)
        assert [entry.new_value for entry in entries] == [2]
    finally:
        await rb.stop()
