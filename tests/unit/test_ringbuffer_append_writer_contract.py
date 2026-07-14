"""Contracts for the ringbuffer write model used by issue #919 planning."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


async def _record_value(rb: RingBuffer, value: int, ts: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id="dp-writer-contract",
        topic="dp/dp-writer-contract/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={
            "datapoint": {"tags": ["contract"]},
            "bindings": [
                {
                    "adapter_type": "KNX",
                    "adapter_instance_id": "inst-1",
                    "normalized": {"group_address": "1/2/3"},
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_record_path_is_append_only_when_retention_is_disabled():
    rb = RingBuffer(storage="memory", max_entries=None, max_file_size_bytes=None, max_age=None)
    await rb.start()
    try:
        assert rb._conn is not None  # noqa: SLF001
        statements: list[str] = []
        original_execute = rb._conn.execute  # noqa: SLF001
        original_executemany = rb._conn.executemany  # noqa: SLF001

        def _record_execute(sql: str, parameters: Any = None):
            statements.append(" ".join(sql.strip().split()).upper())
            return original_execute(sql, parameters)

        def _record_executemany(sql: str, parameters: Any = None):
            statements.append(" ".join(sql.strip().split()).upper())
            return original_executemany(sql, parameters)

        rb._conn.execute = _record_execute  # type: ignore[method-assign]  # noqa: SLF001
        rb._conn.executemany = _record_executemany  # type: ignore[method-assign]  # noqa: SLF001

        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")

        assert any(statement.startswith("INSERT INTO RINGBUFFER ") for statement in statements)
        assert any(statement.startswith("INSERT OR IGNORE INTO RINGBUFFER_METADATA_TAGS ") for statement in statements)
        assert any(statement.startswith("INSERT INTO RINGBUFFER_METADATA_BINDINGS ") for statement in statements)
        assert not any(statement.startswith(("DELETE ", "UPDATE ", "REPLACE ", "VACUUM", "PRAGMA WAL_CHECKPOINT")) for statement in statements)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_concurrent_record_calls_are_serialized_by_single_instance_writer_lock():
    rb = RingBuffer(storage="memory", max_entries=None)
    await rb.start()
    try:
        original_record_locked = rb._record_locked  # noqa: SLF001
        active_writers = 0
        max_active_writers = 0

        async def _record_locked_with_probe(*args: Any, **kwargs: Any) -> None:
            nonlocal active_writers, max_active_writers
            active_writers += 1
            max_active_writers = max(max_active_writers, active_writers)
            try:
                await asyncio.sleep(0.01)
                await original_record_locked(*args, **kwargs)
            finally:
                active_writers -= 1

        rb._record_locked = _record_locked_with_probe  # type: ignore[method-assign]  # noqa: SLF001

        await asyncio.gather(*[_record_value(rb, value, f"2026-01-01T00:00:0{value}.000Z") for value in range(5)])

        assert max_active_writers == 1
        assert (await rb.stats())["total"] == 5
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_file_storage_uses_wal_journal_mode_for_reader_writer_concurrency(tmp_path: Path):
    rb = RingBuffer(
        storage="file",
        max_entries=None,
        disk_path=str(tmp_path / "writer-contract.db"),
    )
    await rb.start()
    try:
        assert rb._conn is not None  # noqa: SLF001
        async with rb._conn.execute("PRAGMA journal_mode") as cur:  # noqa: SLF001
            row = await cur.fetchone()

        assert row is not None
        assert str(row[0]).lower() == "wal"
    finally:
        await rb.stop()
