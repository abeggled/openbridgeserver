"""Unit tests for the PATCH layout-only detection (issue #1031).

A PATCH carrying only moved node positions must not re-initialize the sheet;
corrupt stored flow JSON falls back to the full reload path instead of
failing the request.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.api.v1.logic import _without_positions, update_graph_partial
from obs.logic.models import FlowData, LogicGraphUpdate


def _row(flow_data: str) -> dict:
    return {
        "id": "g1",
        "name": "G",
        "description": "",
        "enabled": 1,
        "flow_data": flow_data,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def test_without_positions_strips_only_positions():
    raw = {"nodes": [{"id": "n1", "position": {"x": 1, "y": 2}, "data": {"a": 1}}], "edges": []}
    assert _without_positions(raw) == {"nodes": [{"id": "n1", "data": {"a": 1}}], "edges": []}


@pytest.mark.asyncio
async def test_patch_with_corrupt_stored_flow_falls_back_to_reload(monkeypatch):
    """json.loads on a corrupt old row must not fail the request — the
    layout-only check falls back to the (guarded) full reload path."""
    monkeypatch.setattr("obs.logic.manager._manager", None)

    valid_flow = FlowData.model_validate({"nodes": [], "edges": []})
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row("not-json"), _row(json.dumps({"nodes": [], "edges": []}))])
    db.execute_and_commit = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=valid_flow), _user="admin", db=db)

    assert result.id == "g1"
    db.execute_and_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_patch_move_only_on_legacy_flow_is_layout_only(monkeypatch):
    """Stored graphs from older exports may omit optional edge handles that a
    freshly parsed body carries as null — a move-only PATCH must still be
    classified layout-only and not re-initialize the sheet."""
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.initialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    # Legacy stored flow: no sourceHandle/targetHandle keys at all
    legacy_flow = {
        "nodes": [{"id": "n1", "type": "and", "position": {"x": 0, "y": 0}, "data": {}}],
        "edges": [{"id": "e1", "source": "n1", "target": "n1"}],
    }
    moved = FlowData.model_validate(
        {
            "nodes": [{"id": "n1", "type": "and", "position": {"x": 50, "y": 50}, "data": {}}],
            "edges": [{"id": "e1", "source": "n1", "target": "n1"}],
        }
    )
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row(json.dumps(legacy_flow)), _row(moved.model_dump_json())])
    db.execute_and_commit = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=moved), _user="admin", db=db)

    assert result.id == "g1"
    manager.update_cached_graph.assert_called_once()
    manager.invalidate_cache.assert_not_called()
    manager.initialize_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_repeating_stored_enabled_is_noop(monkeypatch):
    """PATCH {"enabled": true} on an already-enabled graph without flow_data
    must not cancel/reload the running sheet or re-run initialization."""
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.initialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    row = _row(json.dumps({"nodes": [], "edges": []}))
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[row, row])
    db.execute_and_commit = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(enabled=True), _user="admin", db=db)

    assert result.id == "g1"
    manager.invalidate_cache.assert_not_called()
    manager.reload.assert_not_awaited()
    manager.initialize_graph.assert_not_awaited()
    manager.reinitialize_graph.assert_not_called()
    manager.update_cached_graph_name.assert_called_once()


@pytest.mark.asyncio
async def test_patch_comment_edit_is_layout_only(monkeypatch):
    """Editing a purely visual comment node (text/size) has no execution
    semantics — the save must not re-initialize the sheet."""
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.initialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    def _graph(comment_text: str) -> dict:
        return {
            "nodes": [
                {"id": "n1", "type": "and", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "k1", "type": "comment", "position": {"x": 10, "y": 10}, "data": {"text": comment_text}},
            ],
            "edges": [],
        }

    edited = FlowData.model_validate(_graph("updated documentation"))
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row(json.dumps(_graph("old text"))), _row(edited.model_dump_json())])
    db.execute_and_commit = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=edited), _user="admin", db=db)

    assert result.id == "g1"
    manager.update_cached_graph.assert_called_once()
    manager.initialize_graph.assert_not_awaited()
