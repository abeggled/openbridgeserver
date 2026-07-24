"""WAL maintenance for the main SQLite database (issue #908).

Verifies that the main DB bounds its ``-wal`` sidecar via PRAGMAs and that an explicit
TRUNCATE checkpoint (and the background maintenance scheduler) actually shrinks the WAL
file on disk.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from obs.db.database import Database
from obs.db.maintenance import (
    DatabaseMaintenanceScheduler,
    get_db_maintenance_scheduler,
    init_db_maintenance_scheduler,
)


async def _wal_size(db_path: Path) -> int:
    wal = Path(f"{db_path}-wal")
    return wal.stat().st_size if wal.exists() else 0


async def _grow_wal(db: Database) -> None:
    """Write enough rows to force the WAL file to grow on disk."""
    await db.execute("CREATE TABLE IF NOT EXISTS blob_test (id INTEGER PRIMARY KEY, payload TEXT)")
    await db.commit()
    payload = "x" * 4096
    for _ in range(2000):
        await db.execute("INSERT INTO blob_test (payload) VALUES (?)", (payload,))
    await db.commit()


@pytest.mark.asyncio
async def test_connect_sets_wal_bounding_pragmas(tmp_path):
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    try:
        async with db.conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0].lower() == "wal"
        async with db.conn.execute("PRAGMA journal_size_limit") as cur:
            assert (await cur.fetchone())[0] == 67108864
        async with db.conn.execute("PRAGMA wal_autocheckpoint") as cur:
            assert (await cur.fetchone())[0] == 1000
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migration_v5_swallows_duplicate_adapter_instance_id_column(tmp_path):
    """Migration v5 adds ``adapter_bindings.adapter_instance_id`` and is idempotent:
    re-running it against an already-migrated schema must swallow the resulting
    ``ALTER TABLE ... ADD COLUMN`` duplicate-column ``OperationalError`` instead of
    raising, since a fresh DB already applies v5 once via the normal connect() path.
    """
    from obs.db.database import _migration_v5

    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    try:
        # connect() already ran the full migration chain (including v5) once.
        # Re-invoking it directly hits the "column already exists" branch.
        await _migration_v5(db.conn)
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_truncates_wal_file(tmp_path):
    db_path = tmp_path / "obs.db"
    db = Database(str(db_path))
    await db.connect()
    try:
        await _grow_wal(db)
        assert await _wal_size(db_path) > 0

        assert await db.checkpoint() is True
        # TRUNCATE checkpoint resets the WAL file back to (near) zero on disk.
        assert await _wal_size(db_path) == 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_noop_for_memory_db():
    db = Database(":memory:")
    await db.connect()
    try:
        assert await db.checkpoint() is False
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_run_checkpoint_skips_named_memory_uri(tmp_path, monkeypatch):
    """A named shared in-memory URI must be a no-op, not open a real on-disk file."""
    monkeypatch.chdir(tmp_path)
    db = Database("file:memdb_ckpt?mode=memory&cache=shared")
    assert await db._run_checkpoint() is False
    # The normalized name (memdb_ckpt) must not have been created as a disk database.
    assert not (tmp_path / "memdb_ckpt").exists()


@pytest.mark.asyncio
async def test_checkpoint_reports_busy_result_as_failure(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    try:
        # A TRUNCATE checkpoint blocked by an open reader completes with a *busy* result
        # row (busy != 0) instead of raising. That must be reported as a failed
        # checkpoint, not a silent success that hides an ever-growing WAL.
        class _Cursor:
            def fetchone(self):
                return (1, -1, -1)  # busy, log_pages, checkpointed_pages

        class _BusyConn:
            def execute(self, _sql):
                return _Cursor()

            def close(self):
                pass

        monkeypatch.setattr("obs.db.database.sqlite3.connect", lambda *a, **k: _BusyConn())
        assert await db.checkpoint() is False
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_does_not_commit_shared_connection(tmp_path):
    db_path = tmp_path / "obs.db"
    db = Database(str(db_path))
    await db.connect()
    try:
        await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        await db.commit()
        # Open — but do not commit — a write transaction on the shared connection.
        await db.execute("INSERT INTO t (v) VALUES ('pending')")

        # Maintenance checkpoint runs on its own connection and must not commit the
        # in-flight transaction behind the application's back.
        await db.checkpoint()

        await db.conn.rollback()
        rows = await db.fetchall("SELECT * FROM t")
        assert rows == []
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_treats_locked_db_as_skip(tmp_path, monkeypatch):
    """A non-waiting busy timeout surfaces contention as 'database is locked' → skip."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    try:

        class _LockedConn:
            def execute(self, _sql):
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                pass

        monkeypatch.setattr("obs.db.database.sqlite3.connect", lambda *a, **k: _LockedConn())
        assert await db.checkpoint() is False
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_reraises_non_lock_operational_error(tmp_path, monkeypatch):
    """A non-lock OperationalError (e.g. I/O error) is a real fault and must propagate."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    class _BrokenConn:
        def execute(self, _sql):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    monkeypatch.setattr("obs.db.database.sqlite3.connect", lambda *a, **k: _BrokenConn())
    try:
        with pytest.raises(sqlite3.OperationalError):
            await db.checkpoint()
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_checkpoint_skipped_when_disconnected(tmp_path):
    """Maintenance must not touch the DB file while disconnected (e.g. admin restore)."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    await db.disconnect()
    assert db._conn is None
    assert await db.checkpoint() is False


@pytest.mark.asyncio
async def test_checkpoint_rechecks_disconnect_after_acquiring_lock(tmp_path):
    """If the DB is closed while a checkpoint waits for the lock, it must skip on re-check."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    conn = db._conn

    await db._checkpoint_lock.acquire()  # force the next checkpoint to wait
    cp = asyncio.create_task(db.checkpoint())
    await asyncio.sleep(0.05)
    assert not cp.done()

    # Simulate a disconnect completing while the checkpoint waits for the lock.
    db._conn = None
    db._checkpoint_lock.release()
    assert await asyncio.wait_for(cp, timeout=2) is False

    await conn.close()  # clean up the connection we detached


@pytest.mark.asyncio
async def test_connect_checkpoints_before_migrations(tmp_path, monkeypatch):
    """An initial checkpoint runs before migrations so a full WAL is reclaimed first."""
    calls: list[str] = []
    orig_checkpoint = Database.checkpoint
    orig_migrations = Database._run_migrations

    async def spy_checkpoint(self):
        calls.append("checkpoint")
        return await orig_checkpoint(self)

    async def spy_migrations(self):
        calls.append("migrations")
        return await orig_migrations(self)

    monkeypatch.setattr(Database, "checkpoint", spy_checkpoint)
    monkeypatch.setattr(Database, "_run_migrations", spy_migrations)

    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    try:
        assert calls == ["checkpoint", "migrations"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_connect_survives_initial_checkpoint_failure(tmp_path, monkeypatch):
    """A failing initial checkpoint must not block startup; migrations still run."""

    async def boom(self) -> bool:
        raise RuntimeError("checkpoint exploded")

    monkeypatch.setattr(Database, "checkpoint", boom)
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()  # must not raise despite the checkpoint failing
    try:
        row = await db.fetchone("SELECT COUNT(*) AS c FROM schema_version")
        assert row is not None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_scheduler_checkpoints_periodically(tmp_path):
    db_path = tmp_path / "obs.db"
    db = Database(str(db_path))
    await db.connect()
    scheduler = DatabaseMaintenanceScheduler(db, interval_seconds=0.05)
    try:
        await _grow_wal(db)
        assert await _wal_size(db_path) > 0

        scheduler.start()
        # Wait for at least one scheduled checkpoint to run.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if await _wal_size(db_path) == 0:
                break
        assert await _wal_size(db_path) == 0
    finally:
        await scheduler.stop()
        await db.disconnect()


@pytest.mark.asyncio
async def test_scheduler_stop_is_idempotent_and_cancels_task(tmp_path):
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    scheduler = DatabaseMaintenanceScheduler(db, interval_seconds=10)
    try:
        scheduler.start()
        await scheduler.stop()
        # Second stop must not raise even though the task is already gone.
        await scheduler.stop()
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_scheduler_survives_checkpoint_error(tmp_path):
    """A failing checkpoint is logged and does not kill the maintenance loop."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    calls = {"n": 0}

    async def boom() -> bool:
        calls["n"] += 1
        raise RuntimeError("checkpoint exploded")

    db.checkpoint = boom  # type: ignore[method-assign]
    scheduler = DatabaseMaintenanceScheduler(db, interval_seconds=0.02)
    try:
        scheduler.start()
        for _ in range(40):
            await asyncio.sleep(0.02)
            if calls["n"] >= 2:
                break
        # Loop kept running past the first failure instead of dying.
        assert calls["n"] >= 2
    finally:
        await scheduler.stop()
        await db.disconnect()


@pytest.mark.asyncio
async def test_scheduler_cancellation_during_checkpoint_stops_cleanly(tmp_path):
    """Cancelling while a checkpoint is in flight must terminate the loop, not swallow it."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    in_checkpoint = asyncio.Event()

    async def slow_checkpoint() -> bool:
        in_checkpoint.set()
        await asyncio.sleep(10)  # block so stop() cancels us mid-checkpoint
        return True

    async def noop_checkpoint() -> bool:
        return False

    db.checkpoint = slow_checkpoint  # type: ignore[method-assign]
    scheduler = DatabaseMaintenanceScheduler(db, interval_seconds=0.001)
    try:
        scheduler.start()
        await asyncio.wait_for(in_checkpoint.wait(), timeout=2)
        # stop() cancels the task while it awaits checkpoint(); the loop re-raises.
        await scheduler.stop()
        assert scheduler._task.cancelled()
    finally:
        db.checkpoint = noop_checkpoint  # type: ignore[method-assign]  # keep disconnect fast
        await db.disconnect()


@pytest.mark.asyncio
async def test_init_and_get_scheduler_singleton(tmp_path):
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()
    scheduler = init_db_maintenance_scheduler(db, interval_seconds=10)
    try:
        assert get_db_maintenance_scheduler() is scheduler
    finally:
        await scheduler.stop()
        await db.disconnect()


def test_get_scheduler_before_init_raises(monkeypatch):
    from obs.db import maintenance

    monkeypatch.setattr(maintenance, "_scheduler", None)
    with pytest.raises(RuntimeError):
        get_db_maintenance_scheduler()


@pytest.mark.asyncio
async def test_disconnect_swallows_checkpoint_error(tmp_path):
    """A checkpoint failure on shutdown must not prevent the connection from closing."""
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    async def boom() -> bool:
        raise RuntimeError("checkpoint exploded")

    db._run_checkpoint = boom  # type: ignore[method-assign]
    # Must not raise despite the failing checkpoint.
    await db.disconnect()
    assert db._conn is None


@pytest.mark.asyncio
async def test_checkpoint_waits_for_worker_thread_on_cancel(tmp_path, monkeypatch):
    """Cancelling a checkpoint must hold the lock until the worker thread finishes.

    Cancelling asyncio.to_thread/run_in_executor does not stop the worker, so the lock
    must not be released (letting disconnect/restore proceed) until it is done. See #908.
    """
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    entered = threading.Event()
    release = threading.Event()

    class _Cursor:
        def fetchone(self):
            return (0, -1, -1)

    class _SlowConn:
        def execute(self, _sql):
            entered.set()
            release.wait(2)  # block the worker until the test lets it finish
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setattr("obs.db.database.sqlite3.connect", lambda *a, **k: _SlowConn())

    cp = asyncio.create_task(db.checkpoint())
    while not entered.is_set():  # wait until the worker is running under the lock
        await asyncio.sleep(0.01)

    cp.cancel()
    await asyncio.sleep(0.05)
    # Cancellation is pending but the coroutine still awaits the worker → lock held.
    assert not cp.done()
    assert db._checkpoint_lock.locked()

    release.set()  # let the worker finish
    with pytest.raises(asyncio.CancelledError):
        await cp
    assert not db._checkpoint_lock.locked()  # lock released only after the worker was done

    async def _noop() -> bool:
        return False

    db._run_checkpoint = _noop  # type: ignore[method-assign]  # keep disconnect fast
    await db.disconnect()


@pytest.mark.asyncio
async def test_disconnect_waits_for_inflight_checkpoint(tmp_path):
    """Disconnect must not proceed while a checkpoint worker still runs on the DB file.

    Cancelling asyncio.to_thread does not stop the worker, so an admin restore that
    disconnects and rewrites the file would otherwise race an in-flight checkpoint. The
    shared checkpoint lock serializes them. See issue #908.
    """
    db = Database(str(tmp_path / "obs.db"))
    await db.connect()

    # Simulate an in-flight checkpoint holding the lock.
    await db._checkpoint_lock.acquire()
    disc = asyncio.create_task(db.disconnect())
    await asyncio.sleep(0.05)
    assert not disc.done()  # blocked until the checkpoint releases the lock

    db._checkpoint_lock.release()
    await asyncio.wait_for(disc, timeout=2)
    assert db._conn is None
