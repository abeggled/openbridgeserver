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


class TestTlsClientInterface:
    """Modbus TCP TLS mode (issue #1448) — obs/adapters/modbus_tcp/adapter.py."""

    def test_async_modbus_tls_client_importable(self):
        from pymodbus.client import AsyncModbusTlsClient

        assert AsyncModbusTlsClient is not None

    def test_constructor_accepts_sslctx_host_port_timeout(self):
        from pymodbus.client import AsyncModbusTlsClient

        params = inspect.signature(AsyncModbusTlsClient.__init__).parameters
        for name in ("host", "port", "timeout", "sslctx"):
            assert name in params, f"AsyncModbusTlsClient.__init__ no longer accepts '{name}'"

    def test_default_framer_is_tls(self):
        from pymodbus.client import AsyncModbusTlsClient
        from pymodbus.framer import FramerType

        assert inspect.signature(AsyncModbusTlsClient.__init__).parameters["framer"].default == FramerType.TLS

    def test_tls_framing_is_mbap_on_the_wire(self):
        """The docs promise MBAP framing inside TLS (Modbus/TCP Security): the TLS
        framer must emit byte-identical frames to the plain Modbus TCP socket framer."""
        from pymodbus.framer import FramerSocket, FramerTLS
        from pymodbus.pdu import DecodePDU

        payload = bytes.fromhex("0300000002")  # read holding registers, address 0, count 2
        decoder = DecodePDU(False)
        assert FramerTLS(decoder).encode(payload, 7, 42) == FramerSocket(decoder).encode(payload, 7, 42)

    async def test_transport_exposes_call_create_and_sslctx(self):
        """adapter._record_tls_errors wraps ctx.call_create to see handshake errors."""
        import ssl

        from pymodbus.client import AsyncModbusTlsClient

        sslctx = ssl.create_default_context()
        client = AsyncModbusTlsClient("127.0.0.1", port=802, sslctx=sslctx)
        assert callable(client.ctx.call_create)
        assert client.ctx.comm_params.sslctx is sslctx
