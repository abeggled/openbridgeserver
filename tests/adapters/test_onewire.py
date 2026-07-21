"""Unit tests for the 1-Wire adapter — owserver/pyownet client.
No real owserver connection; pyownet.protocol.proxy() and the proxy's dir/read/write
are mocked. Uses mocked EventBus; no hardware required.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import time
import unittest.mock as mock

import pyownet.protocol as owprotocol
import pytest

from obs.adapters.onewire.adapter import OneWireAdapter, _normalize_sensor_id
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
    adapter._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return adapter


# ---------------------------------------------------------------------------
# _normalize_sensor_id()
# ---------------------------------------------------------------------------


class TestNormalizeSensorId:
    @pytest.mark.parametrize(
        "legacy, expected",
        [
            ("28-000000000001", "28.000000000001"),
            ("29-1122334455aa", "29.1122334455aa"),
            ("29-1122334455AA", "29.1122334455AA"),
        ],
    )
    def test_converts_hyphenated_legacy_id(self, legacy, expected):
        assert _normalize_sensor_id(legacy) == expected

    @pytest.mark.parametrize("already_dotted", ["28.4B057F0A1C10", "boiler", "29.1122334455aa"])
    def test_leaves_non_legacy_ids_unchanged(self, already_dotted):
        assert _normalize_sensor_id(already_dotted) == already_dotted


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_config_applied(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={})
        assert adapter._cfg.host == "localhost"
        assert adapter._cfg.port == 4304
        assert adapter._cfg.poll_interval == 30.0
        assert adapter._cfg.request_timeout == 10.0
        assert adapter._cfg.aliases == {}
        assert adapter._poll_tasks == []
        assert adapter._proxy is None

    def test_config_overrides_applied(self, mock_bus):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={
                "host": "owserver.local",
                "port": 4305,
                "poll_interval": 5.0,
                "request_timeout": 2.5,
                "aliases": {"28.AA": "Gästebad"},
            },
        )
        assert adapter._cfg.host == "owserver.local"
        assert adapter._cfg.port == 4305
        assert adapter._cfg.poll_interval == 5.0
        assert adapter._cfg.request_timeout == 2.5
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
        with (
            mock.patch.object(owprotocol, "proxy", side_effect=owprotocol.ConnError("refused")),
            mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task),
        ):
            await adapter.connect()
        assert adapter._proxy is None
        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"

    @pytest.mark.asyncio
    async def test_protocol_error_leaves_adapter_disconnected(self, mock_bus):
        """host:port reachable but not actually owserver — pyownet raises a
        ProtocolError subclass (MalformedHeader), not ConnError."""
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with (
            mock.patch.object(owprotocol, "proxy", side_effect=owprotocol.MalformedHeader("garbage", b"garbage")),
            mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task),
        ):
            await adapter.connect()
        assert adapter._proxy is None
        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"

    @pytest.mark.asyncio
    async def test_timeout_leaves_adapter_disconnected(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with (
            mock.patch.object(owprotocol, "proxy", side_effect=owprotocol.OwnetTimeout(1.0, 1.0)),
            mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task),
        ):
            await adapter.connect()
        assert adapter._proxy is None
        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"

    @pytest.mark.asyncio
    async def test_failed_connect_spawns_reconnect_task(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with (
            mock.patch.object(owprotocol, "proxy", side_effect=owprotocol.ConnError("refused")),
            mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task) as create_task,
        ):
            await adapter.connect()
        create_task.assert_called_once()
        assert adapter._reconnect_task is not None

    @pytest.mark.asyncio
    async def test_successful_connect_does_not_spawn_reconnect_task(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with (
            mock.patch.object(owprotocol, "proxy", return_value=mock.MagicMock()),
            mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task) as create_task,
        ):
            await adapter.connect()
        create_task.assert_not_called()
        assert adapter._reconnect_task is None

    @pytest.mark.asyncio
    async def test_successful_connect_stores_owprotocol_module(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with mock.patch.object(owprotocol, "proxy", return_value=mock.MagicMock()):
            await adapter.connect()
        assert adapter._owprotocol is owprotocol

    @pytest.mark.asyncio
    async def test_reconnect_shuts_down_previous_executor(self, mock_bus):
        """A restart (connect() called again without an intervening disconnect())
        must not leak the previous single-worker thread pool."""
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        with mock.patch.object(owprotocol, "proxy", return_value=mock.MagicMock()):
            await adapter.connect()
            first_executor = adapter._executor
            await adapter.connect()

        assert adapter._executor is not first_executor
        assert first_executor._shutdown is True


# ---------------------------------------------------------------------------
# _reconnect_loop()
# ---------------------------------------------------------------------------


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_retries_until_success_then_reloads_bindings(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        adapter._owprotocol = owprotocol
        call_count = 0

        def flaky_proxy(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise owprotocol.ConnError("still down")
            return mock.MagicMock()

        with (
            mock.patch.object(owprotocol, "proxy", side_effect=flaky_proxy),
            mock.patch.object(asyncio, "sleep", new=mock.AsyncMock()) as sleep_mock,
            mock.patch.object(adapter, "_on_bindings_reloaded", new=mock.AsyncMock()) as reload_mock,
        ):
            await adapter._reconnect_loop()

        assert call_count == 3
        assert sleep_mock.await_count == 3
        assert adapter.connected is True
        reload_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exits_cleanly_on_cancellation(self, mock_bus):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"host": "owserver.local", "port": 4304})
        adapter._owprotocol = owprotocol
        with mock.patch.object(asyncio, "sleep", side_effect=asyncio.CancelledError):
            await adapter._reconnect_loop()  # must not raise
        assert adapter._proxy is None


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
    async def test_cancels_pending_reconnect_task(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        task = mock.MagicMock()
        adapter._reconnect_task = task

        await adapter.disconnect()

        task.cancel.assert_called_once()
        assert adapter._reconnect_task is None

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

    @pytest.mark.asyncio
    async def test_close_waits_for_in_flight_owlock_holder(self, mock_bus):
        """pyownet's proxy is not concurrency-safe — close_connection() must not
        run while another task still holds _owlock for an in-flight read/write."""
        adapter = _connected_adapter(mock_bus)
        events: list[str] = []

        async def hold_lock_briefly():
            async with adapter._owlock:
                events.append("lock_acquired")
                await asyncio.sleep(0.05)
                events.append("lock_released")

        adapter._proxy.close_connection = mock.Mock(side_effect=lambda: events.append("close_called"))

        holder = asyncio.create_task(hold_lock_briefly())
        await asyncio.sleep(0.01)  # let the holder acquire the lock first
        await adapter.disconnect()
        await holder

        assert events == ["lock_acquired", "lock_released", "close_called"]

    @pytest.mark.asyncio
    async def test_close_does_not_race_orphaned_poll_thread(self, mock_bus):
        """Cancelling a task blocked on run_in_executor() marks its asyncio-side
        future cancelled immediately — it does not stop the underlying thread,
        which keeps running the blocking pyownet call to completion. disconnect()
        must not let close_connection() run while that orphaned call is still
        using the same non-thread-safe proxy (see module docstring)."""
        adapter = _connected_adapter(mock_bus)
        events: list[str] = []

        def blocking_read(path, timeout=None):
            events.append("read_start")
            time.sleep(0.1)
            events.append("read_end")
            return b"1"

        adapter._proxy.read.side_effect = blocking_read
        adapter._proxy.close_connection.side_effect = lambda: events.append("close_called")

        task = asyncio.create_task(adapter._read_property("28.AA", "temperature"))
        await asyncio.sleep(0.02)  # let the worker thread start blocking_read
        task.cancel()

        await adapter.disconnect()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert events == ["read_start", "read_end", "close_called"]


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

    @pytest.mark.asyncio
    async def test_marks_adapter_disconnected_on_connection_level_error(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        adapter._owprotocol = owprotocol
        await adapter._publish_status(True, "connected", code="connectedTo", params={"host": "h", "port": 1})
        binding = make_binding({"sensor_id": "28.AA"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(adapter, "_read_property", mock.AsyncMock(side_effect=owprotocol.ConnError("gone"))):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"

    @pytest.mark.asyncio
    async def test_per_property_ownet_error_does_not_mark_adapter_disconnected(self, mock_bus):
        """OwnetError means owserver answered with an error for this one path
        (e.g. sensor unplugged) — owserver itself is fine, so the adapter must
        stay marked connected."""
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        adapter._owprotocol = owprotocol
        await adapter._publish_status(True, "connected", code="connectedTo", params={"host": "h", "port": 1})
        binding = make_binding({"sensor_id": "28.AA"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch.object(
            adapter,
            "_read_property",
            mock.AsyncMock(side_effect=owprotocol.OwnetError(1, "no such path", "/28.AA/temperature")),
        ):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert adapter.connected is True

    @pytest.mark.asyncio
    async def test_republishes_connected_after_recovering_from_connection_loss(self, mock_bus):
        adapter = _connected_adapter(mock_bus, poll_interval=0.0)
        adapter._owprotocol = owprotocol
        await adapter._publish_status(False, "lost", code="couldNotConnectTo", params={"host": "h", "port": 1})
        binding = make_binding({"sensor_id": "28.AA"})
        assert adapter.connected is False

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

        assert adapter.connected is True
        assert adapter.last_detail_code == "connectedTo"


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
        adapter._proxy.read.assert_called_once_with("/28.AA/temperature", timeout=adapter._cfg.request_timeout)

    @pytest.mark.asyncio
    async def test_normalizes_legacy_hyphenated_sensor_id(self, mock_bus):
        """Bindings created by the pre-owserver sysfs adapter store ROM IDs as
        "28-000000000001"; owserver paths need the dotted OWFS form."""
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"21.5"

        await adapter._read_property("28-000000000001", "temperature")

        adapter._proxy.read.assert_called_once_with("/28.000000000001/temperature", timeout=adapter._cfg.request_timeout)

    @pytest.mark.asyncio
    async def test_uses_configured_request_timeout(self, mock_bus):
        adapter = _connected_adapter(mock_bus, request_timeout=2.5)
        adapter._proxy.read.return_value = b"21.5"

        await adapter._read_property("28.AA", "temperature")

        adapter._proxy.read.assert_called_once_with("/28.AA/temperature", timeout=2.5)

    @pytest.mark.asyncio
    async def test_non_numeric_property_returned_as_string(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"DS18B20"

        result = await adapter._read_property("28.AA", "type")

        assert result == "DS18B20"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("property_name", ["PIO.0", "pio.1", "sensed.0", "latch.1", "power", "present", "PIO.A", "sensed.B"])
    @pytest.mark.parametrize("raw, expected", [(b"1", True), (b"0", False)])
    async def test_yesno_properties_parsed_as_bool(self, mock_bus, property_name, raw, expected):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = raw

        result = await adapter._read_property("29.AA", property_name)

        assert result is expected

    @pytest.mark.asyncio
    async def test_non_yesno_property_still_parsed_as_float(self, mock_bus):
        # A DS18B20 "temperature" containing "1" would previously and still
        # parses as a numeric float — only known yes/no property names take
        # the bool branch.
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"1"

        result = await adapter._read_property("28.AA", "temperature")

        assert result == pytest.approx(1.0)
        assert not isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_yesno_property_falls_back_to_string_when_not_parseable_as_int(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"unexpected"

        result = await adapter._read_property("29.AA", "PIO.0")

        assert result == "unexpected"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("property_name", ["PIO.ALL", "pio.byte", "sensed.ALL", "latch.BYTE", "PIO.byte"])
    async def test_aggregate_byte_and_all_properties_not_parsed_as_bool(self, mock_bus, property_name):
        # PIO.ALL/sensed.ALL/latch.ALL are comma-separated per-channel lists and
        # PIO.BYTE/etc. are a 0-255 bitmask across all channels — neither is a
        # single yes/no value, so collapsing either to bool would lose data.
        adapter = _connected_adapter(mock_bus)
        adapter._proxy.read.return_value = b"255"

        result = await adapter._read_property("29.AA", property_name)

        assert result == pytest.approx(255.0)
        assert not isinstance(result, bool)


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

        adapter._proxy.write.assert_called_once_with("/29.AA/PIO.0", b"1", timeout=adapter._cfg.request_timeout)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value, expected", [(True, b"1"), (False, b"0")])
    async def test_write_encodes_bool_as_1_or_0(self, mock_bus, value, expected):
        # str(True)/str(False) would send the literal words "True"/"False",
        # which owserver would reject or misinterpret for a yes/no property.
        adapter = _connected_adapter(mock_bus)
        binding = make_binding({"sensor_id": "29.AA", "property": "PIO.0"})

        await adapter.write(binding, value)

        adapter._proxy.write.assert_called_once_with("/29.AA/PIO.0", expected, timeout=adapter._cfg.request_timeout)

    @pytest.mark.asyncio
    async def test_write_normalizes_legacy_hyphenated_sensor_id(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        binding = make_binding({"sensor_id": "29-1122334455aa", "property": "PIO.0"})

        await adapter.write(binding, 1)

        adapter._proxy.write.assert_called_once_with("/29.1122334455aa/PIO.0", b"1", timeout=adapter._cfg.request_timeout)

    @pytest.mark.asyncio
    async def test_write_marks_adapter_disconnected_on_connection_level_error(self, mock_bus):
        # A DEST-only instance (write-only bindings) starts no poll task, so
        # write() is the only place that ever observes a lost owserver
        # connection — without this, the UI would keep reporting the instance
        # healthy while writes silently fail.
        adapter = _connected_adapter(mock_bus)
        adapter._owprotocol = owprotocol
        await adapter._publish_status(True, "connected", code="connectedTo", params={"host": "h", "port": 1})
        adapter._proxy.write.side_effect = owprotocol.ConnError("gone")
        binding = make_binding({"sensor_id": "29.AA", "property": "PIO.0"})

        await adapter.write(binding, 1)

        assert adapter.connected is False
        assert adapter.last_detail_code == "couldNotConnectTo"

    @pytest.mark.asyncio
    async def test_write_per_property_ownet_error_does_not_mark_adapter_disconnected(self, mock_bus):
        adapter = _connected_adapter(mock_bus)
        adapter._owprotocol = owprotocol
        await adapter._publish_status(True, "connected", code="connectedTo", params={"host": "h", "port": 1})
        adapter._proxy.write.side_effect = owprotocol.OwnetError(1, "not writable", "/29.AA/PIO.0")
        binding = make_binding({"sensor_id": "29.AA", "property": "PIO.0"})

        await adapter.write(binding, 1)

        assert adapter.connected is True

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

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/28.4B057F0A1C10", "/settings", "/uncached", "/statistics"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir
        # Real owserver: these are system/meta directories, not devices — reading
        # their "address" property fails with OwnetError, same as a real server.
        adapter._proxy.read.side_effect = owprotocol.OwnetError(1, "no such path", "/settings/address")

        result = await adapter.browse_sensors()

        assert len(result) == 1
        assert result[0]["rom_id"] == "28.4B057F0A1C10"
        assert result[0]["family"] == "28"

    @pytest.mark.asyncio
    async def test_resolves_owfs_alias_via_address_property(self, mock_bus):
        """If OWFS aliases are configured (/etc/owfs.conf), the root directory can
        show an alias name (e.g. "/boiler/") instead of the bare ROM-ID — resolve
        it via the device's own "address" property rather than skipping it."""
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/boiler"]
            return ["/boiler/temperature"]

        def fake_read(path, **kwargs):
            assert path == "/boiler/address"
            return b"28.4B057F0A1C10"

        adapter._proxy.dir.side_effect = fake_dir
        adapter._proxy.read.side_effect = fake_read

        result = await adapter.browse_sensors()

        assert len(result) == 1
        assert result[0]["rom_id"] == "boiler"
        assert result[0]["family"] == "28"
        assert result[0]["properties"] == ["temperature"]

    @pytest.mark.asyncio
    async def test_resolves_owfs_alias_via_raw_16_hex_address(self, mock_bus):
        """owserver's "address" property on a real device dir is the raw 64-bit
        ROM code — 16 hex chars, family+serial+crc8, with no dot — not the dotted
        family.serial shape used elsewhere; both must resolve to a real device."""
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/boiler"]
            return ["/boiler/temperature"]

        def fake_read(path, **kwargs):
            assert path == "/boiler/address"
            return b"284B057F0A1C1042"  # family=28, serial=4B057F0A1C10, crc8=42

        adapter._proxy.dir.side_effect = fake_dir
        adapter._proxy.read.side_effect = fake_read

        result = await adapter.browse_sensors()

        assert len(result) == 1
        assert result[0]["rom_id"] == "boiler"
        assert result[0]["family"] == "28"

    @pytest.mark.asyncio
    async def test_skips_entries_with_non_rom_id_shaped_address(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/structure"]
            return []

        adapter._proxy.dir.side_effect = fake_dir
        adapter._proxy.read.return_value = b"not-a-rom-id"

        result = await adapter.browse_sensors()

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_real_owserver_trailing_slash_directories(self, mock_bus):
        """Regression test (issue #6 smoke test against a real owserver, fake DS18B20/DS2408):
        owserver's default DIRALLSLASH form appends "/" to directory entries — both the
        ROM-ID itself at the root, and nested sub-groups per sensor (e.g. DS18B20's
        "errata/") that are not readable leaf properties and must be excluded."""
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/28.A7F1D92A82C8/"]
            return [
                "/28.A7F1D92A82C8/errata/",
                "/28.A7F1D92A82C8/temperature",
                "/28.A7F1D92A82C8/power",
            ]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert len(result) == 1
        assert result[0]["rom_id"] == "28.A7F1D92A82C8"
        assert result[0]["properties"] == ["power", "temperature"]

    @pytest.mark.asyncio
    async def test_filters_structural_properties(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
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

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/28.4B057F0A1C10"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert result[0]["alias"] == "Gästebad Estrich"

    @pytest.mark.asyncio
    async def test_alias_is_none_when_not_set(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/28.4B057F0A1C10"]
            return ["/28.4B057F0A1C10/temperature"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        assert result[0]["alias"] is None

    @pytest.mark.asyncio
    async def test_multiple_sensors(self, mock_bus):
        adapter = _connected_adapter(mock_bus)

        def fake_dir(path, **kwargs):
            if path == "/":
                return ["/28.4B057F0A1C10", "/29.1122334455AA"]
            if path == "/28.4B057F0A1C10":
                return ["/28.4B057F0A1C10/temperature"]
            return ["/29.1122334455AA/PIO.0", "/29.1122334455AA/PIO.1"]

        adapter._proxy.dir.side_effect = fake_dir

        result = await adapter.browse_sensors()

        rom_ids = {s["rom_id"] for s in result}
        assert rom_ids == {"28.4B057F0A1C10", "29.1122334455AA"}
