"""Contract tests for python-socketio — verifies the API surface used by obs.adapters.iobroker.

Production usage (obs/adapters/iobroker/adapter.py):
  import socketio
  sio = socketio.AsyncClient(reconnection=True, logger=False, engineio_logger=False)
  @sio.event
  async def connect(): ...
  await sio.connect(url, ...)
  await sio.emit(event, data)
  await sio.disconnect()
"""

from __future__ import annotations

import inspect

import pytest

socketio = pytest.importorskip("socketio", reason="python-socketio not installed")


class TestAsyncClient:
    def test_async_client_importable(self):
        assert hasattr(socketio, "AsyncClient"), (
            "socketio.AsyncClient no longer exists. "
            "obs/adapters/iobroker/adapter.py instantiates socketio.AsyncClient(...)."
        )

    def test_async_client_constructor_accepts_reconnection(self):
        sig = inspect.signature(socketio.AsyncClient.__init__)
        assert "reconnection" in sig.parameters, (
            "socketio.AsyncClient.__init__ no longer accepts 'reconnection'. "
            "Adapter uses: socketio.AsyncClient(reconnection=True, ...)"
        )

    def test_async_client_constructor_accepts_logger(self):
        sig = inspect.signature(socketio.AsyncClient.__init__)
        assert "logger" in sig.parameters

    def test_async_client_constructor_accepts_extra_kwargs(self):
        # engineio_logger is forwarded via **kwargs to the engine.io layer.
        # The important thing is that the constructor accepts **kwargs so
        # passing engineio_logger=False doesn't raise TypeError.
        sig = inspect.signature(socketio.AsyncClient.__init__)
        params = sig.parameters
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        assert has_var_keyword, (
            "socketio.AsyncClient.__init__ no longer accepts **kwargs. "
            "The adapter passes engineio_logger=False which must not raise TypeError."
        )

    def test_engineio_logger_kwarg_does_not_raise(self):
        # Verify the actual instantiation with engineio_logger=False doesn't raise
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert sio is not None

    def test_async_client_instantiation(self):
        sio = socketio.AsyncClient(reconnection=True, logger=False, engineio_logger=False)
        assert sio is not None

    def test_has_event_decorator(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert hasattr(sio, "event"), (
            "socketio.AsyncClient no longer has an 'event' decorator. "
            "Adapter registers handlers with @sio.event."
        )

    def test_event_is_callable(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert callable(sio.event)

    def test_has_connect_method(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert hasattr(sio, "connect"), "socketio.AsyncClient missing 'connect' method"

    def test_has_disconnect_method(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert hasattr(sio, "disconnect")

    def test_has_emit_method(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert hasattr(sio, "emit"), (
            "socketio.AsyncClient missing 'emit' method. "
            "Adapter uses sio.emit(event, data) to send state updates."
        )

    def test_connect_is_coroutine(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert inspect.iscoroutinefunction(sio.connect), (
            "socketio.AsyncClient.connect must be a coroutine. "
            "Adapter awaits sio.connect(url, ...)."
        )

    def test_emit_is_coroutine(self):
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        assert inspect.iscoroutinefunction(sio.emit)
