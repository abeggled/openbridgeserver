"""Manager-level execution behaviour of the ``edge_detect`` function block.

Assertions about the node definition live in
``tests/unit/logic/nodes/logic/test_edge_detect.py``; the dispatcher branch is
covered by ``TestEdgeDetectNode`` in ``tests/unit/test_executor.py``. This file
covers the part neither of those can show: what a real graph run publishes to a
downstream Write Object.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node


def _manager(values: dict[str, object] | None = None) -> LogicManager:
    """Manager whose registry seeds only the given DataPoint ids.

    The real registry creates an empty ValueState (value=None) as soon as a
    DataPoint is registered, long before any adapter writes a real value — a
    bare MagicMock would hand out a truthy attribute instead and hide the
    unseeded case entirely.
    """
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
    seeded = values or {}
    registry.get_value.side_effect = lambda dp_id: SimpleNamespace(value=seeded.get(str(dp_id)), ts=None)
    return LogicManager(AsyncMock(), AsyncMock(), registry)


def _write_flow(target: uuid.UUID, data: dict | None = None) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [node("ed", "edge_detect", data or {}), node("w", "datapoint_write", {"datapoint_id": str(target)})],
            "edges": [edge("ed", "w", "out", "value")],
        }
    )


async def _run(manager: LogicManager, flow: FlowData, value: object) -> object | None:
    """Execute one graph run and return the value written, or None."""
    before = manager._event_bus.publish.await_count
    await manager._execute_graph("g", "G", flow, {"ed": {"in": value}})
    if manager._event_bus.publish.await_count == before:
        return None
    return manager._event_bus.publish.await_args.args[0].value


@pytest.mark.asyncio
async def test_write_object_is_driven_only_by_edges_not_by_every_run():
    manager = _manager()
    flow = _write_flow(uuid.uuid4())
    manager._graphs["g"] = ("G", True, flow)

    # First value only seeds the level — a save/startup must not actuate.
    assert await _run(manager, flow, False) is None
    assert await _run(manager, flow, True) is True
    # Repeated identical level: "out" stays absent, so nothing is written.
    assert await _run(manager, flow, True) is None
    assert await _run(manager, flow, False) is False
    assert await _run(manager, flow, False) is None


@pytest.mark.asyncio
async def test_send_on_falling_disabled_writes_only_on_the_rising_edge():
    manager = _manager()
    flow = _write_flow(uuid.uuid4(), {"send_on_falling": False})
    manager._graphs["g"] = ("G", True, flow)

    assert await _run(manager, flow, False) is None
    assert await _run(manager, flow, True) is True
    assert await _run(manager, flow, False) is None


@pytest.mark.asyncio
async def test_remembered_level_is_persisted_so_a_restart_resumes_edgeless():
    manager = _manager()
    flow = _write_flow(uuid.uuid4())
    manager._graphs["g"] = ("G", True, flow)

    await _run(manager, flow, True)

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._db.execute_and_commit.assert_awaited()


@pytest.mark.asyncio
async def test_unrelated_branch_run_does_not_corrupt_the_remembered_level():
    """Issue #1090: on an event-driven run, a Change Filter on an *unrelated*
    branch reports changed=False because it was not re-evaluated with fresh
    data, not because its signal went low. Committing that no-pulse
    placeholder as a level would make the next real pulse look like a rising
    edge and publish a write that never happened."""
    manager = _manager()
    dp_a, dp_b, target = str(uuid.uuid4()), str(uuid.uuid4()), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("rA", "datapoint_read", {"datapoint_id": dp_a}),
                node("cf", "change_filter"),
                node("ed", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
                node("rB", "datapoint_read", {"datapoint_id": dp_b}),
            ],
            "edges": [
                edge("rA", "cf", "value", "in"),
                edge("cf", "ed", "changed", "in"),
                edge("ed", "w", "out", "value"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {"rA": {"value": 1, "changed": True}})
    assert manager._hysteresis["g"]["ed"] == {"value": True}

    # A run driven purely by the other branch must leave the level alone.
    await manager._execute_graph("g", "G", flow, {"rB": {"value": 9, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_unseeded_read_object_does_not_seed_a_level_or_fire_on_first_value():
    """A Read Object without a value emits None. That is "nothing arrived",
    not a low level — otherwise the first real value would look like a rising
    edge and write, contradicting "the first value produces no edge"."""
    manager = _manager()
    dp, target = str(uuid.uuid4()), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("r", "datapoint_read", {"datapoint_id": dp}),
                node("ed", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [edge("r", "ed", "value", "in"), edge("ed", "w", "out", "value")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    # Graph run while the Read Object still has no value at all.
    await manager._execute_graph("g", "G", flow, {})
    assert "ed" not in manager._hysteresis.get("g", {})

    # The first real value only seeds the level — no edge, no write.
    await manager._execute_graph("g", "G", flow, {"r": {"value": True, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_level_survives_an_unrelated_run_without_a_downstream_write_object():
    """Same corruption as above, but on a sheet whose only stateful node is the
    Edge Detect itself. A downstream Write Object would already force the
    pre-execute snapshot that the correction pass restores state from; without
    one, only Edge Detect's own membership in the manager's stateful-relay set
    can, so this is what pins that registration down."""
    dp_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    # Both Read Objects seeded: no unseeded-read rollback, no async node and no
    # Write Object, so nothing else asks for the snapshot.
    manager = _manager({dp_a: 1, dp_b: 9})
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("rA", "datapoint_read", {"datapoint_id": dp_a}),
                node("cf", "change_filter"),
                node("ed", "edge_detect"),
                node("rB", "datapoint_read", {"datapoint_id": dp_b}),
            ],
            "edges": [edge("rA", "cf", "value", "in"), edge("cf", "ed", "changed", "in")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {"rA": {"value": 1, "changed": True}})
    assert manager._hysteresis["g"]["ed"] == {"value": True}

    await manager._execute_graph("g", "G", flow, {"rB": {"value": 9, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}
