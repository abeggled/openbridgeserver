from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import config as config_api
from obs.api.v1 import hierarchy as hierarchy_api
from obs.api.v1.services.hierarchy_import import replace_existing_ets_trees
from obs.db.database import Database


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _tree(db: Database, tree_id: str, *, source: str = "") -> None:
    await db.execute_and_commit(
        """INSERT INTO hierarchy_trees
               (id, name, description, source, created_at, updated_at)
           VALUES (?, ?, '', ?, 'now', 'now')""",
        (tree_id, tree_id, source),
    )


async def _node(db: Database, node_id: str, tree_id: str, *, parent_id: str | None = None) -> None:
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, created_at, updated_at)
           VALUES (?, ?, ?, ?, '', 0, 'now', 'now')""",
        (node_id, tree_id, parent_id, node_id),
    )


async def _grant(
    db: Database,
    node_id: str,
    *,
    principal_type: str = "user",
    principal_id: str = "alice",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES (?, ?, 'hierarchy', ?, 'resident', ?)""",
        (principal_type, principal_id, node_id, effect),
    )


async def _hierarchy_grants(db: Database) -> list[tuple[str, str, str]]:
    rows = await db.fetchall(
        """SELECT principal_id, node_id, effect
           FROM authz_node_roles
           WHERE node_type='hierarchy'
           ORDER BY principal_id, node_id"""
    )
    return [(row["principal_id"], row["node_id"], row["effect"]) for row in rows]


@pytest.mark.asyncio
async def test_delete_node_removes_subtree_grants_and_preserves_surviving_allow_and_deny(db: Database) -> None:
    await _tree(db, "tree")
    await _node(db, "root", "tree")
    await _node(db, "child", "tree", parent_id="root")
    await _node(db, "survivor", "tree")
    await _grant(db, "root")
    await _grant(db, "child", principal_type="api_key", principal_id="key-1", effect="deny")
    await _grant(db, "survivor")
    await _grant(db, "survivor", principal_id="bob", effect="deny")

    await hierarchy_api.delete_node("root", _user="admin", db=db)

    assert await db.fetchall("SELECT id FROM hierarchy_nodes WHERE id IN ('root', 'child')") == []
    assert await _hierarchy_grants(db) == [
        ("alice", "survivor", "allow"),
        ("bob", "survivor", "deny"),
    ]


@pytest.mark.asyncio
async def test_delete_tree_removes_all_node_grants_and_preserves_other_tree_deny(db: Database) -> None:
    await _tree(db, "deleted-tree")
    await _tree(db, "surviving-tree")
    await _node(db, "deleted-root", "deleted-tree")
    await _node(db, "deleted-child", "deleted-tree", parent_id="deleted-root")
    await _node(db, "surviving-node", "surviving-tree")
    await _grant(db, "deleted-root")
    await _grant(db, "deleted-child", effect="deny")
    await _grant(db, "surviving-node", effect="deny")

    await hierarchy_api.delete_tree("deleted-tree", _user="admin", db=db)

    assert await db.fetchall("SELECT id FROM hierarchy_nodes WHERE tree_id='deleted-tree'") == []
    assert await _hierarchy_grants(db) == [("alice", "surviving-node", "deny")]


@pytest.mark.asyncio
async def test_delete_node_rolls_back_grant_removal_when_resource_delete_fails(db: Database) -> None:
    await _tree(db, "tree")
    await _node(db, "root", "tree")
    await _node(db, "child", "tree", parent_id="root")
    await _grant(db, "root")
    await _grant(db, "child", effect="deny")
    await db.execute_and_commit(
        """CREATE TRIGGER block_hierarchy_node_delete
           BEFORE DELETE ON hierarchy_nodes
           WHEN OLD.id = 'root'
           BEGIN
               SELECT RAISE(ABORT, 'blocked');
           END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        await hierarchy_api.delete_node("root", _user="admin", db=db)

    assert [row["id"] for row in await db.fetchall("SELECT id FROM hierarchy_nodes ORDER BY id")] == ["child", "root"]
    assert await _hierarchy_grants(db) == [
        ("alice", "child", "deny"),
        ("alice", "root", "allow"),
    ]


@pytest.mark.asyncio
async def test_ets_replacement_removes_replaced_tree_grants_only(db: Database) -> None:
    await _tree(db, "auto-tree", source="ets_import:groups")
    await _tree(db, "manual-tree")
    await _node(db, "auto-node", "auto-tree")
    await _node(db, "manual-node", "manual-tree")
    await _grant(db, "auto-node", effect="deny")
    await _grant(db, "manual-node")

    assert await replace_existing_ets_trees(db, "groups") == 1

    assert await db.fetchone("SELECT id FROM hierarchy_trees WHERE id='auto-tree'") is None
    assert await _hierarchy_grants(db) == [("alice", "manual-node", "allow")]


@pytest.mark.asyncio
async def test_ets_replacement_rolls_back_grants_when_tree_delete_fails(db: Database) -> None:
    await _tree(db, "auto-tree", source="ets_import:groups")
    await _node(db, "auto-node", "auto-tree")
    await _grant(db, "auto-node", effect="deny")
    await db.execute_and_commit(
        """CREATE TRIGGER block_hierarchy_tree_delete
           BEFORE DELETE ON hierarchy_trees
           WHEN OLD.id = 'auto-tree'
           BEGIN
               SELECT RAISE(ABORT, 'blocked');
           END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        await replace_existing_ets_trees(db, "groups")

    assert await db.fetchone("SELECT id FROM hierarchy_trees WHERE id='auto-tree'") is not None
    assert await _hierarchy_grants(db) == [("alice", "auto-node", "deny")]


@pytest.mark.asyncio
async def test_factory_reset_removes_hierarchy_grants_and_preserves_non_resource_capability(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    await _tree(db, "tree")
    await _node(db, "node", "tree")
    await _grant(db, "node", effect="deny")
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'logic_capability', 'http_request', 'resident', 'allow')"""
    )
    registry = SimpleNamespace(_points={}, _values={})
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as manager,
        patch("obs.api.v1.icons._icons_dir") as icons_dir,
    ):
        manager.return_value.reload = AsyncMock()
        icons_dir.return_value = MagicMock(glob=MagicMock(return_value=[]))
        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.errors == []
    assert await _hierarchy_grants(db) == []
    capability = await db.fetchone("SELECT effect FROM authz_node_roles WHERE node_type='logic_capability' AND node_id='http_request'")
    assert capability is not None
    assert capability["effect"] == "allow"
