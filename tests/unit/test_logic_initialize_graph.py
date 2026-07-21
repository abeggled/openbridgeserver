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
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData

_SEED_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _make_manager(graphs: dict, values: dict | None = None) -> LogicManager:
    """LogicManager with an in-memory graph cache and a value-map registry."""
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    value_map = {uuid.UUID(k): v for k, v in (values or {}).items()}
    registry.get_value = MagicMock(side_effect=lambda dp_id: SimpleNamespace(value=value_map[dp_id], ts=_SEED_TS) if dp_id in value_map else None)

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
    # Marked as save-time seeding so notification subscribers can ignore it
    assert event.initialization is True

    # Event filters (trigger_on_change, min_delta) are primed; last_ts keeps
    # the registry timestamp so no fresh throttle window starts at save time
    read_state = mgr._node_state["g1"]["r1"]
    assert read_state["last_value"] == 42
    assert read_state["last_ts"] == _SEED_TS


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


# ---------------------------------------------------------------------------
# Second review round — scoping, ordering, placeholder/state protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_state_is_primed_before_writes_publish():
    """A graph writing a DataPoint it also reads re-enters _on_value_event
    during the publish await — the seed must already be primed by then."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 42})
    seen_at_publish = {}

    async def _capture(event):
        seen_at_publish["last_value"] = mgr._node_state["g1"].get("r1", {}).get("last_value")

    mgr._event_bus.publish = AsyncMock(side_effect=_capture)

    await mgr.initialize_graph("g1")

    assert seen_at_publish == {"last_value": 42}


@pytest.mark.asyncio
async def test_write_not_descending_from_seeded_read_is_suppressed():
    """A save must not actuate branches that carry no seeded value (e.g. a
    constant-fed write) even when another Read Object is seeded."""
    src_id, dst_a, dst_b = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_a}},
            {"id": "c1", "type": "const_value", "data": {"value": 1}},
            {"id": "w2", "type": "datapoint_write", "data": {"datapoint_id": dst_b}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"},
            {"source": "c1", "sourceHandle": "out", "target": "w2", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 7})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].datapoint_id == uuid.UUID(dst_a)


@pytest.mark.asyncio
async def test_write_downstream_of_action_placeholder_is_suppressed():
    """Non-executed action nodes emit placeholder outputs (api_client.success
    is False without any HTTP attempt) — those must not be written."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "a1", "type": "api_client", "data": {"url": "http://example.invalid"}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "a1", "targetHandle": "trigger"},
            {"source": "a1", "sourceHandle": "success", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_downstream_of_statistics_is_suppressed():
    """Accumulator outputs are computed on the throwaway state copy and would
    move backwards on the next real event — they must not be written."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "s1", "type": "statistics", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "s1", "targetHandle": "value"},
            {"source": "s1", "sourceHandle": "avg", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 10})
    mgr._hysteresis["g1"] = {"s1": {"s_min": 3.0, "s_max": 8.0, "s_sum": 25.0, "s_count": 5}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    assert mgr._hysteresis["g1"]["s1"] == {"s_min": 3.0, "s_max": 8.0, "s_sum": 25.0, "s_count": 5}


@pytest.mark.asyncio
async def test_operating_hours_totals_are_injected():
    """Seeded paths through operating_hours publish the accumulated total,
    mirroring _execute_graph's _computed_hours pre-pass."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "oh1", "type": "operating_hours", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "oh1", "targetHandle": "active"},
            {"source": "oh1", "sourceHandle": "hours", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})
    mgr._node_state["g1"] = {"oh1": {"accumulated_hours": 5.5, "last_start": None}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 5.5
    # The seeded truthy active input starts the accumulator right away
    assert mgr._node_state["g1"]["oh1"]["last_start"] is not None


@pytest.mark.asyncio
async def test_operating_hours_running_accumulation_is_included():
    """A currently running operating-hours block adds the elapsed time since
    last_start to the published total, like _execute_graph's pre-pass."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "oh1", "type": "operating_hours", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "oh1", "targetHandle": "active"},
            {"source": "oh1", "sourceHandle": "hours", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})
    mgr._node_state["g1"] = {"oh1": {"accumulated_hours": 2.0, "last_start": datetime.now(UTC)}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value >= 2.0


@pytest.mark.asyncio
async def test_write_downstream_of_random_value_is_suppressed():
    """random_value generates a fresh value per evaluation — a save must not
    publish a new random actuator value."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "rnd1", "type": "random_value", "data": {"min": 0, "max": 100}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "rnd1", "targetHandle": "trigger"},
            {"source": "rnd1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_downstream_of_memory_is_suppressed():
    """The dry-run evaluates with commit_memory=False, so a Memory node emits
    its uncommitted previous value — that stale output must not be written."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "m1", "type": "memory", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "m1", "targetHandle": "in"},
            {"source": "m1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 42})
    mgr._hysteresis["g1"] = {"m1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    assert mgr._hysteresis["g1"]["m1"] == {"value": "stale"}


@pytest.mark.asyncio
async def test_write_downstream_of_timer_is_suppressed():
    """timer_delay/timer_pulse are async manager-driven nodes; the executor
    returns {} for them, so downstream coercions must not be written."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "t1", "type": "timer_delay", "data": {"delay_s": 5}},
            {"id": "m1", "type": "math_map", "data": {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "t1", "targetHandle": "in"},
            {"source": "t1", "sourceHandle": "out", "target": "m1", "targetHandle": "value"},
            {"source": "m1", "sourceHandle": "result", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_downstream_of_missing_node_is_suppressed():
    """missing_node placeholders (unknown imported blocks) produce no output —
    downstream coercions must not be written."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "x1", "type": "missing_node", "data": {"original_type": "gone"}},
            {"id": "m1", "type": "math_map", "data": {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "x1", "targetHandle": "in"},
            {"source": "x1", "sourceHandle": "out", "target": "m1", "targetHandle": "value"},
            {"source": "m1", "sourceHandle": "result", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_read_taints_shared_expression():
    """A Read Object without a datapoint_id evaluates to None like an
    unseeded one — a write joining it with a seeded branch is suppressed."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_name": "unconfigured"}},
            {"id": "a1", "type": "and", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "a1", "targetHandle": "in1"},
            {"source": "rB", "sourceHandle": "value", "target": "a1", "targetHandle": "in2"},
            {"source": "a1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_back_to_read_datapoint_is_skipped():
    """A Read A → Write A feedback loop would re-enter _on_value_event and
    burst until the cascade-depth guard — such writes are not initialized."""
    dp_id = str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(dp_id, dp_id))}, values={dp_id: 42})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    # The filter state is still primed for future events
    assert mgr._node_state["g1"]["r1"]["last_value"] == 42


@pytest.mark.asyncio
async def test_hysteresis_state_on_seeded_path_is_committed():
    """A published hysteresis output must match the persisted state, or the
    next in-band value would flip the output back to the stale state."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "h1", "type": "hysteresis", "data": {"threshold_on": 40, "threshold_off": 20}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "h1", "targetHandle": "value"},
            {"source": "h1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 50})
    mgr._hysteresis["g1"] = {"h1": False}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value is True
    assert mgr._hysteresis["g1"]["h1"] is True

    # The committed state is also persisted so a restart cannot reload the
    # stale pre-save state from the DB
    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert len(persist_calls) == 1
    import json

    assert json.loads(persist_calls[0].args[1][0])["h1"] is True
    assert persist_calls[0].args[1][1] == "g1"


@pytest.mark.asyncio
async def test_unrelated_read_of_target_does_not_skip_write():
    """Read A → Write B plus an independent Read B (no path back to the
    write) is not a feedback loop — B must still be initialized."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wC", "type": "datapoint_write", "data": {"datapoint_id": dst_c}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wC", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7, dp_b: 3})

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    # B settles to the value the sheet itself derives (7), and the Read B →
    # Write C branch initializes from that settled value, not the stale 3
    assert written == {uuid.UUID(dp_b): 7, uuid.UUID(dst_c): 7}


@pytest.mark.asyncio
async def test_no_state_commit_means_no_persist():
    """A plain read→write initialization does not touch node_state in the DB."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 42})
    mgr._hysteresis["g1"] = {"other": 1}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert not [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]


# ---------------------------------------------------------------------------
# _persist_node_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_node_state_excludes_persist_state_false_nodes():
    import json

    flow = _flow([{"id": "s1", "type": "statistics", "data": {"persist_state": False}}, {"id": "h1", "type": "hysteresis", "data": {}}])
    mgr = _make_manager({"g1": ("G", True, flow)})
    mgr._hysteresis["g1"] = {"s1": {"s_count": 3}, "h1": True}

    await mgr._persist_node_state("g1")

    mgr._db.execute_and_commit.assert_awaited_once()
    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved == {"h1": True}


@pytest.mark.asyncio
async def test_persist_node_state_without_graph_entry_saves_everything():
    import json

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"h1": False}

    await mgr._persist_node_state("g1")

    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved == {"h1": False}


@pytest.mark.asyncio
async def test_persist_node_state_swallows_db_errors():
    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"h1": True}
    mgr._db.execute_and_commit = AsyncMock(side_effect=RuntimeError("db down"))

    await mgr._persist_node_state("g1")

    mgr._db.execute_and_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_node_state_without_state_is_noop():
    mgr = _make_manager({})

    await mgr._persist_node_state("g1")

    mgr._db.execute_and_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_init_publish_does_not_reenter_same_graph():
    """Read A → Write B plus Read B → Write C: delivering the Write B event
    back to _on_value_event during the init publish must not re-execute this
    graph mid-pass; other graphs are unaffected."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wC", "type": "datapoint_write", "data": {"datapoint_id": dst_c}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wC", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7, dp_b: 3})
    mgr._execute_graph = AsyncMock()  # only reachable via _on_value_event re-entry

    async def _deliver(event):
        await mgr._on_value_event(event)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graph("g1")

    assert mgr._event_bus.publish.await_count == 2
    mgr._execute_graph.assert_not_awaited()
    # The suppressed self-event still synced Read B's filter state to the
    # written value, so a later event repeating it is deduplicated correctly
    assert mgr._node_state["g1"]["rB"]["last_value"] == 7
    # The guard is released afterwards — later events execute normally
    assert "g1" not in mgr._initializing_graphs


@pytest.mark.asyncio
async def test_write_downstream_of_cron_trigger_is_suppressed():
    """timer_cron evaluates to trigger=False without a manager override — a
    write joining it with a seeded branch must not publish that placeholder."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "c1", "type": "timer_cron", "data": {"cron": "0 7 * * *"}},
            {"id": "a1", "type": "and", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "a1", "targetHandle": "in1"},
            {"source": "c1", "sourceHandle": "trigger", "target": "a1", "targetHandle": "in2"},
            {"source": "a1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_events_still_execute_during_initialization():
    """Only the initialization's own logic-sourced writes are suppressed — a
    live source update racing in during the publish window still executes."""
    from obs.core.event_bus import DataValueEvent

    src_a, dp_b, dp_d = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rD", "type": "datapoint_read", "data": {"datapoint_id": dp_d}},
        ],
        [{"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7, dp_d: 3})
    mgr._execute_graph = AsyncMock()  # only reachable via _on_value_event

    async def _deliver(event):
        # The init's own logic-sourced write event is suppressed …
        await mgr._on_value_event(event)
        # … but an external update arriving during the same window executes
        live = DataValueEvent(datapoint_id=uuid.UUID(dp_d), value=9, quality="good", source_adapter="knx")
        await mgr._on_value_event(live)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_awaited_once()
    overrides = mgr._execute_graph.await_args.args[3]
    assert overrides == {"rD": {"value": 9, "changed": True}}
    assert "g1" not in mgr._initializing_graphs


@pytest.mark.asyncio
async def test_write_downstream_of_ical_is_suppressed():
    """ical outputs come from the fetch cache, which may be empty right after
    a save — a write joining it with a seeded branch must not publish."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "i1", "type": "ical", "data": {"url": "https://example.com/cal.ics"}},
            {"id": "a1", "type": "and", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "a1", "targetHandle": "in1"},
            {"source": "i1", "sourceHandle": "f0_today", "target": "a1", "targetHandle": "in2"},
            {"source": "a1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_handle_branch_is_not_initialized():
    """Read.changed carries the synthetic changed=False seed, not the object
    value — a write fed via that handle must not publish on save."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [{"source": "r1", "sourceHandle": "changed", "target": "w1", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 42})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_operating_hours_seeded_inactive_stops_counter():
    """A seeded falsy active input stops a running accumulator exactly like a
    live off-event would: elapsed time is added and last_start cleared."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "oh1", "type": "operating_hours", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "oh1", "targetHandle": "active"},
            {"source": "oh1", "sourceHandle": "hours", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 0})
    mgr._node_state["g1"] = {"oh1": {"accumulated_hours": 2.0, "last_start": datetime.now(UTC)}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value >= 2.0
    ns = mgr._node_state["g1"]["oh1"]
    assert ns["last_start"] is None
    assert ns["accumulated_hours"] >= 2.0


@pytest.mark.asyncio
async def test_bulk_initialization_runs_each_graph_once():
    """Config restore: Graph A writes B, Graph B reads B — the cascade from
    A's publish must not double-run B; B initializes once from the registry."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager(
        {
            "gA": ("A", True, _read_write_flow(src_a, dp_b)),
            "gB": ("B", True, _read_write_flow(dp_b, dst_c)),
        },
        values={src_a: 7, dp_b: 3},
    )
    mgr._execute_graph = AsyncMock()  # only reachable via _on_value_event

    async def _deliver(event):
        await mgr._on_value_event(event)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graphs(["gA", "gB"])

    written = [(c.args[0].datapoint_id, c.args[0].value) for c in mgr._event_bus.publish.await_args_list]
    assert written == [(uuid.UUID(dp_b), 7), (uuid.UUID(dst_c), 3)]
    mgr._execute_graph.assert_not_awaited()
    assert not mgr._bulk_init_pending


# ---------------------------------------------------------------------------
# reset_node_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_node_state_clears_memory_and_db():
    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"h1": True}
    mgr._node_state["g1"] = {"r1": {"last_value": 5}}

    await mgr.reset_node_state("g1")

    assert "g1" not in mgr._hysteresis
    assert "g1" not in mgr._node_state
    # node_state is TEXT NOT NULL — the reset must write '{}', not NULL
    call = mgr._db.execute_and_commit.await_args
    assert "node_state = '{}'" in call.args[0]
    assert "NULL" not in call.args[0]
    assert call.args[1] == ("g1",)


@pytest.mark.asyncio
async def test_reset_node_state_swallows_db_errors():
    mgr = _make_manager({})
    mgr._db.execute_and_commit = AsyncMock(side_effect=RuntimeError("db down"))

    await mgr.reset_node_state("g1")

    mgr._db.execute_and_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_operating_hours_seeded_reset_zeroes_counter():
    """A seeded truthy reset input zeroes the accumulator like a live reset."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "oh1", "type": "operating_hours", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "oh1", "targetHandle": "reset"},
            {"source": "oh1", "sourceHandle": "hours", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})
    mgr._node_state["g1"] = {"oh1": {"accumulated_hours": 5.5, "last_start": None}}

    await mgr.initialize_graph("g1")

    ns = mgr._node_state["g1"]["oh1"]
    assert ns["accumulated_hours"] == 0.0
    assert ns["last_start"] is None


@pytest.mark.asyncio
async def test_python_script_is_not_executed_during_initialization(monkeypatch):
    """The dry run must not run user scripts inside the save request — a
    loop-heavy script would hang the save/activation."""
    from unittest.mock import MagicMock as _MagicMock

    from obs.logic.executor import GraphExecutor

    run_script = _MagicMock(return_value=1)
    monkeypatch.setattr(GraphExecutor, "_run_script", run_script)

    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "p1", "type": "python_script", "data": {"script": "result = 1"}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "p1", "targetHandle": "value"},
            {"source": "p1", "sourceHandle": "result", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    run_script.assert_not_called()
    mgr._event_bus.publish.assert_not_awaited()
    # The cached flow itself keeps its real node types
    assert mgr._graphs["g1"][2].nodes[1].type == "python_script"


@pytest.mark.asyncio
async def test_trigger_only_seeded_path_does_not_publish_foreign_value():
    """Const → Write.value plus Read → Write.trigger: the seeded read only
    controls WHEN the write fires — a save must not publish the constant."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "c1", "type": "const_value", "data": {"value": 21}},
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "c1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
            {"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "trigger"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeded_value_with_seeded_trigger_still_publishes():
    """A write whose value AND trigger both come from seeded reads stays
    eligible — only trigger-only paths are excluded."""
    src_a, src_b, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": src_b}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "w1", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "w1", "targetHandle": "trigger"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 5, src_b: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 5


@pytest.mark.asyncio
async def test_bulk_initialization_orders_producers_first():
    """Config restore payloads may list consumers before producers — the bulk
    pass reorders so the producer's write lands before the consumer seeds."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager(
        {
            "gA": ("A", True, _read_write_flow(src_a, dp_b)),
            "gB": ("B", True, _read_write_flow(dp_b, dst_c)),
        },
        values={src_a: 7, dp_b: 3},
    )
    mgr._execute_graph = AsyncMock()

    async def _deliver(event):
        await mgr._on_value_event(event)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    # Consumer listed first — the producer must still initialize first
    await mgr.initialize_graphs(["gB", "gA"])

    written = [c.args[0].datapoint_id for c in mgr._event_bus.publish.await_args_list]
    assert written == [uuid.UUID(dp_b), uuid.UUID(dst_c)]
    mgr._execute_graph.assert_not_awaited()
    assert not mgr._bulk_init_pending


@pytest.mark.asyncio
async def test_bulk_initialization_cycle_falls_back_to_given_order():
    """Two graphs writing what the other reads form a cycle — the pass keeps
    the payload order and still initializes each exactly once."""
    dp_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager(
        {
            "gA": ("A", True, _read_write_flow(dp_a, dp_b)),
            "gB": ("B", True, _read_write_flow(dp_b, dp_a)),
        },
        values={dp_a: 1, dp_b: 2},
    )
    mgr._execute_graph = AsyncMock()

    async def _deliver(event):
        await mgr._on_value_event(event)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graphs(["gA", "gB"])

    written = [c.args[0].datapoint_id for c in mgr._event_bus.publish.await_args_list]
    assert written == [uuid.UUID(dp_b), uuid.UUID(dp_a)]
    mgr._execute_graph.assert_not_awaited()
    assert not mgr._bulk_init_pending


@pytest.mark.asyncio
async def test_bulk_initialization_tolerates_unknown_graph_ids():
    """Ids that failed to load into the cache are ordered without effect and
    no-op during initialization."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 4})

    await mgr.initialize_graphs(["missing", "g1"])

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 4
    assert not mgr._bulk_init_pending


@pytest.mark.asyncio
async def test_intermediate_chain_settles_before_publishing():
    """Read A → Write B, Read B → Write C, Read C → Write D: all writes
    publish the value the sheet derives from A, not stale registry values."""
    src_a = str(uuid.uuid4())
    dp_b, dp_c, dp_d = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wC", "type": "datapoint_write", "data": {"datapoint_id": dp_c}},
            {"id": "rC", "type": "datapoint_read", "data": {"datapoint_id": dp_c}},
            {"id": "wD", "type": "datapoint_write", "data": {"datapoint_id": dp_d}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wC", "targetHandle": "value"},
            {"source": "rC", "sourceHandle": "value", "target": "wD", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 1, dp_b: 9, dp_c: 8})

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    assert written == {uuid.UUID(dp_b): 1, uuid.UUID(dp_c): 1, uuid.UUID(dp_d): 1}


@pytest.mark.asyncio
async def test_seeded_falsy_trigger_gates_write_and_settle():
    """A wired falsy trigger gates the write — nothing is published and the
    settle pass does not treat the gated write as delivering a value."""
    src_a, src_b, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": src_b}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "w1", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "w1", "targetHandle": "trigger"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 5, src_b: 0})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_write_on_seeded_path_is_ignored():
    """A write node without a datapoint_id neither publishes nor participates
    in the settle pass; sibling configured writes still initialize."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "wX", "type": "datapoint_write", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "wX", "targetHandle": "value"},
            {"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 6})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 6


@pytest.mark.asyncio
async def test_settle_pass_evaluates_hysteresis_from_original_state():
    """Each settle pass gets a fresh state copy: an earlier pass evaluating
    the stale intermediate value must not flip the hysteresis state that the
    final settled pass (and the commit) is based on."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "h1", "type": "hysteresis", "data": {"threshold_on": 40, "threshold_off": 20}},
            {"id": "wC", "type": "datapoint_write", "data": {"datapoint_id": dst_c}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "h1", "targetHandle": "value"},
            {"source": "h1", "sourceHandle": "out", "target": "wC", "targetHandle": "value"},
        ],
    )
    # Stale B=10 would switch the hysteresis OFF in the first pass; the
    # settled B=30 is inside the dead band and must RETAIN the stored True.
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 30, dp_b: 10})
    mgr._hysteresis["g1"] = {"h1": True}

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    assert written == {uuid.UUID(dp_b): 30, uuid.UUID(dst_c): True}
    assert mgr._hysteresis["g1"]["h1"] is True


@pytest.mark.asyncio
async def test_cross_datapoint_feedback_is_skipped():
    """Read A → Write B plus Read B → Write A is a feedback loop across two
    DataPoints — the settle pass would never converge, so neither write may
    publish on save."""
    dp_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": dp_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wA", "type": "datapoint_write", "data": {"datapoint_id": dp_a}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wA", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={dp_a: 1, dp_b: 2})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_diamond_dependency_is_not_a_cycle():
    """Z feeds Y and both Z and Y feed X — a diamond, not a cycle: all
    writes initialize."""
    dp_z, dp_y, dp_x = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rZ", "type": "datapoint_read", "data": {"datapoint_id": dp_z}},
            {"id": "wY", "type": "datapoint_write", "data": {"datapoint_id": dp_y}},
            {"id": "rY", "type": "datapoint_read", "data": {"datapoint_id": dp_y}},
            {"id": "a1", "type": "and", "data": {}},
            {"id": "wX", "type": "datapoint_write", "data": {"datapoint_id": dp_x}},
        ],
        [
            {"source": "rZ", "sourceHandle": "value", "target": "wY", "targetHandle": "value"},
            {"source": "rZ", "sourceHandle": "value", "target": "a1", "targetHandle": "in1"},
            {"source": "rY", "sourceHandle": "value", "target": "a1", "targetHandle": "in2"},
            {"source": "a1", "sourceHandle": "out", "target": "wX", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={dp_z: 1, dp_y: 0})

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    # Y settles to 1 (from Z); X = Z AND settled Y = True
    assert written == {uuid.UUID(dp_y): 1, uuid.UUID(dp_x): True}
