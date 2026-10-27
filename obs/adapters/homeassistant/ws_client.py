"""Multiplexed Home Assistant WebSocket connection (#1392).

One authenticated connection carries every command and subscription of an
adapter instance.  A background reader routes ``result``/``pong`` messages to
the waiting command at once and queues ``event`` messages for a separate
worker, so slow event handlers never delay a ping answer or a command result.

The access token is only ever sent in the ``auth`` message; it never appears in
an exception text or a log line produced here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# get_states / the subscribe_entities snapshot can exceed websockets' 1 MiB default.
MAX_MESSAGE_SIZE = 32 * 1024 * 1024

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class HaAuthError(Exception):
    """The token was rejected (``auth_invalid``)."""


class HaProtocolError(Exception):
    """HA answered with something the protocol does not allow at this point."""


class HaCommandError(Exception):
    """A command returned ``success: false``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HaConnection:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, EventCallback] = {}
        self._reader: asyncio.Task | None = None
        self._worker: asyncio.Task | None = None
        self._events: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
        self._closed = asyncio.Event()
        self.ha_version: str = ""

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @classmethod
    async def open(cls, uri: str, token: str, *, connect: Callable[..., Any], timeout: float = 10.0) -> HaConnection:
        ws = await asyncio.wait_for(connect(uri, max_size=MAX_MESSAGE_SIZE), timeout)
        conn = cls(ws)
        try:
            await asyncio.wait_for(conn._authenticate(token), timeout)
        except BaseException:
            await conn.close()
            raise
        conn._reader = asyncio.create_task(conn._read_loop(), name="ha-ws-reader")
        conn._worker = asyncio.create_task(conn._event_loop(), name="ha-ws-events")
        return conn

    async def _authenticate(self, token: str) -> None:
        msg = json.loads(await self._ws.recv())
        if msg.get("type") != "auth_required":
            raise HaProtocolError(f"unexpected first message type {msg.get('type')!r}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await self._ws.recv())
        if msg.get("type") == "auth_invalid":
            raise HaAuthError("authentication failed")
        if msg.get("type") != "auth_ok":
            raise HaProtocolError(f"unexpected auth response type {msg.get('type')!r}")
        self.ha_version = str(msg.get("ha_version", ""))

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        for task in (self._reader, self._worker):
            await self._stop(task)
        with contextlib.suppress(Exception):
            await self._ws.close()
        self._mark_closed(ConnectionError("connection closed"))

    @staticmethod
    async def _stop(task: asyncio.Task | None) -> None:
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Swallow only the task's own cancellation, never the caller's.
            if asyncio.current_task().cancelling():
                raise

    def _mark_closed(self, exc: BaseException) -> None:
        self._closed.set()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        self._subscriptions.clear()

    # ------------------------------------------------------------------
    # Reader
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        error: BaseException = ConnectionError("connection closed by Home Assistant")
        try:
            async for raw in self._ws:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning("HA WebSocket: ignoring malformed message")
                    continue
                for msg in payload if isinstance(payload, list) else [payload]:
                    if isinstance(msg, dict):
                        self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- handed to every waiting command via _mark_closed
            error = exc
        finally:
            self._mark_closed(error)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        kind = msg.get("type")
        if kind == "event":
            if msg_id not in self._subscriptions:
                return
            event = msg.get("event")
            if not isinstance(event, dict):
                logger.warning("HA WebSocket: ignoring event without payload")
                return
            self._events.put_nowait((msg_id, event))  # type: ignore[arg-type]
            return
        if kind in ("result", "pong"):
            fut = self._pending.pop(msg_id, None)  # type: ignore[arg-type]
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _event_loop(self) -> None:
        """Run subscription callbacks in arrival order, off the reader task."""
        while True:
            msg_id, event = await self._events.get()
            callback = self._subscriptions.get(msg_id)
            if callback is None:
                continue  # unsubscribed (or connection closed) since the event arrived
            try:
                await callback(event)
            except Exception:
                logger.exception("HA WebSocket: event handler failed")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _request(
        self,
        payload: dict[str, Any],
        timeout: float,
        before_send: Callable[[int], None] | None = None,
    ) -> dict[str, Any]:
        if self.closed:
            raise ConnectionError("connection closed")
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        if before_send is not None:
            before_send(msg_id)
        try:
            await self._ws.send(json.dumps({**payload, "id": msg_id}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(msg_id, None)

    @staticmethod
    def _result(msg: dict[str, Any]) -> Any:
        if not msg.get("success", False):
            error = msg.get("error") or {}
            raise HaCommandError(str(error.get("code", "unknown_error")), str(error.get("message", "")))
        return msg.get("result")

    async def command(self, payload: dict[str, Any], timeout: float = 30.0) -> Any:
        return self._result(await self._request(payload, timeout))

    async def subscribe(self, payload: dict[str, Any], callback: EventCallback, timeout: float = 30.0) -> int:
        """Start a subscription; events for it go to *callback* in arrival order."""
        ids: list[int] = []

        def register(msg_id: int) -> None:
            # Registered before sending: the first event may follow the result immediately.
            ids.append(msg_id)
            self._subscriptions[msg_id] = callback

        try:
            self._result(await self._request(payload, timeout, before_send=register))
        except BaseException:
            if ids:
                self._subscriptions.pop(ids[0], None)
            raise
        return ids[0]

    async def unsubscribe(self, subscription_id: int) -> None:
        if self._subscriptions.pop(subscription_id, None) is None or self.closed:
            return
        with contextlib.suppress(HaCommandError):
            await self.command({"type": "unsubscribe_events", "subscription": subscription_id}, timeout=10.0)

    async def ping(self, timeout: float = 10.0) -> None:
        await self._request({"type": "ping"}, timeout)
