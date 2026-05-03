"""Unit tests for DataPoint rename propagation into logic graphs (issue #333).

Verifies that:
  - _on_datapoint_renamed updates datapoint_name in all graph nodes that reference
    the renamed DataPoint and persists the change to the DB.
  - Nodes that reference a different DataPoint are not touched.
  - Nodes already carrying the correct name are not re-written.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.core.event_bus import DataPointRenamedEvent
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(graphs: dict) -> tuple[LogicManager, MagicMock]:
    """Return a LogicManager with an in-memory graph cache and a tracked DB mock."""
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    event_bus = MagicMock()
    registry = MagicMock()

    mgr = LogicManager(db, event_bus, registry)
    mgr._graphs = graphs
    return mgr, db


def _flow_with_nodes(*nodes_data: dict) -> FlowData:
    nodes = [
        {
            "id": f"n{i}",
            "type": "datapoint_read",
            "position": {"x": 0, "y": 0},
            "data": nd,
        }
        for i, nd in enumerate(nodes_data)
    ]
    return FlowData.model_validate({"nodes": nodes, "edges": []})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_updates_matching_node():
    dp_id = uuid.uuid4()
    flow = _flow_with_nodes({"datapoint_id": str(dp_id), "datapoint_name": "Alt"})
    mgr, db = _make_manager({"g1": ("MyGraph", True, flow)})

    event = DataPointRenamedEvent(dp_id=dp_id, old_name="Alt", new_name="Neu")
    await mgr._on_datapoint_renamed(event)

    assert flow.nodes[0].data["datapoint_name"] == "Neu"
    db.execute_and_commit.assert_awaited_once()
    call_args = db.execute_and_commit.call_args[0]
    assert "UPDATE logic_graphs" in call_args[0]
    saved_flow = json.loads(call_args[1][0])
    assert saved_flow["nodes"][0]["data"]["datapoint_name"] == "Neu"


@pytest.mark.asyncio
async def test_rename_leaves_unrelated_node_unchanged():
    dp_id = uuid.uuid4()
    other_id = uuid.uuid4()
    flow = _flow_with_nodes(
        {"datapoint_id": str(other_id), "datapoint_name": "Unrelated"},
    )
    mgr, db = _make_manager({"g1": ("MyGraph", True, flow)})

    event = DataPointRenamedEvent(dp_id=dp_id, old_name="Alt", new_name="Neu")
    await mgr._on_datapoint_renamed(event)

    assert flow.nodes[0].data["datapoint_name"] == "Unrelated"
    db.execute_and_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_skips_node_already_up_to_date():
    dp_id = uuid.uuid4()
    flow = _flow_with_nodes({"datapoint_id": str(dp_id), "datapoint_name": "Neu"})
    mgr, db = _make_manager({"g1": ("MyGraph", True, flow)})

    event = DataPointRenamedEvent(dp_id=dp_id, old_name="Alt", new_name="Neu")
    await mgr._on_datapoint_renamed(event)

    db.execute_and_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_updates_multiple_graphs_and_nodes():
    dp_id = uuid.uuid4()
    flow1 = _flow_with_nodes(
        {"datapoint_id": str(dp_id), "datapoint_name": "Alt"},
        {"datapoint_id": str(dp_id), "datapoint_name": "Alt"},
    )
    flow2 = _flow_with_nodes({"datapoint_id": str(dp_id), "datapoint_name": "Alt"})
    mgr, db = _make_manager({"g1": ("G1", True, flow1), "g2": ("G2", True, flow2)})

    event = DataPointRenamedEvent(dp_id=dp_id, old_name="Alt", new_name="Neu")
    await mgr._on_datapoint_renamed(event)

    assert flow1.nodes[0].data["datapoint_name"] == "Neu"
    assert flow1.nodes[1].data["datapoint_name"] == "Neu"
    assert flow2.nodes[0].data["datapoint_name"] == "Neu"
    assert db.execute_and_commit.await_count == 2


@pytest.mark.asyncio
async def test_rename_handles_node_without_datapoint_id():
    """Nodes that have no datapoint_id (e.g. math nodes) must not be touched."""
    dp_id = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                {"id": "m1", "type": "math_formula", "position": {"x": 0, "y": 0}, "data": {"formula": "a+b"}},
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id), "datapoint_name": "Alt"},
                },
            ],
            "edges": [],
        }
    )
    mgr, db = _make_manager({"g1": ("G1", True, flow)})

    event = DataPointRenamedEvent(dp_id=dp_id, old_name="Alt", new_name="Neu")
    await mgr._on_datapoint_renamed(event)

    assert flow.nodes[1].data["datapoint_name"] == "Neu"
    db.execute_and_commit.assert_awaited_once()
