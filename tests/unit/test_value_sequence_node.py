"""Tests for the asynchronous value-sequence logic node."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from obs.logic.executor import GraphExecutor
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from obs.logic.node_types import get_node_type
from tests.unit.conftest import node


def _manager() -> LogicManager:
    registry = MagicMock()
    registry.get.return_value = object()
    return LogicManager(AsyncMock(), AsyncMock(), registry)


def test_value_sequence_node_type_is_registered() -> None:
    node_type = get_node_type("value_sequence")

    assert node_type is not None
    assert node_type.category == "timer"
    assert {port.id for port in node_type.inputs} == {"trigger", "condition"}
    assert node_type.config_schema["restart_policy"]["enum"] == ["ignore", "restart", "queue"]


def test_value_sequence_executor_exposes_control_inputs_without_waiting() -> None:
    flow = FlowData.model_validate({"nodes": [node("sequence", "value_sequence")], "edges": []})

    outputs = GraphExecutor(flow).execute(
        {"sequence": {"trigger": True, "condition": False}},
    )

    assert outputs["sequence"] == {"_triggered": True, "_condition": False}


def test_value_sequence_publishes_values_and_pauses() -> None:
    manager = _manager()
    target = uuid.uuid4()

    asyncio.run(
        manager._run_value_sequence(
            "graph",
            "node",
            {"steps": [{"datapoint_id": str(target), "value": "blue", "delay_ms": 0}, {"datapoint_id": str(target), "value": True, "delay_ms": 0}]},
        ),
    )

    assert manager._event_bus.publish.await_count == 2
    assert [call.args[0].value for call in manager._event_bus.publish.await_args_list] == ["blue", True]
    assert all(call.args[0].source_adapter == "logic_sequence" for call in manager._event_bus.publish.await_args_list)


def test_value_sequence_skips_missing_target() -> None:
    manager = _manager()
    manager._registry.get.return_value = None

    asyncio.run(manager._run_value_sequence("graph", "node", {"steps": [{"datapoint_id": str(uuid.uuid4()), "value": 1}]}))

    manager._event_bus.publish.assert_not_awaited()


def test_value_sequence_repeats_and_stops_when_condition_is_false() -> None:
    manager = _manager()
    target = uuid.uuid4()

    asyncio.run(
        manager._run_value_sequence(
            "graph",
            "node",
            {
                "run_mode": "repeat_count",
                "repeat_count": 2,
                "steps": [{"datapoint_id": str(target), "value": 1}],
            },
        ),
    )
    assert manager._event_bus.publish.await_count == 2

    manager._event_bus.publish.reset_mock()
    manager._sequence_conditions[("graph", "cancelled")] = False
    asyncio.run(
        manager._run_value_sequence(
            "graph",
            "cancelled",
            {
                "cancel_when_condition_false": True,
                "steps": [{"datapoint_id": str(target), "value": 1}],
            },
        ),
    )
    manager._event_bus.publish.assert_not_awaited()


def test_value_sequence_restart_and_queue_policies() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        node = SimpleNamespace(
            id="node",
            data={
                "restart_policy": "restart",
                "steps": [{"datapoint_id": str(target), "value": 1, "delay_ms": 10}],
            },
        )
        manager._start_value_sequence("graph", node, True)
        original = manager._sequence_tasks[("graph", "node")]
        manager._start_value_sequence("graph", node, True)
        restarted = manager._sequence_tasks[("graph", "node")]
        assert restarted is not original
        await restarted

        node.data["restart_policy"] = "queue"
        manager._start_value_sequence("graph", node, True)
        manager._start_value_sequence("graph", node, True)
        assert manager._sequence_queues[("graph", "node")] == 1
        await manager._sequence_tasks[("graph", "node")]
        await manager._sequence_tasks[("graph", "node")]
        assert manager._event_bus.publish.await_count == 3

    asyncio.run(exercise())
