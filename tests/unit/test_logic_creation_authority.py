from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from obs.api.auth import Principal
from obs.api.v1 import authz as authz_api
from obs.api.v1 import logic as logic_api
from obs.db.database import Database
from obs.logic.capabilities import LOGIC_CREATE_CAPABILITY
from obs.logic.models import FlowData, LogicGraphCreate, LogicGraphImport, LogicNode, NodePosition
from obs.models.authz import (
    AuthzPreviewGrant,
    AuthzPreviewPrincipal,
    AuthzPreviewRequest,
    AuthzPrincipalGrant,
    AuthzPrincipalGrantsReplace,
)


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _request(path: str = "/api/v1/logic/graphs") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [(b"x-request-id", b"logic-create-test")],
            "client": ("127.0.0.1", 12345),
        }
    )


async def _grant_create(
    db: Database,
    *,
    principal_type: str = "user",
    principal_id: str = "alice",
    role: str = "operator",
    effect: str = "allow",
    central_control: bool = False,
) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect, central_control)
           VALUES (?, ?, 'logic_capability', ?, ?, ?, ?)""",
        (principal_type, principal_id, LOGIC_CREATE_CAPABILITY, role, effect, int(central_control)),
    )


async def _insert_source_graph(
    db: Database,
    *,
    graph_id: str = "source",
    enabled: bool = True,
    flow: FlowData | None = None,
    control_class: str = "room_local",
) -> None:
    await db.execute_and_commit(
        """INSERT INTO logic_graphs
               (id, name, description, enabled, flow_data, control_class, created_at, updated_at, created_by)
           VALUES (?, 'Source', 'source description', ?, ?, ?, '2026-07-13', '2026-07-13', 'admin')""",
        (graph_id, int(enabled), (flow or FlowData()).model_dump_json(), control_class),
    )


async def _grant_graph(
    db: Database,
    *,
    graph_id: str = "source",
    principal_type: str = "user",
    principal_id: str = "alice",
    role: str = "resident",
) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES (?, ?, 'logic_graph', ?, ?, 'allow')""",
        (principal_type, principal_id, graph_id, role),
    )


def _principal(subject: str = "alice", *, principal_type: str = "user", is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type=principal_type, is_admin=is_admin)


@pytest.mark.asyncio
async def test_delegated_create_is_disabled_and_atomically_grants_bounded_follow_up(db: Database, monkeypatch) -> None:
    await _grant_create(db)
    manager = AsyncMock()
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    created = await logic_api.create_graph(
        LogicGraphCreate(name="Delegated", enabled=True),
        _request(),
        _user=_principal(),
        db=db,
    )

    assert created.enabled is False
    row = await db.fetchone("SELECT enabled, created_by FROM logic_graphs WHERE id=?", (created.id,))
    assert (row["enabled"], row["created_by"]) == (0, "alice")
    grant = await db.fetchone(
        """SELECT role, effect, central_control FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='alice' AND node_type='logic_graph' AND node_id=?""",
        (created.id,),
    )
    assert (grant["role"], grant["effect"], grant["central_control"]) == ("operator", "allow", 0)
    audit = await db.fetchone("SELECT actor, details_json FROM audit_log_entries WHERE action='logic.graph.created'")
    assert audit["actor"] == "alice"
    assert json.loads(audit["details_json"]) == {
        "control_class": "room_local",
        "creator_grant_role": "operator",
        "delegated": True,
        "enabled_persisted": False,
        "enabled_requested": True,
        "operation": "create",
    }
    preflight = await logic_api.preflight_graph_run(created.id, _user=_principal(), db=db)
    assert preflight.allowed is False
    assert next(check for check in preflight.checks if check.target_type == "logic_graph_state").reason == "graph_disabled"
    manager.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_creator_follow_up_grant_does_not_bypass_side_effect_activation_capabilities(db: Database, monkeypatch) -> None:
    await _grant_create(db)
    manager = AsyncMock()
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)
    flow = FlowData(nodes=[LogicNode(id="sms", type="notify_sms", position=NodePosition(x=0, y=0), data={})])
    created = await logic_api.create_graph(
        LogicGraphCreate(name="Side effect", enabled=True, flow_data=flow),
        _request(),
        _user=_principal(),
        db=db,
    )
    await db.execute_and_commit("UPDATE logic_graphs SET enabled=1 WHERE id=?", (created.id,))

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.run_graph(created.id, _request(f"/api/v1/logic/graphs/{created.id}/run"), _user=_principal(), db=db)

    assert exc_info.value.status_code == 403
    denied = json.loads(
        (await db.fetchone("SELECT details_json FROM audit_log_entries WHERE action='logic.graph.run' AND outcome='denied'"))["details_json"]
    )["denied_checks"]
    assert [(check["target_type"], check["target_id"], check["reason"]) for check in denied] == [("logic_capability", "sms", "missing_allow")]
    manager.execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_uses_same_capability_and_forces_delegated_graph_disabled(db: Database, monkeypatch) -> None:
    await _grant_create(db)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: (_ for _ in ()).throw(RuntimeError("no registry")))
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: (_ for _ in ()).throw(RuntimeError("no manager")))
    body = LogicGraphImport(
        obs_export="logic_graph",
        version=1,
        name="Imported",
        enabled=True,
        flow_data=FlowData(nodes=[LogicNode(id="future", type="future_node", position=NodePosition(x=0, y=0), data={})]),
    )

    imported = await logic_api.import_graph(
        body,
        _request("/api/v1/logic/graphs/import"),
        _user=_principal(),
        db=db,
    )

    assert imported.enabled is False
    assert imported.flow_data.nodes[0].type == "missing_node"
    assert await db.fetchone(
        "SELECT 1 FROM authz_node_roles WHERE node_type='logic_graph' AND node_id=? AND role='operator'",
        (imported.id,),
    )
    audit = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE resource_id=?", (imported.id,))
    assert json.loads(audit["details_json"])["operation"] == "import"


@pytest.mark.asyncio
async def test_delegated_duplicate_is_disabled_remaps_node_ids_and_uses_atomic_creator_grant(db: Database, monkeypatch) -> None:
    source_flow = FlowData(
        nodes=[
            LogicNode(id="source-a", type="const_value", position=NodePosition(x=0, y=0), data={}),
            LogicNode(id="source-b", type="not", position=NodePosition(x=100, y=0), data={}),
        ],
        edges=[logic_api.LogicEdge(id="source-edge", source="source-a", target="source-b")],
    )
    await _insert_source_graph(db, flow=source_flow)
    await _grant_graph(db, role="operator")
    await _grant_create(db)
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: (_ for _ in ()).throw(RuntimeError("no manager")))

    duplicated = await logic_api.duplicate_graph("source", _request("/api/v1/logic/graphs/source/duplicate"), _user=_principal(), db=db)

    assert duplicated.enabled is False
    copied_ids = {node.id for node in duplicated.flow_data.nodes}
    assert len(copied_ids) == 2
    assert copied_ids.isdisjoint({"source-a", "source-b"})
    assert len(duplicated.flow_data.edges) == 1
    copied_edge = duplicated.flow_data.edges[0]
    assert copied_edge.id != "source-edge"
    assert {copied_edge.source, copied_edge.target} == copied_ids
    grant = await db.fetchone(
        "SELECT role FROM authz_node_roles WHERE principal_id='alice' AND node_type='logic_graph' AND node_id=?",
        (duplicated.id,),
    )
    assert grant["role"] == "operator"
    audit = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE resource_id=?", (duplicated.id,))
    assert json.loads(audit["details_json"])["operation"] == "duplicate"


@pytest.mark.asyncio
async def test_duplicate_conceals_unreadable_source_before_creation_capability_check(db: Database) -> None:
    flow = FlowData(nodes=[LogicNode(id="sms", type="notify_sms", position=NodePosition(x=0, y=0), data={})])
    await _insert_source_graph(db, flow=flow)
    await _grant_create(db)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.duplicate_graph("source", _request("/api/v1/logic/graphs/source/duplicate"), _user=_principal(), db=db)

    assert exc_info.value.status_code == 404
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM logic_graphs"))["n"] == 1
    audit = await db.fetchone("SELECT resource_id, outcome FROM audit_log_entries WHERE action='logic.graph.duplicated'")
    assert (audit["resource_id"], audit["outcome"]) == ("source", "denied")


@pytest.mark.asyncio
async def test_duplicate_denies_readable_source_without_create_capability_and_audits(db: Database) -> None:
    await _insert_source_graph(db)
    await _grant_graph(db)

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.duplicate_graph("source", _request("/api/v1/logic/graphs/source/duplicate"), _user=_principal(), db=db)

    assert exc_info.value.status_code == 403
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM logic_graphs"))["n"] == 1
    audit = await db.fetchone("SELECT resource_id, details_json FROM audit_log_entries WHERE action='logic.graph.duplicated' AND outcome='denied'")
    assert audit["resource_id"] == "source"
    assert json.loads(audit["details_json"])["operation"] == "duplicate"


@pytest.mark.asyncio
async def test_duplicate_api_key_is_denied_even_with_source_and_create_grants(db: Database) -> None:
    await _insert_source_graph(db)
    await _grant_graph(db, principal_type="api_key", principal_id="key-1", role="owner")
    await _grant_create(db, principal_type="api_key", principal_id="key-1", role="owner")
    principal = _principal("api_key:key-1", principal_type="api_key")

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.duplicate_graph("source", _request("/api/v1/logic/graphs/source/duplicate"), _user=principal, db=db)

    assert exc_info.value.status_code == 403
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM logic_graphs"))["n"] == 1
    audit = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE action='logic.graph.duplicated' AND outcome='denied'")
    assert json.loads(audit["details_json"])["reason"] == "principal_type_not_allowed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("principal", "expected_reason"),
    [
        (_principal(), "missing_allow"),
        (_principal("api_key:key-1", principal_type="api_key"), "principal_type_not_allowed"),
    ],
)
async def test_denied_create_is_audited_without_partial_resource(
    db: Database,
    principal: Principal,
    expected_reason: str,
) -> None:
    if principal.type == "api_key":
        await _grant_create(db, principal_type="api_key", principal_id="key-1", role="owner")

    with pytest.raises(HTTPException) as exc_info:
        await logic_api.create_graph(LogicGraphCreate(name="Denied"), _request(), _user=principal, db=db)

    assert exc_info.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM logic_graphs") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='logic_graph'") is None
    audit = await db.fetchone("SELECT resource_type, resource_id, details_json FROM audit_log_entries")
    assert (audit["resource_type"], audit["resource_id"]) == ("logic_graph", None)
    assert json.loads(audit["details_json"])["reason"] == expected_reason


@pytest.mark.asyncio
async def test_legacy_direct_api_key_subject_is_also_denied(db: Database) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await logic_api.create_graph(LogicGraphCreate(name="Denied"), _request(), _user="api_key:key-1", db=db)

    assert exc_info.value.status_code == 403
    audit = await db.fetchone("SELECT actor, details_json FROM audit_log_entries")
    assert audit["actor"] == "api_key:key-1"
    assert json.loads(audit["details_json"])["reason"] == "principal_type_not_allowed"


@pytest.mark.asyncio
async def test_explicit_deny_and_central_control_remain_fail_closed(db: Database) -> None:
    await _grant_create(db, role="owner", effect="deny")
    with pytest.raises(HTTPException) as explicit_deny:
        await logic_api.create_graph(LogicGraphCreate(name="Denied"), _request(), _user=_principal(), db=db)
    assert explicit_deny.value.status_code == 403
    details = json.loads((await db.fetchone("SELECT details_json FROM audit_log_entries"))["details_json"])
    assert details["reason"] == "explicit_deny"

    await db.execute_and_commit("DELETE FROM authz_node_roles")
    await db.execute_and_commit("DELETE FROM audit_log_entries")
    await _grant_create(db)
    with pytest.raises(HTTPException) as central_denied:
        await logic_api.create_graph(
            LogicGraphCreate(name="Central", control_class="central_plant"),
            _request(),
            _user=_principal(),
            db=db,
        )
    assert central_denied.value.status_code == 403
    details = json.loads((await db.fetchone("SELECT details_json FROM audit_log_entries"))["details_json"])
    assert details["reason"] == "central_control_required"


@pytest.mark.asyncio
async def test_central_create_propagates_explicit_control_scope_to_follow_up_grant(db: Database, monkeypatch) -> None:
    await _grant_create(db, central_control=True)
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: (_ for _ in ()).throw(RuntimeError("no manager")))

    created = await logic_api.create_graph(
        LogicGraphCreate(name="Central", control_class="central_plant"),
        _request(),
        _user=_principal(),
        db=db,
    )

    assert created.enabled is False
    grant = await db.fetchone("SELECT role, central_control FROM authz_node_roles WHERE node_type='logic_graph'")
    assert (grant["role"], grant["central_control"]) == ("operator", 1)


@pytest.mark.asyncio
async def test_creation_audit_failure_rolls_back_graph_and_creator_grant(db: Database, monkeypatch) -> None:
    await _grant_create(db)

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await logic_api.create_graph(LogicGraphCreate(name="Rollback"), _request(), _user=_principal(), db=db)

    assert await db.fetchone("SELECT 1 FROM logic_graphs") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='logic_graph'") is None
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_admin_create_preserves_enabled_state_without_creator_grant(db: Database, monkeypatch) -> None:
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: (_ for _ in ()).throw(RuntimeError("no manager")))

    created = await logic_api.create_graph(
        LogicGraphCreate(name="Admin", enabled=True),
        _request(),
        _user=_principal("admin", is_admin=True),
        db=db,
    )

    assert created.enabled is True
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='logic_graph'") is None
    details = json.loads((await db.fetchone("SELECT details_json FROM audit_log_entries"))["details_json"])
    assert (details["delegated"], details["enabled_persisted"]) == (False, True)


@pytest.mark.asyncio
async def test_grant_api_rejects_user_only_create_capability_for_api_keys(db: Database) -> None:
    key_id = str(uuid.uuid4())
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', ?, 'alice', '2026-07-13')",
        (key_id, f"hash-{key_id}"),
    )
    body = AuthzPrincipalGrantsReplace(grants=[AuthzPrincipalGrant(node_type="logic_capability", node_id=LOGIC_CREATE_CAPABILITY, role="owner")])

    with pytest.raises(HTTPException) as exc_info:
        await authz_api.replace_principal_grants(
            "api_key",
            key_id,
            body,
            _request("/api/v1/authz/principals/api_key/key/grants"),
            Response(),
            if_match=authz_api._grants_etag([]),
            db=db,
            _admin="admin",
        )

    assert exc_info.value.status_code == 422
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE principal_type='api_key'") is None


def test_preview_rejects_user_only_create_capability_for_api_keys() -> None:
    body = AuthzPreviewRequest(
        principal=AuthzPreviewPrincipal(principal_type="api_key", principal_id="key-1"),
        targets=[],
        draft_grants=[
            AuthzPreviewGrant(
                principal_type="api_key",
                principal_id="key-1",
                node_type="logic_capability",
                node_id=LOGIC_CREATE_CAPABILITY,
                role="owner",
            )
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        authz_api._validate_draft_grants(body)

    assert exc_info.value.status_code == 422
