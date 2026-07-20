"""Unit tests for LogicManager.initialize_graph (issue #1031).

Verifies that the post-save initialization run:
  - executes an enabled graph containing a configured datapoint_read node,
  - is a no-op for unknown graphs, disabled graphs, graphs without a
    datapoint_read node and read nodes without a configured datapoint_id,
  - swallows execution errors instead of failing the save request.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData


def _make_manager(graphs: dict) -> LogicManager:
    mgr = LogicManager(MagicMock(), MagicMock(), MagicMock())
    mgr._graphs = graphs
    mgr._execute_graph = AsyncMock(return_value={})
    return mgr


def _flow(*nodes: dict) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [
                {"id": f"n{i}", "type": nd.pop("type", "datapoint_read"), "position": {"x": 0, "y": 0}, "data": nd} for i, nd in enumerate(nodes)
            ],
            "edges": [],
        }
    )


@pytest.mark.asyncio
async def test_enabled_graph_with_read_node_is_executed():
    flow = _flow({"datapoint_id": str(uuid.uuid4())})
    mgr = _make_manager({"g1": ("G", True, flow)})

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_awaited_once_with("g1", "G", flow, {})


@pytest.mark.asyncio
async def test_unknown_graph_is_noop():
    mgr = _make_manager({})

    await mgr.initialize_graph("missing")

    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_graph_is_noop():
    flow = _flow({"datapoint_id": str(uuid.uuid4())})
    mgr = _make_manager({"g1": ("G", False, flow)})

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_without_read_node_is_noop():
    flow = _flow({"type": "and"})
    mgr = _make_manager({"g1": ("G", True, flow)})

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_node_without_datapoint_id_is_noop():
    flow = _flow({"datapoint_name": "unconfigured"})
    mgr = _make_manager({"g1": ("G", True, flow)})

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_error_is_swallowed():
    flow = _flow({"datapoint_id": str(uuid.uuid4())})
    mgr = _make_manager({"g1": ("G", True, flow)})
    mgr._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))

    await mgr.initialize_graph("g1")

    mgr._execute_graph.assert_awaited_once()
