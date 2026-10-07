"""Config export/import carries the KNX project's style and the V56 merge notes (#1296).

Calls the API functions directly on a real database, so a mutation of the
import (style or notes dropped) also fails without the integration suite.
"""

from __future__ import annotations

import asyncio
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
    body = config_api.ConfigExport(obs_version="5", exported_at="2026-01-01T00:00:00", datapoints=[], bindings=[], **fields)
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
