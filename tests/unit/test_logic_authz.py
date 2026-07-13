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


async def _insert_grant(
    db: Database,
    node_id: str,
    *,
    role: str = "guest",
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    effect: str = "allow",
    central_control: bool = False,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect, central_control)
        VALUES ('user', ?, ?, ?, ?, ?, ?)
        """,
        (principal_id, node_type, node_id, role, effect, int(central_control)),
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


def _logic_ids(flow_json: str) -> list[str]:
    return [node["data"]["datapoint_id"] for node in json.loads(flow_json)["nodes"]]


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
async def test_side_effect_graph_requires_graph_grant_even_when_datapoints_are_readable(db: Database):
    allowed_dp, _ = await _seed_scope(db)
    flow = _flow_with_nodes([_api_client_node(allowed_dp)])
    await _insert_graph_flow(db, "graph-api-client-readable", "API client readable graph", flow)
    principal = Principal(subject="alice", type="user", is_admin=False)

    with pytest.raises(HTTPException) as get_error:
        await logic_api.get_graph(graph_id="graph-api-client-readable", _user=principal, db=db)
    assert get_error.value.status_code == 404

    with pytest.raises(HTTPException) as export_error:
        await logic_api.export_graph(graph_id="graph-api-client-readable", _user=principal, db=db)
    assert export_error.value.status_code == 404

    await _insert_grant(db, "graph-api-client-readable", role="resident", node_type="logic_graph")
    graph = await logic_api.get_graph(graph_id="graph-api-client-readable", _user=principal, db=db)
    assert graph.id == "graph-api-client-readable"


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
async def test_full_update_requires_generate_scope_for_graph_and_every_proposed_datapoint(db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db, allowed_role="operator")
    await _insert_grant(db, "graph-edit", role="operator", node_type="logic_graph")
    await _insert_graph(db, "graph-edit", "Editable", blocked_dp)
    principal = Principal(subject="alice", type="user", is_admin=False)
    body = logic_api.LogicGraphCreate(
        name="Denied change",
        flow_data=logic_api.FlowData.model_validate_json(_flow(allowed_dp)),
        control_class="room_local",
    )

    with pytest.raises(HTTPException) as denied:
        await logic_api.update_graph_full("graph-edit", body, _user=principal, db=db)

    assert denied.value.status_code == 403
    row = await db.fetchone("SELECT name, flow_data FROM logic_graphs WHERE id='graph-edit'")
    assert row["name"] == "Editable"
    assert _logic_ids(row["flow_data"]) == [str(blocked_dp)]

    await _insert_grant(db, "blocked-room", role="operator")
    updated = await logic_api.update_graph_full("graph-edit", body, _user=principal, db=db)
    assert updated.name == "Denied change"
    assert _logic_ids(updated.flow_data.model_dump_json()) == [str(allowed_dp)]


@pytest.mark.asyncio
async def test_existing_graph_mutation_conceals_unreadable_graph_before_generate_check(db: Database):
    _, blocked_dp = await _seed_scope(db, allowed_role="operator")
    await _insert_graph(db, "graph-hidden", "Hidden", blocked_dp)

    with pytest.raises(HTTPException) as exc:
        await logic_api.update_graph_partial(
            "graph-hidden",
            logic_api.LogicGraphUpdate(name="Leak"),
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_requires_generate_role_and_preserves_admin_bridge(db: Database):
    await _insert_graph(db, "graph-delete", "Delete", enabled=False)
    await _insert_grant(db, "graph-delete", role="resident", node_type="logic_graph")

    with pytest.raises(HTTPException) as denied:
        await logic_api.delete_graph(
            "graph-delete",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )
    assert denied.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM logic_graphs WHERE id='graph-delete'") is not None

    await logic_api.delete_graph(
        "graph-delete",
        _user=Principal(subject="owner", type="user", is_admin=True),
        db=db,
    )
    assert await db.fetchone("SELECT 1 FROM logic_graphs WHERE id='graph-delete'") is None


@pytest.mark.asyncio
async def test_central_graph_mutation_requires_scope_switch(db: Database):
    await _insert_graph(db, "graph-central", "Central")
    await db.execute_and_commit("UPDATE logic_graphs SET control_class='central_plant' WHERE id='graph-central'")
    await _insert_grant(db, "graph-central", role="operator", node_type="logic_graph")
    principal = Principal(subject="alice", type="user", is_admin=False)

    with pytest.raises(HTTPException) as denied:
        await logic_api.update_graph_partial(
            "graph-central",
            logic_api.LogicGraphUpdate(name="Downgrade", control_class="room_local"),
            _user=principal,
            db=db,
        )
    assert denied.value.status_code == 403

    await db.execute_and_commit(
        """UPDATE authz_node_roles SET central_control=1
           WHERE principal_id='alice' AND node_type='logic_graph' AND node_id='graph-central'"""
    )
    updated = await logic_api.update_graph_full(
        "graph-central",
        logic_api.LogicGraphCreate(name="Allowed central"),
        _user=principal,
        db=db,
    )
    assert updated.name == "Allowed central"
    assert updated.control_class == "central_plant"


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
async def test_get_datapoint_logic_usages_hides_graphs_with_out_of_scope_api_client_variables(db: Database):
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
async def test_run_graph_requires_activation_scope_for_api_client_variable_datapoints(monkeypatch, db: Database):
    allowed_dp, blocked_dp = await _seed_scope(db, allowed_role="resident")
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
    manager = AsyncMock()
    manager.execute_graph.return_value = {"read": {"out": True}}
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(
            graph_id="graph-api-client-mixed",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    manager.execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_graph_allows_resident_when_all_datapoints_are_in_scope(monkeypatch, db: Database):
    allowed_dp, _ = await _seed_scope(db, allowed_role="resident")
    await _insert_graph(db, "allowed-graph", "Allowed graph", allowed_dp)
    await _insert_grant(db, "allowed-graph", role="resident", node_type="logic_graph")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", ["api_client", "notify_pushover", "notify_sms", "python_script", "wake_on_lan"])
async def test_run_graph_rejects_non_admin_side_effect_nodes(monkeypatch, db: Database, node_type: str):
    flow = _flow_with_nodes(
        [
            {
                "id": "side-effect",
                "type": node_type,
                "position": {"x": 0, "y": 0},
                "data": {},
            },
        ],
    )
    await _insert_graph_flow(db, f"graph-{node_type}", "Side effect", flow)
    manager = AsyncMock()
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(
            graph_id=f"graph-{node_type}",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    manager.execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_graph_allows_admin_side_effect_nodes(monkeypatch, db: Database):
    flow = _flow_with_nodes(
        [
            {
                "id": "side-effect",
                "type": "wake_on_lan",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
        ],
    )
    await _insert_graph_flow(db, "graph-admin-side-effect", "Admin side effect", flow)
    manager = AsyncMock()
    manager.execute_graph.return_value = {}
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    result = await logic_api.run_graph(
        graph_id="graph-admin-side-effect",
        _user=Principal(subject="admin", type="user", is_admin=True),
        db=db,
    )

    assert result == {"status": "ok", "outputs": {}, "warnings": []}
    manager.execute_graph.assert_awaited_once_with("graph-admin-side-effect")
