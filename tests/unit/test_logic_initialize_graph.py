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
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.manager import LogicManager, _safe_deepcopy_state
from obs.logic.models import FlowData

_SEED_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_safe_state_snapshot_preserves_noncopyable_runtime_value():
    runtime_value = (item for item in (1, 2, 3))
    state = {"memory": {"value": runtime_value}, "other": {"value": [1]}}

    snapshot = _safe_deepcopy_state(state)

    assert snapshot["memory"]["value"] is runtime_value
    assert snapshot["other"] == {"value": [1]}
    assert snapshot["other"] is not state["other"]


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
async def test_seeded_initialization_tolerates_unrelated_noncopyable_filter_baseline():
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
            {"id": "cf", "type": "change_filter", "data": {}},
        ],
        [{"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 42})
    runtime_value = (item for item in (1, 2, 3))
    mgr._hysteresis["g1"] = {"cf": {"value": runtime_value}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._hysteresis["g1"]["cf"]["value"] is runtime_value


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

    assert json.loads(persist_calls[0].args[1][0])["state"]["h1"] is True
    assert persist_calls[0].args[1][1] == "g1"


@pytest.mark.asyncio
async def test_merge_state_on_seeded_path_is_committed():
    """A published merge output must match the persisted "active input" state,
    or the next real event would resolve against the stale pre-save state."""
    src1_id, src2_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src1_id}},
            {"id": "r2", "type": "datapoint_read", "data": {"datapoint_id": src2_id}},
            {"id": "m1", "type": "merge", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "m1", "targetHandle": "in1"},
            {"source": "r2", "sourceHandle": "value", "target": "m1", "targetHandle": "in2"},
            {"source": "m1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    # in2 (=2) is unchanged from its stored value; in1's DataPoint now holds 9
    # where the stored state still has 1 — in1 must become the active input.
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src1_id: 9, src2_id: 2})
    mgr._hysteresis["g1"] = {"m1": {"values": {"in1": 1, "in2": 2}, "active": "in2"}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 9
    assert mgr._hysteresis["g1"]["m1"]["active"] == "in1"

    # The committed state is also persisted so a restart cannot reload the
    # stale pre-save state from the DB
    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert len(persist_calls) == 1
    import json

    assert json.loads(persist_calls[0].args[1][0])["state"]["m1"]["active"] == "in1"
    assert persist_calls[0].args[1][1] == "g1"


@pytest.mark.asyncio
async def test_change_filter_state_on_seeded_path_is_committed():
    """A published change_filter output must match the persisted state, or the
    next real event carrying the same seeded value would report changed=True
    again and re-fire the write it just caused."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 50})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 50
    assert mgr._hysteresis["g1"]["cf1"] == {"value": 50}

    # The committed state is also persisted so a restart cannot reload the
    # stale pre-save state from the DB
    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert len(persist_calls) == 1
    import json

    assert json.loads(persist_calls[0].args[1][0])["state"]["cf1"] == {"value": 50}
    assert persist_calls[0].args[1][1] == "g1"


@pytest.mark.asyncio
async def test_change_filter_state_is_committed_with_no_write_descendant():
    """Regression: a seeded Change Filter feeding only a non-write branch
    (here: wake_on_lan) was previously never committed, because the commit
    loop only acted when the filter's descendants intersected
    published_writes — which is empty when there is no datapoint_write in
    the graph at all. After a save/restart, the seed would then be
    discarded, and a later event from another Read node in the same graph
    would replay the cached seed as the filter's "first" value, reporting
    changed=True and firing the action even though the seed itself never
    changed."""
    src_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "wol", "type": "wake_on_lan", "data": {"mac_address": "AA:BB:CC:DD:EE:FF"}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "changed", "target": "wol", "targetHandle": "trigger"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 50})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": 50}

    import json

    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert len(persist_calls) == 1
    assert json.loads(persist_calls[0].args[1][0])["state"]["cf1"] == {"value": 50}


@pytest.mark.asyncio
async def test_change_filter_state_committed_when_or_gate_absorbed_by_seeded_input():
    """Regression: the blanket `tainted` closure used to discard a Change
    Filter's initialization baseline whenever ANY upstream Read Object was
    unseeded, even if an OR gate in between is already decisively True from
    its OTHER, seeded input. E.g. seeded True + unseeded Read -> OR ->
    change_filter: the OR's output is fully deterministic, so the filter's
    committed state must not be discarded — otherwise the next unrelated
    event would replay the unchanged True as the filter's "first" value and
    re-fire the downstream action."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "or1", "type": "or", "data": {}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_seeded", "sourceHandle": "value", "target": "or1", "targetHandle": "in1"},
            {"source": "r_unseeded", "sourceHandle": "value", "target": "or1", "targetHandle": "in2"},
            {"source": "or1", "sourceHandle": "out", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: True})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": True}


@pytest.mark.asyncio
async def test_initial_changed_target_gate_can_absorb_taint_with_decisive_seed():
    seeded_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "source_cf", "type": "change_filter", "data": {}},
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "or1", "type": "or", "data": {}},
            {"id": "cf1", "type": "change_filter", "data": {}},
        ],
        [
            {"source": "source_cf", "sourceHandle": "changed", "target": "or1", "targetHandle": "in1"},
            {"source": "r_seeded", "sourceHandle": "value", "target": "or1", "targetHandle": "in2"},
            {"source": "or1", "sourceHandle": "out", "target": "cf1", "targetHandle": "in"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: True})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": True}


@pytest.mark.asyncio
async def test_initial_changed_target_closed_gate_absorbs_taint():
    seeded_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "read", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "source_cf", "type": "change_filter", "data": {}},
            {"id": "disabled", "type": "const_value", "data": {"value": "false", "data_type": "bool"}},
            {"id": "gate", "type": "gate", "data": {"closed_behavior": "default_value", "default_value": "9"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
        ],
        [
            {"source": "read", "sourceHandle": "value", "target": "source_cf", "targetHandle": "in"},
            {"source": "source_cf", "sourceHandle": "changed", "target": "gate", "targetHandle": "in"},
            {"source": "disabled", "sourceHandle": "value", "target": "gate", "targetHandle": "enable"},
            {"source": "gate", "sourceHandle": "out", "target": "cf1", "targetHandle": "in"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 1})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": 9.0}


@pytest.mark.asyncio
async def test_initialization_taint_stops_at_memory_tick_boundary():
    seeded_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "unseeded", "type": "datapoint_read", "data": {}},
            {"id": "memory", "type": "memory", "data": {"initial_value": 2, "data_type": "number"}},
            {"id": "seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "add", "type": "math_formula", "data": {"formula": "a + b"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
        ],
        [
            {"source": "unseeded", "sourceHandle": "value", "target": "memory", "targetHandle": "in"},
            {"source": "memory", "sourceHandle": "out", "target": "add", "targetHandle": "in1"},
            {"source": "seeded", "sourceHandle": "value", "target": "add", "targetHandle": "in2"},
            {"source": "add", "sourceHandle": "result", "target": "cf1", "targetHandle": "in"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 10})
    mgr._hysteresis["g1"] = {"memory": {"value": 2}, "cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    # Memory's initialization output is its prior/default tick value (zero
    # in this dry-run), so the downstream result is deterministic despite
    # the unresolved value waiting to be committed for the next tick.
    assert mgr._hysteresis["g1"]["cf1"] == {"value": 10.0}


@pytest.mark.asyncio
async def test_initialization_taint_uses_only_last_edge_for_target_handle():
    seeded_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "unseeded", "type": "datapoint_read", "data": {}},
            {"id": "seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "cf1", "type": "change_filter", "data": {}},
        ],
        [
            {"source": "unseeded", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
            {"source": "seeded", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 7})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": 7}


@pytest.mark.asyncio
async def test_change_filter_state_committed_when_and_gate_absorbed_by_negated_seeded_input():
    """Same absorption as the OR case above, but for an AND gate (decisive
    value False) whose seeded input is negated to reach that decisive
    value — also exercises the negate_in{handle} branch of the taint
    analysis's gate-absorption check."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "and1", "type": "and", "data": {"negate_in1": True}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_seeded", "sourceHandle": "value", "target": "and1", "targetHandle": "in1"},
            {"source": "r_unseeded", "sourceHandle": "value", "target": "and1", "targetHandle": "in2"},
            {"source": "and1", "sourceHandle": "out", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: True})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": False}


@pytest.mark.asyncio
async def test_change_filter_taint_survives_malformed_gate_input_count_during_initialization():
    """Regression: a malformed input_count (e.g. an imported/legacy node
    left with "invalid" or null) must not crash the whole initialization
    pass — the gate is instead treated as not-absorbed (still tainted), so
    a downstream Change Filter's baseline is correctly held rather than
    committed from a still-undetermined gate output."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "and1", "type": "and", "data": {"input_count": "invalid"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_seeded", "sourceHandle": "value", "target": "and1", "targetHandle": "in1"},
            {"source": "r_unseeded", "sourceHandle": "value", "target": "and1", "targetHandle": "in2"},
            {"source": "and1", "sourceHandle": "out", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: True})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": "stale"}
    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert len(persist_calls) == 0


@pytest.mark.asyncio
async def test_change_filter_state_committed_through_a_closed_gate_during_initialization():
    """Regression: a "gate" (Freigabe/relay) node closed by a RESOLVED
    enable input (here: left unwired, resolving to closed) is a boundary
    just like a decisive AND/OR gate — while closed, its output is either
    the retained last-enabled value or a fixed default_value, entirely
    independent of "in". An unseeded Read Object feeding only the gate's
    "in" port must not discard a downstream Change Filter's deterministic,
    fully computed baseline just because it's structurally reachable from
    that unrelated, never-resolving read."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "relay_gate", "type": "gate", "data": {}},
            {"id": "add1", "type": "math_formula", "data": {"formula": "a + b"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_unseeded", "sourceHandle": "value", "target": "relay_gate", "targetHandle": "in"},
            # enable is intentionally left unwired -> resolves to closed
            {"source": "r_seeded", "sourceHandle": "value", "target": "add1", "targetHandle": "in1"},
            {"source": "relay_gate", "sourceHandle": "out", "target": "add1", "targetHandle": "in2"},
            {"source": "add1", "sourceHandle": "result", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 10})
    mgr._hysteresis["g1"] = {"relay_gate": 5, "cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": 15}


@pytest.mark.asyncio
async def test_change_filter_state_commits_past_resolved_hysteresis_during_initialization():
    seeded_id, unseeded_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "hyst", "type": "hysteresis", "data": {"threshold_on": 40, "threshold_off": 20}},
            {"id": "add", "type": "math_formula", "data": {"formula": "a + b"}},
            {"id": "cf", "type": "change_filter", "data": {}},
        ],
        [
            {"source": "r_unseeded", "sourceHandle": "value", "target": "hyst", "targetHandle": "value"},
            {"source": "r_seeded", "sourceHandle": "value", "target": "add", "targetHandle": "in1"},
            {"source": "hyst", "sourceHandle": "out", "target": "add", "targetHandle": "in2"},
            {"source": "add", "sourceHandle": "result", "target": "cf", "targetHandle": "in"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 10})
    mgr._hysteresis["g1"] = {"hyst": True, "cf": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf"] == {"value": 11}


@pytest.mark.asyncio
async def test_change_filter_stays_tainted_when_gate_enable_itself_is_unresolved_during_initialization():
    """The closed-gate boundary exception only applies when the gate's OWN
    enable state is itself resolved — if "enable" is fed by the same
    unresolved Read Object, the gate's closed/open state can't be trusted
    yet either, so taint must still propagate through it normally."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "relay_gate", "type": "gate", "data": {}},
            {"id": "add1", "type": "math_formula", "data": {"formula": "a + b"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_unseeded", "sourceHandle": "value", "target": "relay_gate", "targetHandle": "in"},
            {"source": "r_unseeded", "sourceHandle": "value", "target": "relay_gate", "targetHandle": "enable"},
            {"source": "r_seeded", "sourceHandle": "value", "target": "add1", "targetHandle": "in1"},
            {"source": "relay_gate", "sourceHandle": "out", "target": "add1", "targetHandle": "in2"},
            {"source": "add1", "sourceHandle": "result", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 10})
    mgr._hysteresis["g1"] = {"relay_gate": 5, "cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": "stale"}


@pytest.mark.asyncio
async def test_change_filter_stays_tainted_when_gate_is_open_via_a_resolved_enable_during_initialization():
    """The closed-gate boundary exception must not apply when the gate is
    OPEN (enable resolves to True): an open gate genuinely passes its
    unresolved "in" value straight through as "out", so a downstream
    change_filter must still be tainted — same as if the gate weren't
    there. Also exercises negate_enable (flipping a resolved False into
    True) alongside the open-gate check."""
    seeded_id, unseeded_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r_seeded", "type": "datapoint_read", "data": {"datapoint_id": seeded_id}},
            {"id": "r_unseeded", "type": "datapoint_read", "data": {"datapoint_id": unseeded_id}},
            {"id": "enable_src", "type": "const_value", "data": {"value": "false", "data_type": "bool"}},
            {"id": "relay_gate", "type": "gate", "data": {"negate_enable": True}},
            {"id": "add1", "type": "math_formula", "data": {"formula": "a + b"}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r_unseeded", "sourceHandle": "value", "target": "relay_gate", "targetHandle": "in"},
            {"source": "enable_src", "sourceHandle": "value", "target": "relay_gate", "targetHandle": "enable"},
            {"source": "r_seeded", "sourceHandle": "value", "target": "add1", "targetHandle": "in1"},
            {"source": "relay_gate", "sourceHandle": "out", "target": "add1", "targetHandle": "in2"},
            {"source": "add1", "sourceHandle": "result", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={seeded_id: 10})
    mgr._hysteresis["g1"] = {"relay_gate": 5, "cf1": {"value": "stale"}}

    await mgr.initialize_graph("g1")

    assert mgr._hysteresis["g1"]["cf1"] == {"value": "stale"}


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
    assert saved["state"] == {"h1": True}


@pytest.mark.asyncio
async def test_persist_node_state_excludes_runtime_ical_body_and_result_cache():
    import json

    flow = _flow([{"id": "i1", "type": "ical", "data": {}}])
    mgr = _make_manager({"g1": ("G", True, flow)})
    mgr._hysteresis["g1"] = {
        "i1": {
            "raw": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
            "_ical_result_cache": {"raw": object(), "outputs": {"f0_array": []}},
            "_ical_last_attempt_url": "https://example.com/calendar.ics",
            "_ical_last_attempt_limit": 50 * 1_048_576,
            "_ical_last_attempt_ts": 124.0,
            "fetched_url": "https://example.com/calendar.ics",
            "last_fetch_ts": 123.0,
        },
        "removed-ical": {
            "raw": "large removed body",
            "_ical_result_cache": {"raw": object()},
        },
    }

    await mgr._persist_node_state("g1")

    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved["state"] == {
        "i1": {
            "fetched_url": "https://example.com/calendar.ics",
            "last_fetch_ts": 123.0,
        }
    }


@pytest.mark.asyncio
async def test_persist_node_state_without_graph_entry_strips_ical_runtime_data():
    import json

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {
        "h1": False,
        "i1": {
            "raw": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
            "_ical_last_attempt_url": "https://example.com/calendar.ics",
            "fetched_url": "https://example.com/calendar.ics",
        },
    }

    await mgr._persist_node_state("g1")

    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved["state"] == {"h1": False, "i1": {"fetched_url": "https://example.com/calendar.ics"}}


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


@pytest.mark.asyncio
async def test_persist_node_state_serializes_non_json_native_values():
    """Regression: a change_filter holding a datetime.time/date value (e.g.
    from a KNX DPT10/11 decode) must not raise inside json.dumps and poison
    persistence for the whole graph — this is a single dumps() call for
    every node's state, so one unserializable value would otherwise stop
    all of it from being saved. The value is tagged (not just str()-ed) so
    _load_graphs can restore the exact original type/value on restart."""
    import datetime as dt_module
    import json

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf": {"value": dt_module.time(14, 30)}, "other": {"value": 1}}

    await mgr._persist_node_state("g1")

    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved["__obs_node_state_version__"] == 2
    assert saved["state"] == {"cf": {"value": {"__obs_persisted_type__": "time", "value": "14:30:00"}}, "other": {"value": 1}}


@pytest.mark.asyncio
async def test_persist_node_state_skips_cyclic_node_but_saves_unrelated_state():
    import json

    cyclic = []
    cyclic.append(cyclic)
    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf": {"value": cyclic}, "stats": {"count": 7}}

    await mgr._persist_node_state("g1")

    saved = json.loads(mgr._db.execute_and_commit.await_args.args[1][0])
    assert saved["state"] == {"stats": {"count": 7}}


@pytest.mark.asyncio
async def test_change_filter_replaces_lossy_opaque_baseline_on_first_live_value():
    """Regression (issue #1087 Codex finding): a change_filter's comparison
    baseline is not always JSON-native or one of _persist_default's
    specifically recognized types — e.g. a permitted python_script result
    like a complex number. Persisting it used to fall back to a bare,
    untagged str(v), indistinguishable from a genuine string after restart;
    _load_graphs would restore that as a plain string, and a live value of
    the original type compared against it would report a spurious
    changed=True forever. The catch-all is tagged "opaque_str" so the first
    live value is conservatively emitted once and replaces the lossy stand-in;
    subsequent comparisons use the real in-memory value."""
    import json

    flow = _flow([{"id": "cf1", "type": "change_filter"}])

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf1": {"value": 3 + 4j}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
    saved = json.loads(saved_json)
    assert saved["state"]["cf1"]["value"] == {
        "__obs_persisted_type__": "opaque_str",
        "value": "(3+4j)",
        "type": "builtins.complex",
    }

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    assert mgr2._hysteresis["g1"] == {"cf1": {"value": "(3+4j)", "_opaque_recovered_str": True}}

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        outputs = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": 3 + 4j}})
        repeated = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": 3 + 4j}})

    assert outputs["cf1"]["changed"] is True
    assert repeated["cf1"]["changed"] is False


@pytest.mark.asyncio
async def test_change_filter_replaces_nested_lossy_opaque_baseline_on_first_live_value():
    """Regression: the opaque-recovery detection only recognized an
    "opaque_str" tag placed directly at state["value"] itself — a baseline
    like [3 + 4j] persists as a LIST holding one opaque-tagged item,
    decoded to ['(3+4j)'] (a list, not a dict), so the direct check never
    noticed it and never set "_opaque_recovered_str". The live [3 + 4j]
    then compared unequal against that lossy stand-in. The precise marker
    ensures that this uncertainty emits once and is replaced by the real
    nested value rather than trusting a non-injective string form."""
    import json

    flow = _flow([{"id": "cf1", "type": "change_filter"}])

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf1": {"value": [3 + 4j]}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
    saved = json.loads(saved_json)
    assert saved["state"]["cf1"]["value"] == [{"__obs_persisted_type__": "opaque_str", "value": "(3+4j)", "type": "builtins.complex"}]

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    assert mgr2._hysteresis["g1"] == {"cf1": {"value": ["(3+4j)"], "_opaque_recovered_str": True}}

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        outputs = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": [3 + 4j]}})
        repeated = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": [3 + 4j]}})

    assert outputs["cf1"]["changed"] is True
    assert repeated["cf1"]["changed"] is False


@pytest.mark.asyncio
async def test_change_filter_restores_a_named_zoneinfo_datetime_baseline():
    """Regression (Codex finding): a Change Filter holding an aware
    datetime whose tzinfo is a named ZoneInfo (e.g. "Europe/Zurich")
    persists via isoformat(), which only records the CURRENT numeric UTC
    offset. _load_graphs used to restore that as a fixed-offset datetime
    via datetime.fromisoformat() alone — comparing == True against a live
    matching instant either way, so change_filter's own comparison stayed
    correct, but the RESTORED value handed to downstream nodes carried the
    wrong tzinfo type, silently breaking any DST-aware date arithmetic
    performed on it after a restart."""
    import json
    from zoneinfo import ZoneInfo

    flow = _flow([{"id": "cf1", "type": "change_filter"}])
    aware = datetime(2026, 7, 1, 12, 30, tzinfo=ZoneInfo("Europe/Zurich"))

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf1": {"value": aware}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
    saved = json.loads(saved_json)
    assert saved["state"]["cf1"]["value"]["tz"] == "Europe/Zurich"

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    restored = mgr2._hysteresis["g1"]["cf1"]["value"]
    assert restored == aware
    assert isinstance(restored.tzinfo, ZoneInfo)
    assert restored.tzinfo.key == "Europe/Zurich"

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        outputs = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": aware}})

    assert outputs["cf1"]["changed"] is False
    assert isinstance(outputs["cf1"]["out"].tzinfo, ZoneInfo)
    assert outputs["cf1"]["out"].tzinfo.key == "Europe/Zurich"


@pytest.mark.asyncio
async def test_change_filter_preserves_colliding_opaque_and_string_keys():
    """Opaque and genuine string keys with the same representation remain
    distinct until the live baseline can replace the recovery wrapper."""
    from obs.logic.executor import _OpaqueRecoveredDict

    flow = _flow([{"id": "cf1", "type": "change_filter"}])
    live_value = {3 + 4j: "complex", "(3+4j)": "string"}

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {"cf1": {"value": live_value}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    recovered = mgr2._hysteresis["g1"]["cf1"]["value"]
    assert isinstance(recovered, _OpaqueRecoveredDict)
    assert len(recovered.items) == 2

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        outputs = await mgr2._execute_graph("g1", "G", flow, {"cf1": {"in": live_value}})

    assert outputs["cf1"]["changed"] is True
    assert outputs["cf1"]["out"] == live_value
    assert mgr2._hysteresis["g1"]["cf1"] == {"value": live_value}


@pytest.mark.asyncio
async def test_load_graphs_restores_state_when_a_node_id_collides_with_the_persist_tag():
    """Regression: a stateful node whose own (unrestricted) string id is
    exactly "__obs_persisted_type__" makes _escape_persist_collision wrap
    the ENTIRE top-level state mapping in its escape envelope — that
    mapping is itself just a dict that "contains the reserved tag key",
    same as any other. _load_graphs used to decode each *value* of
    saved_raw["state"] without first decoding the container itself, so it
    iterated the envelope's own "__obs_persisted_type__"/"value" keys as
    though they were node ids, losing every real node's state after a
    restart instead of restoring it."""
    import json

    from obs.logic.manager import _PERSIST_TYPE_TAG

    flow = _flow([{"id": _PERSIST_TYPE_TAG, "type": "change_filter"}, {"id": "other", "type": "change_filter"}])

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {_PERSIST_TYPE_TAG: {"value": 1}, "other": {"value": 2}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
    saved = json.loads(saved_json)
    # The whole state mapping got escape-wrapped, not just the one node.
    assert saved["state"][_PERSIST_TYPE_TAG] == "escaped"
    assert saved["state"]["value"] == {_PERSIST_TYPE_TAG: {"value": 1}, "other": {"value": 2}}

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    assert mgr2._hysteresis["g1"] == {_PERSIST_TYPE_TAG: {"value": 1}, "other": {"value": 2}}


@pytest.mark.asyncio
async def test_load_graphs_marks_opaque_recovery_even_when_a_node_id_collides_with_the_persist_tag():
    """Regression: the opaque-tag detection above used the same raw
    saved_raw["state"] container as before the collision-unwrap fix — when
    a node id collides with _PERSIST_TYPE_TAG, that container is still the
    escape wrapper, so looking up ANY node's raw state by id (including
    well-behaved ones in the same graph) found nothing, and no
    change_filter in that graph ever got its _opaque_recovered_str marker
    restored. An unchanged opaque baseline (e.g. a python_script
    complex-number result) would then report a spurious changed=True on
    every restart."""
    import json

    from obs.logic.manager import _PERSIST_TYPE_TAG

    flow = _flow([{"id": _PERSIST_TYPE_TAG, "type": "change_filter"}, {"id": "cf2", "type": "change_filter"}])

    mgr = _make_manager({})
    mgr._hysteresis["g1"] = {_PERSIST_TYPE_TAG: {"value": 3 + 4j}, "cf2": {"value": 5 + 6j}}
    await mgr._persist_node_state("g1")
    saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
    saved = json.loads(saved_json)
    assert saved["state"][_PERSIST_TYPE_TAG] == "escaped"

    mgr2 = _make_manager({})
    mgr2._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
    )
    await mgr2._load_graphs()

    assert mgr2._hysteresis["g1"] == {
        _PERSIST_TYPE_TAG: {"value": "(3+4j)", "_opaque_recovered_str": True},
        "cf2": {"value": "(5+6j)", "_opaque_recovered_str": True},
    }

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        outputs = await mgr2._execute_graph("g1", "G", flow, {_PERSIST_TYPE_TAG: {"in": 3 + 4j}, "cf2": {"in": 5 + 6j}})

    assert outputs[_PERSIST_TYPE_TAG]["changed"] is True
    assert outputs["cf2"]["changed"] is True


@pytest.mark.asyncio
async def test_load_graphs_survives_a_malformed_escape_envelope_in_raw_state():
    """An "escaped"-tagged state container whose "value" isn't a dict can
    only come from a corrupted/hand-edited row — _escape_persist_collision
    itself never produces that shape (mirrors
    test_decode_persisted_value_keeps_malformed_tagged_escape_as_is below,
    but for the raw-container unwrap used for opaque-tag detection).
    Must not crash; the opaque-tag lookup simply finds nothing to restore."""
    import json

    from obs.logic.manager import _PERSIST_STATE_VERSION, _PERSIST_STATE_VERSION_KEY, _PERSIST_TYPE_TAG

    flow = _flow([{"id": "cf1", "type": "change_filter"}])
    node_state = json.dumps({_PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION, "state": {_PERSIST_TYPE_TAG: "escaped", "value": "not-a-dict"}})

    mgr = _make_manager({})
    mgr._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": node_state}]
    )

    await mgr._load_graphs()

    # _decode_persisted_value's own malformed-escape recovery (see
    # test_decode_persisted_value_keeps_malformed_tagged_escape_as_is)
    # returns the tagged dict unchanged rather than crashing — nothing here
    # actually restores per-node state from it, but nothing raises either.
    assert mgr._hysteresis["g1"] == {_PERSIST_TYPE_TAG: "escaped", "value": "not-a-dict"}


@pytest.mark.asyncio
async def test_load_graphs_recognizes_a_genuine_envelope_for_a_graph_with_reserved_node_ids():
    """Regression: a graph containing a node whose id happens to be
    "state" or "__obs_node_state_version__" (reachable by importing a
    hand-crafted flow_data, unlike node_state itself, which the app never
    lets a client set directly) must still have its GENUINE, correctly
    written version-2 envelope recognized as such. _persist_node_state
    always writes exactly {_PERSIST_STATE_VERSION_KEY: 2, "state": {...}}
    at the top level regardless of what any real node's id is — real
    per-node entries live one level deeper, inside "state", never at this
    level — so this must not be misread as an ambiguous legacy row just
    because a real node happens to share one of these reserved ids."""
    import json

    from obs.logic.manager import _PERSIST_STATE_VERSION, _PERSIST_STATE_VERSION_KEY, _persist_default

    flow = _flow(
        [
            {"id": _PERSIST_STATE_VERSION_KEY, "type": "change_filter"},
            {"id": "state", "type": "change_filter"},
            {"id": "other", "type": "change_filter"},
        ]
    )
    envelope = {
        _PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION,
        "state": {_PERSIST_STATE_VERSION_KEY: {"value": 1}, "state": {"value": 2}, "other": {"value": 3}},
    }
    node_state = json.dumps(envelope, default=_persist_default)

    mgr = _make_manager({})
    mgr._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": node_state}]
    )

    await mgr._load_graphs()

    assert mgr._hysteresis["g1"] == {
        _PERSIST_STATE_VERSION_KEY: {"value": 1},
        "state": {"value": 2},
        "other": {"value": 3},
    }


@pytest.mark.asyncio
async def test_load_graphs_accepts_a_pathological_legacy_two_node_collision_as_an_envelope():
    """Documents a known, accepted limitation: a legacy (pre-envelope) row
    is indistinguishable from a genuine version-2 envelope purely from its
    raw JSON shape when it happens to have EXACTLY two node entries whose
    ids are literally "__obs_node_state_version__" (stored value: bare int
    2) and "state" (stored value: a dict) — the exact same shape
    _persist_node_state itself always writes. Resolving this exact
    collision in the legacy row's favor was tried (cross-checking the
    row's node ids against the graph's current flow.nodes) but that broke
    genuine envelopes for any graph simply containing a node named "state"
    (see test_load_graphs_recognizes_a_genuine_envelope_for_a_graph_with_reserved_node_ids
    above) — a real, ongoing regression for a real, reachable scenario
    (importing a hand-crafted flow_data), traded off here against a
    one-time legacy-row collision that requires directly tampering with
    the node_state DB column, which the application itself never exposes
    a way to do (only flow_data is client-settable via graph import)."""
    import json

    from obs.logic.manager import _PERSIST_STATE_VERSION, _PERSIST_STATE_VERSION_KEY

    flow = _flow(
        [
            {"id": _PERSIST_STATE_VERSION_KEY, "type": "change_filter"},
            {"id": "state", "type": "change_filter"},
        ]
    )
    node_state = json.dumps({_PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION, "state": {"value": "kept"}})

    mgr = _make_manager({})
    mgr._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": node_state}]
    )

    await mgr._load_graphs()

    assert mgr._hysteresis["g1"] == {"value": "kept"}


@pytest.mark.asyncio
async def test_load_graphs_treats_a_legacy_row_with_extra_nodes_as_legacy():
    """A legacy row with MORE than the envelope's own two keys (i.e. a
    third real node beyond the two coincidentally reserved-looking ids)
    can no longer be mistaken for the envelope shape at all — the exact
    top-level key count check added above resolves this more common case
    (an ordinary legacy graph with more than exactly two nodes) without
    needing the node-id cross-check that regressed genuine envelopes."""
    import json

    from obs.logic.manager import _PERSIST_STATE_VERSION, _PERSIST_STATE_VERSION_KEY

    flow = _flow(
        [
            {"id": _PERSIST_STATE_VERSION_KEY, "type": "change_filter"},
            {"id": "state", "type": "change_filter"},
            {"id": "extra", "type": "change_filter"},
        ]
    )
    node_state = json.dumps({_PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION, "state": {"value": "kept"}, "extra": {"value": "also-kept"}})

    mgr = _make_manager({})
    mgr._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": node_state}]
    )

    await mgr._load_graphs()

    assert mgr._hysteresis["g1"] == {
        _PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION,
        "state": {"value": "kept", "_recovered_str": True},
        "extra": {"value": "also-kept", "_recovered_str": True},
    }


class TestPersistDefaultAndDecode:
    """Direct tests for the _persist_default/_decode_persisted_value pair
    (issue #1087 Codex finding: "Preserve non-JSON value types in persisted
    filter state") covering the date/datetime/list branches and the
    malformed-tag recovery paths not already exercised by the round-trip
    tests in test_coverage_adapters_hierarchy_logic.py."""

    def test_persist_default_tags_date_and_datetime(self):
        from datetime import date

        from obs.logic.manager import _persist_default

        assert _persist_default(date(2026, 1, 1)) == {"__obs_persisted_type__": "date", "value": "2026-01-01"}
        assert _persist_default(datetime(2026, 1, 1, 12, 30, tzinfo=UTC)) == {
            "__obs_persisted_type__": "datetime",
            "value": "2026-01-01T12:30:00+00:00",
        }

    def test_persist_default_tags_a_named_zoneinfo_datetime_with_its_zone_key(self):
        """Regression: isoformat() only records the current numeric UTC
        offset (e.g. "+02:00"), not a named zone like "Europe/Zurich" — the
        zone key must be captured separately here so _decode_persisted_value
        can reconstruct the actual named zone, not a fixed-offset stand-in
        that silently mishandles DST-boundary arithmetic downstream."""
        from zoneinfo import ZoneInfo

        from obs.logic.manager import _persist_default

        aware = datetime(2026, 7, 1, 12, 30, tzinfo=ZoneInfo("Europe/Zurich"))
        assert _persist_default(aware) == {
            "__obs_persisted_type__": "datetime",
            "value": aware.isoformat(),
            "tz": "Europe/Zurich",
        }

    def test_persist_default_tags_the_second_occurrence_of_an_ambiguous_dst_time_with_fold(self):
        """Regression: isoformat() does not preserve `fold` — during a DST
        "fall back", the same wall-clock time (e.g. 2:30 in Europe/Zurich)
        occurs TWICE, an hour apart in real UTC terms; fold=1 marks the
        SECOND occurrence. The numeric offset in isoformat() alone (e.g.
        "+01:00") is not restored back into a decoded named-zone datetime
        by a plain replace(tzinfo=...), so fold must be captured
        separately here whenever it is set."""
        from datetime import timedelta, timezone
        from zoneinfo import ZoneInfo

        from obs.logic.manager import _decode_persisted_value, _persist_default

        second_occurrence = datetime(2025, 10, 26, 2, 30, tzinfo=ZoneInfo("Europe/Zurich"), fold=1)
        assert _persist_default(second_occurrence) == {
            "__obs_persisted_type__": "datetime",
            "value": second_occurrence.isoformat(),
            "tz": "Europe/Zurich",
            "fold": 1,
        }
        # The first (default) occurrence needs no extra "fold" key — fold=0
        # is what replace() already re-derives on decode without it.
        first_occurrence = datetime(2025, 10, 26, 2, 30, tzinfo=ZoneInfo("Europe/Zurich"))
        assert "fold" not in _persist_default(first_occurrence)

        naive_second = datetime(2025, 10, 26, 2, 30, fold=1)  # noqa: DTZ001 - specifically tests naive persistence
        encoded_naive = _persist_default(naive_second)
        assert encoded_naive["fold"] == 1
        assert _decode_persisted_value(encoded_naive).fold == 1

        fixed_offset_second = datetime(2025, 10, 26, 2, 30, tzinfo=timezone(timedelta(hours=1)), fold=1)
        encoded_fixed = _persist_default(fixed_offset_second)
        assert encoded_fixed["fold"] == 1
        assert _decode_persisted_value(encoded_fixed).fold == 1

    def test_persist_default_preserves_named_zone_and_fold_for_time(self):
        from datetime import time
        from zoneinfo import ZoneInfo

        from obs.logic.manager import _decode_persisted_value, _persist_default

        aware = time(2, 30, tzinfo=ZoneInfo("Europe/Zurich"), fold=1)
        encoded = _persist_default(aware)

        assert encoded == {
            "__obs_persisted_type__": "time",
            "value": "02:30:00",
            "tz": "Europe/Zurich",
            "fold": 1,
        }
        decoded = _decode_persisted_value(encoded)
        assert decoded == aware
        assert isinstance(decoded.tzinfo, ZoneInfo)
        assert decoded.tzinfo.key == "Europe/Zurich"
        assert decoded.fold == 1

        naive = time(2, 30, fold=1)
        naive_encoded = _persist_default(naive)
        assert naive_encoded == {
            "__obs_persisted_type__": "time",
            "value": "02:30:00",
            "fold": 1,
        }
        assert _decode_persisted_value(naive_encoded).fold == 1

    def test_persist_default_tags_str_fallback_for_unrecognized_types(self):
        """Regression: an untagged bare str(v) fallback here would violate
        the version-2 envelope's own guarantee that every non-JSON-native
        value is fully tagged — _load_graphs would restore this as an
        indistinguishable genuine string, and a change_filter comparing a
        live value of the original type (e.g. a python_script's complex
        number) against it would report a spurious changed=True forever."""
        from obs.logic.manager import _persist_default

        assert _persist_default(3 + 4j) == {
            "__obs_persisted_type__": "opaque_str",
            "value": str(3 + 4j),
            "type": "builtins.complex",
        }

    def test_decimal_round_trips_as_its_original_type(self):
        from obs.logic.manager import _decode_persisted_value, _persist_default

        encoded = _persist_default(Decimal("1.500"))

        assert encoded == {"__obs_persisted_type__": "decimal", "value": "1.500"}
        decoded = _decode_persisted_value(encoded)
        assert decoded == Decimal("1.500")
        assert isinstance(decoded, Decimal)

    def test_recovered_opaque_dictionary_key_keeps_its_tag_when_repersisted(self):
        from obs.logic.executor import _OpaqueRecoveredStr
        from obs.logic.manager import _PERSIST_TYPE_TAG, _escape_persist_collision

        encoded = _escape_persist_collision({_OpaqueRecoveredStr("(3+4j)"): "value"})

        assert encoded == {
            _PERSIST_TYPE_TAG: "dict_nonstr_keys",
            "value": [[{_PERSIST_TYPE_TAG: "opaque_str", "value": "(3+4j)"}, "value"]],
        }

    def test_decode_persisted_value_restores_opaque_str_as_plain_string(self):
        from obs.logic.manager import _decode_persisted_value

        assert _decode_persisted_value({"__obs_persisted_type__": "opaque_str", "value": "(3+4j)"}) == "(3+4j)"

    def test_contains_opaque_tag_recurses_into_an_untagged_dict(self):
        """An application dict without its own _PERSIST_TYPE_TAG (e.g. a
        python_script baseline like {"a": 3 + 4j}) must still be walked
        recursively for a nested opaque_str tag — not just a list."""
        from obs.logic.manager import _contains_opaque_tag

        assert _contains_opaque_tag({"a": {"__obs_persisted_type__": "opaque_str", "value": "(3+4j)"}}) is True
        assert _contains_opaque_tag({"a": 1, "b": "text"}) is False

    def test_persist_default_tags_set_and_frozenset(self):
        from obs.logic.manager import _persist_default

        assert _persist_default({1, 2}) == {"__obs_persisted_type__": "set", "value": [1, 2]}
        assert _persist_default(frozenset({1, 2})) == {"__obs_persisted_type__": "frozenset", "value": [1, 2]}

    def test_decode_preserves_opaque_and_genuine_string_set_collision(self):
        from obs.logic.executor import GraphExecutor
        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value(
            {
                "__obs_persisted_type__": "set",
                "value": [
                    {"__obs_persisted_type__": "opaque_str", "value": "(3+4j)"},
                    "(3+4j)",
                ],
            }
        )
        state = {"cf": {"value": decoded, "_opaque_recovered_str": True}}
        exc = GraphExecutor(
            FlowData.model_validate({"nodes": [{"id": "cf", "type": "change_filter", "position": {"x": 0, "y": 0}, "data": {}}], "edges": []}),
            hysteresis_state=state,
        )

        out = exc.execute({"cf": {"in": {3 + 4j, "(3+4j)"}}})

        assert out["cf"]["changed"] is True
        assert state["cf"] == {"value": {3 + 4j, "(3+4j)"}}

    def test_decode_preserves_opaque_and_genuine_string_dict_key_collision(self):
        from obs.logic.executor import GraphExecutor
        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value(
            {
                "__obs_persisted_type__": "dict_nonstr_keys",
                "value": [
                    [{"__obs_persisted_type__": "opaque_str", "value": "(3+4j)"}, "complex"],
                    ["(3+4j)", "string"],
                ],
            }
        )
        state = {"cf": {"value": decoded, "_opaque_recovered_str": True}}
        exc = GraphExecutor(
            FlowData.model_validate({"nodes": [{"id": "cf", "type": "change_filter", "position": {"x": 0, "y": 0}, "data": {}}], "edges": []}),
            hysteresis_state=state,
        )

        live = {3 + 4j: "complex", "(3+4j)": "string"}
        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert state["cf"] == {"value": live}

    def test_persist_default_recursively_escapes_set_members(self):
        """Regression: a set member that json.dumps' own encoder handles
        NATIVELY (a tuple) is never passed through default=, unlike a plain
        set/frozenset member — so converting a set straight to list(v)
        would silently flatten a tuple member into an indistinguishable
        plain JSON array, exactly the collision _escape_persist_collision's
        own tuple branch exists to prevent for top-level lists. Each set
        member must be pre-escaped the same way before being handed to
        json.dumps."""
        from obs.logic.manager import _persist_default

        assert _persist_default({(1, 2)}) == {
            "__obs_persisted_type__": "set",
            "value": [{"__obs_persisted_type__": "tuple", "value": [1, 2]}],
        }
        assert _persist_default(frozenset({(1, 2)})) == {
            "__obs_persisted_type__": "frozenset",
            "value": [{"__obs_persisted_type__": "tuple", "value": [1, 2]}],
        }

    def test_persist_state_round_trip_survives_a_set_of_tuples(self):
        """Full _escape_persist_collision -> json.dumps(default=
        _persist_default) -> json.loads -> _decode_persisted_value round
        trip, matching _persist_node_state's exact production pipeline.
        Without escaping tuple members inside a set first, the decoded
        "value" list would contain a plain (unhashable) list instead of a
        tuple, and set(decoded_items) would raise TypeError — silently
        skipping restoration of the graph's entire persisted state."""
        import json

        from obs.logic.manager import _decode_persisted_value, _escape_persist_collision, _persist_default

        state_to_save = {"cf": {"value": {(1, 2)}}}
        dumped = json.dumps(_escape_persist_collision(state_to_save), default=_persist_default)
        restored = _decode_persisted_value(json.loads(dumped))

        assert restored == {"cf": {"value": {(1, 2)}}}

    @pytest.mark.asyncio
    async def test_deep_change_filter_state_persists_and_restores_iteratively(self):
        import json

        flow = _flow([{"id": "cf", "type": "change_filter"}])
        retained: list = []
        cursor = retained
        for _ in range(1100):
            child: list = []
            cursor.append(child)
            cursor = child
        cursor.append("leaf")

        mgr = _make_manager({})
        mgr._graphs["g1"] = ("G", True, flow)
        mgr._hysteresis["g1"] = {"cf": {"value": retained}}
        await mgr._persist_node_state("g1")
        saved_json = mgr._db.execute_and_commit.await_args.args[1][0]
        assert "cf" in json.loads(saved_json)["state"]

        mgr2 = _make_manager({})
        mgr2._db.fetchall = AsyncMock(
            return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": saved_json}]
        )
        await mgr2._load_graphs()

        live: list = []
        cursor = live
        for _ in range(1100):
            child = []
            cursor.append(child)
            cursor = child
        cursor.append("leaf")
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = await mgr2._execute_graph("g1", "G", flow, {"cf": {"in": live}})

        assert outputs["cf"]["changed"] is False
        assert "__error__" not in outputs["cf"]

    def test_decode_persisted_value_restores_set_and_frozenset(self):
        from obs.logic.manager import _decode_persisted_value

        assert _decode_persisted_value({"__obs_persisted_type__": "set", "value": [1, 2]}) == {1, 2}
        restored_fs = _decode_persisted_value({"__obs_persisted_type__": "frozenset", "value": [1, 2]})
        assert restored_fs == frozenset({1, 2})
        assert isinstance(restored_fs, frozenset)

    def test_decode_persisted_value_keeps_malformed_tagged_set_as_is(self):
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "set", "value": "not-a-list"}
        assert _decode_persisted_value(malformed) is malformed

    def test_escape_persist_collision_tags_nonstring_keyed_dicts(self):
        from obs.logic.manager import _escape_persist_collision

        assert _escape_persist_collision({1: "x"}) == {"__obs_persisted_type__": "dict_nonstr_keys", "value": [[1, "x"]]}

    def test_decode_persisted_value_restores_nonstring_keyed_dicts(self):
        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value({"__obs_persisted_type__": "dict_nonstr_keys", "value": [[1, "x"], [2, "y"]]})
        assert decoded == {1: "x", 2: "y"}

    def test_decode_persisted_value_keeps_malformed_tagged_nonstring_keys_as_is(self):
        """A corrupted/hand-edited pair list (e.g. an unhashable "key" like
        a nested list) must not crash the whole graph load."""
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "dict_nonstr_keys", "value": [[["not", "hashable"], "x"]]}
        assert _decode_persisted_value(malformed) is malformed

    def test_decode_persisted_value_keeps_tagged_nonstring_keys_with_non_list_value_as_is(self):
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "dict_nonstr_keys", "value": "not-a-list"}
        assert _decode_persisted_value(malformed) is malformed

    def test_decode_persisted_value_restores_date_and_walks_lists(self):
        from datetime import date

        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value({"value": [{"__obs_persisted_type__": "date", "value": "2026-01-01"}, 1]})
        assert decoded == {"value": [date(2026, 1, 1), 1]}

    def test_decode_persisted_value_reconstructs_a_named_zoneinfo_datetime(self):
        """Regression: restoring a "tz"-tagged datetime must produce the
        ORIGINAL named ZoneInfo zone, not the fixed-offset tzinfo
        datetime.fromisoformat() alone would reconstruct — verified via
        .tzinfo identity/key, not just == (which compares equal for either
        tzinfo representation of the same instant, masking the bug)."""
        from zoneinfo import ZoneInfo

        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value({"__obs_persisted_type__": "datetime", "value": "2026-07-01T12:30:00+02:00", "tz": "Europe/Zurich"})
        assert decoded == datetime(2026, 7, 1, 12, 30, tzinfo=ZoneInfo("Europe/Zurich"))
        assert isinstance(decoded.tzinfo, ZoneInfo)
        assert decoded.tzinfo.key == "Europe/Zurich"

    def test_decode_persisted_value_restores_the_second_occurrence_of_an_ambiguous_dst_time(self):
        """Regression: without restoring "fold" explicitly, replace() on the
        fixed-offset-parsed datetime re-derives fold=0 from the wall-clock
        numbers alone — for the SECOND occurrence of an ambiguous DST
        "fall back" wall-clock time, that reconstructs an instant ONE HOUR
        EARLIER than the original, even though the wall-clock numbers and
        zone name both look correct. Verified via .timestamp() (the actual
        UTC instant), which fold alone determines here — .replace()-derived
        fold=0 vs the correct fold=1 both produce a datetime object that
        looks identical when printed, but represents a different moment."""
        from zoneinfo import ZoneInfo

        from obs.logic.manager import _decode_persisted_value

        second_occurrence = datetime(2025, 10, 26, 2, 30, tzinfo=ZoneInfo("Europe/Zurich"), fold=1)
        decoded = _decode_persisted_value(
            {
                "__obs_persisted_type__": "datetime",
                "value": second_occurrence.isoformat(),
                "tz": "Europe/Zurich",
                "fold": 1,
            }
        )
        assert decoded.fold == 1
        assert decoded.timestamp() == second_occurrence.timestamp()

    def test_decode_persisted_value_falls_back_when_the_named_zone_is_unknown(self):
        """A zone no longer known on this host (e.g. a tzdata update/removal
        since the value was persisted) must not crash the whole graph
        load — fall back to the plain fixed-offset decode instead."""
        from obs.logic.manager import _decode_persisted_value

        decoded = _decode_persisted_value({"__obs_persisted_type__": "datetime", "value": "2026-07-01T12:30:00+02:00", "tz": "Not/AZone"})
        assert decoded == datetime.fromisoformat("2026-07-01T12:30:00+02:00")
        assert decoded.isoformat() == "2026-07-01T12:30:00+02:00"

    def test_decode_persisted_value_keeps_malformed_tagged_bytes_as_is(self):
        """A corrupted DB row (e.g. hand-edited or from a future format)
        must not crash the whole graph load — bytes.fromhex on a
        non-hex string raises ValueError, which must be caught and the
        tagged dict returned unchanged rather than propagating."""
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "bytes", "value": "not-hex"}
        assert _decode_persisted_value(malformed) is malformed

    def test_decode_persisted_value_keeps_malformed_tagged_isoformat_as_is(self):
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "time", "value": "not-a-time"}
        assert _decode_persisted_value(malformed) is malformed

    def test_escape_persist_collision_wraps_a_colliding_dict(self):
        from obs.logic.manager import _escape_persist_collision

        colliding = {"__obs_persisted_type__": "date", "value": "2026-01-01"}
        assert _escape_persist_collision(colliding) == {"__obs_persisted_type__": "escaped", "value": colliding}

    def test_escape_persist_collision_tags_tuples(self):
        from obs.logic.manager import _escape_persist_collision

        assert _escape_persist_collision((1, "a")) == {"__obs_persisted_type__": "tuple", "value": [1, "a"]}

    def test_decode_persisted_value_restores_tuples(self):
        from obs.logic.manager import _decode_persisted_value

        assert _decode_persisted_value({"__obs_persisted_type__": "tuple", "value": [1, "a"]}) == (1, "a")

    def test_decode_persisted_value_keeps_malformed_tagged_tuple_as_is(self):
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "tuple", "value": "not-a-list"}
        assert _decode_persisted_value(malformed) is malformed

    def test_decode_persisted_value_keeps_malformed_tagged_escape_as_is(self):
        """An "escaped" tag whose "value" isn't a dict can only come from a
        corrupted/hand-edited row — _escape_persist_collision itself never
        produces that shape. Must not crash; return the tagged dict as-is,
        matching the bytes/isoformat malformed-tag recovery above."""
        from obs.logic.manager import _decode_persisted_value

        malformed = {"__obs_persisted_type__": "escaped", "value": "not-a-dict"}
        assert _decode_persisted_value(malformed) is malformed


# ---------------------------------------------------------------------------
# reinitialize_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinitialize_graph_clears_state_after_disabling_persist_state():
    """Regression: disabling persist_state on a save must clear the node's
    stale DB snapshot immediately. Without a state-committing init publish
    (no seed value here), initialize_graph's own conditional persist never
    fires, so only the unconditional persist at the end of reinitialize_graph
    can prevent a restart before the next real execution from restoring the
    stale pre-toggle value via _load_graphs()."""
    import json

    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "cf1", "type": "change_filter", "data": {"persist_state": False}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={})
    mgr._hysteresis["g1"] = {"cf1": {"value": "stale"}}
    mgr._db.fetchall = AsyncMock(
        return_value=[{"id": "g1", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": '{"cf1": {"value": "stale"}}'}]
    )

    await mgr.reinitialize_graph("g1")

    persist_calls = [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]
    assert persist_calls
    final_state = json.loads(persist_calls[-1].args[1][0])
    assert "cf1" not in final_state


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
    # written value, so a later event repeating it is deduplicated correctly —
    # but last_ts keeps the registry timestamp (no save-time throttle window)
    assert mgr._node_state["g1"]["rB"]["last_value"] == 7
    assert mgr._node_state["g1"]["rB"]["last_ts"] == _SEED_TS
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
async def test_change_filter_changed_handle_branch_is_not_initialized():
    """Regression: a change_filter's "changed" port is the same kind of
    discrete event pulse as Read.changed — on a save/startup pseudo-
    execution it reports a synthetic first-value True (or, after a restart
    with restored state, a synthetic False), never a real DataValueEvent.
    A Write descending from Read -> ChangeFilter.in -> ChangeFilter.changed
    must not be published, exactly like the direct Read.changed case above
    — even though the change_filter's own baseline is still seeded and
    committed."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "cf1", "type": "change_filter", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "r1", "sourceHandle": "value", "target": "cf1", "targetHandle": "in"},
            {"source": "cf1", "sourceHandle": "changed", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 42})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    assert mgr._hysteresis["g1"]["cf1"] == {"value": 42}


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
    mgr._ical_result_caches["g1"] = {"i1": {"outputs": {"events": ["stale"]}}}
    mgr._node_state["g1"] = {"r1": {"last_value": 5}}

    await mgr.reset_node_state("g1")

    assert "g1" not in mgr._hysteresis
    assert "g1" not in mgr._ical_result_caches
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


@pytest.mark.asyncio
async def test_gate_enable_control_path_does_not_publish_foreign_value():
    """Const → Gate.in → Write.value with the seeded Read only on Gate.enable:
    the written value does not descend from the seed — no publish on save."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "c1", "type": "const_value", "data": {"value": 33}},
            {"id": "g1", "type": "gate", "data": {}},
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "c1", "sourceHandle": "out", "target": "g1", "targetHandle": "in"},
            {"source": "r1", "sourceHandle": "value", "target": "g1", "targetHandle": "enable"},
            {"source": "g1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_passing_seeded_value_still_publishes():
    """A gate whose IN carries the seeded value stays eligible — only the
    control handle is excluded from seeded reachability."""
    src_in, src_en, dst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rIn", "type": "datapoint_read", "data": {"datapoint_id": src_in}},
            {"id": "rEn", "type": "datapoint_read", "data": {"datapoint_id": src_en}},
            {"id": "g1", "type": "gate", "data": {}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id}},
        ],
        [
            {"source": "rIn", "sourceHandle": "value", "target": "g1", "targetHandle": "in"},
            {"source": "rEn", "sourceHandle": "value", "target": "g1", "targetHandle": "enable"},
            {"source": "g1", "sourceHandle": "out", "target": "w1", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_in: 17, src_en: 1})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    assert mgr._event_bus.publish.await_args.args[0].value == 17


@pytest.mark.asyncio
async def test_initialization_cascade_into_other_sheet_stays_side_effect_free():
    """An initialization write read by ANOTHER sheet runs that sheet's
    side-effect-free pass, not a full execution."""
    from obs.logic.manager import LogicManager

    src_a, dp_x, dst_y = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    value_map = {uuid.UUID(src_a): 7, uuid.UUID(dp_x): 3}

    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    event_bus = MagicMock()
    registry = MagicMock()
    registry.get_value = MagicMock(side_effect=lambda dp_id: SimpleNamespace(value=value_map[dp_id], ts=_SEED_TS) if dp_id in value_map else None)
    mgr = LogicManager(db, event_bus, registry)
    mgr._graphs = {
        "g1": ("A", True, _read_write_flow(src_a, dp_x)),
        "g2": ("B", True, _read_write_flow(dp_x, dst_y)),
    }
    mgr._execute_graph = AsyncMock()  # a full execution would be a failure

    async def _deliver(event):
        # the registry handler runs before the logic handler
        value_map[event.datapoint_id] = event.value
        await mgr._on_value_event(event)

    event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graph("g1")

    written = [(c.args[0].datapoint_id, c.args[0].value, c.args[0].initialization) for c in event_bus.publish.await_args_list]
    # g1 wrote X=7; the cascade initialized g2 side-effect-free with the
    # fresh value and its write is flagged as initialization too
    assert written == [(uuid.UUID(dp_x), 7, True), (uuid.UUID(dst_y), 7, True)]
    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_intermediate_chain_settles_completely():
    """Chains with more handoffs than the logic cascade depth still settle —
    the pass bound derives from the number of intermediate DataPoints."""
    hops = 14
    dps = [str(uuid.uuid4()) for _ in range(hops + 1)]
    nodes = []
    edges = []
    values = {dps[0]: 5}
    for i in range(hops):
        nodes.append({"id": f"r{i}", "type": "datapoint_read", "data": {"datapoint_id": dps[i]}})
        nodes.append({"id": f"w{i}", "type": "datapoint_write", "data": {"datapoint_id": dps[i + 1]}})
        edges.append({"source": f"r{i}", "sourceHandle": "value", "target": f"w{i}", "targetHandle": "value"})
        if i > 0:
            values[dps[i]] = 100 + i  # stale intermediate values
    mgr = _make_manager({"g1": ("G", True, _flow(nodes, edges))}, values=values)

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    assert written == {uuid.UUID(dps[i + 1]): 5 for i in range(hops)}


@pytest.mark.asyncio
async def test_branch_off_cyclic_pair_still_initializes():
    """Read A → Write X next to the A/B feedback pair: X is not on the cycle
    and initializes, while both cyclic writes stay suppressed."""
    dp_a, dp_b, dp_x = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": dp_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wA", "type": "datapoint_write", "data": {"datapoint_id": dp_a}},
            {"id": "wX", "type": "datapoint_write", "data": {"datapoint_id": dp_x}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wA", "targetHandle": "value"},
            {"source": "rA", "sourceHandle": "value", "target": "wX", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={dp_a: 1, dp_b: 2})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    event = mgr._event_bus.publish.await_args.args[0]
    assert event.datapoint_id == uuid.UUID(dp_x)
    assert event.value == 1


@pytest.mark.asyncio
async def test_settle_honors_write_filters():
    """A filtered (suppressed) intermediate write never lands in the registry
    — the settle pass must not feed its would-be value downstream."""
    src_a, dp_b, dst_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b, "only_on_change": True}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wC", "type": "datapoint_write", "data": {"datapoint_id": dst_c}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wC", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7, dp_b: 3})
    # only_on_change suppresses the B write — 7 was already written earlier
    mgr._node_state["g1"] = {"wB": {"last_write_val": 7}}

    await mgr.initialize_graph("g1")

    written = {c.args[0].datapoint_id: c.args[0].value for c in mgr._event_bus.publish.await_args_list}
    # B is not re-written; C initializes from B's actual registry value
    assert written == {uuid.UUID(dst_c): 3}


@pytest.mark.asyncio
async def test_trigger_only_edge_does_not_form_settle_cycle():
    """Read A → Write B.trigger plus Read B → Write A.value: the trigger edge
    cannot deliver a value, so there is no cycle and Write A initializes."""
    dp_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": dp_a}},
            {"id": "c1", "type": "const_value", "data": {"value": 9}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
            {"id": "wA", "type": "datapoint_write", "data": {"datapoint_id": dp_a}},
        ],
        [
            {"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "trigger"},
            {"source": "c1", "sourceHandle": "out", "target": "wB", "targetHandle": "value"},
            {"source": "rB", "sourceHandle": "value", "target": "wA", "targetHandle": "value"},
        ],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={dp_a: 1, dp_b: 2})

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    event = mgr._event_bus.publish.await_args.args[0]
    assert event.datapoint_id == uuid.UUID(dp_a)
    assert event.value == 2


@pytest.mark.asyncio
async def test_hysteresis_without_published_write_is_not_committed():
    """Without a published write on its path, the save must not act like a
    datapoint event on the stored gate/hysteresis state."""
    src_id = str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "h1", "type": "hysteresis", "data": {"threshold_on": 40, "threshold_off": 20}},
        ],
        [{"source": "r1", "sourceHandle": "value", "target": "h1", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_id: 50})
    mgr._hysteresis["g1"] = {"h1": False}

    await mgr.initialize_graph("g1")

    mgr._event_bus.publish.assert_not_awaited()
    assert mgr._hysteresis["g1"]["h1"] is False
    assert not [c for c in mgr._db.execute_and_commit.await_args_list if "node_state" in c.args[0]]


@pytest.mark.asyncio
async def test_reinitialize_graph_preserves_write_filter_state():
    """A semantic save must not re-send an unchanged actuator value: the
    write-filter state survives the invalidate/reload of the save path."""
    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
            {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst_id, "only_on_change": True}},
        ],
        [{"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"}],
    )
    entry = ("G", True, flow)
    mgr = _make_manager({"g1": entry}, values={src_id: 42})
    # 42 was already written before the save
    mgr._node_state["g1"] = {"w1": {"last_write_val": 42}}

    async def _reload():
        mgr._graphs["g1"] = entry

    mgr.reload = AsyncMock(side_effect=_reload)

    await mgr.reinitialize_graph("g1")

    mgr.reload.assert_awaited_once()
    mgr._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_cascaded_initialization_uses_event_value_over_stale_registry():
    """The registry handler runs concurrently with the logic handler — the
    cascaded sheet must seed from the event value, not the stale registry."""
    src_a, dp_x, dst_y = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # The registry mock is static: dp_x stays 3 even after the write
    mgr = _make_manager(
        {
            "g1": ("A", True, _read_write_flow(src_a, dp_x)),
            "g2": ("B", True, _read_write_flow(dp_x, dst_y)),
        },
        values={src_a: 7, dp_x: 3},
    )
    mgr._execute_graph = AsyncMock()

    async def _deliver(event):
        await mgr._on_value_event(event)

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graph("g1")

    written = [(c.args[0].datapoint_id, c.args[0].value) for c in mgr._event_bus.publish.await_args_list]
    # g2 initialized with the event value 7, not the stale registry 3
    assert written == [(uuid.UUID(dp_x), 7), (uuid.UUID(dst_y), 7)]
    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_pending_graph_still_executes_live_logic_events():
    """Only initialization-flagged cascades are suppressed for bulk-pending
    graphs — a real logic event from an unrelated running sheet executes."""
    from obs.core.event_bus import DataValueEvent

    src_id, dst_id = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g1": ("G", True, _read_write_flow(src_id, dst_id))}, values={src_id: 4})
    mgr._execute_graph = AsyncMock()
    mgr._bulk_init_pending.add("g1")

    live = DataValueEvent(datapoint_id=uuid.UUID(src_id), value=9, quality="good", source_adapter="logic")
    await mgr._on_value_event(live)
    mgr._execute_graph.assert_awaited_once()

    flagged = DataValueEvent(datapoint_id=uuid.UUID(src_id), value=9, quality="good", source_adapter="logic", initialization=True)
    mgr._execute_graph.reset_mock()
    await mgr._on_value_event(flagged)
    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_reinitialize_graph_drops_state_of_retargeted_nodes():
    """A write retargeted to another DataPoint (same node id) must not
    inherit the old target's last_write_val — the new target initializes."""
    src_id, dst_a, dst_b = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    def _rw_flow(dst: str) -> FlowData:
        return _flow(
            [
                {"id": "r1", "type": "datapoint_read", "data": {"datapoint_id": src_id}},
                {"id": "w1", "type": "datapoint_write", "data": {"datapoint_id": dst, "only_on_change": True}},
            ],
            [{"source": "r1", "sourceHandle": "value", "target": "w1", "targetHandle": "value"}],
        )

    mgr = _make_manager({"g1": ("G", True, _rw_flow(dst_a))}, values={src_id: 42})
    mgr._node_state["g1"] = {"w1": {"last_write_val": 42}}

    async def _reload():
        mgr._graphs["g1"] = ("G", True, _rw_flow(dst_b))

    mgr.reload = AsyncMock(side_effect=_reload)

    await mgr.reinitialize_graph("g1")

    mgr._event_bus.publish.assert_awaited_once()
    event = mgr._event_bus.publish.await_args.args[0]
    assert event.datapoint_id == uuid.UUID(dst_b)
    assert event.value == 42


@pytest.mark.asyncio
async def test_cascaded_initialization_does_not_start_read_throttle():
    """The read filter loop keeps last_ts untouched for initialization
    cascades — save-time seeding must not open a throttle window."""
    from obs.core.event_bus import DataValueEvent

    dp_x, dst_y = str(uuid.uuid4()), str(uuid.uuid4())
    mgr = _make_manager({"g2": ("B", True, _read_write_flow(dp_x, dst_y))}, values={dp_x: 3})

    flagged = DataValueEvent(datapoint_id=uuid.UUID(dp_x), value=7, quality="good", source_adapter="logic", initialization=True)
    await mgr._on_value_event(flagged)

    ns = mgr._node_state["g2"]["r1"]
    assert ns["last_value"] == 7
    # seed priming uses the registry ts; the cascade must not stamp `now`
    assert ns.get("last_ts") in (None, _SEED_TS)
    # the cascade still initialized the sheet with the event value
    assert mgr._event_bus.publish.await_args.args[0].value == 7


@pytest.mark.asyncio
async def test_real_logic_write_during_init_publish_executes():
    """Only initialization-flagged events are treated as self-reentry — a
    real logic write from another sheet racing in during the publish await
    executes the graph normally."""
    from obs.core.event_bus import DataValueEvent

    src_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    flow = _flow(
        [
            {"id": "rA", "type": "datapoint_read", "data": {"datapoint_id": src_a}},
            {"id": "wB", "type": "datapoint_write", "data": {"datapoint_id": dp_b}},
            {"id": "rB", "type": "datapoint_read", "data": {"datapoint_id": dp_b}},
        ],
        [{"source": "rA", "sourceHandle": "value", "target": "wB", "targetHandle": "value"}],
    )
    mgr = _make_manager({"g1": ("G", True, flow)}, values={src_a: 7, dp_b: 3})
    mgr._execute_graph = AsyncMock()

    async def _deliver(event):
        await mgr._on_value_event(event)  # own flagged event — suppressed
        other = DataValueEvent(datapoint_id=uuid.UUID(dp_b), value=99, quality="good", source_adapter="logic")
        await mgr._on_value_event(other)  # real write from another sheet

    mgr._event_bus.publish = AsyncMock(side_effect=_deliver)

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_awaited_once()
    overrides = mgr._execute_graph.await_args.args[3]
    assert overrides == {"rB": {"value": 99, "changed": True}}
