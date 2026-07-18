"""Contract tests for pyownet — verifies the API surface used by obs.adapters.onewire.

These tests do NOT connect to a real owserver. They verify that the library's public
interface (factory function, proxy methods, exception classes) matches what OBS expects
so that a version upgrade that renames parameters or restructures classes is caught
immediately.
"""

from __future__ import annotations

import inspect

import pytest

pyownet_protocol = pytest.importorskip("pyownet.protocol", reason="pyownet not installed")


class TestProxyFactory:
    def test_proxy_function_exists(self):
        assert hasattr(pyownet_protocol, "proxy"), "pyownet.protocol.proxy() factory function not found"

    def test_proxy_accepts_host_port_persistent(self):
        sig = inspect.signature(pyownet_protocol.proxy)
        params = sig.parameters
        assert "host" in params, "pyownet.protocol.proxy() no longer accepts 'host'"
        assert "port" in params, "pyownet.protocol.proxy() no longer accepts 'port'"
        assert "persistent" in params, (
            "pyownet.protocol.proxy() no longer accepts 'persistent'. obs/adapters/onewire/adapter.py relies on persistent=True for connection reuse."
        )


class TestProxyMethods:
    def _proxy_class(self):
        return pyownet_protocol._Proxy

    def test_proxy_has_dir(self):
        assert hasattr(self._proxy_class(), "dir"), "pyownet proxy no longer has 'dir()' — used for browse_sensors()"

    def test_dir_accepts_path(self):
        sig = inspect.signature(self._proxy_class().dir)
        assert "path" in sig.parameters

    def test_proxy_has_read(self):
        assert hasattr(self._proxy_class(), "read"), "pyownet proxy no longer has 'read()'"

    def test_read_accepts_path(self):
        sig = inspect.signature(self._proxy_class().read)
        assert "path" in sig.parameters

    def test_proxy_has_write(self):
        assert hasattr(self._proxy_class(), "write"), "pyownet proxy no longer has 'write()'"

    def test_write_accepts_path_and_data(self):
        sig = inspect.signature(self._proxy_class().write)
        params = sig.parameters
        assert "path" in params
        assert "data" in params, "pyownet proxy.write() no longer accepts 'data'. obs/adapters/onewire/adapter.py calls write(path, data)."


class TestPersistentProxyClose:
    def test_persistent_proxy_has_close_connection(self):
        # obs/adapters/onewire/adapter.py calls close_connection() during disconnect()
        # to release the persistent socket instead of relying solely on GC.
        assert hasattr(pyownet_protocol._PersistentProxy, "close_connection"), (
            "pyownet._PersistentProxy no longer has 'close_connection()' — adapter disconnect() relies on it."
        )


class TestExceptions:
    def test_ownet_error_exists(self):
        assert hasattr(pyownet_protocol, "OwnetError")

    def test_conn_error_exists(self):
        assert hasattr(pyownet_protocol, "ConnError")

    def test_ownet_timeout_exists(self):
        assert hasattr(pyownet_protocol, "OwnetTimeout")

    def test_conn_error_is_ioerror(self):
        # obs/adapters/onewire/adapter.py catches ConnError specifically on connect()
        assert issubclass(pyownet_protocol.ConnError, OSError)

    def test_ownet_error_is_oserror(self):
        assert issubclass(pyownet_protocol.OwnetError, OSError)
