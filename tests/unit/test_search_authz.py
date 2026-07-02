from __future__ import annotations

from datetime import UTC, datetime

import pytest

import obs.api.v1.datapoints as datapoints_api
import obs.api.v1.search as search_api
from obs.api.auth import Principal
from obs.db.database import Database
from obs.models.datapoint import DataPoint


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, datapoints: list[DataPoint]) -> None:
        self._datapoints = datapoints

    def all(self) -> list[DataPoint]:
        return list(self._datapoints)

    def get_value(self, dp_id):
        return None


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_tree(db: Database, tree_id: str = "building") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES (?, ?, '', ?, ?)
        """,
        (tree_id, tree_id, NOW, NOW),
    )


async def _insert_node(db: Database, node_id: str, *, tree_id: str = "building", parent_id: str | None = None) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, ?, ?, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, tree_id, parent_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp: DataPoint) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(dp.id),
            dp.name,
            dp.data_type,
            dp.unit,
            "[]",
            dp.mqtt_topic,
            dp.mqtt_alias,
            int(dp.persist_value),
            int(dp.record_history),
            dp.created_at.isoformat(),
            dp.updated_at.isoformat(),
        ),
    )


async def _link_datapoint(db: Database, dp: DataPoint, node_id: str, link_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (link_id, node_id, str(dp.id), NOW),
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


async def _call_search(
    db: Database,
    principal: Principal,
    *,
    q: str = "",
    node_id: str = "",
    tree_id: str = "",
):
    return await search_api.search(
        q=q,
        tag="",
        type="",
        adapter="",
        quality="",
        node_id=node_id,
        tree_id=tree_id,
        sort="name",
        order="asc",
        page=0,
        size=50,
        _user=principal,
        db=db,
    )


def _dp(name: str) -> DataPoint:
    return DataPoint(name=name, data_type="FLOAT", unit="°C", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_non_admin_search_returns_only_readable_datapoints(db: Database, monkeypatch):
    await _insert_tree(db)
    await _insert_node(db, "room-allowed")
    await _insert_node(db, "room-denied")
    allowed = _dp("AuthZ Search Allowed")
    denied = _dp("AuthZ Search Denied")
    await _insert_datapoint(db, allowed)
    await _insert_datapoint(db, denied)
    await _link_datapoint(db, allowed, "room-allowed", "link-allowed")
    await _link_datapoint(db, denied, "room-denied", "link-denied")
    await _insert_grant(db, node_id="room-allowed")
    registry = _RegistryStub([allowed, denied])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    result = await _call_search(db, Principal(subject="alice", type="user", is_admin=False), q="AuthZ Search")

    assert [item.id for item in result.items] == [allowed.id]
    assert result.total == 1


@pytest.mark.asyncio
async def test_explicit_deny_removes_otherwise_matching_search_result(db: Database, monkeypatch):
    await _insert_tree(db)
    await _insert_node(db, "floor")
    await _insert_node(db, "allowed-room", parent_id="floor")
    await _insert_node(db, "denied-room", parent_id="floor")
    allowed = _dp("AuthZ Search Allowed")
    denied = _dp("AuthZ Search Explicit Deny")
    await _insert_datapoint(db, allowed)
    await _insert_datapoint(db, denied)
    await _link_datapoint(db, allowed, "allowed-room", "link-allowed-room")
    await _link_datapoint(db, denied, "denied-room", "link-denied-room")
    await _insert_grant(db, node_id="floor")
    await _insert_grant(db, node_id="denied-room", effect="deny")
    registry = _RegistryStub([allowed, denied])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    result = await _call_search(db, Principal(subject="alice", type="user", is_admin=False), q="AuthZ Search")

    assert [item.id for item in result.items] == [allowed.id]
    assert result.total == 1


@pytest.mark.asyncio
async def test_node_and_tree_filters_compose_with_authz_filtering(db: Database, monkeypatch):
    await _insert_tree(db, "allowed-tree")
    await _insert_tree(db, "other-tree")
    await _insert_node(db, "allowed-root", tree_id="allowed-tree")
    await _insert_node(db, "allowed-room", tree_id="allowed-tree", parent_id="allowed-root")
    await _insert_node(db, "unauthorized-room", tree_id="allowed-tree", parent_id="allowed-root")
    await _insert_node(db, "other-room", tree_id="other-tree")
    allowed = _dp("AuthZ Search Allowed")
    unauthorized_same_tree = _dp("AuthZ Search Unauthorized Same Tree")
    other_tree = _dp("AuthZ Search Other Tree")
    await _insert_datapoint(db, allowed)
    await _insert_datapoint(db, unauthorized_same_tree)
    await _insert_datapoint(db, other_tree)
    await _link_datapoint(db, allowed, "allowed-room", "link-allowed")
    await _link_datapoint(db, unauthorized_same_tree, "unauthorized-room", "link-unauthorized")
    await _link_datapoint(db, other_tree, "other-room", "link-other")
    await _insert_grant(db, node_id="allowed-room")
    registry = _RegistryStub([allowed, unauthorized_same_tree, other_tree])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    principal = Principal(subject="alice", type="user", is_admin=False)

    node_result = await _call_search(db, principal, q="AuthZ Search", node_id="allowed-root")
    tree_result = await _call_search(db, principal, q="AuthZ Search", tree_id="allowed-tree")

    assert [item.id for item in node_result.items] == [allowed.id]
    assert node_result.total == 1
    assert [item.id for item in tree_result.items] == [allowed.id]
    assert tree_result.total == 1


@pytest.mark.asyncio
async def test_node_and_tree_filters_include_directly_granted_datapoints(db: Database, monkeypatch):
    await _insert_tree(db, "building")
    await _insert_node(db, "root", tree_id="building")
    await _insert_node(db, "room", tree_id="building", parent_id="root")
    direct = _dp("AuthZ Search Direct Grant")
    denied = _dp("AuthZ Search Denied")
    await _insert_datapoint(db, direct)
    await _insert_datapoint(db, denied)
    await _link_datapoint(db, direct, "room", "link-direct")
    await _link_datapoint(db, denied, "room", "link-denied")
    await _insert_grant(db, node_type="datapoint", node_id=str(direct.id))
    registry = _RegistryStub([direct, denied])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    principal = Principal(subject="alice", type="user", is_admin=False)

    node_result = await _call_search(db, principal, q="AuthZ Search", node_id="root")
    tree_result = await _call_search(db, principal, q="AuthZ Search", tree_id="building")

    assert [item.id for item in node_result.items] == [direct.id]
    assert node_result.total == 1
    assert [item.id for item in tree_result.items] == [direct.id]
    assert tree_result.total == 1


@pytest.mark.asyncio
async def test_search_hierarchy_metadata_and_filters_hide_unauthorized_links(db: Database, monkeypatch):
    await _insert_tree(db, "building")
    await _insert_node(db, "allowed-room", tree_id="building")
    await _insert_node(db, "secret-room", tree_id="building")
    shared = _dp("AuthZ Search Shared")
    await _insert_datapoint(db, shared)
    await _link_datapoint(db, shared, "allowed-room", "link-shared-allowed")
    await _link_datapoint(db, shared, "secret-room", "link-shared-secret")
    await _insert_grant(db, node_id="allowed-room")
    registry = _RegistryStub([shared])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    principal = Principal(subject="alice", type="user", is_admin=False)

    default_result = await _call_search(db, principal, q="AuthZ Search Shared")
    secret_node_result = await _call_search(db, principal, q="AuthZ Search Shared", node_id="secret-room")

    assert [item.id for item in default_result.items] == [shared.id]
    assert [node.node_id for node in default_result.items[0].hierarchy_nodes] == ["allowed-room"]
    assert secret_node_result.items == []
    assert secret_node_result.total == 0


@pytest.mark.asyncio
async def test_admin_search_still_returns_ungranted_datapoints(db: Database, monkeypatch):
    admin_visible = _dp("AuthZ Search Admin Visible")
    await _insert_datapoint(db, admin_visible)
    registry = _RegistryStub([admin_visible])
    monkeypatch.setattr(search_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    result = await _call_search(db, Principal(subject="admin", type="user", is_admin=True), q="AuthZ Search")

    assert [item.id for item in result.items] == [admin_visible.id]
    assert result.total == 1
