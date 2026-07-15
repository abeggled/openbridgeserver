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
from tests.unit.conftest import edge, node


def _manager() -> LogicManager:
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
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
            4,
        ),
    )

    assert manager._event_bus.publish.await_count == 2
    assert [call.args[0].value for call in manager._event_bus.publish.await_args_list] == ["blue", True]
    assert all(call.args[0].source_adapter == "logic_sequence" for call in manager._event_bus.publish.await_args_list)
    assert all(call.args[0].logic_depth == 5 for call in manager._event_bus.publish.await_args_list)


def test_value_sequence_skips_missing_target() -> None:
    manager = _manager()
    manager._registry.get.return_value = None

    asyncio.run(manager._run_value_sequence("graph", "node", {"steps": [{"datapoint_id": str(uuid.uuid4()), "value": 1}]}))

    manager._event_bus.publish.assert_not_awaited()


def test_value_sequence_coerces_text_editor_values_to_target_type() -> None:
    manager = _manager()
    target = uuid.uuid4()
    manager._registry.get.return_value = SimpleNamespace(data_type="BOOLEAN")

    asyncio.run(manager._run_value_sequence("graph", "node", {"steps": [{"datapoint_id": str(target), "value": "true"}]}))

    assert manager._event_bus.publish.await_args.args[0].value is True


def test_value_sequence_coercion_supports_numbers_and_rejects_invalid_booleans() -> None:
    manager = _manager()
    assert manager._coerce_sequence_value("12", "INTEGER") == 12
    assert manager._coerce_sequence_value("1.5", "FLOAT") == 1.5
    assert manager._coerce_sequence_value("text", "STRING") == "text"
    try:
        manager._coerce_sequence_value("perhaps", "BOOLEAN")
    except ValueError as exc:
        assert "invalid boolean" in str(exc)
    else:
        raise AssertionError("invalid boolean must be rejected")


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


def test_value_sequence_handles_pause_invalid_values_and_while_condition() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        key = ("graph", "node")
        manager._sequence_conditions[key] = True

        async def publish(event):
            manager._sequence_conditions[key] = False

        manager._event_bus.publish.side_effect = publish
        await manager._run_value_sequence(
            "graph",
            "node",
            {
                "run_mode": "while_condition",
                "steps": [
                    {"delay_ms": "invalid"},
                    {"datapoint_id": "not-a-uuid", "value": 1},
                    {"datapoint_id": str(target), "value": 2},
                ],
            },
        )
        manager._event_bus.publish.assert_awaited_once()

        assert LogicManager._sequence_steps("not json") == []
        assert LogicManager._sequence_steps('[{"value": 1}]') == [{"value": 1}]

    asyncio.run(exercise())


def test_while_sequence_with_no_steps_or_noop_steps_returns_without_spinning() -> None:
    async def exercise() -> None:
        manager = _manager()
        await asyncio.wait_for(manager._run_value_sequence("graph", "empty", {"run_mode": "while_condition", "steps": []}), timeout=0.1)
        await asyncio.wait_for(manager._run_value_sequence("graph", "noop", {"run_mode": "while_condition", "steps": [{"delay_ms": 0}]}), timeout=0.1)
        manager._event_bus.publish.assert_not_awaited()

    asyncio.run(exercise())


def test_sequence_task_cancellation_clears_graph_runtime_state() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        node = SimpleNamespace(
            id="node",
            data={"steps": [{"datapoint_id": str(target), "value": 1, "delay_ms": 1000}]},
        )
        manager._start_value_sequence("graph", node, True)
        task = manager._sequence_tasks[("graph", "node")]
        manager._cancel_sequence_tasks("graph")
        assert task.cancelled() is False
        await asyncio.gather(task, return_exceptions=True)
        assert not manager._sequence_tasks
        assert not manager._sequence_conditions

    asyncio.run(exercise())


def test_graph_execution_starts_and_cancels_a_conditioned_sequence() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("trigger", "const_value", {"value": "true", "data_type": "bool"}),
                    node("condition", "const_value", {"value": "false", "data_type": "bool"}),
                    node(
                        "sequence",
                        "value_sequence",
                        {
                            "cancel_when_condition_false": True,
                            "steps": [{"datapoint_id": str(target), "value": 1, "delay_ms": 1000}],
                        },
                    ),
                ],
                "edges": [edge("trigger", "sequence", "value", "trigger"), edge("condition", "sequence", "value", "condition")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {})
        task = manager._sequence_tasks[("graph", "sequence")]
        await asyncio.gather(task, return_exceptions=True)
        manager._event_bus.publish.assert_not_awaited()

        # The level trigger stays high, so the manager records it but does not
        # launch a duplicate task until it sees a new rising edge.
        await manager._execute_graph("graph", "Graph", flow, {})
        assert ("graph", "sequence") not in manager._sequence_tasks

    asyncio.run(exercise())


def test_value_sequence_ignores_a_trigger_while_already_running() -> None:
    async def exercise() -> None:
        manager = _manager()
        node = SimpleNamespace(id="node", data={"restart_policy": "ignore", "steps": [{"delay_ms": 1000}]})
        manager._start_value_sequence("graph", node, True)
        original = manager._sequence_tasks[("graph", "node")]
        manager._start_value_sequence("graph", node, True)
        assert manager._sequence_tasks[("graph", "node")] is original
        manager._cancel_sequence_tasks()
        await asyncio.gather(original, return_exceptions=True)

    asyncio.run(exercise())


def test_datapoint_rename_updates_sequence_step_labels() -> None:
    async def exercise() -> None:
        manager = _manager()
        dp_id = uuid.uuid4()
        flow = FlowData.model_validate(
            {"nodes": [node("sequence", "value_sequence", {"steps": [{"datapoint_id": str(dp_id), "datapoint_name": "Old"}]})], "edges": []},
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._on_datapoint_renamed(SimpleNamespace(dp_id=dp_id, old_name="Old", new_name="New"))
        assert flow.nodes[0].data["steps"][0]["datapoint_name"] == "New"
        manager._db.execute_and_commit.assert_awaited_once()

    asyncio.run(exercise())
