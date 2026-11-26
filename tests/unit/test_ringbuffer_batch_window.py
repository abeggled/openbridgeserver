"""Sammelfenster des segmentierten Schreibpfads (#1484).

Mit ``batch_window_seconds > 0`` sammelt der RingBuffer Ereignisse im Speicher und
schreibt sie als einen ``append``-Block: nach 100 Ereignissen oder nach Ablauf des
Fensters, je nachdem, was zuerst eintritt. Leser sehen immer alles.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import _BATCH_MAX_EVENTS, RingBuffer


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(storage="file", disk_path=str(tmp_path / "obs_ringbuffer.db"), segmented=True, **kwargs)


async def _record(rb: RingBuffer, value: object, *, metadata: dict | None = None) -> None:
    await rb.record(
        ts=f"2026-01-01T00:00:{int(value) % 60:02d}.{int(value) % 1000:03d}Z",
        datapoint_id="dp-batch",
        topic="dp/dp-batch/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata=metadata or {},
    )


def _count_appends(rb: RingBuffer) -> list[int]:
    """Zeichnet die Blockgroesse jedes ``append``-Aufrufs am Store auf."""
    sizes: list[int] = []
    original = rb.store.append

    async def _spy(events):
        sizes.append(len(events))
        await original(events)

    rb.store.append = _spy
    return sizes


async def _stored_total(rb: RingBuffer) -> int:
    return (await rb.store.stats()).common["total"]


@pytest.mark.asyncio
async def test_window_zero_writes_each_event_immediately(tmp_path: Path):
    rb = _rb(tmp_path)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        await _record(rb, 1)
        await _record(rb, 2)
        assert sizes == [1, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_events_are_held_until_the_window_ends(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=0.05)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        await _record(rb, 1)
        await _record(rb, 2)
        assert sizes == []
        await asyncio.sleep(0.2)
        assert sizes == [2]
        # Nach dem Schreiben startet das naechste Ereignis ein neues Fenster.
        await _record(rb, 3)
        await asyncio.sleep(0.2)
        assert sizes == [2, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_full_block_is_written_before_the_window_ends(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        for value in range(_BATCH_MAX_EVENTS + 1):
            await _record(rb, value)
        assert sizes == [_BATCH_MAX_EVENTS]
        assert await _stored_total(rb) == _BATCH_MAX_EVENTS
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_and_stats_see_pending_events(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        await _record(rb, 1)
        await _record(rb, 2)
        assert [e.new_value for e in await rb.query_v2()] == [2, 1]
        await _record(rb, 3)
        assert (await rb.stats())["total"] == 3
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_stop_writes_pending_events(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    await _record(rb, 1)
    await _record(rb, 2)
    await rb.stop()

    reopened = _rb(tmp_path)
    await reopened.start()
    try:
        assert [e.new_value for e in await reopened.query_v2()] == [2, 1]
    finally:
        await reopened.stop()


@pytest.mark.asyncio
async def test_one_bad_event_does_not_drop_the_rest_of_the_block(tmp_path: Path, caplog):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        await _record(rb, 1)
        # Nicht serialisierbare Metadaten lassen genau diesen Insert scheitern.
        await _record(rb, 2, metadata={"bad": {1, 2}})
        await _record(rb, 3)
        assert [e.new_value for e in await rb.query_v2()] == [3, 1]
        assert "verworfen" in caplog.text
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_failing_single_event_block_is_dropped_and_logged(tmp_path: Path, caplog):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        await _record(rb, 1, metadata={"bad": {1, 2}})
        assert await rb.query_v2() == []
        assert "verworfen" in caplog.text
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_changes_the_window_and_writes_pending_events(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        await _record(rb, 1)
        await rb.reconfigure("file", batch_window_seconds=0)
        assert sizes == [1]
        await _record(rb, 2)
        assert sizes == [1, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_with_unchanged_window_is_a_no_op(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        await _record(rb, 1)
        await rb.reconfigure("file", batch_window_seconds=60)
        assert sizes == []
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_timer_after_stop_does_nothing(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=0.05)
    await rb.start()
    await _record(rb, 1)
    await rb.stop()
    # Der Timer wurde beim Stop abgebrochen; es bleibt nichts offen.
    await asyncio.sleep(0.1)
    assert rb._flush_task is None
    assert rb._pending == []


def test_negative_window_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="batch_window_seconds"):
        _rb(tmp_path, batch_window_seconds=-1)


def test_negative_window_is_rejected_on_reconfigure(tmp_path: Path):
    rb = _rb(tmp_path)
    with pytest.raises(ValueError, match="batch_window_seconds"):
        asyncio.run(rb.reconfigure("file", batch_window_seconds=-1))


@pytest.mark.asyncio
async def test_failed_reconfigure_restores_the_window(tmp_path: Path, monkeypatch):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:

        async def _boom(*_args):
            raise RuntimeError("store config rejected")

        monkeypatch.setattr(rb, "_apply_segment_config_locked", _boom)
        with pytest.raises(RuntimeError):
            await rb.reconfigure("file", batch_window_seconds=1)
        assert rb._batch_window == 60
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reader_survives_a_failing_block_write(tmp_path: Path, monkeypatch, caplog):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        await _record(rb, 1)

        async def _boom():
            raise RuntimeError("rotation failed")

        monkeypatch.setattr(rb, "_segment_age_rotation_due", _boom)
        assert await rb.query_v2() == []
        assert "nicht vollstaendig verarbeitet" in caplog.text
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_config_api_applies_persists_and_keeps_the_window(tmp_path: Path, monkeypatch):
    from obs.api.v1 import ringbuffer as rb_api
    from obs.db.database import Database
    from obs.ringbuffer.persisted_config import DEFAULT_BATCH_WINDOW_SECONDS
    from obs.ringbuffer.ringbuffer import reset_ringbuffer

    db = Database(":memory:")
    await db.connect()
    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(tmp_path / "obs_ringbuffer.db"))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)

    async def _configure(**fields):
        return await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(**fields), request=None, _user="admin", db=db)

    try:
        # Ohne Angabe gilt der gespeicherte Standard, auch fuer einen neuen Buffer.
        stats = await _configure(enabled=True)
        rb = rb_api.get_optional_ringbuffer()
        assert stats.batch_window_seconds == rb._batch_window == DEFAULT_BATCH_WINDOW_SECONDS

        assert stats.batch_max_events == rb._batch_max_events == _BATCH_MAX_EVENTS

        stats = await _configure(batch_window_seconds=0, batch_max_events=7)
        assert stats.batch_window_seconds == rb._batch_window == 0
        assert stats.batch_max_events == rb._batch_max_events == 7
        persisted = await rb_api.load_persisted_ringbuffer_config(db)
        assert (persisted["batch_window_seconds"], persisted["batch_max_events"]) == (0, 7)

        # Ein Update ohne das Feld laesst den gespeicherten Wert stehen.
        stats = await _configure(max_entries=5000)
        assert stats.batch_window_seconds == rb._batch_window == 0
        assert stats.batch_max_events == rb._batch_max_events == 7

        stats = await _configure(enabled=False)
        assert stats.batch_window_seconds == 0
        assert stats.batch_max_events == 7
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()


def test_config_api_rejects_a_window_above_the_limit():
    from pydantic import ValidationError

    from obs.api.v1 import ringbuffer as rb_api

    with pytest.raises(ValidationError):
        rb_api.RingBufferConfig(batch_window_seconds=61)
    with pytest.raises(ValidationError):
        rb_api.RingBufferConfig(batch_max_events=0)
    with pytest.raises(ValidationError):
        rb_api.RingBufferConfig(batch_max_events=1001)


@pytest.mark.asyncio
async def test_shorter_window_applies_to_the_next_event(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        await _record(rb, 1)
        await rb.reconfigure("file", batch_window_seconds=0.05)
        await _record(rb, 2)
        # Der alte 60-s-Timer darf das neue Fenster nicht blockieren.
        await asyncio.sleep(0.2)
        assert sizes == [1, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_full_block_restarts_the_window(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=0.3)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        for value in range(_BATCH_MAX_EVENTS):
            await _record(rb, value)
        await _record(rb, 100)
        await asyncio.sleep(0.5)
        assert sizes == [_BATCH_MAX_EVENTS, 1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_window_zero_still_raises_on_a_bad_event(tmp_path: Path):
    rb = _rb(tmp_path)
    await rb.start()
    try:
        with pytest.raises(TypeError):
            await _record(rb, 1, metadata={"bad": {1, 2}})
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_block_size_is_configurable_and_changeable(tmp_path: Path):
    rb = _rb(tmp_path, batch_window_seconds=60, batch_max_events=2)
    await rb.start()
    try:
        sizes = _count_appends(rb)
        for value in range(3):
            await _record(rb, value)
        assert sizes == [2]
        # Die Aenderung schreibt zuerst das gesammelte Ereignis, dann gilt die neue Groesse.
        await rb.reconfigure("file", batch_max_events=3)
        assert sizes == [2, 1]
        for value in range(3, 6):
            await _record(rb, value)
        assert sizes == [2, 1, 3]
    finally:
        await rb.stop()


def test_block_size_below_one_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="batch_max_events"):
        _rb(tmp_path, batch_max_events=0)
    rb = _rb(tmp_path)
    with pytest.raises(ValueError, match="batch_max_events"):
        asyncio.run(rb.reconfigure("file", batch_max_events=0))
