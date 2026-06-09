from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from obs.api.v1 import knxproj as knxproj_api
from obs.db.database import Database


def _ga(address: str, name: str = "GA") -> SimpleNamespace:
    return SimpleNamespace(
        address=address,
        name=name,
        description="",
        dpt="DPT1.001",
        main_group_name="Main",
        mid_group_name="Mid",
    )


def _device(identifier: str, pa: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        identifier=identifier,
        individual_address=pa,
        name=name,
        description="",
        manufacturer_name="Acme",
        order_number="ORD-1",
        application="APP-1",
    )


def _co(identifier: str, pa: str, number: int, name: str, dpts: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        identifier=identifier,
        device_address=pa,
        number=number,
        name=name,
        text="",
        function_text="",
        dpts=dpts,
    )


def _co_link(comm_object_id: str, ga: str) -> SimpleNamespace:
    return SimpleNamespace(comm_object_id=comm_object_id, ga_address=ga)


@pytest.mark.asyncio
async def test_import_knxproj_persists_devices_comm_objects_and_links(monkeypatch: pytest.MonkeyPatch):
    db = Database(":memory:")
    await db.connect()
    try:
        monkeypatch.setattr(knxproj_api, "parse_knxproj", lambda *_args, **_kwargs: [_ga("1/2/3")])
        monkeypatch.setattr(knxproj_api, "parse_knxproj_locations", lambda *_args, **_kwargs: ([], []))
        monkeypatch.setattr(knxproj_api, "parse_knxproj_trades", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            knxproj_api,
            "parse_knxproj_devices",
            lambda *_args, **_kwargs: (
                [_device("dev-1", "1.1.10", "Kitchen Actuator")],
                [_co("co-1", "1.1.10", 1, "Switch", ["DPT1.001"])],
                [_co_link("co-1", "1/2/3")],
            ),
        )

        file = UploadFile(filename="project.knxproj", file=BytesIO(b"dummy"))
        result = await knxproj_api.import_knxproj_file(file=file, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.imported == 1

        row = await db.fetchone("SELECT individual_address, name, product_name, product_refid FROM knx_devices WHERE id='dev-1'")
        assert row is not None
        assert row["individual_address"] == "1.1.10"
        assert row["name"] == "Kitchen Actuator"
        assert row["product_name"] == "Acme"
        assert row["product_refid"] == "ORD-1"

        co_row = await db.fetchone("SELECT id, device_id, number, datapoint_type FROM knx_comm_objects WHERE id='co-1'")
        assert co_row is not None
        assert co_row["device_id"] == "dev-1"
        assert co_row["number"] == "1"
        assert co_row["datapoint_type"] == "DPT1.001"

        link_row = await db.fetchone("SELECT comm_object_id, ga_address FROM knx_co_ga_links WHERE comm_object_id='co-1'")
        assert link_row is not None
        assert link_row["ga_address"] == "1/2/3"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_import_knxproj_replaces_device_snapshot_on_reimport(monkeypatch: pytest.MonkeyPatch):
    db = Database(":memory:")
    await db.connect()
    try:
        monkeypatch.setattr(knxproj_api, "parse_knxproj", lambda *_args, **_kwargs: [_ga("1/2/3")])
        monkeypatch.setattr(knxproj_api, "parse_knxproj_locations", lambda *_args, **_kwargs: ([], []))
        monkeypatch.setattr(knxproj_api, "parse_knxproj_trades", lambda *_args, **_kwargs: [])

        state = {"name": "Version A", "co": "co-a"}

        def _parse_devices(*_args, **_kwargs):
            return (
                [_device("dev-1", "1.1.10", state["name"])],
                [_co(state["co"], "1.1.10", 1, "Switch", ["DPT1.001"])],
                [_co_link(state["co"], "1/2/3")],
            )

        monkeypatch.setattr(knxproj_api, "parse_knxproj_devices", _parse_devices)

        file = UploadFile(filename="project.knxproj", file=BytesIO(b"dummy"))
        await knxproj_api.import_knxproj_file(file=file, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        state["name"] = "Version B"
        state["co"] = "co-b"
        file2 = UploadFile(filename="project.knxproj", file=BytesIO(b"dummy-v2"))
        await knxproj_api.import_knxproj_file(file=file2, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        row = await db.fetchone("SELECT name FROM knx_devices WHERE id='dev-1'")
        assert row["name"] == "Version B"

        count_row = await db.fetchone("SELECT COUNT(*) AS n FROM knx_comm_objects")
        assert count_row["n"] == 1
        only_co = await db.fetchone("SELECT id FROM knx_comm_objects")
        assert only_co["id"] == "co-b"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_failed_device_snapshot_rolls_back_before_adapter_import_commit(monkeypatch: pytest.MonkeyPatch):
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
               VALUES ('inst-1', 'KNX', 'knx-main', '{}', 1, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')"""
        )
        await db.execute(
            """INSERT INTO knx_group_addresses (address, name, description, dpt, imported_at)
               VALUES ('1/2/3', 'Existing GA', '', 'DPT1.001', '2024-01-01T00:00:00Z')"""
        )
        await db.execute(
            """INSERT INTO knx_devices
                   (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
               VALUES ('dev-old', '1.1.10', 'Old Snapshot', '', 'Acme', 'OLD', 'APP-OLD', '2024-01-01T00:00:00Z')"""
        )
        await db.execute(
            """INSERT INTO knx_comm_objects
                   (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
               VALUES ('co-old', 'dev-old', '1', 'Old KO', '', '', 'DPT1.001', '2024-01-01T00:00:00Z')"""
        )
        await db.execute("INSERT INTO knx_co_ga_links (comm_object_id, ga_address) VALUES ('co-old', '1/2/3')")
        await db.commit()

        monkeypatch.setattr(knxproj_api, "parse_knxproj", lambda *_args, **_kwargs: [_ga("1/2/3")])
        monkeypatch.setattr(knxproj_api, "parse_knxproj_locations", lambda *_args, **_kwargs: ([], []))
        monkeypatch.setattr(knxproj_api, "parse_knxproj_trades", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            knxproj_api,
            "parse_knxproj_devices",
            lambda *_args, **_kwargs: (
                [
                    _device("dev-new-a", "1.1.10", "New Snapshot A"),
                    _device("dev-new-b", "1.1.10", "New Snapshot B"),
                ],
                [_co("co-new", "1.1.10", 1, "New KO", ["DPT1.001"])],
                [_co_link("co-new", "1/2/3")],
            ),
        )

        file = UploadFile(filename="project.knxproj", file=BytesIO(b"dummy"))
        result = await knxproj_api.import_knxproj_file(
            file=file,
            password=None,
            adapter_name="knx-main",
            direction="SOURCE",
            _user="admin",
            db=db,
        )

        assert result.created == 1
        device = await db.fetchone("SELECT id, name FROM knx_devices WHERE individual_address = '1.1.10'")
        assert device["id"] == "dev-old"
        assert device["name"] == "Old Snapshot"
        link = await db.fetchone("SELECT comm_object_id FROM knx_co_ga_links WHERE ga_address = '1/2/3'")
        assert link["comm_object_id"] == "co-old"
    finally:
        await db.disconnect()
