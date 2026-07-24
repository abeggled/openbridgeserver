"""Retention tests for ringbuffer (issue #384)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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

        rb._max_file_size_bytes = 100

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

        rb._max_file_size_bytes = 100

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


@pytest.mark.asyncio
async def test_metadata_bindings_entry_id_is_indexed_for_cascade_deletes(tmp_path: Path):
    rb = RingBuffer(
        storage="file",
        max_entries=None,
        disk_path=str(tmp_path / "rb-metadata-index.db"),
    )
    await rb.start()
    try:
        assert rb._conn is not None

        async with rb._conn.execute("PRAGMA index_list('ringbuffer_metadata_bindings')") as cur:
            index_rows = await cur.fetchall()

        indexed_columns: list[tuple[str, ...]] = []
        for row in index_rows:
            index_name = row["name"]
            async with rb._conn.execute(f"PRAGMA index_info('{index_name}')") as cur:
                columns = tuple(info["name"] for info in await cur.fetchall())
            indexed_columns.append(columns)

        assert any(columns[:1] == ("entry_id",) for columns in indexed_columns)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_existing_ringbuffer_database_gets_metadata_binding_entry_id_index(tmp_path: Path):
    db_path = tmp_path / "rb-legacy-metadata-index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE ringbuffer (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ts             TEXT    NOT NULL,
                datapoint_id   TEXT    NOT NULL,
                topic          TEXT    NOT NULL,
                old_value      TEXT,
                new_value      TEXT,
                source_adapter TEXT    NOT NULL,
                quality        TEXT    NOT NULL,
                metadata_version INTEGER NOT NULL DEFAULT 1,
                metadata       TEXT    NOT NULL DEFAULT '{}'
            );
            CREATE TABLE ringbuffer_metadata_bindings (
                entry_id             INTEGER NOT NULL REFERENCES ringbuffer(id) ON DELETE CASCADE,
                adapter_type         TEXT    NOT NULL DEFAULT '',
                adapter_instance_id  TEXT    NOT NULL DEFAULT '',
                group_address        TEXT    NOT NULL DEFAULT '',
                topic                TEXT    NOT NULL DEFAULT '',
                entity_id            TEXT    NOT NULL DEFAULT '',
                register_type        TEXT    NOT NULL DEFAULT '',
                register_address     TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX idx_rb_meta_bind_adapter_type ON ringbuffer_metadata_bindings(adapter_type);
            """
        )
        conn.commit()
    finally:
        conn.close()

    rb = RingBuffer(
        storage="file",
        max_entries=None,
        disk_path=str(db_path),
    )
    await rb.start()
    try:
        assert rb._conn is not None
        async with rb._conn.execute("PRAGMA index_list('ringbuffer_metadata_bindings')") as cur:
            indexes = {row["name"] for row in await cur.fetchall()}

        assert "idx_rb_meta_bind_entry_id" in indexes
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_large_count_trim_uses_batched_delete_instead_of_one_huge_in_clause(tmp_path: Path):
    rb = RingBuffer(
        storage="file",
        max_entries=None,
        disk_path=str(tmp_path / "rb-large-trim.db"),
    )
    await rb.start()
    try:
        assert rb._conn is not None
        for value in range(1200):
            await _record_value(rb, value, f"2026-01-01T00:{value // 60:02d}:{value % 60:02d}.000Z")

        delete_param_counts: list[int] = []
        original_execute = rb._conn.execute

        def _record_delete_shape(sql: str, parameters: Any = None):
            if sql.strip().upper().startswith("DELETE FROM RINGBUFFER"):
                delete_param_counts.append(len(parameters or ()))
            return original_execute(sql, parameters)

        rb._conn.execute = _record_delete_shape  # type: ignore[method-assign]

        await rb.reconfigure("file", max_entries=50)

        assert len(delete_param_counts) > 1
        assert max(delete_param_counts) <= 1
        stats = await rb.stats()
        assert stats["total"] == 50
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_delete_oldest_returns_zero_for_invalid_limit_and_empty_buffer():
    rb = RingBuffer(storage="memory", max_entries=None)
    await rb.start()
    try:
        assert await rb._delete_oldest(0) == 0
        assert await rb._delete_oldest(10) == 0
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_delete_oldest_uses_sqlite_changes_when_rowcount_unavailable():
    rb = RingBuffer(storage="memory", max_entries=None)
    await rb.start()
    try:
        assert rb._conn is not None
        for value in range(3):
            await _record_value(rb, value, f"2026-01-01T00:00:0{value}.000Z")

        original_execute = rb._conn.execute

        class CursorWithoutRowcount:
            def __init__(self, cursor: Any) -> None:
                self._cursor = cursor

            @property
            def rowcount(self) -> int:
                return -1

            async def close(self) -> None:
                await self._cursor.close()

        class ExecuteWithoutDeleteRowcount:
            def __init__(self, result: Any) -> None:
                self._result = result

            def __await__(self):
                async def _await_cursor():
                    cursor = await self._result
                    return CursorWithoutRowcount(cursor)

                return _await_cursor().__await__()

        def _hide_delete_rowcount(sql: str, parameters: Any = None):
            result = original_execute(sql, parameters)
            if sql.strip().upper().startswith("DELETE FROM RINGBUFFER"):
                return ExecuteWithoutDeleteRowcount(result)
            return result

        rb._conn.execute = _hide_delete_rowcount  # type: ignore[method-assign]

        assert await rb._delete_oldest(2) == 2
        entries = await rb.query(q="dp-retention", limit=10)
        assert [entry.new_value for entry in entries] == [2]
    finally:
        await rb.stop()
