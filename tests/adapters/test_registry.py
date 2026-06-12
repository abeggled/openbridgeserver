"""Tests for obs.adapters.registry — registration, lifecycle, and helper functions."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import obs.adapters.registry as reg
from obs.adapters.registry import (
    _row_to_binding,
    all_classes,
    all_types,
    get_all_instances,
    get_class,
    get_instance,
    get_instance_by_id,
    get_status,
    register,
    reload_instance_bindings,
    restart_instance,
    start_all,
    start_instance,
    stop_all,
    stop_instance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _MockAdapterInstance:
    """Lightweight fake adapter instance."""

    def __init__(self, adapter_type="TEST", name="test-inst", connected=False):
        self.adapter_type = adapter_type
        self._instance_name = name
        self.connected = connected
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.reload_bindings = AsyncMock()
        self.set_value_getter = MagicMock()


class _MockAdapterClass:
    """Fake adapter class (constructor returns a _MockAdapterInstance)."""

    adapter_type = "TEST"
    hidden = False

    def __init__(self, event_bus, config=None, instance_id=None, name=None, **kwargs):
        self.__class__._last_instance = _MockAdapterInstance(
            adapter_type=self.adapter_type,
            name=name or "test-inst",
        )
        # Copy attributes to self so the registry can use this object
        inst = self.__class__._last_instance
        self.adapter_type = inst.adapter_type
        self._instance_name = inst._instance_name
        self.connected = inst.connected
        self.connect = inst.connect
        self.disconnect = inst.disconnect
        self.reload_bindings = inst.reload_bindings
        self.set_value_getter = inst.set_value_getter

    _last_instance = None


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Isolate _adapters and _instances for each test."""
    monkeypatch.setattr(reg, "_adapters", {})
    monkeypatch.setattr(reg, "_instances", {})


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _make_instance_row(**overrides):
    defaults = {
        "id": str(uuid.uuid4()),
        "adapter_type": "TEST",
        "name": "Test Adapter",
        "config": "{}",
        "enabled": 1,
    }
    defaults.update(overrides)
    return defaults


def _make_binding_row(**overrides):
    now = "2024-01-01T00:00:00"
    defaults = {
        "id": str(uuid.uuid4()),
        "datapoint_id": str(uuid.uuid4()),
        "adapter_type": "TEST",
        "adapter_instance_id": str(uuid.uuid4()),
        "direction": "SOURCE",
        "config": "{}",
        "enabled": 1,
        "send_throttle_ms": None,
        "send_on_change": 0,
        "send_min_delta": None,
        "send_min_delta_pct": None,
        "value_formula": None,
        "value_map": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults


def _make_db(instance_rows=None, binding_rows=None, fetchone_row=None):
    db = MagicMock()

    async def _fetchall(query, *args):
        if "adapter_instances" in query:
            return instance_rows or []
        return binding_rows or []

    db.fetchall = AsyncMock(side_effect=_fetchall)
    db.fetchone = AsyncMock(return_value=fetchone_row)
    return db


# ---------------------------------------------------------------------------
# register() decorator
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_adapter_by_type(self):
        class MyAdapter:
            adapter_type = "MY_TYPE"

        register(MyAdapter)
        assert reg._adapters["MY_TYPE"] is MyAdapter

    def test_raises_when_adapter_type_missing(self):
        class NoType:
            pass

        with pytest.raises(TypeError, match="must define adapter_type"):
            register(NoType)

    def test_raises_when_adapter_type_empty(self):
        class EmptyType:
            adapter_type = ""

        with pytest.raises(TypeError):
            register(EmptyType)

    def test_returns_class_unchanged(self):
        class MyAdapter:
            adapter_type = "ECHO"

        result = register(MyAdapter)
        assert result is MyAdapter


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


class TestLookup:
    def test_get_class_returns_registered(self):
        class A:
            adapter_type = "A"

        reg._adapters["A"] = A
        assert get_class("A") is A

    def test_get_class_returns_none_for_unknown(self):
        assert get_class("UNKNOWN") is None

    def test_all_types_returns_registered_keys(self):
        reg._adapters["X"] = object()
        reg._adapters["Y"] = object()
        types = all_types()
        assert "X" in types
        assert "Y" in types

    def test_all_classes_returns_copy(self):
        cls = object()
        reg._adapters["Z"] = cls
        result = all_classes()
        assert result["Z"] is cls
        result["NEW"] = object()
        assert "NEW" not in reg._adapters  # copy, not original


# ---------------------------------------------------------------------------
# Instance lookup
# ---------------------------------------------------------------------------


class TestInstanceLookup:
    def test_get_instance_by_id_known(self):
        iid = str(uuid.uuid4())
        inst = _MockAdapterInstance()
        reg._instances[iid] = inst
        assert get_instance_by_id(iid) is inst

    def test_get_instance_by_id_uuid_object(self):
        iid = uuid.uuid4()
        inst = _MockAdapterInstance()
        reg._instances[str(iid)] = inst
        assert get_instance_by_id(iid) is inst

    def test_get_instance_by_id_unknown_returns_none(self):
        assert get_instance_by_id("no-such-id") is None

    def test_get_instance_returns_first_matching_type(self):
        inst = _MockAdapterInstance(adapter_type="KNX")
        reg._instances["abc"] = inst
        assert get_instance("KNX") is inst

    def test_get_instance_returns_none_when_no_match(self):
        assert get_instance("NONEXISTENT") is None

    def test_get_all_instances_returns_copy(self):
        inst = _MockAdapterInstance()
        reg._instances["id1"] = inst
        result = get_all_instances()
        assert result["id1"] is inst
        result["id2"] = _MockAdapterInstance()
        assert "id2" not in reg._instances


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_registered_type_with_no_instance(self):
        class Cls:
            adapter_type = "S1"
            hidden = False

        reg._adapters["S1"] = Cls
        status = get_status()
        assert status["S1"]["registered"] is True
        assert status["S1"]["running"] is False
        assert status["S1"]["connected"] is False
        assert status["S1"]["hidden"] is False

    def test_registered_type_with_running_instance(self):
        class Cls:
            adapter_type = "S2"
            hidden = True

        reg._adapters["S2"] = Cls
        inst = _MockAdapterInstance(adapter_type="S2", connected=True)
        reg._instances["x"] = inst
        status = get_status()
        assert status["S2"]["running"] is True
        assert status["S2"]["connected"] is True
        assert status["S2"]["hidden"] is True


# ---------------------------------------------------------------------------
# start_all
# ---------------------------------------------------------------------------


class TestStartAll:
    @pytest.mark.asyncio
    async def test_no_rows_returns_early(self, mock_bus):
        db = _make_db(instance_rows=[])
        await start_all(mock_bus, db)
        assert reg._instances == {}

    @pytest.mark.asyncio
    async def test_unknown_adapter_type_is_skipped(self, mock_bus):
        row = _make_instance_row(adapter_type="GHOST")
        db = _make_db(instance_rows=[row])
        await start_all(mock_bus, db)
        assert reg._instances == {}

    @pytest.mark.asyncio
    async def test_starts_known_adapter_and_loads_bindings(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        row = _make_instance_row()
        iid = row["id"]
        db = _make_db(instance_rows=[row], binding_rows=[])
        await start_all(mock_bus, db)
        assert iid in reg._instances
        inst = reg._instances[iid]
        inst.connect.assert_called_once()
        inst.reload_bindings.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_calls_set_value_getter_when_provided(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        row = _make_instance_row()
        iid = row["id"]
        db = _make_db(instance_rows=[row], binding_rows=[])
        getter = MagicMock()
        await start_all(mock_bus, db, value_getter=getter)
        inst = reg._instances[iid]
        inst.set_value_getter.assert_called_once_with(getter)

    @pytest.mark.asyncio
    async def test_exception_during_start_is_caught(self, mock_bus):
        class ExplodingAdapter:
            adapter_type = "BOOM"

            def __init__(self, **kwargs):
                raise RuntimeError("boom")

        reg._adapters["BOOM"] = ExplodingAdapter
        row = _make_instance_row(adapter_type="BOOM")
        db = _make_db(instance_rows=[row])
        # Must not raise
        await start_all(mock_bus, db)
        assert reg._instances == {}

    @pytest.mark.asyncio
    async def test_parses_config_json(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        row = _make_instance_row(config='{"host": "192.168.1.1", "port": 1883}')
        db = _make_db(instance_rows=[row], binding_rows=[])
        await start_all(mock_bus, db)
        assert len(reg._instances) == 1

    @pytest.mark.asyncio
    async def test_null_config_treated_as_empty_dict(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        row = _make_instance_row(config=None)
        db = _make_db(instance_rows=[row], binding_rows=[])
        await start_all(mock_bus, db)
        assert len(reg._instances) == 1


# ---------------------------------------------------------------------------
# stop_all
# ---------------------------------------------------------------------------


class TestStopAll:
    @pytest.mark.asyncio
    async def test_disconnects_all_instances_and_clears(self):
        inst1 = _MockAdapterInstance()
        inst2 = _MockAdapterInstance()
        reg._instances["a"] = inst1
        reg._instances["b"] = inst2
        await stop_all()
        inst1.disconnect.assert_called_once()
        inst2.disconnect.assert_called_once()
        assert reg._instances == {}

    @pytest.mark.asyncio
    async def test_exception_during_disconnect_is_caught(self):
        inst = _MockAdapterInstance()
        inst.disconnect.side_effect = RuntimeError("disconnect failed")
        reg._instances["x"] = inst
        # Must not raise
        await stop_all()
        assert reg._instances == {}

    @pytest.mark.asyncio
    async def test_no_instances_is_noop(self):
        await stop_all()  # must not raise
        assert reg._instances == {}


# ---------------------------------------------------------------------------
# stop_instance
# ---------------------------------------------------------------------------


class TestStopInstance:
    @pytest.mark.asyncio
    async def test_disconnects_and_removes_from_registry(self):
        iid = str(uuid.uuid4())
        inst = _MockAdapterInstance()
        reg._instances[iid] = inst
        await stop_instance(iid)
        inst.disconnect.assert_called_once()
        assert iid not in reg._instances

    @pytest.mark.asyncio
    async def test_unknown_id_is_noop(self):
        await stop_instance("no-such-id")  # must not raise

    @pytest.mark.asyncio
    async def test_exception_during_disconnect_is_caught(self):
        iid = str(uuid.uuid4())
        inst = _MockAdapterInstance()
        inst.disconnect.side_effect = RuntimeError("oops")
        reg._instances[iid] = inst
        # Must not raise
        await stop_instance(iid)
        assert iid not in reg._instances


# ---------------------------------------------------------------------------
# restart_instance
# ---------------------------------------------------------------------------


class TestRestartInstance:
    @pytest.mark.asyncio
    async def test_disconnects_old_instance_if_present(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        iid = str(uuid.uuid4())
        old_inst = _MockAdapterInstance()
        reg._instances[iid] = old_inst

        row = _make_instance_row(id=iid)
        db = _make_db(fetchone_row=row, binding_rows=[])
        await restart_instance(iid, mock_bus, db)

        old_inst.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_row_not_in_db(self, mock_bus):
        iid = str(uuid.uuid4())
        db = _make_db(fetchone_row=None)
        result = await restart_instance(iid, mock_bus, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_adapter_type(self, mock_bus):
        iid = str(uuid.uuid4())
        row = _make_instance_row(id=iid, adapter_type="GHOST")
        db = _make_db(fetchone_row=row)
        result = await restart_instance(iid, mock_bus, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_new_instance(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        iid = str(uuid.uuid4())
        row = _make_instance_row(id=iid)
        db = _make_db(fetchone_row=row, binding_rows=[])
        result = await restart_instance(iid, mock_bus, db)
        assert result is not None
        assert iid in reg._instances
        result.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_value_getter_on_new_instance(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        iid = str(uuid.uuid4())
        row = _make_instance_row(id=iid)
        db = _make_db(fetchone_row=row, binding_rows=[])
        getter = MagicMock()
        result = await restart_instance(iid, mock_bus, db, value_getter=getter)
        result.set_value_getter.assert_called_once_with(getter)

    @pytest.mark.asyncio
    async def test_exception_during_restart_returns_none(self, mock_bus):
        class ExplodingAdapter:
            adapter_type = "BOOM2"

            def __init__(self, **kwargs):
                raise RuntimeError("explode")

        reg._adapters["BOOM2"] = ExplodingAdapter
        iid = str(uuid.uuid4())
        row = _make_instance_row(id=iid, adapter_type="BOOM2")
        db = _make_db(fetchone_row=row)
        result = await restart_instance(iid, mock_bus, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_disconnecting_old_instance_is_caught(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        iid = str(uuid.uuid4())
        old_inst = _MockAdapterInstance()
        old_inst.disconnect.side_effect = RuntimeError("old disconnect failed")
        reg._instances[iid] = old_inst

        row = _make_instance_row(id=iid)
        db = _make_db(fetchone_row=row, binding_rows=[])
        # Must not raise even if old disconnect explodes
        result = await restart_instance(iid, mock_bus, db)
        assert result is not None


# ---------------------------------------------------------------------------
# start_instance
# ---------------------------------------------------------------------------


class TestStartInstance:
    @pytest.mark.asyncio
    async def test_delegates_to_restart_instance(self, mock_bus):
        reg._adapters["TEST"] = _MockAdapterClass
        iid = str(uuid.uuid4())
        row = _make_instance_row(id=iid)
        db = _make_db(fetchone_row=row, binding_rows=[])
        result = await start_instance(iid, mock_bus, db)
        assert result is not None
        assert iid in reg._instances


# ---------------------------------------------------------------------------
# reload_instance_bindings
# ---------------------------------------------------------------------------


class TestReloadInstanceBindings:
    @pytest.mark.asyncio
    async def test_unknown_instance_is_noop(self):
        await reload_instance_bindings("no-such-id", MagicMock())  # must not raise

    @pytest.mark.asyncio
    async def test_reloads_bindings_for_known_instance(self):
        iid = str(uuid.uuid4())
        inst = _MockAdapterInstance()
        reg._instances[iid] = inst

        db = MagicMock()
        db.fetchall = AsyncMock(return_value=[])
        await reload_instance_bindings(iid, db)

        inst.reload_bindings.assert_called_once_with([])


# ---------------------------------------------------------------------------
# _row_to_binding
# ---------------------------------------------------------------------------


class TestRowToBinding:
    def test_valid_row_returns_adapter_binding(self):
        row = _make_binding_row()
        binding = _row_to_binding(row)
        assert str(binding.id) == row["id"]
        assert str(binding.datapoint_id) == row["datapoint_id"]
        assert binding.direction == "SOURCE"

    def test_null_adapter_instance_id_becomes_none(self):
        row = _make_binding_row(adapter_instance_id=None)
        binding = _row_to_binding(row)
        assert binding.adapter_instance_id is None

    def test_value_map_json_is_parsed(self):
        row = _make_binding_row(value_map='{"0": "off", "1": "on"}')
        binding = _row_to_binding(row)
        assert binding.value_map == {"0": "off", "1": "on"}

    def test_null_value_map_becomes_none(self):
        row = _make_binding_row(value_map=None)
        binding = _row_to_binding(row)
        assert binding.value_map is None

    def test_send_throttle_ms_preserved(self):
        row = _make_binding_row(send_throttle_ms=500)
        binding = _row_to_binding(row)
        assert binding.send_throttle_ms == 500

    def test_value_formula_preserved(self):
        row = _make_binding_row(value_formula="x * 0.1")
        binding = _row_to_binding(row)
        assert binding.value_formula == "x * 0.1"

    def test_null_value_formula_becomes_none(self):
        row = _make_binding_row(value_formula=None)
        binding = _row_to_binding(row)
        assert binding.value_formula is None
