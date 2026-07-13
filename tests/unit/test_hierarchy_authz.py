from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import hierarchy as hierarchy_api
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


def _principal(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


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


async def _insert_datapoint(db: Database, dp_id: str, *, name: str, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', NULL, '[]', ?, NULL, ?, ?)
        """,
        (dp_id, name, f"obs/test/{dp_id}", NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{dp_id}", node_id, dp_id, NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, ?, ?, ?, ?)
        """,
        (principal_id, node_type, node_id, role, effect),
    )


@pytest.mark.asyncio
async def test_get_tree_nodes_filters_unreadable_branch(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "allowed-room", parent_id="building")
    await _insert_node(db, "secret-room", parent_id="building")
    await _insert_grant(db, node_id="allowed-room")

    nodes = await hierarchy_api.get_tree_nodes("tree", _user=_principal(), db=db)

    assert [node.id for node in nodes] == ["building"]
    assert [node.id for node in nodes[0].children] == ["allowed-room"]


@pytest.mark.asyncio
async def test_get_tree_nodes_admin_sees_ungranted_nodes(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "building")
    await _insert_node(db, "secret-room", parent_id="building")

    nodes = await hierarchy_api.get_tree_nodes("tree", _user=_principal(is_admin=True), db=db)

    assert [node.id for node in nodes] == ["building"]
    assert [node.id for node in nodes[0].children] == ["secret-room"]


@pytest.mark.asyncio
async def test_get_node_datapoints_filters_denied_datapoints(db: Database):
    allowed_dp = str(uuid.uuid4())
    denied_dp = str(uuid.uuid4())
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_datapoint(db, allowed_dp, name="Allowed", node_id="room")
    await _insert_datapoint(db, denied_dp, name="Denied", node_id="room")
    await _insert_grant(db, node_id="room")
    await _insert_grant(db, node_type="datapoint", node_id=denied_dp, effect="deny")

    datapoints = await hierarchy_api.get_node_datapoints("room", _user=_principal(), db=db)

    assert [str(dp.id) for dp in datapoints] == [allowed_dp]


@pytest.mark.asyncio
async def test_get_node_datapoints_hides_unreadable_node(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "secret-room")

    with pytest.raises(HTTPException) as exc_info:
        await hierarchy_api.get_node_datapoints("secret-room", _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_search_nodes_filters_unreadable_results(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "allowed-room")
    await _insert_node(db, "secret-room")
    await _insert_grant(db, node_id="allowed-room")

    results = await hierarchy_api.search_nodes(q="room", limit=10, _user=_principal(), db=db)

    assert [result.node_id for result in results] == ["allowed-room"]


@pytest.mark.asyncio
async def test_search_nodes_applies_auth_before_limit(db: Database):
    await _insert_tree(db)
    await _insert_node(db, "a-blocked-room")
    await _insert_node(db, "z-readable-room")
    await _insert_grant(db, node_id="z-readable-room")

    results = await hierarchy_api.search_nodes(q="room", limit=1, _user=_principal(), db=db)

    assert [result.node_id for result in results] == ["z-readable-room"]
