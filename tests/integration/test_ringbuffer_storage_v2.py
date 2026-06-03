"""Storage-v2 integration tests for ringbuffer (issue #385)."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
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


async def test_file_storage_recovers_malformed_database_on_start(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-malformed-start.db"
    db_path.write_bytes(b"not a sqlite database")

    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        entries = await rb.query(q="dp-storage-v2", limit=10)
        stats = await rb.stats()

        assert [entry.new_value for entry in entries] == [1]
        assert stats["last_recovery_at"]
        assert stats["last_recovery_file_count"] == 1
        assert list(tmp_path.glob("ringbuffer-malformed-start.db.corrupt-*"))
    finally:
        await rb.stop()


async def test_file_storage_limits_quarantined_database_files(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-quarantine-limit.db"
    for index in range(5):
        (tmp_path / f"ringbuffer-quarantine-limit.db.corrupt-20260101T00000{index}000000Z").write_bytes(b"old")
    db_path.write_bytes(b"not a sqlite database")

    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        quarantined = sorted(tmp_path.glob("ringbuffer-quarantine-limit.db.corrupt-*"))

        assert len(quarantined) == 3
        assert any(path.read_bytes() == b"not a sqlite database" for path in quarantined)
    finally:
        await rb.stop()


async def test_file_storage_recovers_malformed_database_during_record(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ringbuffer-malformed-record.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        calls = {"count": 0}
        original_record_locked = rb._record_locked  # noqa: SLF001

        async def _raise_malformed_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise aiosqlite.OperationalError("database disk image is malformed")
            return await original_record_locked(*args, **kwargs)

        monkeypatch.setattr(rb, "_record_locked", _raise_malformed_once)

        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        entries = await rb.query(q="dp-storage-v2", limit=10)

        assert calls["count"] == 2
        assert [entry.new_value for entry in entries] == [1]
        assert list(tmp_path.glob("ringbuffer-malformed-record.db.corrupt-*"))
    finally:
        await rb.stop()


async def test_file_storage_recovery_retry_failure_propagates(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ringbuffer-retry-fails.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")

        async def _always_malformed(*args, **kwargs):
            raise aiosqlite.DatabaseError("database disk image is malformed")

        monkeypatch.setattr(rb, "_fetchall", _always_malformed)

        with pytest.raises(aiosqlite.DatabaseError, match="database disk image is malformed"):
            await rb.query(q="dp-storage-v2", limit=10)

        assert list(tmp_path.glob("ringbuffer-retry-fails.db.corrupt-*"))
    finally:
        await rb.stop()


async def test_memory_storage_does_not_attempt_corruption_recovery(monkeypatch):
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        async def _always_malformed(*args, **kwargs):
            raise aiosqlite.DatabaseError("database disk image is malformed")

        async def _fail_if_called(*args, **kwargs):
            raise AssertionError("memory storage should not run file recovery")

        monkeypatch.setattr(rb, "_fetchall", _always_malformed)
        monkeypatch.setattr(rb, "_recover_corrupt_storage_locked", _fail_if_called)

        with pytest.raises(aiosqlite.DatabaseError, match="database disk image is malformed"):
            await rb.query(q="dp-storage-v2", limit=10)
    finally:
        await rb.stop()


async def test_file_storage_recovers_malformed_database_during_query(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ringbuffer-malformed-query.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")

        calls = {"count": 0}
        original_fetchall = rb._fetchall  # noqa: SLF001

        async def _raise_malformed_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise aiosqlite.DatabaseError("file is not a database")
            return await original_fetchall(*args, **kwargs)

        monkeypatch.setattr(rb, "_fetchall", _raise_malformed_once)

        entries = await rb.query(q="dp-storage-v2", limit=10)

        assert calls["count"] == 2
        assert entries == []
        assert list(tmp_path.glob("ringbuffer-malformed-query.db.corrupt-*"))
    finally:
        await rb.stop()


async def test_file_storage_recovers_malformed_database_during_stats(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ringbuffer-malformed-stats.db"
    rb = RingBuffer(storage="file", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")

        original_execute = rb._conn.execute  # noqa: SLF001
        calls = {"count": 0}

        def _raise_malformed_once(sql, *args, **kwargs):
            if str(sql).startswith("SELECT COUNT(*) AS c"):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise aiosqlite.DatabaseError("SQLite integrity_check failed: bad page")
            return original_execute(sql, *args, **kwargs)

        monkeypatch.setattr(rb._conn, "execute", _raise_malformed_once)  # noqa: SLF001

        stats = await rb.stats()

        assert calls["count"] == 1
        assert stats["total"] == 0
        assert stats["file_size_bytes"] >= 0
        assert list(tmp_path.glob("ringbuffer-malformed-stats.db.corrupt-*"))
    finally:
        await rb.stop()


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


async def test_reconfigure_model_switch_recovers_malformed_file_storage(tmp_path: Path):
    db_path = tmp_path / "ringbuffer-reconfigure-malformed.db"
    db_path.write_bytes(b"not a sqlite database")
    rb = RingBuffer(storage="memory", max_entries=100, disk_path=str(db_path))
    await rb.start()
    try:
        await rb.reconfigure("file", 100)
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")

        entries = await rb.query(q="dp-storage-v2", limit=10)
        stats = await rb.stats()

        assert [entry.new_value for entry in entries] == [1]
        assert stats["storage"] == "file"
        assert list(tmp_path.glob("ringbuffer-reconfigure-malformed.db.corrupt-*"))
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
