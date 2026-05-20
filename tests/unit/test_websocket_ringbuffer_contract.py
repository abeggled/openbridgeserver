"""Contract test for ringbuffer websocket payload."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from obs.api.v1.websocket import WebSocketManager
from obs.core.event_bus import DataValueEvent


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, msg: dict) -> None:
        self.messages.append(msg)

    async def close(self) -> None:
        return None


class _SerializationFailWebSocket(_FakeWebSocket):
    async def send_json(self, msg: dict) -> None:
        raise TypeError("not JSON serializable")


class _TransportFailWebSocket(_FakeWebSocket):
    async def send_json(self, msg: dict) -> None:
        raise RuntimeError("socket is closed")


@pytest.mark.asyncio
async def test_ringbuffer_entry_payload_contains_documented_fields(monkeypatch):
    ws = _FakeWebSocket()
    manager = WebSocketManager()
    await manager.connect(ws)

    dp_id = uuid4()
    fixed_ts = datetime(2026, 5, 6, 19, 44, 49, 123000, tzinfo=UTC)

    class _RegistryStub:
        def get(self, _dp_id):
            return SimpleNamespace(name="Contract DP", unit="W")

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=12.5)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=42.0,
        quality="good",
        source_adapter="api",
        ts=fixed_ts,
    )
    await manager.handle_value_event(event)

    assert len(ws.messages) == 1
    msg = ws.messages[0]
    assert msg.get("action") == "ringbuffer_entry"
    assert "entry" in msg

    entry = msg["entry"]
    required_fields = {
        "ts",
        "datapoint_id",
        "name",
        "new_value",
        "old_value",
        "quality",
        "source_adapter",
    }
    assert required_fields.issubset(entry.keys())
    assert entry["datapoint_id"] == str(dp_id)
    assert entry["name"] == "Contract DP"
    assert entry["new_value"] == 42.0
    assert entry["old_value"] == 12.5
    assert entry["quality"] == "good"
    assert entry["source_adapter"] == "api"
    assert entry["ts"] == "2026-05-06T19:44:49.123Z"


@pytest.mark.asyncio
async def test_send_drops_non_serializable_message_without_disconnect():
    ws = _SerializationFailWebSocket()
    manager = WebSocketManager()
    conn_id = await manager.connect(ws)

    ok = await manager._send(conn_id, {"action": "ringbuffer_entry", "entry": object()})  # noqa: SLF001

    assert ok is True
    assert manager.connection_count == 1


@pytest.mark.asyncio
async def test_broadcast_disconnects_dead_connection_on_transport_error():
    manager = WebSocketManager()
    good = _FakeWebSocket()
    bad = _TransportFailWebSocket()

    await manager.connect(good)
    await manager.connect(bad)
    assert manager.connection_count == 2

    await manager.broadcast({"action": "ping"})

    assert manager.connection_count == 1
    assert good.messages == [{"action": "ping"}]
