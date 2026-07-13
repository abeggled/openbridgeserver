from __future__ import annotations

import aiosqlite
import pytest

from obs.db.database import Database, _migration_v45


AUTHZ_TABLE = """
CREATE TABLE authz_node_roles (
    principal_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    node_id TEXT NOT NULL,
    role TEXT NOT NULL,
    effect TEXT NOT NULL,
    PRIMARY KEY (principal_type, principal_id, node_type, node_id)
)
"""


async def _grant(
    conn: aiosqlite.Connection,
    *,
    node_type: str,
    node_id: str,
    effect: str = "allow",
) -> None:
    await conn.execute(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, ?, ?, 'guest', ?)""",
        (f"user-{node_type}-{node_id}-{effect}", node_type, node_id, effect),
    )


@pytest.mark.asyncio
async def test_v45_removes_only_orphaned_concrete_resource_grants() -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    resources = {
        "datapoint": "datapoints",
        "hierarchy": "hierarchy_nodes",
        "visu_page": "visu_nodes",
        "logic_graph": "logic_graphs",
        "ringbuffer_filterset": "ringbuffer_filtersets",
        "adapter_instance": "adapter_instances",
    }
    try:
        await conn.execute(AUTHZ_TABLE)
        for table in resources.values():
            await conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")

        for node_type, table in resources.items():
            await conn.execute(f"INSERT INTO {table} (id) VALUES ('valid-{node_type}')")
            await _grant(conn, node_type=node_type, node_id=f"valid-{node_type}", effect="deny")
            await _grant(conn, node_type=node_type, node_id=f"orphan-{node_type}")
        await _grant(conn, node_type="logic_capability", node_id="http_request", effect="deny")
        await _grant(conn, node_type="future_resource", node_id="unknown")

        await _migration_v45(conn)
        await _migration_v45(conn)

        rows = await (
            await conn.execute(
                "SELECT node_type, node_id, effect FROM authz_node_roles ORDER BY node_type, node_id",
            )
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("adapter_instance", "valid-adapter_instance", "deny"),
            ("datapoint", "valid-datapoint", "deny"),
            ("future_resource", "unknown", "allow"),
            ("hierarchy", "valid-hierarchy", "deny"),
            ("logic_capability", "http_request", "deny"),
            ("logic_graph", "valid-logic_graph", "deny"),
            ("ringbuffer_filterset", "valid-ringbuffer_filterset", "deny"),
            ("visu_page", "valid-visu_page", "deny"),
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_v45_leaves_grant_types_with_missing_historical_tables_untouched() -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(AUTHZ_TABLE)
        await conn.execute("CREATE TABLE datapoints (id TEXT PRIMARY KEY)")
        await _grant(conn, node_type="datapoint", node_id="missing-datapoint")
        await _grant(conn, node_type="hierarchy", node_id="unknown-without-table", effect="deny")

        await _migration_v45(conn)

        rows = await (await conn.execute("SELECT node_type, node_id, effect FROM authz_node_roles")).fetchall()
        assert [tuple(row) for row in rows] == [
            ("hierarchy", "unknown-without-table", "deny"),
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_clean_install_reaches_v45_and_reconciliation_is_idempotent() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        row = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert row["version"] == 47

        await _migration_v45(db.conn)
        await _migration_v45(db.conn)
        assert await db.fetchall("SELECT * FROM authz_node_roles") == []
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_populated_v44_database_applies_v45_on_upgrade(tmp_path) -> None:
    path = tmp_path / "populated-v44.sqlite"
    db = Database(str(path))
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO datapoints
                   (id, name, data_type, tags, mqtt_topic, created_at, updated_at)
               VALUES ('valid-dp', 'Valid', 'FLOAT', '[]', 'valid/dp', 'now', 'now')""",
        )
        await db.executemany(
            """INSERT INTO authz_node_roles
                   (principal_type, principal_id, node_type, node_id, role, effect)
               VALUES ('user', ?, 'datapoint', ?, 'guest', 'allow')""",
            [("valid-user", "valid-dp"), ("orphan-user", "orphan-dp")],
        )
        await db.execute("DELETE FROM schema_version WHERE version>=45")
        await db.commit()
    finally:
        await db.disconnect()

    upgraded = Database(str(path))
    await upgraded.connect()
    try:
        version = await upgraded.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert version["version"] == 47
        rows = await upgraded.fetchall(
            "SELECT principal_id, node_id FROM authz_node_roles ORDER BY principal_id",
        )
        assert [tuple(row) for row in rows] == [("valid-user", "valid-dp")]
    finally:
        await upgraded.disconnect()
