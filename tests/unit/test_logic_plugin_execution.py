"""Executing plugin-contributed logic blocks (issue #446)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic import plugin_registry
from obs.logic.executor import GraphExecutor
from obs.logic.manager import LogicManager, _cron_fire_overrides
from obs.logic.models import FlowData
from obs.logic.plugin_api import LogicNodePlugin, NodeTypeDef, NodeTypePort, register_node_type


def _counter(type_name: str, *, in_place: bool) -> type[LogicNodePlugin]:
    class Counter(LogicNodePlugin):
        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(
                type=type_name,
                label="Counter",
                category="integration",
                inputs=[NodeTypePort(id="trigger", label="Trigger", type="trigger")],
                outputs=[NodeTypePort(id="count", label="Count")],
            )

        @classmethod
        def evaluate(cls, node_id: str, inputs: dict[str, Any], config: dict[str, Any], state: dict[str, Any]):
            count = state.get("count", 0) + int(config.get("step", 1))
            if in_place:
                state["count"] = count
                return {"count": count}, state
            return {"count": count}, {"count": count}

    Counter.type_name = type_name
    return Counter


@pytest.fixture(params=[True, False], ids=["in_place", "new_dict"])
def counter_type(request):
    type_name = f"test_counter_{request.node.name.split('[')[0][-20:]}_{request.param}"
    register_node_type(_counter(type_name, in_place=request.param))
    yield type_name
    plugin_registry._unregister(type_name)


def _flow(counter_type: str) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [
                {"id": "tick", "type": "timer_interval", "position": {"x": 0, "y": 0}, "data": {"interval_s": 1}},
                {"id": "c", "type": counter_type, "position": {"x": 0, "y": 0}, "data": {"step": 2}},
            ],
            "edges": [{"id": "e", "source": "tick", "sourceHandle": "trigger", "target": "c", "targetHandle": "trigger"}],
        }
    )


def test_executor_keeps_the_state_a_plugin_returns(counter_type):
    executor = GraphExecutor(_flow(counter_type))
    counts = [executor.execute({"tick": {"trigger": True}})["c"]["count"] for _ in range(3)]
    assert counts == [2, 4, 6]
    assert executor.hysteresis_state["c"] == {"count": 6}


@pytest.mark.asyncio
async def test_plugin_state_survives_consecutive_manager_runs(counter_type):
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    registry.get_value = MagicMock(return_value=None)
    mgr = LogicManager(db, event_bus, registry)
    flow = _flow(counter_type)
    mgr._graphs = {"g": ("G", True, flow)}

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        counts = [(await mgr._execute_graph("g", "G", flow, _cron_fire_overrides(["tick"])))["c"]["count"] for _ in range(3)]

    assert counts == [2, 4, 6]


def test_unknown_node_type_yields_no_outputs():
    flow = FlowData.model_validate({"nodes": [{"id": "x", "type": "no_such_block_zzz", "position": {"x": 0, "y": 0}, "data": {}}]})
    assert GraphExecutor(flow).execute({}).get("x", {}) == {}
