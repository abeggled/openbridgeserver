"""Config export/import carries the KNX project's style and the V56 merge notes (#1296).

Calls the API functions directly on a real database, so a mutation of the
import (style or notes dropped) also fails without the integration suite.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import config as config_api
from obs.db.database import Database

NOTE = {"address": "1/0/234", "spelling": "1/234", "field": "description", "kept": "neu", "dropped": "alt"}


class _Registry:
    def __init__(self):
        self._points = {}
        self._values = {}
        self.external_write_lock = asyncio.Lock()

    def all(self):
        return []

    def get(self, _dp_id):
        return None

    async def load_from_db(self):
        pass


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config_api, "get_registry", lambda: _Registry())
    database = Database(str(tmp_path / "obs.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _export(db):
    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value = MagicMock(glob=MagicMock(return_value=[]))
        return await config_api.export_config(_user="admin", db=db)


async def _import(db, **fields):
    fields.setdefault("bindings", [])
    body = config_api.ConfigExport(obs_version="5", exported_at="2026-01-01T00:00:00", datapoints=[], **fields)
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        return await config_api.import_config(body=body, _user="admin", db=db)


async def _project(db):
    row = await db.fetchone("SELECT group_address_style FROM knx_project WHERE id = 1")
    notes = [dict(r) for r in await db.fetchall("SELECT address, spelling, field, kept, dropped FROM knx_ga_merge_conflicts")]
    return (row["group_address_style"] if row else None), notes


async def test_export_and_import_restore_style_and_notes(db):
    await db.execute_and_commit("INSERT OR REPLACE INTO knx_project (id, group_address_style) VALUES (1, 'TwoLevel')")
    await db.execute_and_commit(
        "INSERT INTO knx_ga_merge_conflicts (address, spelling, field, kept, dropped) VALUES (?, ?, ?, ?, ?)", tuple(NOTE.values())
    )
    export = await _export(db)
    assert (export.knx_group_address_style, [n.model_dump() for n in export.knx_ga_merge_conflicts]) == ("TwoLevel", [NOTE])

    await db.execute_and_commit("DELETE FROM knx_project")
    await db.execute_and_commit("DELETE FROM knx_ga_merge_conflicts")
    result = await _import(db, knx_group_address_style="TwoLevel", knx_ga_merge_conflicts=[{**NOTE, "address": "1/234"}])
    assert result.errors == []
    assert await _project(db) == ("TwoLevel", [NOTE])


async def test_import_reports_unknown_style_and_invalid_note_and_keeps_the_stored_style(db):
    await db.execute_and_commit("INSERT OR REPLACE INTO knx_project (id, group_address_style) VALUES (1, 'Free')")
    result = await _import(db, knx_group_address_style="FourLevel", knx_ga_merge_conflicts=[{**NOTE, "address": "1/2/x"}])
    assert [("FourLevel" in e, "1/2/x" in e) for e in result.errors] == [(True, False), (False, True)]
    assert await _project(db) == ("Free", [])

    result = await _import(db)  # an export of an older version: no style, no notes
    assert result.errors == []
    assert await _project(db) == ("Free", [])


async def test_export_without_project_has_no_style(db):
    await db.execute_and_commit("DELETE FROM knx_project")
    export = await _export(db)
    assert (export.knx_group_address_style, export.knx_ga_merge_conflicts) == (None, [])


def _gas(*addresses):
    return [{"address": address, "name": "GA", "description": "", "dpt": None} for address in addresses]


@pytest.mark.parametrize(
    ("addresses", "stored", "expected"),
    [
        (("1/234", "1/235", "1/2/3", "1/x"), "ThreeLevel", "TwoLevel"),  # majority of the raw texts; invalid ones do not count
        (("2282", "2283"), "ThreeLevel", "Free"),
        (("1/234", "1/2/3"), "Free", "Free"),  # a tie keeps the stored style
        ((), "TwoLevel", "TwoLevel"),  # no addresses keep it as well
    ],
)
async def test_import_without_style_derives_it_like_v55(db, addresses, stored, expected):
    """P2 (#1307 review): a backup from before #1296 carries no style; the raw addresses reveal it."""
    await db.execute_and_commit("UPDATE knx_project SET group_address_style = ? WHERE id = 1", (stored,))
    await _import(db, knx_group_addresses=_gas(*addresses))
    assert (await _project(db))[0] == expected


async def test_import_with_style_ignores_the_address_majority(db):
    await _import(db, knx_group_address_style="ThreeLevel", knx_group_addresses=_gas("1/234", "1/235"))
    assert (await _project(db))[0] == "ThreeLevel"


async def test_import_drops_only_an_invalid_feedback_address_of_a_knx_binding(db):
    """P3 (#1307 review): the restore keeps the command binding, as the adapter does at runtime."""
    dp_id, binding_id = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    await db.execute_and_commit(
        "INSERT INTO datapoints (id, name, data_type, mqtt_topic, created_at, updated_at) VALUES (?, 'DP', 'BOOLEAN', 't', 'x', 'x')", (dp_id,)
    )
    binding = {
        "id": binding_id,
        "datapoint_id": dp_id,
        "adapter_type": "KNX",
        "direction": "DEST",
        "config": {"group_address": "1/235", "state_group_address": "-"},
        "enabled": True,
    }
    result = await _import(db, bindings=[binding])
    assert result.bindings_created == 1
    assert len(result.errors) == 1 and "state_group_address" in result.errors[0] and "'-'" in result.errors[0]
    row = await db.fetchone("SELECT config FROM adapter_bindings WHERE id = ?", (binding_id,))
    assert json.loads(row["config"]).get("state_group_address") is None
    assert json.loads(row["config"])["group_address"] == "1/0/235"


async def test_import_still_skips_a_knx_binding_with_an_invalid_command_address(db):
    dp_id = "33333333-3333-3333-3333-333333333333"
    await db.execute_and_commit(
        "INSERT INTO datapoints (id, name, data_type, mqtt_topic, created_at, updated_at) VALUES (?, 'DP', 'BOOLEAN', 't', 'x', 'x')", (dp_id,)
    )
    binding = {
        "id": "44444444-4444-4444-4444-444444444444",
        "datapoint_id": dp_id,
        "adapter_type": "KNX",
        "direction": "DEST",
        "config": {"group_address": "-"},
        "enabled": True,
    }
    result = await _import(db, bindings=[binding])
    assert result.bindings_created == 0
    assert len(result.errors) == 1 and "'-'" in result.errors[0]
