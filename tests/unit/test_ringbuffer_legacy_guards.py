"""Coverage tests for ringbuffer guard and fallback paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from obs.ringbuffer import ringbuffer as rb_mod
from obs.ringbuffer.ringbuffer import RingBuffer, _safe_loads, get_ringbuffer, reset_ringbuffer


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
