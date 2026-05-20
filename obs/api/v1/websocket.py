"""WebSocket API — Phase 4

Preferred auth: Authorization: Bearer {jwt}   (header — token not logged)
Legacy fallback: WS /api/v1/ws?token={jwt}    (query param — avoid in production)

Client → Server:
  {"action": "subscribe",   "ids": ["uuid1", "uuid2"]}
  {"action": "unsubscribe", "ids": ["uuid1"]}
  {"action": "ping"}

Server → Client (on value change):
  {"id": "uuid1", "v": 21.4, "u": "°C", "t": "2025-03-26T10:23:41.123Z", "q": "good", "old_v": 21.1}

Server → Client (pong):
  {"action": "pong"}
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class WebSocketManager:
    """Tracks all connected WebSocket clients and their DataPoint subscriptions."""

    def __init__(self) -> None:
        # conn_id → (websocket, subscribed_dp_ids, send_lock)
        # send_lock serialises concurrent sends on the same WebSocket;
        # concurrent asyncio.gather calls in EventBus would otherwise race.
        self._connections: dict[str, tuple[WebSocket, set[str], asyncio.Lock]] = {}

    async def connect(self, ws: WebSocket) -> str:
        await ws.accept()
        conn_id = str(uuid.uuid4())
        self._connections[conn_id] = (ws, set(), asyncio.Lock())
        logger.debug("WS client connected: %s  (total: %d)", conn_id[:8], len(self._connections))
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        entry = self._connections.pop(conn_id, None)
        if entry:
            ws = entry[0]
            try:
                await ws.close()
            except Exception:
                pass
        logger.debug(
            "WS client disconnected: %s  (total: %d)",
            conn_id[:8],
            len(self._connections),
        )

    def subscribe(self, conn_id: str, dp_ids: list[str]) -> None:
        if conn_id in self._connections:
            self._connections[conn_id][1].update(dp_ids)

    def unsubscribe(self, conn_id: str, dp_ids: list[str]) -> None:
        if conn_id in self._connections:
            self._connections[conn_id][1].difference_update(dp_ids)

    async def _send(self, conn_id: str, msg: dict) -> bool:
        """Send *msg* to one connection, serialised via its per-connection lock.

        Returns True on success, False if the WebSocket itself is broken (caller
        should disconnect so the client can reconnect cleanly).
        Serialisation errors (e.g. non-JSON-serialisable value) are logged and
        the message is dropped — they do NOT close the connection.
        """
        entry = self._connections.get(conn_id)
        if entry is None:
            return False
        ws, _, lock = entry
        async with lock:
            try:
                await ws.send_json(msg)
                return True
            except (TypeError, ValueError) as exc:
                # The message itself cannot be serialised — log and drop it,
                # but keep the WebSocket open.
                logger.error("WS send skipped — message not JSON-serialisable: %s", exc)
                return True
            except Exception:
                # Actual transport error — signal caller to close the connection.
                return False

    async def broadcast(self, msg: dict) -> None:
        """Send a message to ALL connected clients (no subscription filter)."""
        dead: list[str] = []
        for conn_id in list(self._connections):
            if not await self._send(conn_id, msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

    async def handle_value_event(self, event: Any) -> None:
        """Called by EventBus when a DataValueEvent arrives."""
        if not self._connections:
            return

        from obs.core.registry import get_registry

        try:
            reg = get_registry()
        except RuntimeError:
            return

        dp_id_str = str(event.datapoint_id)
        dp = reg.get(event.datapoint_id)
        state = reg.get_value(event.datapoint_id)
        ts_str = event.ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # ── 1. Per-subscription DP value events ──────────────────────────
        dp_msg = {
            "id": dp_id_str,
            "v": event.value,
            "u": dp.unit if dp else None,
            "t": ts_str,
            "q": event.quality,
            "old_v": state.old_value if state else None,
        }
        dead: list[str] = []
        for conn_id, (_, subs, _lock) in list(self._connections.items()):
            if dp_id_str not in subs:
                continue
            if not await self._send(conn_id, dp_msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

        # ── 2. RingBuffer live-push — broadcast to ALL clients ────────────
        rb_msg = {
            "action": "ringbuffer_entry",
            "entry": {
                "ts": ts_str,
                "datapoint_id": dp_id_str,
                "name": dp.name if dp else None,
                "new_value": event.value,
                "old_value": state.old_value if state else None,
                "quality": event.quality,
                "source_adapter": event.source_adapter,
                "unit": dp.unit if dp else None,
            },
        }
        await self.broadcast(rb_msg)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: WebSocketManager | None = None


def get_ws_manager() -> WebSocketManager:
    if _manager is None:
        raise RuntimeError("WebSocketManager not initialized")
    return _manager


def reset_ws_manager() -> None:
    """Reset the WebSocketManager singleton. For testing only."""
    global _manager
    _manager = None


def init_ws_manager() -> WebSocketManager:
    global _manager
    _manager = WebSocketManager()
    return _manager


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str | None = Query(None, description="JWT access token (legacy — prefer Authorization header)"),
) -> None:
    # Auth: optional — authenticated users get a user context, anonymous users
    # can still subscribe to public datapoints (read-only push channel).
    from obs.api.auth import decode_token

    auth_header = ws.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        resolved_token: str | None = auth_header[7:]
    elif token:
        logger.debug("WS auth via query param — prefer Authorization header in production")
        resolved_token = token
    else:
        resolved_token = None

    if resolved_token is not None:
        try:
            decode_token(resolved_token)
        except Exception:
            await ws.close(code=4001, reason="Invalid token")
            return

    manager = get_ws_manager()
    conn_id = await manager.connect(ws)

    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
            except TimeoutError:
                # Send keepalive
                await ws.send_json({"action": "ping"})
                continue

            action = data.get("action", "")

            if action == "subscribe":
                ids = [str(i) for i in data.get("ids", [])]
                manager.subscribe(conn_id, ids)
                await ws.send_json({"action": "subscribed", "ids": ids})

            elif action == "unsubscribe":
                ids = [str(i) for i in data.get("ids", [])]
                manager.unsubscribe(conn_id, ids)
                await ws.send_json({"action": "unsubscribed", "ids": ids})

            elif action == "ping":
                await ws.send_json({"action": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for connection %s", conn_id[:8])
    finally:
        await manager.disconnect(conn_id)
