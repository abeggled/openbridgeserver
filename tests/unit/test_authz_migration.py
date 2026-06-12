from __future__ import annotations

import pytest

from obs.db.database import Database


@pytest.mark.asyncio
async def test_db_migration_creates_authz_node_roles_table():
    db = Database(":memory:")
    await db.connect()
    try:
        columns = await db.fetchall("PRAGMA table_info(authz_node_roles)")
        column_names = {row["name"] for row in columns}
        assert {
            "principal_type",
            "principal_id",
            "node_type",
            "node_id",
            "role",
            "effect",
            "created_at",
            "updated_at",
        } <= column_names

        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='authz_node_roles'")
        index_names = {row["name"] for row in indexes}
        assert "idx_authz_node_roles_principal" in index_names
        assert "idx_authz_node_roles_node" in index_names
        assert "idx_authz_node_roles_role" in index_names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_authz_node_roles_accepts_valid_role_assignments():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            """
            INSERT INTO authz_node_roles
                (principal_type, principal_id, node_type, node_id, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("user", "alice", "hierarchy", "node-1", "owner"),
        )

        row = await db.fetchone("SELECT principal_type, principal_id, node_type, node_id, role, effect FROM authz_node_roles")
        assert row is not None
        assert dict(row) == {
            "principal_type": "user",
            "principal_id": "alice",
            "node_type": "hierarchy",
            "node_id": "node-1",
            "role": "owner",
            "effect": "allow",
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_authz_node_roles_rejects_unknown_roles():
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(Exception, match="CHECK constraint failed"):
            await db.execute_and_commit(
                """
                INSERT INTO authz_node_roles
                    (principal_type, principal_id, node_type, node_id, role)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("user", "alice", "hierarchy", "node-1", "admin"),
            )
    finally:
        await db.disconnect()
