"""Unit tests for ringbuffer filterset DB migration (#431).

V29 was overwritten in-place when the flat filterset schema landed — the
legacy ``ringbuffer_filterset_groups`` and ``ringbuffer_filterset_rules``
tables no longer exist and the single remaining ``ringbuffer_filtersets``
table carries the new columns (``color``, ``topbar_active``, ``topbar_order``,
``filter_json``). See #431 for the rationale; the previous nested layout
never reached upstream main so a destructive DROP+CREATE is safe.
"""

from __future__ import annotations

import pytest

from obs.db.database import Database, _migration_v43


@pytest.mark.asyncio
async def test_db_migration_creates_flat_ringbuffer_filterset_table():
    db = Database(":memory:")
    await db.connect()
    try:
        tables = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ringbuffer_filterset%' ORDER BY name")
        names = [row["name"] for row in tables]
        # V33 adds the per-user state table alongside the main filterset table.
        assert names == ["ringbuffer_filterset_user_state", "ringbuffer_filtersets"], (
            "legacy ringbuffer_filterset_groups/_rules tables must be dropped by V29; V33 adds the per-user state table"
        )

        columns = await db.fetchall("PRAGMA table_info(ringbuffer_filtersets)")
        column_names = {row["name"] for row in columns}
        # New columns introduced by #431.
        assert {"color", "topbar_active", "topbar_order", "filter_json"} <= column_names
        # Existing columns preserved.
        assert {"id", "name", "description", "dsl_version", "is_active"} <= column_names
        # #478 adds the owner column.
        assert "created_by" in column_names
        # The legacy is_default column was dropped by V31 (Epic #36 — default
        # sets were superseded by the topbar).
        assert "is_default" not in column_names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_db_migration_creates_topbar_indexes():
    db = Database(":memory:")
    await db.connect()
    try:
        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ringbuffer_filtersets'")
        names = {row["name"] for row in indexes}
        assert "idx_rb_fs_topbar_active" in names
        assert "idx_rb_fs_topbar_order" in names
        # #478 added an index on created_by for fast owner lookups.
        assert "idx_rb_fs_created_by" in names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v33_creates_user_state_table_and_indexes():
    """V33 (#478) adds the per-user topbar override table and its indexes."""
    db = Database(":memory:")
    await db.connect()
    try:
        columns = await db.fetchall("PRAGMA table_info(ringbuffer_filterset_user_state)")
        column_names = {row["name"] for row in columns}
        assert {"username", "filterset_id", "topbar_active", "topbar_order"} <= column_names

        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ringbuffer_filterset_user_state'")
        names = {row["name"] for row in indexes}
        assert "idx_rb_fs_user_state_active" in names
        assert "idx_rb_fs_user_state_order" in names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v43_snapshots_existing_readers_and_valid_owner_idempotently():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO users
               (id, username, password_hash, is_admin, mqtt_enabled, created_at)
               VALUES ('user-alice', 'alice', 'hash', 0, 0, 'now'),
                      ('user-bob', 'bob', 'hash', 0, 0, 'now')"""
        )
        await db.execute(
            """INSERT INTO api_keys (id, name, key_hash, owner, created_at)
               VALUES ('key-alice', 'key', 'key-hash', 'alice', 'now')"""
        )
        await db.execute(
            """INSERT INTO ringbuffer_filtersets
               (id, name, filter_json, created_at, updated_at, created_by)
               VALUES ('owned', 'Owned', '{}', 'now', 'now', 'alice'),
                      ('orphan', 'Orphan', '{}', 'now', 'now', 'missing'),
                      ('empty-owner', 'Empty', '{}', 'now', 'now', '')"""
        )

        await _migration_v43(db.conn)
        await _migration_v43(db.conn)
        await db.commit()

        rows = await db.fetchall(
            """SELECT principal_type, principal_id, node_id, role, effect
               FROM authz_node_roles
               WHERE node_type='ringbuffer_filterset'
               ORDER BY principal_type, principal_id, node_id"""
        )
        assert [tuple(row) for row in rows] == [
            ("api_key", "key-alice", "empty-owner", "guest", "allow"),
            ("api_key", "key-alice", "orphan", "guest", "allow"),
            ("api_key", "key-alice", "owned", "guest", "allow"),
            ("user", "alice", "empty-owner", "guest", "allow"),
            ("user", "alice", "orphan", "guest", "allow"),
            ("user", "alice", "owned", "owner", "allow"),
            ("user", "bob", "empty-owner", "guest", "allow"),
            ("user", "bob", "orphan", "guest", "allow"),
            ("user", "bob", "owned", "guest", "allow"),
        ]
        assert (
            await db.fetchone(
                """SELECT 1 FROM authz_node_roles
               WHERE node_type='ringbuffer_filterset' AND principal_id='missing'"""
            )
            is None
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v43_clean_install_and_future_principals_receive_no_implicit_access():
    db = Database(":memory:")
    await db.connect()
    try:
        assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='ringbuffer_filterset'") is None
        await db.execute(
            """INSERT INTO ringbuffer_filtersets
               (id, name, filter_json, created_at, updated_at)
               VALUES ('future-filter', 'Future', '{}', 'now', 'now')"""
        )
        await db.execute(
            """INSERT INTO users
               (id, username, password_hash, is_admin, mqtt_enabled, created_at)
               VALUES ('future-user', 'future', 'hash', 0, 0, 'now')"""
        )
        await db.commit()
        assert (
            await db.fetchone(
                """SELECT 1 FROM authz_node_roles
               WHERE node_type='ringbuffer_filterset' AND principal_id='future'"""
            )
            is None
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v43_rerun_preserves_post_migration_policy_change():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO users
               (id, username, password_hash, is_admin, mqtt_enabled, created_at)
               VALUES ('user-alice', 'alice', 'hash', 0, 0, 'now')"""
        )
        await db.execute(
            """INSERT INTO ringbuffer_filtersets
               (id, name, filter_json, created_at, updated_at, created_by)
               VALUES ('owned', 'Owned', '{}', 'now', 'now', 'alice')"""
        )
        await _migration_v43(db.conn)
        await db.execute(
            """UPDATE authz_node_roles SET role='resident', effect='deny'
               WHERE principal_type='user' AND principal_id='alice'
                 AND node_type='ringbuffer_filterset' AND node_id='owned'"""
        )

        await _migration_v43(db.conn)
        await db.commit()

        row = await db.fetchone(
            """SELECT role, effect FROM authz_node_roles
               WHERE principal_type='user' AND principal_id='alice'
                 AND node_type='ringbuffer_filterset' AND node_id='owned'"""
        )
        assert dict(row) == {"role": "resident", "effect": "deny"}
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v43_preserves_preexisting_owner_deny():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO users
               (id, username, password_hash, is_admin, mqtt_enabled, created_at)
               VALUES ('user-alice', 'alice', 'hash', 0, 0, 'now')"""
        )
        await db.execute(
            """INSERT INTO ringbuffer_filtersets
               (id, name, filter_json, created_at, updated_at, created_by)
               VALUES ('owned', 'Owned', '{}', 'now', 'now', 'alice')"""
        )
        await db.execute(
            """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
               VALUES ('user', 'alice', 'ringbuffer_filterset', 'owned', 'guest', 'deny')"""
        )

        await _migration_v43(db.conn)
        await db.commit()

        row = await db.fetchone(
            """SELECT role, effect FROM authz_node_roles
               WHERE principal_type='user' AND principal_id='alice'
                 AND node_type='ringbuffer_filterset' AND node_id='owned'"""
        )
        assert dict(row) == {"role": "guest", "effect": "deny"}
    finally:
        await db.disconnect()
