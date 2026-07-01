from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from obs.logic.node_types import get_node_type
from tests.unit.conftest import node


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
