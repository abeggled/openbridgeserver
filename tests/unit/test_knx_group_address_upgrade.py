"""Seam S4 (#1296): an installation created before #1296 keeps working after the upgrade.

The fixtures ``tests/fixtures/knx_legacy_<style>.json`` were produced with the code
of b765f253 (``tools/knx_legacy_fixture.py``): a two-level and a free project
imported with a KNX instance, the stored rows in the project's notation, and
what the read endpoints returned at that time ("before").

Each test loads those rows into a database at schema V54, starts it with the
current code (which runs the pending migrations, as an upgrade does) and checks
the read endpoints — GA→devices, device→datapoints, datapoint KNX context —
against "before".
"""

from __future__ import annotations

import io
import json
import pathlib
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import UploadFile

from obs.api.v1 import knxproj as knxproj_api
from obs.api.v1.services.knx_traceability import build_datapoint_knx_context
from obs.db import database
from obs.db.database import Database
from tests.knx_group_address_invariant import non_internal_group_addresses
from tests.knxproj_style_variants import CO_SWITCH_RAW, DEVICE_PA, FUNCTION_RAW, INTERNAL, NOTATION, knxproj_in_style

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"
LEGACY_STYLES = ("TwoLevel", "Free")
LAST_PRE_1296_VERSION = 54
INSERT_ORDER = (
    "knx_group_addresses",
    "knx_devices",
    "knx_comm_objects",
    "knx_co_ga_links",
    "knx_locations",
    "knx_functions",
    "knx_function_ga_links",
    "knx_space_device_links",
    "adapter_instances",
    "datapoints",
    "adapter_bindings",
)


def _fixture(style: str) -> dict:
    return json.loads((FIXTURES / f"knx_legacy_{style.lower()}.json").read_text(encoding="utf-8"))


async def _insert_rows(db: Database, rows: dict[str, list[dict]]) -> None:
    for table in INSERT_ORDER:
        for row in rows[table]:
            columns = ",".join(row)
            await db.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    await db.commit()


@asynccontextmanager
async def _upgraded(monkeypatch, tmp_path, data: dict, extra_rows: dict[str, list[dict]] | None = None) -> AsyncIterator[Database]:
    """Create the legacy database at schema V54, then open it with the current code.

    Every connection is closed again, also when the upgrade itself fails —
    an open aiosqlite connection keeps the pytest process alive forever.
    """
    path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(database, "MIGRATIONS", [m for m in database.MIGRATIONS if m[0] <= LAST_PRE_1296_VERSION])
    legacy = Database(path)
    try:
        await legacy.connect()
        await _insert_rows(legacy, data["rows"])
        if extra_rows:
            await _insert_rows(legacy, {table: extra_rows.get(table, []) for table in INSERT_ORDER})
    finally:
        await legacy.disconnect()
        monkeypatch.undo()
    db = Database(path)
    try:
        await db.connect()
        yield db
    finally:
        await db.disconnect()


async def _observe(db: Database, data: dict, ga_text: str) -> dict:
    devices = await knxproj_api.list_knx_devices_for_group_address(ga=ga_text, page=0, size=50, _user="admin", db=db)
    device_dps = await knxproj_api.get_knx_device_datapoints(pa=DEVICE_PA, _user="admin", db=db)
    contexts = [await build_datapoint_knx_context(uuid.UUID(row["datapoint_id"]), db) for row in data["rows"]["adapter_bindings"]]
    return {
        "devices_for_switch_ga": [item.pa for item in devices.items],
        "device_datapoints": len(device_dps.datapoints),
        "contexts_with_name": sum(1 for ctx in contexts for ga in ctx.group_addresses if ga.name),
        "contexts_with_device": sum(1 for ctx in contexts for ga in ctx.group_addresses if ga.devices),
        "bindings": len(await db.fetchall("SELECT id FROM adapter_bindings")),
    }


@pytest.mark.parametrize("style", LEGACY_STYLES)
async def test_read_endpoints_answer_as_before_the_upgrade(style, monkeypatch, tmp_path):
    data = _fixture(style)
    async with _upgraded(monkeypatch, tmp_path, data) as db:
        for ga_text in (NOTATION[style][CO_SWITCH_RAW], INTERNAL[CO_SWITCH_RAW]):
            assert await _observe(db, data, ga_text) == data["before"], ga_text


@pytest.mark.parametrize("style", LEGACY_STYLES)
async def test_upgrade_leaves_only_internal_addresses_in_storage(style, monkeypatch, tmp_path):
    data = _fixture(style)
    async with _upgraded(monkeypatch, tmp_path, data) as db:
        assert await non_internal_group_addresses(db) == []
        assert len(await db.fetchall("SELECT address FROM knx_group_addresses")) == len(data["rows"]["knx_group_addresses"])


@pytest.mark.parametrize("style", LEGACY_STYLES)
async def test_raw_and_internal_spelling_of_one_address_are_merged(style, monkeypatch, tmp_path):
    """A reimport with the round-1 code (no migration) left the internal spelling next to the raw one."""
    data = _fixture(style)
    internal = INTERNAL[CO_SWITCH_RAW]
    extra = {
        "knx_group_addresses": [{"address": internal, "name": "", "description": "neu", "dpt": None}],
        "knx_co_ga_links": [{"comm_object_id": data["rows"]["knx_co_ga_links"][0]["comm_object_id"], "ga_address": internal}],
        "knx_function_ga_links": [{"function_id": data["rows"]["knx_function_ga_links"][0]["function_id"], "ga_address": INTERNAL[FUNCTION_RAW]}],
    }
    async with _upgraded(monkeypatch, tmp_path, data, extra) as db:
        rows = await db.fetchall("SELECT * FROM knx_group_addresses WHERE address = ?", (internal,))
        assert len(rows) == 1
        assert rows[0]["name"] == "Licht EG Schalten", "an empty field of the newer row is filled from the legacy row"
        assert rows[0]["description"] == "neu", "a filled field of the newer row wins; the description is never rewritten"
        page = await knxproj_api.list_group_addresses(q="", page=0, size=100, _user="admin", db=db)
        assert [(c.address, c.spelling, c.field, c.kept, c.dropped) for c in page.merge_conflicts] == [
            (internal, NOTATION[style][CO_SWITCH_RAW], "description", "neu", data["rows"]["knx_group_addresses"][0]["description"])
        ]
        assert len(await db.fetchall("SELECT 1 FROM knx_co_ga_links WHERE ga_address = ?", (internal,))) == 1
        assert [row["ga_address"] for row in await db.fetchall("SELECT ga_address FROM knx_function_ga_links")] == [INTERNAL[FUNCTION_RAW]]
        assert await non_internal_group_addresses(db) == []


@pytest.mark.parametrize("style", LEGACY_STYLES)
async def test_reimport_after_the_upgrade_creates_no_duplicates(style, monkeypatch, tmp_path):
    data = _fixture(style)
    async with _upgraded(monkeypatch, tmp_path, data) as db:
        await knxproj_api.import_knxproj_file(
            file=UploadFile(file=io.BytesIO(knxproj_in_style(style)), filename="reimport.knxproj"),
            request=None,
            password=None,
            adapter_name="KNX-Bestand",
            direction="SOURCE",
            hierarchy_modes=None,
            hierarchy_auto_link=True,
            hierarchy_replace_existing=True,
            _user="admin",
            db=db,
        )
        assert len(await db.fetchall("SELECT address FROM knx_group_addresses")) == 500
        legacy_dp_ids = {row["datapoint_id"] for row in data["rows"]["adapter_bindings"]}
        bindings = await db.fetchall("SELECT datapoint_id, config FROM adapter_bindings")
        assert len(bindings) == 500, "legacy bindings are updated, not duplicated"
        assert legacy_dp_ids <= {row["datapoint_id"] for row in bindings}
        assert await non_internal_group_addresses(db) == []


async def test_migration_is_idempotent(monkeypatch, tmp_path):
    data = _fixture("TwoLevel")
    async with _upgraded(monkeypatch, tmp_path, data) as db:
        snapshot = {table: [dict(row) for row in await db.fetchall(f"SELECT * FROM {table} ORDER BY 1, 2")] for table in INSERT_ORDER}
        await database._migration_v56_knx_internal_group_addresses(db.conn)
        await db.commit()
        assert {table: [dict(row) for row in await db.fetchall(f"SELECT * FROM {table} ORDER BY 1, 2")] for table in INSERT_ORDER} == snapshot


async def test_migration_leaves_what_is_no_group_address_untouched(monkeypatch, tmp_path):
    """Edge rows of a legacy database: kept as they are, the rest still migrates."""
    data = _fixture("TwoLevel")
    template = data["rows"]["adapter_bindings"][0]
    odd_configs = {"json-list": "[1, 2]", "garbage-ga": '{"group_address": "x/y", "state_group_address": "1/2/x"}'}
    internal = INTERNAL[CO_SWITCH_RAW]
    extra = {
        # internal spelling already complete: nothing to fill from the raw row
        "knx_group_addresses": [
            {"address": internal, "name": "neu", "description": "neu", "dpt": "DPT1.001", "main_group_name": "neu", "mid_group_name": "neu"}
        ],
        # a function link whose address has no row in knx_group_addresses (no foreign key there)
        "knx_function_ga_links": [{"function_id": "F-orphan", "ga_address": "1/1000"}],
        "adapter_bindings": [{**template, "id": binding_id, "config": config} for binding_id, config in odd_configs.items()],
    }
    async with _upgraded(monkeypatch, tmp_path, data, extra) as db:
        assert dict((await db.fetchall("SELECT * FROM knx_group_addresses WHERE address = ?", (internal,)))[0])["name"] == "neu"
        assert [row["ga_address"] for row in await db.fetchall("SELECT ga_address FROM knx_function_ga_links WHERE function_id = 'F-orphan'")] == [
            "1/3/232"
        ]
        stored = {row["id"]: row["config"] for row in await db.fetchall("SELECT id, config FROM adapter_bindings")}
        assert {binding_id: stored[binding_id] for binding_id in odd_configs} == odd_configs


@pytest.mark.parametrize(
    ("typed", "kept"),
    [(["1/257"], ["1/257"]), (["2305"], ["2305"]), (["1/1/2"], ["__obs_no_matching_group_address__"])],
)
def test_device_scope_keeps_a_group_address_filter_typed_in_any_notation(typed, kept):
    """A filter set combining devices and group addresses: the device's addresses are internal (#1296).

    The typed text is kept; the ringbuffer query expands it to every notation.
    """
    from obs.api.v1.ringbuffer import RingBufferMetadataFilterV2, RingBufferQueryV2, _apply_group_addresses_to_filter_query

    query = RingBufferQueryV2.model_validate({"filters": {"metadata": RingBufferMetadataFilterV2(group_addresses_any_of=typed).model_dump()}})
    scoped = _apply_group_addresses_to_filter_query(query, ["1/1/1"])
    assert scoped.filters.metadata.group_addresses_any_of == kept


@pytest.mark.parametrize(
    ("table", "column", "row"),
    [
        ("knx_group_addresses", "address", {"address": "1/257", "name": "roh"}),
        ("knx_co_ga_links", "ga_address", {"comm_object_id": "1.1.5/O-1_R-1", "ga_address": "2305"}),
        ("knx_function_ga_links", "ga_address", {"function_id": "F-1", "ga_address": "01/1/1"}),
    ],
)
async def test_database_rejects_raw_texts_on_insert_and_update(table, column, row, monkeypatch, tmp_path):
    """The V56 triggers: any write of a non-internal text into a knx_* GA column aborts."""
    data = _fixture("TwoLevel")
    async with _upgraded(monkeypatch, tmp_path, data) as db:
        with pytest.raises(sqlite3.IntegrityError, match="interner Schreibweise"):
            await db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        await db.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="interner Schreibweise"):
            await db.execute(f"UPDATE {table} SET {column} = ?", (row[column],))
        await db.rollback()
        assert await non_internal_group_addresses(db) == []


async def test_two_raw_spellings_of_one_address_merge_without_silent_loss(monkeypatch, tmp_path, caplog):
    """Two legacy spellings of 1/1/1: the project-notation row wins, gaps are filled, conflicts stay visible."""
    data = _fixture("TwoLevel")
    exotic = {"address": "01/257", "name": "AndererName", "description": "", "dpt": "DPST-9-1", "mid_group_name": "Mitte"}
    async with _upgraded(monkeypatch, tmp_path, data, {"knx_group_addresses": [exotic]}) as db:
        [row] = await db.fetchall("SELECT * FROM knx_group_addresses WHERE address = '1/1/1'")
        assert (row["name"], row["dpt"]) == ("Licht EG Schalten", "DPT1.001"), "the row in the project's notation wins"
        assert row["mid_group_name"] == "Mitte", "an empty field is filled from the other spelling"
        assert row["description"] == "Demo 01 - Binaersignale / Schalten / Licht EG Schalten", "user data stays untouched"
        page = await knxproj_api.list_group_addresses(q="", page=0, size=100, _user="admin", db=db)
        assert {(c.address, c.spelling, c.field, c.kept, c.dropped) for c in page.merge_conflicts} == {
            ("1/1/1", "01/257", "name", "Licht EG Schalten", "AndererName"),
            ("1/1/1", "01/257", "dpt", "DPT1.001", "DPST-9-1"),
        }
    assert any("01/257" in record.getMessage() and record.levelname == "WARNING" for record in caplog.records)


async def test_identical_spellings_merge_without_a_note(monkeypatch, tmp_path):
    """The realistic intermediate reimport: the internal row carries the same data, nothing to fill or note."""
    data = _fixture("TwoLevel")
    legacy = next(row for row in data["rows"]["knx_group_addresses"] if row["address"] == NOTATION["TwoLevel"][CO_SWITCH_RAW])
    async with _upgraded(monkeypatch, tmp_path, data, {"knx_group_addresses": [{**legacy, "address": INTERNAL[CO_SWITCH_RAW]}]}) as db:
        [row] = await db.fetchall("SELECT * FROM knx_group_addresses WHERE address = ?", (INTERNAL[CO_SWITCH_RAW],))
        assert {key: row[key] for key in legacy if key != "address"} == {key: value for key, value in legacy.items() if key != "address"}
