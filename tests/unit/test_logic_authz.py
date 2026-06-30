from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import logic as logic_api
from obs.db.database import Database


NOW = "2026-06-12T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_tree(db: Database) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (NOW, NOW),
    )


async def _insert_node(db: Database, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', NULL, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp_id: uuid.UUID, name: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', 'degC', '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (str(dp_id), name, f"dp/{dp_id}/value", NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: uuid.UUID, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), node_id, str(dp_id), NOW),
    )


async def _insert_grant(db: Database, node_id: str, *, role: str = "guest", principal_id: str = "alice") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, ?, 'allow')
        """,
        (principal_id, node_id, role),
    )


def _flow(*dp_ids: uuid.UUID) -> str:
    nodes = [
        {
            "id": f"n{index}",
            "type": "datapoint_read",
            "position": {"x": 0, "y": index},
            "data": {"datapoint_id": str(dp_id)},
        }
        for index, dp_id in enumerate(dp_ids)
    ]
    return json.dumps({"nodes": nodes, "edges": []})


def _flow_with_nodes(nodes: list[dict]) -> str:
    return json.dumps({"nodes": nodes, "edges": []})


def _api_client_node(dp_id: uuid.UUID, node_id: str = "api-client") -> dict:
    return {
        "id": node_id,
        "type": "api_client",
        "position": {"x": 0, "y": 0},
        "data": {
            "url": "https://example.test/###OBS1###",
            "variables": [{"slot": 1, "datapoint_id": str(dp_id), "datapoint_name": "Secret"}],
        },
    }


async def _insert_graph(db: Database, graph_id: str, name: str, *dp_ids: uuid.UUID, enabled: bool = True) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
        VALUES (?, ?, '', ?, ?, ?, ?)
        """,
        (graph_id, name, int(enabled), _flow(*dp_ids), NOW, NOW),
    )


async def _insert_graph_flow(db: Database, graph_id: str, name: str, flow_data: str, *, enabled: bool = True) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
        VALUES (?, ?, '', ?, ?, ?, ?)
        """,
        (graph_id, name, int(enabled), flow_data, NOW, NOW),
    )


async def _seed_scope(db: Database, *, allowed_role: str = "guest") -> tuple[uuid.UUID, uuid.UUID]:
    await _insert_tree(db)
    await _insert_node(db, "allowed-room")
    await _insert_node(db, "blocked-room")
    allowed_dp = uuid.UUID("00000000-0000-0000-0000-000000000101")
    blocked_dp = uuid.UUID("00000000-0000-0000-0000-000000000102")
    await _insert_datapoint(db, allowed_dp, "Allowed")
    await _insert_datapoint(db, blocked_dp, "Blocked")
    await _link_datapoint(db, allowed_dp, "allowed-room")
    await _link_datapoint(db, blocked_dp, "blocked-room")
    await _insert_grant(db, "allowed-room", role=allowed_role)
    return allowed_dp, blocked_dp


@pytest.mark.asyncio
async def test_list_graphs_filters_out_unreadable_logic_graphs(db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-allowed", "Allowed graph", allowed_dp)
    await _insert_graph(db, "graph-blocked", "Blocked graph", blocked_dp)

    result = await logic_api.list_graphs(
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert [graph.id for graph in result] == ["graph-allowed"]


@pytest.mark.asyncio
async def test_get_graph_returns_404_for_existing_out_of_scope_graph(db: Database):
    _, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-blocked", "Blocked graph", blocked_dp)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.get_graph(
            graph_id="graph-blocked",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_graph_requires_read_scope_for_all_referenced_datapoints(db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-mixed", "Mixed graph", allowed_dp, blocked_dp)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.get_graph(
            graph_id="graph-mixed",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_graph_requires_read_scope_for_api_client_variable_datapoints(db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db)
    flow = _flow_with_nodes(
        [
            {
                "id": "read",
                "type": "datapoint_read",
                "position": {"x": 0, "y": 0},
                "data": {"datapoint_id": str(allowed_dp)},
            },
            _api_client_node(blocked_dp),
        ]
    )
    await _insert_graph_flow(db, "graph-api-client-mixed", "API client mixed graph", flow)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.get_graph(
            graph_id="graph-api-client-mixed",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_export_graph_returns_404_for_existing_out_of_scope_graph(db: Database):
    _, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-blocked", "Blocked graph", blocked_dp)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.export_graph(
            graph_id="graph-blocked",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_datapoint_logic_usages_returns_404_for_out_of_scope_datapoint(db: Database):
    _, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-blocked", "Blocked graph", blocked_dp)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.get_datapoint_logic_usages(
            dp_id=str(blocked_dp),
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_datapoint_logic_usages_returns_readable_usages(db: Database):
    allowed_dp, _ = await _seed_scope(db)
    await _insert_graph(db, "graph-allowed", "Allowed graph", allowed_dp)

    result = await logic_api.get_datapoint_logic_usages(
        dp_id=str(allowed_dp),
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert len(result) == 1
    assert result[0].graph_id == "graph-allowed"
    assert result[0].direction == "SOURCE"


@pytest.mark.asyncio
async def test_get_datapoint_logic_usages_hides_mixed_scope_graphs(db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db)
    await _insert_graph(db, "graph-mixed", "Mixed graph", allowed_dp, blocked_dp)

    result = await logic_api.get_datapoint_logic_usages(
        dp_id=str(allowed_dp),
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result == []


@pytest.mark.asyncio
async def test_run_graph_requires_activation_scope_for_all_referenced_datapoints(monkeypatch, db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db, allowed_role="resident")
    await _insert_graph(db, "mixed-graph", "Mixed graph", allowed_dp, blocked_dp)
    manager = AsyncMock()
    manager.execute_graph.return_value = {"n0": {"out": True}}
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(
            graph_id="mixed-graph",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    manager.execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_graph_allows_resident_when_all_datapoints_are_in_scope(monkeypatch, db: Database):
    allowed_dp, _ = await _seed_scope(db, allowed_role="resident")
    await _insert_graph(db, "allowed-graph", "Allowed graph", allowed_dp)
    manager = AsyncMock()
    manager.execute_graph.return_value = {"n0": {"out": True}}
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    result = await logic_api.run_graph(
        graph_id="allowed-graph",
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result == {"status": "ok", "outputs": {"n0": {"out": True}}, "warnings": []}
    manager.execute_graph.assert_awaited_once_with("allowed-graph")
