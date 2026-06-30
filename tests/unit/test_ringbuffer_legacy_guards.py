"""Coverage tests for ringbuffer guard and fallback paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from obs.ringbuffer import ringbuffer as rb_mod
from obs.ringbuffer.ringbuffer import RingBuffer, _is_sqlite_corruption, _safe_loads, get_ringbuffer, reset_ringbuffer


@pytest.fixture(autouse=True)
async def _cleanup_ringbuffer_singleton():
    rb = rb_mod.get_optional_ringbuffer()
    if rb is not None:
        await rb.stop()
    rb_mod.reset_ringbuffer()
    try:
        yield
    finally:
        rb = rb_mod.get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        rb_mod.reset_ringbuffer()


@pytest.mark.asyncio
async def test_constructor_and_reconfigure_reject_invalid_storage():
    with pytest.raises(ValueError, match="storage must be one of: file, disk, memory"):
        RingBuffer(storage="invalid", max_entries=10)

    rb = RingBuffer(storage="memory", max_entries=10)
    await rb.start()
    try:
        with pytest.raises(ValueError, match="storage must be one of: file, disk, memory"):
            await rb.reconfigure("invalid", 10)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_noop_returns_without_trimming(monkeypatch):
    rb = RingBuffer(storage="memory", max_entries=5)
    await rb.start()
    try:
        called = {"trim": 0}

        async def _trim_stub(*args, **kwargs):
            called["trim"] += 1

        monkeypatch.setattr(rb, "_trim", _trim_stub)
        await rb.reconfigure("memory", 5)
        assert called["trim"] == 0
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_guard_paths_when_connection_is_missing():
    rb = RingBuffer(storage="memory", max_entries=5)

    await rb.record(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-guard",
        topic="dp/dp-guard/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
    )
    await rb._trim(reference_ts="2026-01-01T00:00:00.000Z")  # noqa: SLF001
    assert await rb._trim_by_count() == 0  # noqa: SLF001
    assert await rb._delete_oldest(0) == 0  # noqa: SLF001
    assert await rb._count_entries() == 0  # noqa: SLF001
    stats = await rb.stats()
    assert stats["total"] == 0


@pytest.mark.asyncio
async def test_trim_by_age_without_reference_on_empty_buffer_returns_zero():
    rb = RingBuffer(storage="memory", max_entries=5, max_age=10)
    await rb.start()
    try:
        assert await rb._trim_by_age(reference_ts=None) == 0  # noqa: SLF001
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_allows_disabling_count_limit_with_null_max_entries():
    rb = RingBuffer(storage="memory", max_entries=2)
    await rb.start()
    try:
        await rb.reconfigure("memory", None)
        for value in range(4):
            await rb.record(
                ts=f"2026-01-01T00:00:0{value}.000Z",
                datapoint_id="dp-no-count-limit",
                topic="dp/dp-no-count-limit/value",
                old_value=None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )
        entries = await rb.query(q="dp-no-count-limit", limit=10)
        stats = await rb.stats()
        assert len(entries) == 4
        assert stats["max_entries"] is None
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_disabled_ringbuffer_skips_records():
    rb = RingBuffer(storage="memory", max_entries=5)
    await rb.start()
    rb_mod.set_ringbuffer_enabled(False)
    try:
        await rb.record(
            ts="2026-01-01T00:00:00.000Z",
            datapoint_id="dp-disabled",
            topic="dp/dp-disabled/value",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
        )
        assert await rb.query(q="dp-disabled", limit=10) == []
        assert (await rb.stats())["total"] == 0
    finally:
        rb_mod.set_ringbuffer_enabled(True)
        await rb.stop()


@pytest.mark.asyncio
async def test_disabled_ringbuffer_skips_event_bus_handler(monkeypatch):
    rb = RingBuffer(storage="memory", max_entries=5)
    await rb.start()
    rb_mod.set_ringbuffer_enabled(False)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: pytest.fail("registry should not be loaded"))
    try:
        event = SimpleNamespace(
            datapoint_id=uuid4(),
            ts=datetime.now(UTC),
            value=42,
            source_adapter="api",
            quality="good",
        )
        await rb.handle_value_event(event)
        assert await rb.query(limit=5) == []
    finally:
        rb_mod.set_ringbuffer_enabled(True)
        await rb.stop()


@pytest.mark.asyncio
async def test_handle_value_event_falls_back_to_default_topic_when_registry_unavailable(monkeypatch):
    rb = RingBuffer(storage="memory", max_entries=5)
    await rb.start()
    try:

        def _raise_runtime_error():
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr("obs.core.registry.get_registry", _raise_runtime_error)

        event = SimpleNamespace(
            datapoint_id=uuid4(),
            ts=datetime.now(UTC),
            value=42,
            source_adapter="api",
            quality="good",
        )
        await rb.handle_value_event(event)

        rows = await rb.query(limit=5)
        assert rows
        assert rows[0].topic == f"dp/{event.datapoint_id}/value"
    finally:
        await rb.stop()


def test_safe_loads_and_singleton_guard_paths():
    assert _safe_loads(None) is None
    raw = "not-json"
    assert _safe_loads(raw) == raw

    reset_ringbuffer()
    with pytest.raises(RuntimeError, match="RingBuffer not initialized"):
        get_ringbuffer()

    # Keep explicit module reference path covered as well.
    rb_mod.reset_ringbuffer()
    with pytest.raises(RuntimeError, match="RingBuffer not initialized"):
        rb_mod.get_ringbuffer()


@pytest.mark.asyncio
async def test_init_ringbuffer_start_failure_keeps_disabled_state(monkeypatch, tmp_path):
    rb_mod.set_ringbuffer_enabled(False)

    async def _fail_start(self):  # noqa: ARG001
        raise OSError("cannot open ringbuffer")

    monkeypatch.setattr(rb_mod.RingBuffer, "start", _fail_start)

    with pytest.raises(OSError, match="cannot open ringbuffer"):
        await rb_mod.init_ringbuffer("file", 10, str(tmp_path / "obs_ringbuffer.db"))

    assert rb_mod.is_ringbuffer_enabled() is False
    assert rb_mod.get_optional_ringbuffer() is None


def test_sqlite_corruption_detector_only_matches_sqlite_corruption_errors():
    assert _is_sqlite_corruption(rb_mod.aiosqlite.OperationalError("database disk image is malformed")) is True
    assert _is_sqlite_corruption(rb_mod.aiosqlite.DatabaseError("file is not a database")) is True
    assert _is_sqlite_corruption(rb_mod.aiosqlite.DatabaseError("SQLite integrity_check failed: bad page")) is True
    assert _is_sqlite_corruption(rb_mod.aiosqlite.OperationalError("database is locked")) is False
    assert _is_sqlite_corruption(RuntimeError("database disk image is malformed")) is False


def test_delete_ringbuffer_storage_files_removes_sqlite_sidecars(tmp_path):
    db_path = tmp_path / "obs_ringbuffer.db"
    for path in (db_path, tmp_path / "obs_ringbuffer.db-wal", tmp_path / "obs_ringbuffer.db-shm"):
        path.write_text("x", encoding="utf-8")

    rb_mod.delete_ringbuffer_storage_files(str(db_path))

    assert not db_path.exists()
    assert not (tmp_path / "obs_ringbuffer.db-wal").exists()
    assert not (tmp_path / "obs_ringbuffer.db-shm").exists()


def test_delete_ringbuffer_storage_files_normalizes_sqlite_file_uri(tmp_path):
    db_path = tmp_path / "obs_ringbuffer.db"
    for path in (db_path, tmp_path / "obs_ringbuffer.db-wal", tmp_path / "obs_ringbuffer.db-shm"):
        path.write_text("x", encoding="utf-8")

    rb_mod.delete_ringbuffer_storage_files(f"file:{db_path}?mode=rwc")

    assert not db_path.exists()
    assert not (tmp_path / "obs_ringbuffer.db-wal").exists()
    assert not (tmp_path / "obs_ringbuffer.db-shm").exists()


def test_delete_ringbuffer_storage_files_skips_in_memory_sqlite_uri(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for path in (tmp_path / "memdb1", tmp_path / "memdb1-wal", tmp_path / "memdb1-shm"):
        path.write_text("x", encoding="utf-8")

    rb_mod.delete_ringbuffer_storage_files("file:memdb1?mode=memory&cache=shared")

    assert (tmp_path / "memdb1").exists()
    assert (tmp_path / "memdb1-wal").exists()
    assert (tmp_path / "memdb1-shm").exists()


def test_delete_ringbuffer_storage_files_restores_files_when_prepare_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "obs_ringbuffer.db"
    wal_path = tmp_path / "obs_ringbuffer.db-wal"
    shm_path = tmp_path / "obs_ringbuffer.db-shm"
    for path in (db_path, wal_path, shm_path):
        path.write_text(path.name, encoding="utf-8")

    original_replace = rb_mod.os.replace

    def fail_on_wal(src, dst):
        if str(src).endswith("-wal"):
            raise PermissionError("locked wal")
        original_replace(src, dst)

    monkeypatch.setattr(rb_mod.os, "replace", fail_on_wal)

    with pytest.raises(PermissionError, match="locked wal"):
        rb_mod.delete_ringbuffer_storage_files(str(db_path))

    assert db_path.read_text(encoding="utf-8") == "obs_ringbuffer.db"
    assert wal_path.read_text(encoding="utf-8") == "obs_ringbuffer.db-wal"
    assert shm_path.read_text(encoding="utf-8") == "obs_ringbuffer.db-shm"
    assert list(tmp_path.glob("*.deleting-*")) == []


def test_delete_ringbuffer_storage_files_restores_files_when_first_unlink_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "obs_ringbuffer.db"
    wal_path = tmp_path / "obs_ringbuffer.db-wal"
    for path in (db_path, wal_path):
        path.write_text(path.name, encoding="utf-8")

    original_remove = rb_mod.os.remove

    def fail_on_wal(path):
        if "-wal.deleting-" in str(path):
            raise PermissionError("locked wal")
        original_remove(path)

    monkeypatch.setattr(rb_mod, "uuid4", lambda: SimpleNamespace(hex="test"))
    monkeypatch.setattr(rb_mod.os, "remove", fail_on_wal)

    with pytest.raises(PermissionError, match="locked wal"):
        rb_mod.delete_ringbuffer_storage_files(str(db_path))

    assert db_path.read_text(encoding="utf-8") == "obs_ringbuffer.db"
    assert wal_path.read_text(encoding="utf-8") == "obs_ringbuffer.db-wal"
    assert list(tmp_path.glob("*.deleting-*")) == []


def test_delete_ringbuffer_storage_files_surfaces_partial_unlink_failures(tmp_path, monkeypatch):
    db_path = tmp_path / "obs_ringbuffer.db"
    wal_path = tmp_path / "obs_ringbuffer.db-wal"
    for path in (db_path, wal_path):
        path.write_text(path.name, encoding="utf-8")

    original_remove = rb_mod.os.remove

    def fail_on_db(path):
        if path.name.startswith("obs_ringbuffer.db.deleting-"):
            raise PermissionError("locked db")
        original_remove(path)

    monkeypatch.setattr(rb_mod, "uuid4", lambda: SimpleNamespace(hex="test"))
    monkeypatch.setattr(rb_mod.os, "remove", fail_on_db)

    with pytest.raises(rb_mod.RingBufferStorageDeleteIncompleteError, match="locked db"):
        rb_mod.delete_ringbuffer_storage_files(str(db_path))

    assert not wal_path.exists()
    assert not db_path.exists()
    assert len(list(tmp_path.glob("obs_ringbuffer.db.deleting-*-test"))) == 1


def test_default_ringbuffer_disk_path_never_reuses_app_database_path():
    assert rb_mod.default_ringbuffer_disk_path("/data/obs.db") == "/data/obs_ringbuffer.db"
    assert rb_mod.default_ringbuffer_disk_path("/data/obs.sqlite") == "/data/obs_ringbuffer.db"
    assert rb_mod.default_ringbuffer_disk_path("/data/obs") == "/data/obs_ringbuffer.db"


def test_default_ringbuffer_disk_path_preserves_in_memory_sqlite_paths():
    assert rb_mod.default_ringbuffer_disk_path(":memory:") == ":memory:"
    assert rb_mod.default_ringbuffer_disk_path("file::memory:?cache=shared") == "file::memory:?cache=shared"
    assert rb_mod.default_ringbuffer_disk_path("file:memdb1?mode=memory&cache=shared") == "file:memdb1?mode=memory&cache=shared"


def test_default_ringbuffer_disk_path_normalizes_sqlite_file_uris(tmp_path):
    db_path = tmp_path / "obs.db"

    assert rb_mod.default_ringbuffer_disk_path(f"file:{db_path}?mode=rwc") == str(tmp_path / "obs_ringbuffer.db")
    assert rb_mod.default_ringbuffer_disk_path(f"file://localhost{db_path}?mode=rwc") == str(tmp_path / "obs_ringbuffer.db")
