"""Unit tests for Modbus TCP and Modbus RTU adapters.

Both adapters share identical logic (_modbus_call, _read_register,
_write_register, _poll_loop) — they differ only in their config model
and the pymodbus client class they instantiate.

All pymodbus and network calls are mocked; no hardware required.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.adapters.modbus_base import ModbusBindingConfig, decode_registers, encode_value
from obs.adapters.modbus_rtu.adapter import ModbusRtuAdapter, ModbusRtuAdapterConfig
from obs.adapters.modbus_tcp.adapter import ModbusTcpAdapter, ModbusTcpAdapterConfig
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_TCP_CFG = {"host": "127.0.0.1", "port": 502, "timeout": 1.0}
_DEFAULT_RTU_CFG = {
    "port": "/dev/ttyUSB0",
    "baudrate": 9600,
    "parity": "N",
    "stopbits": 1,
    "bytesize": 8,
    "timeout": 1.0,
}

_HOLDING_CFG = {
    "unit_id": 1,
    "register_type": "holding",
    "address": 0,
    "data_format": "uint16",
    "poll_interval": 0.05,
}
_INPUT_CFG = {
    "unit_id": 1,
    "register_type": "input",
    "address": 0,
    "data_format": "uint16",
    "poll_interval": 0.05,
}
_COIL_CFG = {
    "unit_id": 1,
    "register_type": "coil",
    "address": 0,
    "data_format": "uint16",
    "poll_interval": 0.05,
}
_DISCRETE_CFG = {
    "unit_id": 1,
    "register_type": "discrete_input",
    "address": 0,
    "data_format": "uint16",
    "poll_interval": 0.05,
}


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


def _install_client_factory(adapter, *clients):
    adapter._client_factory = MagicMock(side_effect=list(clients))


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

    async def test_input_register_uint32_big_byte_little_word(self):
        adapter, _ = _make_tcp()
        client = _make_client(response=_ok_response([0x5678, 0x1234]))
        cfg = {
            **_INPUT_CFG,
            "data_format": "uint32",
            "count": 2,
            "byte_order": "big",
            "word_order": "little",
        }
        result = await self._read(adapter, cfg, client)

        assert result == 0x12345678
        client.read_input_registers.assert_awaited_once()
        args = client.read_input_registers.await_args.args
        assert args[:2] == (0, 2)
        assert client.read_input_registers.await_args.kwargs.get("device_id") == 1

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

    @pytest.mark.parametrize(
        ("byte_order", "word_order", "registers"),
        [
            ("big", "big", [0x1234, 0x5678]),
            ("big", "little", [0x5678, 0x1234]),
            ("little", "big", [0x3412, 0x7856]),
            ("little", "little", [0x7856, 0x3412]),
        ],
    )
    def test_uint32_endian_decode_and_encode_are_symmetric(self, byte_order, word_order, registers):
        assert decode_registers(registers, "uint32", byte_order, word_order) == 0x12345678
        assert encode_value(0x12345678, "uint32", byte_order, word_order) == registers


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
            task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
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

        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
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

        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
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

        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
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

        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
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


# ---------------------------------------------------------------------------
# Fix PR#714 — _on_bindings_reloaded: await gather + reconnect
# ---------------------------------------------------------------------------


class TestBindingsReloadedAwaitAndReconnect:
    """Tests for the fix in PR#714:
    - Old tasks must be fully awaited before new tasks start (no race condition)
    - Client is reconnected if it became disconnected during cancellation
    """

    async def test_old_tasks_are_fully_done_after_reload(self):
        """_on_bindings_reloaded must await old tasks, not just cancel them.

        Without asyncio.gather(), t.cancel() only *requests* cancellation.
        The old task keeps running until its next await point, which means it
        can still be executing a Modbus read concurrently with the new tasks.
        After the fix, every old task.done() must be True once reload returns.
        """
        adapter, _ = _make_tcp()
        adapter._client = _make_client()

        handled_cancel = asyncio.Event()

        async def slow_cancel():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # Set event synchronously — asyncio.sleep(0) can race with gather() in Py3.13
                handled_cancel.set()
                raise

        old_task = asyncio.create_task(slow_cancel())
        adapter._poll_tasks.append(old_task)
        adapter._bindings = []

        await asyncio.sleep(0)  # let old_task reach its first await before cancellation
        await adapter._on_bindings_reloaded()

        assert old_task.done(), (
            "Old task is not done after _on_bindings_reloaded — "
            "asyncio.gather() was not awaited, leaving a race condition "
            "where old and new tasks read the same TCP client concurrently."
        )
        assert handled_cancel.is_set(), "Old task never processed its CancelledError"

    async def test_new_tasks_start_only_after_old_tasks_finish(self):
        """New poll tasks must not start before old tasks have finished.

        This is the core race condition: if a new task starts while an old
        task is still mid-read, both call read_holding_registers() on the
        same AsyncModbusTcpClient, corrupting the TCP stream.
        """
        adapter, _ = _make_tcp()
        adapter._client = _make_client()
        old_task_done_when_new_started = []

        async def slow_cancel():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                raise

        old_task = asyncio.create_task(slow_cancel())
        adapter._poll_tasks.append(old_task)

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        adapter._bindings = [binding]

        original_create_task = asyncio.create_task

        def recording_create_task(coro, **kwargs):
            old_task_done_when_new_started.append(old_task.done())
            return original_create_task(coro, **kwargs)

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            with patch("asyncio.create_task", side_effect=recording_create_task):
                await adapter._on_bindings_reloaded()

        assert old_task_done_when_new_started, "No new task was created"
        assert all(old_task_done_when_new_started), "New task was created before old task finished — gather() was not awaited properly."

        for t in adapter._poll_tasks:
            t.cancel()

    async def test_reconnects_when_client_disconnected_after_cancel(self):
        """After cancelling tasks, if the TCP client is disconnected,
        _on_bindings_reloaded must reconnect before starting new pollers.

        Cancelled tasks may leave the AsyncModbusTcpClient in a bad state
        (a pending Modbus request was abandoned mid-flight). Reconnecting
        ensures a clean TCP session for the new poll tasks.
        """
        adapter, _ = _make_tcp()
        client = _make_client(connected=False)
        new_client = _make_client(connected=True)
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

        client.close.assert_called_once()
        new_client.connect.assert_awaited_once()
        assert adapter._client is new_client

    async def test_always_reconnects_after_reload_for_clean_tcp_state(self):
        """After reload the client is always closed and reconnected regardless of
        connected state — cancelled tasks may leave a pending in-flight request
        that corrupts the TCP stream even when connected=True.
        """
        adapter, _ = _make_tcp()
        client = _make_client(connected=True)
        new_client = _make_client(connected=True)
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

        client.close.assert_called_once()
        client.connect.assert_not_awaited()
        new_client.connect.assert_awaited_once()
        assert adapter._client is new_client

    async def test_reconnect_failure_is_swallowed_new_tasks_still_start(self):
        """A reconnect failure must not propagate — new tasks still start."""
        adapter, _ = _make_tcp()
        client = _make_client(connected=False)
        new_client = _make_client(connected=False)
        new_client.connect = AsyncMock(side_effect=OSError("connection refused"))
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        adapter._bindings = [binding]

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            await adapter._on_bindings_reloaded()

        assert len(adapter._poll_tasks) == 1

        for t in adapter._poll_tasks:
            t.cancel()

    async def test_no_reconnect_attempt_when_no_client(self):
        """If _client is None, no AttributeError must be raised."""
        adapter, _ = _make_tcp()
        adapter._client = None
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

    async def test_initial_binding_load_uses_existing_connection(self):
        """The first registry binding load follows connect() and must not reconnect.

        connect() already opened the TCP client before the registry calls
        reload_bindings(). Reconnecting here creates a second initial TCP session
        before any old poller or in-flight Modbus call exists.
        """
        adapter, _ = _make_tcp()
        client = _make_client(connected=True)
        new_client = _make_client(connected=True)
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._bindings = [make_binding(_HOLDING_CFG, direction="SOURCE")]

        with patch.object(adapter, "_poll_loop", new=AsyncMock()):
            await adapter._on_bindings_reloaded()

        client.close.assert_not_called()
        adapter._client_factory.assert_not_called()
        assert adapter._client is client
        assert adapter._initial_load_done
        assert len(adapter._poll_tasks) == 1

        for t in adapter._poll_tasks:
            t.cancel()


# ---------------------------------------------------------------------------
# Fix PR#714 — _poll_loop: auto-reconnect on disconnected client
# ---------------------------------------------------------------------------


class TestPollLoopAutoReconnect:
    """Tests for the auto-reconnect behaviour added to _poll_loop in PR#714."""

    async def test_reconnects_and_resumes_polling_when_disconnected(self):
        """When client is disconnected at the start of a poll iteration,
        _poll_loop must reconnect and then publish a good-quality value.
        """
        adapter, bus = _make_tcp()

        async def mock_connect():
            adapter._client.connected = True

        client = _make_client(connected=False, response=_ok_response([55]))
        client.connect = AsyncMock(side_effect=mock_connect)
        adapter._client = client

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        client.connect.assert_awaited()
        good_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality") and c.args[0].quality == "good"]
        assert len(good_events) >= 1, "No good-quality value published after reconnect — poll loop did not resume after re-establishing connection."
        assert good_events[0].value == 55

    async def test_publishes_bad_and_retries_when_reconnect_fails(self):
        """When reconnect fails, publish quality=bad and retry next cycle."""
        adapter, bus = _make_tcp()
        client = _make_client(connected=False)
        client.connect = AsyncMock(side_effect=OSError("refused"))
        adapter._client = client

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        assert len(events) >= 1, "No events published when reconnect failed"
        assert all(e.quality == "bad" for e in events)

    async def test_recovers_after_transient_disconnect(self):
        """After a transient disconnect mid-loop, the poll loop reconnects
        and resumes publishing good values.
        """
        adapter, bus = _make_tcp()
        call_count = 0

        async def flaky_read(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                adapter._client.connected = False
                raise OSError("connection lost")
            return _ok_response([77])

        async def mock_connect():
            adapter._client.connected = True

        client = _make_client(connected=True)
        client.read_holding_registers = AsyncMock(side_effect=flaky_read)
        client.connect = AsyncMock(side_effect=mock_connect)
        adapter._client = client

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        client.connect.assert_awaited()
        good_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality") and c.args[0].quality == "good"]
        assert len(good_events) >= 1, "No good values published after recovery"


# ---------------------------------------------------------------------------
# Regression — delete+recreate cycle (the original production bug)
# ---------------------------------------------------------------------------


class TestBindingDeleteRecreateCycleRegression:
    """Regression tests for the full delete+recreate bug reported in PR#714.

    Before the fix, deleting a binding and creating a new one (two sequential
    _on_bindings_reloaded calls) caused the new binding to poll exactly once
    then stop permanently. Values appeared in the visu briefly then vanished.
    """

    async def test_new_binding_continues_polling_after_delete_recreate(self):
        """After a delete+recreate cycle the new binding must keep polling,
        not just fire once and stop.
        """
        adapter, bus = _make_tcp()
        client = _make_client(response=_ok_response([42]))
        reload_clients = [_make_client(response=_ok_response([42])) for _ in range(3)]
        adapter._client = client
        _install_client_factory(adapter, *reload_clients)

        b_original = make_binding(_HOLDING_CFG, direction="SOURCE")
        b_new = make_binding(_HOLDING_CFG, direction="SOURCE")

        # Step 1: original binding active
        adapter._bindings = [b_original]
        await adapter._on_bindings_reloaded()
        original_tasks = list(adapter._poll_tasks)

        # Step 2: DELETE — reload without original
        adapter._bindings = []
        await adapter._on_bindings_reloaded()
        for t in original_tasks:
            assert t.done(), "Old task not awaited — race condition risk"

        # Step 3: POST (recreate) — reload with new binding
        adapter._bindings = [b_new]
        await adapter._on_bindings_reloaded()
        assert len(adapter._poll_tasks) == 1

        # Step 4: new binding must poll multiple times, not just once
        bus.publish.reset_mock()
        await asyncio.sleep(0.2)  # ~4 cycles at poll_interval=0.05

        good_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality") and c.args[0].quality == "good"]
        assert len(good_events) >= 2, (
            f"Expected >=2 good polls after delete+recreate, got {len(good_events)}. "
            "New binding only polled once then stopped — original bug not fixed."
        )

        for t in adapter._poll_tasks:
            t.cancel()
        await asyncio.gather(*adapter._poll_tasks, return_exceptions=True)

    async def test_rapid_reload_does_not_corrupt_tcp_connection(self):
        """Rapid _on_bindings_reloaded calls (bulk binding creation) must not
        corrupt the TCP client via concurrent reads.
        """
        adapter, bus = _make_tcp()
        client = _make_client(response=_ok_response([99]))
        reload_clients = [_make_client(response=_ok_response([99])) for _ in range(5)]
        adapter._client = client
        _install_client_factory(adapter, *reload_clients)

        bindings = [make_binding(_HOLDING_CFG, direction="SOURCE") for _ in range(5)]

        for i in range(len(bindings)):
            adapter._bindings = bindings[: i + 1]
            await adapter._on_bindings_reloaded()

        assert len(adapter._poll_tasks) == 5

        bus.publish.reset_mock()
        await asyncio.sleep(0.15)

        good_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality") and c.args[0].quality == "good"]
        assert len(good_events) >= 5, (
            f"Expected >=5 good events after rapid reloads, got {len(good_events)}. TCP connection may be corrupted by concurrent reads."
        )

        for t in adapter._poll_tasks:
            t.cancel()
        await asyncio.gather(*adapter._poll_tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Config options: serialize_reads + startup_jitter_s
# ---------------------------------------------------------------------------


class TestModbusTcpConfigOptions:
    """Tests for the two new adapter config options introduced in PR#714."""

    async def test_serialize_reads_true_creates_semaphore_1(self):
        """serialize_reads=True (default) must create a Semaphore(1)."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": True})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        # Semaphore(1): first acquire succeeds, second blocks
        await adapter._io_sem.acquire()
        assert adapter._io_sem.locked()
        adapter._io_sem.release()

    async def test_serialize_reads_false_creates_unlimited_semaphore(self):
        """serialize_reads=False: _io_sem is None (no locking — unlimited concurrency)."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": False})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        # With serialize_reads=False, _io_sem is None — no lock, unlimited concurrency
        assert adapter._io_sem is None

    async def test_default_config_has_serialize_reads_true(self):
        """Default adapter config must have serialize_reads=True (safe default)."""
        from obs.adapters.modbus_tcp.adapter import ModbusTcpAdapterConfig

        cfg = ModbusTcpAdapterConfig()
        assert cfg.serialize_reads is True

    async def test_default_config_has_startup_jitter_30s(self):
        """Default startup_jitter_s must be 30s."""
        from obs.adapters.modbus_tcp.adapter import ModbusTcpAdapterConfig

        cfg = ModbusTcpAdapterConfig()
        assert cfg.startup_jitter_s == 30.0

    async def test_startup_jitter_zero_skips_sleep(self):
        """startup_jitter_s=0 must skip the initial sleep entirely."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "startup_jitter_s": 0.0})
        client = _make_client(connected=True, response=_ok_response([1]))
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._client = client

        sleep_calls = []

        async def recording_sleep(delay, *args, **kwargs):
            sleep_calls.append(delay)
            raise asyncio.CancelledError  # exit immediately after capturing the delay

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")

        with patch("asyncio.sleep", side_effect=recording_sleep):
            task = asyncio.create_task(adapter._poll_loop(binding))
            try:
                await task
            except asyncio.CancelledError:
                pass

        # With jitter=0, no initial sleep (only poll_interval sleeps allowed)
        jitter_sleeps = [d for d in sleep_calls if d > 0]
        assert all(d >= _HOLDING_CFG["poll_interval"] for d in jitter_sleeps), "startup_jitter_s=0 produced an unexpected initial sleep"

    async def test_startup_jitter_nonzero_produces_initial_sleep(self):
        """startup_jitter_s > 0 must produce an initial sleep <= jitter_max."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "startup_jitter_s": 5.0})
        client = _make_client(connected=True, response=_ok_response([1]))
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._client = client

        sleep_calls = []

        async def _sleep(delay, *args, **kwargs):
            sleep_calls.append(delay)
            raise asyncio.CancelledError

        binding = make_binding({**_HOLDING_CFG, "poll_interval": 60.0}, direction="SOURCE")

        with patch("asyncio.sleep", side_effect=_sleep):
            task = asyncio.create_task(adapter._poll_loop(binding))
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(sleep_calls) >= 1, "No initial jitter sleep produced"
        assert sleep_calls[0] <= 5.0, f"Jitter sleep {sleep_calls[0]:.2f}s exceeds startup_jitter_s=5.0"

    async def test_startup_jitter_uses_configured_window_for_fast_polling(self):
        """startup_jitter_s is not capped by the binding poll interval."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "startup_jitter_s": 30.0})
        adapter._client = _make_client(connected=True)

        sleep_calls = []

        async def _sleep(delay, *args, **kwargs):
            sleep_calls.append(delay)
            raise asyncio.CancelledError

        binding = make_binding({**_HOLDING_CFG, "poll_interval": 1.0}, direction="SOURCE")

        with (
            patch("random.uniform", return_value=12.0) as uniform_mock,
            patch("asyncio.sleep", side_effect=_sleep),
        ):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.gather(task, return_exceptions=True)

        uniform_mock.assert_called_once_with(0, 30.0)
        assert sleep_calls[0] == 12.0


# ---------------------------------------------------------------------------
# Maintainer review fixes — write path, disconnect, jitter, None semaphore
# ---------------------------------------------------------------------------


class TestWritePathUsesIoSemaphore:
    """Fix 1: _write_register must hold _io_sem so reads and writes are mutually
    exclusive on the shared TCP socket.
    """

    async def test_write_acquires_io_sem_when_serialize_reads_true(self):
        """With serialize_reads=True, _write_register must acquire _io_sem."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": True})
        client = _make_client()
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._client = client

        # Acquire the semaphore manually — write must then block until released
        await adapter._io_sem.acquire()
        write_completed = False

        async def do_write():
            nonlocal write_completed
            bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "uint16"})
            await adapter._write_register(bc, 42)
            write_completed = True

        write_task = asyncio.create_task(do_write())
        await asyncio.sleep(0.05)
        assert not write_completed, "_write_register completed despite locked semaphore"
        adapter._io_sem.release()
        await write_task
        assert write_completed

    async def test_write_skips_sem_when_serialize_reads_false(self):
        """With serialize_reads=False, _io_sem is None — write proceeds without locking."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": False})
        client = _make_client()
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._client = client

        assert adapter._io_sem is None
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "uint16"})
        # Must not raise even without semaphore
        await adapter._write_register(bc, 42)
        client.write_register.assert_awaited_once()


class TestDisconnectAwaitsGather:
    """Fix 2: disconnect() must await gather() so cancelled tasks finish
    before the TCP client is closed.
    """

    async def test_disconnect_waits_for_tasks_before_closing(self):
        """All poll tasks must be done before self._client.close() is called."""
        adapter, _ = _make_tcp()
        client = _make_client()
        adapter._client = client

        task_done_at_close = []

        async def slow_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(slow_task())
        adapter._poll_tasks.append(task)
        await asyncio.sleep(0)  # let task start

        original_close = client.close

        def recording_close():
            task_done_at_close.append(task.done())
            original_close()

        client.close = recording_close
        await adapter.disconnect()

        assert task_done_at_close, "close() was never called"
        assert task_done_at_close[0], "close() called before task finished — gather() not awaited"

    async def test_disconnect_resets_initial_load_flag(self):
        """After disconnect, jitter applies again on the next connect/reload cycle."""
        adapter, _ = _make_tcp()
        adapter._client = _make_client()
        adapter._initial_load_done = True

        await adapter.disconnect()

        assert not adapter._initial_load_done


class TestJitterOnlyOnInitialLoad:
    """Fix 3: Startup jitter is applied only on the first _on_bindings_reloaded()
    call after connect, not on subsequent binding changes.
    """

    async def test_jitter_applied_on_first_reload(self):
        """First _on_bindings_reloaded: apply_jitter=True passed to _poll_loop."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "startup_jitter_s": 5.0})
        adapter._client = _make_client()
        assert not adapter._initial_load_done

        jitter_flags = []

        async def recording_poll_loop(binding, *, apply_jitter=True):
            jitter_flags.append(apply_jitter)

        with patch.object(adapter, "_poll_loop", side_effect=recording_poll_loop):
            adapter._bindings = [make_binding(_HOLDING_CFG, direction="SOURCE")]
            await adapter._on_bindings_reloaded()
            await asyncio.sleep(0)  # let the created task(s) start and run recording_poll_loop

        assert jitter_flags == [True], "Jitter must be applied on first reload"
        assert adapter._initial_load_done

        for t in adapter._poll_tasks:
            t.cancel()

    async def test_jitter_skipped_on_subsequent_reloads(self):
        """After the first reload, apply_jitter=False for all new tasks."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "startup_jitter_s": 5.0})
        adapter._client = _make_client()
        adapter._initial_load_done = True  # simulate already-started adapter

        jitter_flags = []

        async def recording_poll_loop(binding, *, apply_jitter=True):
            jitter_flags.append(apply_jitter)

        with patch.object(adapter, "_poll_loop", side_effect=recording_poll_loop):
            adapter._bindings = [make_binding(_HOLDING_CFG, direction="SOURCE")]
            await adapter._on_bindings_reloaded()
            await asyncio.sleep(0)  # let tasks start

        assert jitter_flags == [False], "Jitter must NOT be applied on subsequent reloads"

        for t in adapter._poll_tasks:
            t.cancel()


class TestNoneIoSemaphore:
    """Fix 4: _io_sem is None when serialize_reads=False (no sys.maxsize hack).
    The None sentinel is used with `async with (sem or nullcontext())`.
    """

    async def test_io_sem_is_semaphore_when_serialize_reads_true(self):
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": True})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        assert isinstance(adapter._io_sem, asyncio.Semaphore)

    async def test_io_sem_is_none_when_serialize_reads_false(self):
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": False})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        assert adapter._io_sem is None


class TestSilentReconnectFailure:
    """Fix 5: When connect() returns without error but client.connected stays False,
    the poll loop publishes quality=bad and retries after poll_interval.
    """

    async def test_silent_reconnect_fail_publishes_bad_quality(self):
        """connect() no exception but still disconnected → bad quality + retry."""
        adapter, bus = _make_tcp()
        # Client stays disconnected even after connect()
        client = _make_client(connected=False)
        client.connect = AsyncMock()  # no exception, but connected stays False
        adapter._client = client

        binding = make_binding(_HOLDING_CFG, direction="SOURCE")
        task = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality")]
        assert any(e.quality == "bad" for e in events), "Expected bad-quality event when connect() succeeds but client stays disconnected"


# ---------------------------------------------------------------------------
# P2 review fixes — reload lock, backoff, io_sem around close/connect
# ---------------------------------------------------------------------------


class TestReloadLock:
    """_reload_lock prevents concurrent _on_bindings_reloaded() calls from
    interleaving their cancel/create sequences and producing orphan tasks.
    """

    async def test_concurrent_reloads_are_serialized(self):
        """Two concurrent reload calls must not interleave."""
        adapter, _ = _make_tcp()
        adapter._client = _make_client()
        _install_client_factory(adapter, _make_client(), _make_client())
        adapter._initial_load_done = True
        adapter._bindings = []

        reload_order = []
        active_lifecycle_sections = 0

        @contextlib.asynccontextmanager
        async def recording_lifecycle():
            nonlocal active_lifecycle_sections
            reload_order.append("enter")
            assert active_lifecycle_sections == 0
            active_lifecycle_sections += 1
            await asyncio.sleep(0.05)
            try:
                yield
            finally:
                active_lifecycle_sections -= 1
                reload_order.append("exit")

        adapter._client_lifecycle = recording_lifecycle

        t1 = asyncio.create_task(adapter._on_bindings_reloaded())
        t2 = asyncio.create_task(adapter._on_bindings_reloaded())
        await asyncio.gather(t1, t2)

        assert reload_order == ["enter", "exit", "enter", "exit"]


class TestReloadIoSemaphoreForCloseConnect:
    """_on_bindings_reloaded waits for in-flight I/O before close()+connect(),
    even when request serialization is disabled.
    """

    async def test_reload_waits_for_held_io_sem_before_closing(self):
        """A write in progress must finish before reload closes the socket."""
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._bindings = []

        write_started = asyncio.Event()
        release_write = asyncio.Event()

        async def slow_write_register(*args, **kwargs):
            write_started.set()
            await release_write.wait()
            return _ok_response()

        client.write_register = AsyncMock(side_effect=slow_write_register)
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "uint16"})
        write_task = asyncio.create_task(adapter._write_register(bc, 42))
        await write_started.wait()

        reload_task = asyncio.create_task(adapter._on_bindings_reloaded())
        await asyncio.sleep(0.05)
        assert client.close.call_count == 0, "close() called while write was in flight"

        release_write.set()
        await write_task
        await reload_task
        client.close.assert_called_once()

    async def test_reload_waits_for_inflight_io_when_serialize_reads_false(self):
        adapter, _ = _make_tcp(
            {"host": "127.0.0.1", "port": 502, "timeout": 1.0, "serialize_reads": False},
        )
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()
        adapter._bindings = []
        assert adapter._io_sem is None

        read_started = asyncio.Event()
        release_read = asyncio.Event()

        async def slow_read_registers(*args, **kwargs):
            read_started.set()
            await release_read.wait()
            return _ok_response([42])

        client.read_holding_registers = AsyncMock(side_effect=slow_read_registers)
        bc = ModbusBindingConfig(**_HOLDING_CFG)
        read_task = asyncio.create_task(adapter._read_register(bc))
        await read_started.wait()

        reload_task = asyncio.create_task(adapter._on_bindings_reloaded())
        await asyncio.sleep(0.05)
        assert client.close.call_count == 0, "close() called while unserialized read was in flight"

        release_read.set()
        await read_task
        await reload_task
        client.close.assert_called_once()

    async def test_cancelled_lifecycle_wait_clears_busy_flag(self):
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()

        write_started = asyncio.Event()
        release_write = asyncio.Event()

        async def slow_write_register(*args, **kwargs):
            write_started.set()
            await release_write.wait()
            return _ok_response()

        client.write_register = AsyncMock(side_effect=slow_write_register)
        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "uint16"})
        write_task = asyncio.create_task(adapter._write_register(bc, 42))
        await write_started.wait()

        lifecycle_entered = asyncio.Event()

        async def wait_for_lifecycle():
            async with adapter._client_lifecycle():
                lifecycle_entered.set()

        lifecycle_task = asyncio.create_task(wait_for_lifecycle())
        await asyncio.sleep(0.05)
        assert adapter._lifecycle_busy is True
        assert not lifecycle_entered.is_set()

        lifecycle_task.cancel()
        await asyncio.gather(lifecycle_task, return_exceptions=True)
        assert adapter._lifecycle_busy is False

        release_write.set()
        await write_task

        await asyncio.wait_for(adapter._read_register(bc), timeout=0.5)

    async def test_queued_io_rechecks_client_after_lifecycle_wait(self):
        adapter, _ = _make_tcp({"host": "127.0.0.1", "port": 502, "timeout": 1.0})
        client = _make_client(connected=True)
        fake_mod = MagicMock()
        fake_mod.AsyncModbusTcpClient = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"pymodbus.client": fake_mod}):
            await adapter.connect()

        lifecycle_entered = asyncio.Event()
        release_lifecycle = asyncio.Event()

        async def close_client_during_lifecycle():
            async with adapter._client_lifecycle():
                lifecycle_entered.set()
                await release_lifecycle.wait()

        lifecycle_task = asyncio.create_task(close_client_during_lifecycle())
        await lifecycle_entered.wait()

        bc = ModbusBindingConfig(**{**_HOLDING_CFG, "data_format": "uint16"})
        write_task = asyncio.create_task(adapter._write_register(bc, 42))
        read_task = asyncio.create_task(adapter._read_register(bc))
        await asyncio.sleep(0.05)
        assert not write_task.done()
        assert not read_task.done()

        client.connected = False
        release_lifecycle.set()
        read_result = await asyncio.wait_for(read_task, timeout=0.5)
        await asyncio.wait_for(write_task, timeout=0.5)
        await lifecycle_task

        assert read_result is None
        client.write_register.assert_not_awaited()
        client.read_holding_registers.assert_not_awaited()

        client.connected = True
        adapter._stopping = True
        assert await adapter._read_register(bc) is None
        await adapter._write_register(bc, 42)
        client.write_register.assert_not_awaited()
        client.read_holding_registers.assert_not_awaited()


class TestReloadPublishesStatus:
    """_on_bindings_reloaded publishes adapter status after reconnect."""

    async def test_reload_publishes_connected_on_success(self):
        adapter, bus = _make_tcp()
        client = _make_client(connected=True)
        new_client = _make_client(connected=True)
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

        status_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "connected")]
        connected_events = [e for e in status_events if e.connected]
        assert connected_events, "No connected=True status published after reload reconnect"

    async def test_reload_success_clears_stale_reconnect_backoff(self):
        adapter, _ = _make_tcp()
        client = _make_client(connected=True)
        new_client = _make_client(connected=True)
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._bindings = []
        adapter._reconnect_ok_after = 999999.0
        adapter._initial_load_done = True

        await adapter._on_bindings_reloaded()

        assert adapter._reconnect_ok_after == 0.0

    async def test_reload_publishes_disconnected_on_failure(self):
        adapter, bus = _make_tcp()
        client = _make_client(connected=False)
        new_client = _make_client(connected=False)
        new_client.connect = AsyncMock(side_effect=OSError("refused"))
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

        status_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "connected")]
        disconnected_events = [e for e in status_events if not e.connected]
        assert disconnected_events, "No connected=False status published after reload reconnect failure"

    async def test_reload_publishes_disconnected_when_connect_returns_but_stays_false(self):
        """connect() returns without raising but client.connected stays False (silent fail).

        P2 review: the else-branch must publish disconnected so a DEST-only instance
        does not continue to report connected while writes are silently skipped.
        """
        adapter, bus = _make_tcp()
        client = _make_client(connected=False)
        new_client = _make_client(connected=False)
        new_client.connect = AsyncMock()  # no exception — silent failure
        adapter._client = client
        _install_client_factory(adapter, new_client)
        adapter._initial_load_done = True
        adapter._bindings = []

        await adapter._on_bindings_reloaded()

        status_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "connected")]
        disconnected_events = [e for e in status_events if not e.connected]
        assert disconnected_events, "No connected=False status published when connect() returned but client stayed disconnected"


class TestReconnectBackoff:
    """_reconnect_ok_after prevents N bindings from each firing a separate
    connect() timeout when the device is offline.
    """

    async def test_only_one_connect_attempt_per_backoff_window(self):
        """After a failed reconnect, subsequent tasks skip connect() until backoff clears.

        Uses poll_interval=10s so the backoff window (now+10s) outlasts the 0.15s test
        window, and apply_jitter=False so tasks reach the reconnect path immediately.
        With poll_interval=0.05s the backoff expires every cycle, producing one connect()
        call per cycle rather than one per backoff window.
        """
        adapter, bus = _make_tcp()
        connect_calls = 0

        async def failing_connect():
            nonlocal connect_calls
            connect_calls += 1
            # Never sets connected=True

        client = _make_client(connected=False)
        client.connect = AsyncMock(side_effect=failing_connect)
        adapter._client = client

        long_interval_cfg = {**_HOLDING_CFG, "poll_interval": 10.0}
        binding1 = make_binding(long_interval_cfg, direction="SOURCE")
        binding2 = make_binding(long_interval_cfg, direction="SOURCE")

        t1 = asyncio.create_task(adapter._poll_loop(binding1, apply_jitter=False))
        t2 = asyncio.create_task(adapter._poll_loop(binding2, apply_jitter=False))
        await asyncio.sleep(0.15)
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)

        assert connect_calls == 1, (
            f"Expected 1 connect() call due to backoff, got {connect_calls}. Without backoff, every binding fires its own timeout."
        )

    async def test_failed_reconnect_releases_lock_before_sleeping(self):
        """All bindings should observe backoff immediately, not wait behind sleep()."""
        adapter, bus = _make_tcp()
        connect_calls = 0

        async def failing_connect():
            nonlocal connect_calls
            connect_calls += 1

        client = _make_client(connected=False)
        client.connect = AsyncMock(side_effect=failing_connect)
        adapter._client = client

        long_interval_cfg = {**_HOLDING_CFG, "poll_interval": 10.0}
        binding1 = make_binding(long_interval_cfg, direction="SOURCE")
        binding2 = make_binding(long_interval_cfg, direction="SOURCE")

        t1 = asyncio.create_task(adapter._poll_loop(binding1, apply_jitter=False))
        t2 = asyncio.create_task(adapter._poll_loop(binding2, apply_jitter=False))
        await asyncio.sleep(0.15)
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)

        bad_events = [c.args[0] for c in bus.publish.call_args_list if hasattr(c.args[0], "quality") and c.args[0].quality == "bad"]
        assert connect_calls == 1
        assert len(bad_events) >= 2, (
            "Reconnect lock was held during poll_interval sleep; the second binding "
            "could not publish bad quality while the first binding was backing off."
        )

    async def test_backoff_uses_shortest_active_poll_interval(self):
        adapter, _ = _make_tcp()
        client = _make_client(connected=False)
        client.connect = AsyncMock()
        adapter._client = client

        slow_binding = make_binding({**_HOLDING_CFG, "poll_interval": 300.0}, direction="SOURCE")
        fast_binding = make_binding({**_HOLDING_CFG, "poll_interval": 1.0}, direction="SOURCE")
        adapter._bindings = [slow_binding, fast_binding]

        assert adapter._reconnect_backoff_delay(slow_binding.config["poll_interval"]) == 1.0
