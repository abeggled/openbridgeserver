"""AuthZ administration and preview endpoints for the owner UI."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from obs.api.auth import Principal, get_admin_user
from obs.api.audit import AuditLogWriter, build_audit_context
from obs.api.authz import AuthzAction, AuthzDecision, AuthzTarget, GrantEffect, Role, RoleGrant, authorize
from obs.api.authz_service import _datapoint_read_grants, load_role_grants, resolve_datapoint_targets, resolve_hierarchy_targets
from obs.db.database import Database, get_db
from obs.models.authz import (
    AuthzPreviewGrant,
    AuthzPreviewPrincipal,
    AuthzPreviewRequest,
    AuthzPreviewResolvedTarget,
    AuthzPreviewResponse,
    AuthzPreviewResult,
    AuthzPreviewTarget,
    AuthzPrincipalGrant,
    AuthzPrincipalGrantsReplace,
    AuthzPrincipalGrantsResponse,
    AuthzPrincipalReference,
    PrincipalTypeName,
)

router = APIRouter(tags=["authz"])

_STRONG_ETAG_PATTERN = re.compile(r'^"[0-9a-f]{64}"$')

_ROLE_RANK: dict[Role, int] = {
    Role.GUEST: 0,
    Role.RESIDENT: 1,
    Role.OPERATOR: 2,
    Role.OWNER: 3,
}

_REASON_TEXT: dict[str, str] = {
    "admin": "Allowed by administrator bridge.",
    "allowed": "Allowed by matching role grant.",
    "direct_datapoint_grant": "Allowed by direct datapoint grant.",
    "explicit_deny": "Denied by explicit deny grant.",
    "missing_allow": "Denied because no matching allow grant applies.",
    "no_targets": "Denied because the target could not be resolved.",
}


def _canonical_principal_id(principal_type: PrincipalTypeName, principal_id: str) -> str:
    if principal_type == "user":
        return principal_id

    raw_id = principal_id.removeprefix("api_key:")
    try:
        return str(UUID(raw_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "API key principal_id must be a UUID") from exc


def _principal_ids(principal_type: PrincipalTypeName, principal_id: str) -> tuple[str, ...]:
    if principal_type == "api_key":
        return (principal_id, f"api_key:{principal_id}")
    return (principal_id,)


async def _require_principal(db: Database, principal_type: PrincipalTypeName, principal_id: str) -> None:
    if principal_type == "user":
        row = await db.fetchone("SELECT 1 FROM users WHERE username=?", (principal_id,))
    else:
        row = await db.fetchone("SELECT 1 FROM api_keys WHERE id=?", (principal_id,))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{principal_type} principal '{principal_id}' not found")


async def _require_grant_targets(db: Database, grants: Sequence[AuthzPrincipalGrant]) -> None:
    table_by_type = {"hierarchy": "hierarchy_nodes", "datapoint": "datapoints"}
    for node_type, table in table_by_type.items():
        node_ids = sorted({grant.node_id for grant in grants if grant.node_type == node_type})
        if not node_ids:
            continue
        placeholders = ",".join("?" for _ in node_ids)
        rows = await db.fetchall(f"SELECT id FROM {table} WHERE id IN ({placeholders})", node_ids)
        missing = sorted(set(node_ids) - {row["id"] for row in rows})
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown {node_type} grant targets: {', '.join(missing)}",
            )


async def _load_principal_grants(
    db: Database,
    principal_type: PrincipalTypeName,
    principal_id: str,
) -> list[AuthzPrincipalGrant]:
    principal_ids = _principal_ids(principal_type, principal_id)
    placeholders = ",".join("?" for _ in principal_ids)
    rows = await db.fetchall(
        f"""
        SELECT principal_id, node_type, node_id, role, effect
        FROM authz_node_roles
        WHERE principal_type=? AND principal_id IN ({placeholders})
        ORDER BY CASE WHEN principal_id=? THEN 0 ELSE 1 END, node_type, node_id
        """,
        (principal_type, *principal_ids, principal_id),
    )
    by_target: dict[tuple[str, str], AuthzPrincipalGrant] = {}
    for row in rows:
        target = (row["node_type"], row["node_id"])
        grant = AuthzPrincipalGrant(node_type=row["node_type"], node_id=row["node_id"], role=row["role"], effect=row["effect"])
        existing = by_target.get(target)
        if existing is not None:
            continue
        by_target[target] = grant
    return [by_target[target] for target in sorted(by_target)]


def _grants_response(
    principal_type: PrincipalTypeName,
    principal_id: str,
    grants: Sequence[AuthzPrincipalGrant],
) -> AuthzPrincipalGrantsResponse:
    return AuthzPrincipalGrantsResponse(
        principal=AuthzPrincipalReference(principal_type=principal_type, principal_id=principal_id),
        grants=list(grants),
    )


def _canonical_grants(grants: Sequence[AuthzPrincipalGrant]) -> list[dict[str, str]]:
    return [
        {
            "node_type": grant.node_type,
            "node_id": grant.node_id,
            "role": grant.role,
            "effect": grant.effect,
        }
        for grant in sorted(grants, key=lambda grant: (grant.node_type, grant.node_id))
    ]


def _grants_sha256(grants: Sequence[AuthzPrincipalGrant]) -> str:
    payload = json.dumps(_canonical_grants(grants), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _grants_etag(grants: Sequence[AuthzPrincipalGrant]) -> str:
    return f'"{_grants_sha256(grants)}"'


def _require_if_match(if_match: object) -> str:
    if not isinstance(if_match, str) or _STRONG_ETAG_PATTERN.fullmatch(if_match) is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, "A valid strong If-Match grant revision is required")
    return if_match


def _set_revision_headers(response: Response, grants: Sequence[AuthzPrincipalGrant]) -> None:
    response.headers["ETag"] = _grants_etag(grants)
    response.headers["Cache-Control"] = "no-store"


def _grant_diff_details(
    before: Sequence[AuthzPrincipalGrant],
    after: Sequence[AuthzPrincipalGrant],
) -> dict[str, Any]:
    before_by_target = {(grant.node_type, grant.node_id): {"role": grant.role, "effect": grant.effect} for grant in before}
    after_by_target = {(grant.node_type, grant.node_id): {"role": grant.role, "effect": grant.effect} for grant in after}
    common = set(before_by_target) & set(after_by_target)
    changed_targets = sorted(
        target for target in set(before_by_target) | set(after_by_target) if before_by_target.get(target) != after_by_target.get(target)
    )
    return {
        "before_count": len(before),
        "after_count": len(after),
        "added_count": len(set(after_by_target) - set(before_by_target)),
        "removed_count": len(set(before_by_target) - set(after_by_target)),
        "updated_count": sum(before_by_target[target] != after_by_target[target] for target in common),
        "unchanged_count": sum(before_by_target[target] == after_by_target[target] for target in common),
        "before_sha256": _grants_sha256(before),
        "after_sha256": _grants_sha256(after),
        "changes": [
            {
                "node_type": node_type,
                "node_id": node_id,
                "before": before_by_target.get((node_type, node_id)),
                "after": after_by_target.get((node_type, node_id)),
            }
            for node_type, node_id in changed_targets
        ],
    }


async def _principal_from_preview(db: Database, principal: AuthzPreviewPrincipal) -> Principal:
    if principal.principal_type == "user":
        row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (principal.principal_id,))
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{principal.principal_id}' not found")
        return Principal(
            subject=principal.principal_id,
            type="user",
            is_admin=bool(row["is_admin"]),
        )

    subject = principal.principal_id if principal.principal_id.startswith("api_key:") else f"api_key:{principal.principal_id}"
    return Principal(subject=subject, type="api_key", is_admin=False)


async def _materialize_draft_grants(db: Database, grants: Sequence[AuthzPreviewGrant]) -> list[RoleGrant]:
    hierarchy_ids = [grant.node_id for grant in grants if grant.node_type == "hierarchy"]
    hierarchy_targets = {target.node_id: target for target in await resolve_hierarchy_targets(db, hierarchy_ids)}

    result: list[RoleGrant] = []
    for grant in grants:
        target = hierarchy_targets.get(grant.node_id)
        result.append(
            RoleGrant(
                principal_type=grant.principal_type,
                principal_id=grant.principal_id,
                node_type=grant.node_type,
                node_id=grant.node_id,
                role=grant.role,
                effect=grant.effect,
                ancestors=target.ancestors if target else (),
            )
        )
    return result


def _grant_key(grant: RoleGrant) -> tuple[str, str, str, str]:
    return (grant.principal_type, grant.principal_id, grant.node_type, grant.node_id)


def _merge_grants(persisted: Sequence[RoleGrant], draft: Sequence[RoleGrant]) -> list[RoleGrant]:
    by_key = {_grant_key(grant): grant for grant in persisted}
    for grant in draft:
        by_key[_grant_key(grant)] = grant
    return list(by_key.values())


def _validate_draft_grants(body: AuthzPreviewRequest) -> None:
    expected_type = body.principal.principal_type
    expected_ids = {body.principal.principal_id}
    if expected_type == "api_key":
        raw_id = body.principal.principal_id.removeprefix("api_key:")
        expected_ids = {body.principal.principal_id, raw_id, f"api_key:{raw_id}"}

    for grant in body.draft_grants:
        if grant.principal_type != expected_type or grant.principal_id not in expected_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Draft grants must target the preview principal")


async def _resolve_targets(db: Database, target: AuthzPreviewTarget) -> list[AuthzTarget]:
    if target.node_type == "hierarchy":
        resolved = await resolve_hierarchy_targets(db, [target.node_id])
    else:
        resolved = (await resolve_datapoint_targets(db, [target.node_id])).get(target.node_id, [])

    if target.min_role is None:
        return resolved
    return [
        AuthzTarget(
            node_type=resolved_target.node_type,
            node_id=resolved_target.node_id,
            ancestors=resolved_target.ancestors,
            min_role=target.min_role,
        )
        for resolved_target in resolved
    ]


def _direct_datapoint_grants(grants: Iterable[RoleGrant], target: AuthzPreviewTarget) -> list[RoleGrant]:
    if target.node_type != "datapoint":
        return []
    return [grant for grant in grants if grant.node_type == "datapoint" and grant.node_id == target.node_id]


def _decision_for_target(
    *,
    principal: Principal,
    action: AuthzAction,
    target: AuthzPreviewTarget,
    resolved_targets: Sequence[AuthzTarget],
    grants: Sequence[RoleGrant],
) -> AuthzDecision:
    direct_targets = [AuthzTarget(node_type="datapoint", node_id=target.node_id, min_role=target.min_role)]
    direct_grants = _direct_datapoint_grants(grants, target)

    if target.node_type == "datapoint" and action == AuthzAction.READ:
        decision_targets = [*resolved_targets, *direct_targets] if direct_grants else list(resolved_targets)
        decision_grants = _datapoint_read_grants(grants, decision_targets)
        return authorize(principal=principal, action=action, targets=decision_targets, grants=decision_grants)

    decision_grants = grants
    decision = authorize(principal=principal, action=action, targets=resolved_targets, grants=decision_grants)
    if target.node_type != "datapoint" or not direct_grants or action == AuthzAction.READ:
        return decision

    direct_decision = authorize(principal=principal, action=action, targets=direct_targets, grants=grants)
    if decision.reason == "explicit_deny" or direct_decision.reason == "explicit_deny":
        return AuthzDecision(False, "explicit_deny")
    if decision.allowed or direct_decision.allowed:
        return AuthzDecision(True, "direct_datapoint_grant" if direct_decision.allowed and not decision.allowed else "allowed")
    return decision


def _grant_matches_principal(principal: Principal, grant: RoleGrant) -> bool:
    if grant.principal_type != principal.type:
        return False
    if grant.principal_id == principal.subject:
        return True
    if principal.type == "api_key" and principal.subject.startswith("api_key:"):
        return grant.principal_id == principal.subject.removeprefix("api_key:")
    return False


def _grant_applies_to_target(action: AuthzAction, grant: RoleGrant, target: AuthzTarget) -> bool:
    if grant.node_type != target.node_type:
        return False
    if action == AuthzAction.READ:
        return target.node_id in grant.path or grant.node_id in target.path
    return grant.node_id in target.path


def _matching_grants(
    *,
    principal: Principal,
    action: AuthzAction,
    target: AuthzPreviewTarget,
    resolved_targets: Sequence[AuthzTarget],
    grants: Sequence[RoleGrant],
) -> list[RoleGrant]:
    targets = list(resolved_targets)
    if target.node_type == "datapoint" and _direct_datapoint_grants(grants, target):
        targets.append(AuthzTarget(node_type="datapoint", node_id=target.node_id, min_role=target.min_role))
    matching_candidates = _datapoint_read_grants(grants, targets) if (target.node_type == "datapoint" and action == AuthzAction.READ) else grants

    return [
        grant
        for grant in matching_candidates
        if _grant_matches_principal(principal, grant) and any(_grant_applies_to_target(action, grant, resolved_target) for resolved_target in targets)
    ]


def _effective_role(grants: Sequence[RoleGrant]) -> Role | None:
    allow_roles = [grant.role for grant in grants if grant.effect == GrantEffect.ALLOW]
    if not allow_roles:
        return None
    return max(allow_roles, key=lambda role: _ROLE_RANK[role])


def _grant_to_model(grant: RoleGrant) -> AuthzPreviewGrant:
    return AuthzPreviewGrant(
        principal_type=grant.principal_type,
        principal_id=grant.principal_id,
        node_type=grant.node_type,
        node_id=grant.node_id,
        role=grant.role.value,
        effect=grant.effect.value,
    )


def _target_to_model(target: AuthzTarget) -> AuthzPreviewResolvedTarget:
    return AuthzPreviewResolvedTarget(
        node_type=target.node_type,
        node_id=target.node_id,
        ancestors=list(target.ancestors),
        min_role=target.min_role.value if target.min_role else None,
    )


@router.get(
    "/principals/{principal_type}/{principal_id:path}/grants",
    response_model=AuthzPrincipalGrantsResponse,
)
async def get_principal_grants(
    principal_type: PrincipalTypeName,
    principal_id: str,
    response: Response,
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> AuthzPrincipalGrantsResponse:
    """Return the complete persisted grant set for one principal."""
    canonical_id = _canonical_principal_id(principal_type, principal_id)
    await _require_principal(db, principal_type, canonical_id)
    grants = await _load_principal_grants(db, principal_type, canonical_id)
    _set_revision_headers(response, grants)
    return _grants_response(principal_type, canonical_id, grants)


@router.put(
    "/principals/{principal_type}/{principal_id:path}/grants",
    response_model=AuthzPrincipalGrantsResponse,
)
async def replace_principal_grants(
    principal_type: PrincipalTypeName,
    principal_id: str,
    body: AuthzPrincipalGrantsReplace,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> AuthzPrincipalGrantsResponse:
    """Atomically replace one principal's complete persisted grant set."""
    expected_etag = _require_if_match(if_match)
    canonical_id = _canonical_principal_id(principal_type, principal_id)
    principal_ids = _principal_ids(principal_type, canonical_id)
    placeholders = ",".join("?" for _ in principal_ids)
    async with db.transaction():
        await _require_principal(db, principal_type, canonical_id)
        before = await _load_principal_grants(db, principal_type, canonical_id)
        if expected_etag != _grants_etag(before):
            raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, "Grant revision is stale")
        await _require_grant_targets(db, body.grants)
        await db.execute(
            f"DELETE FROM authz_node_roles WHERE principal_type=? AND principal_id IN ({placeholders})",
            (principal_type, *principal_ids),
        )
        if body.grants:
            await db.executemany(
                """
                INSERT INTO authz_node_roles
                    (principal_type, principal_id, node_type, node_id, role, effect)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(principal_type, canonical_id, grant.node_type, grant.node_id, grant.role, grant.effect) for grant in body.grants],
            )

        audit_writer = AuditLogWriter(
            db=db,
            context=build_audit_context(request=request, current_user=_admin),
        )
        await audit_writer.write(
            action="authz.grants.replace",
            resource_type="authz_principal",
            resource_id=f"{principal_type}:{canonical_id}",
            details=_grant_diff_details(before, body.grants),
            commit=False,
        )

    grants = sorted(body.grants, key=lambda grant: (grant.node_type, grant.node_id))
    _set_revision_headers(response, grants)
    return _grants_response(principal_type, canonical_id, grants)


@router.post("/preview", response_model=AuthzPreviewResponse)
async def preview_permissions(
    body: AuthzPreviewRequest,
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> AuthzPreviewResponse:
    """Dry-run effective AuthZ decisions for the owner UI without writing grants."""
    _validate_draft_grants(body)
    principal = await _principal_from_preview(db, body.principal)
    persisted = await load_role_grants(db, principal) if body.include_persisted else []
    draft = await _materialize_draft_grants(db, body.draft_grants)
    grants = _merge_grants(persisted, draft)

    results: list[AuthzPreviewResult] = []
    for target in body.targets:
        resolved_targets = await _resolve_targets(db, target)
        for raw_action in body.actions:
            action = AuthzAction(raw_action)
            matching = _matching_grants(
                principal=principal,
                action=action,
                target=target,
                resolved_targets=resolved_targets,
                grants=grants,
            )
            decision = _decision_for_target(
                principal=principal,
                action=action,
                target=target,
                resolved_targets=resolved_targets,
                grants=grants,
            )
            effective_role = _effective_role(matching)
            results.append(
                AuthzPreviewResult(
                    action=action.value,
                    node_type=target.node_type,
                    node_id=target.node_id,
                    allowed=decision.allowed,
                    reason=decision.reason,
                    reason_text=_REASON_TEXT.get(decision.reason, decision.reason),
                    effective_role=effective_role.value if effective_role else None,
                    required_role=target.min_role,
                    resolved_targets=[_target_to_model(resolved_target) for resolved_target in resolved_targets],
                    matching_grants=[_grant_to_model(grant) for grant in matching],
                )
            )

    return AuthzPreviewResponse(
        principal=AuthzPreviewPrincipal(
            principal_type=principal.type,
            principal_id=body.principal.principal_id,
            is_admin=principal.is_admin,
        ),
        results=results,
    )
