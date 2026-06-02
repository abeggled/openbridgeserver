"""Tests for AdapterBase — concrete helpers and abstract-method super() bodies."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.adapters.base import AdapterBase


class _MinimalAdapter(AdapterBase):
    """Concrete subclass used to exercise AdapterBase directly."""

    adapter_type = "TEST_BASE"
    config_schema = None
    binding_config_schema = None

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def read(self, binding):
        return None

    async def write(self, binding, value): ...


class _SuperCallingAdapter(AdapterBase):
    """Subclass that delegates every abstract method body to super()."""

    adapter_type = "SUPER_TEST"
    config_schema = None
    binding_config_schema = None

    async def connect(self):
        await super().connect()

    async def disconnect(self):
        await super().disconnect()

    async def read(self, binding):
        return await super().read(binding)

    async def write(self, binding, value):
        return await super().write(binding, value)


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


# ---------------------------------------------------------------------------
# __init__ defaults
# ---------------------------------------------------------------------------


class TestAdapterBaseInit:
    def test_defaults(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        assert a._connected is False
        assert a._bindings == []
        assert a.connected is False
        assert a.last_severity == "ok"
        assert a.last_detail == ""

    def test_custom_instance_id_preserved(self, mock_bus):
        iid = uuid.uuid4()
        a = _MinimalAdapter(event_bus=mock_bus, instance_id=iid)
        assert a._instance_id == iid

    def test_name_falls_back_to_adapter_type(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        assert a._instance_name == "TEST_BASE"

    def test_custom_name_stored(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus, name="Living Room KNX")
        assert a._instance_name == "Living Room KNX"


# ---------------------------------------------------------------------------
# Concrete property helpers
# ---------------------------------------------------------------------------


class TestConcreteHelpers:
    def test_get_bindings_returns_copy(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        a._bindings = [1, 2, 3]
        result = a.get_bindings()
        assert result == [1, 2, 3]
        result.append(4)
        assert len(a._bindings) == 3  # original unchanged

    def test_last_severity_property(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        a._last_severity = "warning"
        assert a.last_severity == "warning"

    def test_last_detail_property(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        a._last_detail = "reconnecting"
        assert a.last_detail == "reconnecting"


# ---------------------------------------------------------------------------
# _publish_status
# ---------------------------------------------------------------------------


class TestPublishStatus:
    @pytest.mark.asyncio
    async def test_sets_connected_true_and_publishes(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        await a._publish_status(True, detail="ready")
        assert a.connected is True
        assert a.last_detail == "ready"
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_sets_connected_false(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        a._connected = True
        await a._publish_status(False, detail="lost")
        assert a.connected is False

    @pytest.mark.asyncio
    async def test_warning_severity_does_not_change_connected_flag(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        a._connected = True
        await a._publish_status(False, severity="warning")
        assert a.connected is True  # warning never flips connected
        assert a.last_severity == "warning"


# ---------------------------------------------------------------------------
# reload_bindings
# ---------------------------------------------------------------------------


class TestReloadBindings:
    @pytest.mark.asyncio
    async def test_stores_bindings_and_calls_hook(self, mock_bus):
        a = _MinimalAdapter(event_bus=mock_bus)
        bindings = [object(), object()]
        await a.reload_bindings(bindings)
        assert a.get_bindings() == bindings


# ---------------------------------------------------------------------------
# Abstract method bodies (via super())
# ---------------------------------------------------------------------------


class TestAbstractMethodBodies:
    @pytest.mark.asyncio
    async def test_connect_body_is_noop(self, mock_bus):
        a = _SuperCallingAdapter(event_bus=mock_bus)
        await a.connect()  # must not raise

    @pytest.mark.asyncio
    async def test_disconnect_body_is_noop(self, mock_bus):
        a = _SuperCallingAdapter(event_bus=mock_bus)
        await a.disconnect()

    @pytest.mark.asyncio
    async def test_read_body_returns_none(self, mock_bus):
        a = _SuperCallingAdapter(event_bus=mock_bus)
        result = await a.read(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_write_body_is_noop(self, mock_bus):
        a = _SuperCallingAdapter(event_bus=mock_bus)
        await a.write(None, None)  # must not raise
