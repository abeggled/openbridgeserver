from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from obs.api.v1 import knxproj as knxproj_api
from obs.db.database import Database


async def _prepare_db() -> Database:
    db = Database(":memory:")
    await db.connect()
    await db.commit()

    now = datetime.now(UTC).isoformat()
    await db.executemany(
        """INSERT INTO knx_group_addresses
           (address, name, description, dpt, imported_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("1/2/3", "GA 1", "", "1.001", now),
            ("1/2/4", "GA 2", "", "1.001", now),
        ],
    )
    await db.executemany(
        """INSERT INTO knx_devices
           (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("dev-1", "1.1.1", "Kitchen Switch", "", "Siemens", "5WG1", "APP-KITCHEN", now),
            ("dev-2", "1.1.2", "Living Dimmer", "", "ABB", "LD-200", "APP-LIVING", now),
            ("dev-3", "1.1.3", "Hall Sensor", "", "Siemens", "HS-10", "APP-HALL", now),
        ],
    )

    await db.executemany(
        """INSERT INTO knx_comm_objects
           (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("co-1", "dev-1", "1", "Switch", "", "", "1.001", now),
            ("co-2", "dev-1", "2", "Status", "", "", "1.001", now),
            ("co-3", "dev-2", "1", "Dim", "", "", "5.001", now),
        ],
    )

    await db.executemany(
        "INSERT INTO knx_co_ga_links (comm_object_id, ga_address) VALUES (?, ?)",
        [
            ("co-1", "1/2/3"),
            ("co-2", "1/2/4"),
            ("co-3", "1/2/3"),
        ],
    )
    await db.commit()

    return db


@pytest.mark.asyncio
async def test_list_knx_devices_with_filters_and_pagination():
    db = await _prepare_db()
    try:
        result = await knxproj_api.list_knx_devices(
            q="app",
            manufacturer="siemens",
            order_number="",
            page=0,
            size=1,
            _user="admin",
            db=db,
        )

        assert result.total == 2
        assert result.page == 0
        assert result.size == 1
        assert result.pages == 2
        assert len(result.items) == 1
        assert result.items[0].manufacturer == "Siemens"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_knx_device_by_pa_includes_comm_objects_and_ga_links():
    db = await _prepare_db()
    try:
        result = await knxproj_api.get_knx_device(
            pa="1.1.1",
            _user="admin",
            db=db,
        )

        assert result.pa == "1.1.1"
        assert result.manufacturer == "Siemens"
        assert result.order_number == "5WG1"
        assert result.app_ref == "APP-KITCHEN"

        comm_objects = {co.id: co for co in result.comm_objects}
        assert set(comm_objects.keys()) == {"co-1", "co-2"}
        assert comm_objects["co-1"].ga_addresses == ["1/2/3"]
        assert comm_objects["co-2"].ga_addresses == ["1/2/4"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_knx_devices_for_group_address():
    db = await _prepare_db()
    try:
        result = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/3",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )

        assert result.total == 2
        assert [item.pa for item in result.items] == ["1.1.1", "1.1.2"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_knx_device_by_pa_returns_404_for_unknown_pa():
    db = await _prepare_db()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.get_knx_device(
                pa="9.9.9",
                _user="admin",
                db=db,
            )
        assert exc_info.value.status_code == 404
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_without_knx_device_schema(monkeypatch: pytest.MonkeyPatch):
    db = await _prepare_db()

    async def _schema_not_ready(_db):
        return False

    monkeypatch.setattr(knxproj_api, "_knx_device_schema_ready", _schema_not_ready)
    try:
        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            page=0,
            size=10,
            _user="admin",
            db=db,
        )
        assert result.total == 0
        assert result.items == []
        assert result.pages == 1
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_get_knx_device_without_knx_device_schema(monkeypatch: pytest.MonkeyPatch):
    db = await _prepare_db()

    async def _schema_not_ready(_db):
        return False

    monkeypatch.setattr(knxproj_api, "_knx_device_schema_ready", _schema_not_ready)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.get_knx_device(pa="1.1.1", _user="admin", db=db)
        assert exc_info.value.status_code == 404
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_for_group_address_without_knx_device_schema(monkeypatch: pytest.MonkeyPatch):
    db = await _prepare_db()

    async def _schema_not_ready(_db):
        return False

    monkeypatch.setattr(knxproj_api, "_knx_device_schema_ready", _schema_not_ready)
    try:
        result = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/3",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 0
        assert result.items == []
        assert result.pages == 1
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_manufacturer_filter():
    db = await _prepare_db()
    try:
        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="abb",
            order_number="",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].manufacturer == "ABB"
        assert result.items[0].order_number == "LD-200"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_order_number_filter():
    db = await _prepare_db()
    try:
        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="hs-10",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].order_number == "HS-10"
    finally:
        await db.disconnect()
