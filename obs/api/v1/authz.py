"""AuthZ preview endpoints for owner UI dry-runs."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from fastapi import APIRouter, Depends, HTTPException, status

from obs.api.auth import Principal, get_admin_user
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
)

router = APIRouter(tags=["authz"])

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
        if grant.effect == GrantEffect.DENY:
            return grant.node_id in target.path
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
