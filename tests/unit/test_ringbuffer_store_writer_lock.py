"""Writer-Exklusivität pro Storage-Root via Lockfile/Lease (#931).

Genau ein Writer darf eine Storage-Root besitzen. Ein zweiter Writer auf
derselben Root wird fail-fast abgewiesen (nicht blockierend gewartet).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.writer_lock import WriterLease, WriterLockHeldError


async def test_acquire_writes_lockfile(tmp_path: Path):
    lease = WriterLease(tmp_path)
    await lease.acquire()
    try:
        assert (tmp_path / "writer.lock").exists()
    finally:
        await lease.release()


async def test_second_writer_on_same_root_is_rejected_fail_fast(tmp_path: Path):
    first = WriterLease(tmp_path)
    await first.acquire()
    try:
        second = WriterLease(tmp_path)
        with pytest.raises(WriterLockHeldError):
            await second.acquire()
    finally:
        await first.release()


async def test_release_allows_reacquire(tmp_path: Path):
    first = WriterLease(tmp_path)
    await first.acquire()
    await first.release()

    second = WriterLease(tmp_path)
    await second.acquire()
    try:
        assert (tmp_path / "writer.lock").exists()
    finally:
        await second.release()


async def test_stale_lockfile_from_dead_process_is_taken_over(tmp_path: Path):
    # Ein Lockfile eines nicht mehr existierenden PID darf übernommen werden,
    # sonst würde ein Absturz die Root dauerhaft blockieren.
    lock_path = tmp_path / "writer.lock"
    lock_path.write_text('{"pid": 999999, "acquired_at": "2000-01-01T00:00:00Z"}', encoding="utf-8")

    lease = WriterLease(tmp_path)
    await lease.acquire()
    try:
        assert lease.owns_lock
    finally:
        await lease.release()


async def test_release_is_idempotent(tmp_path: Path):
    lease = WriterLease(tmp_path)
    await lease.acquire()
    await lease.release()
    # Zweites release darf nicht werfen.
    await lease.release()
    assert not lease.owns_lock
