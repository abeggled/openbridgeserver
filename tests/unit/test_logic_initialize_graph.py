"""Unit tests for LogicManager.initialize_graph (issue #1031).

Verifies that the post-save initialization pass:
  - publishes the current registry value of seeded Read Objects through
    datapoint_write nodes and primes the read-node event-filter state,
  - is a no-op for unknown graphs, disabled graphs, graphs without a
    configured datapoint_read node and graphs where no Read Object has a
    current value,
  - suppresses writes that descend from an unseeded Read Object instead of
    publishing coerced 0/False values,
  - does not mutate stateful node accumulators (statistics),
  - swallows execution errors instead of failing the save request.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData


def _make_manager(graphs: dict, values: dict | None = None) -> LogicManager:
    """LogicManager with an in-memory graph cache and a value-map registry."""
    db = MagicMock()
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    value_map = {uuid.UUID(k): v for k, v in (values or {}).items()}
    registry.get_value = MagicMock(side_effect=lambda dp_id: SimpleNamespace(value=value_map[dp_id]) if dp_id in value_map else None)

    mgr = LogicManager(db, event_bus, registry)
    mgr._graphs = graphs
    return mgr


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [{"position": {"x": 0, "y": 0}, **n} for n in nodes],
            "edges": [{"id": f"e{i}", **e} for i, e in enumerate(edges or [])],
        }
    )


def _read_write_flow(src_id: str, dst_id: str) -> FlowData:
    return _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [{"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"}],
    )


@pytest.mark.asyncio
async def test_seeded_read_publishes_write_and_primes_filter_state():
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 42})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    event = mgr._event_bus.publish.await_args.args[0]
    assert event.datapoint_id == uuid.UUID(dst_id)
    assert event.value == 42
    assert event.source_adapter == "logic"

    # Event filters (trigger_on_change, min_delta, throttle) are primed
    read_state = mgr._node_state["g1"]["r1"]
    assert read_state["last_value"] == 42
    assert read_state["last_ts"] is not None


@pytest.mark.asyncio
async def test_unknown_graph_is_noop():
    mgr = _make_manager({})

    await mgr.initialize_graph("missing")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_graph_is_noop():
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", False, _read_write_flow(src_id, dst_id))}, values={src_id: 42})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_without_configured_read_node_is_noop():
    flow = _flow(
        [
            {"id": "n0", "type": "and", "data": {}},
            {"id": "n1", "type": "datapoint_read", "data": {"datapoint_name": "unconfigured"}},
        ]
    )
    mgr = _make_manager({"g1": ("G", True, flow)})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    mgr._registry.get_value.assert_not_called()


@pytest.mark.asyncio
async def test_no_current_value_is_noop():
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    assert mgr._node_state.get("g1", {}).get("r1") is None


@pytest.mark.asyncio
async def test_write_descending_from_unseeded_read_is_suppressed():
    """An unseeded Read Object taints its subgraph — downstream nodes would
    coerce its None to 0/False and publish a bogus value otherwise."""
    src_a, dst_a = str(uuid.uuid4()), str(uuid.uuid4())
    src_b, dst_b = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wA", "type": "datapoint_write", "data": {"datapoint_id": dst_a}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": src_b}},
            {"id": "mB", "type": "math_map", "data": {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dst_b}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wA", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "mB", "targetHandle": "value"},
            {"source": "mB", "sourceHandle": "result", "target": "wB", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    event = mgr._event_bus.publish.await_args.args[0]
    assert event.datapoint_id == uuid.UUID(dst_a)
    assert event.value == 7


@pytest.mark.asyncio
async def test_statistics_accumulators_are_not_mutated():
    """The registry seed is not a fresh sample — stateful nodes keep their
    accumulated state untouched."""
    src_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "s1", "type": "statistics", "data": {}},
        ],
        [{"source": "r1", "sourceHandle": "value", "target": "s1", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 10})
    mgr._hysteresis["g1"] = {"s1": {"s_min": 3.0, "s_max": 8.0, "s_sum": 25.0, "s_count": 5}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["s1"] == {"s_min": 3.0, "s_max": 8.0, "s_sum": 25.0, "s_count": 5}


@pytest.mark.asyncio
async def test_execution_error_is_swallowed():
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 42})
    mgr._apply_datapoint_write_outputs = AsyncMock(side_effect=RuntimeError("boom"))

    await mgr.initialize_graph("g1")

    mgr._apply_datapoint_write_outputs.assert_awaited_once()


# ---------------------------------------------------------------------------
# _apply_datapoint_write_outputs — trigger gating, write-side filters
# ---------------------------------------------------------------------------


def _write_flow(dst_id: str, data: dict | None = None, *, wire_trigger: bool = False) -> FlowData:
    edges = [{"source": "x1", "sourceHandle": "result", "target": "w1", "targetHandle": "trigger"}] if wire_trigger else []
    return _flow([{"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id, **(data or {})}}], edges)


async def _apply(mgr, flow: FlowData, outputs: dict, graph_state: dict | None = None, **kwargs) -> dict:
    from datetime import UTC, datetime

    graph_state = graph_state if graph_state is not None else {}
    wired_inputs = {(e.target, e.targetHandle or "in") for e in flow.edges}
    await mgr._apply_datapoint_write_outputs("g1", flow, outputs, graph_state, wired_inputs, datetime.now(UTC), 0, **kwargs)
    return graph_state


@pytest.mark.asyncio
async def test_write_outputs_wired_trigger_gates_publish():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, wire_trigger=True)

    await _apply(mgr, flow, {"w1": {"_write_value": 1, "_triggered": False}})
    mgr._event_bus.publish.assert_not_awaited()

    await _apply(mgr, flow, {"w1": {"_write_value": 1, "_triggered": True}})
    mgr._event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_outputs_only_on_change_filter():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, {"only_on_change": True})
    graph_state = {"w1": {"last_write_val": 5}}

    await _apply(mgr, flow, {"w1": {"_write_value": 5}}, graph_state)
    mgr._event_bus.publish.assert_not_awaited()

    await _apply(mgr, flow, {"w1": {"_write_value": 6}}, graph_state)
    mgr._event_bus.publish.assert_awaited_once()
    assert graph_state["w1"]["last_write_val"] == 6


@pytest.mark.asyncio
async def test_write_outputs_min_delta_filter():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, {"min_delta": 10})
    graph_state = {"w1": {"last_write_val": 100}}

    await _apply(mgr, flow, {"w1": {"_write_value": 105}}, graph_state)
    mgr._event_bus.publish.assert_not_awaited()

    await _apply(mgr, flow, {"w1": {"_write_value": 111}}, graph_state)
    mgr._event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_outputs_throttle_filter():
    from datetime import UTC, datetime

    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, {"throttle_value": 60, "throttle_unit": "s"})
    graph_state = {"w1": {"last_write_ts": datetime.now(UTC)}}

    await _apply(mgr, flow, {"w1": {"_write_value": 1}}, graph_state)
    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_outputs_skip_node_ids_and_unconfigured():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})

    await _apply(mgr, _write_flow(dst_id), {"w1": {"_write_value": 1}}, skip_node_ids={"w1"})
    mgr._event_bus.publish.assert_not_awaited()

    unconfigured = _flow([{"id": "w1", "type": "datapoint_write", "data": {}}])
    await _apply(mgr, unconfigured, {"w1": {"_write_value": 1}})
    mgr._event_bus.publish.assert_not_awaited()

    await _apply(mgr, _write_flow(dst_id), {"w1": {"_write_value": None}})
    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_outputs_publish_error_is_swallowed():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    mgr._event_bus.publish = AsyncMock(side_effect=RuntimeError("bus down"))

    await _apply(mgr, _write_flow(dst_id), {"w1": {"_write_value": 1}})

    mgr._event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_datapoint_id_is_treated_as_unseeded():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow("not-a-uuid", dst_id))})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_outputs_min_delta_ignores_non_numeric_values():
    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, {"min_delta": 10})
    graph_state = {"w1": {"last_write_val": "on"}}

    await _apply(mgr, flow, {"w1": {"_write_value": "off"}}, graph_state)

    mgr._event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_outputs_throttle_ignores_non_numeric_config():
    from datetime import UTC, datetime

    dst_id = str(uuid.uuid4())
    mgr = _make_manager({})
    flow = _write_flow(dst_id, {"throttle_value": "abc"})
    graph_state = {"w1": {"last_write_ts": datetime.now(UTC)}}

    await _apply(mgr, flow, {"w1": {"_write_value": 1}}, graph_state)

    mgr._event_bus.publish.assert_awaited_once()
