from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from obs.api.auth import Principal
from obs.api.authz import AuthzTarget, RoleGrant
from obs.api.v1 import authz as authz_api
from obs.api.v1 import logic as logic_api
from obs.db.database import Database
from obs.logic.capabilities import LOGIC_CAPABILITIES
from obs.logic.models import NodeTypeDef
from obs.logic.node_types import NODE_TYPE_REGISTRY
from obs.models.authz import AuthzPrincipalGrant


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_graph(db: Database, graph_id: str, nodes: list[dict]) -> dict:
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """
        INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
        VALUES (?, 'Capabilities', '', 1, ?, ?, ?)
        """,
        (graph_id, json.dumps({"nodes": nodes, "edges": []}), now, now),
    )
    return await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))


async def _grant(
    db: Database,
    node_type: str,
    node_id: str,
    *,
    role: str = "resident",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', ?, ?, ?, ?)
        """,
        (node_type, node_id, role, effect),
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
            has_external_side_effect=True,
        ),
    )
    row = await _insert_graph(
        db,
        "graph-3",
        [_node("future", "future_side_effect"), _node("unknown", "unknown_node")],
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
            has_external_side_effect=True,
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
    assert {check.reason for check in preflight.checks} == {"admin"}


@pytest.mark.asyncio
async def test_current_principal_preflight_endpoint_returns_decisions(db: Database) -> None:
    await _insert_graph(db, "graph-endpoint", [])
    await _grant(db, "logic_graph", "graph-endpoint")

    response = await logic_api.preflight_graph_run(
        "graph-endpoint",
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert response.allowed is True
    assert response.checks[0].target_type == "logic_graph"

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.preflight_graph_run(
            "missing",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )
    assert exc_info.value.status_code == 404


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
    assert exc_info.value.detail["graph_id"] == "graph-audit"
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
