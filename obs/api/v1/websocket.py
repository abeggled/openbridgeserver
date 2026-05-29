"""WebSocket API — Phase 4

Preferred auth: Authorization: Bearer {jwt}   (header; no URL token leakage)

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
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from obs.api.v1.sessions import validate_session
from obs.db.database import Database, get_db
from obs.models.visu import PageConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class WebSocketManager:
    """Tracks all connected WebSocket clients and their DataPoint subscriptions."""

    def __init__(self) -> None:
        # conn_id → (websocket, subscribed_dp_ids, send_lock, allowed_dp_ids)
        # allowed_dp_ids: None = unrestricted (authenticated user),
        # otherwise page-scoped allowlist for anonymous viewer sessions.
        # send_lock serialises concurrent sends on the same WebSocket;
        # concurrent asyncio.gather calls in EventBus would otherwise race.
        self._connections: dict[str, tuple[WebSocket, set[str], asyncio.Lock, set[str] | None]] = {}

    async def connect(
        self,
        ws: WebSocket,
        allowed_dp_ids: set[str] | None = None,
        subprotocol: str | None = None,
    ) -> str:
        if subprotocol is None:
            await ws.accept()
        else:
            try:
                await ws.accept(subprotocol=subprotocol)
            except TypeError:
                # Test doubles may not support the subprotocol kwarg.
                await ws.accept()
        conn_id = str(uuid.uuid4())
        self._connections[conn_id] = (ws, set(), asyncio.Lock(), allowed_dp_ids)
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
            allowed = self._connections[conn_id][3]
            if allowed is None:
                self._connections[conn_id][1].update(dp_ids)
            else:
                self._connections[conn_id][1].update(i for i in dp_ids if i in allowed)

    def subscriptions(self, conn_id: str) -> set[str]:
        entry = self._connections.get(conn_id)
        if entry is None:
            return set()
        return set(entry[1])

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
        ws, _, lock, _allowed = entry
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
        for conn_id, (_, subs, _lock, _allowed_ids) in list(self._connections.items()):
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
        dead = []
        for conn_id, (_, _subs, _lock, allowed_ids) in list(self._connections.items()):
            if allowed_ids is not None and dp_id_str not in allowed_ids:
                continue
            if not await self._send(conn_id, rb_msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


async def _page_allowed_datapoints(
    db: Database,
    page_id: str,
    *,
    widget_ref_access_check: Callable[[str], Awaitable[bool]] | None = None,
) -> set[str] | None:
    """Return datapoint IDs referenced by a PAGE node, or None if page does not exist."""

    datapoint_keys_exact = {
        "datapoint_id",
        "status_datapoint_id",
        "dp_id",
        "house_dp",
        "secondary_dp_id",
        "actual_temp_dp_id",
        "mode_dp_id",
    }

    def _is_uuid_str(value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except (TypeError, ValueError):
            return False

    def _is_datapoint_config_key(key: str, parent_key: str | None) -> bool:
        if key in datapoint_keys_exact:
            return True
        if key.startswith("dp_"):
            return True
        if key.endswith(("_dp", "_dp_id", "_datapoint_id")):
            return True
        # Widgets with array items that store datapoint IDs as `id`.
        # - Info: extra_datapoints[].id
        # - Energiefluss: entities[].id
        if key == "id" and parent_key in {"extra_datapoints", "entities"}:
            return True
        return False

    def _collect_datapoint_ids(value: Any, out: set[str], *, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if isinstance(nested, str) and _is_datapoint_config_key(key, parent_key) and _is_uuid_str(nested):
                    out.add(nested)
                _collect_datapoint_ids(nested, out, parent_key=key)
            return
        if isinstance(value, list):
            for nested in value:
                _collect_datapoint_ids(nested, out, parent_key=parent_key)

    def _non_empty_str(value: Any) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    page_cache: dict[str, PageConfig | None] = {}

    async def _load_page_config(target_page_id: str) -> PageConfig | None:
        if target_page_id in page_cache:
            return page_cache[target_page_id]
        row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (target_page_id,))
        if not row or not row["page_config"]:
            page_cache[target_page_id] = None
            return None
        try:
            parsed = PageConfig.model_validate_json(row["page_config"])
        except Exception:
            parsed = None
        page_cache[target_page_id] = parsed
        return parsed

    async def _collect_widget_datapoints(
        widget: Any,
        out: set[str],
        visited_refs: set[tuple[str, str]],
    ) -> None:
        if widget.datapoint_id and _is_uuid_str(widget.datapoint_id):
            out.add(widget.datapoint_id)
        if widget.status_datapoint_id and _is_uuid_str(widget.status_datapoint_id):
            out.add(widget.status_datapoint_id)
        _collect_datapoint_ids(widget.config, out)

        if widget.type != "widget_ref":
            return

        source_page_id = _non_empty_str(widget.config.get("source_page_id"))
        source_widget_name = _non_empty_str(widget.config.get("source_widget_name"))
        if not source_page_id or not source_widget_name:
            return
        if widget_ref_access_check is not None and not await widget_ref_access_check(source_page_id):
            return

        ref_key = (source_page_id, source_widget_name)
        if ref_key in visited_refs:
            return
        visited_refs.add(ref_key)

        source_page = await _load_page_config(source_page_id)
        if source_page is None:
            return

        target_widget = next(
            (candidate for candidate in source_page.widgets if candidate.name == source_widget_name),
            None,
        )
        if target_widget is None:
            return
        await _collect_widget_datapoints(target_widget, out, visited_refs)

    page = await _load_page_config(page_id)
    if page is None:
        return None

    ids: set[str] = set()
    visited_refs: set[tuple[str, str]] = set()
    for widget in page.widgets:
        await _collect_widget_datapoints(widget, ids, visited_refs)
    return ids


def _extract_subprotocol_tokens(ws: WebSocket) -> tuple[str | None, str | None, str | None]:
    offered_subprotocols = ws.scope.get("subprotocols")
    if not isinstance(offered_subprotocols, list):
        return None, None, None

    jwt_prefix = "obs.jwt."
    session_prefix = "obs.session."
    jwt_token: str | None = None
    session_token: str | None = None
    selected: str | None = None

    for candidate in offered_subprotocols:
        if not isinstance(candidate, str):
            continue
        if candidate.startswith(jwt_prefix):
            token = candidate.removeprefix(jwt_prefix)
            if token and jwt_token is None:
                jwt_token = token
                selected = candidate
        elif candidate.startswith(session_prefix):
            token = candidate.removeprefix(session_prefix)
            if token and session_token is None and selected is None:
                # Use session subprotocol only when no JWT subprotocol is selected.
                session_token = token
                selected = candidate
    return jwt_token, session_token, selected


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
) -> None:
    # Auth:
    # - authenticated users: unrestricted subscriptions/live pushes
    # - anonymous users: only allowed with page context and page-scoped datapoints
    from obs.api.auth import decode_token
    from obs.api.v1.visu import _resolve_access_with_node

    resolved_token: str | None = None
    selected_subprotocol: str | None = None
    auth_header = ws.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        resolved_token = auth_header[7:]
    subprotocol_jwt, subprotocol_session, selected = _extract_subprotocol_tokens(ws)
    if subprotocol_jwt:
        resolved_token = subprotocol_jwt
        selected_subprotocol = selected
    if resolved_token is None:
        query_token = ws.query_params.get("token")
        if query_token:
            resolved_token = query_token

    page_id = ws.query_params.get("page_id")
    user: str | None = None
    invalid_token = False
    if resolved_token is not None:
        try:
            user = decode_token(resolved_token)
        except Exception:
            invalid_token = True
            user = None

    if invalid_token and not page_id:
        await ws.close(code=4001, reason="Invalid token")
        return

    allowed_dp_ids: set[str] | None = None
    if user is None:
        if not page_id:
            await ws.close(code=4001, reason="Authentication required")
            return

        db = get_db()
        session_token = subprotocol_session or ws.query_params.get("session_token")
        access, defining_node_id = await _resolve_access_with_node(db, page_id)
        if access == "protected":
            validate_id = defining_node_id or page_id
            if not session_token or not validate_session(session_token, validate_id):
                await ws.close(code=4003, reason="Valid session token required")
                return
        elif access == "user":
            await ws.close(code=4001, reason="Authentication required")
            return
        elif access not in ("public", "readonly"):
            await ws.close(code=4001, reason="Authentication required")
            return

        async def _can_access_widget_ref_page(source_page_id: str) -> bool:
            source_access, source_defining_node_id = await _resolve_access_with_node(db, source_page_id)
            if source_access in ("public", "readonly"):
                return True
            if source_access == "protected":
                source_validate_id = source_defining_node_id or source_page_id
                return bool(session_token and validate_session(session_token, source_validate_id))
            return False

        allowed_dp_ids = await _page_allowed_datapoints(
            db,
            page_id,
            widget_ref_access_check=_can_access_widget_ref_page,
        )
        if allowed_dp_ids is None:
            await ws.close(code=4003, reason="Page not found")
            return

    manager = get_ws_manager()
    conn_id = await manager.connect(ws, allowed_dp_ids=allowed_dp_ids, subprotocol=selected_subprotocol)

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
                before = manager.subscriptions(conn_id)
                manager.subscribe(conn_id, ids)
                after = manager.subscriptions(conn_id)
                added = [i for i in ids if i in after and i not in before]
                await ws.send_json({"action": "subscribed", "ids": added})

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
