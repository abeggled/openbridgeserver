"""Unit tests for the hierarchy tree-root-node backfill migration (#1217 follow-up)."""

from __future__ import annotations

import pytest

from obs.db.database import Database, _migration_v54_hierarchy_tree_root_nodes


async def _column_names(db: Database, table: str) -> set[str]:
    rows = await db.fetchall(f"PRAGMA table_info({table})")
    return {row["name"] for row in rows}


@pytest.mark.asyncio
async def test_v54_adds_is_tree_root_column():
    db = Database(":memory:")
    await db.connect()
    try:
        assert "is_tree_root" in await _column_names(db, "hierarchy_nodes")
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v54_backfills_a_root_node_for_a_pre_existing_tree():
    db = Database(":memory:")
    await db.connect()
    try:
        now = "2024-01-01T00:00:00+00:00"
        # A tree inserted directly (bypassing create_tree) simulates one that
        # predates this migration and therefore has no root node yet.
        await db.execute_and_commit(
            "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("pre-existing-tree", "Beschattung", "", now, now),
        )

        await _migration_v54_hierarchy_tree_root_nodes(db.conn)

        roots = await db.fetchall(
            "SELECT * FROM hierarchy_nodes WHERE tree_id=? AND is_tree_root=1",
            ("pre-existing-tree",),
        )
        assert len(roots) == 1
        assert roots[0]["name"] == "Beschattung"
        assert roots[0]["parent_id"] is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v54_backfill_is_idempotent():
    db = Database(":memory:")
    await db.connect()
    try:
        now = "2024-01-01T00:00:00+00:00"
        await db.execute_and_commit(
            "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("idempotent-tree", "Licht", "", now, now),
        )

        await _migration_v54_hierarchy_tree_root_nodes(db.conn)
        await _migration_v54_hierarchy_tree_root_nodes(db.conn)
        await _migration_v54_hierarchy_tree_root_nodes(db.conn)

        roots = await db.fetchall(
            "SELECT id FROM hierarchy_nodes WHERE tree_id=? AND is_tree_root=1",
            ("idempotent-tree",),
        )
        assert len(roots) == 1  # never creates a second root node for the same tree
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v54_does_not_touch_a_tree_that_already_has_a_root_node():
    """A tree created after v54 (via create_tree) already has its root node —
    re-running the migration must not add a second one."""
    db = Database(":memory:")
    await db.connect()
    try:
        now = "2024-01-01T00:00:00+00:00"
        await db.execute_and_commit(
            "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("normal-tree", "Steckdosen", "", now, now),
        )
        await db.execute_and_commit(
            """INSERT INTO hierarchy_nodes
                   (id, tree_id, parent_id, name, description, node_order, icon, is_tree_root, created_at, updated_at)
               VALUES (?,?,NULL,?,'',-1,NULL,1,?,?)""",
            ("existing-root", "normal-tree", "Steckdosen", now, now),
        )

        await _migration_v54_hierarchy_tree_root_nodes(db.conn)

        roots = await db.fetchall(
            "SELECT id FROM hierarchy_nodes WHERE tree_id=? AND is_tree_root=1",
            ("normal-tree",),
        )
        assert [r["id"] for r in roots] == ["existing-root"]
    finally:
        await db.disconnect()
