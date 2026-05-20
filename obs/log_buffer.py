"""In-memory log buffer with WebSocket broadcast.

A single LogBufferHandler is attached to the root logger in obs/main.py.
Every log record emitted anywhere in the process is captured into a bounded
deque and simultaneously pushed to all connected WebSocket clients.

The deque size is intentionally small (500 entries ≈ <100 KB RAM).
Logs are not persisted across restarts — they are a debugging aid, not an
audit trail.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

_buffer: deque[dict[str, Any]] = deque(maxlen=500)
_loop: asyncio.AbstractEventLoop | None = None
_NON_PROPAGATING_LOGGER_NAMES = ("uvicorn.access", "uvicorn.error")


def get_log_buffer() -> list[dict[str, Any]]:
    """Return a snapshot of the current buffer (newest entry last)."""
    return list(_buffer)


def set_log_buffer_level(level: int | str) -> None:
    """Update all installed LogBufferHandler instances to the given level."""
    logging.getLogger().setLevel(level)
    for handler in _iter_log_buffer_handlers():
        handler.setLevel(level)


class LogBufferHandler(logging.Handler):
    """Captures log records into an in-memory deque and broadcasts them via WebSocket."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            _buffer.append(entry)

            # Fire-and-forget WS broadcast — must never block the logging call.
            if _loop is not None and _loop.is_running():
                _loop.call_soon_threadsafe(_broadcast_nowait, entry)
        except Exception:
            # During interpreter shutdown sys.meta_path is None and imports/loop
            # operations raise — a logging handler must never crash its caller.
            pass

    @classmethod
    def install(cls, loop: asyncio.AbstractEventLoop, level: int = logging.INFO) -> None:
        """Attach a handler instance to the root logger.

        Call once after logging.basicConfig() in obs/main.py.
        """
        global _loop
        _loop = loop
        handler = cls(level=level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        for logger_name in _NON_PROPAGATING_LOGGER_NAMES:
            logger = logging.getLogger(logger_name)
            if not logger.propagate:
                logger.addHandler(handler)


def _iter_log_buffer_handlers() -> list[LogBufferHandler]:
    """Return installed buffer handlers across root and non-propagating loggers."""
    handlers: list[LogBufferHandler] = []
    seen: set[int] = set()
    loggers = [logging.getLogger(), *(logging.getLogger(name) for name in _NON_PROPAGATING_LOGGER_NAMES)]
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, LogBufferHandler) and id(handler) not in seen:
                seen.add(id(handler))
                handlers.append(handler)
    return handlers


def _broadcast_nowait(entry: dict[str, Any]) -> None:
    """Schedule a WS broadcast without awaiting it (called from call_soon_threadsafe)."""
    if _loop is None or not _loop.is_running():
        return
    try:
        from obs.api.v1.websocket import get_ws_manager

        manager = get_ws_manager()
    except RuntimeError:
        # WS manager not yet initialised (early startup log records) — drop silently.
        return
    _loop.create_task(manager.broadcast({"action": "log_entry", "entry": entry}))
