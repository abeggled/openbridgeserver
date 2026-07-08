from __future__ import annotations

import pytest

from obs.api.auth import Principal
from obs.api.authz import AuthzAction, GrantEffect, Role
from obs.api.authz_service import (
    filter_authorized_hierarchy_nodes,
    filter_authorized_datapoints,
    load_role_grants,
    resolve_datapoint_targets,
    resolve_hierarchy_targets,
)
from obs.db.database import Database


NOW = "2026-06-10T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_tree(db: Database, tree_id: str = "tree") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES (?, ?, '', ?, ?)
        """,
        (tree_id, tree_id, NOW, NOW),
    )


async def _insert_node(db: Database, node_id: str, *, parent_id: str | None = None, tree_id: str = "tree") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, ?, ?, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, tree_id, parent_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', NULL, '[]', ?, NULL, ?, ?)
        """,
        (dp_id, dp_id, f"obs/test/{dp_id}", NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: str, node_id: str, link_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (link_id, node_id, dp_id, NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_type: str = "user",
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (principal_type, principal_id, node_type, node_id, role, effect),
    )


@pytest.mark.asyncio
async def test_load_role_grants_converts_db_rows_with_text_enums_and_ancestors(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_grant(db, node_id="room", role="operator", effect="deny")

    grants = await load_role_grants(db, Principal(subject="alice", type="user", is_admin=False))

    assert len(grants) == 1
    assert grants[0].role is Role.OPERATOR
    assert grants[0].effect is GrantEffect.DENY
    assert grants[0].ancestors == ("building", "floor")


@pytest.mark.asyncio
async def test_load_role_grants_matches_api_key_principal_to_persisted_raw_key_id(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id="key-1",
        node_id="room",
        role="guest",
    )

    grants = await load_role_grants(db, Principal(subject="api_key:key-1", type="api_key", is_admin=False))

    assert len(grants) == 1
    assert grants[0].principal_id == "key-1"
    assert grants[0].role is Role.GUEST


@pytest.mark.asyncio
async def test_resolve_hierarchy_targets_returns_ancestor_paths(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")

    targets = await resolve_hierarchy_targets(db, ["room"])

    assert len(targets) == 1
    assert targets[0].node_type == "hierarchy"
    assert targets[0].node_id == "room"
    assert targets[0].ancestors == ("building", "floor")


@pytest.mark.asyncio
async def test_resolve_hierarchy_targets_keeps_ancestors_beyond_legacy_depth_cutoff(db: Database):
    await _insert_tree(db)
    parent_id = None
    for index in range(70):
        node_id = f"node-{index}"
        await _insert_node(db, node_id, parent_id=parent_id)
        parent_id = node_id

    targets = await resolve_hierarchy_targets(db, ["node-69"])

    assert len(targets) == 1
    assert len(targets[0].ancestors) == 69
    assert targets[0].ancestors[0] == "node-0"


@pytest.mark.asyncio
async def test_resolve_datapoint_targets_includes_all_linked_hierarchy_nodes(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "wing-a")
    await _insert_node(db, "room-a", parent_id="wing-a")
    await _insert_node(db, "wing-b")
    await _insert_node(db, "room-b", parent_id="wing-b")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room-a", "link-a")
    await _link_datapoint(db, "dp-1", "room-b", "link-b")

    targets_by_dp = await resolve_datapoint_targets(db, ["dp-1"])

    targets = targets_by_dp["dp-1"]
    assert {target.node_id for target in targets} == {"room-a", "room-b"}
    hierarchy_targets = [target for target in targets if target.node_type == "hierarchy"]
    assert {target.ancestors for target in hierarchy_targets} == {("wing-a",), ("wing-b",)}


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_honors_direct_grants_on_linked_datapoints(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="guest", effect="allow")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert allowed == ["dp-1"]


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_does_not_use_child_grants_for_ancestor_links(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "floor", "link-floor")
    await _insert_grant(db, node_id="room", role="guest", effect="allow")

    child_allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    await _insert_grant(db, node_id="building", role="guest", effect="allow")
    ancestor_allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert child_allowed == []
    assert ancestor_allowed == ["dp-1"]


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_honors_direct_write_grants_on_linked_datapoints(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="resident", effect="allow")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.WRITE,
    )

    assert allowed == ["dp-1"]


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_keeps_explicit_deny_over_direct_write_grant(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, node_id="room", role="resident", effect="deny")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="resident", effect="allow")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.WRITE,
    )

    assert allowed == []


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_keeps_direct_deny_over_hierarchy_write_grant(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, node_id="room", role="resident", effect="allow")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="resident", effect="deny")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.WRITE,
    )

    assert allowed == []


@pytest.mark.asyncio
async def test_resolve_datapoint_targets_ignores_other_principals_direct_grants(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, principal_id="bob", node_type="datapoint", node_id="dp-1", role="guest", effect="allow")

    targets_by_dp = await resolve_datapoint_targets(db, ["dp-1"])

    assert [(target.node_type, target.node_id) for target in targets_by_dp["dp-1"]] == [("hierarchy", "room")]


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_honors_direct_denies_on_linked_datapoints(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link")
    await _insert_grant(db, node_id="room", role="guest", effect="allow")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="guest", effect="deny")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert allowed == []


@pytest.mark.asyncio
async def test_filter_authorized_datapoints_evaluates_all_linked_targets(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room-a")
    await _insert_node(db, "room-b")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room-a", "link-a")
    await _link_datapoint(db, "dp-1", "room-b", "link-b")
    await _insert_grant(db, node_id="room-a", role="guest", effect="allow")
    await _insert_grant(db, node_id="room-b", role="guest", effect="deny")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert allowed == []


@pytest.mark.asyncio
async def test_linked_datapoints_keep_direct_datapoint_allow_grants(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link-room")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="guest", effect="allow")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert allowed == ["dp-1"]


@pytest.mark.asyncio
async def test_linked_datapoints_keep_direct_datapoint_deny_grants(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, "dp-1")
    await _link_datapoint(db, "dp-1", "room", "link-room")
    await _insert_grant(db, node_type="hierarchy", node_id="room", role="guest", effect="allow")
    await _insert_grant(db, node_type="datapoint", node_id="dp-1", role="guest", effect="deny")

    allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-1"],
        action=AuthzAction.READ,
    )

    assert allowed == []


@pytest.mark.asyncio
async def test_unlinked_datapoints_keep_admin_bridge_but_deny_ungranted_non_admin(db: Database):
    await _insert_datapoint(db, "dp-unlinked")

    admin_allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="admin", type="user", is_admin=True),
        ["dp-unlinked"],
    )
    user_allowed = await filter_authorized_datapoints(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["dp-unlinked"],
    )

    assert admin_allowed == ["dp-unlinked"]
    assert user_allowed == []


@pytest.mark.asyncio
async def test_filter_authorized_hierarchy_nodes_applies_read_inheritance(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_node(db, "secret")
    await _insert_grant(db, node_id="room", role="guest", effect="allow")

    allowed = await filter_authorized_hierarchy_nodes(
        db,
        Principal(subject="alice", type="user", is_admin=False),
        ["building", "floor", "room", "secret"],
    )

    assert allowed == ["building", "floor", "room"]
