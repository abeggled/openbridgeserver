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
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from obs.api.v1 import sessions as sessions_api
from obs.api.v1.datapoint_config import collect_datapoint_ids_from_config, is_uuid_str
from obs.core.json import jsonable
from obs.db.database import Database, get_db
from obs.models.visu import PageConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

LogAccessCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class MessageArchivePredicate:
    archive_ids: set[str] | None = None
    types: set[str] | None = None
    severities: set[str] | None = None
    statuses: set[str] | None = None
    sources: set[str] | None = None
    allow_read: bool = True
    allow_acknowledge: bool = True


MessageArchiveAccess = list[MessageArchivePredicate] | None


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class WebSocketManager:
    """Tracks all connected WebSocket clients and their DataPoint subscriptions."""

    def __init__(self) -> None:
        # conn_id → (websocket, subscribed_dp_ids, send_lock, allowed_dp_ids, log_access, log_access_check)
        # allowed_dp_ids: None = unrestricted (authenticated user),
        # otherwise page-scoped allowlist for anonymous viewer sessions.
        # log_access: authenticated non-page connections receive log_entry pushes.
        # log_access_check: revalidates API-key existence before every log_entry push.
        # send_lock serialises concurrent sends on the same WebSocket;
        # concurrent asyncio.gather calls in EventBus would otherwise race.
        self._connections: dict[str, tuple[WebSocket, set[str], asyncio.Lock, set[str] | None, bool, LogAccessCheck | None]] = {}
        # conn_id -> allowed message archive predicates. None means unrestricted.
        self._message_archive_access: dict[str, MessageArchiveAccess] = {}

    async def connect(
        self,
        ws: WebSocket,
        allowed_dp_ids: set[str] | None = None,
        allowed_message_archive_ids: set[str] | None = None,
        allowed_message_archive_access: MessageArchiveAccess = None,
        log_access: bool = False,
        log_access_check: LogAccessCheck | None = None,
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
        self._connections[conn_id] = (ws, set(), asyncio.Lock(), allowed_dp_ids, log_access, log_access_check)
        if allowed_message_archive_access is not None:
            self._message_archive_access[conn_id] = allowed_message_archive_access
        elif allowed_message_archive_ids is not None:
            self._message_archive_access[conn_id] = [MessageArchivePredicate(archive_ids=allowed_message_archive_ids)]
        else:
            self._message_archive_access[conn_id] = None
        logger.debug("WS client connected: %s  (total: %d)", conn_id[:8], len(self._connections))
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        entry = self._connections.pop(conn_id, None)
        self._message_archive_access.pop(conn_id, None)
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

    async def send_initial_values(self, conn_id: str, dp_ids: list[str]) -> None:
        """Send current registry values for subscribed datapoints."""
        from obs.core.registry import get_registry

        try:
            reg = get_registry()
        except RuntimeError:
            return

        dead = False
        for dp_id in dp_ids:
            try:
                dp_uuid = uuid.UUID(dp_id)
            except (TypeError, ValueError):
                continue

            dp = reg.get(dp_uuid)
            state = reg.get_value(dp_uuid)
            if dp is None or state is None:
                continue

            ts = state.ts
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            msg = {
                "id": str(dp_uuid),
                "v": jsonable(state.value),
                "u": dp.unit,
                "t": ts_str,
                "q": state.quality,
            }
            if not await self._send(conn_id, msg):
                dead = True
                break

        if dead:
            await self.disconnect(conn_id)

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
        ws = entry[0]
        lock = entry[2]
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
        log_only = msg.get("action") == "log_entry"
        for conn_id, entry in list(self._connections.items()):
            _, _subs, _lock, _allowed_ids, log_access, log_access_check = entry
            if log_only:
                if not log_access:
                    continue
                if log_access_check is not None and not await log_access_check():
                    self._set_log_access(conn_id, False)
                    continue
            if not await self._send(conn_id, msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

    async def broadcast_message_archive_entry(self, entry: dict[str, Any], previous_entry: dict[str, Any] | None = None) -> None:
        """Push a newly stored message archive entry to allowed clients."""
        dead: list[str] = []
        msg = {"action": "message_archive_entry", "entry": entry}
        for conn_id in list(self._connections):
            access = self._message_archive_access.get(conn_id)
            if access is not None and not (
                _message_archive_entry_matches_access(entry, access)
                or (previous_entry is not None and _message_archive_entry_matches_access(previous_entry, access))
            ):
                continue
            if not await self._send(conn_id, msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

    async def broadcast_message_archive_refresh(self, archive_id: str | None = None) -> None:
        """Ask clients with access to an archive to refresh their message archive view."""
        dead: list[str] = []
        normalized_archive_id = str(archive_id or "").lower() or None
        msg = {"action": "message_archive_refresh", "archive_id": normalized_archive_id}
        for conn_id in list(self._connections):
            access = self._message_archive_access.get(conn_id)
            if access is not None and not _message_archive_archive_matches_access(normalized_archive_id, access):
                continue
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
            "v": jsonable(event.value),
            "u": dp.unit if dp else None,
            "t": ts_str,
            "q": event.quality,
            "old_v": jsonable(state.old_value) if state else None,
        }
        dead: list[str] = []
        for conn_id, entry in list(self._connections.items()):
            subs = entry[1]
            if dp_id_str not in subs:
                continue
            if not await self._send(conn_id, dp_msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

        # ── 2. RingBuffer live-push — broadcast to ALL clients ────────────
        from obs.ringbuffer.ringbuffer import is_ringbuffer_enabled

        if not is_ringbuffer_enabled():
            return

        base_rb_entry = {
            "ts": ts_str,
            "datapoint_id": dp_id_str,
            "name": dp.name if dp else None,
            "new_value": jsonable(event.value),
            "old_value": jsonable(state.old_value) if state else None,
            "quality": event.quality,
            "source_adapter": event.source_adapter,
            "unit": dp.unit if dp else None,
        }
        metadata: dict[str, Any] | None = None
        if any(entry[3] is None for entry in self._connections.values()):
            from obs.ringbuffer.ringbuffer import build_ringbuffer_metadata_snapshot

            metadata = await build_ringbuffer_metadata_snapshot(
                dp_id=dp_id_str,
                source_adapter=str(event.source_adapter),
                datapoint=dp,
            )
        dead = []
        for conn_id, entry in list(self._connections.items()):
            allowed_ids = entry[3]
            if allowed_ids is not None and dp_id_str not in allowed_ids:
                continue
            rb_entry = base_rb_entry
            if allowed_ids is None and metadata is not None:
                rb_entry = {
                    **base_rb_entry,
                    "metadata_version": 1,
                    "metadata": metadata,
                }
            rb_msg = {"action": "ringbuffer_entry", "entry": rb_entry}
            if not await self._send(conn_id, rb_msg):
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(conn_id)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def _set_log_access(self, conn_id: str, log_access: bool) -> None:
        entry = self._connections.get(conn_id)
        if entry is None:
            return
        ws, subs, lock, allowed_dp_ids, _old_log_access, log_access_check = entry
        self._connections[conn_id] = (ws, subs, lock, allowed_dp_ids, log_access, log_access_check)


async def _page_allowed_datapoints(
    db: Database,
    page_id: str,
    *,
    widget_ref_access_check: Callable[[str], Awaitable[bool]] | None = None,
) -> set[str] | None:
    """Return datapoint IDs referenced by a PAGE node, or None if page does not exist."""

    def _non_empty_str(value: Any) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    page_cache: dict[str, PageConfig | None] = {}

    async def _load_page_config(target_page_id: str) -> PageConfig | None:
        if target_page_id in page_cache:
            return page_cache[target_page_id]
        row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (target_page_id,))
        page_config_raw = None
        if row:
            if isinstance(row, dict):
                page_config_raw = row.get("page_config")
            else:
                try:
                    page_config_raw = row["page_config"]
                except Exception:
                    page_config_raw = None
        if not row or not page_config_raw:
            page_cache[target_page_id] = None
            return None
        try:
            parsed = PageConfig.model_validate_json(page_config_raw)
        except Exception:
            parsed = None
        page_cache[target_page_id] = parsed
        return parsed

    async def _collect_widget_datapoints(
        widget: Any,
        out: set[str],
        visited_refs: set[tuple[str, str]],
    ) -> None:
        if widget.datapoint_id and is_uuid_str(widget.datapoint_id):
            out.add(widget.datapoint_id)
        if widget.status_datapoint_id and is_uuid_str(widget.status_datapoint_id):
            out.add(widget.status_datapoint_id)
        collect_datapoint_ids_from_config(widget.config, out)

        if widget.type not in {"widget_ref", "WidgetRef"}:
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


async def _page_allowed_message_archives(
    db: Database,
    page_id: str,
    *,
    widget_ref_access_check: Callable[[str], Awaitable[bool]] | None = None,
    widget_ref_readonly_check: Callable[[str], Awaitable[bool]] | None = None,
) -> set[str] | None:
    """Return archive IDs referenced by MessageArchive widgets.

    None means a page contains a MessageArchive widget without an archive
    filter, so it intentionally displays all message archives.
    """
    predicates = await _page_allowed_message_archive_predicates(
        db,
        page_id,
        widget_ref_access_check=widget_ref_access_check,
        widget_ref_readonly_check=widget_ref_readonly_check,
    )
    if not predicates:
        return set()
    ids: set[str] = set()
    for predicate in predicates:
        if predicate.archive_ids is None:
            return None
        ids.update(predicate.archive_ids)
    return ids


async def _page_allowed_message_archive_predicates(
    db: Database,
    page_id: str,
    *,
    widget_ref_access_check: Callable[[str], Awaitable[bool]] | None = None,
    widget_ref_readonly_check: Callable[[str], Awaitable[bool]] | None = None,
) -> list[MessageArchivePredicate]:
    """Return per-widget MessageArchive predicates for page-scoped pushes."""

    def _non_empty_str(value: Any) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    def _string_set_from_config(config: dict[str, Any], *keys: str, lower: bool = False) -> set[str] | None:
        values: list[str] = []
        for key in keys:
            raw = config.get(key)
            if isinstance(raw, list):
                values.extend(item for item in raw if isinstance(item, str))
            elif isinstance(raw, str) and raw:
                values.extend(part.strip() for part in raw.split(","))
        cleaned = {(value.strip().lower() if lower else value.strip()) for value in values if value.strip()}
        if not cleaned:
            return None
        return cleaned

    def _bool_from_config(config: dict[str, Any], *keys: str, default: bool = True) -> bool:
        for key in keys:
            if key in config:
                return bool(config.get(key))
        return default

    def _mini_widgets_from_config(config: dict[str, Any]) -> list[Any]:
        raw = config.get("miniWidgets")
        return raw if isinstance(raw, list) else []

    page_cache: dict[str, PageConfig | None] = {}

    async def _load_page_config(target_page_id: str) -> PageConfig | None:
        if target_page_id in page_cache:
            return page_cache[target_page_id]
        row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (target_page_id,))
        page_config_raw = None
        if row:
            if isinstance(row, dict):
                page_config_raw = row.get("page_config")
            else:
                try:
                    page_config_raw = row["page_config"]
                except Exception:
                    page_config_raw = None
        if not row or not page_config_raw:
            page_cache[target_page_id] = None
            return None
        try:
            parsed = PageConfig.model_validate_json(page_config_raw)
        except Exception:
            parsed = None
        page_cache[target_page_id] = parsed
        return parsed

    async def _collect_widget_archives(
        widget: Any,
        out: list[MessageArchivePredicate],
        visited_refs: set[tuple[str, str, bool]],
        *,
        inherited_readonly: bool = False,
    ) -> None:
        config = widget.config if isinstance(widget.config, dict) else {}
        if widget.type == "MessageArchive":
            out.append(
                MessageArchivePredicate(
                    archive_ids=_string_set_from_config(config, "archive_ids", "archive_id", lower=True),
                    types=_string_set_from_config(config, "types", "type"),
                    severities=_string_set_from_config(config, "severities", "severity"),
                    statuses=_string_set_from_config(config, "statuses", "status"),
                    sources=_string_set_from_config(config, "sources", "source"),
                    allow_read=False if inherited_readonly else _bool_from_config(config, "allow_read", "allowRead", default=True),
                    allow_acknowledge=False
                    if inherited_readonly
                    else _bool_from_config(config, "allow_acknowledge", "allowAcknowledge", default=True),
                )
            )

        for mini_widget in _mini_widgets_from_config(config):
            if not isinstance(mini_widget, dict):
                continue
            if mini_widget.get("visible") is not True:
                continue
            mini_type = _non_empty_str(mini_widget.get("widgetType")) or _non_empty_str(mini_widget.get("type"))
            if not mini_type:
                continue
            mini_config = mini_widget.get("config")
            await _collect_widget_archives(
                SimpleNamespace(type=mini_type, config=mini_config if isinstance(mini_config, dict) else {}),
                out,
                visited_refs,
                inherited_readonly=inherited_readonly,
            )

        if widget.type not in {"widget_ref", "WidgetRef"}:
            return

        source_page_id = _non_empty_str(widget.config.get("source_page_id"))
        source_widget_name = _non_empty_str(widget.config.get("source_widget_name"))
        if not source_page_id or not source_widget_name:
            return
        if widget_ref_access_check is not None and not await widget_ref_access_check(source_page_id):
            return
        source_is_readonly = False
        if widget_ref_readonly_check is not None:
            source_is_readonly = await widget_ref_readonly_check(source_page_id)

        ref_readonly = inherited_readonly or source_is_readonly
        ref_key = (source_page_id, source_widget_name, ref_readonly)
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
        await _collect_widget_archives(target_widget, out, visited_refs, inherited_readonly=ref_readonly)

    page = await _load_page_config(page_id)
    if page is None:
        return []

    predicates: list[MessageArchivePredicate] = []
    visited_refs: set[tuple[str, str, bool]] = set()
    for widget in page.widgets:
        await _collect_widget_archives(widget, predicates, visited_refs)
    return predicates


def _message_archive_entry_matches_access(entry: dict[str, Any], access: list[MessageArchivePredicate]) -> bool:
    for predicate in access:
        archive_id = str(entry.get("archive_id") or "").lower()
        if predicate.archive_ids is not None and archive_id not in predicate.archive_ids:
            continue
        if predicate.types and str(entry.get("type") or "") not in predicate.types:
            continue
        if predicate.severities and str(entry.get("severity") or "") not in predicate.severities:
            continue
        if predicate.statuses and str(entry.get("status") or "") not in predicate.statuses:
            continue
        if predicate.sources and str(entry.get("source") or "") not in predicate.sources:
            continue
        return True
    return False


def _message_archive_archive_matches_access(archive_id: str | None, access: list[MessageArchivePredicate]) -> bool:
    if archive_id is None:
        return True
    for predicate in access:
        if predicate.archive_ids is None or archive_id in predicate.archive_ids:
            return True
    return False


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


def _requested_jwt_subprotocol(ws: WebSocket) -> str | None:
    _, _, selected = _extract_subprotocol_tokens(ws)
    if selected and selected.startswith("obs.jwt."):
        return selected
    return None


async def _authorize_visu_page_scope(
    *,
    db: Database,
    page_id: str | None,
    session_token: str | None,
    username: str | None,
) -> tuple[bool, str]:
    """Validate that a websocket may use a visu page scope."""
    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

    if not page_id:
        return False, "Missing credentials"

    page_row = await db.fetchone("SELECT type FROM visu_nodes WHERE id = ?", (page_id,))
    if not page_row:
        return False, "Page not found"
    page_type = page_row.get("type") if isinstance(page_row, dict) else page_row["type"]
    if page_type != "PAGE":
        return False, "Invalid visu page"

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    if access in ("public", "readonly"):
        return True, "OK"
    if access == "protected":
        validate_id = defining_node_id or page_id
        if session_token and sessions_api.validate_session(session_token, validate_id):
            return True, "OK"
        return False, "Valid session token required"
    if access == "user":
        if username is None:
            return False, "Authentication required"
        if username == "__api_key__" or username.startswith("api_key:"):
            return False, "Authentication required"
        if await _check_user_access(db, page_id, username):
            return True, "OK"
        return False, "Zugriff verweigert"
    return False, "Authentication required"


async def _authenticate_visu_page_scope(ws: WebSocket) -> tuple[bool, str]:
    """Validate page-scoped visu websocket access without JWT."""
    _jwt_token, session_token_subprotocol, _selected = _extract_subprotocol_tokens(ws)
    session_token = session_token_subprotocol or ws.query_params.get("session_token")
    page_id = ws.query_params.get("page_id")
    if not page_id:
        return False, "Missing credentials"
    return await _authorize_visu_page_scope(
        db=get_db(),
        page_id=page_id,
        session_token=session_token,
        username=None,
    )


async def _authenticate_ws_request(ws: WebSocket) -> tuple[bool, str]:
    """Validate auth for websocket handshake."""
    from obs.api.auth import decode_token, hash_api_key

    auth_header = ws.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            decode_token(auth_header[7:])
            return True, "OK"
        except Exception:
            return False, "Invalid token"

    subprotocol_jwt, _session_token, _selected = _extract_subprotocol_tokens(ws)
    if subprotocol_jwt:
        try:
            decode_token(subprotocol_jwt)
            return True, "OK"
        except Exception:
            return False, "Invalid token"

    api_key = ws.headers.get("x-api-key")
    if api_key:
        db = get_db()
        key_hash = hash_api_key(api_key)
        row = await db.fetchone("SELECT name FROM api_keys WHERE key_hash=?", (key_hash,))
        if not row:
            return False, "Invalid API key"
        await db.execute_and_commit(
            "UPDATE api_keys SET last_used_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key_hash=?",
            (key_hash,),
        )
        return True, "OK"

    return await _authenticate_visu_page_scope(ws)


async def _resolve_ws_api_key_subject(api_key: str) -> str | None:
    """Return the REST-equivalent subject for an already authenticated API key."""
    from obs.api.auth import hash_api_key

    db = get_db()
    key_hash = hash_api_key(api_key)
    row = await db.fetchone("SELECT id, owner FROM api_keys WHERE key_hash=?", (key_hash,))
    if not row:
        return None
    owner = row["owner"] if row["owner"] else None
    return owner or f"api_key:{row['id']}"


async def _ws_has_log_access(user: str | None, api_key: str | None) -> bool:
    """Return whether the authenticated websocket may receive log_entry pushes."""
    if user and user != "__api_key__":
        return True
    if api_key:
        try:
            db = get_db()
        except RuntimeError:
            return False
        from obs.api.auth import hash_api_key

        key_hash = hash_api_key(api_key)
        row = await db.fetchone(
            "SELECT COALESCE(NULLIF(owner, ''), name) AS subject FROM api_keys WHERE key_hash=?",
            (key_hash,),
        )
        return row is not None
    return False


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
    requested_subprotocol = _requested_jwt_subprotocol(ws)
    auth_ok, reason = await _authenticate_ws_request(ws)
    if not auth_ok:
        if requested_subprotocol:
            await ws.accept(subprotocol=requested_subprotocol)
        else:
            await ws.accept()
        await ws.close(code=4001, reason=reason)
        return

    # Auth:
    # - authenticated users: unrestricted subscriptions/live pushes
    # - anonymous users: only allowed with page context and page-scoped datapoints
    from obs.api.auth import decode_token
    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

    resolved_token: str | None = None
    selected_subprotocol: str | None = None
    api_key = ws.headers.get("x-api-key")
    auth_header = ws.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        resolved_token = auth_header[7:]
    subprotocol_jwt, subprotocol_session, selected = _extract_subprotocol_tokens(ws)
    if subprotocol_jwt:
        resolved_token = subprotocol_jwt
        selected_subprotocol = selected

    page_id = ws.query_params.get("page_id")
    user: str | None = "__api_key__" if api_key else None
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
    allowed_message_archive_access: MessageArchiveAccess = None
    db = get_db() if page_id else None
    session_token = subprotocol_session or ws.query_params.get("session_token")
    if db is not None and api_key and user == "__api_key__":
        user = await _resolve_ws_api_key_subject(api_key) or user

    async def _can_access_widget_ref_page(source_page_id: str) -> bool:
        if db is None:
            return False
        source_access, source_defining_node_id = await _resolve_access_with_node(db, source_page_id)
        if source_access in ("public", "readonly"):
            return True
        if source_access == "protected":
            source_validate_id = source_defining_node_id or source_page_id
            return bool(session_token and sessions_api.validate_session(session_token, source_validate_id))
        if source_access == "user":
            if user is None or user == "__api_key__" or user.startswith("api_key:"):
                return False
            return await _check_user_access(db, source_page_id, user)
        return False

    async def _is_readonly_widget_ref_page(source_page_id: str) -> bool:
        if db is None:
            return False
        source_access, _source_defining_node_id = await _resolve_access_with_node(db, source_page_id)
        return source_access == "readonly"

    if user is None:
        if not page_id:
            await ws.close(code=4001, reason="Authentication required")
            return

        if db is None:
            await ws.close(code=4001, reason="Authentication required")
            return
        access, defining_node_id = await _resolve_access_with_node(db, page_id)
        if access == "protected":
            validate_id = defining_node_id or page_id
            if not session_token or not sessions_api.validate_session(session_token, validate_id):
                await ws.close(code=4001, reason="Valid session token required")
                return
        elif access == "user":
            await ws.close(code=4001, reason="Authentication required")
            return
        elif access not in ("public", "readonly"):
            await ws.close(code=4001, reason="Authentication required")
            return

        allowed_dp_ids = await _page_allowed_datapoints(
            db,
            page_id,
            widget_ref_access_check=_can_access_widget_ref_page,
        )
        if allowed_dp_ids is None:
            # Keep the connection authenticated for page-scope sessions even if
            # page config cannot be parsed (e.g. lightweight test doubles).
            allowed_dp_ids = set()
    if db is not None and page_id:
        ok, reason = await _authorize_visu_page_scope(
            db=db,
            page_id=page_id,
            session_token=session_token,
            username=user,
        )
        if not ok:
            await ws.close(code=4001, reason=reason)
            return
        allowed_message_archive_access = await _page_allowed_message_archive_predicates(
            db,
            page_id,
            widget_ref_access_check=_can_access_widget_ref_page,
            widget_ref_readonly_check=_is_readonly_widget_ref_page,
        )

    log_access = await _ws_has_log_access(user, api_key) if allowed_dp_ids is None else False

    manager = get_ws_manager()
    conn_id = await manager.connect(
        ws,
        allowed_dp_ids=allowed_dp_ids,
        allowed_message_archive_access=allowed_message_archive_access,
        log_access=log_access,
        log_access_check=(lambda: _ws_has_log_access(user, api_key)) if log_access else None,
        subprotocol=selected_subprotocol,
    )

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
                subscribed = [i for i in ids if i in after]
                await ws.send_json({"action": "subscribed", "ids": added})
                await manager.send_initial_values(conn_id, subscribed)

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
