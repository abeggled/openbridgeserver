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


def test_without_positions_strips_positions_and_block_names():
    """Positions and the user-defined block name (issue #1157) are cosmetic;
    every other data field stays part of the execution comparison."""
    raw = {"nodes": [{"id": "n1", "position": {"x": 1, "y": 2}, "data": {"a": 1, "label": "Küche"}}], "edges": []}
    assert _without_positions(raw) == {"nodes": [{"id": "n1", "data": {"a": 1}}], "edges": []}


def test_without_positions_keeps_nodes_without_data():
    """A node without a `data` key normalizes to an empty one, so both sides of
    the layout-only comparison are shaped identically."""
    raw = {"nodes": [{"id": "n1", "position": {"x": 0, "y": 0}}], "edges": []}
    assert _without_positions(raw) == {"nodes": [{"id": "n1", "data": {}}], "edges": []}


def test_without_positions_drops_comment_nodes():
    raw = {
        "nodes": [
            {"id": "c1", "type": "comment", "position": {"x": 0, "y": 0}, "data": {"text": "hi"}},
            {"id": "n1", "type": "and", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [],
    }
    assert _without_positions(raw) == {"nodes": [{"id": "n1", "type": "and", "data": {}}], "edges": []}


@pytest.mark.asyncio
async def test_patch_with_corrupt_stored_flow_falls_back_to_reload(monkeypatch):
    """json.loads on a corrupt old row must not fail the request — the
    layout-only check falls back to the (guarded) full reload path."""
    monkeypatch.setattr("obs.logic.manager._manager", None)

    valid_flow = FlowData.model_validate({"nodes": [], "edges": []})
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row("not-json"), _row(json.dumps({"nodes": [], "edges": []}))])
    db.execute = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=valid_flow), _user="admin", db=db)

    assert result.id == "g1"
    assert db.execute.await_count == 2


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
    db.execute = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=moved), _user="admin", db=db)

    assert result.id == "g1"
    manager.update_cached_graph.assert_called_once()
    manager.invalidate_cache.assert_not_called()
    manager.initialize_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_move_only_migrates_legacy_api_client_fields_before_comparison(monkeypatch):
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.initialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    legacy_flow = {
        "nodes": [
            {
                "id": "api",
                "type": "api_client",
                "position": {"x": 0, "y": 0},
                "data": {
                    "headers_secret_file": "/run/secrets/headers",
                    "auth_token_file": "/run/secrets/token",
                },
            }
        ],
        "edges": [],
    }
    moved = FlowData.model_validate(
        {
            "nodes": [
                {
                    "id": "api",
                    "type": "api_client",
                    "position": {"x": 50, "y": 50},
                    "data": {
                        "headers_value_file": "/run/secrets/headers",
                        "auth_value_file": "/run/secrets/token",
                    },
                }
            ],
            "edges": [],
        }
    )
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row(json.dumps(legacy_flow)), _row(moved.model_dump_json())])
    db.execute = AsyncMock()
    db.execute_and_commit = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=moved), _user="admin", db=db)

    assert result.id == "g1"
    manager.update_cached_graph.assert_called_once()
    manager.reinitialize_graph.assert_not_called()


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
    db.execute = AsyncMock()

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
    db.execute = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=edited), _user="admin", db=db)

    assert result.id == "g1"
    manager.update_cached_graph.assert_called_once()
    manager.initialize_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_rename_only_is_layout_only(monkeypatch):
    """Renaming a block (issue #1157) is cosmetic: the sheet must keep running
    with its persisted block state instead of being re-initialized."""
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.initialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    stored_flow = {
        "nodes": [{"id": "n1", "type": "memory", "position": {"x": 0, "y": 0}, "data": {"initial_value": "0"}}],
        "edges": [],
    }
    renamed = FlowData.model_validate(
        {
            "nodes": [{"id": "n1", "type": "memory", "position": {"x": 0, "y": 0}, "data": {"initial_value": "0", "label": "Lüftung Stufe"}}],
            "edges": [],
        }
    )
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row(json.dumps(stored_flow)), _row(renamed.model_dump_json())])
    db.execute = AsyncMock()

    result = await update_graph_partial("g1", LogicGraphUpdate(flow_data=renamed), _user="admin", db=db)

    assert result.id == "g1"
    manager.initialize_graph.assert_not_awaited()
    # The renamed flow must still reach the runtime cache — `message_archive`
    # blocks stamp `data.label` onto every message they archive.
    manager.update_cached_graph.assert_called_once()
    cached_flow = manager.update_cached_graph.call_args.args[-1]
    assert cached_flow.nodes[0].data["label"] == "Lüftung Stufe"


@pytest.mark.asyncio
async def test_patch_config_change_alongside_rename_is_not_layout_only(monkeypatch):
    """The rename exemption must not hide a real configuration change that is
    saved in the same request."""
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.reinitialize_graph = AsyncMock()
    monkeypatch.setattr("obs.logic.manager._manager", manager)

    stored_flow = {
        "nodes": [{"id": "n1", "type": "memory", "position": {"x": 0, "y": 0}, "data": {"initial_value": "0"}}],
        "edges": [],
    }
    changed = FlowData.model_validate(
        {
            "nodes": [{"id": "n1", "type": "memory", "position": {"x": 0, "y": 0}, "data": {"initial_value": "1", "label": "Lüftung Stufe"}}],
            "edges": [],
        }
    )
    db = MagicMock()
    db.fetchone = AsyncMock(side_effect=[_row(json.dumps(stored_flow)), _row(changed.model_dump_json())])
    db.execute = AsyncMock()

    await update_graph_partial("g1", LogicGraphUpdate(flow_data=changed), _user="admin", db=db)

    manager.update_cached_graph.assert_not_called()
    manager.reinitialize_graph.assert_awaited_once()


def test_row_to_out_strips_the_legacy_missing_node_marker():
    """The read boundary every response goes through must hand out the cleaned
    shape — the block card and the rename field both read `data.label`."""
    from obs.api.v1.logic import _row_to_out

    stored = {
        "nodes": [
            {
                "id": "x1",
                "type": "missing_node",
                "position": {"x": 0, "y": 0},
                "data": {"original_type": "gone_v9", "label": "[Fehlend: gone_v9]"},
            },
            {"id": "n1", "type": "and", "position": {"x": 0, "y": 0}, "data": {"label": "Treppenhaus"}},
        ],
        "edges": [],
    }
    out = _row_to_out(_row(json.dumps(stored)))

    assert "label" not in out.flow_data.nodes[0].data
    assert out.flow_data.nodes[0].data["original_type"] == "gone_v9"
    # A genuine block name on a working block is untouched.
    assert out.flow_data.nodes[1].data["label"] == "Treppenhaus"


def test_normalize_missing_node_placeholders_removes_the_generated_marker():
    """Placeholders written before issue #1157 carry a generated German type
    marker in `label`, which now means "user-defined block name"."""
    from obs.api.v1.logic import _normalize_missing_node_placeholders

    flow = FlowData.model_validate(
        {
            "nodes": [
                {
                    "id": "x1",
                    "type": "missing_node",
                    "position": {"x": 0, "y": 0},
                    "data": {"original_type": "gone_v9", "label": "[Fehlend: gone_v9]"},
                }
            ],
            "edges": [],
        }
    )
    _normalize_missing_node_placeholders(flow)
    assert flow.nodes[0].data == {"original_type": "gone_v9"}


def test_normalize_missing_node_placeholders_keeps_a_real_block_name():
    from obs.api.v1.logic import _normalize_missing_node_placeholders

    flow = FlowData.model_validate(
        {
            "nodes": [
                {"id": "x1", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {"original_type": "gone_v9", "label": "Treppenhaus"}},
                # A marker naming a *different* type is not this node's marker.
                {"id": "x2", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {"original_type": "gone_v9", "label": "[Fehlend: other]"}},
                # Not a placeholder at all — a renamed working block.
                {"id": "n1", "type": "and", "position": {"x": 0, "y": 0}, "data": {"label": "[Fehlend: and]"}},
            ],
            "edges": [],
        }
    )
    _normalize_missing_node_placeholders(flow)
    assert flow.nodes[0].data["label"] == "Treppenhaus"
    assert flow.nodes[1].data["label"] == "[Fehlend: other]"
    assert flow.nodes[2].data["label"] == "[Fehlend: and]"


def test_normalize_missing_node_placeholders_promotes_a_type_carried_in_label():
    """A placeholder whose missing type sits in `label` alone would have that
    type presented — and overwritten — by the panel's rename field, so the type
    is moved to `original_type` where renaming cannot reach it."""
    from obs.api.v1.logic import _normalize_missing_node_placeholders

    flow = FlowData.model_validate(
        {
            "nodes": [
                {"id": "x1", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {"label": " gone_v9 "}},
                # Nothing to promote — left exactly as it is.
                {"id": "x2", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "x3", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {"label": "   "}},
                {"id": "x4", "type": "missing_node", "position": {"x": 0, "y": 0}, "data": {"label": 7}},
            ],
            "edges": [],
        }
    )
    _normalize_missing_node_placeholders(flow)
    assert flow.nodes[0].data == {"original_type": "gone_v9"}
    assert flow.nodes[1].data == {}
    assert flow.nodes[2].data == {"label": "   "}
    assert flow.nodes[3].data == {"label": 7}
