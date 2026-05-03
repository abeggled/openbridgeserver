"""Contract tests for pymodbus — verifies the import paths and API surface used by
obs.adapters.modbus_tcp and obs.adapters.modbus_rtu.

Notes:
- obs/adapters/modbus_tcp/adapter.py already has a version-safe _modbus_call() shim
  that tries multiple calling conventions. These tests guard the import paths and the
  existence of methods that _modbus_call() dispatches to.
- No broker/device connection is made; tests are purely structural.
"""

from __future__ import annotations

import inspect


class TestImportPaths:
    def test_async_modbus_tcp_client_importable(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert AsyncModbusTcpClient is not None

    def test_async_modbus_serial_client_importable(self):
        from pymodbus.client import AsyncModbusSerialClient

        assert AsyncModbusSerialClient is not None


class TestTcpClientInterface:
    def test_constructor_accepts_host(self):
        from pymodbus.client import AsyncModbusTcpClient

        sig = inspect.signature(AsyncModbusTcpClient.__init__)
        assert "host" in sig.parameters, (
            "AsyncModbusTcpClient.__init__ no longer accepts 'host'. obs/adapters/modbus_tcp/adapter.py passes host=cfg.host."
        )

    def test_constructor_accepts_port(self):
        from pymodbus.client import AsyncModbusTcpClient

        sig = inspect.signature(AsyncModbusTcpClient.__init__)
        assert "port" in sig.parameters

    def test_has_connect_method(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "connect")

    def test_has_close_method(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "close")

    def test_has_connected_attribute_or_property(self):
        from pymodbus.client import AsyncModbusTcpClient

        # adapter.py checks: if self._client.connected
        assert hasattr(AsyncModbusTcpClient, "connected"), (
            "AsyncModbusTcpClient no longer has 'connected'. "
            "obs/adapters/modbus_tcp/adapter.py uses self._client.connected to check connection state."
        )

    def test_has_read_holding_registers(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "read_holding_registers")

    def test_has_read_input_registers(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "read_input_registers")

    def test_has_read_coils(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "read_coils")

    def test_has_read_discrete_inputs(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "read_discrete_inputs")

    def test_has_write_register(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "write_register")

    def test_has_write_registers(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "write_registers")

    def test_has_write_coil(self):
        from pymodbus.client import AsyncModbusTcpClient

        assert hasattr(AsyncModbusTcpClient, "write_coil")
