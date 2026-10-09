"""Binary Statistics + Change Filter: no-pulse provenance is decided per output handle (#1164)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tests.unit.conftest import edge, node
from tests.unit.test_host_check import _flow, _make_manager


async def _run_twice(stats_cfg: dict, decisive_value: str):
    target = uuid.uuid4()
    flow = _flow(
        [
            node("source", "const_value", {"value": "1", "data_type": "number"}),
            node("cf", "change_filter"),
            node("decisive", "const_value", {"value": decisive_value, "data_type": "boolean"}),
            node("stats", "binary_stats", stats_cfg),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("source", "cf", "value", "in"),
            edge("cf", "stats", "changed", "in1"),
            edge("decisive", "stats", "value", "in2"),
            edge("stats", "write", "threshold_reached", "value"),
        ],
    )
    manager = _make_manager()
    graph_id = "g"
    manager._graphs[graph_id] = ("test", True, flow)
    manager._node_state[graph_id] = {}
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        await manager._execute_graph(graph_id, "test", flow, {})
        return await manager._execute_graph(graph_id, "test", flow, {})


@pytest.mark.asyncio
async def test_threshold_decided_by_a_sibling_is_not_suppressed_by_a_missing_pulse():
    out = await _run_twice({"input_count": 2, "threshold_count": 1}, "true")
    assert out["stats"]["threshold_reached"] is True
    assert out["write"]["_write_value"] is True


@pytest.mark.asyncio
async def test_threshold_that_depends_on_the_pulse_is_still_suppressed():
    out = await _run_twice({"input_count": 2, "threshold_count": 2}, "true")
    assert out["write"].get("_write_value") is None


@pytest.mark.asyncio
async def test_probe_failure_stays_provenance_conservative():
    with patch("obs.logic.executor.GraphExecutor._nan_aware_equal", side_effect=RuntimeError("boom")):
        out = await _run_twice({"input_count": 2, "threshold_count": 1}, "true")
    assert out["write"].get("_write_value") is None


@pytest.mark.asyncio
async def test_off_threshold_is_independent_of_the_pulse():
    out = await _run_twice({"input_count": 2, "threshold_count": "x"}, "true")
    assert out["write"]["_write_value"] is False


@pytest.mark.asyncio
async def test_sustained_change_filter_out_input_preserves_the_threshold():
    target = uuid.uuid4()
    flow = _flow(
        [
            node("source", "const_value", {"value": "true", "data_type": "boolean"}),
            node("cf", "change_filter"),
            node("stats", "binary_stats", {"input_count": 2, "threshold_count": 1}),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("source", "cf", "value", "in"),
            edge("cf", "stats", "changed", "in1"),
            edge("cf", "stats", "out", "in2"),
            edge("stats", "write", "threshold_reached", "value"),
        ],
    )
    manager = _make_manager()
    manager._graphs["g"] = ("test", True, flow)
    manager._node_state["g"] = {}
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        await manager._execute_graph("g", "test", flow, {})
        out = await manager._execute_graph("g", "test", flow, {})
    assert out["stats"]["threshold_reached"] is True
    assert out["write"]["_write_value"] is True


def _stats_manager(flow):
    manager = _make_manager()
    manager._graphs["g"] = ("test", True, flow)
    manager._node_state["g"] = {}
    return manager


@pytest.mark.asyncio
async def test_nested_stats_with_sustained_upstream_handle_preserves_the_threshold():
    target = uuid.uuid4()
    flow = _flow(
        [
            node("src", "const_value", {"value": "1", "data_type": "number"}),
            node("cf", "change_filter"),
            node("a", "binary_stats", {"input_count": 2}),
            node("b", "binary_stats", {"input_count": 2, "threshold_count": 1}),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("src", "cf", "value", "in"),
            edge("cf", "a", "changed", "in1"),
            edge("a", "b", "total", "in1"),
            edge("a", "b", "majority_true", "in2"),
            edge("b", "write", "threshold_reached", "value"),
        ],
    )
    manager = _stats_manager(flow)
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        await manager.execute_graph("g")
        out = await manager.execute_graph("g")
    assert out["b"]["threshold_reached"] is True
    assert out["write"]["_write_value"] is True


@pytest.mark.asyncio
async def test_debug_override_decides_the_output_despite_idle_pulse_inputs():
    target = uuid.uuid4()
    flow = _flow(
        [
            node("s1", "const_value", {"value": "1", "data_type": "number"}),
            node("cf1", "change_filter"),
            node("s2", "const_value", {"value": "1", "data_type": "number"}),
            node("cf2", "change_filter"),
            node("stats", "binary_stats", {"input_count": 2, "threshold_count": 1}),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("s1", "cf1", "value", "in"),
            edge("cf1", "stats", "changed", "in1"),
            edge("s2", "cf2", "value", "in"),
            edge("cf2", "stats", "changed", "in2"),
            edge("stats", "write", "threshold_reached", "value"),
        ],
    )
    manager = _stats_manager(flow)
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        await manager.execute_graph_debug("g", {"stats": {"in1": "true"}})
        out, _ = await manager.execute_graph_debug("g", {"stats": {"in1": "true"}})
    assert out["stats"]["threshold_reached"] is True
    assert out["write"]["_write_value"] is True


def _unresolved_flow(threshold: int):
    target = uuid.uuid4()
    return _flow(
        [
            node("unseeded", "datapoint_read", {}),
            node("decisive", "const_value", {"value": "true", "data_type": "boolean"}),
            node("stats", "binary_stats", {"input_count": 2, "threshold_count": threshold}),
            node("cf", "change_filter"),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("unseeded", "stats", "value", "in1"),
            edge("decisive", "stats", "value", "in2"),
            edge("stats", "cf", "threshold_reached", "in"),
            edge("cf", "write", "out", "value"),
        ],
    )


@pytest.mark.asyncio
async def test_output_decided_by_a_resolved_input_survives_unresolved_source_taint():
    manager = _stats_manager(_unresolved_flow(1))
    manager._hysteresis["g"] = {"cf": {"value": False}}
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        out = await manager.execute_graph("g")
    assert out["stats"]["threshold_reached"] is True
    assert out["cf"]["changed"] is True
    assert out["write"]["_write_value"] is True


@pytest.mark.asyncio
async def test_output_that_depends_on_the_unresolved_source_stays_held():
    manager = _stats_manager(_unresolved_flow(2))
    manager._hysteresis["g"] = {"cf": {"value": False}}
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        out = await manager.execute_graph("g")
    assert out["cf"]["changed"] is False
