"""Regression tests for ETS group hierarchy DataPoint auto-linking (#1060)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from obs.api.v1.services.hierarchy_import import EtsImportRequest, create_ets_hierarchy
from obs.db.database import Database


@asynccontextmanager
async def _database(path: Path):
    db = Database(str(path))
    await db.connect()
    try:
        yield db
    finally:
        await db.disconnect()


async def _insert_group_address(
    db: Database,
    address: str,
    *,
    name: str = "Light",
    main_group_name: str = "Main",
    mid_group_name: str = "Middle",
) -> None:
    await db.execute_and_commit(
        """INSERT INTO knx_group_addresses
           (address, name, description, dpt, main_group_name, mid_group_name, imported_at)
           VALUES (?, ?, '', 'DPT1.001', ?, ?, '2026-07-22T00:00:00+00:00')""",
        (address, name, main_group_name, mid_group_name),
    )


async def _insert_knx_binding(db: Database, datapoint_id: str, binding_id: str, address: str) -> None:
    now = "2026-07-22T00:00:00+00:00"
    await db.execute_and_commit(
        """INSERT INTO datapoints
           (id, name, data_type, unit, tags, mqtt_topic, created_at, updated_at)
           VALUES (?, ?, 'BOOL', NULL, '[]', ?, ?, ?)""",
        (datapoint_id, datapoint_id, f"obs/test/{datapoint_id}", now, now),
    )
    await _insert_binding(db, datapoint_id, binding_id, address)


async def _insert_binding(db: Database, datapoint_id: str, binding_id: str, address: str) -> None:
    now = "2026-07-22T00:00:00+00:00"
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, 'KNX', 'BOTH', ?, 1, ?, ?)""",
        (binding_id, datapoint_id, json.dumps({"group_address": address}), now, now),
    )


@pytest.mark.parametrize(
    ("mode", "expected_node_name"),
    [("groups", "Light"), ("flat", "Light"), ("mid", "Middle")],
)
@pytest.mark.asyncio
async def test_group_modes_auto_link_unique_datapoint_at_expected_node(tmp_path, mode, expected_node_name):
    async with _database(tmp_path / f"{mode}.db") as db:
        await _insert_group_address(db, "1/2/3")
        await _insert_knx_binding(db, "dp-1", "binding-1", "1/2/3")

        result = await create_ets_hierarchy(
            db,
            EtsImportRequest(tree_name=f"ETS {mode}", mode=mode, auto_link=True),
        )

        links = await db.fetchall(
            """SELECT hdl.datapoint_id, hn.name
               FROM hierarchy_datapoint_links hdl
               JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
               WHERE hn.tree_id = ?""",
            (result.tree_id,),
        )
        assert result.links_created == 1
        assert [(row["datapoint_id"], row["name"]) for row in links] == [("dp-1", expected_node_name)]


@pytest.mark.asyncio
async def test_group_mode_auto_link_false_creates_no_links(tmp_path):
    async with _database(tmp_path / "disabled.db") as db:
        await _insert_group_address(db, "1/2/3")
        await _insert_knx_binding(db, "dp-1", "binding-1", "1/2/3")

        result = await create_ets_hierarchy(
            db,
            EtsImportRequest(tree_name="ETS groups", mode="groups", auto_link=False),
        )

        links = await db.fetchall("SELECT id FROM hierarchy_datapoint_links")
        assert result.links_created == 0
        assert links == []


@pytest.mark.asyncio
async def test_group_mode_links_only_unique_bindings_in_current_import_scope(tmp_path):
    async with _database(tmp_path / "scope.db") as db:
        await _insert_group_address(db, "1/2/3", name="Unique")
        await _insert_group_address(db, "1/2/4", name="Ambiguous")
        await _insert_group_address(db, "1/2/5", name="Missing")
        await _insert_group_address(db, "9/9/9", name="Out of scope", main_group_name="Other", mid_group_name="Other")
        await _insert_knx_binding(db, "dp-unique", "binding-unique", "1/2/3")
        await _insert_knx_binding(db, "dp-a", "binding-a", "1/2/4")
        await _insert_knx_binding(db, "dp-b", "binding-b", "1/2/4")
        await _insert_knx_binding(db, "dp-out", "binding-out", "9/9/9")

        result = await create_ets_hierarchy(
            db,
            EtsImportRequest(
                tree_name="Scoped groups",
                mode="groups",
                auto_link=True,
                group_addresses=["1/2/3", "1/2/4", "1/2/5"],
            ),
        )

        linked_datapoints = await db.fetchall("SELECT datapoint_id FROM hierarchy_datapoint_links ORDER BY datapoint_id")
        node_names = await db.fetchall(
            "SELECT name FROM hierarchy_nodes WHERE tree_id=? ORDER BY name",
            (result.tree_id,),
        )
        assert result.links_created == 1
        assert [row["datapoint_id"] for row in linked_datapoints] == ["dp-unique"]
        assert "Out of scope" not in {row["name"] for row in node_names}


@pytest.mark.asyncio
async def test_mid_mode_deduplicates_same_datapoint_reached_through_multiple_addresses(tmp_path):
    async with _database(tmp_path / "mid-deduplicated.db") as db:
        await _insert_group_address(db, "1/2/3")
        await _insert_group_address(db, "1/2/4")
        await _insert_knx_binding(db, "dp-1", "binding-1", "1/2/3")
        await _insert_binding(db, "dp-1", "binding-2", "1/2/4")

        result = await create_ets_hierarchy(
            db,
            EtsImportRequest(tree_name="ETS mid", mode="mid", auto_link=True),
        )

        links = await db.fetchall("SELECT datapoint_id FROM hierarchy_datapoint_links")
        assert result.links_created == 1
        assert [row["datapoint_id"] for row in links] == ["dp-1"]


@pytest.mark.asyncio
async def test_group_mode_replacement_recreates_single_link(tmp_path):
    async with _database(tmp_path / "replace.db") as db:
        await _insert_group_address(db, "1/2/3")
        await _insert_knx_binding(db, "dp-1", "binding-1", "1/2/3")
        request = EtsImportRequest(
            tree_name="ETS groups",
            mode="groups",
            auto_link=True,
            replace_existing=True,
        )

        first = await create_ets_hierarchy(db, request)
        first_link = await db.fetchone("SELECT id FROM hierarchy_datapoint_links")
        second = await create_ets_hierarchy(db, request)

        trees = await db.fetchall("SELECT id FROM hierarchy_trees WHERE source='ets_import:groups'")
        links = await db.fetchall("SELECT id, datapoint_id FROM hierarchy_datapoint_links")
        assert first.links_created == 1
        assert second.links_created == 1
        assert second.trees_replaced == 1
        assert [row["id"] for row in trees] == [second.tree_id]
        assert len(links) == 1
        assert links[0]["datapoint_id"] == "dp-1"
        assert links[0]["id"] != first_link["id"]
