"""Tests for the asynchronous value-sequence logic node."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from obs.logic.manager import LogicManager
from obs.logic.node_types import get_node_type


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
