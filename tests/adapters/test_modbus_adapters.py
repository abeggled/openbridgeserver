"""Unit tests for Modbus TCP and Modbus RTU adapters.

Both adapters share identical logic (_modbus_call, _read_register,
_write_register, _poll_loop) — they differ only in their config model
and the pymodbus client class they instantiate.

All pymodbus and network calls are mocked; no hardware required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.adapters.modbus_base import ModbusBindingConfig
from obs.adapters.modbus_rtu.adapter import ModbusRtuAdapter, ModbusRtuAdapterConfig
from obs.adapters.modbus_tcp.adapter import ModbusTcpAdapter, ModbusTcpAdapterConfig
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_TCP_CFG = {"host": "127.0.0.1", "port": 502, "timeout": 1.0}
_DEFAULT_RTU_CFG = {"port": "/dev/ttyUSB0", "baudrate": 9600, "parity": "N", "stopbits": 1, "bytesize": 8, "timeout": 1.0}

_HOLDING_CFG = {"unit_id": 1, "register_type": "holding", "address": 0, "data_format": "uint16", "poll_interval": 0.05}
_INPUT_CFG = {"unit_id": 1, "register_type": "input", "address": 0, "data_format": "uint16", "poll_interval": 0.05}
_COIL_CFG = {"unit_id": 1, "register_type": "coil", "address": 0, "data_format": "uint16", "poll_interval": 0.05}
_DISCRETE_CFG = {"unit_id": 1, "register_type": "discrete_input", "address": 0, "data_format": "uint16", "poll_interval": 0.05}


def _mock_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _ok_response(registers=None, bits=None):
    r = MagicMock()
    r.isError.return_value = False
    r.registers = registers or [42]
    r.bits = bits or [True, False]
    return r


def _error_response():
    r = MagicMock()
    r.isError.return_value = True
    return r


def _make_tcp(config=None) -> tuple[ModbusTcpAdapter, MagicMock]:
    bus = _mock_bus()
    adapter = ModbusTcpAdapter(bus, config or _DEFAULT_TCP_CFG)
    return adapter, bus


def _make_rtu(config=None) -> tuple[ModbusRtuAdapter, MagicMock]:
    bus = _mock_bus()
    adapter = ModbusRtuAdapter(bus, config or _DEFAULT_RTU_CFG)
    return adapter, bus


def _make_client(connected=True, response=None):
    client = MagicMock()
    client.connected = connected
    resp = response or _ok_response()
    client.read_holding_registers = AsyncMock(return_value=resp)
    client.read_input_registers = AsyncMock(return_value=resp)
    client.read_coils = AsyncMock(return_value=resp)
    client.read_discrete_inputs = AsyncMock(return_value=resp)
    client.write_coil = AsyncMock(return_value=_ok_response())
    client.write_register = AsyncMock(return_value=_ok_response())
    client.write_registers = AsyncMock(return_value=_ok_response())
    client.connect = AsyncMock()
    client.close = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class TestModbusTcpAdapterConfig:
    def test_defaults(self):
        cfg = ModbusTcpAdapterConfig()
        assert cfg.host == "192.168.1.1"
        assert cfg.port == 502
        assert cfg.timeout == 3.0

    def test_custom(self):
        cfg = ModbusTcpAdapterConfig(host="10.0.0.1", port=503, timeout=5.0)
        assert cfg.host == "10.0.0.1"


class TestModbusRtuAdapterConfig:
    def test_defaults(self):
        cfg = ModbusRtuAdapterConfig()
        assert cfg.port == "/dev/ttyUSB0"
        assert cfg.baudrate == 9600
        assert cfg.parity == "N"
        assert cfg.stopbits == 1
        assert cfg.bytesize == 8

    def test_custom(self):
        cfg = ModbusRtuAdapterConfig(port="COM3", baudrate=19200, parity="E")
        assert cfg.parity == "E"


# ---------------------------------------------------------------------------
# connect / disconnect — TCP
# ---------------------------------------------------------------------------


class TestModbusTcpConnect:
    async def test_connect_success_publishes_status(self):
        adapter, bus = _make_tcp()
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        bus.publish.assert_awaited()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is True

    async def test_connect_not_connected_publishes_failure(self):
        adapter, bus = _make_tcp()
        client = _make_client(connected=False)
        client.connect = AsyncMock()
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_connect_exception_publishes_failure(self):
        adapter, bus = _make_tcp()
        client = MagicMock()
        client.connect = AsyncMock(side_effect=OSError("refused"))
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_connect_import_error_publishes_failure(self):
        adapter, bus = _make_tcp()
        with patch.dict("sys.modules", {"pymodbus.client": None}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_disconnect_cancels_tasks_and_closes(self):
        adapter, bus = _make_tcp()
        client = _make_client()
        adapter._client = client
        t = asyncio.create_task(asyncio.sleep(100))
        adapter._poll_tasks.append(t)
        await adapter.disconnect()
        await asyncio.sleep(0)  # let event loop process cancellation
        assert t.cancelled()
        client.close.assert_called_once()


# ---------------------------------------------------------------------------
# connect / disconnect — RTU
# ---------------------------------------------------------------------------


class TestModbusRtuConnect:
    async def test_connect_success(self):
        adapter, bus = _make_rtu()
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusSerialClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is True

    async def test_connect_not_connected_publishes_failure(self):
        adapter, bus = _make_rtu()
        client = _make_client(connected=False)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusSerialClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_connect_import_error(self):
        adapter, bus = _make_rtu()
        with patch.dict("sys.modules", {"pymodbus.client": None}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_disconnect_cancels_tasks(self):
        adapter, bus = _make_rtu()
        client = _make_client()
        adapter._client = client
        t = asyncio.create_task(asyncio.sleep(100))
        adapter._poll_tasks.append(t)
        await adapter.disconnect()
        await asyncio.sleep(0)
        assert t.cancelled()
        client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _modbus_call — version-safe dispatch (shared logic, tested via TCP)
# ---------------------------------------------------------------------------


class TestModbusCall:
    async def test_first_slave_variant_succeeds(self):
        adapter, _ = _make_tcp()
        fn = AsyncMock(return_value="ok")
        result = await adapter._modbus_call(fn, 0, 1, unit_id=1)
        assert result == "ok"

    async def test_falls_through_to_second_variant(self):
        adapter, _ = _make_tcp()
        call_count = 0

        async def fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "device_id" in kwargs:
                raise TypeError("unexpected keyword device_id")
            return "ok"

        result = await adapter._modbus_call(fn, 0, 1, unit_id=1)
        assert result == "ok"

    async def test_keyword_fallback_when_all_positional_fail(self):
        """When all positional variants raise TypeError, try kwargs."""
        adapter, _ = _make_tcp()

        async def fn(**kwargs):
            if "device_id" in kwargs or "slave" in kwargs or "unit" in kwargs:
                raise TypeError("bad kwarg")
            # Only accepts address= and count= (keyword-only, no slave)
            if "address" in kwargs and "count" in kwargs:
                return "kw-ok"
            raise TypeError("unexpected")

        result = await adapter._modbus_call(fn, 0, 1, unit_id=1)
        assert result == "kw-ok"

    async def test_raises_when_all_variants_fail(self):
        adapter, _ = _make_tcp()

        async def fn(**kwargs):
            raise TypeError("always fails")

        with pytest.raises(RuntimeError, match="pymodbus"):
            await adapter._modbus_call(fn, 0, 1, unit_id=1)


# ---------------------------------------------------------------------------
# _read_register — all register types (TCP)
# ---------------------------------------------------------------------------


class TestReadRegisterTcp:
    async def _read(self, adapter, bc_config, client):
        adapter._client = client
        bc = ModbusBindingConfig(**bc_config)
        return await adapter._read_register(bc)

    async def test_holding_register_returns_value(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response([1234]))
        result = await self._read(adapter, _HOLDING_CFG, client)
        assert result == 1234

    async def test_input_register_returns_value(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response([5678]))
        result = await self._read(adapter, _INPUT_CFG, client)
        assert result == 5678

    async def test_coil_returns_bool(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response(bits=[True]))
        result = await self._read(adapter, _COIL_CFG, client)
        assert result is True

    async def test_discrete_input_returns_bool(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response(bits=[False]))
        result = await self._read(adapter, _DISCRETE_CFG, client)
        assert result is False

    async def test_error_response_returns_none(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_error_response())
        result = await self._read(adapter, _HOLDING_CFG, client)
        assert result is None

    async def test_not_connected_returns_none(self):
        adapter, _ = _make_tcp()
        client = _make_client(connected=False)
        result = await self._read(adapter, _HOLDING_CFG, client)
        assert result is None

    async def test_unknown_register_type_returns_none(self):
        """Patch ModbusBindingConfig to bypass Pydantic validation and test the else-branch."""
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        bc = MagicMock(spec=ModbusBindingConfig)
        bc.data_format = "uint16"
        bc.register_type = "unknown_type"
        bc.address = 0
        bc.unit_id = 1
        bc.byte_order = "big"
        bc.word_order = "big"
        bc.scale_factor = 1.0
        result = await adapter._read_register(bc)
        assert result is None

    async def test_float32_decoded(self):
        import struct

        adapter, _ = _make_tcp()
        regs = list(struct.unpack(">HH", struct.pack(">f", 3.14)))
        client = _make_client(response=_ok_response(registers=regs))
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "float32"})
        adapter._client = client
        result = await adapter._read_register(bc)
        assert abs(result - 3.14) < 1e-5


# ---------------------------------------------------------------------------
# _write_register — TCP
# ---------------------------------------------------------------------------


class TestWriteRegisterTcp:
    async def test_write_coil(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_COIL_CFG)
        await adapter._write_register(bc, True)
        client.write_coil.assert_awaited_once()

    async def test_write_holding_single_register(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_HOLDING_CFG)
        await adapter._write_register(bc, 42)
        client.write_register.assert_awaited_once()

    async def test_write_holding_multi_register_float32(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "float32"})
        await adapter._write_register(bc, 3.14)
        client.write_registers.assert_awaited_once()

    async def test_write_non_coil_non_holding_is_noop(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_INPUT_CFG)
        # Should not raise, just be a noop
        await adapter._write_register(bc, 1)
        client.write_register.assert_not_awaited()


# ---------------------------------------------------------------------------
# read() / write() public methods — TCP
# ---------------------------------------------------------------------------


class TestPublicReadWriteTcp:
    async def test_read_returns_value(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response([99]))
        adapter._client = client
        binding = make_binding(_HOLDING_CFG)
        result = await adapter.read(binding)
        assert result == 99

    async def test_read_exception_returns_none(self):
        adapter, _ = _make_tcp()
        adapter._client = MagicMock()
        adapter._client.connected = True
        adapter._client.read_holding_registers = AsyncMock(side_effect=Exception("boom"))
        binding = make_binding(_HOLDING_CFG)
        result = await adapter.read(binding)
        assert result is None

    async def test_write_calls_write_register(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        binding = make_binding(_HOLDING_CFG, direction="DEST")
        await adapter.write(binding, 100)
        client.write_register.assert_awaited_once()

    async def test_write_skipped_when_not_connected(self):
        adapter, _ = _make_tcp()
        adapter._client = _make_client(connected=False)
        binding = make_binding(_HOLDING_CFG, direction="DEST")
        await adapter.write(binding, 100)  # should not raise

    async def test_write_skipped_when_no_client(self):
        adapter, _ = _make_tcp()
        adapter._client = None
        binding = make_binding(_HOLDING_CFG)
        await adapter.write(binding, 1)  # should not raise

    async def test_write_exception_does_not_propagate(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        client.write_register = AsyncMock(side_effect=Exception("write failed"))
        adapter._client = client
        binding = make_binding(_HOLDING_CFG)
        await adapter.write(binding, 42)  # should not raise


# ---------------------------------------------------------------------------
# read() / write() — RTU (same logic, just verify RTU adapter wires up)
# ---------------------------------------------------------------------------


class TestPublicReadWriteRtu:
    async def test_read_returns_value(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_ok_response([77]))
        adapter._client = client
        binding = make_binding(_HOLDING_CFG)
        result = await adapter.read(binding)
        assert result == 77

    async def test_write_coil(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        binding = make_binding(_COIL_CFG, direction="DEST")
        await adapter.write(binding, True)
        client.write_coil.assert_awaited_once()


# ---------------------------------------------------------------------------
# _on_bindings_reloaded — TCP
# ---------------------------------------------------------------------------


class TestBindingsReloadedTcp:
    async def test_creates_poll_tasks_for_source_bindings(self):
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client
        b = make_binding(_HOLDING_CFG, direction="SOURCE")
        adapter._bindings = [b]

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            await adapter._on_bindings_reloaded()
            assert len(adapter._poll_tasks) == 1

        for t in adapter._poll_tasks:
            t.cancel()

    async def test_dest_bindings_not_polled(self):
        adapter, _ = _make_tcp()
        adapter._client = _make_client()
        b = make_binding(_HOLDING_CFG, direction="DEST")
        adapter._bindings = [b]

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            await adapter._on_bindings_reloaded()
            assert len(adapter._poll_tasks) == 0

    async def test_reload_cancels_old_tasks(self):
        adapter, _ = _make_tcp()
        adapter._client = _make_client()
        old_task = asyncio.create_task(asyncio.sleep(100))
        adapter._poll_tasks.append(old_task)
        adapter._bindings = []

        await adapter._on_bindings_reloaded()
        await asyncio.sleep(0)  # let cancellation propagate
        assert old_task.cancelled()
        assert len(adapter._poll_tasks) == 0


# ---------------------------------------------------------------------------
# _on_bindings_reloaded — RTU
# ---------------------------------------------------------------------------


class TestBindingsReloadedRtu:
    async def test_creates_poll_tasks(self):
        adapter, _ = _make_rtu()
        adapter._client = _make_client()
        b = make_binding(_HOLDING_CFG, direction="SOURCE")
        adapter._bindings = [b]

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            await adapter._on_bindings_reloaded()
            assert len(adapter._poll_tasks) == 1

        for t in adapter._poll_tasks:
            t.cancel()


# ---------------------------------------------------------------------------
# _poll_loop — TCP (exercises value_formula, value_map, error paths)
# ---------------------------------------------------------------------------


class TestPollLoopTcp:
    async def test_poll_publishes_good_value(self):
        adapter, bus = _make_tcp()
        adapter._client = _make_client(response=_ok_response([10]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        async def one_iteration():
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await one_iteration()
        events = [c.args[0] for c in bus.publish.call_args_list]
        good_events = [e for e in events if hasattr(e, "quality") and e.quality == "good"]
        assert len(good_events) >= 1
        assert good_events[0].value == 10

    async def test_poll_applies_value_formula(self):
        adapter, bus = _make_tcp()
        adapter._client = _make_client(response=_ok_response([10]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE", value_formula="x * 2")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        good = [e for e in events if e.quality == "good"]
        assert good[0].value == 20

    async def test_poll_applies_value_map(self):
        adapter, bus = _make_tcp()
        adapter._client = _make_client(response=_ok_response([1]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE", value_map={"1": "ON", "0": "OFF"})

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        good = [e for e in events if e.quality == "good"]
        assert good[0].value == "ON"

    async def test_poll_publishes_bad_on_read_none(self):
        adapter, bus = _make_tcp()
        # Client disconnected → _read_register returns None
        adapter._client = _make_client(connected=False)
        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        assert all(e.quality == "bad" for e in events)

    async def test_poll_publishes_bad_on_exception(self):
        adapter, bus = _make_tcp()
        adapter._client = _make_client()
        adapter._client.read_holding_registers = AsyncMock(side_effect=OSError("read error"))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        assert any(e.quality == "bad" for e in events)

    async def test_poll_invalid_binding_config_returns_early(self):
        adapter, bus = _make_tcp()
        adapter._client = _make_client()
        # Pass a completely invalid config that will fail ModbusBindingConfig(**config)
        binding = make_binding({"register_type": "holding", "address": "not-an-int"}, direction="SOURCE")

        # Should return without raising
        await adapter._poll_loop(binding)
        bus.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# RTU — _read_register / _write_register / _poll_loop (mirror TCP tests)
# ---------------------------------------------------------------------------


class TestReadRegisterRtu:
    async def _read(self, adapter, bc_config, client):
        adapter._client = client
        bc = ModbusBindingConfig(**bc_config)
        return await adapter._read_register(bc)

    async def test_holding_register(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_ok_response([111]))
        assert await self._read(adapter, _HOLDING_CFG, client) == 111

    async def test_input_register(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_ok_response([222]))
        assert await self._read(adapter, _INPUT_CFG, client) == 222

    async def test_coil_returns_bool(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_ok_response(bits=[False]))
        assert await self._read(adapter, _COIL_CFG, client) is False

    async def test_discrete_input_returns_bool(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_ok_response(bits=[True]))
        assert await self._read(adapter, _DISCRETE_CFG, client) is True

    async def test_error_response_returns_none(self):
        adapter, _ = _make_rtu()
        client = _make_client(response=_error_response())
        assert await self._read(adapter, _HOLDING_CFG, client) is None

    async def test_not_connected_returns_none(self):
        adapter, _ = _make_rtu()
        client = _make_client(connected=False)
        assert await self._read(adapter, _HOLDING_CFG, client) is None

    async def test_unknown_register_type_returns_none(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        bc = MagicMock(spec=ModbusBindingConfig)
        bc.data_format = "uint16"
        bc.register_type = "unknown_type"
        bc.address = 0
        bc.unit_id = 1
        bc.byte_order = "big"
        bc.word_order = "big"
        bc.scale_factor = 1.0
        assert await adapter._read_register(bc) is None

    async def test_float32_decoded(self):
        import struct

        adapter, _ = _make_rtu()
        regs = list(struct.unpack(">HH", struct.pack(">f", 2.71)))
        client = _make_client(response=_ok_response(registers=regs))
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "float32"})
        adapter._client = client
        result = await adapter._read_register(bc)
        assert abs(result - 2.71) < 1e-5


class TestWriteRegisterRtu:
    async def test_write_coil(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_COIL_CFG)
        await adapter._write_register(bc, True)
        client.write_coil.assert_awaited_once()

    async def test_write_holding_single(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_HOLDING_CFG)
        await adapter._write_register(bc, 99)
        client.write_register.assert_awaited_once()

    async def test_write_holding_multi_float32(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "float32"})
        await adapter._write_register(bc, 1.5)
        client.write_registers.assert_awaited_once()

    async def test_write_non_coil_non_holding_noop(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        bc = ModbusBindingConfig(**_INPUT_CFG)
        await adapter._write_register(bc, 1)
        client.write_register.assert_not_awaited()


class TestPollLoopRtu:
    async def test_poll_publishes_good_value(self):
        adapter, bus = _make_rtu()
        adapter._client = _make_client(response=_ok_response([55]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        good = [e for e in events if e.quality == "good"]
        assert len(good) >= 1
        assert good[0].value == 55

    async def test_poll_publishes_bad_on_exception(self):
        adapter, bus = _make_rtu()
        adapter._client = _make_client()
        adapter._client.read_holding_registers = AsyncMock(side_effect=OSError("serial error"))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        assert any(e.quality == "bad" for e in events)

    async def test_poll_invalid_config_returns_early(self):
        adapter, bus = _make_rtu()
        adapter._client = _make_client()
        binding = make_binding({"register_type": "holding", "address": "bad"}, direction="SOURCE")
        await adapter._poll_loop(binding)
        bus.publish.assert_not_awaited()

    async def test_poll_applies_formula(self):
        adapter, bus = _make_rtu()
        adapter._client = _make_client(response=_ok_response([5]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE", value_formula="x * 3")

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        good = [e for e in events if e.quality == "good"]
        assert good[0].value == 15


class TestModbusCallRtu:
    async def test_first_variant_succeeds(self):
        adapter, _ = _make_rtu()
        fn = AsyncMock(return_value="rtu-ok")
        result = await adapter._modbus_call(fn, 0, 1, unit_id=1)
        assert result == "rtu-ok"

    async def test_raises_when_all_variants_fail(self):
        adapter, _ = _make_rtu()

        async def fn(**kwargs):
            raise TypeError("always fails")

        with pytest.raises(RuntimeError, match="pymodbus"):
            await adapter._modbus_call(fn, 0, 1, unit_id=1)


# ---------------------------------------------------------------------------
# RTU — additional branches not yet covered above
# ---------------------------------------------------------------------------


class TestModbusRtuAdditional:
    async def test_connect_exception_publishes_failure(self):
        adapter, bus = _make_rtu()
        client = MagicMock()
        client.connect = AsyncMock(side_effect=OSError("serial port busy"))
        fake_mod = MagicMock()
        fake_mod.AsyncModbusSerialClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        status_event = bus.publish.call_args_list[-1].args[0]
        assert status_event.connected is False

    async def test_disconnect_calls_close_on_client(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        adapter._client = client
        await adapter.disconnect()
        client.close.assert_called_once()

    async def test_on_bindings_reloaded_cancels_old_tasks(self):
        adapter, _ = _make_rtu()
        adapter._client = _make_client()
        old_task = asyncio.create_task(asyncio.sleep(100))
        adapter._poll_tasks.append(old_task)
        adapter._bindings = []
        await adapter._on_bindings_reloaded()
        await asyncio.sleep(0)
        assert old_task.cancelled()

    async def test_poll_applies_value_map(self):
        adapter, bus = _make_rtu()
        adapter._client = _make_client(response=_ok_response([1]))
        binding = make_binding(_HOLDING_CFG, direction="SOURCE", value_map={"1": "ON", "0": "OFF"})

        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        good = [e for e in events if e.quality == "good"]
        assert good[0].value == "ON"

    async def test_read_exception_returns_none(self):
        adapter, _ = _make_rtu()
        adapter._client = _make_client()
        adapter._client.read_holding_registers = AsyncMock(side_effect=Exception("boom"))
        binding = make_binding(_HOLDING_CFG)
        result = await adapter.read(binding)
        assert result is None

    async def test_write_skipped_when_not_connected(self):
        adapter, _ = _make_rtu()
        adapter._client = _make_client(connected=False)
        binding = make_binding(_HOLDING_CFG)
        await adapter.write(binding, 1)  # should not raise

    async def test_write_skipped_when_no_client(self):
        adapter, _ = _make_rtu()
        adapter._client = None
        binding = make_binding(_HOLDING_CFG)
        await adapter.write(binding, 1)  # should not raise

    async def test_write_exception_does_not_propagate(self):
        adapter, _ = _make_rtu()
        client = _make_client()
        client.write_register = AsyncMock(side_effect=Exception("write error"))
        adapter._client = client
        binding = make_binding(_HOLDING_CFG)
        await adapter.write(binding, 99)  # should not raise
