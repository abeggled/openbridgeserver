"""Unit tests for the 1-Wire adapter — owserver/pyownet client.
No real owserver connection; pyownet.protocol.proxy() and the proxy's dir/read/write
are mocked. Uses mocked EventBus; no hardware required.
"""

from __future__ import annotations

import asyncio
import sys
import unittest.mock as mock

import pyownet.protocol as owprotocol
import pytest

from obs.adapters.onewire.adapter import OneWireAdapter
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_create_task(coro, *, name=None, context=None):
    """Side-effect for patched asyncio.create_task — closes the coroutine so Python
    does not emit 'coroutine was never awaited' RuntimeWarnings during GC."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return mock.MagicMock()


def _connected_adapter(mock_bus, **config) -> OneWireAdapter:
    """An adapter with a fake, already-connected proxy — bypasses connect()."""
    adapter = OneWireAdapter(event_bus=mock_bus, config=config)
    adapter._proxy = mock.MagicMock()
    return adapter


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_config_applied(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        assert adapter._cfg.host == "localhost"
        assert adapter._cfg.port == 4304
        assert adapter._cfg.poll_interval == 30.0
        assert adapter._cfg.aliases == {}
        assert adapter._poll_tasks == []
        assert adapter._proxy is None

    def test_config_overrides_applied(self, mock_bus):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"host": "owserver.local", "port": 4305, "poll_interval": 5.0, "aliases": {"28.AA": "Gästebad"}},
        )
        assert adapter._cfg.host == "owserver.local"
        assert adapter._cfg.port == 4305
        assert adapter._cfg.poll_interval == 5.0
        assert adapter._cfg.aliases == {"28.AA": "Gästebad"}


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_lib_not_installed_disables_adapter(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        with mock.patch.dict(sys.modules, {"pyownet": None, "pyownet.protocol": None}):
            await adapter.connect()
        assert adapter._proxy is None
        assert adapter.connected is False
        assert adapter.last_detail_code == "libNotInstalled"

    @pytest.mark.asyncio
    async def test_connection_success_marks_connected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        fake_proxy = mock.MagicMock()
        with mock.patch.object(owprotocol, "proxy", return_value=fake_proxy) as proxy_fn:
            await adapter.connect()
        proxy_fn.assert_called_once_with(host="owserver.local", port=4304, persistent=True)
        assert adapter._proxy is fake_proxy
        assert adapter.connected is True
        assert adapter.last_detail_code == "connectedTo"
        assert adapter.last_detail_params == {"host": "owserver.local", "port": 4304}

    @pytest.mark.asyncio
    async def test_conn_error_leaves_adapter_disconnected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with mock.patch.object(owprotocol, "proxy", side_effect=owprotocol.ConnError("refused")):
            await adapter.connect()
        assert adapter._proxy is None
        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"


# ---------------------------------------------------------------------------
# disconnect()
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_cancels_poll_tasks_and_publishes_status(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        async def sleeper():
            await asyncio.sleep(100)

        adapter._poll_tasks = [asyncio.create_task(sleeper()), asyncio.create_task(sleeper())]

        await adapter.disconnect()

        assert adapter._poll_tasks == []
        assert adapter._proxy is None
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_closes_persistent_connection_when_available(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        close_connection = adapter._proxy.close_connection

        await adapter.disconnect()

        close_connection.assert_called_once()
        assert adapter._proxy is None

    @pytest.mark.asyncio
    async def test_disconnect_with_no_proxy(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        await adapter.disconnect()
        assert adapter._poll_tasks == []
        assert adapter._proxy is None

    @pytest.mark.asyncio
    async def test_disconnect_without_close_connection_attr(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        adapter._proxy = object()  # no close_connection attribute
        await adapter.disconnect()
        assert adapter._proxy is None


# ---------------------------------------------------------------------------
# _on_bindings_reloaded()
# ---------------------------------------------------------------------------


class TestOnBindingsReloaded:
    @pytest.mark.asyncio
    async def test_cancels_old_tasks_before_reload(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})

        async def sleeper():
            await asyncio.sleep(100)

        old_task = asyncio.create_task(sleeper())
        adapter._poll_tasks = [old_task]

        await adapter._on_bindings_reloaded()
        await asyncio.sleep(0)

        assert old_task.cancelled()
        assert adapter._poll_tasks == []

    @pytest.mark.asyncio
    async def test_returns_early_when_not_connected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        adapter._bindings = [make_binding({"sensor_id": "28.AA"})]

        await adapter._on_bindings_reloaded()

        assert adapter._poll_tasks == []

    @pytest.mark.asyncio
    async def test_creates_tasks_for_source_and_both_only(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._bindings = [
            make_binding({"sensor_id": "28.AA"}, direction="SOURCE"),
            make_binding({"sensor_id": "28.BB"}, direction="BOTH"),
            make_binding({"sensor_id": "28.CC"}, direction="DEST"),
        ]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert len(adapter._poll_tasks) == 2


# ---------------------------------------------------------------------------
# _poll_loop()
# ---------------------------------------------------------------------------


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_invalid_binding_config_returns_immediately(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        bad_binding = make_binding({})  # missing sensor_id -> ValidationError

        await adapter._poll_loop(bad_binding)

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_good_quality_event_on_valid_read(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        binding = make_binding({"sensor_id": "28.AA", "property": "temperature"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(return_value=21.5)):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(21.5)
        assert event.quality == "good"

    @pytest.mark.asyncio
    async def test_publishes_bad_quality_when_read_raises(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        binding = make_binding({"sensor_id": "28.AA"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(side_effect=owprotocol.OwnetError(1, "no such path", "/28.AA/temperature"))):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "bad"
        assert event.value is None

    @pytest.mark.asyncio
    async def test_formula_applied_when_set(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        binding = make_binding({"sensor_id": "28.AA"}, value_formula="x * 2")

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(return_value=10.0)):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_value_map_applied_when_set(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        binding = make_binding({"sensor_id": "28.AA"}, value_map={"21.5": "warm"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(return_value=21.5)):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "warm"

    @pytest.mark.asyncio
    async def test_cancelled_error_inside_try_exits_loop_cleanly(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        binding = make_binding({"sensor_id": "28.AA"})

        async def raise_cancelled(ev):
            raise asyncio.CancelledError()

        mock_bus.publish.side_effect = raise_cancelled

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(return_value=21.5)):
            result = await adapter._poll_loop(binding)

        assert result is None


# ---------------------------------------------------------------------------
# _read_property()
# ---------------------------------------------------------------------------


class TestReadProperty:
    @pytest.mark.asyncio
    async def test_numeric_property_parsed_as_float(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"    21.312"

        result = await adapter._read_property("28.AA", "temperature")

        assert result == pytest.approx(21.312)
        adapter._proxy.read.assert_called_once_with("/28.AA/temperature")

    @pytest.mark.asyncio
    async def test_non_numeric_property_returned_as_string(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"DS18B20"

        result = await adapter._read_property("28.AA", "type")

        assert result == "DS18B20"


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


class TestRead:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_connected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        binding = make_binding({"sensor_id": "28.AA"})

        result = await adapter.read(binding)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_sensor_value_when_connected(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        binding = make_binding({"sensor_id": "28.AA"})

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(return_value=22.5)):
            result = await adapter.read(binding)

        assert result == pytest.approx(22.5)

    @pytest.mark.asyncio
    async def test_exception_in_read_returns_none(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        bad_binding = make_binding({})  # missing sensor_id -> ValidationError

        result = await adapter.read(bad_binding)

        assert result is None


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_skipped_when_not_connected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        binding = make_binding({"sensor_id": "29.AA", "property": "PIO.0"})

        await adapter.write(binding, 1)  # should not raise

    @pytest.mark.asyncio
    async def test_write_calls_proxy_write_with_encoded_value(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        binding = make_binding({"sensor_id": "29.AA", "property": "PIO.0"})

        await adapter.write(binding, 1)

        adapter._proxy.write.assert_called_once_with("/29.AA/PIO.0", b"1")

    @pytest.mark.asyncio
    async def test_write_error_is_caught_and_logged(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.write.side_effect = owprotocol.OwnetError(1, "read-only", "/28.AA/temperature")
        binding = make_binding({"sensor_id": "28.AA", "property": "temperature"})

        await adapter.write(binding, 1)  # should not raise

    @pytest.mark.asyncio
    async def test_write_invalid_binding_config_is_caught(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        bad_binding = make_binding({})  # missing sensor_id -> ValidationError

        await adapter.write(bad_binding, 1)  # should not raise


# ---------------------------------------------------------------------------
# browse_sensors()
# ---------------------------------------------------------------------------


class TestBrowseSensors:
    @pytest.mark.asyncio
    async def test_returns_empty_when_not_connected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})

        result = await adapter.browse_sensors()

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_non_rom_id_entries(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path):
            if path == "/":
                return ["/28.4B057F0A1C10", "/settings", "/uncached", "/statistics"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert len(result) == 1
        assert result[0]["rom_id"] == "28.4B057F0A1C10"
        assert result[0]["family"] == "28"

    @pytest.mark.asyncio
    async def test_filters_structural_properties(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path):
            if path == "/":
                return ["/28.4B057F0A1C10"]
            return [
                "/28.4B057F0A1C10/address",
                "/28.4B057F0A1C10/alias",
                "/28.4B057F0A1C10/crc8",
                "/28.4B057F0A1C10/family",
                "/28.4B057F0A1C10/id",
                "/28.4B057F0A1C10/locator",
                "/28.4B057F0A1C10/r_address",
                "/28.4B057F0A1C10/r_id",
                "/28.4B057F0A1C10/r_locator",
                "/28.4B057F0A1C10/type",
                "/28.4B057F0A1C10/version",
                "/28.4B057F0A1C10/temperature",
            ]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert result[0]["properties"] == ["temperature"]

    @pytest.mark.asyncio
    async def test_includes_persisted_alias(self, mock_bus):
        adapter = _connected_adapter(mock_bus, aliases={"28.4B057F0A1C10": "Gästebad Estrich"})

        def fake_dir(path):
            if path == "/":
                return ["/28.4B057F0A1C10"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert result[0]["alias"] == "Gästebad Estrich"

    @pytest.mark.asyncio
    async def test_alias_is_none_when_not_set(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path):
            if path == "/":
                return ["/28.4B057F0A1C10"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert result[0]["alias"] is None

    @pytest.mark.asyncio
    async def test_multiple_sensors(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path):
            if path == "/":
                return ["/28.4B057F0A1C10", "/29.1122334455AA"]
            if path == "/28.4B057F0A1C10":
                return ["/28.4B057F0A1C10/temperature"]
            return ["/29.1122334455AA/PIO.0", "/29.1122334455AA/PIO.1"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        rom_ids = {s["rom_id"] for s in result}
        assert rom_ids == {"28.4B057F0A1C10", "29.1122334455AA"}
