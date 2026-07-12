from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from obs.api.v1 import authz as authz_api
from obs.db.database import Database
from obs.models.authz import AuthzPreviewGrant, AuthzPreviewPrincipal, AuthzPreviewRequest, AuthzPreviewTarget


NOW = "2026-06-10T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_user(db: Database, username: str = "alice", *, is_admin: bool = False) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, is_admin, created_at)
        VALUES (?, ?, 'hash', ?, ?)
        """,
        (str(uuid.uuid4()), username, int(is_admin), NOW),
    )


async def _insert_tree(db: Database) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (NOW, NOW),
    )


async def _insert_node(db: Database, node_id: str, *, parent_id: str | None = None) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', ?, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, parent_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (dp_id, dp_id, f"obs/test/{dp_id}", NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: str, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{node_id}-{dp_id}", node_id, dp_id, NOW),
    )


async def _insert_persisted_grant(
    db: Database,
    *,
    node_type: str = "hierarchy",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', ?, ?, ?, ?)
        """,
        (node_type, node_id, role, effect),
    )


def _request(*, targets: list[AuthzPreviewTarget], grants: list[AuthzPreviewGrant], actions: list[str]) -> AuthzPreviewRequest:
    return AuthzPreviewRequest(
        principal=AuthzPreviewPrincipal(principal_id="alice"),
        actions=actions,
        targets=targets,
        draft_grants=grants,
        include_persisted=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_type", "node_id"),
    [("logic_graph", "missing-graph"), ("logic_capability", "unknown-capability")],
)
async def test_preview_rejects_unknown_logic_targets_like_grant_persistence(
    db: Database,
    node_type: str,
    node_id: str,
) -> None:
    await _insert_user(db)

    with pytest.raises(HTTPException) as exc_info:
        await authz_api.preview_permissions(
            _request(
                actions=["activate"],
                targets=[AuthzPreviewTarget(node_type=node_type, node_id=node_id)],
                grants=[],
            ),
            db=db,
            _admin="admin",
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_uses_draft_grants_without_persisting_them(db: Database):
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")

    response = await authz_api.preview_permissions(
        _request(
            actions=["read", "write"],
            targets=[AuthzPreviewTarget(node_type="hierarchy", node_id="room")],
            grants=[
                AuthzPreviewGrant(
                    principal_id="alice",
                    node_type="hierarchy",
                    node_id="room",
                    role="guest",
                )
            ],
        ),
        db=db,
        _admin="admin",
    )

    by_action = {result.action: result for result in response.results}
    assert response.principal.is_admin is False
    assert by_action["read"].allowed is True
    assert by_action["read"].effective_role == "guest"
    assert by_action["write"].allowed is False
    assert by_action["write"].reason == "missing_allow"
    assert await db.fetchone("SELECT 1 FROM authz_node_roles") is None


@pytest.mark.asyncio
async def test_preview_reports_explicit_deny_as_why_forbidden(db: Database):
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_persisted_grant(db, node_id="room", role="owner", effect="deny")

    response = await authz_api.preview_permissions(
        _request(
            actions=["read"],
            targets=[AuthzPreviewTarget(node_type="hierarchy", node_id="room")],
            grants=[],
        ),
        db=db,
        _admin="admin",
    )

    result = response.results[0]
    assert result.allowed is False
    assert result.reason == "explicit_deny"
    assert result.reason_text == "Denied by explicit deny grant."
    assert result.matching_grants[0].effect == "deny"


@pytest.mark.asyncio
async def test_preview_excludes_descendant_read_deny_from_ancestor_explanation(db: Database):
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_persisted_grant(db, node_id="floor", role="guest", effect="allow")
    await _insert_persisted_grant(db, node_id="room", role="guest", effect="deny")

    response = await authz_api.preview_permissions(
        _request(
            actions=["read"],
            targets=[AuthzPreviewTarget(node_type="hierarchy", node_id="floor")],
            grants=[],
        ),
        db=db,
        _admin="admin",
    )

    result = response.results[0]
    assert result.allowed is True
    assert [(grant.node_id, grant.effect) for grant in result.matching_grants] == [("floor", "allow")]


@pytest.mark.asyncio
async def test_preview_uses_persisted_admin_status_for_user(db: Database):
    await _insert_user(db, is_admin=False)
    await _insert_tree(db)
    await _insert_node(db, "room")

    response = await authz_api.preview_permissions(
        AuthzPreviewRequest(
            principal=AuthzPreviewPrincipal(principal_id="alice", is_admin=True),
            actions=["write"],
            targets=[AuthzPreviewTarget(node_type="hierarchy", node_id="room")],
            draft_grants=[],
        ),
        db=db,
        _admin="admin",
    )

    assert response.principal.is_admin is False
    assert response.results[0].allowed is False
    assert response.results[0].reason == "missing_allow"


@pytest.mark.asyncio
async def test_preview_honors_direct_datapoint_draft_grant_for_linked_datapoint(db: Database):
    dp_id = str(uuid.uuid4())
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, dp_id)
    await _link_datapoint(db, dp_id, "room")

    response = await authz_api.preview_permissions(
        _request(
            actions=["write"],
            targets=[AuthzPreviewTarget(node_type="datapoint", node_id=dp_id)],
            grants=[
                AuthzPreviewGrant(
                    principal_id="alice",
                    node_type="datapoint",
                    node_id=dp_id,
                    role="resident",
                )
            ],
        ),
        db=db,
        _admin="admin",
    )

    result = response.results[0]
    assert result.allowed is True
    assert result.reason == "direct_datapoint_grant"
    assert result.effective_role == "resident"
    assert result.resolved_targets[0].node_type == "hierarchy"
    assert result.matching_grants[0].node_type == "datapoint"


@pytest.mark.asyncio
async def test_preview_datapoint_read_rejects_descendant_hierarchy_grant(db: Database):
    """Grant on a descendant hierarchy node must not imply READ on a datapoint linked to an ancestor.

    The runtime (_datapoint_read_grants) strips descendant hierarchy grants before calling
    authorize so that only the linked node and its ancestors count.  The preview must mirror
    this filtering, otherwise a grant on 'room' (child of 'floor') would falsely show
    'allowed' for a datapoint linked to 'floor' while the actual API would deny it.
    """
    dp_id = str(uuid.uuid4())
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_datapoint(db, dp_id)
    await _link_datapoint(db, dp_id, "floor")
    await _insert_persisted_grant(db, node_id="room", role="guest", effect="allow")

    response = await authz_api.preview_permissions(
        _request(
            actions=["read"],
            targets=[AuthzPreviewTarget(node_type="datapoint", node_id=dp_id)],
            grants=[],
        ),
        db=db,
        _admin="admin",
    )

    result = response.results[0]
    assert result.allowed is False
    assert result.reason == "missing_allow"
    assert result.effective_role is None
    assert result.matching_grants == []


@pytest.mark.asyncio
async def test_preview_direct_datapoint_read_ignores_descendant_hierarchy_deny(db: Database):
    dp_id = str(uuid.uuid4())
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "floor", parent_id="building")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_datapoint(db, dp_id)
    await _link_datapoint(db, dp_id, "floor")
    await _insert_persisted_grant(db, node_id="room", role="guest", effect="deny")

    response = await authz_api.preview_permissions(
        _request(
            actions=["read"],
            targets=[AuthzPreviewTarget(node_type="datapoint", node_id=dp_id)],
            grants=[
                AuthzPreviewGrant(
                    principal_id="alice",
                    node_type="datapoint",
                    node_id=dp_id,
                    role="guest",
                )
            ],
        ),
        db=db,
        _admin="admin",
    )

    result = response.results[0]
    assert result.allowed is True
    assert result.reason == "allowed"
    assert result.effective_role == "guest"
    assert [(grant.node_type, grant.node_id, grant.effect) for grant in result.matching_grants] == [("datapoint", dp_id, "allow")]
