"""Contract test for ringbuffer websocket payload."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from obs.api.v1.websocket import WebSocketManager, _extract_subprotocol_tokens, _page_allowed_datapoints
from obs.core.event_bus import DataValueEvent


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.accepted = False

    async def accept(self, subprotocol: str | None = None) -> None:
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
            return SimpleNamespace(name="Contract DP", unit="W", data_type="FLOAT", tags=["heizung"])

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=12.5)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    class _DbStub:
        async def fetchall(self, query, _params):
            if "FROM adapter_bindings" in query:
                return [
                    {
                        "adapter_type": "KNX",
                        "adapter_instance_id": "inst-1",
                        "direction": "both",
                        "config": '{"group_address":"1/2/30"}',
                    }
                ]
            if "hierarchy_datapoint_links" in query:
                return [
                    {"tree_id": "tree-1", "node_id": "leaf-node", "ancestor_id": "leaf-node"},
                    {"tree_id": "tree-1", "node_id": "leaf-node", "ancestor_id": "root-node"},
                ]
            return []

    monkeypatch.setattr("obs.db.database.get_db", lambda: _DbStub())

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
        "metadata_version",
        "metadata",
    }
    assert required_fields.issubset(entry.keys())
    assert entry["datapoint_id"] == str(dp_id)
    assert entry["name"] == "Contract DP"
    assert entry["new_value"] == 42.0
    assert entry["old_value"] == 12.5
    assert entry["quality"] == "good"
    assert entry["source_adapter"] == "api"
    assert entry["ts"] == "2026-05-06T19:44:49.123Z"
    assert entry["metadata_version"] == 1
    assert entry["metadata"]["datapoint"]["id"] == str(dp_id)
    assert entry["metadata"]["datapoint"]["tags"] == ["heizung"]
    assert entry["metadata"]["bindings"][0]["adapter_type"] == "KNX"
    assert entry["metadata"]["bindings"][0]["normalized"]["group_address"] == "1/2/30"
    assert entry["metadata"]["hierarchy_nodes"] == [
        {
            "tree_id": "tree-1",
            "node_id": "leaf-node",
            "ancestor_node_ids": ["leaf-node", "root-node"],
        }
    ]


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


@pytest.mark.asyncio
async def test_log_broadcast_only_reaches_log_access_connections():
    manager = WebSocketManager()
    admin_ws = _FakeWebSocket()
    user_ws = _FakeWebSocket()

    await manager.connect(admin_ws, log_access=True)
    await manager.connect(user_ws, log_access=False)

    msg = {"action": "log_entry", "entry": {"level": "DEBUG", "message": "secret detail"}}
    await manager.broadcast(msg)

    assert admin_ws.messages == [msg]
    assert user_ws.messages == []


@pytest.mark.asyncio
async def test_log_broadcast_revalidates_existing_log_access_connections():
    manager = WebSocketManager()
    admin_ws = _FakeWebSocket()
    checks = [True, False]

    async def log_access_check() -> bool:
        return checks.pop(0)

    await manager.connect(admin_ws, log_access=True, log_access_check=log_access_check)

    first = {"action": "log_entry", "entry": {"level": "INFO", "message": "first"}}
    second = {"action": "log_entry", "entry": {"level": "INFO", "message": "second"}}
    await manager.broadcast(first)
    await manager.broadcast(second)

    assert admin_ws.messages == [first]


@pytest.mark.asyncio
async def test_subscribe_filters_datapoints_for_page_scoped_connection():
    ws = _FakeWebSocket()
    manager = WebSocketManager()
    conn_id = await manager.connect(ws, allowed_dp_ids={"allowed-id"})

    manager.subscribe(conn_id, ["allowed-id", "blocked-id"])

    assert manager.subscriptions(conn_id) == {"allowed-id"}


@pytest.mark.asyncio
async def test_subscribe_initial_values_sends_current_registry_snapshot(monkeypatch):
    dp_id = uuid4()
    other_dp_id = uuid4()
    ws = _FakeWebSocket()
    manager = WebSocketManager()
    conn_id = await manager.connect(ws)

    class _RegistryStub:
        def get(self, dp_uuid):
            if dp_uuid == dp_id:
                return SimpleNamespace(unit="W")
            return None

        def get_value(self, dp_uuid):
            if dp_uuid == dp_id:
                return SimpleNamespace(
                    value=42.5,
                    quality="good",
                    ts=datetime(2026, 6, 8, 9, 10, 11, 123000, tzinfo=UTC),
                )
            return None

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    manager.subscribe(conn_id, [str(dp_id), str(other_dp_id), "not-a-uuid"])
    await manager.send_initial_values(conn_id, [str(dp_id), str(other_dp_id), "not-a-uuid"])

    assert ws.messages == [
        {
            "id": str(dp_id),
            "v": 42.5,
            "u": "W",
            "t": "2026-06-08T09:10:11.123Z",
            "q": "good",
        }
    ]


@pytest.mark.asyncio
async def test_subscribe_initial_values_respects_page_scope(monkeypatch):
    allowed_uuid = uuid4()
    blocked_uuid = uuid4()
    allowed_id = str(allowed_uuid)
    blocked_id = str(blocked_uuid)
    ws = _FakeWebSocket()
    manager = WebSocketManager()
    conn_id = await manager.connect(ws, allowed_dp_ids={allowed_id})

    class _RegistryStub:
        def get(self, dp_uuid):
            if dp_uuid in {allowed_uuid, blocked_uuid}:
                return SimpleNamespace(unit="W")
            return None

        def get_value(self, dp_uuid):
            if dp_uuid in {allowed_uuid, blocked_uuid}:
                return SimpleNamespace(
                    value=str(dp_uuid),
                    quality="good",
                    ts=datetime(2026, 6, 8, 9, 10, 11, 123000, tzinfo=UTC),
                )
            return None

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    before = manager.subscriptions(conn_id)
    manager.subscribe(conn_id, [allowed_id, blocked_id])
    after = manager.subscriptions(conn_id)
    added = [dp_id for dp_id in [allowed_id, blocked_id] if dp_id in after and dp_id not in before]
    await manager.send_initial_values(conn_id, added)

    assert [msg["id"] for msg in ws.messages] == [allowed_id]


@pytest.mark.asyncio
async def test_ringbuffer_push_is_scoped_for_anonymous_page_connections(monkeypatch):
    allowed_uuid = uuid4()
    blocked_uuid = uuid4()
    allowed_id = str(allowed_uuid)
    blocked_id = str(blocked_uuid)

    unrestricted_ws = _FakeWebSocket()
    scoped_ws = _FakeWebSocket()
    manager = WebSocketManager()
    await manager.connect(unrestricted_ws)
    await manager.connect(scoped_ws, allowed_dp_ids={allowed_id})

    class _RegistryStub:
        def get(self, _dp_id):
            return SimpleNamespace(name="Contract DP", unit="W")

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=1.0)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    base_ts = datetime(2026, 5, 6, 19, 44, 49, 123000, tzinfo=UTC)
    allowed_event = DataValueEvent(
        datapoint_id=allowed_uuid,
        value=1.0,
        quality="good",
        source_adapter="api",
        ts=base_ts,
    )
    blocked_event = DataValueEvent(
        datapoint_id=blocked_uuid,
        value=2.0,
        quality="good",
        source_adapter="api",
        ts=base_ts,
    )

    await manager.handle_value_event(allowed_event)
    await manager.handle_value_event(blocked_event)

    scoped_ringbuffer = [m for m in scoped_ws.messages if m.get("action") == "ringbuffer_entry"]
    unrestricted_ringbuffer = [m for m in unrestricted_ws.messages if m.get("action") == "ringbuffer_entry"]

    assert [m["entry"]["datapoint_id"] for m in scoped_ringbuffer] == [allowed_id]
    assert all("metadata" not in m["entry"] for m in scoped_ringbuffer)
    assert [m["entry"]["datapoint_id"] for m in unrestricted_ringbuffer] == [allowed_id, blocked_id]


@pytest.mark.asyncio
async def test_handle_value_event_accepts_six_field_connection_entries(monkeypatch):
    dp_uuid = uuid4()
    dp_id = str(dp_uuid)
    ws = _FakeWebSocket()
    manager = WebSocketManager()
    conn_id = await manager.connect(ws)
    manager.subscribe(conn_id, [dp_id])

    ws_entry = manager._connections[conn_id]  # noqa: SLF001
    manager._connections[conn_id] = (ws_entry[0], ws_entry[1], asyncio.Lock(), ws_entry[3], False, None)  # noqa: SLF001

    class _RegistryStub:
        def get(self, _dp_id):
            return SimpleNamespace(name="Six Field DP", unit="W")

        def get_value(self, _dp_id):
            return SimpleNamespace(old_value=1.0)

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    event = DataValueEvent(
        datapoint_id=dp_uuid,
        value=2.0,
        quality="good",
        source_adapter="api",
        ts=datetime(2026, 5, 6, 19, 44, 49, 123000, tzinfo=UTC),
    )

    await manager.handle_value_event(event)

    assert ws.messages[0]["id"] == dp_id
    assert ws.messages[1]["action"] == "ringbuffer_entry"
    assert ws.messages[1]["entry"]["datapoint_id"] == dp_id


@pytest.mark.asyncio
async def test_page_allowed_datapoints_collects_only_datapoint_fields():
    nested_dp_id = str(uuid4())
    not_a_datapoint_uuid = str(uuid4())
    source_page_id_uuid = str(uuid4())
    entity_dp_id = str(uuid4())
    mini_widget_dp_id = str(uuid4())
    mini_widget_status_dp_id = str(uuid4())

    page_config = {
        "grid_cols": 12,
        "grid_row_height": 80,
        "background": None,
        "widgets": [
            {
                "id": str(uuid4()),
                "type": "horizontal_bar",
                "x": 0,
                "y": 0,
                "w": 2,
                "h": 2,
                "datapoint_id": str(uuid4()),
                "status_datapoint_id": None,
                "config": {
                    "bars": [
                        {"label": not_a_datapoint_uuid, "dp_id": nested_dp_id},
                        {"label": "B", "dp_id": str(uuid4())},
                    ],
                    "source_page_id": source_page_id_uuid,
                    "description": str(uuid4()),
                    "entities": [
                        {"id": entity_dp_id, "label": str(uuid4())},
                    ],
                    "miniWidgets": [
                        {
                            "id": str(uuid4()),
                            "widgetType": "display_value",
                            "datapointId": mini_widget_dp_id,
                            "statusDatapointId": mini_widget_status_dp_id,
                        },
                    ],
                },
            },
        ],
    }

    class _DbStub:
        async def fetchone(self, _sql, _params):
            return {"page_config": json.dumps(page_config)}

    ids = await _page_allowed_datapoints(_DbStub(), "page-1")

    assert ids is not None
    assert nested_dp_id in ids
    assert entity_dp_id in ids
    assert mini_widget_dp_id in ids
    assert mini_widget_status_dp_id in ids
    assert not_a_datapoint_uuid not in ids
    assert source_page_id_uuid not in ids


@pytest.mark.asyncio
async def test_page_allowed_datapoints_includes_widgetref_target_datapoints():
    target_dp_id = str(uuid4())
    target_status_dp_id = str(uuid4())
    nested_target_dp_id = str(uuid4())

    page_config_main = {
        "grid_cols": 12,
        "grid_row_height": 80,
        "background": None,
        "widgets": [
            {
                "id": str(uuid4()),
                "name": "ref-host",
                "type": "widget_ref",
                "x": 0,
                "y": 0,
                "w": 2,
                "h": 2,
                "datapoint_id": None,
                "status_datapoint_id": None,
                "config": {
                    "source_page_id": "page-target",
                    "source_widget_name": "kitchen-widget",
                },
            },
        ],
    }

    page_config_target = {
        "grid_cols": 12,
        "grid_row_height": 80,
        "background": None,
        "widgets": [
            {
                "id": str(uuid4()),
                "name": "kitchen-widget",
                "type": "horizontal_bar",
                "x": 0,
                "y": 0,
                "w": 2,
                "h": 2,
                "datapoint_id": target_dp_id,
                "status_datapoint_id": target_status_dp_id,
                "config": {
                    "bars": [
                        {"label": "A", "datapoint_id": nested_target_dp_id},
                    ],
                },
            },
        ],
    }

    class _DbStub:
        async def fetchone(self, _sql, params):
            if params[0] == "page-main":
                return {"page_config": json.dumps(page_config_main)}
            if params[0] == "page-target":
                return {"page_config": json.dumps(page_config_target)}
            return None

    ids = await _page_allowed_datapoints(_DbStub(), "page-main")

    assert ids is not None
    assert target_dp_id in ids
    assert target_status_dp_id in ids
    assert nested_target_dp_id in ids


@pytest.mark.asyncio
async def test_page_allowed_datapoints_skips_widgetref_target_when_access_denied():
    target_dp_id = str(uuid4())

    page_config_main = {
        "grid_cols": 12,
        "grid_row_height": 80,
        "background": None,
        "widgets": [
            {
                "id": str(uuid4()),
                "name": "ref-host",
                "type": "widget_ref",
                "x": 0,
                "y": 0,
                "w": 2,
                "h": 2,
                "datapoint_id": None,
                "status_datapoint_id": None,
                "config": {
                    "source_page_id": "page-target",
                    "source_widget_name": "kitchen-widget",
                },
            },
        ],
    }

    page_config_target = {
        "grid_cols": 12,
        "grid_row_height": 80,
        "background": None,
        "widgets": [
            {
                "id": str(uuid4()),
                "name": "kitchen-widget",
                "type": "horizontal_bar",
                "x": 0,
                "y": 0,
                "w": 2,
                "h": 2,
                "datapoint_id": target_dp_id,
                "status_datapoint_id": None,
                "config": {},
            },
        ],
    }

    class _DbStub:
        async def fetchone(self, _sql, params):
            if params[0] == "page-main":
                return {"page_config": json.dumps(page_config_main)}
            if params[0] == "page-target":
                return {"page_config": json.dumps(page_config_target)}
            return None

    async def _deny_target(page_id: str) -> bool:
        return page_id != "page-target"

    ids = await _page_allowed_datapoints(
        _DbStub(),
        "page-main",
        widget_ref_access_check=_deny_target,
    )

    assert ids is not None
    assert target_dp_id not in ids


def test_extract_subprotocol_tokens_prefers_jwt_over_session():
    ws = SimpleNamespace(scope={"subprotocols": ["obs.session.session-abc", "obs.jwt.jwt-token-123"]})

    jwt_token, session_token, selected = _extract_subprotocol_tokens(ws)

    assert jwt_token == "jwt-token-123"
    assert session_token == "session-abc"
    assert selected == "obs.jwt.jwt-token-123"


def test_extract_subprotocol_tokens_accepts_session_when_jwt_missing():
    ws = SimpleNamespace(scope={"subprotocols": ["obs.session.session-only-token"]})

    jwt_token, session_token, selected = _extract_subprotocol_tokens(ws)

    assert jwt_token is None
    assert session_token == "session-only-token"
    assert selected == "obs.session.session-only-token"
