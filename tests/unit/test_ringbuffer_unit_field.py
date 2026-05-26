"""Tests for the ``unit`` field on ringbuffer entry responses (#434).

The Monitor table needs the DataPoint unit alongside the value. We hydrate it
from the DataPoint registry at serialization time — both for REST responses
(:class:`RingBufferEntryOut`) and the live WebSocket push (``ringbuffer_entry``
action).

Entries that belong to a DataPoint without a unit (or to a DataPoint that no
longer exists in the registry) must serialize ``unit=None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from obs.api.v1.ringbuffer import RingBufferEntryOut
from obs.api.v1.websocket import WebSocketManager
from obs.core.event_bus import DataValueEvent


def test_ringbuffer_entry_out_accepts_unit_field():
    """The Pydantic model must expose ``unit`` as an optional field."""
    entry = RingBufferEntryOut(
        id=1,
        ts="2026-05-11T12:00:00Z",
        datapoint_id="dp-1",
        name="Temp",
        topic="dp/dp-1/value",
        old_value=21.5,
        new_value=22.3,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
        unit="°C",
    )
    assert entry.unit == "°C"

    dumped = entry.model_dump()
    assert "unit" in dumped
    assert dumped["unit"] == "°C"


def test_ringbuffer_entry_out_unit_defaults_to_none():
    """``unit`` must be optional and default to None for back-compat."""
    entry = RingBufferEntryOut(
        id=1,
        ts="2026-05-11T12:00:00Z",
        datapoint_id="dp-1",
        name="Temp",
        topic="dp/dp-1/value",
        old_value=21.5,
        new_value=22.3,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
    )
    assert entry.unit is None
    dumped = entry.model_dump()
    assert dumped["unit"] is None


@pytest.mark.asyncio
async def test_ringbuffer_websocket_push_includes_unit_when_dp_has_unit(monkeypatch):
    """When the registry DataPoint has a unit, the live WS push carries it."""
    ws_received: list[dict] = []

    class _FakeWebSocket:
        def __init__(self) -> None:
            self.accepted = False

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, msg: dict) -> None:
            ws_received.append(msg)

        async def close(self) -> None:
            return None

    manager = WebSocketManager()
    await manager.connect(_FakeWebSocket())

    dp_id = uuid4()

    class _RegistryStub:
        def get(self, _dp_id):
            return SimpleNamespace(name="Temperature", unit="°C")

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=21.5)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=22.3,
        quality="good",
        source_adapter="api",
        ts=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    await manager.handle_value_event(event)

    assert len(ws_received) == 1
    entry = ws_received[0]["entry"]
    assert "unit" in entry
    assert entry["unit"] == "°C"


@pytest.mark.asyncio
async def test_ringbuffer_websocket_push_unit_is_none_when_dp_has_no_unit(monkeypatch):
    """When the DataPoint has no unit the WS payload carries unit=None."""
    ws_received: list[dict] = []

    class _FakeWebSocket:
        async def accept(self) -> None:
            return None

        async def send_json(self, msg: dict) -> None:
            ws_received.append(msg)

        async def close(self) -> None:
            return None

    manager = WebSocketManager()
    await manager.connect(_FakeWebSocket())

    dp_id = uuid4()

    class _RegistryStub:
        def get(self, _dp_id):
            return SimpleNamespace(name="Boolean", unit=None)

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=False)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=True,
        quality="good",
        source_adapter="api",
        ts=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    await manager.handle_value_event(event)

    assert len(ws_received) == 1
    entry = ws_received[0]["entry"]
    assert "unit" in entry
    assert entry["unit"] is None


@pytest.mark.asyncio
async def test_ringbuffer_websocket_push_unit_is_none_when_dp_missing(monkeypatch):
    """When the DataPoint is not in the registry the WS payload carries unit=None."""
    ws_received: list[dict] = []

    class _FakeWebSocket:
        async def accept(self) -> None:
            return None

        async def send_json(self, msg: dict) -> None:
            ws_received.append(msg)

        async def close(self) -> None:
            return None

    manager = WebSocketManager()
    await manager.connect(_FakeWebSocket())

    dp_id = uuid4()

    class _RegistryStub:
        def get(self, _dp_id):
            return None

        def get_value(self, _dp_id):
            return None

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=1.0,
        quality="good",
        source_adapter="api",
        ts=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    await manager.handle_value_event(event)

    assert len(ws_received) == 1
    entry = ws_received[0]["entry"]
    assert entry.get("unit") is None
