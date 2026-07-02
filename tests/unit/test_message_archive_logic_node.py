from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from obs.logic.node_types import get_node_type
from tests.unit.conftest import edge, node


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> FlowData:
    return FlowData.model_validate({"nodes": nodes, "edges": edges or []})


def _make_manager() -> LogicManager:
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    event_bus = AsyncMock()
    registry = MagicMock()
    registry.get_value.return_value = None
    return LogicManager(db, event_bus, registry)


def _run(manager: LogicManager, flow: FlowData, overrides: dict | None = None) -> dict:
    graph_id = "archive-graph"
    manager._graphs[graph_id] = ("Archiv Test", True, flow)
    manager._node_state[graph_id] = {}
    return asyncio.run(
        manager._execute_graph(
            graph_id,
            "Archiv Test",
            flow,
            overrides if overrides is not None else {"ma": {"trigger": True}},
        ),
    )


class _MockResponse:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


def _patch_api_success():
    patcher = patch("obs.logic.manager.httpx.AsyncClient")
    mock_client_cls = patcher.start()
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = AsyncMock(return_value=_MockResponse())
    return patcher, mock_client


def test_message_archive_node_type_is_registered() -> None:
    node_type = get_node_type("message_archive")

    assert node_type is not None
    assert node_type.label == "Meldungsarchiv"
    assert any(port.id == "message" for port in node_type.inputs)
    assert any(port.id == "stored" for port in node_type.outputs)
    assert "critical" in node_type.config_schema["severity"]["enum"]


def test_message_archive_node_records_entry() -> None:
    manager = _make_manager()
    flow = _flow(
        [
            node(
                "ma",
                "message_archive",
                {
                    "archive_id": "Alerts",
                    "type": "automation",
                    "severity": "critical",
                    "title": "Fallback title",
                    "message": "Fallback message",
                },
            )
        ]
    )
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow, {"ma": {"trigger": True, "message": "Input message", "title": "Input title"}})

    assert outputs["ma"]["stored"] is True
    service.record.assert_awaited_once_with(
        "alerts",
        type="automation",
        severity="critical",
        source="logic.graph.archive-graph.node.ma",
        title="Input title",
        message="Input message",
        payload={
            "graph_id": "archive-graph",
            "graph_name": "Archiv Test",
            "node_id": "ma",
            "node_label": "",
        },
    )


def test_message_archive_stored_output_replays_downstream_nodes() -> None:
    manager = _make_manager()
    flow = _flow(
        [
            node("ma", "message_archive", {"archive_id": "Alerts", "message": "Stored"}),
            node("truth", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ],
        [
            edge("ma", "gate", "stored", "in1"),
            edge("truth", "gate", "value", "in2"),
        ],
    )
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow, {"ma": {"trigger": True}})

    assert outputs["ma"]["stored"] is True
    assert outputs["gate"]["out"] is True


def test_message_archive_replay_runs_downstream_api_client() -> None:
    manager = _make_manager()
    flow = _flow(
        [
            node("ma", "message_archive", {"archive_id": "Alerts", "message": "Stored"}),
            node("api", "api_client", {"url": "http://93.184.216.34/hook", "method": "GET"}),
        ],
        [edge("ma", "api", "stored", "trigger")],
    )
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})
    patcher, mock_client = _patch_api_success()

    try:
        with patch("obs.message_archive.get_message_archive_service", return_value=service):
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = _run(manager, flow, {"ma": {"trigger": True}})
    finally:
        patcher.stop()

    service.record.assert_awaited_once()
    mock_client.request.assert_awaited_once()
    assert outputs["ma"]["stored"] is True
    assert outputs["api"]["success"] is True


def test_message_archive_replay_runs_downstream_host_check_and_wol() -> None:
    manager = _make_manager()
    flow = _flow(
        [
            node("ma", "message_archive", {"archive_id": "Alerts", "message": "Stored"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
        ],
        [
            edge("ma", "hc", "stored", "trigger"),
            edge("hc", "wol", "reachable", "trigger"),
        ],
    )
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
                with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                    outputs = _run(manager, flow, {"ma": {"trigger": True}})

    service.record.assert_awaited_once()
    mock_ping.assert_awaited_once()
    mock_to_thread.assert_awaited_once()
    assert outputs["hc"]["reachable"] is True
    assert outputs["wol"]["sent"] is True


def test_message_archive_replay_runs_downstream_message_archive() -> None:
    manager = _make_manager()
    flow = _flow(
        [
            node("ma1", "message_archive", {"archive_id": "Alerts", "message": "Stored"}),
            node("ma2", "message_archive", {"archive_id": "Audit", "message": "Stored again"}),
        ],
        [edge("ma1", "ma2", "stored", "trigger")],
    )
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow, {"ma1": {"trigger": True}})

    assert service.record.await_count == 2
    assert service.record.await_args_list[0].args[0] == "alerts"
    assert service.record.await_args_list[1].args[0] == "audit"
    assert outputs["ma1"]["stored"] is True
    assert outputs["ma2"]["stored"] is True


def test_message_archive_node_does_not_record_without_trigger() -> None:
    manager = _make_manager()
    flow = _flow([node("ma", "message_archive", {"archive_id": "Alerts", "message": "Fallback"})])
    service = MagicMock()
    service.record = AsyncMock()

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow, {"ma": {"trigger": False}})

    assert outputs["ma"]["stored"] is False
    service.record.assert_not_awaited()


def test_message_archive_node_keeps_stored_false_when_record_fails() -> None:
    manager = _make_manager()
    flow = _flow([node("ma", "message_archive", {"archive_id": "Alerts", "message": "Fallback"})])
    service = MagicMock()
    service.record = AsyncMock(side_effect=RuntimeError("archive unavailable"))

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow, {"ma": {"trigger": True}})

    assert outputs["ma"]["stored"] is False
    service.record.assert_awaited_once()


def test_message_archive_node_does_not_record_without_archive() -> None:
    manager = _make_manager()
    flow = _flow([node("ma", "message_archive", {"archive_id": "", "message": "Fallback"})])
    service = MagicMock()
    service.record = AsyncMock()

    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = _run(manager, flow)

    assert outputs["ma"]["stored"] is False
    service.record.assert_not_awaited()
