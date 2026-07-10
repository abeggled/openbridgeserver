from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
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


async def _insert_authz_tree(db: Database) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (now, now),
    )
    await db.executemany(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', NULL, ?, '', 0, NULL, ?, ?)
        """,
        [
            ("allowed-room", "allowed-room", now, now),
            ("blocked-room", "blocked-room", now, now),
        ],
    )
    await db.commit()


async def _insert_knx_instance(db: Database, *, instance_id: str = "knx-main", enabled: bool = True) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, 'KNX', ?, '{}', ?, ?, ?)
        """,
        (instance_id, instance_id, int(enabled), now, now),
    )


async def _insert_scoped_datapoint(
    db: Database,
    *,
    dp_id: str,
    name: str,
    node_id: str,
    ga: str,
    state_ga: str | None = None,
    binding_enabled: bool = True,
    adapter_instance_id: str = "knx-main",
    adapter_type: str = "KNX",
) -> None:
    now = datetime.now(UTC).isoformat()
    config = {"group_address": ga}
    if state_ga:
        config["state_group_address"] = state_ga
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'BOOLEAN', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (dp_id, name, f"dp/{dp_id}/value", now, now),
    )
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{dp_id}", node_id, dp_id, now),
    )
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'SOURCE', ?, ?, ?, ?)
        """,
        (f"binding-{dp_id}", dp_id, adapter_type, adapter_instance_id, json.dumps(config), int(binding_enabled), now, now),
    )


async def _grant_room(db: Database, *, principal_id: str = "alice", node_id: str = "allowed-room") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, 'guest', 'allow')
        """,
        (principal_id, node_id),
    )


async def _prepare_scoped_devices_db() -> Database:
    db = await _prepare_db()
    await _insert_authz_tree(db)
    await _insert_knx_instance(db)
    await _insert_scoped_datapoint(
        db,
        dp_id="00000000-0000-0000-0000-000000000101",
        name="Allowed switch",
        node_id="allowed-room",
        ga="1/2/3",
    )
    await _insert_scoped_datapoint(
        db,
        dp_id="00000000-0000-0000-0000-000000000102",
        name="Blocked status",
        node_id="blocked-room",
        ga="1/2/4",
    )
    await _grant_room(db)
    return db


async def _insert_hierarchy(db: Database) -> tuple[str, str, str]:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        """INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at)
           VALUES ('tree-1', 'Gebäude', '', '', ?, ?)""",
        (now, now),
    )
    await db.executemany(
        """INSERT INTO hierarchy_nodes
           (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES (?, 'tree-1', ?, ?, '', ?, NULL, ?, ?)""",
        [
            ("node-kitchen", None, "Küche", 1, now, now),
            ("node-living", None, "Wohnen", 2, now, now),
        ],
    )
    await db.execute(
        """INSERT INTO hierarchy_device_links (id, node_id, device_id, created_at)
           VALUES ('hdl-1', 'node-kitchen', 'dev-1', ?)""",
        (now,),
    )
    await db.commit()
    return "tree-1", "node-kitchen", "node-living"


@pytest.mark.asyncio
async def test_list_knx_devices_with_filters_and_pagination():
    db = await _prepare_db()
    try:
        result = await knxproj_api.list_knx_devices(
            q="app",
            manufacturer="siemens",
            order_number="",
            hierarchy_node_id="",
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
        assert result.hierarchy_links == []

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
            hierarchy_node_id="",
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
            hierarchy_node_id="",
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
            hierarchy_node_id="",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].order_number == "HS-10"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_filters_by_hierarchy_node():
    db = await _prepare_db()
    try:
        _, kitchen_node_id, _ = await _insert_hierarchy(db)

        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            hierarchy_node_id=kitchen_node_id,
            page=0,
            size=50,
            _user="admin",
            db=db,
        )

        assert result.total == 1
        assert result.items[0].pa == "1.1.1"
        assert result.items[0].hierarchy_links[0].node_id == kitchen_node_id
        assert result.items[0].hierarchy_links[0].tree_name == "Gebäude"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_hierarchy_filter_includes_descendant_nodes():
    db = await _prepare_db()
    try:
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at)
               VALUES ('tree-nested', 'Gebäude', '', '', ?, ?)""",
            (now, now),
        )
        await db.executemany(
            """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
               VALUES (?, 'tree-nested', ?, ?, '', ?, NULL, ?, ?)""",
            [
                ("node-floor", None, "EG", 1, now, now),
                ("node-kitchen", "node-floor", "Küche", 2, now, now),
                ("node-living", None, "Wohnen", 3, now, now),
            ],
        )
        await db.execute(
            """INSERT INTO hierarchy_device_links (id, node_id, device_id, created_at)
               VALUES ('hdl-nested', 'node-kitchen', 'dev-1', ?)""",
            (now,),
        )
        await db.commit()

        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            hierarchy_node_id="node-floor",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )

        assert result.total == 1
        assert result.items[0].pa == "1.1.1"
        assert result.items[0].hierarchy_links[0].node_id == "node-kitchen"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_knx_devices_includes_hierarchy_display_path_metadata():
    db = await _prepare_db()
    try:
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO hierarchy_trees (id, name, description, source, display_depth, created_at, updated_at)
               VALUES ('tree-display', 'Gebäude', '', '', 2, ?, ?)""",
            (now, now),
        )
        await db.executemany(
            """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
               VALUES (?, 'tree-display', ?, ?, '', ?, NULL, ?, ?)""",
            [
                ("node-floor", None, "EG", 1, now, now),
                ("node-kitchen", "node-floor", "Küche", 2, now, now),
            ],
        )
        await db.execute(
            """INSERT INTO hierarchy_device_links (id, node_id, device_id, created_at)
               VALUES ('hdl-display', 'node-kitchen', 'dev-1', ?)""",
            (now,),
        )
        await db.commit()

        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            hierarchy_node_id="node-kitchen",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )

        link = result.items[0].hierarchy_links[0]
        assert link.tree_name == "Gebäude"
        assert link.node_name == "Küche"
        assert link.node_path == ["EG"]
        assert link.display_depth == 2
    finally:
        await db.disconnect()


def test_parse_hierarchy_node_filter_normalizes_csv():
    assert knxproj_api._parse_hierarchy_node_filter(" node-a, node-b,node-a,, ") == ["node-a", "node-b"]
    assert knxproj_api._parse_hierarchy_node_filter("") == []
    assert knxproj_api._parse_hierarchy_node_filter(None) == []


@pytest.mark.asyncio
async def test_load_device_hierarchy_links_returns_empty_for_empty_ids():
    db = await _prepare_db()
    try:
        assert await knxproj_api._load_device_hierarchy_links(db, []) == {}
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_replaces_assignments():
    db = await _prepare_db()
    try:
        _, _, living_node_id = await _insert_hierarchy(db)

        result = await knxproj_api.set_knx_device_hierarchy_links(
            pa="1.1.1",
            body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=[living_node_id]),
            _user="admin",
            db=db,
        )

        assert result.pa == "1.1.1"
        assert [link.node_id for link in result.hierarchy_links] == [living_node_id]

        rows = await db.fetchall("SELECT node_id, device_id FROM hierarchy_device_links ORDER BY node_id")
        assert [(row["node_id"], row["device_id"]) for row in rows] == [(living_node_id, "dev-1")]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_can_clear_assignments():
    db = await _prepare_db()
    try:
        await _insert_hierarchy(db)

        result = await knxproj_api.set_knx_device_hierarchy_links(
            pa="1.1.1",
            body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=[]),
            _user="admin",
            db=db,
        )

        assert result.hierarchy_links == []
        rows = await db.fetchall("SELECT node_id, device_id FROM hierarchy_device_links")
        assert rows == []
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_preserves_non_default_admin_status():
    db = await _prepare_db()
    try:
        _, _, living_node_id = await _insert_hierarchy(db)

        result = await knxproj_api.set_knx_device_hierarchy_links(
            pa="1.1.1",
            body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=[living_node_id]),
            _user="owner-user",
            db=db,
        )

        assert result.pa == "1.1.1"
        assert [link.node_id for link in result.hierarchy_links] == [living_node_id]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_rejects_unknown_node():
    db = await _prepare_db()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.set_knx_device_hierarchy_links(
                pa="1.1.1",
                body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=["missing-node"]),
                _user="admin",
                db=db,
            )

        assert exc_info.value.status_code == 400
        assert "missing-node" in str(exc_info.value.detail)
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_returns_404_for_unknown_device():
    db = await _prepare_db()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.set_knx_device_hierarchy_links(
                pa="9.9.9",
                body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=[]),
                _user="admin",
                db=db,
            )

        assert exc_info.value.status_code == 404
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_set_knx_device_hierarchy_links_without_knx_device_schema(monkeypatch: pytest.MonkeyPatch):
    db = await _prepare_db()

    async def _schema_not_ready(_db):
        return False

    monkeypatch.setattr(knxproj_api, "_knx_device_schema_ready", _schema_not_ready)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.set_knx_device_hierarchy_links(
                pa="1.1.1",
                body=knxproj_api.KnxDeviceHierarchyLinksIn(node_ids=[]),
                _user="admin",
                db=db,
            )

        assert exc_info.value.status_code == 404
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_list_knx_devices_only_returns_devices_with_readable_group_address():
    db = await _prepare_scoped_devices_db()
    try:
        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            hierarchy_node_id="",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert result.total == 2
        assert [item.pa for item in result.items] == ["1.1.1", "1.1.2"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_get_knx_device_hides_out_of_scope_comm_object_links():
    db = await _prepare_scoped_devices_db()
    try:
        now = datetime.now(UTC).isoformat()
        await db.executemany(
            "INSERT INTO hierarchy_device_links (id, node_id, device_id, created_at) VALUES (?, ?, 'dev-1', ?)",
            [
                ("hdl-allowed", "allowed-room", now),
                ("hdl-blocked", "blocked-room", now),
            ],
        )
        await db.commit()

        result = await knxproj_api.get_knx_device(
            pa="1.1.1",
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert result.pa == "1.1.1"
        assert [co.id for co in result.comm_objects] == ["co-1"]
        assert result.comm_objects[0].ga_addresses == ["1/2/3"]
        assert [link.node_id for link in result.hierarchy_links] == ["allowed-room"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_get_knx_device_returns_404_when_device_has_no_readable_group_address():
    db = await _prepare_scoped_devices_db()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await knxproj_api.get_knx_device(
                pa="1.1.3",
                _user=Principal(subject="alice", type="user", is_admin=False),
                db=db,
            )

        assert exc_info.value.status_code == 404
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_group_address_device_lookup_requires_readable_group_address():
    db = await _prepare_scoped_devices_db()
    try:
        allowed = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/3",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )
        blocked = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/4",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert [item.pa for item in allowed.items] == ["1.1.1", "1.1.2"]
        assert allowed.total == 2
        assert blocked.items == []
        assert blocked.total == 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_scope_includes_state_group_address():
    db = await _prepare_db()
    try:
        await _insert_authz_tree(db)
        await _insert_knx_instance(db)
        await _insert_scoped_datapoint(
            db,
            dp_id="00000000-0000-0000-0000-000000000201",
            name="Allowed state",
            node_id="allowed-room",
            ga="9/9/9",
            state_ga="1/2/4",
        )
        await _grant_room(db)

        result = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/4",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert [item.pa for item in result.items] == ["1.1.1"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_scope_matches_legacy_lowercase_knx_bindings():
    db = await _prepare_db()
    try:
        await _insert_authz_tree(db)
        await _insert_knx_instance(db)
        await _insert_scoped_datapoint(
            db,
            dp_id="00000000-0000-0000-0000-000000000202",
            name="Allowed legacy binding",
            node_id="allowed-room",
            ga="1/2/3",
            adapter_type="knx",
        )
        await _grant_room(db)

        result = await knxproj_api.list_knx_devices_for_group_address(
            ga="1/2/3",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert [item.pa for item in result.items] == ["1.1.1", "1.1.2"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_non_admin_scope_ignores_disabled_knx_bindings_and_instances():
    db = await _prepare_db()
    try:
        await _insert_authz_tree(db)
        await _insert_knx_instance(db, instance_id="knx-enabled", enabled=True)
        await _insert_knx_instance(db, instance_id="knx-disabled", enabled=False)
        await _insert_scoped_datapoint(
            db,
            dp_id="00000000-0000-0000-0000-000000000301",
            name="Disabled binding",
            node_id="allowed-room",
            ga="1/2/3",
            binding_enabled=False,
            adapter_instance_id="knx-enabled",
        )
        await _insert_scoped_datapoint(
            db,
            dp_id="00000000-0000-0000-0000-000000000302",
            name="Disabled instance",
            node_id="allowed-room",
            ga="1/2/4",
            adapter_instance_id="knx-disabled",
        )
        await _grant_room(db)

        result = await knxproj_api.list_knx_devices(
            q="",
            manufacturer="",
            order_number="",
            page=0,
            size=50,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

        assert result.items == []
        assert result.total == 0
    finally:
        await db.disconnect()
