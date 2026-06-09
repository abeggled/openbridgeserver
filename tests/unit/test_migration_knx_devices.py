"""Unit tests for KNX device schema migration (#647)."""

from __future__ import annotations

import pytest

from obs.db.database import Database, _MIGRATION_V34


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
