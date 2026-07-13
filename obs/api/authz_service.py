"""Shared AuthZ runtime helpers for route-level policy wiring."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from obs.api.auth import Principal
from obs.api.authz import AuthzAction, AuthzTarget, RoleGrant, authorize
from obs.db.database import Database


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _placeholders(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


def _principal_ids(principal: Principal) -> list[str]:
    if principal.type == "api_key" and principal.subject.startswith("api_key:"):
        return _unique([principal.subject.removeprefix("api_key:"), principal.subject])
    return [principal.subject]


async def load_role_grants(
    db: Database,
    principal: Principal,
    *,
    node_type: str | None = None,
) -> list[RoleGrant]:
    """Load persisted grants for *principal* and materialize hierarchy paths."""
    principal_ids = _principal_ids(principal)
    params: list[Any] = [principal.type, *principal_ids]
    node_filter = ""
    if node_type is not None:
        node_filter = " AND node_type=?"
        params.append(node_type)

    rows = await db.fetchall(
        f"""
        SELECT principal_type, principal_id, node_type, node_id, role, effect
        FROM authz_node_roles
        WHERE principal_type=? AND principal_id IN ({_placeholders(principal_ids)}){node_filter}
        ORDER BY node_type, node_id, role
        """,
        params,
    )

    hierarchy_ids = [row["node_id"] for row in rows if row["node_type"] == "hierarchy"]
    hierarchy_targets = {target.node_id: target for target in await resolve_hierarchy_targets(db, hierarchy_ids)}
    page_ids = [row["node_id"] for row in rows if row["node_type"] == "visu_page"]
    page_targets = {target.node_id: target for target in await resolve_visu_page_targets(db, page_ids)}

    grants: list[RoleGrant] = []
    for row in rows:
        ancestors: tuple[str, ...] = ()
        if row["node_type"] == "hierarchy":
            target = hierarchy_targets.get(row["node_id"])
            ancestors = target.ancestors if target else ()
        elif row["node_type"] == "visu_page":
            target = page_targets.get(row["node_id"])
            ancestors = target.ancestors if target else ()
        grants.append(
            RoleGrant(
                principal_type=row["principal_type"],
                principal_id=row["principal_id"],
                node_type=row["node_type"],
                node_id=row["node_id"],
                role=row["role"],
                effect=row["effect"],
                ancestors=ancestors,
            )
        )
    return grants


async def resolve_visu_page_targets(db: Database, node_ids: Iterable[str]) -> list[AuthzTarget]:
    """Resolve Visu nodes into central AuthZ targets with page-tree ancestry."""
    ordered_ids = _unique(node_ids)
    if not ordered_ids:
        return []

    rows = await db.fetchall(
        f"""
        WITH RECURSIVE anc(leaf_id, cur_id, cur_parent, depth, seen) AS (
            SELECT id, id, parent_id, 0, '|' || id || '|'
            FROM visu_nodes
            WHERE id IN ({_placeholders(ordered_ids)})
            UNION ALL
            SELECT anc.leaf_id, vn.id, vn.parent_id, anc.depth + 1, anc.seen || vn.id || '|'
            FROM anc
            JOIN visu_nodes vn ON vn.id = anc.cur_parent
            WHERE anc.cur_parent IS NOT NULL
              AND instr(anc.seen, '|' || vn.id || '|') = 0
        )
        SELECT leaf_id, cur_id, depth
        FROM anc
        ORDER BY leaf_id, depth DESC
        """,
        ordered_ids,
    )

    ancestors_by_leaf: dict[str, list[str]] = {}
    found_leaf_ids: set[str] = set()
    for row in rows:
        leaf_id = row["leaf_id"]
        found_leaf_ids.add(leaf_id)
        if row["depth"] > 0:
            ancestors_by_leaf.setdefault(leaf_id, []).append(row["cur_id"])

    return [
        AuthzTarget(
            node_type="visu_page",
            node_id=node_id,
            ancestors=tuple(ancestors_by_leaf.get(node_id, [])),
        )
        for node_id in ordered_ids
        if node_id in found_leaf_ids
    ]


async def authorize_visu_page(
    db: Database,
    principal: Principal,
    node_id: str,
    *,
    action: AuthzAction | str = AuthzAction.READ,
) -> bool:
    """Evaluate a Visu page-tree target through the central grant engine."""
    targets = await resolve_visu_page_targets(db, [node_id])
    grants = await load_role_grants(db, principal, node_type="visu_page")
    return authorize(principal=principal, action=action, targets=targets, grants=grants).allowed


async def resolve_hierarchy_targets(db: Database, node_ids: Iterable[str]) -> list[AuthzTarget]:
    """Resolve hierarchy nodes into AuthZ targets with root-to-parent ancestors."""
    ordered_ids = _unique(node_ids)
    if not ordered_ids:
        return []

    rows = await db.fetchall(
        f"""
        WITH RECURSIVE anc(leaf_id, cur_id, cur_parent, depth, seen) AS (
            SELECT id, id, parent_id, 0, '|' || id || '|'
            FROM hierarchy_nodes
            WHERE id IN ({_placeholders(ordered_ids)})
            UNION ALL
            SELECT anc.leaf_id, hn.id, hn.parent_id, anc.depth + 1, anc.seen || hn.id || '|'
            FROM anc
            JOIN hierarchy_nodes hn ON hn.id = anc.cur_parent
            WHERE anc.cur_parent IS NOT NULL
              AND instr(anc.seen, '|' || hn.id || '|') = 0
        )
        SELECT leaf_id, cur_id, depth
        FROM anc
        ORDER BY leaf_id, depth DESC
        """,
        ordered_ids,
    )

    ancestors_by_leaf: dict[str, list[str]] = {}
    found_leaf_ids: set[str] = set()
    for row in rows:
        leaf_id = row["leaf_id"]
        found_leaf_ids.add(leaf_id)
        if row["depth"] > 0:
            ancestors_by_leaf.setdefault(leaf_id, []).append(row["cur_id"])

    return [
        AuthzTarget(
            node_type="hierarchy",
            node_id=node_id,
            ancestors=tuple(ancestors_by_leaf.get(node_id, [])),
        )
        for node_id in ordered_ids
        if node_id in found_leaf_ids
    ]


async def resolve_datapoint_targets(db: Database, dp_ids: Iterable[str]) -> dict[str, list[AuthzTarget]]:
    """Resolve datapoints to hierarchy targets.

    Linked datapoints evaluate all linked hierarchy nodes. Existing datapoints
    without hierarchy links receive a direct ``datapoint`` target: that keeps
    the admin bridge effective while ungranted non-admin access still denies.
    """
    ordered_ids = _unique(dp_ids)
    if not ordered_ids:
        return {}

    existing_rows = await db.fetchall(
        f"SELECT id FROM datapoints WHERE id IN ({_placeholders(ordered_ids)})",
        ordered_ids,
    )
    existing_ids = {row["id"] for row in existing_rows}

    link_rows = await db.fetchall(
        f"""
        SELECT datapoint_id, node_id
        FROM hierarchy_datapoint_links
        WHERE datapoint_id IN ({_placeholders(ordered_ids)})
        ORDER BY datapoint_id, node_id
        """,
        ordered_ids,
    )
    node_targets = {target.node_id: target for target in await resolve_hierarchy_targets(db, [row["node_id"] for row in link_rows])}
    targets_by_dp: dict[str, list[AuthzTarget]] = {dp_id: [] for dp_id in ordered_ids}
    for row in link_rows:
        target = node_targets.get(row["node_id"])
        if target is not None:
            targets_by_dp[row["datapoint_id"]].append(target)

    for dp_id in ordered_ids:
        if dp_id in existing_ids and not targets_by_dp[dp_id]:
            targets_by_dp[dp_id].append(AuthzTarget(node_type="datapoint", node_id=dp_id))

    return targets_by_dp


def _datapoint_read_grants(grants: Sequence[RoleGrant], targets: Sequence[AuthzTarget]) -> list[RoleGrant]:
    """Restrict datapoint READ hierarchy grants to linked nodes and ancestors."""
    result: list[RoleGrant] = []
    for grant in grants:
        if any(grant.node_type == target.node_type and grant.node_id in target.path for target in targets):
            result.append(grant)
    return result


async def filter_authorized_datapoints(
    db: Database,
    principal: Principal,
    dp_ids: Iterable[str],
    *,
    action: AuthzAction | str = AuthzAction.READ,
    grants: Sequence[RoleGrant] | None = None,
) -> list[str]:
    """Return datapoint IDs from *dp_ids* authorized for *principal*."""
    ordered_ids = _unique(dp_ids)
    if not ordered_ids:
        return []

    action_value = AuthzAction(action)
    resolved_grants = list(grants) if grants is not None else await load_role_grants(db, principal)
    targets_by_dp = await resolve_datapoint_targets(db, ordered_ids)
    direct_grant_ids = {grant.node_id for grant in resolved_grants if grant.node_type == "datapoint" and grant.node_id in ordered_ids}
    for dp_id in direct_grant_ids if action_value == AuthzAction.READ else ():
        targets = targets_by_dp.setdefault(dp_id, [])
        if not any(target.node_type == "datapoint" and target.node_id == dp_id for target in targets):
            targets.append(AuthzTarget(node_type="datapoint", node_id=dp_id))

    allowed: list[str] = []
    for dp_id in ordered_ids:
        targets = targets_by_dp.get(dp_id, [])
        decision_grants = _datapoint_read_grants(resolved_grants, targets) if action_value == AuthzAction.READ else resolved_grants
        decision = authorize(
            principal=principal,
            action=action_value,
            targets=targets,
            grants=decision_grants,
        )
        if action_value != AuthzAction.READ and dp_id in direct_grant_ids:
            direct_decision = authorize(
                principal=principal,
                action=action_value,
                targets=[AuthzTarget(node_type="datapoint", node_id=dp_id)],
                grants=resolved_grants,
            )
            if decision.reason == "explicit_deny" or direct_decision.reason == "explicit_deny":
                continue
            if decision.allowed or direct_decision.allowed:
                allowed.append(dp_id)
            continue
        if decision.allowed:
            allowed.append(dp_id)
            continue
    return allowed


async def filter_authorized_hierarchy_nodes(
    db: Database,
    principal: Principal,
    node_ids: Iterable[str],
    *,
    action: AuthzAction | str = AuthzAction.READ,
    grants: Sequence[RoleGrant] | None = None,
) -> list[str]:
    """Return hierarchy node IDs from *node_ids* authorized for *principal*."""
    ordered_ids = _unique(node_ids)
    if not ordered_ids:
        return []
    if principal.type == "user" and principal.is_admin:
        return ordered_ids

    resolved_grants = list(grants) if grants is not None else await load_role_grants(db, principal, node_type="hierarchy")
    targets_by_node = {target.node_id: target for target in await resolve_hierarchy_targets(db, ordered_ids)}
    allowed: list[str] = []
    for node_id in ordered_ids:
        target = targets_by_node.get(node_id)
        if target is None:
            continue
        decision = authorize(
            principal=principal,
            action=action,
            targets=[target],
            grants=resolved_grants,
        )
        if decision.allowed:
            allowed.append(node_id)
    return allowed
