"""Support package monitor diagnostics tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from obs.api.v1.support import _build_monitor_info, _sqlite_file_sizes
from obs.ringbuffer.ringbuffer import RingBuffer


class _FakeDb:
    async def fetchone(self, _query, _params):
        return {
            "value": json.dumps(
                {
                    "enabled": False,
                    "max_entries": 123,
                    "max_file_size_bytes": 456,
                    "max_age": 789,
                }
            )
        }


async def test_build_monitor_info_preserves_disabled_config():
    with (
        patch("obs.ringbuffer.ringbuffer.is_ringbuffer_enabled", return_value=False),
        patch("obs.ringbuffer.ringbuffer.get_optional_ringbuffer", return_value=None),
    ):
        monitor = await _build_monitor_info(_FakeDb())

    assert monitor["available"] is True
    assert monitor["stats"]["enabled"] is False
    assert monitor["stats"]["max_entries"] == 123
    assert monitor["stats"]["max_file_size_bytes"] == 456
    assert monitor["stats"]["max_age"] == 789
    assert monitor["recent_sample_size"] == 0
    assert monitor["recent_source_adapter_counts"] == {}
    assert monitor["recent_quality_counts"] == {}
    # No ringbuffer instance → zeroed storage files, still present in the package.
    assert monitor["storage_files"] == {"db_bytes": 0, "wal_bytes": 0, "shm_bytes": 0, "total_bytes": 0}


def test_sqlite_file_sizes_reports_db_and_wal(tmp_path: Path):
    db = tmp_path / "obs.db"
    db.write_bytes(b"d" * 1024)
    (tmp_path / "obs.db-wal").write_bytes(b"w" * 4096)

    sizes = _sqlite_file_sizes(str(db))

    assert sizes == {"db_bytes": 1024, "wal_bytes": 4096, "shm_bytes": 0, "total_bytes": 5120}


def test_sqlite_file_sizes_zero_for_memory_db():
    assert _sqlite_file_sizes(":memory:") == {"db_bytes": 0, "wal_bytes": 0, "shm_bytes": 0, "total_bytes": 0}
    assert _sqlite_file_sizes("file::memory:?cache=shared") == {"db_bytes": 0, "wal_bytes": 0, "shm_bytes": 0, "total_bytes": 0}
    assert _sqlite_file_sizes(None)["total_bytes"] == 0


def test_sqlite_file_sizes_normalizes_file_uri(tmp_path: Path):
    db = tmp_path / "obs.db"
    db.write_bytes(b"d" * 512)
    (tmp_path / "obs.db-wal").write_bytes(b"w" * 2048)

    # A SQLite file URI must be normalized to a filesystem path before statting sidecars.
    sizes = _sqlite_file_sizes(f"file:{db}?mode=rwc")

    assert sizes == {"db_bytes": 512, "wal_bytes": 2048, "shm_bytes": 0, "total_bytes": 2560}


def test_ringbuffer_disk_file_sizes_splits_db_and_wal(tmp_path: Path):
    db_path = tmp_path / "rb.db"
    db_path.write_bytes(b"r" * 2048)
    (tmp_path / "rb.db-wal").write_bytes(b"w" * 1024)
    (tmp_path / "rb.db-shm").write_bytes(b"s" * 512)

    rb = RingBuffer(storage="disk", disk_path=str(db_path))

    assert rb.disk_file_sizes() == {"db_bytes": 2048, "wal_bytes": 1024, "shm_bytes": 512, "total_bytes": 3584}


def test_ringbuffer_disk_file_sizes_zero_for_memory():
    rb = RingBuffer(storage="memory")
    assert rb.disk_file_sizes() == {"db_bytes": 0, "wal_bytes": 0, "shm_bytes": 0, "total_bytes": 0}


async def test_build_monitor_info_includes_storage_files_for_active_ringbuffer():
    fake_rb = MagicMock()
    fake_rb.stats = AsyncMock(return_value={"total": 0})
    fake_rb.query = AsyncMock(return_value=[])
    fake_rb.disk_file_sizes = MagicMock(return_value={"db_bytes": 100, "wal_bytes": 200, "shm_bytes": 0, "total_bytes": 300})

    with (
        patch("obs.ringbuffer.ringbuffer.is_ringbuffer_enabled", return_value=True),
        patch("obs.ringbuffer.ringbuffer.get_optional_ringbuffer", return_value=fake_rb),
    ):
        monitor = await _build_monitor_info(_FakeDb())

    assert monitor["available"] is True
    assert monitor["storage_files"] == {"db_bytes": 100, "wal_bytes": 200, "shm_bytes": 0, "total_bytes": 300}
