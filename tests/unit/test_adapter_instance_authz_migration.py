from __future__ import annotations

import pytest

from obs.db.database import Database, _migration_v44


NOW = "2026-07-13T00:00:00+00:00"


async def _insert_user(db: Database, username: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, created_at)
        VALUES (?, ?, 'hash', 0, 0, ?)
        """,
        (f"user-{username}", username, NOW),
    )


async def _insert_datapoint(db: Database, datapoint_id: str, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, tags, mqtt_topic, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', '[]', ?, 1, 1, ?, ?)
        """,
        (datapoint_id, datapoint_id, f"test/{datapoint_id}", NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{datapoint_id}", node_id, datapoint_id, NOW),
    )


async def _insert_instance(db: Database, instance_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, 'MQTT', ?, '{}', 0, ?, ?)
        """,
        (instance_id, instance_id, NOW, NOW),
    )


async def _insert_binding(
    db: Database,
    binding_id: str,
    instance_id: str,
    datapoint_id: str,
    *,
    adapter_type: str = "MQTT",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'SOURCE', '{}', 1, ?, ?)
        """,
        (binding_id, datapoint_id, adapter_type, instance_id, NOW, NOW),
    )


@pytest.mark.asyncio
async def test_v44_clean_install_is_default_deny_and_idempotent() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        version = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert version["version"] == 44

        await _migration_v44(db.conn)
        await _migration_v44(db.conn)
        grants = await db.fetchall("SELECT * FROM authz_node_roles WHERE node_type='adapter_instance'")
        assert grants == []
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v44_materializes_only_unambiguous_effective_instance_scope() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            """
            INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
            VALUES ('tree', 'Tree', '', ?, ?)
            """,
            (NOW, NOW),
        )
        await db.executemany(
            """
            INSERT INTO hierarchy_nodes
                (id, tree_id, parent_id, name, description, node_order, created_at, updated_at)
            VALUES (?, 'tree', NULL, ?, '', 0, ?, ?)
            """,
            [("room-a", "Room A", NOW, NOW), ("room-b", "Room B", NOW, NOW)],
        )
        await db.commit()
        await _insert_datapoint(db, "dp-a", "room-a")
        await _insert_datapoint(db, "dp-b", "room-b")

        await _insert_user(db, "operator")
        await _insert_user(db, "partial")
        await db.executemany(
            """
            INSERT INTO authz_node_roles
                (principal_type, principal_id, node_type, node_id, role, effect)
            VALUES ('user', ?, 'hierarchy', ?, ?, ?)
            """,
            [
                ("operator", "room-a", "operator", "allow"),
                ("operator", "room-b", "operator", "allow"),
                ("partial", "room-a", "guest", "allow"),
                ("partial", "room-b", "operator", "deny"),
            ],
        )
        await db.commit()

        await _insert_instance(db, "scoped")
        await _insert_binding(db, "binding-a", "scoped", "dp-a")
        await _insert_binding(db, "binding-b", "scoped", "dp-b")
        await _insert_instance(db, "unbound")
        await _insert_instance(db, "ambiguous")
        await _insert_binding(db, "binding-bad", "ambiguous", "dp-a", adapter_type="KNX")

        await _migration_v44(db.conn)
        first = await db.fetchall(
            """
            SELECT principal_id, node_id, role, effect
            FROM authz_node_roles
            WHERE node_type='adapter_instance'
            ORDER BY principal_id, node_id
            """,
        )
        assert [tuple(row) for row in first] == [
            ("operator", "scoped", "operator", "allow"),
            ("partial", "scoped", "guest", "allow"),
        ]

        await _migration_v44(db.conn)
        second = await db.fetchall(
            """
            SELECT principal_id, node_id, role, effect
            FROM authz_node_roles
            WHERE node_type='adapter_instance'
            ORDER BY principal_id, node_id
            """,
        )
        assert [tuple(row) for row in second] == [tuple(row) for row in first]
    finally:
        await db.disconnect()
