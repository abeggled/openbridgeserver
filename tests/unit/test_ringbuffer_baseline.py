"""Baseline characterization tests for ringbuffer behavior.

Scope for issue #383:
- count-based trim behavior (oldest entries are removed first)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


@pytest.mark.asyncio
async def test_ringbuffer_trim_is_count_based_and_drops_oldest_first():
    rb = RingBuffer(storage="memory", max_entries=3)
    await rb.start()
    try:
        for value in range(5):
            await rb.record(
                ts=f"2026-01-01T00:00:0{value}.000Z",
                datapoint_id="dp-trim-baseline",
                topic="dp/dp-trim-baseline/value",
                old_value=value - 1 if value > 0 else None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )

        entries = await rb.query(q="dp-trim-baseline", limit=10)
        stats = await rb.stats()

        assert stats["total"] == 3
        assert len(entries) == 3
        # Query returns newest-first; values 0 and 1 are trimmed away.
        assert [entry.new_value for entry in entries] == [4, 3, 2]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_ringbuffer_no_trim_when_count_equals_limit():
    rb = RingBuffer(storage="memory", max_entries=3)
    await rb.start()
    try:
        for value in range(3):
            await rb.record(
                ts=f"2026-01-01T00:00:0{value}.000Z",
                datapoint_id="dp-trim-eq-limit",
                topic="dp/dp-trim-eq-limit/value",
                old_value=value - 1 if value > 0 else None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )

        entries = await rb.query(q="dp-trim-eq-limit", limit=10)
        assert len(entries) == 3
        assert [entry.new_value for entry in entries] == [2, 1, 0]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_ringbuffer_limit_one_keeps_only_latest_entry():
    rb = RingBuffer(storage="memory", max_entries=1)
    await rb.start()
    try:
        for value in (10, 20, 30):
            await rb.record(
                ts=f"2026-01-01T00:00:{value}.000Z",
                datapoint_id="dp-limit-one",
                topic="dp/dp-limit-one/value",
                old_value=None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )

        entries = await rb.query(q="dp-limit-one", limit=10)
        assert len(entries) == 1
        assert entries[0].new_value == 30
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_ringbuffer_stats_include_effective_retention_seconds():
    rb = RingBuffer(storage="memory", max_entries=10)
    await rb.start()
    try:
        oldest = datetime.now(UTC) - timedelta(seconds=95)
        await rb.record(
            ts=oldest.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            datapoint_id="dp-effective-retention",
            topic="dp/dp-effective-retention/value",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
        )

        stats = await rb.stats()
        assert "effective_retention_seconds" in stats
        assert isinstance(stats["effective_retention_seconds"], int)
        assert stats["effective_retention_seconds"] >= 90
    finally:
        await rb.stop()
