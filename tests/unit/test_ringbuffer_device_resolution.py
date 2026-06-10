from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obs.api.v1.ringbuffer import FilterCriteria, _build_query_from_filter_criteria, _resolve_device_pas_to_group_addresses
from obs.db.database import Database


@pytest.mark.asyncio
async def test_resolve_device_pas_to_group_addresses_supports_v34_knx_schema():
    db = Database(":memory:")
    await db.connect()
    try:
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO knx_group_addresses (address, name, description, dpt, imported_at, main_group_name, mid_group_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("1/2/3", "Test GA", "", "DPT1.001", now, "", ""),
        )
        await db.execute(
            """INSERT INTO knx_devices (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("dev-1", "1.1.10", "Device 1", "", "", "", "", now),
        )
        await db.execute(
            """INSERT INTO knx_comm_objects (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("co-1", "dev-1", "1", "Obj", "", "", "DPT1.001", now),
        )
        await db.execute(
            "INSERT INTO knx_co_ga_links (comm_object_id, ga_address) VALUES (?, ?)",
            ("co-1", "1/2/3"),
        )
        await db.commit()

        assert await _resolve_device_pas_to_group_addresses(["1.1.10"], db) == ["1/2/3"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_build_query_from_filter_criteria_applies_device_group_address_metadata():
    db = Database(":memory:")
    await db.connect()
    try:
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO knx_group_addresses (address, name, description, dpt, imported_at, main_group_name, mid_group_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2/1/5", "GA 2", "", "DPT5.001", now, "", ""),
        )
        await db.execute(
            """INSERT INTO knx_devices (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("dev-2", "1.1.20", "Device 2", "", "", "", "", now),
        )
        await db.execute(
            """INSERT INTO knx_comm_objects (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("co-2", "dev-2", "2", "Obj2", "", "", "DPT5.001", now),
        )
        await db.execute(
            "INSERT INTO knx_co_ga_links (comm_object_id, ga_address) VALUES (?, ?)",
            ("co-2", "2/1/5"),
        )
        await db.commit()

        query = await _build_query_from_filter_criteria(
            FilterCriteria(devices=["1.1.20"], tags=["heizung"]),
            time_filter=None,
            db=db,
        )

        assert query is not None
        assert query.filters.metadata is not None
        assert query.filters.metadata.tags_any_of == ["heizung"]
        assert query.filters.metadata.group_addresses_any_of == ["2/1/5"]
    finally:
        await db.disconnect()
