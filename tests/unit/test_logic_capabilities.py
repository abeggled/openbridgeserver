from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from obs.api.auth import Principal
from obs.api.authz import AuthzTarget, RoleGrant
from obs.api.v1 import authz as authz_api
from obs.api.v1 import logic as logic_api
from obs.db.database import Database
from obs.logic.capabilities import LOGIC_CAPABILITIES, LOGIC_NODE_CAPABILITIES, PURE_LOGIC_NODE_TYPES
from obs.logic.models import LogicGraphCreate, LogicGraphImport, LogicGraphUpdate, NodeTypeDef
from obs.logic.node_types import NODE_TYPE_REGISTRY, _classify_node_type
from obs.models.authz import AuthzPrincipalGrant


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_graph(
    db: Database,
    graph_id: str,
    nodes: list[dict],
    *,
    enabled: bool = True,
    control_class: str = "room_local",
) -> dict:
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """
        INSERT INTO logic_graphs
            (id, name, description, enabled, flow_data, control_class, created_at, updated_at)
        VALUES (?, 'Capabilities', '', ?, ?, ?, ?, ?)
        """,
        (graph_id, int(enabled), json.dumps({"nodes": nodes, "edges": []}), control_class, now, now),
    )
    return await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))


async def _grant(
    db: Database,
    node_type: str,
    node_id: str,
    *,
    role: str = "resident",
    effect: str = "allow",
    central_control: bool = False,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect, central_control)
        VALUES ('user', 'alice', ?, ?, ?, ?, ?)
        """,
        (node_type, node_id, role, effect, int(central_control)),
    )


def _node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": data or {},
    }


def test_privileged_node_types_publish_stable_capabilities() -> None:
    expected = {
        "api_client": "http_request",
        "host_check": "network_probe",
        "ical": "http_request",
        "notify_pushover": "notification",
        "notify_sms": "sms",
        "python_script": "python_execution",
        "wake_on_lan": "wake_on_lan",
    }

    assert LOGIC_CAPABILITIES == frozenset(expected.values())
    for node_type, capability in expected.items():
        definition = NODE_TYPE_REGISTRY[node_type]
        assert definition.has_external_side_effect is True
        assert definition.required_capability == capability

    assert LOGIC_NODE_CAPABILITIES == expected
    assert set(NODE_TYPE_REGISTRY) == PURE_LOGIC_NODE_TYPES | set(LOGIC_NODE_CAPABILITIES)
    assert PURE_LOGIC_NODE_TYPES.isdisjoint(LOGIC_NODE_CAPABILITIES)
    for node_type in PURE_LOGIC_NODE_TYPES:
        definition = NODE_TYPE_REGISTRY[node_type]
        assert definition.has_external_side_effect is False
        assert definition.required_capability is None

    unclassified = _classify_node_type(
        NodeTypeDef(type="future_node", label="Future", category="integration"),
    )
    assert unclassified.has_external_side_effect is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("node_type", "capability"), sorted(LOGIC_NODE_CAPABILITIES.items()))
async def test_every_external_executor_node_requires_its_capability(
    db: Database,
    node_type: str,
    capability: str,
) -> None:
    graph_id = f"graph-{node_type}"
    row = await _insert_graph(db, graph_id, [_node("external", node_type)])
    await _grant(db, "logic_graph", graph_id)

    preflight = await logic_api._logic_run_preflight(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        row,
    )

    check = next(item for item in preflight.checks if item.target_type == "logic_capability")
    assert (check.target_id, check.node_ids, check.allowed, check.reason) == (
        capability,
        ["external"],
        False,
        "missing_allow",
    )


@pytest.mark.asyncio
async def test_preflight_requires_graph_and_every_side_effect_capability(db: Database) -> None:
    row = await _insert_graph(
        db,
        "graph-1",
        [_node("http", "api_client"), _node("sms", "notify_sms")],
    )
    await _grant(db, "logic_graph", "graph-1")
    await _grant(db, "logic_capability", "http_request")

    preflight = await logic_api._logic_run_preflight(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        row,
    )

    assert preflight.allowed is False
    assert [(check.target_id, check.allowed, check.reason) for check in preflight.checks] == [
        ("graph-1", True, "allowed"),
        ("enabled", True, "enabled"),
        ("http_request", True, "allowed"),
        ("sms", False, "missing_allow"),
    ]


@pytest.mark.asyncio
async def test_run_executes_when_graph_and_capability_are_allowed(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_graph(db, "graph-run", [_node("http", "api_client")])
    await _grant(db, "logic_graph", "graph-run")
    await _grant(db, "logic_capability", "http_request")
    manager = AsyncMock()
    manager.execute_graph.return_value = {"http": {"success": True}}
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    result = await logic_api.run_graph(
        "graph-run",
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result["status"] == "ok"
    manager.execute_graph.assert_awaited_once_with("graph-run")


@pytest.mark.asyncio
async def test_preflight_explicit_capability_deny_overrides_allow(db: Database) -> None:
    row = await _insert_graph(db, "graph-2", [_node("http", "api_client")])
    await _grant(db, "logic_graph", "graph-2")
    await _grant(db, "logic_capability", "http_request", effect="deny")

    preflight = await logic_api._logic_run_preflight(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        row,
    )

    denied = next(check for check in preflight.checks if check.target_id == "http_request")
    assert preflight.allowed is False
    assert denied.reason == "explicit_deny"


@pytest.mark.asyncio
async def test_preflight_default_denies_side_effect_without_capability(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        NODE_TYPE_REGISTRY,
        "future_side_effect",
        NodeTypeDef(
            type="future_side_effect",
            label="Future",
            category="integration",
        ),
    )
    monkeypatch.setitem(
        NODE_TYPE_REGISTRY,
        "malformed_side_effect",
        NodeTypeDef(
            type="malformed_side_effect",
            label="Malformed",
            category="integration",
            has_external_side_effect=True,
        ),
    )
    row = await _insert_graph(
        db,
        "graph-3",
        [
            _node("future", "future_side_effect"),
            _node("malformed", "malformed_side_effect"),
            _node("unknown", "unknown_node"),
        ],
    )
    await _grant(db, "logic_graph", "graph-3", role="owner")

    preflight = await logic_api._logic_run_preflight(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        row,
    )

    assert preflight.allowed is False
    denied = {check.target_id: check for check in preflight.checks if not check.allowed}
    assert denied["future_side_effect"].node_ids == ["future"]
    assert denied["future_side_effect"].reason == "undeclared_capability"
    assert denied["malformed_side_effect"].node_ids == ["malformed"]
    assert denied["malformed_side_effect"].reason == "undeclared_capability"
    assert denied["unknown_node"].node_ids == ["unknown"]
    assert denied["unknown_node"].reason == "undeclared_capability"


@pytest.mark.asyncio
async def test_admin_bridge_keeps_full_logic_activation_access(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        NODE_TYPE_REGISTRY,
        "future_side_effect",
        NodeTypeDef(
            type="future_side_effect",
            label="Future",
            category="integration",
        ),
    )
    row = await _insert_graph(
        db,
        "graph-admin",
        [
            _node("python", "python_script"),
            _node("future", "future_side_effect"),
            _node("legacy", "legacy_unknown_node"),
        ],
    )

    preflight = await logic_api._logic_run_preflight(
        db,
        Principal(subject="admin", type="user", is_admin=True),
        row,
    )

    assert preflight.allowed is True
    assert {check.reason for check in preflight.checks} == {"admin", "enabled"}


@pytest.mark.asyncio
async def test_central_graph_run_preflight_requires_central_control(db: Database, monkeypatch) -> None:
    row = await _insert_graph(db, "graph-central", [], control_class="central_plant")
    await _grant(db, "logic_graph", "graph-central")
    principal = Principal(subject="alice", type="user", is_admin=False)

    denied = await logic_api._logic_run_preflight(db, principal, row)
    graph_check = next(check for check in denied.checks if check.target_type == "logic_graph")
    assert denied.allowed is False
    assert (graph_check.allowed, graph_check.reason) == (False, "central_control_required")

    with pytest.raises(HTTPException) as run_denied:
        await logic_api.run_graph("graph-central", _user=principal, db=db)
    assert run_denied.value.status_code == 403

    await db.execute_and_commit("UPDATE authz_node_roles SET central_control=1 WHERE principal_id='alice' AND node_id='graph-central'")
    allowed = await logic_api._logic_run_preflight(db, principal, row)
    assert allowed.allowed is True
    assert allowed.checks[0].reason == "allowed"

    manager = MagicMock()
    manager.execute_graph = AsyncMock(return_value={})
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)
    assert await logic_api.run_graph("graph-central", _user=principal, db=db) == {
        "status": "ok",
        "outputs": {},
        "warnings": [],
    }

    admin = await logic_api._logic_run_preflight(
        db,
        Principal(subject="admin", type="user", is_admin=True),
        row,
    )
    assert admin.allowed is True
    assert admin.checks[0].reason == "admin"


@pytest.mark.asyncio
async def test_logic_control_class_roundtrips_crud_import_export_and_duplicate(db: Database, monkeypatch) -> None:
    manager = MagicMock()
    manager.reload = AsyncMock()
    manager.invalidate_cache = MagicMock()
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    created = await logic_api.create_graph(
        LogicGraphCreate(name="Central", control_class="central_plant"),
        _user="admin",
        db=db,
    )
    assert created.control_class == "central_plant"

    exported = await logic_api.export_graph(created.id, _user="admin", db=db)
    export_payload = json.loads(exported.body)
    assert export_payload["control_class"] == "central_plant"

    duplicate = await logic_api.duplicate_graph(created.id, _user="admin", db=db)
    assert duplicate.control_class == "central_plant"

    replaced = await logic_api.update_graph_full(
        created.id,
        LogicGraphCreate(name="Local", control_class="room_local"),
        _user="admin",
        db=db,
    )
    assert replaced.control_class == "room_local"
    patched = await logic_api.update_graph_partial(
        created.id,
        LogicGraphUpdate(control_class="central_plant"),
        _user="admin",
        db=db,
    )
    assert patched.control_class == "central_plant"

    imported = await logic_api.import_graph(
        LogicGraphImport.model_validate(export_payload),
        _user="admin",
        db=db,
    )
    assert imported.control_class == "central_plant"
    assert (await db.fetchone("SELECT control_class FROM logic_graphs WHERE id=?", (imported.id,)))["control_class"] == "central_plant"


@pytest.mark.asyncio
async def test_current_principal_preflight_endpoint_returns_decisions(db: Database) -> None:
    await _insert_graph(db, "graph-endpoint", [])
    await _grant(db, "logic_graph", "graph-endpoint")

    response = await logic_api.preflight_graph_run(
        "graph-endpoint",
        _user=Principal(subject="admin", type="user", is_admin=True),
        db=db,
    )

    assert response.allowed is True
    assert response.checks[0].target_type == "logic_graph"

    granted_response = await logic_api.preflight_graph_run(
        "graph-endpoint",
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )
    assert granted_response.allowed is True

    listed = await logic_api.list_graphs(
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )
    assert [graph.id for graph in listed] == ["graph-endpoint"]

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.preflight_graph_run(
            "missing",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_graph_preflight_matches_run_rejection(db: Database) -> None:
    await _insert_graph(db, "graph-disabled", [], enabled=False)
    principal = Principal(subject="admin", type="user", is_admin=True)

    preflight = await logic_api.preflight_graph_run(
        "graph-disabled",
        _user=principal,
        db=db,
    )

    state = next(check for check in preflight.checks if check.target_type == "logic_graph_state")
    assert preflight.allowed is False
    assert (state.target_id, state.allowed, state.reason) == ("enabled", False, "graph_disabled")

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(
            "graph-disabled",
            _user=principal,
            db=db,
        )
    assert exc_info.value.status_code == 422


def test_direct_datapoint_grants_preserve_deny_precedence_and_allow_fallback() -> None:
    principal = Principal(subject="alice", type="user", is_admin=False)
    hierarchy_target = AuthzTarget(node_type="hierarchy", node_id="room")
    hierarchy_allow = RoleGrant(
        principal_type="user",
        principal_id="alice",
        node_type="hierarchy",
        node_id="room",
        role="resident",
    )
    direct_allow = RoleGrant(
        principal_type="user",
        principal_id="alice",
        node_type="datapoint",
        node_id="dp-1",
        role="resident",
    )
    direct_deny = RoleGrant(
        principal_type="user",
        principal_id="alice",
        node_type="datapoint",
        node_id="dp-1",
        role="guest",
        effect="deny",
    )
    direct_guest = RoleGrant(
        principal_type="user",
        principal_id="alice",
        node_type="datapoint",
        node_id="dp-1",
        role="guest",
    )

    allowed = logic_api._direct_datapoint_activation_decision(
        principal,
        "dp-1",
        [AuthzTarget(node_type="hierarchy", node_id="other")],
        [direct_allow],
    )
    denied = logic_api._direct_datapoint_activation_decision(
        principal,
        "dp-1",
        [hierarchy_target],
        [hierarchy_allow, direct_deny],
    )
    missing = logic_api._direct_datapoint_activation_decision(
        principal,
        "dp-1",
        [AuthzTarget(node_type="hierarchy", node_id="other")],
        [direct_guest],
    )

    assert (allowed.allowed, allowed.reason) == (True, "allowed")
    assert (denied.allowed, denied.reason) == (False, "explicit_deny")
    assert (missing.allowed, missing.reason) == (False, "missing_allow")


@pytest.mark.asyncio
async def test_denied_run_audits_graph_and_missing_capability(db: Database) -> None:
    await _insert_graph(db, "graph-audit", [_node("sms-node", "notify_sms")])
    await _grant(db, "logic_graph", "graph-audit")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(
            "graph-audit",
            request,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Zugriff verweigert"
    row = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='logic.graph.run.denied'")
    assert row["resource_type"] == "logic_graph"
    assert row["resource_id"] == "graph-audit"
    details = json.loads(row["details_json"])
    assert details["denied_checks"] == [
        {
            "allowed": False,
            "node_ids": ["sms-node"],
            "reason": "missing_allow",
            "target_id": "sms",
            "target_type": "logic_capability",
        }
    ]


@pytest.mark.asyncio
async def test_grant_target_validation_accepts_graph_and_rejects_unknown_capability(db: Database) -> None:
    await _insert_graph(db, "graph-grant", [])
    await authz_api._require_grant_targets(
        db,
        [
            AuthzPrincipalGrant(node_type="logic_graph", node_id="graph-grant", role="resident"),
            AuthzPrincipalGrant(node_type="logic_capability", node_id="http_request", role="resident"),
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await authz_api._require_grant_targets(
            db,
            [AuthzPrincipalGrant(node_type="logic_capability", node_id="shell", role="owner")],
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_delete_graph_removes_central_role_grants(db: Database) -> None:
    await _insert_graph(db, "graph-delete", [])
    await _grant(db, "logic_graph", "graph-delete", role="owner")

    await logic_api.delete_graph("graph-delete", _user="admin", db=db)

    assert await db.fetchone("SELECT 1 FROM logic_graphs WHERE id='graph-delete'") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='logic_graph' AND node_id='graph-delete'") is None
