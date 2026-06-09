"""Coverage boost — obs/api/v1/config.py, obs/api/v1/history.py,
obs/api/v1/autobackup.py, obs/api/v1/system.py, obs/api/v1/datapoints.py,
obs/core/mqtt_passwd.py.

All tests call production code directly with lightweight stubs — no HTTP
client required.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from obs.api.v1 import config as config_api
from obs.api.v1 import history as history_api
from obs.api.v1 import system as system_api


# ===========================================================================
# Shared stubs
# ===========================================================================


class _Row:
    """Minimal stand-in for aiosqlite.Row with .keys() + __getitem__."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return list(self._data.keys())

    def get(self, key, default=None):
        return self._data.get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


class _DbStub:
    """Minimal async DB stub for config tests."""

    def __init__(self, *, fetchone_result=None, fetchall_results=None):
        self._fetchone = fetchone_result
        self._fetchall = fetchall_results or []
        self.committed: list[tuple] = []
        # Support multiple fetchall calls per test via iterator or list-of-lists
        self._fetchall_iter = (
            iter(fetchall_results) if isinstance(fetchall_results, list) and fetchall_results and isinstance(fetchall_results[0], list) else None
        )
        if self._fetchall_iter is not None:
            self._fetchall_flat = None
        else:
            self._fetchall_flat = list(fetchall_results) if fetchall_results else []

    async def fetchone(self, query, params=()):
        if callable(self._fetchone):
            return self._fetchone(query, params)
        return self._fetchone

    async def fetchall(self, query, params=()):
        if self._fetchall_iter is not None:
            try:
                return next(self._fetchall_iter)
            except StopIteration:
                return []
        return list(self._fetchall_flat)

    async def execute_and_commit(self, query, params=()):
        self.committed.append((query, params))

    async def disconnect(self):
        pass

    async def connect(self):
        pass


class _RegistryStub:
    def __init__(self, dps=None):
        self._dps = dps or []
        self._points = {}
        self._values = {}
        for dp in self._dps:
            self._points[dp.id] = dp

    def all(self):
        return list(self._dps)

    def get(self, dp_id):
        return self._points.get(dp_id)

    def count(self):
        return len(self._dps)

    def get_value(self, dp_id):
        return self._values.get(dp_id)

    async def load_from_db(self):
        pass


def _mk_dp(name="Test DP", data_type="FLOAT"):
    dp_id = uuid.uuid4()
    return SimpleNamespace(
        id=dp_id,
        name=name,
        data_type=data_type,
        unit="°C",
        tags=["sensor"],
        mqtt_alias=None,
        mqtt_topic=f"dp/{dp_id}/value",
        persist_value=True,
        record_history=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


# ===========================================================================
# obs/api/v1/config.py — export_config
# ===========================================================================


@pytest.mark.asyncio
async def test_export_config_empty_db(monkeypatch):
    """export_config with empty registry and DB returns valid ConfigExport."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())

    # All fetchall calls return empty lists; fetchone returns None
    db = _DbStub(fetchone_result=None, fetchall_results=[])

    with patch("obs.api.v1.icons._icons_dir") as mock_icons_dir:
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path
        result = await config_api.export_config(_user="testuser", db=db)

    assert result.obs_version == "5"
    assert result.datapoints == []
    assert result.bindings == []
    assert result.adapter_instances == []
    assert result.knx_group_addresses == []
    assert result.logic_graphs == []
    assert result.visu_nodes == []
    assert result.nav_links == []
    assert result.app_settings == []
    assert result.hierarchy_trees == []
    assert result.hierarchy_nodes == []
    assert result.hierarchy_dp_links == []
    assert result.fa_api_key is None


@pytest.mark.asyncio
async def test_export_config_with_datapoints_and_bindings(monkeypatch):
    """export_config serialises DataPoints from registry and bindings from DB."""
    dp = _mk_dp("Wohnzimmer Temp")
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub([dp]))

    binding_row = _row(
        {
            "id": "bind-1",
            "datapoint_id": str(dp.id),
            "adapter_type": "mqtt",
            "adapter_instance_id": None,
            "direction": "SOURCE",
            "config": '{"topic": "sensors/temp"}',
            "enabled": 1,
            "value_formula": None,
            "send_throttle_ms": None,
            "send_on_change": 0,
            "send_min_delta": None,
            "send_min_delta_pct": None,
        }
    )

    call_count = [0]

    async def _fetchall(query, params=()):
        call_count[0] += 1
        if "adapter_bindings" in query:
            return [binding_row]
        return []

    db = _DbStub()
    db.fetchall = _fetchall

    with patch("obs.api.v1.icons._icons_dir") as mock_icons_dir:
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path
        result = await config_api.export_config(_user="u", db=db)

    assert len(result.datapoints) == 1
    assert result.datapoints[0].name == "Wohnzimmer Temp"
    assert len(result.bindings) == 1
    assert result.bindings[0].adapter_type == "mqtt"


@pytest.mark.asyncio
async def test_export_config_with_fa_key(monkeypatch):
    """export_config includes fa_api_key when present in app_settings."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())

    async def _fetchone(query, params=()):
        if "fontawesome_api_key" in query:
            return _row({"value": "fa-key-abc123"})
        return None

    db = _DbStub()
    db.fetchone = _fetchone

    async def _fetchall(query, params=()):
        return []

    db.fetchall = _fetchall

    with patch("obs.api.v1.icons._icons_dir") as mock_icons_dir:
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path
        result = await config_api.export_config(_user="u", db=db)

    assert result.fa_api_key == "fa-key-abc123"


@pytest.mark.asyncio
async def test_export_config_with_visu_nodes(monkeypatch):
    """export_config correctly assembles visu_nodes with user lists."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())

    node_row = _row(
        {
            "id": "node-1",
            "parent_id": None,
            "name": "Haus",
            "type": "PAGE",
            "node_order": 0,
            "icon": None,
            "access": "public",
            "access_pin": None,
            "page_config": None,
        }
    )
    user_row = _row({"node_id": "node-1", "username": "alice"})

    async def _fetchall(query, params=()):
        if "visu_nodes" in query and "visu_node_users" not in query:
            return [node_row]
        if "visu_node_users" in query:
            return [user_row]
        return []

    db = _DbStub()
    db.fetchall = _fetchall
    db.fetchone = AsyncMock(return_value=None)

    with patch("obs.api.v1.icons._icons_dir") as mock_icons_dir:
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path
        result = await config_api.export_config(_user="u", db=db)

    assert len(result.visu_nodes) == 1
    assert result.visu_nodes[0].id == "node-1"
    assert "alice" in result.visu_nodes[0].users


@pytest.mark.asyncio
async def test_export_config_icons_dir_error_is_swallowed(monkeypatch):
    """export_config swallows exceptions from icons iteration."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None, fetchall_results=[])

    with patch("obs.api.v1.icons._icons_dir") as mock_icons_dir:
        mock_icons_dir.side_effect = RuntimeError("no icons dir")
        result = await config_api.export_config(_user="u", db=db)

    assert result.icons == []


# ===========================================================================
# obs/api/v1/config.py — import_config
# ===========================================================================


def _make_config_export(**kwargs):
    defaults = dict(
        obs_version="5",
        exported_at=datetime.now(UTC).isoformat(),
        datapoints=[],
        bindings=[],
        adapter_instances=[],
        knx_group_addresses=[],
        logic_graphs=[],
        adapter_configs=[],
        icons=[],
        fa_api_key=None,
        visu_nodes=[],
        nav_links=[],
        app_settings=[],
        hierarchy_trees=[],
        hierarchy_nodes=[],
        hierarchy_dp_links=[],
    )
    defaults.update(kwargs)
    return config_api.ConfigExport(**defaults)


@pytest.mark.asyncio
async def test_import_config_empty_body(monkeypatch):
    """import_config with empty body returns zero counts."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    db = _DbStub()

    # Stub out adapter restart and logic reload
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        body = _make_config_export()
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.datapoints_created == 0
    assert result.datapoints_updated == 0
    assert result.bindings_created == 0
    assert result.errors == []


@pytest.mark.asyncio
async def test_import_config_creates_new_datapoint(monkeypatch):
    """import_config creates a new DataPoint when it doesn't exist in registry."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)
    db = _DbStub()

    new_dp_id = str(uuid.uuid4())
    body = _make_config_export(
        datapoints=[
            config_api.ExportedDataPoint(
                id=new_dp_id,
                name="Sensor 1",
                data_type="FLOAT",
                unit="°C",
                tags=["sensor"],
                mqtt_alias=None,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.datapoints_created == 1
    assert result.datapoints_updated == 0
    assert len(db.committed) > 0


@pytest.mark.asyncio
async def test_import_config_updates_existing_datapoint(monkeypatch):
    """import_config calls reg.update when datapoint already exists."""
    dp = _mk_dp("Old Name")
    reg = _RegistryStub([dp])
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    update_called = []

    async def _mock_update(dp_id, upd):
        update_called.append((dp_id, upd))
        return dp

    reg.update = _mock_update

    db = _DbStub()
    body = _make_config_export(
        datapoints=[
            config_api.ExportedDataPoint(
                id=str(dp.id),
                name="New Name",
                data_type="FLOAT",
                unit="°C",
                tags=[],
                mqtt_alias=None,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.datapoints_updated == 1
    assert len(update_called) == 1


@pytest.mark.asyncio
async def test_import_config_upserts_adapter_instances(monkeypatch):
    """import_config upserts adapter instances into DB."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(
        adapter_instances=[
            config_api.ExportedAdapterInstance(
                id=str(uuid.uuid4()),
                adapter_type="knx",
                name="KNX Bus",
                config={"host": "192.168.1.1"},
                enabled=True,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.adapter_instances_upserted == 1


@pytest.mark.asyncio
async def test_import_config_legacy_adapter_configs(monkeypatch):
    """import_config migrates v1 adapter_configs to new instances format."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(
        adapter_configs=[
            config_api.ExportedAdapterConfig(
                adapter_type="mqtt",
                config={"host": "localhost"},
                enabled=True,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.adapter_instances_upserted == 1


@pytest.mark.asyncio
async def test_import_config_creates_binding(monkeypatch):
    """import_config inserts a new binding when it doesn't exist."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())

    # fetchone returns None (binding not found)
    db = _DbStub(fetchone_result=None)

    bind_id = str(uuid.uuid4())
    body = _make_config_export(
        bindings=[
            config_api.ExportedBinding(
                id=bind_id,
                datapoint_id=str(uuid.uuid4()),
                adapter_type="mqtt",
                direction="SOURCE",
                config={"topic": "test"},
                enabled=True,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.bindings_created == 1


@pytest.mark.asyncio
async def test_import_config_updates_existing_binding(monkeypatch):
    """import_config updates an existing binding when found."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())

    bind_id = str(uuid.uuid4())
    # fetchone returns existing row
    db = _DbStub(fetchone_result=_row({"id": bind_id}))

    body = _make_config_export(
        bindings=[
            config_api.ExportedBinding(
                id=bind_id,
                datapoint_id=str(uuid.uuid4()),
                adapter_type="mqtt",
                direction="SOURCE",
                config={"topic": "test"},
                enabled=True,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.bindings_updated == 1


@pytest.mark.asyncio
async def test_import_config_invalid_formula_adds_error(monkeypatch):
    """import_config appends an error for invalid value_formula."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None)

    with patch("obs.core.formula.validate_formula", return_value="Syntax error near X"):
        body = _make_config_export(
            bindings=[
                config_api.ExportedBinding(
                    id=str(uuid.uuid4()),
                    datapoint_id=str(uuid.uuid4()),
                    adapter_type="mqtt",
                    direction="SOURCE",
                    config={},
                    enabled=True,
                    value_formula="x + * y",
                )
            ]
        )

        with (
            patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
            patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
            patch("obs.adapters.registry.get_all_instances", return_value={}),
        ):
            result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.bindings_created == 0
    assert len(result.errors) >= 1


@pytest.mark.asyncio
async def test_import_config_knx_group_addresses(monkeypatch):
    """import_config upserts KNX group addresses."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(
        knx_group_addresses=[
            config_api.ExportedKnxGroupAddress(
                address="1/2/3",
                name="Licht Wohnzimmer",
                description="",
                dpt="1.001",
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.knx_group_addresses_upserted == 1


@pytest.mark.asyncio
async def test_import_config_creates_logic_graph(monkeypatch):
    """import_config inserts a new logic graph."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
    ):
        mock_lm.return_value.reload = AsyncMock()
        lg_id = str(uuid.uuid4())
        body = _make_config_export(
            logic_graphs=[
                config_api.ExportedLogicGraph(
                    id=lg_id,
                    name="Graph 1",
                    description="",
                    enabled=True,
                    flow_data={"nodes": [], "edges": []},
                )
            ]
        )
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.logic_graphs_created == 1


@pytest.mark.asyncio
async def test_import_config_updates_logic_graph(monkeypatch):
    """import_config updates an existing logic graph."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    lg_id = str(uuid.uuid4())
    db = _DbStub(fetchone_result=_row({"id": lg_id}))

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
    ):
        mock_lm.return_value.reload = AsyncMock()
        body = _make_config_export(
            logic_graphs=[
                config_api.ExportedLogicGraph(
                    id=lg_id,
                    name="Graph Updated",
                    description="",
                    enabled=False,
                    flow_data={},
                )
            ]
        )
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.logic_graphs_updated == 1


@pytest.mark.asyncio
async def test_import_config_logic_manager_reload_error_recorded(monkeypatch):
    """import_config records error when logic manager reload fails."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    lg_id = str(uuid.uuid4())
    db = _DbStub(fetchone_result=None)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
    ):
        mock_lm.return_value.reload = AsyncMock(side_effect=RuntimeError("reload boom"))
        body = _make_config_export(logic_graphs=[config_api.ExportedLogicGraph(id=lg_id, name="G", description="", enabled=True, flow_data={})])
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert any("reload" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_import_config_fa_api_key(monkeypatch):
    """import_config stores FA API key in app_settings."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(fa_api_key="test-fa-key-xyz")

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        await config_api.import_config(body=body, _user="u", db=db)

    # committed should contain the FA key upsert
    _ = [c[0] for c in db.committed]
    assert any("icons.fontawesome_api_key" in str(c) for c in db.committed)


@pytest.mark.asyncio
async def test_import_config_nav_links(monkeypatch):
    """import_config upserts nav links."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(
        nav_links=[
            config_api.ExportedNavLink(
                id=str(uuid.uuid4()),
                label="GitHub",
                url="https://github.com",
                icon="github",
                sort_order=1,
                open_new_tab=True,
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.nav_links_upserted == 1


@pytest.mark.asyncio
async def test_import_config_app_settings(monkeypatch):
    """import_config upserts app settings."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(
        app_settings=[
            config_api.ExportedAppSetting(key="timezone", value="Europe/Berlin"),
            config_api.ExportedAppSetting(key="history.plugin", value="sqlite"),
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.app_settings_upserted == 2


@pytest.mark.asyncio
async def test_import_config_hierarchy_trees_and_nodes(monkeypatch):
    """import_config upserts hierarchy trees, nodes and dp links."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None, fetchall_results=[])
    # need fetchall to return empty for hierarchy_nodes lookup
    db.fetchall = AsyncMock(return_value=[])

    tree_id = str(uuid.uuid4())
    node_id = str(uuid.uuid4())
    dp_id = str(uuid.uuid4())

    body = _make_config_export(
        hierarchy_trees=[config_api.ExportedHierarchyTree(id=tree_id, name="Gebäude", description="")],
        hierarchy_nodes=[
            config_api.ExportedHierarchyNode(
                id=node_id,
                tree_id=tree_id,
                parent_id=None,
                name="EG",
                description="",
                node_order=0,
                icon=None,
            )
        ],
        hierarchy_dp_links=[config_api.ExportedHierarchyDpLink(id=str(uuid.uuid4()), node_id=node_id, datapoint_id=dp_id)],
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.hierarchy_upserted >= 3


@pytest.mark.asyncio
async def test_import_config_visu_nodes_topological(monkeypatch):
    """import_config processes visu_nodes in topological order (parent before child)."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None, fetchall_results=[])
    db.fetchall = AsyncMock(return_value=[])

    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())

    body = _make_config_export(
        # Child comes first in list — should still work
        visu_nodes=[
            config_api.ExportedVisuNode(
                id=child_id,
                parent_id=parent_id,
                name="Kindseite",
                type="PAGE",
                node_order=1,
                icon=None,
                access=None,
                access_pin=None,
                page_config=None,
                users=[],
            ),
            config_api.ExportedVisuNode(
                id=parent_id,
                parent_id=None,
                name="Hauptbereich",
                type="AREA",
                node_order=0,
                icon=None,
                access="public",
                access_pin=None,
                page_config=None,
                users=["alice"],
            ),
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.visu_nodes_upserted == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_import_config_visu_node_orphan_gets_error(monkeypatch):
    """import_config records error for visu_node whose parent is never found."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub(fetchone_result=None, fetchall_results=[])
    db.fetchall = AsyncMock(return_value=[])

    body = _make_config_export(
        visu_nodes=[
            config_api.ExportedVisuNode(
                id=str(uuid.uuid4()),
                parent_id="nonexistent-parent",
                name="Waise",
                type="PAGE",
                node_order=0,
                icon=None,
                access=None,
                access_pin=None,
                page_config=None,
                users=[],
            )
        ]
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert any("nonexistent-parent" in e for e in result.errors)


@pytest.mark.asyncio
async def test_import_config_icons(monkeypatch):
    """import_config decodes and writes valid SVG icons."""
    import base64

    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    svg_content = b"<svg><circle/></svg>"
    b64 = base64.b64encode(svg_content).decode()

    mock_icons_dir = MagicMock()
    mock_file = MagicMock()
    mock_icons_dir.__truediv__ = MagicMock(return_value=mock_file)

    body = _make_config_export(icons=[config_api.ExportedIcon(name="test-icon", content_b64=b64)])

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.api.v1.icons._icons_dir", return_value=mock_icons_dir),
        patch("obs.api.v1.icons._safe_name", return_value="test-icon"),
        patch("obs.api.v1.icons._sanitize_svg", return_value=svg_content),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.icons_imported == 1


@pytest.mark.asyncio
async def test_import_config_icon_invalid_svg_adds_error(monkeypatch):
    """import_config adds error for icon with invalid SVG content."""
    import base64

    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export(icons=[config_api.ExportedIcon(name="bad-icon", content_b64=base64.b64encode(b"not svg").decode())])

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.api.v1.icons._icons_dir", return_value=MagicMock()),
        patch("obs.api.v1.icons._safe_name", return_value="bad-icon"),
        patch("obs.api.v1.icons._sanitize_svg", return_value=None),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert result.icons_imported == 0
    assert any("bad-icon" in e for e in result.errors)


@pytest.mark.asyncio
async def test_import_config_adapter_restart_error_recorded(monkeypatch):
    """import_config records error when adapter restart fails."""
    monkeypatch.setattr(config_api, "get_registry", lambda: _RegistryStub())
    db = _DbStub()

    body = _make_config_export()

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(body=body, _user="u", db=db)

    assert any("Adapter restart" in e for e in result.errors)


# ===========================================================================
# obs/api/v1/config.py — factory_reset
# ===========================================================================


@pytest.mark.asyncio
async def test_factory_reset_empty_db(monkeypatch):
    """factory_reset returns zero counts for empty DB."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    db = _DbStub(fetchone_result=_row({"n": 0}))

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
        patch("obs.api.v1.icons._icons_dir") as mock_icons_dir,
    ):
        mock_lm.return_value.reload = AsyncMock()
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path

        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.datapoints_deleted == 0
    assert result.bindings_deleted == 0
    assert result.errors == []


@pytest.mark.asyncio
async def test_factory_reset_counts_rows(monkeypatch):
    """factory_reset reads and returns correct deletion counts."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    table_counts = {
        "logic_graphs": 3,
        "adapter_bindings": 5,
        "datapoints": 10,
        "adapter_instances": 2,
        "knx_group_addresses": 4,
        "visu_nodes": 7,
        "nav_links": 1,
        "hierarchy_trees": 2,
    }

    async def _fetchone(query, params=()):
        for table, count in table_counts.items():
            if table in query:
                return _row({"n": count})
        return _row({"n": 0})

    db = _DbStub()
    db.fetchone = _fetchone

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
        patch("obs.api.v1.icons._icons_dir") as mock_icons_dir,
    ):
        mock_lm.return_value.reload = AsyncMock()
        icons_path = MagicMock()
        icons_path.glob.return_value = []
        mock_icons_dir.return_value = icons_path

        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.datapoints_deleted == 10
    assert result.bindings_deleted == 5
    assert result.logic_graphs_deleted == 3
    committed_sql = [query for query, _params in db.committed]
    assert "DELETE FROM knx_space_device_links" in committed_sql
    assert "DELETE FROM knx_co_ga_links" in committed_sql
    assert "DELETE FROM knx_comm_objects" in committed_sql
    assert "DELETE FROM knx_devices" in committed_sql
    assert committed_sql.index("DELETE FROM knx_devices") < committed_sql.index("DELETE FROM knx_group_addresses")


@pytest.mark.asyncio
async def test_factory_reset_counts_icons(monkeypatch):
    """factory_reset deletes SVG files and counts them."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)
    db = _DbStub(fetchone_result=_row({"n": 0}))

    mock_svg = MagicMock()
    mock_svg.unlink = MagicMock()

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as mock_lm,
        patch("obs.api.v1.icons._icons_dir") as mock_icons_dir,
    ):
        mock_lm.return_value.reload = AsyncMock()
        icons_path = MagicMock()
        icons_path.glob.return_value = [mock_svg, mock_svg]
        mock_icons_dir.return_value = icons_path

        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.icons_deleted == 2


# ===========================================================================
# obs/api/v1/config.py — clear_bindings / clear_datapoints / clear_logic / clear_adapters
# ===========================================================================


@pytest.mark.asyncio
async def test_clear_bindings(monkeypatch):
    """clear_bindings stops adapters, deletes bindings, restarts adapters."""
    db = _DbStub(fetchone_result=_row({"n": 3}))

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.clear_bindings(_admin="admin", db=db)

    assert result.deleted == 3
    assert result.errors == []


@pytest.mark.asyncio
async def test_clear_bindings_error_recorded(monkeypatch):
    """clear_bindings records error if stop_all fails."""
    db = _DbStub(fetchone_result=_row({"n": 0}))

    with patch("obs.adapters.registry.stop_all", AsyncMock(side_effect=RuntimeError("fail"))):
        result = await config_api.clear_bindings(_admin="admin", db=db)

    assert len(result.errors) >= 1


@pytest.mark.asyncio
async def test_clear_datapoints(monkeypatch):
    """clear_datapoints clears registry and DB."""
    reg = _RegistryStub()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    call_count = [0]

    async def _fetchone(query, params=()):
        call_count[0] += 1
        if "adapter_bindings" in query:
            return _row({"n": 2})
        if "datapoints" in query:
            return _row({"n": 5})
        return _row({"n": 0})

    db = _DbStub()
    db.fetchone = _fetchone

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.clear_datapoints(_admin="admin", db=db)

    assert result.deleted == 5
    assert result.bindings_deleted == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_clear_logic(monkeypatch):
    """clear_logic deletes all logic graphs and reloads manager."""
    db = _DbStub(fetchone_result=_row({"n": 4}))

    with patch("obs.logic.manager.get_logic_manager") as mock_lm:
        mock_lm.return_value.reload = AsyncMock()
        result = await config_api.clear_logic(_admin="admin", db=db)

    assert result.deleted == 4
    assert result.errors == []


@pytest.mark.asyncio
async def test_clear_adapters(monkeypatch):
    """clear_adapters stops adapters, deletes bindings and instances."""

    async def _fetchone(query, params=()):
        if "adapter_bindings" in query:
            return _row({"n": 7})
        if "adapter_instances" in query:
            return _row({"n": 3})
        return _row({"n": 0})

    db = _DbStub()
    db.fetchone = _fetchone

    with patch("obs.adapters.registry.stop_all", new_callable=AsyncMock):
        result = await config_api.clear_adapters(_admin="admin", db=db)

    assert result.bindings_deleted == 7
    assert result.deleted == 3
    assert result.errors == []


# ===========================================================================
# obs/api/v1/history.py — helpers
# ===========================================================================


def test_parse_ts_valid():
    """_parse_ts returns datetime for valid ISO string."""
    default = datetime.now(UTC)
    result = history_api._parse_ts("2025-01-15T10:00:00Z", default)
    assert result.year == 2025
    assert result.month == 1


def test_parse_ts_none_returns_default():
    """_parse_ts returns default when input is None or empty."""
    default = datetime(2025, 6, 1, tzinfo=UTC)
    assert history_api._parse_ts(None, default) == default
    assert history_api._parse_ts("", default) == default


def test_parse_ts_invalid_raises_422():
    """_parse_ts raises HTTPException 422 for invalid input."""
    default = datetime.now(UTC)
    with pytest.raises(HTTPException) as exc_info:
        history_api._parse_ts("not-a-date", default)
    assert exc_info.value.status_code == 422


def test_format_utc_bucket_treats_naive_timestamp_as_utc():
    assert history_api._format_utc_bucket("2026-06-03T12:00:00") == "2026-06-03T12:00:00Z"


def test_format_utc_bucket_converts_offset_timestamp_to_utc():
    assert history_api._format_utc_bucket("2026-06-03T14:00:00+02:00") == "2026-06-03T12:00:00Z"


def test_format_utc_bucket_keeps_unparseable_bucket():
    assert history_api._format_utc_bucket("bucket-1") == "bucket-1"


@pytest.mark.asyncio
async def test_get_default_history_window_hours_missing(monkeypatch):
    """_get_default_history_window_hours returns default when key missing."""
    db = _DbStub(fetchone_result=None)
    result = await history_api._get_default_history_window_hours(db)
    assert result == history_api.DEFAULT_HISTORY_WINDOW_HOURS


@pytest.mark.asyncio
async def test_get_default_history_window_hours_from_db():
    """_get_default_history_window_hours reads value from DB."""
    db = _DbStub(fetchone_result=_row({"value": "48"}))
    result = await history_api._get_default_history_window_hours(db)
    assert result == 48


@pytest.mark.asyncio
async def test_get_default_history_window_hours_clamped():
    """_get_default_history_window_hours clamps to valid range."""
    db = _DbStub(fetchone_result=_row({"value": "0"}))
    result = await history_api._get_default_history_window_hours(db)
    assert result == history_api.MIN_HISTORY_WINDOW_HOURS


@pytest.mark.asyncio
async def test_get_default_history_window_hours_invalid_string():
    """_get_default_history_window_hours returns default for non-integer value."""
    db = _DbStub(fetchone_result=_row({"value": "not-a-number"}))
    result = await history_api._get_default_history_window_hours(db)
    assert result == history_api.DEFAULT_HISTORY_WINDOW_HOURS


@pytest.mark.asyncio
async def test_check_history_access_with_user():
    """_check_history_access allows request when user is authenticated."""
    request = MagicMock()
    db = _DbStub()
    # Should not raise — user is present
    await history_api._check_history_access(request, user="alice", db=db)


@pytest.mark.asyncio
async def test_check_history_access_no_page_id_raises():
    """_check_history_access raises 401 when unauthenticated and no X-Page-Id."""
    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)
    db = _DbStub()
    with pytest.raises(HTTPException) as exc_info:
        await history_api._check_history_access(request, user=None, db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_check_history_access_public_page_allowed():
    """_check_history_access allows access for public pages."""
    request = MagicMock()
    request.headers.get = MagicMock(return_value="page-1")
    db = _DbStub()

    with patch.object(history_api, "_resolve_page_access", AsyncMock(return_value="public")):
        # Should not raise
        await history_api._check_history_access(request, user=None, db=db)


@pytest.mark.asyncio
async def test_check_history_access_readonly_page_allowed():
    """_check_history_access allows access for readonly pages."""
    request = MagicMock()
    request.headers.get = MagicMock(return_value="page-1")
    db = _DbStub()

    with patch.object(history_api, "_resolve_page_access", AsyncMock(return_value="readonly")):
        await history_api._check_history_access(request, user=None, db=db)


@pytest.mark.asyncio
async def test_check_history_access_protected_page_with_valid_token():
    """_check_history_access allows protected page with valid session token."""
    request = MagicMock()
    # First call returns page_id, second returns session_token
    request.headers.get = MagicMock(side_effect=["page-1", "valid-token"])
    db = _DbStub()

    with (
        patch.object(history_api, "_resolve_page_access", AsyncMock(return_value="protected")),
        patch("obs.api.v1.history.validate_session", return_value=True),
    ):
        await history_api._check_history_access(request, user=None, db=db)


@pytest.mark.asyncio
async def test_check_history_access_protected_page_no_token_raises():
    """_check_history_access raises 401 for protected page without token."""
    request = MagicMock()
    request.headers.get = MagicMock(side_effect=["page-1", None])
    db = _DbStub()

    with (
        patch.object(history_api, "_resolve_page_access", AsyncMock(return_value="protected")),
        patch("obs.api.v1.history.validate_session", return_value=False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await history_api._check_history_access(request, user=None, db=db)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_check_history_access_private_page_raises():
    """_check_history_access raises 401 for private page without auth."""
    request = MagicMock()
    request.headers.get = MagicMock(side_effect=["page-1"])
    db = _DbStub()

    with patch.object(history_api, "_resolve_page_access", AsyncMock(return_value="private")):
        with pytest.raises(HTTPException) as exc_info:
            await history_api._check_history_access(request, user=None, db=db)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_query_history_dp_not_found(monkeypatch):
    """query_history raises 404 when DataPoint not found."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.history.get_registry", lambda: reg)

    db = _DbStub(fetchone_result=None)
    request = MagicMock()
    dp_id = uuid.uuid4()

    with patch.object(history_api, "_check_history_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await history_api.query_history(
                dp_id=dp_id,
                from_ts=None,
                to_ts=None,
                limit=100,
                request=request,
                user="admin",
                db=db,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_query_history_success(monkeypatch):
    """query_history returns list of HistoryPoints."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.history.get_registry", lambda: reg)

    db = _DbStub(fetchone_result=None)
    request = MagicMock()

    mock_plugin = AsyncMock()
    mock_plugin.query = AsyncMock(return_value=[{"ts": "2025-01-01T00:00:00Z", "v": 23.5, "u": "°C", "q": "good"}])

    with (
        patch.object(history_api, "_check_history_access", AsyncMock()),
        patch("obs.api.v1.history.get_history_plugin", return_value=mock_plugin),
    ):
        result = await history_api.query_history(
            dp_id=dp.id,
            from_ts=None,
            to_ts=None,
            limit=100,
            request=request,
            user="admin",
            db=db,
        )

    assert len(result) == 1
    assert result[0].v == 23.5


@pytest.mark.asyncio
async def test_aggregate_history_invalid_fn(monkeypatch):
    """aggregate_history raises 422 for unknown fn."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.history.get_registry", lambda: reg)

    db = _DbStub(fetchone_result=None)
    request = MagicMock()

    with patch.object(history_api, "_check_history_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await history_api.aggregate_history(
                dp_id=dp.id,
                fn="median",
                interval="1h",
                from_ts=None,
                to_ts=None,
                request=request,
                user="admin",
                db=db,
            )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_aggregate_history_success(monkeypatch):
    """aggregate_history returns aggregated points."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.history.get_registry", lambda: reg)

    db = _DbStub(fetchone_result=None)
    request = MagicMock()

    mock_plugin = AsyncMock()
    mock_plugin.aggregate = AsyncMock(return_value=[{"bucket": "2025-01-01T00:00:00Z", "v": 20.0}])

    with (
        patch.object(history_api, "_check_history_access", AsyncMock()),
        patch("obs.api.v1.history.get_history_plugin", return_value=mock_plugin),
    ):
        result = await history_api.aggregate_history(
            dp_id=dp.id,
            fn="avg",
            interval="1h",
            from_ts=None,
            to_ts=None,
            request=request,
            user="admin",
            db=db,
        )

    assert len(result) == 1
    assert result[0].bucket == "2025-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_aggregate_history_dp_not_found(monkeypatch):
    """aggregate_history raises 404 when DataPoint not found."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.history.get_registry", lambda: reg)

    db = _DbStub(fetchone_result=None)
    request = MagicMock()

    with patch.object(history_api, "_check_history_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await history_api.aggregate_history(
                dp_id=uuid.uuid4(),
                fn="avg",
                interval="1h",
                from_ts=None,
                to_ts=None,
                request=request,
                user="admin",
                db=db,
            )
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/autobackup.py
# ===========================================================================


from obs.api.v1 import autobackup as ab_api


@pytest.mark.asyncio
async def test_load_config_defaults(monkeypatch):
    """_load_config returns defaults when no app_settings rows."""
    db = _DbStub(fetchall_results=[])
    cfg = await ab_api._load_config(db)
    assert cfg.enabled is False
    assert cfg.hour == 3
    assert cfg.retention_days == 7


@pytest.mark.asyncio
async def test_load_config_from_db():
    """_load_config reads values from app_settings rows."""
    rows = [
        _row({"key": "autobackup.enabled", "value": "1"}),
        _row({"key": "autobackup.hour", "value": "5"}),
        _row({"key": "autobackup.retention_days", "value": "14"}),
    ]
    db = _DbStub(fetchall_results=rows)
    cfg = await ab_api._load_config(db)
    assert cfg.enabled is True
    assert cfg.hour == 5
    assert cfg.retention_days == 14


@pytest.mark.asyncio
async def test_save_config():
    """_save_config commits three rows to app_settings."""
    db = _DbStub()
    cfg = ab_api.AutobackupConfig(enabled=True, hour=4, retention_days=10)
    await ab_api._save_config(db, cfg)
    assert len(db.committed) == 3
    keys = [c[1][0] for c in db.committed]
    assert "autobackup.enabled" in keys
    assert "autobackup.hour" in keys
    assert "autobackup.retention_days" in keys


def test_list_backups_empty(tmp_path, monkeypatch):
    """_list_backups returns empty list when no backup files."""
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    result = ab_api._list_backups()
    assert result == []


def test_list_backups_with_files(tmp_path, monkeypatch):
    """_list_backups returns sorted entries for existing files."""
    (tmp_path / "20250101-0300.json").write_text("{}")
    (tmp_path / "20250201-0300.json").write_text("{}")
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    result = ab_api._list_backups()
    assert len(result) == 2
    # Sorted descending (newest first)
    assert result[0].name == "20250201-0300"


def test_prune_old_backups(tmp_path, monkeypatch):
    """_prune_old_backups deletes files beyond retention limit."""
    # Create 5 backup files
    for i in range(5):
        (tmp_path / f"2025010{i + 1}-0300.json").write_text("{}")

    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    deleted = ab_api._prune_old_backups(retention_days=3)
    assert deleted == 2
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 3


@pytest.mark.asyncio
async def test_get_autobackup_config_endpoint():
    """get_autobackup_config returns AutobackupConfig from DB."""
    db = _DbStub(fetchall_results=[])
    result = await ab_api.get_autobackup_config(_admin="admin", db=db)
    assert isinstance(result, ab_api.AutobackupConfig)


@pytest.mark.asyncio
async def test_set_autobackup_config_valid():
    """set_autobackup_config saves valid config and returns it."""
    db = _DbStub()
    body = ab_api.AutobackupConfig(enabled=True, hour=2, retention_days=5)

    with patch.object(ab_api, "_notify_config_change"):
        result = await ab_api.set_autobackup_config(body=body, _admin="admin", db=db)

    assert result.hour == 2
    assert result.retention_days == 5


@pytest.mark.asyncio
async def test_set_autobackup_config_invalid_hour():
    """set_autobackup_config raises 400 for invalid hour."""
    db = _DbStub()
    body = ab_api.AutobackupConfig(enabled=True, hour=25, retention_days=5)

    with pytest.raises(HTTPException) as exc_info:
        await ab_api.set_autobackup_config(body=body, _admin="admin", db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_autobackup_config_invalid_retention():
    """set_autobackup_config raises 400 for invalid retention_days."""
    db = _DbStub()
    body = ab_api.AutobackupConfig(enabled=True, hour=3, retention_days=0)

    with pytest.raises(HTTPException) as exc_info:
        await ab_api.set_autobackup_config(body=body, _admin="admin", db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_list_autobackups_endpoint(tmp_path, monkeypatch):
    """list_autobackups endpoint returns backup entries."""
    (tmp_path / "20250601-0300.json").write_text("{}")
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    result = await ab_api.list_autobackups(_admin="admin")
    assert len(result) == 1
    assert result[0].name == "20250601-0300"


@pytest.mark.asyncio
async def test_delete_autobackup_invalid_name():
    """delete_autobackup raises 400 for invalid name format."""
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.delete_autobackup(name="../evil", _admin="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_autobackup_not_found(tmp_path, monkeypatch):
    """delete_autobackup raises 404 when backup file doesn't exist."""
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.delete_autobackup(name="20250601-0300", _admin="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_autobackup_success(tmp_path, monkeypatch):
    """delete_autobackup deletes existing backup and returns ok."""
    backup_file = tmp_path / "20250601-0300.json"
    backup_file.write_text("{}")
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)

    result = await ab_api.delete_autobackup(name="20250601-0300", _admin="admin")
    assert result["ok"] is True
    assert not backup_file.exists()


@pytest.mark.asyncio
async def test_restore_autobackup_invalid_name():
    """restore_autobackup raises 400 for invalid name format."""
    db = _DbStub()
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.restore_autobackup(name="../../etc/passwd", _admin="admin", db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_restore_autobackup_not_found(tmp_path, monkeypatch):
    """restore_autobackup raises 404 when backup doesn't exist."""
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    db = _DbStub()
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.restore_autobackup(name="20250601-0300", _admin="admin", db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_restore_autobackup_invalid_json(tmp_path, monkeypatch):
    """restore_autobackup raises 400 for invalid JSON content."""
    backup_file = tmp_path / "20250601-0300.json"
    backup_file.write_text("NOT JSON")
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    db = _DbStub()
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.restore_autobackup(name="20250601-0300", _admin="admin", db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_run_autobackup_now(tmp_path, monkeypatch):
    """run_autobackup_now creates a backup and prunes old ones."""
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    db = _DbStub(fetchall_results=[])

    with patch.object(ab_api, "_create_backup_now", AsyncMock(return_value="20250601-0300")):
        result = await ab_api.run_autobackup_now(_admin="admin", db=db)

    assert result["ok"] is True
    assert result["name"] == "20250601-0300"


def test_notify_config_change_no_event():
    """_notify_config_change does nothing when event is None."""
    original = ab_api._config_changed_event
    ab_api._config_changed_event = None
    ab_api._notify_config_change()  # Should not raise
    ab_api._config_changed_event = original


def test_notify_config_change_sets_event():
    """_notify_config_change sets the asyncio event."""
    original = ab_api._config_changed_event
    event = asyncio.Event()
    ab_api._config_changed_event = event
    ab_api._notify_config_change()
    assert event.is_set()
    ab_api._config_changed_event = original


def test_init_and_get_autobackup_scheduler():
    """init_autobackup_scheduler creates scheduler and get_ retrieves it."""
    db = _DbStub()
    _scheduler_obj = ab_api.AutobackupScheduler(db)
    _ = _scheduler_obj  # created, not started
    original = ab_api._scheduler
    ab_api._scheduler = None
    with pytest.raises(RuntimeError):
        ab_api.get_autobackup_scheduler()
    ab_api._scheduler = original


# ===========================================================================
# obs/api/v1/system.py — helpers and routes
# ===========================================================================


@pytest.mark.asyncio
async def test_read_history_cfg_defaults():
    """_read_history_cfg returns defaults when no rows."""
    db = _DbStub(fetchall_results=[])
    cfg = await system_api._read_history_cfg(db)
    assert cfg["plugin"] == "sqlite"
    assert cfg["influx_url"] == "http://localhost:8086"


@pytest.mark.asyncio
async def test_read_history_cfg_overrides():
    """_read_history_cfg merges DB values over defaults."""
    rows = [
        _row({"key": "history.plugin", "value": "influxdb"}),
        _row({"key": "history.influx_url", "value": "http://influx:8086"}),
    ]
    db = _DbStub(fetchall_results=rows)
    cfg = await system_api._read_history_cfg(db)
    assert cfg["plugin"] == "influxdb"
    assert cfg["influx_url"] == "http://influx:8086"
    # Unchanged defaults
    assert cfg["influx_bucket"] == "obs"


@pytest.mark.asyncio
async def test_update_history_settings_invalid_plugin():
    """update_history_settings raises 422 for unknown plugin."""
    db = _DbStub()
    body = system_api.HistorySettingsIn(plugin="elasticsearch")
    with pytest.raises(HTTPException) as exc_info:
        await system_api.update_history_settings(body=body, db=db, _admin="admin")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_update_history_settings_sqlite_success():
    """update_history_settings saves settings and returns them."""
    db = _DbStub()
    body = system_api.HistorySettingsIn(plugin="sqlite")

    with patch("obs.history.factory.reload_history_plugin", AsyncMock()):
        result = await system_api.update_history_settings(body=body, db=db, _admin="admin")

    assert result.plugin == "sqlite"


@pytest.mark.asyncio
async def test_update_history_settings_reload_failure():
    """update_history_settings raises 500 when plugin reload fails."""
    db = _DbStub()
    body = system_api.HistorySettingsIn(plugin="sqlite")

    with patch("obs.history.factory.reload_history_plugin", AsyncMock(side_effect=RuntimeError("reload fail"))):
        with pytest.raises(HTTPException) as exc_info:
            await system_api.update_history_settings(body=body, db=db, _admin="admin")
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_test_history_connection_sqlite():
    """test_history_connection returns ok=True for sqlite."""
    body = system_api.HistorySettingsIn(plugin="sqlite")
    result = await system_api.test_history_connection(body=body, _admin="admin")
    assert result.ok is True
    assert "sqlite" in result.message.lower()


@pytest.mark.asyncio
async def test_test_history_connection_unknown_plugin():
    """test_history_connection returns ok=False for unknown plugin."""
    body = system_api.HistorySettingsIn(plugin="unknown_db")
    result = await system_api.test_history_connection(body=body, _admin="admin")
    assert result.ok is False


@pytest.mark.asyncio
async def test_get_app_settings_no_row():
    """get_app_settings returns default timezone when no row in DB."""
    db = _DbStub(fetchone_result=None)
    result = await system_api.get_app_settings(db=db, _user="admin")
    assert result.timezone == "Europe/Zurich"


@pytest.mark.asyncio
async def test_get_app_settings_from_db():
    """get_app_settings returns timezone from DB."""
    db = _DbStub(fetchone_result=_row({"value": "America/New_York"}))
    result = await system_api.get_app_settings(db=db, _user="admin")
    assert result.timezone == "America/New_York"


@pytest.mark.asyncio
async def test_update_app_settings_valid():
    """update_app_settings saves valid timezone."""
    db = _DbStub()
    body = system_api.AppSettingsIn(timezone="Europe/Berlin")

    with patch("obs.logic.manager.get_logic_manager") as mock_lm:
        mock_lm.return_value.update_app_config = MagicMock()
        result = await system_api.update_app_settings(body=body, db=db, _user="admin")

    assert result.timezone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_update_app_settings_invalid_timezone():
    """update_app_settings raises 422 for unknown timezone."""
    db = _DbStub()
    body = system_api.AppSettingsIn(timezone="Mars/Olympus_Mons")

    with pytest.raises(HTTPException) as exc_info:
        await system_api.update_app_settings(body=body, db=db, _user="admin")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_list_nav_links():
    """list_nav_links returns NavLinkOut objects from DB rows."""
    rows = [_row({"id": "nl-1", "label": "GitHub", "url": "https://github.com", "icon": "gh", "sort_order": 0, "open_new_tab": 1})]
    db = _DbStub(fetchall_results=rows)
    result = await system_api.list_nav_links(db=db, _user="admin")
    assert len(result) == 1
    assert result[0].label == "GitHub"
    assert result[0].open_new_tab is True


@pytest.mark.asyncio
async def test_create_nav_link():
    """create_nav_link inserts row and returns NavLinkOut."""
    db = _DbStub()
    body = system_api.NavLinkIn(label="OBS", url="https://obs.example.com", icon="home")
    result = await system_api.create_nav_link(body=body, db=db, _admin="admin")
    assert result.label == "OBS"
    assert result.url == "https://obs.example.com"
    assert len(db.committed) == 1


@pytest.mark.asyncio
async def test_update_nav_link_not_found():
    """update_nav_link raises 404 when link doesn't exist."""
    db = _DbStub(fetchone_result=None)
    body = system_api.NavLinkPatch(label="New Label")
    with pytest.raises(HTTPException) as exc_info:
        await system_api.update_nav_link(link_id="missing", body=body, db=db, _admin="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_nav_link_partial_patch():
    """update_nav_link applies partial patch correctly."""
    existing = _row({"id": "nl-1", "label": "Old", "url": "http://old.com", "icon": "", "sort_order": 0, "open_new_tab": 1})
    db = _DbStub(fetchone_result=existing)
    body = system_api.NavLinkPatch(label="New Label")
    result = await system_api.update_nav_link(link_id="nl-1", body=body, db=db, _admin="admin")
    assert result.label == "New Label"
    assert result.url == "http://old.com"  # unchanged


@pytest.mark.asyncio
async def test_delete_nav_link_not_found():
    """delete_nav_link raises 404 when link doesn't exist."""
    db = _DbStub(fetchone_result=None)
    with pytest.raises(HTTPException) as exc_info:
        await system_api.delete_nav_link(link_id="missing", db=db, _admin="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_nav_link_success():
    """delete_nav_link deletes existing link."""
    db = _DbStub(fetchone_result=_row({"id": "nl-1"}))
    await system_api.delete_nav_link(link_id="nl-1", db=db, _admin="admin")
    assert len(db.committed) == 1


@pytest.mark.asyncio
async def test_get_logs_filtered():
    """get_logs filters by level and limits entries."""

    entries = [
        {"ts": "2025-01-01T00:00:00Z", "level": "INFO", "logger": "obs", "message": "started"},
        {"ts": "2025-01-01T00:00:01Z", "level": "ERROR", "logger": "obs", "message": "oops"},
    ]

    with patch("obs.log_buffer.get_log_buffer", return_value=entries):
        result = await system_api.get_logs(level="ERROR", limit=10, _user="admin")

    assert len(result) == 1
    assert result[0].level == "ERROR"


@pytest.mark.asyncio
async def test_get_log_level():
    """get_log_level returns current root log level."""
    result = await system_api.get_log_level(_admin="admin")
    assert result.level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NOTSET", "Level 0"}


@pytest.mark.asyncio
async def test_set_log_level_valid():
    """set_log_level calls set_log_buffer_level for valid level."""
    with patch("obs.log_buffer.set_log_buffer_level") as mock_set:
        await system_api.set_log_level(
            body=system_api.LogLevelIn(level="DEBUG"),
            _admin="admin",
        )
    mock_set.assert_called_once_with("DEBUG")


@pytest.mark.asyncio
async def test_set_log_level_invalid():
    """set_log_level raises 422 for invalid level."""
    with pytest.raises(HTTPException) as exc_info:
        await system_api.set_log_level(
            body=system_api.LogLevelIn(level="TRACE"),
            _admin="admin",
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_health_registry_not_initialized():
    """health endpoint returns dp_count=0 when registry not initialized."""
    with patch("obs.core.registry.get_registry", side_effect=RuntimeError("not init")):
        result = await system_api.health()
    assert result.datapoints == 0
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_health_with_registry():
    """health endpoint returns correct datapoint count."""
    reg = _RegistryStub([_mk_dp(), _mk_dp()])

    with patch("obs.core.registry.get_registry", return_value=reg):
        result = await system_api.health()

    assert result.datapoints == 2


# ===========================================================================
# obs/api/v1/datapoints.py — helpers
# ===========================================================================


from obs.api.v1 import datapoints as dp_api


@pytest.mark.asyncio
async def test_page_has_datapoint_no_page():
    """_page_has_datapoint returns False when page not found."""
    db = _DbStub(fetchone_result=None)
    result = await dp_api._page_has_datapoint(db, "nonexistent-page", uuid.uuid4())
    assert result is False


@pytest.mark.asyncio
async def test_page_has_datapoint_no_page_config():
    """_page_has_datapoint returns False when page_config is None."""
    db = _DbStub(fetchone_result=_row({"page_config": None}))
    result = await dp_api._page_has_datapoint(db, "page-1", uuid.uuid4())
    assert result is False


@pytest.mark.asyncio
async def test_page_has_datapoint_invalid_json():
    """_page_has_datapoint returns False for invalid page_config JSON."""
    db = _DbStub(fetchone_result=_row({"page_config": "not-json{{{"}))
    result = await dp_api._page_has_datapoint(db, "page-1", uuid.uuid4())
    assert result is False


@pytest.mark.asyncio
async def test_page_has_datapoint_includes_nested_extra_datapoints():
    """_page_has_datapoint includes Info widget extra_datapoints[].id."""
    dp_id = uuid.uuid4()
    page_config = {
        "widgets": [
            {
                "type": "info",
                "config": {
                    "extra_datapoints": [
                        {"id": str(dp_id), "label": "Eingang", "unit": "A", "decimals": 2},
                    ],
                },
            },
        ],
    }
    db = _DbStub(fetchone_result=_row({"page_config": json.dumps(page_config)}))

    result = await dp_api._page_has_datapoint(db, "page-1", dp_id)

    assert result is True


@pytest.mark.asyncio
async def test_page_has_datapoint_includes_grundriss_mini_widget_datapoints():
    """_page_has_datapoint includes Grundriss miniWidgets camelCase datapoint fields."""
    dp_id = uuid.uuid4()
    page_config = {
        "widgets": [
            {
                "type": "grundriss",
                "config": {
                    "miniWidgets": [
                        {
                            "id": str(uuid.uuid4()),
                            "widgetType": "display_value",
                            "datapointId": str(dp_id),
                            "statusDatapointId": str(uuid.uuid4()),
                        },
                    ],
                },
            },
        ],
    }
    db = _DbStub(fetchone_result=_row({"page_config": json.dumps(page_config)}))

    result = await dp_api._page_has_datapoint(db, "page-1", dp_id)

    assert result is True


@pytest.mark.asyncio
async def test_page_has_datapoint_ignores_nested_non_datapoint_uuids():
    """_page_has_datapoint does not allow arbitrary nested UUID strings."""
    label_uuid = uuid.uuid4()
    source_page_id = uuid.uuid4()
    page_config = {
        "widgets": [
            {
                "type": "info",
                "config": {
                    "source_page_id": str(source_page_id),
                    "extra_datapoints": [
                        {"id": "not-a-uuid", "label": str(label_uuid)},
                    ],
                },
            },
        ],
    }
    db = _DbStub(fetchone_result=_row({"page_config": json.dumps(page_config)}))

    assert await dp_api._page_has_datapoint(db, "page-1", label_uuid) is False
    assert await dp_api._page_has_datapoint(db, "page-1", source_page_id) is False


@pytest.mark.asyncio
async def test_list_tags_empty(monkeypatch):
    """list_tags returns empty list when no datapoints."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    result = await dp_api.list_tags(_user="admin")
    assert result == []


@pytest.mark.asyncio
async def test_list_tags_deduplicated(monkeypatch):
    """list_tags returns unique sorted tags."""
    dp1 = _mk_dp()
    dp1.tags = ["sensor", "indoor"]
    dp2 = _mk_dp()
    dp2.tags = ["sensor", "outdoor"]
    reg = _RegistryStub([dp1, dp2])
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    result = await dp_api.list_tags(_user="admin")
    assert result == ["indoor", "outdoor", "sensor"]


@pytest.mark.asyncio
async def test_get_datapoint_not_found(monkeypatch):
    """get_datapoint raises 404 when DataPoint not in registry."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_datapoint(dp_id=uuid.uuid4(), _user="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_datapoint_not_found(monkeypatch):
    """delete_datapoint raises 404 when DataPoint not in registry."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.delete_datapoint(dp_id=uuid.uuid4(), _user="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_datapoint_not_found(monkeypatch):
    """update_datapoint raises 404 when DataPoint not in registry."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    from obs.models.datapoint import DataPointUpdate

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.update_datapoint(dp_id=uuid.uuid4(), body=DataPointUpdate(), _user="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_datapoint_unknown_data_type(monkeypatch):
    """update_datapoint raises 422 for unknown data_type."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)

    from obs.models.datapoint import DataPointUpdate
    from obs.models.types import DataTypeRegistry

    with patch.object(DataTypeRegistry, "is_registered", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await dp_api.update_datapoint(
                dp_id=dp.id,
                body=DataPointUpdate(data_type="UNKNOWN_TYPE"),
                _user="admin",
            )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_list_datapoints_empty(monkeypatch):
    """list_datapoints returns empty page for empty registry."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    result = await dp_api.list_datapoints(page=0, size=50, sort="name", order="asc", _user="admin")
    assert result.total == 0
    assert result.items == []
    assert result.pages == 1


@pytest.mark.asyncio
async def test_list_datapoints_sorted_desc(monkeypatch):
    """list_datapoints sorts descending when order=desc."""
    dp1 = _mk_dp("Alpha")
    dp2 = _mk_dp("Zeta")
    reg = _RegistryStub([dp1, dp2])

    from obs.core.registry import ValueState

    reg._values = {dp.id: ValueState() for dp in [dp1, dp2]}

    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)
    result = await dp_api.list_datapoints(page=0, size=50, sort="name", order="desc", _user="admin")
    assert result.items[0].name == "Zeta"


@pytest.mark.asyncio
async def test_get_value_unauthenticated_no_page_id(monkeypatch):
    """get_value raises 401 when unauthenticated and no X-Page-Id header."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)

    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)
    db = _DbStub()

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(dp_id=dp.id, request=request, user=None, db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_value_not_found(monkeypatch):
    """get_value raises 404 when DataPoint not found."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)

    request = MagicMock()
    db = _DbStub()

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(dp_id=uuid.uuid4(), request=request, user="admin", db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_write_value_not_found(monkeypatch):
    """write_value raises 404 when DataPoint not found."""
    reg = _RegistryStub()
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)

    request = MagicMock()
    db = _DbStub()

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=uuid.uuid4(),
            body=dp_api.WriteValueIn(value=42),
            request=request,
            user="admin",
            db=db,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_write_value_unauthenticated_no_page_id(monkeypatch):
    """write_value raises 401 when unauthenticated and no X-Page-Id header."""
    dp = _mk_dp()
    reg = _RegistryStub([dp])
    monkeypatch.setattr("obs.api.v1.datapoints.get_registry", lambda: reg)

    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)
    db = _DbStub()

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=dp.id,
            body=dp_api.WriteValueIn(value=42),
            request=request,
            user=None,
            db=db,
        )
    assert exc_info.value.status_code == 401


# ===========================================================================
# obs/core/mqtt_passwd.py
# ===========================================================================


from obs.core import mqtt_passwd


def test_mosquitto_hash_format():
    """mosquitto_hash returns correctly formatted PBKDF2-SHA512 hash."""
    result = mqtt_passwd.mosquitto_hash("testpassword")
    assert result.startswith("$7$")
    parts = result.split("$")
    assert len(parts) == 5
    assert parts[2] == str(mqtt_passwd._ITERATIONS)


def test_mosquitto_hash_different_each_call():
    """mosquitto_hash returns different hash on each call (random salt)."""
    h1 = mqtt_passwd.mosquitto_hash("password")
    h2 = mqtt_passwd.mosquitto_hash("password")
    assert h1 != h2


@pytest.mark.asyncio
async def test_rebuild_passwd_file(tmp_path):
    """rebuild_passwd_file writes service account and user entries."""
    passwd_file = str(tmp_path / "passwd")

    rows = [
        _row({"username": "alice", "mqtt_password_hash": "$7$901$abc$def"}),
        _row({"username": "bob", "mqtt_password_hash": "$7$901$xyz$uvw"}),
    ]
    db = _DbStub(fetchall_results=rows)

    await mqtt_passwd.rebuild_passwd_file(
        db=db,
        passwd_path=passwd_file,
        service_username="obs",
        service_password="supersecret",
    )

    content = (tmp_path / "passwd").read_text()
    lines = content.strip().split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("obs:")
    assert "alice:$7$" in lines[1]
    assert "bob:$7$" in lines[2]


@pytest.mark.asyncio
async def test_rebuild_passwd_file_no_users(tmp_path):
    """rebuild_passwd_file only writes service account when no users."""
    passwd_file = str(tmp_path / "passwd")
    db = _DbStub(fetchall_results=[])

    await mqtt_passwd.rebuild_passwd_file(
        db=db,
        passwd_path=passwd_file,
        service_username="service",
        service_password="pw123",
    )

    content = (tmp_path / "passwd").read_text()
    lines = [line_ for line_ in content.strip().split("\n") if line_]
    assert len(lines) == 1
    assert lines[0].startswith("service:")


@pytest.mark.asyncio
async def test_reload_mosquitto_no_config():
    """reload_mosquitto logs warning when neither command nor pid configured."""
    # Should not raise
    await mqtt_passwd.reload_mosquitto(reload_command=None, reload_pid=None)


@pytest.mark.asyncio
async def test_reload_mosquitto_with_pid_not_found():
    """reload_mosquitto handles ProcessLookupError gracefully."""
    import os

    with patch.object(os, "kill", side_effect=ProcessLookupError("no proc")):
        await mqtt_passwd.reload_mosquitto(reload_command=None, reload_pid=99999)


@pytest.mark.asyncio
async def test_reload_mosquitto_with_pid_permission_error():
    """reload_mosquitto handles PermissionError gracefully."""
    import os

    with patch.object(os, "kill", side_effect=PermissionError("no perm")):
        await mqtt_passwd.reload_mosquitto(reload_command=None, reload_pid=1)


@pytest.mark.asyncio
async def test_reload_mosquitto_with_command_success():
    """reload_mosquitto runs subprocess command successfully."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        await mqtt_passwd.reload_mosquitto(reload_command="echo ok", reload_pid=None)


@pytest.mark.asyncio
async def test_reload_mosquitto_with_command_failure():
    """reload_mosquitto logs warning when command returns non-zero."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error output"))
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
        await mqtt_passwd.reload_mosquitto(reload_command="false", reload_pid=None)


@pytest.mark.asyncio
async def test_reload_mosquitto_command_timeout():
    """reload_mosquitto handles TimeoutError from subprocess gracefully."""
    with patch(
        "asyncio.create_subprocess_shell",
        side_effect=TimeoutError("timed out"),
    ):
        await mqtt_passwd.reload_mosquitto(reload_command="slow-cmd", reload_pid=None)


# ===========================================================================
# obs/api/v1/config.py — export_db (path checks)
# ===========================================================================


@pytest.mark.asyncio
async def test_export_db_file_not_found(monkeypatch, tmp_path):
    """export_db raises 404 when DB file doesn't exist."""
    from fastapi import BackgroundTasks

    nonexistent = str(tmp_path / "nonexistent.db")

    with patch("obs.config.get_settings") as mock_settings:
        mock_settings.return_value.database.path = nonexistent
        with pytest.raises(HTTPException) as exc_info:
            await config_api.export_db(
                background_tasks=BackgroundTasks(),
                _user="admin",
            )

    assert exc_info.value.status_code == 404
