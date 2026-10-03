"""Trigger mode of the wake_on_lan / host_check action blocks (issue #1274).

"event" fires on every freshly delivered truthy trigger — a repeated TRUE
telegram on the driving DataPoint must send/ping again — while "edge" (and
nodes saved before the option existed) keeps the rising-edge dedup. Driven
through the real DataPoint entry point (``_on_value_event``) so the registry
re-seeding of unrelated Read Objects is exercised exactly as in production.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.core.event_bus import DataValueEvent
from obs.logic.manager import LogicManager, _trigger_mode
from obs.logic.models import FlowData

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_WOL = {"mac_address": "AA:BB:CC:DD:EE:FF"}
_HC = {"host": "192.168.1.1", "timeout_s": 1, "count": 1}


def _flow(nodes: list[dict], edges: list[dict]) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [{"position": {"x": 0, "y": 0}, **n} for n in nodes],
            "edges": [{"id": f"e{i}", **e} for i, e in enumerate(edges)],
        }
    )


def _make_manager(flow: FlowData, values: dict[str, object]) -> LogicManager:
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    registry = MagicMock()
    registry.get_value = MagicMock(side_effect=lambda dp_id: SimpleNamespace(value=values[str(dp_id)], ts=_TS) if str(dp_id) in values else None)
    mgr = LogicManager(db, MagicMock(publish=AsyncMock()), registry)
    mgr._graphs = {"g": ("G", True, flow)}
    mgr._node_state = {"g": {}}
    return mgr


class _Sheet:
    """One Read Object (optionally two, joined by AND) driving one action block."""

    def __init__(self, action_type: str, action_data: dict, *, with_second_input: bool = False) -> None:
        self.dp_a = str(uuid.uuid4())
        self.dp_b = str(uuid.uuid4())
        self.values: dict[str, object] = {self.dp_a: False, self.dp_b: True}
        nodes = [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": self.dp_a}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": self.dp_b}},
            {"id": "act", "type": action_type, "data": dict(action_data)},
        ]
        if with_second_input:
            nodes.append({"id": "and", "type": "and", "data": {}})
            edges = [
                {"source": "rA", "sourceHandle": "value", "target": "and", "targetHandle": "in1"},
                {"source": "rB", "sourceHandle": "value", "target": "and", "targetHandle": "in2"},
                {"source": "and", "sourceHandle": "out", "target": "act", "targetHandle": "trigger"},
            ]
        else:
            edges = [{"source": "rA", "sourceHandle": "value", "target": "act", "targetHandle": "trigger"}]
        self.flow = _flow(nodes, edges)
        self.mgr = _make_manager(self.flow, self.values)

    def event(self, dp_id: str, value: object) -> None:
        self.values[dp_id] = value
        asyncio.run(self.mgr._on_value_event(DataValueEvent(datapoint_id=uuid.UUID(dp_id), value=value, quality="good", source_adapter="knx")))

    def manual_run(self) -> None:
        asyncio.run(self.mgr.execute_graph("g"))


_ACTIONS = [
    pytest.param("wake_on_lan", _WOL, "obs.logic.manager.asyncio.to_thread", None, id="wake_on_lan"),
    pytest.param("host_check", _HC, "obs.logic.manager._ping_host", (True, 1.0), id="host_check"),
]


@pytest.fixture(autouse=True)
def _no_websocket():
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        yield


@pytest.mark.parametrize(("action_type", "action_data", "target", "result"), _ACTIONS)
def test_event_mode_fires_on_every_repeated_true_telegram(action_type, action_data, target, result):
    """Issue #1274: TRUE, TRUE, TRUE on the trigger DataPoint → three actions."""
    sheet = _Sheet(action_type, {**action_data, "trigger_mode": "event"})
    with patch(target, new_callable=AsyncMock, return_value=result) as action:
        sheet.event(sheet.dp_a, True)
        sheet.event(sheet.dp_a, True)
        sheet.event(sheet.dp_a, True)
    assert action.await_count == 3


@pytest.mark.parametrize(("action_type", "action_data", "target", "result"), _ACTIONS)
@pytest.mark.parametrize("mode", [{"trigger_mode": "edge"}, {}], ids=["edge", "legacy-without-field"])
def test_edge_mode_and_legacy_nodes_keep_rising_edge_dedup(action_type, action_data, target, result, mode):
    sheet = _Sheet(action_type, {**action_data, **mode})
    with patch(target, new_callable=AsyncMock, return_value=result) as action:
        sheet.event(sheet.dp_a, True)
        sheet.event(sheet.dp_a, True)
        sheet.event(sheet.dp_a, False)
        sheet.event(sheet.dp_a, True)
    assert action.await_count == 2


@pytest.mark.parametrize(("action_type", "action_data", "target", "result"), _ACTIONS)
def test_event_mode_ignores_unrelated_datapoint_events(action_type, action_data, target, result):
    """An event on a DataPoint that does not feed the trigger merely re-seeds
    the trigger's Read Object from the registry — that is no fresh TRUE."""
    sheet = _Sheet(action_type, {**action_data, "trigger_mode": "event"})
    with patch(target, new_callable=AsyncMock, return_value=result) as action:
        sheet.event(sheet.dp_a, True)
        sheet.event(sheet.dp_b, False)
        sheet.event(sheet.dp_b, True)
    assert action.await_count == 1


@pytest.mark.parametrize(("action_type", "action_data", "target", "result"), _ACTIONS)
def test_event_mode_fresh_event_travels_through_logic_relay(action_type, action_data, target, result):
    sheet = _Sheet(action_type, {**action_data, "trigger_mode": "event"}, with_second_input=True)
    with patch(target, new_callable=AsyncMock, return_value=result) as action:
        sheet.event(sheet.dp_a, True)  # A AND B(seeded True) → fire
        sheet.event(sheet.dp_a, True)  # repeated TRUE on A → fire again
        sheet.event(sheet.dp_b, True)  # repeated TRUE on B also reaches the AND → fire
        sheet.event(sheet.dp_b, False)  # AND drops → nothing
    assert action.await_count == 3


@pytest.mark.parametrize(("action_type", "action_data", "target", "result"), _ACTIONS)
@pytest.mark.parametrize(("mode", "expected"), [("event", 2), ("edge", 0)])
def test_manual_run_counts_every_input_as_fresh_only_in_event_mode(action_type, action_data, target, result, mode, expected):
    sheet = _Sheet(action_type, {**action_data, "trigger_mode": mode})
    with patch(target, new_callable=AsyncMock, return_value=result) as action:
        sheet.event(sheet.dp_a, True)
        action.reset_mock()
        sheet.manual_run()
        sheet.manual_run()
    assert action.await_count == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (None, "edge"),
        ({}, "edge"),
        ({"trigger_mode": "edge"}, "edge"),
        ({"trigger_mode": "event"}, "event"),
        ({"trigger_mode": "bogus"}, "edge"),
    ],
)
def test_trigger_mode_defaults_to_edge_for_missing_or_unknown_values(data, expected):
    assert _trigger_mode(data) == expected
