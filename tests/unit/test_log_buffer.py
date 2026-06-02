"""Unit tests for obs.log_buffer.

Covers:
  - LogBufferHandler captures log records into the buffer
  - Buffer entries contain expected fields (ts, level, logger, message)
  - deque maxlen evicts oldest entries when full
  - Level filter in get_log_buffer (via API helper)
  - Handler install() attaches to root logger
  - Early _broadcast_nowait with no WS manager does not raise
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def clear_buffer():
    """Reset the module-level deque and remove stray root-logger handlers before each test.

    Integration tests may leave a LogBufferHandler on the root logger. Without cleanup,
    log messages propagate to it and produce duplicate buffer entries in unit tests.
    """
    from obs.log_buffer import LogBufferHandler, _NON_PROPAGATING_LOGGER_NAMES, _buffer

    def _remove_handlers():
        loggers = [logging.getLogger(), *(logging.getLogger(name) for name in _NON_PROPAGATING_LOGGER_NAMES)]
        for logger in loggers:
            for h in list(logger.handlers):
                if isinstance(h, LogBufferHandler):
                    logger.removeHandler(h)

    _remove_handlers()
    _buffer.clear()
    yield
    _remove_handlers()
    _buffer.clear()


def _make_handler(level: int = logging.DEBUG):
    from obs.log_buffer import LogBufferHandler

    handler = LogBufferHandler(level=level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


# ---------------------------------------------------------------------------
# Basic capture
# ---------------------------------------------------------------------------


def test_emit_adds_entry_to_buffer():
    from obs.log_buffer import get_log_buffer

    logger = logging.getLogger("test.capture")
    logger.addHandler(_make_handler())
    logger.setLevel(logging.DEBUG)

    logger.info("hello log viewer")

    entries = get_log_buffer()
    assert len(entries) == 1
    assert entries[0]["level"] == "INFO"
    assert entries[0]["logger"] == "test.capture"
    assert "hello log viewer" in entries[0]["message"]


def test_entry_contains_all_fields():
    from obs.log_buffer import get_log_buffer

    logger = logging.getLogger("test.fields")
    logger.addHandler(_make_handler())
    logger.setLevel(logging.WARNING)

    logger.warning("field check")

    entry = get_log_buffer()[0]
    assert set(entry.keys()) == {"ts", "level", "logger", "message"}
    assert entry["ts"].endswith("Z")
    assert entry["level"] == "WARNING"


def test_message_contains_raw_log_message_without_prefix():
    from obs.log_buffer import get_log_buffer

    logger = logging.getLogger("test.message")
    logger.addHandler(_make_handler())
    logger.setLevel(logging.DEBUG)

    logger.info("plain message %s", "only")

    entry = get_log_buffer()[0]
    assert entry["message"] == "plain message only"
    assert "[INFO]" not in entry["message"]
    assert "test.message:" not in entry["message"]


def test_multiple_records_are_ordered_oldest_first():
    from obs.log_buffer import get_log_buffer

    logger = logging.getLogger("test.order")
    logger.addHandler(_make_handler())
    logger.setLevel(logging.DEBUG)

    for i in range(3):
        logger.info("msg %d", i)

    messages = [e["message"] for e in get_log_buffer()]
    assert "msg 0" in messages[0]
    assert "msg 2" in messages[2]


# ---------------------------------------------------------------------------
# deque maxlen behaviour
# ---------------------------------------------------------------------------


def test_buffer_evicts_oldest_when_full():
    from obs.log_buffer import _buffer, get_log_buffer

    # Temporarily shrink maxlen for the test
    original_maxlen = _buffer.maxlen
    _buffer.__init__(maxlen=3)  # type: ignore[misc]

    logger = logging.getLogger("test.maxlen")
    logger.addHandler(_make_handler())
    logger.setLevel(logging.DEBUG)

    for i in range(5):
        logger.info("entry %d", i)

    entries = get_log_buffer()
    assert len(entries) == 3
    assert "entry 2" in entries[0]["message"]
    assert "entry 4" in entries[2]["message"]

    _buffer.__init__(maxlen=original_maxlen)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Level filter
# ---------------------------------------------------------------------------


def test_level_filtering_in_handler():
    from obs.log_buffer import get_log_buffer

    logger = logging.getLogger("test.levelfilter")
    logger.addHandler(_make_handler(level=logging.WARNING))
    logger.setLevel(logging.DEBUG)

    logger.debug("should be ignored")
    logger.info("also ignored")
    logger.warning("captured")

    entries = get_log_buffer()
    assert len(entries) == 1
    assert entries[0]["level"] == "WARNING"


# ---------------------------------------------------------------------------
# install() attaches to root logger
# ---------------------------------------------------------------------------


def test_install_attaches_to_root_logger():
    import asyncio

    from obs.log_buffer import LogBufferHandler

    root = logging.getLogger()
    before = len(root.handlers)

    loop = asyncio.new_event_loop()
    LogBufferHandler.install(loop, level=logging.INFO)

    assert len(root.handlers) == before + 1
    assert any(isinstance(h, LogBufferHandler) for h in root.handlers)

    # Cleanup — remove handler we just added
    for h in list(root.handlers):
        if isinstance(h, LogBufferHandler):
            root.removeHandler(h)


def test_set_log_buffer_level_updates_installed_handler():
    import asyncio

    from obs.log_buffer import LogBufferHandler, get_log_buffer, set_log_buffer_level

    root = logging.getLogger()
    old_level = root.level
    loop = asyncio.new_event_loop()
    LogBufferHandler.install(loop, level=logging.INFO)
    root.setLevel(logging.INFO)

    logger = logging.getLogger("test.runtime_level")
    logger.setLevel(logging.DEBUG)

    logger.debug("ignored before runtime level change")
    assert get_log_buffer() == []

    set_log_buffer_level("DEBUG")
    logger.debug("captured after runtime level change")

    entries = get_log_buffer()
    assert len(entries) == 1
    assert entries[0]["level"] == "DEBUG"
    assert "captured after runtime level change" in entries[0]["message"]

    root.setLevel(old_level)


def test_install_attaches_to_non_propagating_uvicorn_error_logger_only():
    import asyncio

    from obs.log_buffer import LogBufferHandler, get_log_buffer

    access_logger = logging.getLogger("uvicorn.access")
    error_logger = logging.getLogger("uvicorn.error")

    old_access_propagate = access_logger.propagate
    old_error_propagate = error_logger.propagate
    old_access_level = access_logger.level
    old_error_level = error_logger.level

    access_logger.propagate = False
    error_logger.propagate = False
    access_logger.setLevel(logging.INFO)
    error_logger.setLevel(logging.INFO)

    loop = asyncio.new_event_loop()
    LogBufferHandler.install(loop, level=logging.INFO)

    assert not any(isinstance(h, LogBufferHandler) for h in access_logger.handlers)
    assert any(isinstance(h, LogBufferHandler) for h in error_logger.handlers)

    access_logger.info('127.0.0.1:1234 - "GET /api/v1/system/logs HTTP/1.1" 200 OK')
    error_logger.info("startup complete")

    entries = get_log_buffer()
    assert len(entries) == 1
    assert entries[0]["logger"] == "uvicorn.error"
    assert "startup complete" in entries[0]["message"]

    access_logger.propagate = old_access_propagate
    error_logger.propagate = old_error_propagate
    access_logger.setLevel(old_access_level)
    error_logger.setLevel(old_error_level)

    loop.close()


def test_handler_ignores_propagating_uvicorn_access_records():
    import asyncio

    from obs.log_buffer import LogBufferHandler, get_log_buffer

    access_logger = logging.getLogger("uvicorn.access")
    old_access_propagate = access_logger.propagate
    old_access_level = access_logger.level

    access_logger.propagate = True
    access_logger.setLevel(logging.INFO)

    loop = asyncio.new_event_loop()
    LogBufferHandler.install(loop, level=logging.INFO)

    access_logger.info('127.0.0.1:1234 - "GET /api/v1/ws?token=secret HTTP/1.1" 101 Switching Protocols')

    assert get_log_buffer() == []

    access_logger.propagate = old_access_propagate
    access_logger.setLevel(old_access_level)
    loop.close()


# ---------------------------------------------------------------------------
# _broadcast_nowait without WS manager must not raise
# ---------------------------------------------------------------------------


def test_broadcast_nowait_without_ws_manager_is_silent():
    from obs.log_buffer import _broadcast_nowait

    # No WS manager initialised — should silently return
    _broadcast_nowait({"ts": "x", "level": "INFO", "logger": "test", "message": "x"})
