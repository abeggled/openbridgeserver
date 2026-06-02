"""Coverage tests for:
- obs/api/v1/adapters.py
- obs/api/v1/hierarchy.py
- obs/adapters/iobroker/adapter.py
- obs/logic/manager.py
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Shared helpers / stubs
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics sqlite3.Row attribute access used in tests."""

    def __getitem__(self, k):
        return super().__getitem__(k)

    def keys(self):
        return super().keys()


def _row(**kwargs) -> _Row:
    return _Row(kwargs)


class _DbStub:
    """Minimal async DB stub."""

    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one
        self.committed = []
        self._fetchone_seq: list | None = None

    def set_fetchone_sequence(self, seq: list):
        self._fetchone_seq = list(seq)

    async def fetchone(self, query, params=()):
        if self._fetchone_seq is not None:
            return self._fetchone_seq.pop(0)
        return self._one

    async def fetchall(self, query, params=()):
        return list(self._rows)

    async def execute_and_commit(self, query, params=()):
        self.committed.append((query, params))

    async def execute(self, query, params=()):
        self.committed.append(("execute", query, params))

    async def commit(self):
        pass

    async def executemany(self, query, params_list):
        self.committed.append(("executemany", query))


def _inst_row(*, adapter_type="KNX", name="Test", enabled=1, config=None, iid=None):
    iid = iid or str(uuid.uuid4())
    now = "2024-01-01T00:00:00+00:00"
    return _row(
        id=iid,
        adapter_type=adapter_type,
        name=name,
        config=json.dumps(config or {}),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


# ============================================================================
# obs/api/v1/adapters.py tests
# ============================================================================


class TestInstanceOut:
    """Tests for the _instance_out helper and instance listing."""

    def test_instance_out_no_instance(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        row = _inst_row()
        result = adp_api._instance_out(row, None)
        assert result.running is False
        assert result.connected is False
        assert result.registered is False
        assert result.severity == "ok"
        assert result.bindings == 0

    def test_instance_out_with_instance(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        mock_cls = MagicMock()
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        inst = MagicMock()
        inst.connected = True
        inst.last_severity = "warning"
        inst.last_detail = "some detail"
        inst.get_bindings.return_value = [1, 2, 3]
        row = _inst_row()
        result = adp_api._instance_out(row, inst)
        assert result.running is True
        assert result.connected is True
        assert result.severity == "warning"
        assert result.status_detail == "some detail"
        assert result.bindings == 3

    @pytest.mark.asyncio
    async def test_list_instances(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()
        db = _DbStub(rows=[row])
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        result = await adp_api.list_instances(db=db, _user="admin")
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_instances_empty(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(rows=[])
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        result = await adp_api.list_instances(db=db, _user="admin")
        assert result == []


class TestCreateInstance:
    @pytest.mark.asyncio
    async def test_create_instance_unknown_type(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceCreate

        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        body = AdapterInstanceCreate(adapter_type="UNKNOWN", name="test")
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.create_instance(body=body, db=_DbStub(), _user="admin")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_create_instance_invalid_config(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceCreate

        mock_cls = MagicMock()
        mock_cls.config_schema.side_effect = Exception("bad config")
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        body = AdapterInstanceCreate(adapter_type="SOME_TYPE", name="test", config={"bad": "val"})
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.create_instance(body=body, db=_DbStub(), _user="admin")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_create_instance_success_disabled(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceCreate

        mock_cls = MagicMock()
        mock_cls.config_schema.return_value = MagicMock()
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        row = _inst_row(adapter_type="KNX", enabled=0)
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        body = AdapterInstanceCreate(adapter_type="KNX", name="test", enabled=False)
        result = await adp_api.create_instance(body=body, db=db, _user="admin")
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_instance_enabled_starts_instance(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceCreate

        mock_cls = MagicMock()
        mock_cls.config_schema.return_value = MagicMock()
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        start_called = []

        async def _fake_start(iid, bus, db):
            start_called.append(iid)

        monkeypatch.setattr(adp_api.adapter_registry, "start_instance", _fake_start)
        row = _inst_row(adapter_type="KNX", enabled=1)
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)

        fake_bus = MagicMock()
        with patch("obs.core.event_bus.get_event_bus", return_value=fake_bus):
            body = AdapterInstanceCreate(adapter_type="KNX", name="test", enabled=True)
            result = await adp_api.create_instance(body=body, db=db, _user="admin")
        assert len(start_called) == 1
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_instance_start_exception_ignored(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceCreate

        mock_cls = MagicMock()
        mock_cls.config_schema.return_value = MagicMock()
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)

        async def _fail_start(iid, bus, db):
            raise RuntimeError("connect failed")

        monkeypatch.setattr(adp_api.adapter_registry, "start_instance", _fail_start)
        row = _inst_row(adapter_type="KNX", enabled=1)
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)

        fake_bus = MagicMock()
        with patch("obs.core.event_bus.get_event_bus", return_value=fake_bus):
            body = AdapterInstanceCreate(adapter_type="KNX", name="test", enabled=True)
            # Should not raise — exception is suppressed
            result = await adp_api.create_instance(body=body, db=db, _user="admin")
        assert result is not None


class TestGetInstance:
    @pytest.mark.asyncio
    async def test_get_instance_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.get_instance(instance_id=uuid.uuid4(), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_instance_success(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        result = await adp_api.get_instance(instance_id=uuid.UUID(row["id"]), db=db, _user="admin")
        assert str(result.id) == row["id"]


class TestUpdateInstance:
    @pytest.mark.asyncio
    async def test_update_instance_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceUpdate

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.update_instance(
                instance_id=uuid.uuid4(),
                body=AdapterInstanceUpdate(name="new"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_instance_config_validation_error(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceUpdate

        row = _inst_row()
        mock_cls = MagicMock()
        mock_cls.config_schema.side_effect = Exception("bad config")
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        db = _DbStub(one=row)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.update_instance(
                instance_id=uuid.UUID(row["id"]),
                body=AdapterInstanceUpdate(config={"bad": True}),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_update_instance_enabled_restarts(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceUpdate

        row = _inst_row(enabled=1)
        mock_cls = MagicMock()
        mock_cls.config_schema.return_value = MagicMock()
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        restart_called = []

        async def _fake_restart(iid, bus, db):
            restart_called.append(iid)

        monkeypatch.setattr(adp_api.adapter_registry, "restart_instance", _fake_restart)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        db = _DbStub(one=row)
        fake_bus = MagicMock()
        with patch("obs.core.event_bus.get_event_bus", return_value=fake_bus):
            result = await adp_api.update_instance(
                instance_id=uuid.UUID(row["id"]),
                body=AdapterInstanceUpdate(name="updated"),
                db=db,
                _user="admin",
            )
        assert len(restart_called) == 1
        assert result is not None

    @pytest.mark.asyncio
    async def test_update_instance_disabled_stops(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import AdapterInstanceUpdate

        row = _inst_row(enabled=1)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        stop_called = []

        async def _fake_stop(iid):
            stop_called.append(iid)

        monkeypatch.setattr(adp_api.adapter_registry, "stop_instance", _fake_stop)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        db = _DbStub(one=row)
        fake_bus = MagicMock()
        with patch("obs.core.event_bus.get_event_bus", return_value=fake_bus):
            await adp_api.update_instance(
                instance_id=uuid.UUID(row["id"]),
                body=AdapterInstanceUpdate(enabled=False),
                db=db,
                _user="admin",
            )
        assert len(stop_called) == 1


class TestDeleteInstance:
    @pytest.mark.asyncio
    async def test_delete_instance_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.delete_instance(instance_id=uuid.uuid4(), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_instance_success(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()

        async def _fake_stop(iid):
            pass

        monkeypatch.setattr(adp_api.adapter_registry, "stop_instance", _fake_stop)
        db = _DbStub(one=row)
        # Should not raise
        await adp_api.delete_instance(instance_id=uuid.UUID(row["id"]), db=db, _user="admin")
        assert len(db.committed) >= 2  # DELETE bindings + DELETE instance


class TestRestartInstance:
    @pytest.mark.asyncio
    async def test_restart_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.restart_instance_route(instance_id=uuid.uuid4(), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_restart_calls_registry(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()
        restart_called = []

        async def _fake_restart(iid, bus, db):
            restart_called.append(iid)

        monkeypatch.setattr(adp_api.adapter_registry, "restart_instance", _fake_restart)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        db = _DbStub(one=row)
        fake_bus = MagicMock()
        with patch("obs.core.event_bus.get_event_bus", return_value=fake_bus):
            result = await adp_api.restart_instance_route(instance_id=uuid.UUID(row["id"]), db=db, _user="admin")
        assert len(restart_called) == 1
        assert result is not None


class TestTestInstance:
    @pytest.mark.asyncio
    async def test_test_instance_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.test_instance(instance_id=uuid.uuid4(), body=None, db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_test_instance_type_not_registered(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row(adapter_type="UNKNOWN")
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        result = await adp_api.test_instance(instance_id=uuid.UUID(row["id"]), body=None, db=db, _user="admin")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_test_instance_config_error(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()
        db = _DbStub(one=row)
        mock_cls = MagicMock()
        mock_cls.config_schema.side_effect = Exception("bad config")
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        result = await adp_api.test_instance(instance_id=uuid.UUID(row["id"]), body=None, db=db, _user="admin")
        assert result.success is False
        assert "Config-Fehler" in result.detail

    @pytest.mark.asyncio
    async def test_test_instance_connection_fails(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row()
        db = _DbStub(one=row)
        mock_cls = MagicMock()
        mock_cls.config_schema.return_value = MagicMock()
        inst = MagicMock()
        inst.connect = AsyncMock(side_effect=RuntimeError("refused"))
        inst.disconnect = AsyncMock()
        mock_cls.return_value = inst
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        result = await adp_api.test_instance(instance_id=uuid.UUID(row["id"]), body=None, db=db, _user="admin")
        assert result.success is False


class TestListInstanceBindings:
    @pytest.mark.asyncio
    async def test_list_bindings_returns_entries(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        bid = str(uuid.uuid4())
        dpid = str(uuid.uuid4())
        binding_row = _row(
            id=bid,
            datapoint_id=dpid,
            dp_name="Temperatur",
            enabled=1,
            config=json.dumps({"group_address": "1/1/1"}),
        )
        db = _DbStub(rows=[binding_row])
        result = await adp_api.list_instance_bindings(instance_id=uuid.uuid4(), db=db, _user="admin")
        assert len(result) == 1
        assert result[0].datapoint_name == "Temperatur"


class TestListAdapters:
    @pytest.mark.asyncio
    async def test_list_adapters(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        monkeypatch.setattr(
            adp_api.adapter_registry,
            "get_status",
            lambda: {"KNX": {"registered": True, "running": True, "connected": True, "hidden": False}},
        )
        result = await adp_api.list_adapters(_user="admin")
        assert len(result) == 1
        assert result[0].adapter_type == "KNX"


class TestGetAdapterSchema:
    @pytest.mark.asyncio
    async def test_schema_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.get_adapter_schema(adapter_type="UNKNOWN", _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_schema_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        from pydantic import BaseModel

        class FakeConfig(BaseModel):
            host: str = "localhost"

        mock_cls = MagicMock()
        mock_cls.config_schema = FakeConfig
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        result = await adp_api.get_adapter_schema(adapter_type="KNX", _user="admin")
        assert "KNX Connection Config" == result.get("title")


class TestGetBindingSchema:
    @pytest.mark.asyncio
    async def test_binding_schema_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.get_binding_schema(adapter_type="UNKNOWN", _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_binding_schema_no_schema_attr(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        mock_cls = MagicMock(spec=[])  # no binding_config_schema attr
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        result = await adp_api.get_binding_schema(adapter_type="KNX", _user="admin")
        assert result == {}


class TestUpdateAdapterConfig:
    @pytest.mark.asyncio
    async def test_update_config_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import ConfigPatch

        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.update_adapter_config(adapter_type="UNKNOWN", body=ConfigPatch(config={}), db=_DbStub(), _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_config_invalid_config(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import ConfigPatch

        mock_cls = MagicMock()
        mock_cls.config_schema.side_effect = Exception("bad")
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.update_adapter_config(adapter_type="KNX", body=ConfigPatch(config={"x": 1}), db=_DbStub(), _user="admin")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_update_config_success(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import ConfigPatch

        from pydantic import BaseModel

        class Cfg(BaseModel):
            host: str = "localhost"

        mock_cls = MagicMock()
        mock_cls.config_schema = Cfg
        monkeypatch.setattr(adp_api.adapter_registry, "get_class", lambda t: mock_cls)
        result = await adp_api.update_adapter_config(
            adapter_type="KNX", body=ConfigPatch(config={"host": "192.168.1.1"}), db=_DbStub(), _user="admin"
        )
        assert result.adapter_type == "KNX"


class TestGetAdapterConfig:
    @pytest.mark.asyncio
    async def test_get_config_missing_returns_default(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        result = await adp_api.get_adapter_config(adapter_type="KNX", db=db, _user="admin")
        assert result.config == {}
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_get_config_existing(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _row(adapter_type="KNX", config=json.dumps({"host": "10.0.0.1"}), enabled=1, updated_at="2024-01-01T00:00:00")
        db = _DbStub(one=row)
        result = await adp_api.get_adapter_config(adapter_type="KNX", db=db, _user="admin")
        assert result.config == {"host": "10.0.0.1"}


class TestMigrateInstanceBindings:
    @pytest.mark.asyncio
    async def test_migrate_same_source_and_target(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import BindingMigrationRequest

        iid = uuid.uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.migrate_instance_bindings(
                source_instance_id=iid,
                body=BindingMigrationRequest(target_instance_id=iid),
                db=_DbStub(),
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_migrate_source_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import BindingMigrationRequest

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.migrate_instance_bindings(
                source_instance_id=uuid.uuid4(),
                body=BindingMigrationRequest(target_instance_id=uuid.uuid4()),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_migrate_type_mismatch(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import BindingMigrationRequest

        source_id = str(uuid.uuid4())
        target_id = str(uuid.uuid4())
        source_row = _row(id=source_id, adapter_type="KNX")
        target_row = _row(id=target_id, adapter_type="MQTT")

        db = _DbStub()
        db.set_fetchone_sequence([source_row, target_row])

        with pytest.raises(HTTPException) as exc_info:
            await adp_api.migrate_instance_bindings(
                source_instance_id=uuid.UUID(source_id),
                body=BindingMigrationRequest(target_instance_id=uuid.UUID(target_id)),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_migrate_success_with_bindings(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import BindingMigrationRequest

        source_id = str(uuid.uuid4())
        target_id = str(uuid.uuid4())
        dp_id1 = str(uuid.uuid4())
        dp_id2 = str(uuid.uuid4())
        source_row = _row(id=source_id, adapter_type="KNX")
        target_row = _row(id=target_id, adapter_type="KNX")

        source_binding = _row(id=str(uuid.uuid4()), datapoint_id=dp_id1)
        target_binding = _row(datapoint_id=dp_id2)

        class _MultiDb:
            def __init__(self):
                self.committed = []
                self._fetchone_calls = 0
                self._fetchall_calls = 0

            async def fetchone(self, q, p=()):
                self._fetchone_calls += 1
                if self._fetchone_calls == 1:
                    return source_row
                return target_row

            async def fetchall(self, q, p=()):
                self._fetchall_calls += 1
                if self._fetchall_calls == 1:
                    return [source_binding]
                return [target_binding]

            async def execute(self, q, p=()):
                self.committed.append(p)

            async def commit(self):
                pass

        async def _fake_reload(iid, db):
            pass

        monkeypatch.setattr(adp_api.adapter_registry, "reload_instance_bindings", _fake_reload)
        db = _MultiDb()
        result = await adp_api.migrate_instance_bindings(
            source_instance_id=uuid.UUID(source_id),
            body=BindingMigrationRequest(target_instance_id=uuid.UUID(target_id)),
            db=db,
            _user="admin",
        )
        assert result.migrated == 1
        assert result.skipped == 0

    @pytest.mark.asyncio
    async def test_migrate_skips_existing_target_bindings(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api
        from obs.api.v1.adapters import BindingMigrationRequest

        source_id = str(uuid.uuid4())
        target_id = str(uuid.uuid4())
        dp_id = str(uuid.uuid4())
        source_row = _row(id=source_id, adapter_type="KNX")
        target_row = _row(id=target_id, adapter_type="KNX")
        shared_binding_src = _row(id=str(uuid.uuid4()), datapoint_id=dp_id)
        shared_binding_tgt = _row(datapoint_id=dp_id)

        class _Db2:
            def __init__(self):
                self.committed = []
                self._fo_calls = 0
                self._fa_calls = 0

            async def fetchone(self, q, p=()):
                self._fo_calls += 1
                return source_row if self._fo_calls == 1 else target_row

            async def fetchall(self, q, p=()):
                self._fa_calls += 1
                return [shared_binding_src] if self._fa_calls == 1 else [shared_binding_tgt]

            async def execute(self, q, p=()):
                self.committed.append(p)

            async def commit(self):
                pass

        async def _fake_reload(iid, db):
            pass

        monkeypatch.setattr(adp_api.adapter_registry, "reload_instance_bindings", _fake_reload)
        db = _Db2()
        result = await adp_api.migrate_instance_bindings(
            source_instance_id=uuid.UUID(source_id),
            body=BindingMigrationRequest(target_instance_id=uuid.UUID(target_id)),
            db=db,
            _user="admin",
        )
        assert result.migrated == 0
        assert result.skipped == 1


class TestIoBrokerHelpers:
    def test_iobroker_obs_type(self):
        from obs.api.v1.adapters import _iobroker_obs_type

        assert _iobroker_obs_type("boolean") == ("BOOLEAN", "bool")
        assert _iobroker_obs_type("number") == ("FLOAT", "float")
        assert _iobroker_obs_type("string") == ("STRING", "string")
        assert _iobroker_obs_type(None) == ("STRING", None)

    def test_iobroker_source_type(self):
        from obs.api.v1.adapters import _iobroker_source_type

        assert _iobroker_source_type("BOOLEAN") == "bool"
        assert _iobroker_source_type("FLOAT") == "float"
        assert _iobroker_source_type("INTEGER") == "int"
        assert _iobroker_source_type("UNKNOWN") is None

    def test_iobroker_direction_explicit(self):
        from obs.api.v1.adapters import _iobroker_direction

        assert _iobroker_direction({}, "SOURCE") == "SOURCE"
        assert _iobroker_direction({}, "DEST") == "DEST"
        assert _iobroker_direction({}, "BOTH") == "BOTH"

    def test_iobroker_direction_auto_bidirectional(self):
        from obs.api.v1.adapters import _iobroker_direction

        assert _iobroker_direction({"read": True, "write": True}, "auto") == "BOTH"

    def test_iobroker_direction_auto_readonly(self):
        from obs.api.v1.adapters import _iobroker_direction

        assert _iobroker_direction({"read": True, "write": False}, "auto") == "SOURCE"

    def test_iobroker_name_from_name(self):
        from obs.api.v1.adapters import _iobroker_name

        assert _iobroker_name({"name": "My Light"}) == "My Light"

    def test_iobroker_name_from_id(self):
        from obs.api.v1.adapters import _iobroker_name

        assert _iobroker_name({"id": "hue.0.light.on", "name": None}) == "on"

    def test_iobroker_tags_dedup(self):
        from obs.api.v1.adapters import _iobroker_tags

        tags = _iobroker_tags({"id": "hue.0.light", "role": "switch", "type": "boolean"}, ["hue", "extra"])
        assert tags.count("hue") == 1
        assert "iobroker" in tags

    def test_build_tls_context_no_tls(self):
        from obs.api.v1.adapters import _build_tls_context

        cfg = SimpleNamespace(tls=False)
        assert _build_tls_context(cfg) is None

    def test_build_tls_context_with_tls(self):
        import ssl

        from obs.api.v1.adapters import _build_tls_context

        cfg = SimpleNamespace(tls=True, tls_insecure=False)
        ctx = _build_tls_context(cfg)
        assert isinstance(ctx, ssl.SSLContext)

    def test_build_tls_context_insecure(self):
        import ssl

        from obs.api.v1.adapters import _build_tls_context

        cfg = SimpleNamespace(tls=True, tls_insecure=True)
        ctx = _build_tls_context(cfg)
        assert ctx.verify_mode == ssl.CERT_NONE


class TestIoBrokerBrowseStates:
    @pytest.mark.asyncio
    async def test_browse_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.iobroker_browse_states(instance_id=uuid.uuid4(), q="", limit=10, db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_browse_wrong_type(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row(adapter_type="KNX")
        db = _DbStub(one=row)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.iobroker_browse_states(instance_id=uuid.uuid4(), q="", limit=10, db=db, _user="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_browse_instance_not_connected(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row(adapter_type="IOBROKER")
        db = _DbStub(one=row)
        monkeypatch.setattr(adp_api.adapter_registry, "get_instance_by_id", lambda iid: None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.iobroker_browse_states(instance_id=uuid.uuid4(), q="", limit=10, db=db, _user="admin")
        assert exc_info.value.status_code == 503


class TestSnmpWalk:
    @pytest.mark.asyncio
    async def test_snmp_walk_not_found(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.snmp_walk(
                instance_id=uuid.uuid4(),
                host="10.0.0.1",
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_snmp_walk_wrong_type(self, monkeypatch):
        from obs.api.v1 import adapters as adp_api

        row = _inst_row(adapter_type="KNX")
        db = _DbStub(one=row)
        with pytest.raises(HTTPException) as exc_info:
            await adp_api.snmp_walk(
                instance_id=uuid.UUID(row["id"]),
                host="10.0.0.1",
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 400


# ============================================================================
# obs/api/v1/hierarchy.py tests
# ============================================================================


class TestHierarchyHelpers:
    def test_row_to_tree(self):
        from obs.api.v1.hierarchy import _row_to_tree

        row = _row(
            id="t1",
            name="My Tree",
            description="desc",
            display_depth=2,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        tree = _row_to_tree(row)
        assert tree.name == "My Tree"
        assert tree.display_depth == 2

    def test_row_to_tree_null_depth(self):
        from obs.api.v1.hierarchy import _row_to_tree

        row = _row(
            id="t1",
            name="My Tree",
            description="",
            display_depth=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        tree = _row_to_tree(row)
        assert tree.display_depth == 0

    def test_build_tree_nested(self):
        from obs.api.v1.hierarchy import HierarchyNode, _build_tree

        parent = HierarchyNode(
            id="p1",
            tree_id="t1",
            parent_id=None,
            name="Parent",
            description="",
            order=0,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        child = HierarchyNode(
            id="c1",
            tree_id="t1",
            parent_id="p1",
            name="Child",
            description="",
            order=1,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        roots = _build_tree([parent, child])
        assert len(roots) == 1
        assert len(roots[0].children) == 1
        assert roots[0].children[0].id == "c1"

    def test_build_tree_orphan_becomes_root(self):
        from obs.api.v1.hierarchy import HierarchyNode, _build_tree

        node = HierarchyNode(
            id="n1",
            tree_id="t1",
            parent_id="nonexistent",
            name="Orphan",
            description="",
            order=0,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        roots = _build_tree([node])
        assert len(roots) == 1


class TestListTrees:
    @pytest.mark.asyncio
    async def test_list_trees_empty(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(rows=[])
        result = await hier_api.list_trees(db=db, _user="admin")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_trees_returns_items(self):
        from obs.api.v1 import hierarchy as hier_api

        row = _row(id="t1", name="Tree1", description="", display_depth=0, created_at="2024-01-01", updated_at="2024-01-01")
        db = _DbStub(rows=[row])
        result = await hier_api.list_trees(db=db, _user="admin")
        assert len(result) == 1
        assert result[0].name == "Tree1"


class TestCreateTree:
    @pytest.mark.asyncio
    async def test_create_tree(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyTreeCreate

        row = _row(id="t1", name="NewTree", description="", display_depth=0, created_at="2024-01-01", updated_at="2024-01-01")
        db = _DbStub(one=row)
        result = await hier_api.create_tree(body=HierarchyTreeCreate(name="NewTree"), db=db, _user="admin")
        assert result.name == "NewTree"
        assert len(db.committed) >= 1


class TestUpdateTree:
    @pytest.mark.asyncio
    async def test_update_tree_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyTreeUpdate

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.update_tree(tree_id="t1", body=HierarchyTreeUpdate(name="X"), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_tree_success(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyTreeUpdate

        row = _row(id="t1", name="OldName", description="", display_depth=0, created_at="2024-01-01", updated_at="2024-01-01")
        db = _DbStub(one=row)
        result = await hier_api.update_tree(tree_id="t1", body=HierarchyTreeUpdate(name="NewName"), db=db, _user="admin")
        assert result.name == "OldName"  # stub returns the same row


class TestDeleteTree:
    @pytest.mark.asyncio
    async def test_delete_tree_not_found(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.delete_tree(tree_id="t1", db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_tree_success(self):
        from obs.api.v1 import hierarchy as hier_api

        row = _row(id="t1")
        db = _DbStub(one=row)
        await hier_api.delete_tree(tree_id="t1", db=db, _user="admin")
        assert any("DELETE" in str(c) for c in db.committed)


class TestGetTreeNodes:
    @pytest.mark.asyncio
    async def test_get_tree_nodes_not_found(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(one=None, rows=[])
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.get_tree_nodes(tree_id="missing", db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_tree_nodes_empty(self):
        from obs.api.v1 import hierarchy as hier_api

        tree_row = _row(id="t1")
        db = _DbStub(one=tree_row, rows=[])
        result = await hier_api.get_tree_nodes(tree_id="t1", db=db, _user="admin")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_tree_nodes_nested(self):
        from obs.api.v1 import hierarchy as hier_api

        tree_row = _row(id="t1")
        node_rows = [
            _row(
                id="p1",
                tree_id="t1",
                parent_id=None,
                name="Parent",
                description="",
                node_order=0,
                icon=None,
                created_at="2024-01-01",
                updated_at="2024-01-01",
            ),
            _row(
                id="c1",
                tree_id="t1",
                parent_id="p1",
                name="Child",
                description="",
                node_order=0,
                icon=None,
                created_at="2024-01-01",
                updated_at="2024-01-01",
            ),
        ]
        db = _DbStub(one=tree_row, rows=node_rows)
        result = await hier_api.get_tree_nodes(tree_id="t1", db=db, _user="admin")
        assert len(result) == 1  # only root
        assert len(result[0].children) == 1


class TestCreateNode:
    @pytest.mark.asyncio
    async def test_create_node_tree_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeCreate

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.create_node(
                body=HierarchyNodeCreate(tree_id="t1", name="Node"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_node_invalid_parent(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeCreate

        tree_row = _row(id="t1")
        parent_row = _row(tree_id="t2")  # different tree

        class _Db:
            def __init__(self):
                self.committed = []
                self._calls = 0

            async def fetchone(self, q, p=()):
                self._calls += 1
                return tree_row if self._calls == 1 else parent_row

            async def fetchall(self, q, p=()):
                return []

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

        db = _Db()
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.create_node(
                body=HierarchyNodeCreate(tree_id="t1", parent_id="p_other", name="Node"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_node_success(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeCreate

        node_row = _row(
            id="n1",
            tree_id="t1",
            parent_id=None,
            name="Node",
            description="",
            node_order=0,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        tree_row = _row(id="t1")
        _db_unused = _DbStub(one=tree_row, rows=[])

        class _Db2:
            def __init__(self):
                self.committed = []
                self._calls = 0

            async def fetchone(self, q, p=()):
                self._calls += 1
                return tree_row if self._calls == 1 else node_row

            async def fetchall(self, q, p=()):
                return []

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

        db2 = _Db2()
        result = await hier_api.create_node(
            body=HierarchyNodeCreate(tree_id="t1", name="Node"),
            db=db2,
            _user="admin",
        )
        assert result.name == "Node"


class TestUpdateNode:
    @pytest.mark.asyncio
    async def test_update_node_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeUpdate

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.update_node(node_id="n1", body=HierarchyNodeUpdate(name="X"), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_node_success(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeUpdate

        node_row = _row(
            id="n1",
            tree_id="t1",
            parent_id=None,
            name="Old",
            description="",
            node_order=0,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        db = _DbStub(one=node_row)
        result = await hier_api.update_node(node_id="n1", body=HierarchyNodeUpdate(name="New"), db=db, _user="admin")
        assert result.name == "Old"  # stub always returns same row


class TestDeleteNode:
    @pytest.mark.asyncio
    async def test_delete_node_not_found(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.delete_node(node_id="n1", db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_node_success(self):
        from obs.api.v1 import hierarchy as hier_api

        row = _row(id="n1")
        db = _DbStub(one=row)
        await hier_api.delete_node(node_id="n1", db=db, _user="admin")
        assert any("DELETE" in str(c) for c in db.committed)


class TestMoveNode:
    @pytest.mark.asyncio
    async def test_move_node_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeMove

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.move_node(node_id="n1", body=HierarchyNodeMove(), db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_move_node_self_reference(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeMove

        node_row = _row(id="n1", tree_id="t1")
        parent_row = _row(tree_id="t1")

        class _Db:
            def __init__(self):
                self._calls = 0

            async def fetchone(self, q, p=()):
                self._calls += 1
                return node_row if self._calls == 1 else parent_row

        db = _Db()
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.move_node(node_id="n1", body=HierarchyNodeMove(new_parent_id="n1"), db=db, _user="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_move_node_wrong_tree(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeMove

        node_row = _row(id="n1", tree_id="t1")
        wrong_parent = _row(tree_id="t2")

        class _Db:
            def __init__(self):
                self._calls = 0

            async def fetchone(self, q, p=()):
                self._calls += 1
                return node_row if self._calls == 1 else wrong_parent

        db = _Db()
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.move_node(node_id="n1", body=HierarchyNodeMove(new_parent_id="p_other"), db=db, _user="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_move_node_success(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyNodeMove

        node_row = _row(
            id="n1",
            tree_id="t1",
            parent_id=None,
            name="Node",
            description="",
            node_order=0,
            icon=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        db = _DbStub(one=node_row)
        result = await hier_api.move_node(node_id="n1", body=HierarchyNodeMove(new_order=5), db=db, _user="admin")
        assert result.id == "n1"


class TestHierarchyLinks:
    @pytest.mark.asyncio
    async def test_get_node_datapoints_not_found(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(one=None, rows=[])
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.get_node_datapoints(node_id="n1", db=db, _user="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_node_datapoints_empty(self):
        from obs.api.v1 import hierarchy as hier_api

        node_row = _row(id="n1")
        db = _DbStub(one=node_row, rows=[])
        result = await hier_api.get_node_datapoints(node_id="n1", db=db, _user="admin")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_node_datapoints_with_items(self):
        from obs.api.v1 import hierarchy as hier_api

        node_row = _row(id="n1")
        dp_row = _row(
            link_id=str(uuid.uuid4()),
            id=str(uuid.uuid4()),
            name="Temp",
            data_type="FLOAT",
            unit="°C",
        )
        db = _DbStub(one=node_row, rows=[dp_row])
        result = await hier_api.get_node_datapoints(node_id="n1", db=db, _user="admin")
        assert len(result) == 1
        assert result[0].name == "Temp"

    @pytest.mark.asyncio
    async def test_create_link_node_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyLinkCreate

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.create_link(
                body=HierarchyLinkCreate(node_id="n1", datapoint_id="dp1"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_link_dp_not_found(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyLinkCreate

        class _Db:
            def __init__(self):
                self._calls = 0

            async def fetchone(self, q, p=()):
                self._calls += 1
                return _row(id="n1") if self._calls == 1 else None

        db = _Db()
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.create_link(
                body=HierarchyLinkCreate(node_id="n1", datapoint_id="dp_missing"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_link_existing_returns_existing(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyLinkCreate

        existing_id = str(uuid.uuid4())

        class _Db:
            def __init__(self):
                self._calls = 0
                self.committed = []

            async def fetchone(self, q, p=()):
                self._calls += 1
                if self._calls <= 2:
                    return _row(id="n1") if self._calls == 1 else _row(id="dp1")
                return _row(id=existing_id)

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

        db = _Db()
        result = await hier_api.create_link(
            body=HierarchyLinkCreate(node_id="n1", datapoint_id="dp1"),
            db=db,
            _user="admin",
        )
        assert result["id"] == existing_id
        assert len(db.committed) == 0  # no insert

    @pytest.mark.asyncio
    async def test_create_link_new(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import HierarchyLinkCreate

        class _Db:
            def __init__(self):
                self._calls = 0
                self.committed = []

            async def fetchone(self, q, p=()):
                self._calls += 1
                if self._calls == 1:
                    return _row(id="n1")
                elif self._calls == 2:
                    return _row(id="dp1")
                return None  # no existing link

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

        db = _Db()
        result = await hier_api.create_link(
            body=HierarchyLinkCreate(node_id="n1", datapoint_id="dp1"),
            db=db,
            _user="admin",
        )
        assert result["node_id"] == "n1"
        assert len(db.committed) == 1  # insert

    @pytest.mark.asyncio
    async def test_delete_link(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub()
        await hier_api.delete_link(node_id="n1", datapoint_id="dp1", db=db, _user="admin")
        assert len(db.committed) >= 1

    @pytest.mark.asyncio
    async def test_get_datapoint_nodes_empty(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(rows=[])
        result = await hier_api.get_datapoint_nodes(dp_id="dp1", db=db, _user="admin")
        assert result == []


class TestSearchNodes:
    @pytest.mark.asyncio
    async def test_search_nodes_empty(self):
        from obs.api.v1 import hierarchy as hier_api

        db = _DbStub(rows=[])
        result = await hier_api.search_nodes(q="", limit=10, db=db, _user="admin")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_search_nodes_with_query(self):
        from obs.api.v1 import hierarchy as hier_api

        search_row = _row(node_id="n1", node_name="Wohnzimmer", tree_id="t1", tree_name="EG")

        class _Db:
            async def fetchall(self, q, p=()):
                if "LIKE" in q:
                    return [search_row]
                # node_map query
                return [_row(id="n1", parent_id=None, name="Wohnzimmer")]

        result = await hier_api.search_nodes(q="Wohnzimmer", limit=10, db=_Db(), _user="admin")
        assert len(result) == 1
        assert result[0].node_name == "Wohnzimmer"


class TestEtsImport:
    @pytest.mark.asyncio
    async def test_ets_import_invalid_mode(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        db = _DbStub()
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.import_from_ets(
                body=EtsImportRequest(tree_name="Test", mode="invalid"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_ets_import_groups_no_data(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        db = _DbStub(rows=[])
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.import_from_ets(
                body=EtsImportRequest(tree_name="Test", mode="groups"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ets_import_buildings_no_data(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        db = _DbStub(rows=[])
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.import_from_ets(
                body=EtsImportRequest(tree_name="Test", mode="buildings"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ets_import_trades_no_data(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        db = _DbStub(rows=[])
        with pytest.raises(HTTPException) as exc_info:
            await hier_api.import_from_ets(
                body=EtsImportRequest(tree_name="Test", mode="trades"),
                db=db,
                _user="admin",
            )
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ets_import_flat_mode(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        ga_rows = [
            _row(address="0/0/1", name="GA1", description="desc", dpt="1.001", main_group_name="Main0", mid_group_name="Mid0"),
            _row(address="0/1/1", name="GA2", description="", dpt="1.001", main_group_name="Main0", mid_group_name="Mid1"),
        ]

        class _Db:
            def __init__(self):
                self.committed = []

            async def fetchall(self, q, p=()):
                if "knx_group_addresses" in q:
                    return ga_rows
                return []

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

            async def executemany(self, q, rows):
                pass

            async def commit(self):
                pass

        result = await hier_api.import_from_ets(
            body=EtsImportRequest(tree_name="Test", mode="flat"),
            db=_Db(),
            _user="admin",
        )
        assert result.nodes_created >= 1

    @pytest.mark.asyncio
    async def test_ets_import_mid_mode(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        ga_rows = [
            _row(address="0/0/1", name="GA1", description="", dpt="1.001", main_group_name="MG0", mid_group_name="MidG0"),
        ]

        class _Db:
            def __init__(self):
                self.committed = []

            async def fetchall(self, q, p=()):
                if "knx_group_addresses" in q:
                    return ga_rows
                return []

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

            async def executemany(self, q, rows):
                pass

            async def commit(self):
                pass

        result = await hier_api.import_from_ets(
            body=EtsImportRequest(tree_name="Test", mode="mid"),
            db=_Db(),
            _user="admin",
        )
        assert result.nodes_created >= 2  # main + mid

    @pytest.mark.asyncio
    async def test_ets_import_groups_mode(self):
        from obs.api.v1 import hierarchy as hier_api
        from obs.api.v1.hierarchy import EtsImportRequest

        ga_rows = [
            _row(address="0/0/1", name="Light", description="", dpt="1.001", main_group_name="EG", mid_group_name="Wohnzimmer"),
        ]

        class _Db:
            def __init__(self):
                self.committed = []

            async def fetchall(self, q, p=()):
                if "knx_group_addresses" in q:
                    return ga_rows
                return []

            async def execute_and_commit(self, q, p=()):
                self.committed.append(p)

            async def executemany(self, q, rows):
                pass

            async def commit(self):
                pass

        result = await hier_api.import_from_ets(
            body=EtsImportRequest(tree_name="Test", mode="groups"),
            db=_Db(),
            _user="admin",
        )
        assert result.nodes_created >= 3  # main + mid + GA


# ============================================================================
# obs/adapters/iobroker/adapter.py tests
# ============================================================================


class TestIoBrokerAdapterExtras:
    """Tests for uncovered branches in the ioBroker adapter."""

    def _make_adapter(self, mock_bus):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(
            event_bus=mock_bus,
            config={"host": "192.168.1.50", "port": 8084},
        )
        socket = MagicMock()
        socket.connected = True
        socket.call = AsyncMock()
        socket.disconnect = AsyncMock()
        a._socket = socket
        from obs.adapters.iobroker.adapter import IoBrokerAdapterConfig

        a._cfg = IoBrokerAdapterConfig(**a._config)
        return a

    def test_socket_is_connected_none(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._socket = None
        assert a._socket_is_connected() is False

    def test_socket_is_connected_via_eio(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = False
        mock_socket.eio = MagicMock()
        mock_socket.eio.state = "connected"
        a._socket = mock_socket
        assert a._socket_is_connected() is True

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.disconnect = AsyncMock()
        a._socket = mock_socket
        a._state_map["test.0.state"] = ["binding"]
        a._last_source_values["key"] = 42

        await a.disconnect()

        assert a._socket is None
        assert a._state_map == {}
        assert a._last_source_values == {}
        assert a._disconnect_requested is True

    @pytest.mark.asyncio
    async def test_on_bindings_reloaded_no_cfg(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._cfg = None
        # Should return early without error
        await a._on_bindings_reloaded()
        assert a._state_map == {}

    @pytest.mark.asyncio
    async def test_on_bindings_reloaded_source_bindings(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerAdapterConfig

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._cfg = IoBrokerAdapterConfig()

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "hue.0.light.on"}, direction="SOURCE")
        a._bindings = [b]
        a._socket = None  # not connected → subscribe won't be called

        await a._on_bindings_reloaded()
        assert "hue.0.light.on" in a._state_map

    @pytest.mark.asyncio
    async def test_on_bindings_reloaded_dest_binding_ignored(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerAdapterConfig

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._cfg = IoBrokerAdapterConfig()

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "hue.0.light.on"}, direction="DEST")
        a._bindings = [b]
        a._socket = None

        await a._on_bindings_reloaded()
        assert "hue.0.light.on" not in a._state_map

    @pytest.mark.asyncio
    async def test_on_bindings_reloaded_invalid_config_skipped(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerAdapterConfig

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._cfg = IoBrokerAdapterConfig()

        from tests.adapters.conftest import make_binding

        # Missing state_id → invalid config
        b = make_binding({}, direction="SOURCE")
        a._bindings = [b]
        a._socket = None

        await a._on_bindings_reloaded()
        assert a._state_map == {}

    def test_should_retry_with_websocket(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._should_retry_with_websocket(Exception("OPEN packet not returned by server")) is True
        assert IoBrokerAdapter._should_retry_with_websocket(Exception("connection refused")) is False

    def test_prune_disconnects(self):
        from collections import deque
        from datetime import timedelta

        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerAdapterConfig

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._cfg = IoBrokerAdapterConfig(socket_instability_window_s=60)
        import datetime

        t0 = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        t1 = t0 + timedelta(seconds=61)
        t2 = t0 + timedelta(seconds=70)
        a._disconnect_times = deque([t0, t1])
        a._prune_disconnects(t2)
        assert list(a._disconnect_times) == [t1]

    def test_disconnect_threshold_from_cfg(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerAdapterConfig

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._cfg = IoBrokerAdapterConfig(socket_instability_threshold=5)
        assert a._disconnect_threshold() == 5

    def test_disconnect_threshold_from_raw_config(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084, "socket_instability_threshold": "4"})
        a._cfg = None
        assert a._disconnect_threshold() == 4

    def test_disconnect_threshold_default_on_error(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084, "socket_instability_threshold": "not_a_number"})
        a._cfg = None
        assert a._disconnect_threshold() == 3

    @pytest.mark.asyncio
    async def test_call_socket_not_connected(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._socket = None
        with pytest.raises(RuntimeError):
            await a._call_socket("getState", "test.0.state")

    @pytest.mark.asyncio
    async def test_call_socket_single_result(self):
        mock_bus = MagicMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = True
        mock_socket.call = AsyncMock(return_value=["single_value"])
        a._socket = mock_socket
        result = await a._call_socket("someEvent")
        assert result == "single_value"

    @pytest.mark.asyncio
    async def test_call_socket_no_args(self):
        mock_bus = MagicMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = True
        mock_socket.call = AsyncMock(return_value=[None, "result"])
        a._socket = mock_socket
        result = await a._call_socket("someEvent")
        # called with no extra data arg → data is None
        mock_socket.call.assert_awaited_once_with("someEvent", None, timeout=10.0)
        assert result == "result"

    def test_extract_state_value_dict_val(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._extract_state_value({"val": 42}) == 42

    def test_extract_state_value_dict_value(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._extract_state_value({"value": "hello"}) == "hello"

    def test_extract_state_value_nested_state(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._extract_state_value({"state": {"val": 99}}) == 99

    def test_extract_state_value_nested_state_non_dict(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        # state key is not a dict with "val" -> coerce
        assert IoBrokerAdapter._extract_state_value({"state": "true"}) is True

    def test_extract_state_value_string(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._extract_state_value("42") == 42

    def test_iobroker_common_type_mappings(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._iobroker_common_type("BOOLEAN") == "boolean"
        assert IoBrokerAdapter._iobroker_common_type("FLOAT") == "number"
        assert IoBrokerAdapter._iobroker_common_type("INTEGER") == "number"
        assert IoBrokerAdapter._iobroker_common_type("STRING") == "string"
        assert IoBrokerAdapter._iobroker_common_type("UNKNOWN") == "string"

    def test_iobroker_default_role_mappings(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        assert IoBrokerAdapter._iobroker_default_role("BOOLEAN") == "switch"
        assert IoBrokerAdapter._iobroker_default_role("FLOAT") == "value"
        assert IoBrokerAdapter._iobroker_default_role("STRING") == "state"

    def test_state_row_to_info_localized_name(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        row = {
            "id": "hue.0.light.on",
            "value": {
                "common": {
                    "name": {"de": "Licht an", "en": "Light on"},
                    "type": "boolean",
                    "role": "switch",
                    "read": True,
                    "write": True,
                }
            },
        }
        info = IoBrokerAdapter._state_row_to_info(row)
        assert info["name"] == "Licht an"

    def test_state_row_to_info_empty_row(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        info = IoBrokerAdapter._state_row_to_info({})
        assert info["id"] == ""

    @pytest.mark.asyncio
    async def test_read_binding_value_no_socket(self):
        mock_bus = MagicMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._socket = None

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "test.0.state"})
        result = await a._read_binding_value(b)
        assert result is None

    @pytest.mark.asyncio
    async def test_write_no_socket(self):
        mock_bus = MagicMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        a._socket = None

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "test.0.state"})
        # Should log warning but not raise
        await a.write(b, True)

    @pytest.mark.asyncio
    async def test_publish_warning_status_connected(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = True
        a._socket = mock_socket
        a._connected = True

        await a._publish_warning_status("test warning")
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_ensure_state_calls_set_object_and_set_state(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerEnsureStateRequest

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = True
        mock_socket.call = AsyncMock(return_value=[None, None])
        a._socket = mock_socket

        req = IoBrokerEnsureStateRequest(
            state_id="0_userdata.0.test",
            data_type="BOOLEAN",
            name="Test State",
            initial_value=True,
        )
        await a.ensure_state(req)
        assert mock_socket.call.await_count == 2  # setObject + setState

    @pytest.mark.asyncio
    async def test_ensure_state_no_initial_value(self):
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        from obs.adapters.iobroker.adapter import IoBrokerAdapter, IoBrokerEnsureStateRequest

        a = IoBrokerAdapter(event_bus=mock_bus, config={"host": "x", "port": 8084})
        mock_socket = MagicMock()
        mock_socket.connected = True
        mock_socket.call = AsyncMock(return_value=[None, None])
        a._socket = mock_socket

        req = IoBrokerEnsureStateRequest(state_id="0_userdata.0.test", data_type="FLOAT")
        await a.ensure_state(req)
        assert mock_socket.call.await_count == 1  # only setObject

    @pytest.mark.asyncio
    async def test_source_filters_throttle(self):
        import time

        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "test.0.state"}, send_throttle_ms=10000)
        key = str(b.id)
        a._source_filter_last_sent[key] = time.monotonic()
        a._source_filter_last_values[key] = 1.0

        assert a._source_filters_allow(b, 2.0, "test.0.state") is False

    @pytest.mark.asyncio
    async def test_source_filters_min_delta_pct_blocked(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "test.0.state"}, send_min_delta_pct=10.0)
        key = str(b.id)
        a._source_filter_last_values[key] = 100.0

        # 1% change is below 10% threshold
        assert a._source_filters_allow(b, 101.0, "test.0.state") is False

    @pytest.mark.asyncio
    async def test_source_filters_min_delta_pct_allowed(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})

        from tests.adapters.conftest import make_binding

        b = make_binding({"state_id": "test.0.state"}, send_min_delta_pct=5.0)
        key = str(b.id)
        a._source_filter_last_values[key] = 100.0

        # 20% change exceeds 5% threshold
        assert a._source_filters_allow(b, 120.0, "test.0.state") is True

    def test_ensure_reconnect_task_when_disconnect_requested(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._disconnect_requested = True
        a._ensure_reconnect_task()
        assert a._reconnect_task is None

    @pytest.mark.asyncio
    async def test_stop_subscription_watchdog_none(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._subscription_watchdog_task = None
        # Should not raise
        await a._stop_subscription_watchdog()

    @pytest.mark.asyncio
    async def test_stop_reconnect_task_none(self):
        from obs.adapters.iobroker.adapter import IoBrokerAdapter

        a = IoBrokerAdapter(event_bus=MagicMock(), config={"host": "x", "port": 8084})
        a._reconnect_task = None
        # Should not raise
        await a._stop_reconnect_task()


# ============================================================================
# obs/logic/manager.py tests
# ============================================================================


def _make_logic_manager(graphs=None):
    """Create a LogicManager with mock db/event_bus/registry."""
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    event_bus = MagicMock()
    event_bus.subscribe = MagicMock()
    event_bus.unsubscribe = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    registry.get_value = MagicMock(return_value=None)

    from obs.logic.manager import LogicManager

    mgr = LogicManager(db, event_bus, registry)
    if graphs is not None:
        mgr._graphs = graphs
    return mgr, db, event_bus, registry


def _make_flow(nodes=None, edges=None):
    from obs.logic.models import FlowData

    return FlowData.model_validate({"nodes": nodes or [], "edges": edges or []})


class TestLogicManagerBasics:
    def test_get_logic_manager_not_initialized(self):
        from obs.logic import manager as mgr_mod

        orig = mgr_mod._manager
        mgr_mod._manager = None
        try:
            with pytest.raises(RuntimeError):
                mgr_mod.get_logic_manager()
        finally:
            mgr_mod._manager = orig

    def test_init_logic_manager(self):
        from obs.logic import manager as mgr_mod

        orig = mgr_mod._manager
        try:
            m = mgr_mod.init_logic_manager(MagicMock(), MagicMock(), MagicMock())
            assert mgr_mod.get_logic_manager() is m
        finally:
            mgr_mod._manager = orig

    @pytest.mark.asyncio
    async def test_load_app_config(self):
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(return_value=[_row(key="timezone", value="Europe/Berlin")])
        await mgr._load_app_config()
        assert mgr._app_config["timezone"] == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_load_app_config_db_error(self):
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(side_effect=Exception("db error"))
        # Should not raise
        await mgr._load_app_config()

    def test_update_app_config(self):
        mgr, _, _, _ = _make_logic_manager()
        mgr.update_app_config({"timezone": "America/New_York"})
        assert mgr._app_config["timezone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_stop_cancels_cron_tasks(self):
        mgr, _, event_bus, _ = _make_logic_manager()
        task = MagicMock()
        task.cancel = MagicMock()
        mgr._cron_tasks[("g1", "n1")] = task
        await mgr.stop()
        task.cancel.assert_called_once()
        assert mgr._cron_tasks == {}

    @pytest.mark.asyncio
    async def test_reload(self):
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(return_value=[])
        task = MagicMock()
        task.cancel = MagicMock()
        mgr._cron_tasks[("g1", "n1")] = task
        await mgr.reload()
        task.cancel.assert_called_once()

    def test_invalidate_cache(self):
        mgr, _, _, _ = _make_logic_manager()
        flow = _make_flow()
        mgr._graphs["g1"] = ("G1", True, flow)
        mgr._node_state["g1"] = {"n1": {}}
        task = MagicMock()
        task.cancel = MagicMock()
        mgr._cron_tasks[("g1", "n1")] = task
        mgr._cron_tasks[("g2", "n1")] = MagicMock()  # different graph

        mgr.invalidate_cache("g1")
        assert "g1" not in mgr._graphs
        assert "g1" not in mgr._node_state
        task.cancel.assert_called_once()
        assert ("g1", "n1") not in mgr._cron_tasks
        assert ("g2", "n1") in mgr._cron_tasks

    @pytest.mark.asyncio
    async def test_execute_graph_missing(self):
        mgr, _, _, _ = _make_logic_manager()
        with pytest.raises(KeyError):
            await mgr.execute_graph("nonexistent-graph-id")

    @pytest.mark.asyncio
    async def test_execute_graph_success(self):
        flow = _make_flow()
        mgr, db, event_bus, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        with patch("obs.api.v1.websocket.get_ws_manager") as mock_ws:
            mock_ws.return_value.broadcast = AsyncMock()
            result = await mgr.execute_graph("g1")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_load_graphs_parses_flow(self):
        from obs.logic.models import FlowData

        flow = FlowData.model_validate({"nodes": [], "edges": []})
        flow_json = flow.model_dump_json()
        db_rows = [_row(id="g1", name="Graph1", enabled=1, flow_data=flow_json, node_state="{}")]
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(return_value=db_rows)
        await mgr._load_graphs()
        assert "g1" in mgr._graphs

    @pytest.mark.asyncio
    async def test_load_graphs_skips_invalid(self):
        db_rows = [_row(id="g1", name="Bad", enabled=1, flow_data="not-json", node_state=None)]
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(return_value=db_rows)
        await mgr._load_graphs()
        assert "g1" not in mgr._graphs

    @pytest.mark.asyncio
    async def test_load_graphs_restores_node_state(self):
        from obs.logic.models import FlowData

        flow = FlowData.model_validate({"nodes": [], "edges": []})
        state = {"n1": {"accumulated_hours": 10.0}}
        db_rows = [_row(id="g1", name="G1", enabled=1, flow_data=flow.model_dump_json(), node_state=json.dumps(state))]
        mgr, db, _, _ = _make_logic_manager()
        db.fetchall = AsyncMock(return_value=db_rows)
        await mgr._load_graphs()
        assert mgr._hysteresis.get("g1") == state


class TestLogicManagerValueEvent:
    @pytest.mark.asyncio
    async def test_on_value_event_no_matching_graph(self):
        """When no graph has a node watching the DP, no execute call is made."""
        mgr, db, event_bus, _ = _make_logic_manager(graphs={"g1": ("G1", True, _make_flow())})
        event = MagicMock()
        event.datapoint_id = uuid.uuid4()
        event.value = 42.0
        # Should not raise and no execute called
        await mgr._on_value_event(event)

    @pytest.mark.asyncio
    async def test_on_value_event_disabled_graph_skipped(self):
        dp_id = uuid.uuid4()
        flow = _make_flow(
            nodes=[
                {
                    "id": "n1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id)},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(
            graphs={"g1": ("G1", False, flow)}  # disabled
        )
        event = MagicMock()
        event.datapoint_id = dp_id
        event.value = 1.0
        await mgr._on_value_event(event)
        # No execution happened (would call ws_manager)

    @pytest.mark.asyncio
    async def test_on_value_event_trigger_on_change_suppresses_duplicate(self):
        dp_id = uuid.uuid4()
        flow = _make_flow(
            nodes=[
                {
                    "id": "n1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id), "trigger_on_change": True},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        mgr._node_state["g1"] = {"n1": {"last_value": 42.0, "last_ts": None}}

        executed = []
        orig_execute = mgr._execute_graph

        async def _spy(*args, **kwargs):
            executed.append(True)
            return await orig_execute(*args, **kwargs)

        mgr._execute_graph = _spy
        event = MagicMock()
        event.datapoint_id = dp_id
        event.value = 42.0  # same value

        with patch("obs.api.v1.websocket.get_ws_manager") as mock_ws:
            mock_ws.return_value.broadcast = AsyncMock()
            await mgr._on_value_event(event)
        assert len(executed) == 0

    @pytest.mark.asyncio
    async def test_on_value_event_min_delta_suppresses_small_change(self):
        dp_id = uuid.uuid4()
        flow = _make_flow(
            nodes=[
                {
                    "id": "n1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id), "min_delta": 5.0},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        mgr._node_state["g1"] = {"n1": {"last_value": 100.0, "last_ts": None}}

        executed = []
        orig = mgr._execute_graph

        async def _spy(*a, **kw):
            executed.append(True)
            return await orig(*a, **kw)

        mgr._execute_graph = _spy
        event = MagicMock()
        event.datapoint_id = dp_id
        event.value = 102.0  # delta of 2.0 < 5.0

        await mgr._on_value_event(event)
        assert len(executed) == 0

    @pytest.mark.asyncio
    async def test_on_value_event_min_delta_pct_suppresses(self):
        dp_id = uuid.uuid4()
        flow = _make_flow(
            nodes=[
                {
                    "id": "n1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id), "min_delta_pct": 10.0},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        mgr._node_state["g1"] = {"n1": {"last_value": 100.0, "last_ts": None}}

        executed = []
        orig = mgr._execute_graph

        async def _spy(*a, **kw):
            executed.append(True)
            return await orig(*a, **kw)

        mgr._execute_graph = _spy
        event = MagicMock()
        event.datapoint_id = dp_id
        event.value = 103.0  # 3% change < 10%

        await mgr._on_value_event(event)
        assert len(executed) == 0


class TestLogicManagerHelpers:
    def test_msg_to_str_dict(self):
        from obs.logic.manager import _msg_to_str

        assert _msg_to_str({"key": "val"}) == '{"key": "val"}'

    def test_msg_to_str_list(self):
        from obs.logic.manager import _msg_to_str

        assert _msg_to_str([1, 2, 3]) == "[1, 2, 3]"

    def test_msg_to_str_zero(self):
        from obs.logic.manager import _msg_to_str

        assert _msg_to_str(0) == "0"

    def test_msg_to_str_false(self):
        from obs.logic.manager import _msg_to_str

        assert _msg_to_str(False) == "False"

    def test_msg_to_str_empty_string(self):
        from obs.logic.manager import _msg_to_str

        assert _msg_to_str("") == ""

    def test_parse_http_url_valid(self):
        from obs.logic.manager import _parse_http_url

        p = _parse_http_url("https://example.com/path")
        assert p is not None
        assert p.hostname == "example.com"

    def test_parse_http_url_invalid_scheme(self):
        from obs.logic.manager import _parse_http_url

        assert _parse_http_url("ftp://example.com") is None

    def test_parse_http_url_no_hostname(self):
        from obs.logic.manager import _parse_http_url

        assert _parse_http_url("https://") is None

    def test_is_private_host_empty(self):
        from obs.logic.manager import _is_private_host

        assert _is_private_host("") is True

    def test_is_private_host_loopback_literal(self):
        from obs.logic.manager import _is_private_host

        # 127.0.0.1 is loopback — function returns False (loopback is allowed)
        assert _is_private_host("127.0.0.1") is False

    def test_is_private_host_private_ip(self):
        from obs.logic.manager import _is_private_host

        # 192.168.x.x is private
        assert _is_private_host("192.168.1.1") is True

    def test_read_secret_file_empty_path(self):
        from obs.logic.manager import _read_secret_file

        assert _read_secret_file("") == ""

    def test_read_secret_file_nonexistent(self):
        from obs.logic.manager import _read_secret_file

        assert _read_secret_file("/nonexistent/path/secret.txt") == ""

    def test_cookie_domain_matches(self):
        from obs.logic.manager import _cookie_domain_matches

        assert _cookie_domain_matches("auth.example.com", ".example.com") is True
        assert _cookie_domain_matches("example.com", "example.com") is True
        assert _cookie_domain_matches("other.com", "example.com") is False

    def test_cookie_path_matches(self):
        from obs.logic.manager import _cookie_path_matches

        assert _cookie_path_matches("/auth/sub", "/auth") is True
        assert _cookie_path_matches("/auth", "/auth") is True
        assert _cookie_path_matches("/authz", "/auth") is False

    def test_default_cookie_path(self):
        from obs.logic.manager import _default_cookie_path

        assert _default_cookie_path("/foo/bar/baz") == "/foo/bar"
        assert _default_cookie_path("/foo") == "/"
        assert _default_cookie_path("/") == "/"

    def test_origin_tuple_valid(self):
        from obs.logic.manager import _origin_tuple, _parse_http_url

        p = _parse_http_url("https://example.com/path")
        assert _origin_tuple(p) == ("https", "example.com", 443)

    def test_origin_tuple_none(self):
        from obs.logic.manager import _origin_tuple

        assert _origin_tuple(None) is None

    def test_build_http_host_header_ipv6(self):
        from obs.logic.manager import _build_http_host_header

        # IPv6 should be wrapped in brackets
        result = _build_http_host_header("2001:db8::1", "https", None)
        assert result == "[2001:db8::1]"

    def test_build_http_host_header_non_default_port(self):
        from obs.logic.manager import _build_http_host_header

        result = _build_http_host_header("example.com", "https", 8443)
        assert result == "example.com:8443"

    def test_build_http_host_header_default_port_omitted(self):
        from obs.logic.manager import _build_http_host_header

        result = _build_http_host_header("example.com", "https", 443)
        assert result == "example.com"

    def test_should_send_cookie_host_only_mismatch(self):
        from obs.logic.manager import _should_send_cookie

        assert (
            _should_send_cookie(
                req_hostname="other.example.com",
                req_path="/",
                req_is_https=True,
                cookie_domain="auth.example.com",
                cookie_path="/",
                cookie_host_only=True,
                cookie_secure=False,
            )
            is False
        )

    def test_should_send_cookie_secure_over_http(self):
        from obs.logic.manager import _should_send_cookie

        assert (
            _should_send_cookie(
                req_hostname="example.com",
                req_path="/",
                req_is_https=False,
                cookie_domain="example.com",
                cookie_path="/",
                cookie_host_only=True,
                cookie_secure=True,
            )
            is False
        )

    def test_should_send_cookie_allowed(self):
        from obs.logic.manager import _should_send_cookie

        assert (
            _should_send_cookie(
                req_hostname="example.com",
                req_path="/path",
                req_is_https=True,
                cookie_domain="example.com",
                cookie_path="/",
                cookie_host_only=True,
                cookie_secure=True,
            )
            is True
        )


class TestLogicManagerExecuteGraph:
    @pytest.mark.asyncio
    async def test_execute_graph_writes_datapoint(self):
        """Test that datapoint_write nodes publish events."""
        write_dp_id = uuid.uuid4()
        flow = _make_flow(
            nodes=[
                {
                    "id": "n_write",
                    "type": "datapoint_write",
                    "position": {"x": 200, "y": 0},
                    "data": {"datapoint_id": str(write_dp_id)},
                },
            ],
        )
        mgr, db, event_bus, registry = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        registry.get_value = MagicMock(return_value=None)
        db.execute_and_commit = AsyncMock()

        with patch("obs.api.v1.websocket.get_ws_manager") as mock_ws:
            mock_ws.return_value.broadcast = AsyncMock()
            # Inject value for the write node — executor converts this to _write_value
            await mgr._execute_graph("g1", "G1", flow, {"n_write": {"value": 42.0}})

        # The event_bus.publish should have been called for the write
        assert event_bus.publish.called

    @pytest.mark.asyncio
    async def test_execute_graph_ws_error_ignored(self):
        """If websocket broadcast fails, execution should still succeed."""
        flow = _make_flow()
        mgr, db, event_bus, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        db.execute_and_commit = AsyncMock()

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=Exception("ws not ready")):
            result = await mgr._execute_graph("g1", "G1", flow, {})
        assert isinstance(result, dict)


class TestStartCronTasks:
    def test_start_cron_tasks_no_croniter(self, monkeypatch):
        """When croniter is not installed, cron tasks are skipped."""
        flow = _make_flow(
            nodes=[
                {
                    "id": "c1",
                    "type": "timer_cron",
                    "position": {"x": 0, "y": 0},
                    "data": {"cron": "0 7 * * *"},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})

        with patch.dict("sys.modules", {"croniter": None}):
            mgr._start_cron_tasks()
        # No tasks created since croniter is missing
        assert len(mgr._cron_tasks) == 0

    @pytest.mark.asyncio
    async def test_start_cron_tasks_creates_tasks(self):
        """With croniter installed, cron tasks are created for enabled graphs."""
        flow = _make_flow(
            nodes=[
                {
                    "id": "c1",
                    "type": "timer_cron",
                    "position": {"x": 0, "y": 0},
                    "data": {"cron": "0 7 * * *"},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        mgr._start_cron_tasks()
        assert ("g1", "c1") in mgr._cron_tasks
        # Clean up
        for task in mgr._cron_tasks.values():
            task.cancel()

    @pytest.mark.asyncio
    async def test_start_cron_tasks_ical_node(self):
        """iCal nodes also get scheduled."""
        flow = _make_flow(
            nodes=[
                {
                    "id": "i1",
                    "type": "ical",
                    "position": {"x": 0, "y": 0},
                    "data": {"url": "https://example.com/cal.ics", "refresh_interval_min": 30},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", True, flow)})
        mgr._start_cron_tasks()
        assert ("g1", "i1") in mgr._cron_tasks
        for task in mgr._cron_tasks.values():
            task.cancel()

    @pytest.mark.asyncio
    async def test_start_cron_tasks_skips_disabled(self):
        """Cron tasks not created for disabled graphs."""
        flow = _make_flow(
            nodes=[
                {
                    "id": "c1",
                    "type": "timer_cron",
                    "position": {"x": 0, "y": 0},
                    "data": {"cron": "0 7 * * *"},
                }
            ]
        )
        mgr, _, _, _ = _make_logic_manager(graphs={"g1": ("G1", False, flow)})
        mgr._start_cron_tasks()
        assert ("g1", "c1") not in mgr._cron_tasks
