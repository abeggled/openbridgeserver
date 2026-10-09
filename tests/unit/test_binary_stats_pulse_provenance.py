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
