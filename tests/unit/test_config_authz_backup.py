from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import config as config_api
from obs.core.registry import DataPointRegistry
from obs.db.database import Database
from obs.logic.capabilities import LOGIC_CREATE_CAPABILITY
from obs.models.datapoint import DataPointCreate


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


class _EmptyRegistry:
    _points: dict = {}
    _values: dict = {}

    def all(self) -> list:
        return []


def _registry(db: Database) -> DataPointRegistry:
    return DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=AsyncMock())


def _authz_import(*grants: config_api.ExportedAuthzGrant) -> config_api.ConfigExport:
    return config_api.ConfigExport(
        obs_version="5",
        exported_at="2026-07-13T00:00:00+00:00",
        datapoints=[],
        bindings=[],
        authz_grants=list(grants),
    )


@pytest.mark.asyncio
async def test_config_roundtrip_preserves_user_logic_creation_grant_but_drops_api_key_alias(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    key_id = str(uuid.uuid4())
    now = "2026-07-13T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('alice-id', 'alice', 'hash', 0, ?)",
        (now,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', ?, 'alice', ?)",
        (key_id, f"hash-{key_id}", now),
    )
    await db.executemany(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES (?, ?, 'logic_capability', ?, 'operator', 'allow')""",
        [
            ("user", "alice", LOGIC_CREATE_CAPABILITY),
            ("api_key", key_id, LOGIC_CREATE_CAPABILITY),
        ],
    )
    await db.commit()

    monkeypatch.setattr(config_api, "get_registry", lambda: _EmptyRegistry())
    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value.glob.return_value = []
        exported = await config_api.export_config(_user="admin", db=db)

    assert [(grant.principal_type, grant.principal_id, grant.node_id) for grant in exported.authz_grants] == [
        ("user", "alice", LOGIC_CREATE_CAPABILITY)
    ]

    await db.execute_and_commit("DELETE FROM authz_node_roles")
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(body=exported, _user="admin", db=db)

    assert result.errors == []
    assert result.authz_grants_upserted == 1
    restored = await db.fetchone(
        """SELECT principal_type, principal_id, role FROM authz_node_roles
           WHERE node_type='logic_capability' AND node_id=?""",
        (LOGIC_CREATE_CAPABILITY,),
    )
    assert (restored["principal_type"], restored["principal_id"], restored["role"]) == ("user", "alice", "operator")

    rejected_import = _authz_import(
        config_api.ExportedAuthzGrant(
            principal_type="api_key",
            principal_id=key_id,
            node_type="logic_capability",
            node_id=LOGIC_CREATE_CAPABILITY,
            role="owner",
        )
    )
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        rejected = await config_api.import_config(body=rejected_import, _user="admin", db=db)

    assert rejected.authz_grants_upserted == 0
    assert rejected.errors == [
        f"AuthzGrant api_key:{key_id}/logic_capability:{LOGIC_CREATE_CAPABILITY}: Logic graph creation can only be granted to users"
    ]


@pytest.mark.asyncio
async def test_json_export_import_preserves_central_authz_and_api_key_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    username = "backup-user"
    key_id = str(uuid.uuid4())
    now = "2026-07-13T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, 'hash', 0, ?)",
        (str(uuid.uuid4()), username, now),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'backup-key', ?, ?, ?)",
        (key_id, f"hash-{key_id}", username, now),
    )
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES ('tree', 'Tree', '', '', ?, ?)",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES ('room', 'tree', NULL, 'Room', '', 0, NULL, ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect, central_control)
           VALUES ('user', ?, 'hierarchy', 'room', 'operator', 'deny', 1)""",
        (username,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capability_sets (key_id, revision) VALUES (?, 7)",
        (key_id,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capabilities (key_id, capability) VALUES (?, 'datapoint.metadata.write')",
        (key_id,),
    )

    registry = _EmptyRegistry()
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)
    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value.glob.return_value = []
        exported = await config_api.export_config(_user="admin", db=db)

    assert [grant.model_dump() for grant in exported.authz_grants] == [
        {
            "principal_type": "user",
            "principal_id": username,
            "node_type": "hierarchy",
            "node_id": "room",
            "role": "operator",
            "effect": "deny",
            "central_control": True,
        }
    ]
    assert [capability_set.model_dump() for capability_set in exported.api_key_capability_sets] == [
        {
            "key_id": key_id,
            "revision": 7,
            "capabilities": ["datapoint.metadata.write"],
        }
    ]

    await db.execute_and_commit("DELETE FROM authz_node_roles")
    await db.execute_and_commit("DELETE FROM api_key_capabilities")
    await db.execute_and_commit("DELETE FROM api_key_capability_sets")

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.logic.manager.get_logic_manager") as logic_manager,
    ):
        logic_manager.return_value.reload = AsyncMock()
        result = await config_api.import_config(body=exported, _user="admin", db=db)

    assert result.errors == []
    assert result.authz_grants_upserted == 1
    assert result.api_key_capability_sets_upserted == 1
    restored_grant = await db.fetchone(
        """SELECT role, effect, central_control FROM authz_node_roles
           WHERE principal_type='user' AND principal_id=? AND node_type='hierarchy' AND node_id='room'""",
        (username,),
    )
    assert (restored_grant["role"], restored_grant["effect"], restored_grant["central_control"]) == (
        "operator",
        "deny",
        1,
    )
    restored_set = await db.fetchone("SELECT revision FROM api_key_capability_sets WHERE key_id=?", (key_id,))
    assert restored_set["revision"] == 7
    restored_capabilities = await db.fetchall("SELECT capability FROM api_key_capabilities WHERE key_id=?", (key_id,))
    assert [row["capability"] for row in restored_capabilities] == ["datapoint.metadata.write"]


@pytest.mark.asyncio
async def test_config_roundtrip_preserves_resource_control_classes(monkeypatch, db: Database) -> None:
    now = "2026-07-13T00:00:00+00:00"
    registry = _registry(db)
    datapoint = await registry.create(DataPointCreate(name="Plant", control_class="central_plant"))
    await db.execute_and_commit(
        """INSERT INTO logic_graphs
               (id, name, description, enabled, flow_data, control_class, created_at, updated_at)
           VALUES ('central-graph', 'Central', '', 1, '{"nodes":[],"edges":[]}', 'central_plant', ?, ?)""",
        (now, now),
    )
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)
    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value.glob.return_value = []
        exported = await config_api.export_config(_user="admin", db=db)

    assert exported.datapoints[0].control_class == "central_plant"
    assert exported.logic_graphs[0].control_class == "central_plant"

    datapoint.control_class = "room_local"
    await db.execute_and_commit("UPDATE datapoints SET control_class='room_local'")
    await db.execute_and_commit("UPDATE logic_graphs SET control_class='room_local'")
    logic_manager = MagicMock()
    logic_manager.reload = AsyncMock()
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.logic.manager.get_logic_manager", return_value=logic_manager),
    ):
        result = await config_api.import_config(body=exported, _user="admin", db=db)

    assert result.errors == []
    assert registry.get(datapoint.id).control_class == "central_plant"
    assert (await db.fetchone("SELECT control_class FROM datapoints WHERE id=?", (str(datapoint.id),)))["control_class"] == "central_plant"
    assert (await db.fetchone("SELECT control_class FROM logic_graphs WHERE id='central-graph'"))["control_class"] == "central_plant"


@pytest.mark.asyncio
async def test_export_omits_orphan_grant_targets_and_preserves_valid_principals(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    now = "2026-07-13T00:00:00+00:00"
    key_id = str(uuid.uuid4())
    missing_key_id = str(uuid.uuid4())
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('alice', 'alice', 'hash', 0, ?)",
        (now,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'hash', 'alice', ?)",
        (key_id, now),
    )
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES ('tree', 'Tree', '', '', ?, ?)",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES ('hierarchy-valid', 'tree', NULL, 'Room', '', 0, NULL, ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO datapoints
               (id, name, data_type, tags, mqtt_topic, created_at, updated_at)
           VALUES ('datapoint-valid', 'Datapoint', 'FLOAT', '[]', 'test/datapoint', ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO logic_graphs
               (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES ('logic-valid', 'Logic', '', 0, '{"nodes":[],"edges":[]}', ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO visu_nodes
               (id, parent_id, name, type, page_config, created_at, updated_at)
           VALUES ('visu-valid', NULL, 'Page', 'PAGE', '{"grid_cols":12,"grid_row_height":80,"background":null,"widgets":[]}', ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO ringbuffer_filtersets
               (id, name, filter_json, created_at, updated_at)
           VALUES ('filterset-valid', 'Filter', '{}', ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO adapter_instances
               (id, adapter_type, name, config, enabled, created_at, updated_at)
           VALUES ('adapter-valid', 'MQTT', 'Adapter', '{}', 0, ?, ?)""",
        (now, now),
    )

    table_backed_types = {
        "hierarchy": "hierarchy-valid",
        "datapoint": "datapoint-valid",
        "logic_graph": "logic-valid",
        "visu_page": "visu-valid",
        "ringbuffer_filterset": "filterset-valid",
        "adapter_instance": "adapter-valid",
    }
    grants = [
        ("user", "alice", node_type, node_id, "guest", "allow")
        for node_type, node_id in table_backed_types.items()
        if node_type != "adapter_instance"
    ]
    grants.append(("api_key", f"api_key:{key_id.upper()}", "adapter_instance", "adapter-valid", "guest", "allow"))
    grants.extend(("user", "alice", node_type, f"{node_type}-orphan", "owner", "deny") for node_type in table_backed_types)
    grants.extend(
        [
            ("api_key", key_id, "logic_graph", "logic-valid", "resident", "allow"),
            ("api_key", f"api_key:{missing_key_id}", "visu_page", "visu-valid", "owner", "allow"),
            ("user", "retired", "hierarchy", "hierarchy-valid", "owner", "allow"),
            ("user", "Alice", "datapoint", "datapoint-valid", "owner", "allow"),
            ("user", "alice", "logic_capability", "http_request", "operator", "allow"),
            ("user", "alice", "logic_capability", "invalid_capability", "owner", "allow"),
            ("user", "alice", "unknown", "target", "owner", "allow"),
        ],
    )
    await db.executemany(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES (?, ?, ?, ?, ?, ?)""",
        grants,
    )
    await db.commit()
    monkeypatch.setattr(config_api, "get_registry", _EmptyRegistry)

    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value.glob.return_value = []
        exported = await config_api.export_config(_user="admin", db=db)

    exported_grants = {
        (grant.principal_type, grant.principal_id, grant.node_type, grant.node_id, grant.role, grant.effect) for grant in exported.authz_grants
    }
    expected = {
        ("user", "alice", node_type, node_id, "guest", "allow")
        for node_type, node_id in table_backed_types.items()
        if node_type != "adapter_instance"
    }
    expected.update(
        {
            ("api_key", f"api_key:{key_id.upper()}", "adapter_instance", "adapter-valid", "guest", "allow"),
            ("api_key", key_id, "logic_graph", "logic-valid", "resident", "allow"),
            ("user", "alice", "logic_capability", "http_request", "operator", "allow"),
        },
    )
    assert exported_grants == expected


@pytest.mark.asyncio
async def test_import_canonicalizes_legacy_prefixed_api_key_grant(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    key_id = str(uuid.uuid4())
    now = "2026-07-13T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('owner', 'owner', 'hash', 0, ?)",
        (now,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'hash', 'owner', ?)",
        (key_id, now),
    )
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES ('tree', 'Tree', '', '', ?, ?)",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES ('room', 'tree', NULL, 'Room', '', 0, NULL, ?, ?)""",
        (now, now),
    )
    monkeypatch.setattr(config_api, "get_registry", _EmptyRegistry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(
            body=_authz_import(
                config_api.ExportedAuthzGrant(
                    principal_type="api_key",
                    principal_id=f"api_key:{key_id.upper()}",
                    node_type="hierarchy",
                    node_id="room",
                    role="guest",
                ),
            ),
            _user="admin",
            db=db,
        )

    assert result.errors == []
    assert result.authz_grants_upserted == 1
    restored = await db.fetchone("SELECT principal_id, node_type, node_id FROM authz_node_roles")
    assert dict(restored) == {"principal_id": key_id, "node_type": "hierarchy", "node_id": "room"}


@pytest.mark.asyncio
async def test_import_skips_missing_and_unknown_grant_targets_but_keeps_valid_grant(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    now = "2026-07-13T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('alice', 'alice', 'hash', 0, ?)",
        (now,),
    )
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES ('tree', 'Tree', '', '', ?, ?)",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES ('room', 'tree', NULL, 'Room', '', 0, NULL, ?, ?)""",
        (now, now),
    )
    monkeypatch.setattr(config_api, "get_registry", _EmptyRegistry)
    missing_table_targets = [
        config_api.ExportedAuthzGrant(
            principal_type="user",
            principal_id="alice",
            node_type=node_type,
            node_id="missing",
            role="owner",
        )
        for node_type in (
            "hierarchy",
            "datapoint",
            "logic_graph",
            "visu_page",
            "ringbuffer_filterset",
            "adapter_instance",
        )
    ]

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(
            body=_authz_import(
                config_api.ExportedAuthzGrant(
                    principal_type="user",
                    principal_id="alice",
                    node_type="hierarchy",
                    node_id="room",
                    role="guest",
                ),
                config_api.ExportedAuthzGrant(
                    principal_type="user",
                    principal_id="alice",
                    node_type="logic_capability",
                    node_id="http_request",
                    role="operator",
                ),
                config_api.ExportedAuthzGrant(
                    principal_type="user",
                    principal_id="alice",
                    node_type="logic_capability",
                    node_id="invalid_capability",
                    role="owner",
                ),
                config_api.ExportedAuthzGrant(
                    principal_type="user",
                    principal_id="alice",
                    node_type="unknown",
                    node_id="target",
                    role="owner",
                ),
                *missing_table_targets,
            ),
            _user="admin",
            db=db,
        )

    assert result.authz_grants_upserted == 2
    assert len(result.errors) == 8
    for node_type in (
        "hierarchy",
        "datapoint",
        "logic_graph",
        "visu_page",
        "ringbuffer_filterset",
        "adapter_instance",
    ):
        assert any(f"Unknown {node_type} grant targets: missing" in error for error in result.errors)
    assert any("Unknown logic_capability grant targets: invalid_capability" in error for error in result.errors)
    assert any("node_type" in error and "unknown" in error for error in result.errors)
    rows = await db.fetchall("SELECT node_type, node_id FROM authz_node_roles ORDER BY node_type")
    assert [dict(row) for row in rows] == [
        {"node_type": "hierarchy", "node_id": "room"},
        {"node_type": "logic_capability", "node_id": "http_request"},
    ]


@pytest.mark.asyncio
async def test_factory_reset_clears_reset_resource_grants_while_preserving_filtersets(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    tmp_path,
) -> None:
    await db.executemany(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', ?, ?, 'owner', 'allow')
        """,
        [
            ("logic_graph", "graph"),
            ("hierarchy", "room"),
            ("datapoint", "datapoint"),
            ("adapter_instance", "adapter"),
            ("visu_page", "page"),
            ("ringbuffer_filterset", "filterset"),
        ],
    )
    await db.commit()

    monkeypatch.setattr(config_api, "get_registry", _EmptyRegistry)
    logic_manager = MagicMock()
    logic_manager.reload = AsyncMock()
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager", return_value=logic_manager),
        patch("obs.api.v1.icons._icons_dir", return_value=tmp_path),
    ):
        clear_result = await config_api.clear_logic(_admin="admin", db=db)
        grants_after_clear = await db.fetchall("SELECT node_type FROM authz_node_roles ORDER BY node_type")

        reset_result = await config_api.factory_reset(_admin="admin", db=db)
        grants_after_reset = await db.fetchall("SELECT node_type FROM authz_node_roles ORDER BY node_type")

    assert clear_result.errors == []
    assert [row["node_type"] for row in grants_after_clear] == [
        "adapter_instance",
        "datapoint",
        "hierarchy",
        "ringbuffer_filterset",
        "visu_page",
    ]
    assert reset_result.errors == []
    assert [row["node_type"] for row in grants_after_reset] == ["ringbuffer_filterset"]
