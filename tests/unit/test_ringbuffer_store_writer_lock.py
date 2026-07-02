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


async def test_corrupt_lockfile_is_treated_as_stale(tmp_path: Path):
    (tmp_path / "writer.lock").write_text("not-json", encoding="utf-8")
    lease = WriterLease(tmp_path)
    await lease.acquire()
    try:
        assert lease.owns_lock
    finally:
        await lease.release()


async def test_pid_of_current_process_is_alive(tmp_path: Path):
    import os

    # Lockfile mit der eigenen (lebenden) PID → zweiter Writer wird abgewiesen.
    (tmp_path / "writer.lock").write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")
    lease = WriterLease(tmp_path)
    with pytest.raises(WriterLockHeldError):
        await lease.acquire()


async def test_lockfile_with_zero_pid_is_treated_as_stale(tmp_path: Path):
    (tmp_path / "writer.lock").write_text('{"pid": 0}', encoding="utf-8")
    lease = WriterLease(tmp_path)
    await lease.acquire()
    try:
        assert lease.owns_lock
    finally:
        await lease.release()


async def test_acquire_is_atomic_no_double_owns_on_race(tmp_path: Path):
    """#951: Zwei quasi-gleichzeitig startende Writer dürfen NICHT beide _owns setzen.

    Der frühere check-then-write (``exists()`` gefolgt von ``write_text``) ließ ein
    Fenster offen, in dem beide Writer den Check passieren, bevor einer schreibt →
    beide setzten ``_owns=True`` (zwei Writer auf demselben Manifest/Segment-Satz).
    Der atomare ``O_CREAT|O_EXCL``-Erwerb schließt das: genau einer gewinnt.
    """
    first = WriterLease(tmp_path)
    second = WriterLease(tmp_path)

    # Sequenziell — beide würden mit dem alten check-then-write beide gewinnen,
    # weil kein lebender Halter-PID im (noch leeren) Lockfile stünde. Mit dem
    # atomaren Erwerb hält der ERSTE das Lock (lebende eigene PID) und der zweite
    # wird fail-fast abgewiesen.
    await first.acquire()
    try:
        assert first.owns_lock is True
        with pytest.raises(WriterLockHeldError):
            await second.acquire()
        assert second.owns_lock is False
    finally:
        await first.release()


async def test_held_flock_blocks_second_acquire(tmp_path: Path):
    """Solange der erste Lease den flock hält, wird ein zweiter Erwerb fail-fast abgewiesen (Basis der Atomizität)."""
    first = WriterLease(tmp_path)
    await first.acquire()
    try:
        assert first.owns_lock
        second = WriterLease(tmp_path)
        with pytest.raises(WriterLockHeldError):
            await second.acquire()
        assert second.owns_lock is False
    finally:
        await first.release()


async def test_concurrent_acquire_only_one_wins(tmp_path: Path):
    """Mehrere Leases gleichzeitig (asyncio.gather) auf derselben Root → genau einer besitzt das Lock."""
    import asyncio

    leases = [WriterLease(tmp_path) for _ in range(8)]

    async def _try(lease):
        try:
            await lease.acquire()
            return True
        except WriterLockHeldError:
            return False

    results = await asyncio.gather(*(_try(le) for le in leases))
    try:
        assert sum(results) == 1
        assert sum(le.owns_lock for le in leases) == 1
    finally:
        for le in leases:
            await le.release()


async def test_stale_takeover_blocked_by_live_flock_holder_is_fail_fast(tmp_path: Path):
    """#951: Hält bereits ein lebender Übernehmer den flock, wird der zweite fail-fast abgewiesen.

    Der Übernahme-Pfad ``unlink()``t das verwaiste Lock NICHT mehr, sondern gewinnt
    es über den kernel-serialisierten ``flock``. Hält der erste Übernehmer den flock
    (lebendig), scheitert der zweite am ``LOCK_NB`` → ``WriterLockHeldError`` statt
    stillschweigend ``_owns=True``.
    """
    # Verwaistes Lock (toter PID) → wird vom ersten Übernehmer live übernommen.
    (tmp_path / "writer.lock").write_text('{"pid": 999999}', encoding="utf-8")
    winner = WriterLease(tmp_path)
    await winner.acquire()
    try:
        loser = WriterLease(tmp_path)
        with pytest.raises(WriterLockHeldError):
            await loser.acquire()
        assert loser.owns_lock is False
    finally:
        await winner.release()


async def test_permission_error_on_kill_treats_holder_as_alive(tmp_path: Path, monkeypatch):
    import obs.ringbuffer.store.writer_lock as wl

    def _raise_permission(_pid, _sig):
        raise PermissionError

    monkeypatch.setattr(wl.os, "kill", _raise_permission)
    (tmp_path / "writer.lock").write_text('{"pid": 424242}', encoding="utf-8")
    lease = WriterLease(tmp_path)
    with pytest.raises(WriterLockHeldError):
        await lease.acquire()


async def test_stale_takeover_two_concurrent_takers_only_one_owns(tmp_path: Path):
    """#951 [P1]: Zwei quasi-gleichzeitige Übernahmen eines verwaisten Locks – genau einer gewinnt.

    Vorher: der Übernahme-Pfad ``unlink()``te das verwaiste Lockfile, BEVOR er die
    Ersatzdatei anlegte. Übernahm Prozess A das Lock (unlink+create → besitzt es),
    konnte Prozess B – der dasselbe verwaiste Lock gesehen hatte – A's frisch
    erzeugtes Lockfile weg-``unlink()``en und sein eigenes anlegen: beide
    ``_owns=True``. Mit atomarer/flock-basierter Übernahme darf das NIE passieren.
    """
    lock_path = tmp_path / "writer.lock"
    lock_path.write_text('{"pid": 999999, "acquired_at": "2000-01-01T00:00:00Z"}', encoding="utf-8")

    leases = [WriterLease(tmp_path) for _ in range(6)]

    def _try(lease):
        try:
            lease._acquire_sync()
            return True
        except WriterLockHeldError:
            return False

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(leases)) as pool:
        results = list(pool.map(_try, leases))
    try:
        assert sum(results) == 1
        assert sum(le.owns_lock for le in leases) == 1
    finally:
        for le in leases:
            await le.release()


async def test_live_holder_that_took_over_stale_lock_rejects_next_writer(tmp_path: Path):
    """Nach einer Übernahme hält der Gewinner das Lock live – ein Folgeschreiber wird abgewiesen."""
    lock_path = tmp_path / "writer.lock"
    lock_path.write_text('{"pid": 999999}', encoding="utf-8")

    winner = WriterLease(tmp_path)
    await winner.acquire()
    try:
        assert winner.owns_lock
        loser = WriterLease(tmp_path)
        with pytest.raises(WriterLockHeldError):
            await loser.acquire()
        assert loser.owns_lock is False
    finally:
        await winner.release()
