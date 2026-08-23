"""Logic Engine API

GET    /api/v1/logic/node-types               list all node type definitions
GET    /api/v1/logic/graphs                   list all logic graphs
POST   /api/v1/logic/graphs                   create a new graph
POST   /api/v1/logic/graphs/import            import graph from JSON
POST   /api/v1/logic/graphs/validate          validate flow topology
GET    /api/v1/logic/graphs/{id}              get graph (with flow_data)
PUT    /api/v1/logic/graphs/{id}              full update (save canvas)
PATCH  /api/v1/logic/graphs/{id}             partial update (name/enabled)
DELETE /api/v1/logic/graphs/{id}              delete graph
POST   /api/v1/logic/graphs/{id}/run          manually trigger execution
POST   /api/v1/logic/graphs/{id}/duplicate    duplicate graph with new node IDs
GET    /api/v1/logic/graphs/{id}/export       export graph as JSON download
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context
from obs.api.auth import Principal, get_current_principal, get_current_user
from obs.api.authz import AuthzAction, AuthzDecision, AuthzTarget, RoleGrant, authorize
from obs.api.authz_service import filter_authorized_datapoints, load_role_grants, resolve_datapoint_targets
from obs.api.v1.application_audit import audit_application_contract, mark_contract_audited, write_application_success
from obs.db.database import Database, get_db
from obs.logic.capabilities import LOGIC_CREATE_CAPABILITY
from obs.logic.graph_analysis import topology_warnings
from obs.logic.manager import _migrate_legacy_api_client_field_names, _normalise_api_client_variables
from obs.logic.models import (
    FlowData,
    LogicEdge,
    LogicGraphCreate,
    LogicGraphImport,
    LogicGraphOut,
    LogicGraphRun,
    LogicGraphUpdate,
    LogicNode,
    LogicRunPreflight,
    LogicRunPreflightCheck,
    LogicUsageOut,
    NodeTypeDef,
)
from obs.logic.registry import get_node_type, list_node_types
from obs.logic.validation import validate_timer_durations

logger = logging.getLogger(__name__)

router = APIRouter(tags=["logic"])


def _validate_timer_durations(flow_data: FlowData) -> None:
    """Translate shared persistence validation to an API error."""
    try:
        validate_timer_durations(flow_data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _without_positions(raw: dict) -> dict:
    """Strip node positions, user-defined block names and purely visual comment
    nodes for layout-only save detection — none of them affects execution
    semantics.

    ``data.label`` is the block name a user typed on the sheet (issue #1157).
    Treating a rename as an execution change would re-initialize the graph and
    reset persisted block state (memory, counters, statistics) over a purely
    cosmetic edit. The renamed flow still reaches the runtime: the layout-only
    path hands it to ``LogicManager.update_cached_graph``, so the one place
    that does read the name — the ``node_label`` of an archived message — sees
    the new one on the next tick.
    """
    raw = dict(raw)
    raw["nodes"] = [
        {k: v for k, v in node.items() if k != "position"} | {"data": {k: v for k, v in (node.get("data") or {}).items() if k != "label"}}
        for node in raw.get("nodes", [])
        if node.get("type") != "comment"
    ]
    return raw


def _normalized_without_positions(raw: dict) -> dict:
    """Normalize a flow through FlowData, then strip positions.

    Stored graphs (e.g. from older exports) may omit optional fields that a
    freshly parsed request body carries explicitly as null — comparing raw
    dicts would misclassify a move-only save as an execution change.
    """
    flow_data = FlowData.model_validate(raw)
    _migrate_legacy_api_client_field_names(flow_data)
    return _without_positions(json.loads(flow_data.model_dump_json()))


def _normalize_missing_node_placeholders(flow_data: FlowData) -> None:
    """Canonicalize ``missing_node`` placeholders before they reach a client.

    ``data.label`` now means "user-defined block name" (issue #1157), so the two
    older uses of that key on a placeholder have to be resolved here — the
    properties panel offers ``label`` as an editable name for every block type
    and cannot know a placeholder ever meant something else by it:

    * Imports before #1157 wrote a generated German type marker
      (``[Fehlend: <type>]``). That is not a name the user typed, so it is
      dropped.
    * A placeholder carrying its missing type in ``label`` alone has that type
      promoted to ``original_type``, where renaming the block cannot overwrite
      it. The built-in importer has always written both keys, but a hand-edited
      or third-party export can still reach this route.

    Doing it at the read boundary keeps the block card, the properties panel and
    re-imported old exports consistent, and the canonical shape is written back
    the next time the sheet is saved.
    """
    for node in flow_data.nodes:
        if node.type != "missing_node":
            continue
        original_type = node.data.get("original_type")
        label = node.data.get("label")
        if not original_type:
            if isinstance(label, str) and label.strip():
                node.data["original_type"] = label.strip()
                node.data.pop("label", None)
            continue
        if label == f"[Fehlend: {original_type}]":
            node.data.pop("label", None)


def _row_to_out(row: dict) -> LogicGraphOut:
    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    flow_data = FlowData.model_validate(raw)
    _migrate_legacy_api_client_field_names(flow_data)
    _normalize_missing_node_placeholders(flow_data)
    return LogicGraphOut(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        enabled=bool(row["enabled"]),
        flow_data=flow_data,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        control_class=_row_control_class(row),
    )


def _row_control_class(row: dict) -> str:
    try:
        return row["control_class"]
    except (IndexError, KeyError):
        return "room_local"


def _principal_from_dependency(value: Principal | str) -> Principal:
    if isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _principal_from_mutation_dependency(value: Principal | str | object) -> Principal:
    if isinstance(value, Principal):
        return value
    if isinstance(value, str) and value.startswith("api_key:"):
        return Principal(subject=value, type="api_key", is_admin=False)
    # Direct callers historically pass admin dependency values as strings.
    # Runtime requests now receive a Principal from get_current_principal.
    subject = value if isinstance(value, str) else "admin"
    return Principal(subject=subject, type="user", is_admin=True)


async def _require_logic_graph_creation(
    db: Database,
    principal: Principal,
    request: Request | None,
    *,
    operation: str,
    control_class: str,
    resource_id: str | None = None,
) -> bool:
    """Return whether creation is delegated after enforcing its closed capability."""
    if principal.type == "user" and principal.is_admin:
        return False

    if principal.type != "user":
        decision = AuthzDecision(False, "principal_type_not_allowed")
    else:
        grants = await load_role_grants(db, principal, node_type="logic_capability")
        decision = authorize(
            principal=principal,
            action=AuthzAction.GENERATE,
            targets=[
                AuthzTarget(
                    node_type="logic_capability",
                    node_id=LOGIC_CREATE_CAPABILITY,
                    control_class=control_class,
                )
            ],
            grants=grants,
        )

    if not decision.allowed:
        writer = AuditLogWriter(
            db=db,
            context=build_audit_context(request=request, current_user=principal),
        )
        path = {
            "create": "/api/v1/logic/graphs",
            "import": "/api/v1/logic/graphs/import",
            "duplicate": "/api/v1/logic/graphs/{graph_id}/duplicate",
        }[operation]
        await writer.write_contract(
            "POST",
            path,
            resource_id=resource_id,
            details={"control_class": control_class, "operation": operation, "reason": decision.reason},
            outcome=AuditOutcome.DENIED,
        )
        raise mark_contract_audited(HTTPException(status.HTTP_403_FORBIDDEN, "Zugriff verweigert"))

    return True


async def _persist_created_graph(
    db: Database,
    principal: Principal,
    request: Request | None,
    *,
    name: str,
    description: str,
    enabled: bool,
    flow: FlowData,
    control_class: str,
    delegated: bool,
    audit_path: str,
) -> dict:
    now = datetime.now(UTC).isoformat()
    graph_id = str(uuid.uuid4())
    persisted_enabled = enabled if not delegated else False
    async with db.transaction():
        await db.execute(
            """INSERT INTO logic_graphs (id, name, description, enabled, flow_data, control_class, created_at, updated_at, created_by)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                graph_id,
                name,
                description,
                int(persisted_enabled),
                flow.model_dump_json(),
                control_class,
                now,
                now,
                principal.subject,
            ),
        )
        if delegated:
            await db.execute(
                """INSERT INTO authz_node_roles
                       (principal_type, principal_id, node_type, node_id, role, effect, central_control)
                   VALUES ('user', ?, 'logic_graph', ?, 'operator', 'allow', ?)""",
                (principal.subject, graph_id, int(control_class == "central_plant")),
            )
        details = {
            "control_class": control_class,
            "creator_grant_role": "operator" if delegated else None,
            "delegated": delegated,
            "enabled_persisted": persisted_enabled,
            "enabled_requested": enabled,
            "operation": audit_path.rsplit("/", 1)[-1] if audit_path.endswith(("/import", "/duplicate")) else "create",
        }
        if audit_path == "/api/v1/logic/graphs":
            await write_application_success(
                db,
                request,
                principal,
                "POST",
                "/api/v1/logic/graphs",
                resource_id=graph_id,
                details=details,
                commit=False,
            )
        elif audit_path == "/api/v1/logic/graphs/import":
            await write_application_success(
                db,
                request,
                principal,
                "POST",
                "/api/v1/logic/graphs/import",
                resource_id=graph_id,
                details=details,
                commit=False,
            )
        else:
            await write_application_success(
                db,
                request,
                principal,
                "POST",
                "/api/v1/logic/graphs/{graph_id}/duplicate",
                resource_id=graph_id,
                details=details,
                commit=False,
            )
        row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
        assert row is not None
    return row


def _flow_from_row(row: dict) -> FlowData:
    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    return FlowData.model_validate(raw)


def _node_datapoint_ids(node: LogicNode) -> list[object]:
    if node.type in {"datapoint_read", "datapoint_write"}:
        return [node.data.get("datapoint_id")]
    if node.type == "api_client":
        return [variable["datapoint_id"] for variable in _normalise_api_client_variables(node.data.get("variables")).values()]
    if node.type == "value_sequence":
        steps = node.data.get("steps") or []
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except json.JSONDecodeError:
                return []
        if isinstance(steps, list):
            return [step.get("datapoint_id") for step in steps if isinstance(step, dict)]
    return []


def _logic_datapoint_ids(flow: FlowData) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for node in flow.nodes:
        for dp_id in _node_datapoint_ids(node):
            if not isinstance(dp_id, str) or not dp_id or dp_id in seen:
                continue
            seen.add(dp_id)
            ids.append(dp_id)
    return ids


async def _authorized_logic_datapoint_ids(
    db: Database,
    principal: Principal,
    row: dict,
    *,
    action: AuthzAction,
) -> tuple[list[str], list[str]]:
    all_ids = _logic_datapoint_ids(_flow_from_row(row))
    if principal.type == "user" and principal.is_admin:
        return all_ids, all_ids
    if not all_ids:
        return all_ids, []
    allowed_ids = await filter_authorized_datapoints(db, principal, all_ids, action=action)
    return all_ids, allowed_ids


def _flow_requires_graph_grant(flow: FlowData) -> bool:
    for node in flow.nodes:
        node_type = get_node_type(node.type)
        if node_type is None or node_type.has_external_side_effect is not False:
            return True
    return False


async def _can_read_logic_graph(db: Database, principal: Principal, row: dict) -> bool:
    if principal.type == "user" and principal.is_admin:
        return True
    graph_grants = await load_role_grants(db, principal, node_type="logic_graph")
    graph_decision = authorize(
        principal=principal,
        action=AuthzAction.READ,
        targets=[AuthzTarget(node_type="logic_graph", node_id=row["id"])],
        grants=graph_grants,
    )
    if graph_decision.reason == "explicit_deny":
        return False
    if graph_decision.allowed:
        return True
    if _flow_requires_graph_grant(_flow_from_row(row)):
        return False
    all_ids, allowed_ids = await _authorized_logic_datapoint_ids(db, principal, row, action=AuthzAction.READ)
    return bool(all_ids) and len(allowed_ids) == len(all_ids)


async def _require_logic_graph_read(db: Database, principal: Principal, row: dict) -> None:
    if not await _can_read_logic_graph(db, principal, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")


async def _require_logic_graph_generate(
    db: Database,
    principal: Principal,
    row: dict,
    flow: FlowData,
    *,
    control_class: str,
) -> None:
    """Authorize an existing graph and every datapoint in the proposed flow."""
    await _require_logic_graph_read(db, principal, row)
    if principal.type == "user" and principal.is_admin:
        return
    grants = await load_role_grants(db, principal)
    current_control_class = row["control_class"] if "control_class" in row.keys() else "room_local"  # noqa: SIM118 -- sqlite Row membership checks values
    graph_targets = [
        AuthzTarget(
            node_type="logic_graph",
            node_id=row["id"],
            control_class=current_control_class,
        )
    ]
    if control_class != current_control_class:
        graph_targets.append(
            AuthzTarget(
                node_type="logic_graph",
                node_id=row["id"],
                control_class=control_class,
            )
        )
    graph_decision = authorize(
        principal=principal,
        action=AuthzAction.GENERATE,
        targets=graph_targets,
        grants=grants,
    )
    datapoint_ids = list(dict.fromkeys([*_logic_datapoint_ids(_flow_from_row(row)), *_logic_datapoint_ids(flow)]))
    allowed_ids = set(
        await filter_authorized_datapoints(
            db,
            principal,
            datapoint_ids,
            action=AuthzAction.GENERATE,
            grants=grants,
        )
    )
    if not graph_decision.allowed or allowed_ids != set(datapoint_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Zugriff verweigert")


def _datapoint_node_ids(flow: FlowData) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in flow.nodes:
        for candidate_id in _node_datapoint_ids(node):
            if isinstance(candidate_id, str) and candidate_id:
                result.setdefault(candidate_id, []).append(node.id)
    return result


def _adapter_instance_node_ids(flow: FlowData) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in flow.nodes:
        if node.type != "notify_message":
            continue
        instance_id = node.data.get("adapter_instance_id")
        if isinstance(instance_id, str) and instance_id:
            result.setdefault(instance_id, []).append(node.id)
    return result


def _direct_datapoint_activation_decision(
    principal: Principal,
    datapoint_id: str,
    targets: list[AuthzTarget],
    grants: list[RoleGrant],
) -> AuthzDecision:
    decision = authorize(principal=principal, action=AuthzAction.ACTIVATE, targets=targets, grants=grants)
    direct_grants = [grant for grant in grants if grant.node_type == "datapoint" and grant.node_id == datapoint_id]
    if not direct_grants:
        return decision
    control_class = targets[0].control_class if targets else "room_local"
    direct_decision = authorize(
        principal=principal,
        action=AuthzAction.ACTIVATE,
        targets=[AuthzTarget(node_type="datapoint", node_id=datapoint_id, control_class=control_class)],
        grants=grants,
    )
    if decision.reason == "explicit_deny" or direct_decision.reason == "explicit_deny":
        return AuthzDecision(False, "explicit_deny")
    if decision.allowed or direct_decision.allowed:
        return AuthzDecision(True, "allowed")
    return decision


async def _logic_run_preflight(db: Database, principal: Principal, row: dict) -> LogicRunPreflight:
    flow = _flow_from_row(row)
    grants = [] if principal.type == "user" and principal.is_admin else await load_role_grants(db, principal)
    checks: list[LogicRunPreflightCheck] = []

    graph_decision = authorize(
        principal=principal,
        action=AuthzAction.ACTIVATE,
        targets=[AuthzTarget(node_type="logic_graph", node_id=row["id"], control_class=_row_control_class(row))],
        grants=grants,
    )
    checks.append(
        LogicRunPreflightCheck(
            target_type="logic_graph",
            target_id=row["id"],
            allowed=graph_decision.allowed,
            reason=graph_decision.reason,
        )
    )
    checks.append(
        LogicRunPreflightCheck(
            target_type="logic_graph_state",
            target_id="enabled",
            allowed=bool(row["enabled"]),
            reason="enabled" if bool(row["enabled"]) else "graph_disabled",
        )
    )

    node_ids_by_capability: dict[str, list[str]] = {}
    for node in flow.nodes:
        node_type = get_node_type(node.type)
        if node_type is None or node_type.has_external_side_effect is None:
            checks.append(
                LogicRunPreflightCheck(
                    target_type="logic_capability",
                    target_id=node.type,
                    node_ids=[node.id],
                    allowed=principal.type == "user" and principal.is_admin,
                    reason="admin" if principal.type == "user" and principal.is_admin else "undeclared_capability",
                )
            )
        elif node_type.has_external_side_effect:
            if not node_type.required_capability:
                checks.append(
                    LogicRunPreflightCheck(
                        target_type="logic_capability",
                        target_id=node.type,
                        node_ids=[node.id],
                        allowed=principal.type == "user" and principal.is_admin,
                        reason="admin" if principal.type == "user" and principal.is_admin else "undeclared_capability",
                    )
                )
            else:
                node_ids_by_capability.setdefault(node_type.required_capability, []).append(node.id)

    for capability, node_ids in sorted(node_ids_by_capability.items()):
        decision = authorize(
            principal=principal,
            action=AuthzAction.ACTIVATE,
            targets=[AuthzTarget(node_type="logic_capability", node_id=capability)],
            grants=grants,
        )
        checks.append(
            LogicRunPreflightCheck(
                target_type="logic_capability",
                target_id=capability,
                node_ids=node_ids,
                allowed=decision.allowed,
                reason=decision.reason,
            )
        )

    for instance_id, node_ids in _adapter_instance_node_ids(flow).items():
        decision = authorize(
            principal=principal,
            action=AuthzAction.ACTIVATE,
            targets=[AuthzTarget(node_type="adapter_instance", node_id=instance_id)],
            grants=grants,
        )
        checks.append(
            LogicRunPreflightCheck(
                target_type="adapter_instance",
                target_id=instance_id,
                node_ids=node_ids,
                allowed=decision.allowed,
                reason=decision.reason,
            )
        )

    datapoint_nodes = _datapoint_node_ids(flow)
    targets_by_datapoint = await resolve_datapoint_targets(db, datapoint_nodes)
    for datapoint_id, node_ids in datapoint_nodes.items():
        targets = targets_by_datapoint.get(datapoint_id, [])
        decision = _direct_datapoint_activation_decision(principal, datapoint_id, targets, grants)
        checks.append(
            LogicRunPreflightCheck(
                target_type="datapoint",
                target_id=datapoint_id,
                node_ids=node_ids,
                allowed=decision.allowed,
                reason=decision.reason,
            )
        )

    return LogicRunPreflight(graph_id=row["id"], allowed=all(check.allowed for check in checks), checks=checks)


def _logic_run_warnings(outputs: dict) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        diagnostic = node_out.get("__diagnostic__")
        if not isinstance(diagnostic, str) or not diagnostic.startswith("graph_cycle"):
            continue
        warnings.append(
            {
                "node_id": str(node_id),
                "code": diagnostic,
                "message": str(node_out.get("__error__") or "Logic graph cycle detected"),
            },
        )
    return warnings


@router.get("/node-types", response_model=list[NodeTypeDef])
async def get_node_types(_user: str = Depends(get_current_user)) -> list[NodeTypeDef]:
    return list_node_types()


@router.post("/graphs/validate")
async def validate_graph(
    body: FlowData,
    _user: str = Depends(get_current_user),
) -> dict:
    return {"status": "ok", "warnings": topology_warnings(body)}


@router.get("/graphs", response_model=list[LogicGraphOut])
async def list_graphs(
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> list[LogicGraphOut]:
    principal = _principal_from_dependency(_user)
    rows = await db.fetchall("SELECT * FROM logic_graphs ORDER BY name")
    readable_rows = [row for row in rows if await _can_read_logic_graph(db, principal, row)]
    return [_row_to_out(r) for r in readable_rows]


@router.post("/graphs", response_model=LogicGraphOut, status_code=status.HTTP_201_CREATED)
@audit_application_contract("POST", "/api/v1/logic/graphs", principal_param="_user")
async def create_graph(
    body: LogicGraphCreate,
    request: Request = None,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    _validate_timer_durations(body.flow_data)
    principal = _principal_from_mutation_dependency(_user)
    delegated = await _require_logic_graph_creation(
        db,
        principal,
        request,
        operation="create",
        control_class=body.control_class,
    )
    row = await _persist_created_graph(
        db,
        principal,
        request,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        flow=body.flow_data,
        control_class=body.control_class,
        delegated=delegated,
        audit_path="/api/v1/logic/graphs",
    )
    # Load into executor cache so the graph is immediately runnable
    try:
        from obs.logic.manager import get_logic_manager

        manager = get_logic_manager()
        await manager.reload()
        await manager.initialize_graph(row["id"])
    except Exception:
        logger.exception("Failed to reload logic manager after creating graph %s", row["id"])
    return _row_to_out(row)


@router.get("/graphs/{graph_id}", response_model=LogicGraphOut)
async def get_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)
    return _row_to_out(row)


@router.put("/graphs/{graph_id}", response_model=LogicGraphOut)
@audit_application_contract("PUT", "/api/v1/logic/graphs/{graph_id}", principal_param="_user", resource_param="graph_id")
async def update_graph_full(
    graph_id: str,
    body: LogicGraphCreate,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(UTC).isoformat()
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    control_class = (
        body.control_class if "control_class" in body.model_fields_set else (row["control_class"] if "control_class" in row.keys() else "room_local")  # noqa: SIM118 -- sqlite Row membership checks values
    )
    await _require_logic_graph_generate(
        db,
        _principal_from_mutation_dependency(_user),
        row,
        body.flow_data,
        control_class=control_class,
    )
    _validate_timer_durations(body.flow_data)
    try:
        layout_only = bool(row["enabled"]) == body.enabled and _normalized_without_positions(
            json.loads(row["flow_data"] or "{}")
        ) == _normalized_without_positions(json.loads(body.flow_data.model_dump_json()))
    except (TypeError, ValueError):
        layout_only = False

    principal = _principal_from_mutation_dependency(_user)
    async with db.transaction():
        await db.execute(
            """UPDATE logic_graphs
               SET name=?, description=?, enabled=?, flow_data=?, control_class=?, updated_at=?
               WHERE id=?""",
            (
                body.name,
                body.description,
                int(body.enabled),
                body.flow_data.model_dump_json(),
                control_class,
                now,
                graph_id,
            ),
        )
        persisted_row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
        if persisted_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
        await write_application_success(db, None, principal, "PUT", "/api/v1/logic/graphs/{graph_id}", resource_id=graph_id, commit=False)
    # Invalidate executor cache only when execution semantics changed.
    try:
        from obs.logic.manager import get_logic_manager

        manager = get_logic_manager()
        if layout_only:
            manager.update_cached_graph(graph_id, body.name, body.enabled, body.flow_data)
        else:
            await manager.reinitialize_graph(graph_id)
    except Exception:
        logger.exception("Failed to refresh logic manager cache after updating graph %s", graph_id)
    return _row_to_out(persisted_row)


@router.patch("/graphs/{graph_id}", response_model=LogicGraphOut)
@audit_application_contract("PATCH", "/api/v1/logic/graphs/{graph_id}", principal_param="_user", resource_param="graph_id")
async def update_graph_partial(
    graph_id: str,
    body: LogicGraphUpdate,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(UTC).isoformat()
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    proposed_flow = body.flow_data if body.flow_data is not None else _flow_from_row(row)
    control_class = body.control_class or (
        row["control_class"] if "control_class" in row.keys() else "room_local"  # noqa: SIM118 -- sqlite Row membership checks values
    )
    await _require_logic_graph_generate(
        db,
        _principal_from_mutation_dependency(_user),
        row,
        proposed_flow,
        control_class=control_class,
    )
    name = body.name if body.name is not None else row["name"]
    description = body.description if body.description is not None else row["description"]
    enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    control_class = body.control_class if body.control_class is not None else _row_control_class(row)
    if body.flow_data is not None:
        _validate_timer_durations(body.flow_data)
        flow_json = body.flow_data.model_dump_json()
    else:
        flow_json = row["flow_data"]
        if body.enabled is True:
            _validate_timer_durations(FlowData.model_validate(json.loads(flow_json) if flow_json else {}))

    principal = _principal_from_mutation_dependency(_user)
    async with db.transaction():
        await db.execute(
            """UPDATE logic_graphs
               SET name=?, description=?, enabled=?, flow_data=?, control_class=?, updated_at=?
               WHERE id=?""",
            (name, description, int(enabled), flow_json, control_class, now, graph_id),
        )
        persisted_row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
        if persisted_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
        await write_application_success(db, None, principal, "PATCH", "/api/v1/logic/graphs/{graph_id}", resource_id=graph_id, commit=False)

    # A title/description change does not alter execution.  Keeping the cache
    # intact preserves in-flight value sequences; flow or enabled changes need
    # the normal reload and cancellation semantics.
    # A PATCH repeating the stored enabled value without flow_data is a
    # no-op for execution semantics — it must not cancel/reload the running
    # sheet or re-run the initialization writes.
    enabled_changed = body.enabled is not None and body.enabled != bool(row["enabled"])
    if body.flow_data is not None or enabled_changed:
        # Position-only canvas saves keep execution semantics — mirror the
        # PUT path: refresh the cache without re-initializing the sheet.
        try:
            layout_only = (
                body.flow_data is not None
                and (body.enabled is None or body.enabled == bool(row["enabled"]))
                and _normalized_without_positions(json.loads(row["flow_data"] or "{}")) == _normalized_without_positions(json.loads(flow_json))
            )
        except (TypeError, ValueError):
            layout_only = False
        try:
            from obs.logic.manager import get_logic_manager

            manager = get_logic_manager()
            if layout_only:
                manager.update_cached_graph(graph_id, name, enabled, body.flow_data)
            else:
                await manager.reinitialize_graph(graph_id)
        except Exception:
            logger.exception("Failed to refresh logic manager cache after updating graph %s", graph_id)
    else:
        try:
            from obs.logic.manager import get_logic_manager

            get_logic_manager().update_cached_graph_name(graph_id, name)
        except Exception:
            logger.exception("Failed to update cached graph name for graph %s", graph_id)
    return _row_to_out(persisted_row)


@router.delete("/graphs/{graph_id}", status_code=status.HTTP_204_NO_CONTENT)
@audit_application_contract("DELETE", "/api/v1/logic/graphs/{graph_id}", principal_param="_user", resource_param="graph_id")
async def delete_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    principal = _principal_from_mutation_dependency(_user)
    await _require_logic_graph_generate(
        db,
        principal,
        row,
        _flow_from_row(row),
        control_class=row["control_class"] if "control_class" in row.keys() else "room_local",  # noqa: SIM118 -- sqlite Row membership checks values
    )
    async with db.transaction():
        await db.execute(
            "DELETE FROM authz_node_roles WHERE node_type='logic_graph' AND node_id=?",
            (graph_id,),
        )
        await db.execute("DELETE FROM logic_graphs WHERE id=?", (graph_id,))
        await write_application_success(db, None, principal, "DELETE", "/api/v1/logic/graphs/{graph_id}", resource_id=graph_id, commit=False)
    try:
        from obs.logic.manager import get_logic_manager

        get_logic_manager().remove_graph(graph_id)
    except Exception:
        logger.exception("Failed to invalidate logic manager cache after deleting graph %s", graph_id)


@router.post("/graphs/import", response_model=LogicGraphOut, status_code=status.HTTP_201_CREATED)
@audit_application_contract("POST", "/api/v1/logic/graphs/import", principal_param="_user")
async def import_graph(
    body: LogicGraphImport,
    request: Request = None,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    if body.obs_export != "logic_graph":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ungültiges Export-Format (erwartet 'logic_graph')",
        )

    _validate_timer_durations(body.flow_data)
    principal = _principal_from_mutation_dependency(_user)
    delegated = await _require_logic_graph_creation(
        db,
        principal,
        request,
        operation="import",
        control_class=body.control_class,
    )

    known_types = {nt.type for nt in list_node_types()}

    # Unbekannte Node-Typen → missing_node Platzhalter
    # Bekannte Nodes: datapoint_name aus aktuellem Objektsystem holen
    try:
        from obs.core.registry import get_registry

        _registry = get_registry()
    except RuntimeError:
        _registry = None

    processed_nodes: list[LogicNode] = []
    for node in body.flow_data.nodes:
        if node.type not in known_types and node.type != "missing_node":
            placeholder_data: dict[str, Any] = {"original_type": node.type}
            # Keep the user-defined block name (issue #1157) so a renamed block
            # stays identifiable after its type disappeared; the frontend
            # renders the missing type from `original_type` and localizes the
            # placeholder heading itself.
            if custom_label := str(node.data.get("label") or "").strip():
                placeholder_data["label"] = custom_label
            processed_nodes.append(
                LogicNode(
                    id=node.id,
                    type="missing_node",
                    position=node.position,
                    data=placeholder_data,
                ),
            )
        else:
            if _registry is not None and "datapoint_id" in node.data:
                try:
                    dp = _registry.get(uuid.UUID(node.data["datapoint_id"]))
                    if dp is not None:
                        node.data["datapoint_name"] = dp.name
                except (ValueError, TypeError, AttributeError):
                    pass
            if _registry is not None and node.type == "value_sequence":
                steps = node.data.get("steps", [])
                if isinstance(steps, list):
                    for step in steps:
                        if not isinstance(step, dict) or not step.get("datapoint_id"):
                            continue
                        try:
                            dp = _registry.get(uuid.UUID(str(step["datapoint_id"])))
                            if dp is not None:
                                step["datapoint_name"] = dp.name
                        except (ValueError, TypeError, AttributeError):
                            pass
            processed_nodes.append(node)

    processed_flow = FlowData(nodes=processed_nodes, edges=body.flow_data.edges)

    row = await _persist_created_graph(
        db,
        principal,
        request,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        flow=processed_flow,
        control_class=body.control_class,
        delegated=delegated,
        audit_path="/api/v1/logic/graphs/import",
    )
    try:
        from obs.logic.manager import get_logic_manager

        manager = get_logic_manager()
        await manager.reload()
        await manager.initialize_graph(row["id"])
    except Exception:
        logger.exception("Failed to reload logic manager after importing graph %s", row["id"])
    return _row_to_out(row)


@router.get("/graphs/{graph_id}/run-preflight", response_model=LogicRunPreflight)
async def preflight_graph_run(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicRunPreflight:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)
    return await _logic_run_preflight(db, principal, row)


@router.post("/graphs/{graph_id}/run", status_code=status.HTTP_200_OK)
@audit_application_contract("POST", "/api/v1/logic/graphs/{graph_id}/run", principal_param="_user", resource_param="graph_id")
async def run_graph(
    graph_id: str,
    body: LogicGraphRun | None = None,
    request: Request = None,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)
    preflight = await _logic_run_preflight(db, principal, row)
    authorization_denied = any(not check.allowed and check.target_type != "logic_graph_state" for check in preflight.checks)
    if authorization_denied:
        denied_checks = [check.model_dump() for check in preflight.checks if not check.allowed]
        await AuditLogWriter(
            db=db,
            context=build_audit_context(request=request, current_user=principal),
        ).write_contract(
            "POST",
            "/api/v1/logic/graphs/{graph_id}/run",
            resource_id=graph_id,
            details={"denied_checks": denied_checks},
            outcome=AuditOutcome.DENIED,
        )
        raise mark_contract_audited(HTTPException(status.HTTP_403_FORBIDDEN, "Zugriff verweigert"))
    if not bool(row["enabled"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logikblatt ist deaktiviert")
    try:
        from obs.logic.manager import get_logic_manager

        started = time.perf_counter()
        overrides = body.input_overrides if body else {}
        debug_requested = bool(body and body.debug) or bool(overrides)
        if debug_requested:
            outputs, inputs = await get_logic_manager().execute_graph_debug(graph_id, overrides)
        else:
            outputs = await get_logic_manager().execute_graph(graph_id)
            inputs = {}
        warnings = _logic_run_warnings(outputs)
        await write_application_success(
            db,
            request,
            principal,
            "POST",
            "/api/v1/logic/graphs/{graph_id}/run",
            resource_id=graph_id,
            details={
                "control_class": _row_control_class(row),
                "output_count": len(outputs),
                "warning_count": len(warnings),
            },
            commit=True,
        )
        return {
            "status": "ok",
            "outputs": outputs,
            "warnings": warnings,
            "debug": {
                "timestamp": datetime.now(UTC).isoformat(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "used_overrides": bool(overrides),
                "inputs": inputs,
            },
        }
    except Exception as exc:
        logger.exception("Logic graph run failed for graph %s", graph_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc


@router.post(
    "/graphs/{graph_id}/duplicate",
    response_model=LogicGraphOut,
    status_code=status.HTTP_201_CREATED,
)
@audit_application_contract(
    "POST",
    "/api/v1/logic/graphs/{graph_id}/duplicate",
    principal_param="_user",
    resource_param="graph_id",
)
async def duplicate_graph(
    graph_id: str,
    request: Request = None,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    principal = _principal_from_mutation_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)
    delegated = await _require_logic_graph_creation(
        db,
        principal,
        request,
        operation="duplicate",
        control_class=_row_control_class(row),
        resource_id=graph_id,
    )

    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    flow = FlowData.model_validate(raw)

    # Neue IDs für alle Nodes; Edges auf neue IDs umleiten
    id_map = {n.id: str(uuid.uuid4()) for n in flow.nodes}
    new_nodes = [n.model_copy(update={"id": id_map[n.id]}) for n in flow.nodes]
    new_edges = [
        LogicEdge(
            id=str(uuid.uuid4()),
            source=id_map.get(e.source, e.source),
            target=id_map.get(e.target, e.target),
            sourceHandle=e.sourceHandle,
            targetHandle=e.targetHandle,
        )
        for e in flow.edges
    ]
    new_flow = FlowData(nodes=new_nodes, edges=new_edges)
    _validate_timer_durations(new_flow)

    new_name = f"Kopie von {row['name']}"
    result = await _persist_created_graph(
        db,
        principal,
        request,
        name=new_name,
        description=row["description"] or "",
        enabled=bool(row["enabled"]),
        flow=new_flow,
        control_class=_row_control_class(row),
        delegated=delegated,
        audit_path="/api/v1/logic/graphs/{graph_id}/duplicate",
    )
    try:
        from obs.logic.manager import get_logic_manager

        manager = get_logic_manager()
        await manager.reload()
        await manager.initialize_graph(result["id"])
    except Exception:
        logger.exception("Failed to reload logic manager after duplicating graph %s", result["id"])
    return _row_to_out(result)


@router.get("/datapoint/{dp_id}/usages", response_model=list[LogicUsageOut])
async def get_datapoint_logic_usages(
    dp_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> list[LogicUsageOut]:
    """Return all logic graphs that reference a given DataPoint, with direction from the DP's perspective.

    - datapoint_read node  → logic reads the DP   → direction SOURCE
    - datapoint_write node → logic writes to the DP → direction DEST
    """
    principal = _principal_from_dependency(_user)
    if principal.type != "user" or not principal.is_admin:
        allowed = await filter_authorized_datapoints(db, principal, [dp_id], action=AuthzAction.READ)
        if not allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "DataPoint nicht gefunden")

    rows = await db.fetchall("SELECT id, name, enabled, flow_data FROM logic_graphs")
    usages: list[LogicUsageOut] = []
    for row in rows:
        if not await _can_read_logic_graph(db, principal, row):
            continue
        raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
        flow = FlowData.model_validate(raw)
        for node in flow.nodes:
            if node.type == "datapoint_read":
                if node.data.get("datapoint_id") != dp_id:
                    continue
                direction = "SOURCE"
            elif node.type == "datapoint_write":
                if node.data.get("datapoint_id") != dp_id:
                    continue
                direction = "DEST"
            elif node.type == "api_client":
                variables = _normalise_api_client_variables(node.data.get("variables"))
                if not any(variable["datapoint_id"] == dp_id for variable in variables.values()):
                    continue
                direction = "SOURCE"
            elif node.type == "value_sequence":
                steps = node.data.get("steps") or []
                if isinstance(steps, str):
                    try:
                        steps = json.loads(steps)
                    except json.JSONDecodeError:
                        steps = []
                if not isinstance(steps, list) or not any(isinstance(step, dict) and step.get("datapoint_id") == dp_id for step in steps):
                    continue
                direction = "DEST"
            else:
                continue
            usages.append(
                LogicUsageOut(
                    graph_id=row["id"],
                    graph_name=row["name"],
                    graph_enabled=bool(row["enabled"]),
                    node_id=node.id,
                    node_type=node.type,
                    direction=direction,
                )
            )
    return usages


@router.get("/graphs/{graph_id}/export")
async def export_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> JSONResponse:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)

    export_data = {
        "obs_export": "logic_graph",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "name": row["name"],
        "description": row["description"] or "",
        "enabled": bool(row["enabled"]),
        "control_class": _row_control_class(row),
        "flow_data": json.loads(row["flow_data"]) if row["flow_data"] else {"nodes": [], "edges": []},
    }
    safe_name = row["name"].replace(" ", "_").replace("/", "_")
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )
