"""Unit tests for KNX device schema migration (#647)."""

from __future__ import annotations

import pytest
import aiosqlite

from obs.db.database import Database, _MIGRATION_V34, _MIGRATION_V35, _migration_v36


async def _table_names(db: Database) -> set[str]:
    rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    return {row["name"] for row in rows}


async def _column_names(db: Database, table: str) -> set[str]:
    rows = await db.fetchall(f"PRAGMA table_info({table})")
    return {row["name"] for row in rows}


async def _index_names(db: Database, table: str) -> set[str]:
    rows = await db.fetchall(f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='{table}'")
    return {row["name"] for row in rows}


@pytest.mark.asyncio
async def test_v34_creates_knx_device_tables_columns_and_indexes():
    db = Database(":memory:")
    await db.connect()
    try:
        tables = await _table_names(db)
        assert "knx_devices" in tables
        assert "knx_comm_objects" in tables
        assert "knx_co_ga_links" in tables
        assert "knx_space_device_links" in tables

        assert {
            "id",
            "individual_address",
            "name",
            "description",
            "product_name",
            "product_refid",
            "hardware2program_refid",
            "imported_at",
        } <= await _column_names(db, "knx_devices")

        assert {
            "id",
            "device_id",
            "number",
            "name",
            "text",
            "function_text",
            "datapoint_type",
            "imported_at",
        } <= await _column_names(db, "knx_comm_objects")

        assert {"comm_object_id", "ga_address"} <= await _column_names(db, "knx_co_ga_links")
        assert {"space_id", "device_id"} <= await _column_names(db, "knx_space_device_links")

        assert "idx_knx_devices_pa" in await _index_names(db, "knx_devices")
        assert "idx_knx_co_device" in await _index_names(db, "knx_comm_objects")
        assert "idx_knx_coga_ga" in await _index_names(db, "knx_co_ga_links")
        assert "idx_knx_space_device_device" in await _index_names(db, "knx_space_device_links")
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v34_is_idempotent_and_preserves_existing_knx_tables():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.conn.executescript(_MIGRATION_V34)
        await db.commit()

        tables = await _table_names(db)
        assert "knx_group_addresses" in tables
        assert "knx_locations" in tables
        assert "knx_functions" in tables
        assert "knx_trades" in tables
        assert "knx_devices" in tables
        assert "knx_comm_objects" in tables
        assert "knx_co_ga_links" in tables
        assert "knx_space_device_links" in tables
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v39_repairs_existing_v38_without_device_hierarchy_links(tmp_path):
    db_path = tmp_path / "v38-collision.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT '')")
        await conn.executemany("INSERT INTO schema_version (version) VALUES (?)", [(version,) for version in range(1, 30)])
        await conn.executemany("INSERT INTO schema_version (version) VALUES (?)", [(32,), (33,), (34,), (35,), (36,), (37,), (38,)])
        await conn.executescript(_MIGRATION_V34)
        await conn.executescript(_MIGRATION_V35)
        await conn.commit()

    db = Database(str(db_path))
    await db.connect()
    try:
        tables = await _table_names(db)
        assert "hierarchy_device_links" in tables
        assert {"id", "node_id", "device_id", "created_at"} <= await _column_names(db, "hierarchy_device_links")
        assert "idx_hierarchy_device_links_node" in await _index_names(db, "hierarchy_device_links")
        assert "idx_hierarchy_device_links_device" in await _index_names(db, "hierarchy_device_links")
        version = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert version["version"] == 47
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v36_hierarchy_source_migration_is_idempotent():
    db = Database(":memory:")
    await db.connect()
    try:
        await _migration_v36(db.conn)

        columns = await db.fetchall("PRAGMA table_info(hierarchy_trees)")
        column_names = {row["name"] for row in columns}
        assert "source" in column_names

        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='hierarchy_trees'")
        index_names = {row["name"] for row in indexes}
        assert "idx_hierarchy_trees_source" in index_names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v36_does_not_promote_editable_descriptions_to_sources():
    db = Database(":memory:")
    await db.connect()
    try:
        now = "2024-01-01T00:00:00+00:00"
        await db.execute_and_commit(
            "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("legacy-ets-tree", "Legacy ETS", "ets_import:buildings", "", now, now),
        )
        await db.execute_and_commit(
            "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("manual-tree", "Manual", "custom manual tree", "", now, now),
        )

        await _migration_v36(db.conn)

        legacy_like_manual = await db.fetchone("SELECT source FROM hierarchy_trees WHERE id=?", ("legacy-ets-tree",))
        manual = await db.fetchone("SELECT source FROM hierarchy_trees WHERE id=?", ("manual-tree",))

        assert legacy_like_manual["source"] == ""
        assert manual["source"] == ""
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v36_reraises_unexpected_operational_error():
    class FailingConnection:
        async def execute(self, _sql: str) -> None:
            raise aiosqlite.OperationalError("database is locked")

    with pytest.raises(aiosqlite.OperationalError, match="database is locked"):
        await _migration_v36(FailingConnection())
