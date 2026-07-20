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
