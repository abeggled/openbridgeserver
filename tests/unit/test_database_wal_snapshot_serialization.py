from __future__ import annotations

import asyncio
import sqlite3

import pytest

from obs.db.database import Database, _LoopReusableLock


def test_loop_reusable_lock_allows_next_loop_after_owner_loop_closes() -> None:
    lock = _LoopReusableLock()
    owner_loop = asyncio.new_event_loop()
    owner_loop.run_until_complete(lock.acquire())
    owner_loop.close()

    async def acquire_from_next_loop() -> None:
        await lock.acquire()
        lock.release()

    asyncio.run(acquire_from_next_loop())


def test_loop_reusable_lock_tracks_paused_event_loops_independently() -> None:
    lock = _LoopReusableLock()
    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()
    try:
        first_loop.run_until_complete(lock.acquire())

        async def acquire_and_release() -> None:
            await lock.acquire()
            lock.release()

        second_loop.run_until_complete(acquire_and_release())

        async def release_first() -> None:
            lock.release()

        first_loop.run_until_complete(release_first())
    finally:
        first_loop.close()
        second_loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("write_style", ["execute_and_commit", "execute_then_commit"])
async def test_private_transaction_cannot_stale_shared_wal_reader_snapshot(tmp_path, write_style: str) -> None:
    db = Database(str(tmp_path / "snapshot.db"))
    await db.connect()
    reader_open = asyncio.Event()
    release_reader = asyncio.Event()
    reader_task: asyncio.Task | None = None
    private_task: asyncio.Task | None = None
    try:
        await db.execute_and_commit("CREATE TABLE snapshot_test (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        await db.executemany("INSERT INTO snapshot_test(value) VALUES (?)", [("first",), ("second",)])
        await db.commit()

        async def hold_shared_reader() -> None:
            async with db._ordinary_operation(), db.conn.execute("SELECT * FROM snapshot_test ORDER BY id") as cursor:
                await cursor.fetchone()
                reader_open.set()
                await release_reader.wait()

        private_committed = asyncio.Event()

        async def private_write() -> None:
            async with db.transaction():
                await db.execute("INSERT INTO snapshot_test(value) VALUES ('private')")
            private_committed.set()

        reader_task = asyncio.create_task(hold_shared_reader())
        await reader_open.wait()
        private_task = asyncio.create_task(private_write())

        interleaved = False
        try:
            await asyncio.wait_for(private_committed.wait(), timeout=0.1)
            interleaved = True
        except TimeoutError:
            pass

        if interleaved:
            with pytest.raises(sqlite3.OperationalError) as exc_info:
                await db.execute_and_commit("INSERT INTO snapshot_test(value) VALUES ('stale')")
            assert exc_info.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT
            await db.rollback()
        assert not interleaved, "private transaction committed while the shared WAL reader snapshot was still open"

        release_reader.set()
        await reader_task
        await private_task

        if write_style == "execute_and_commit":
            await db.execute_and_commit("INSERT INTO snapshot_test(value) VALUES ('shared')")
        else:
            await db.execute("INSERT INTO snapshot_test(value) VALUES ('shared')")
            await db.commit()

        rows = await db.fetchall("SELECT value FROM snapshot_test ORDER BY id")
        assert [row["value"] for row in rows] == ["first", "second", "private", "shared"]
    finally:
        release_reader.set()
        if reader_task is not None:
            await asyncio.gather(reader_task, return_exceptions=True)
        if private_task is not None:
            await asyncio.gather(private_task, return_exceptions=True)
        await db.disconnect()
