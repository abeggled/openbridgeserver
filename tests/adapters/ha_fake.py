"""In-memory Home Assistant fake at the protocol boundary (#1392).

``FakeHomeAssistant.connect`` replaces ``websockets.connect`` and speaks the HA
WebSocket protocol (auth, get_states, subscribe_entities, subscribe_events,
unsubscribe_events, ping, registry lists); ``FakeHomeAssistant.transport`` is an
``httpx.MockTransport`` for the REST API (states + action calls).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

_CLOSE = object()


class FakeConnectionClosed(Exception):
    pass


class FakeHaSocket:
    def __init__(self, server: FakeHomeAssistant) -> None:
        self._server = server
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.subscriptions: dict[int, dict[str, Any]] = {}
        self.authenticated = False
        self.closed = False
        self._queue.put_nowait({"type": "auth_required", "ha_version": server.ha_version})

    # websockets client surface -----------------------------------------
    async def recv(self) -> str:
        item = await self._queue.get()
        if item is _CLOSE:
            self.closed = True
            raise FakeConnectionClosed("closed")
        return item if isinstance(item, str) else json.dumps(item)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await self.recv()
        except FakeConnectionClosed:
            raise StopAsyncIteration from None

    async def send(self, data: str) -> None:
        msg = json.loads(data)
        self.sent.append(msg)
        await self._server._handle(self, msg)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(_CLOSE)

    # server side --------------------------------------------------------
    def push(self, msg: Any) -> None:
        if not self.closed:
            self._queue.put_nowait(msg)

    def drop(self) -> None:
        """Simulate HA going away."""
        self._queue.put_nowait(_CLOSE)


class FakeHomeAssistant:
    def __init__(
        self,
        states: dict[str, dict[str, Any]] | None = None,
        *,
        token: str = "secret-token",
        ha_version: str = "2025.10.1",
        subscribe_entities: bool = True,
        registries: bool = True,
    ) -> None:
        self.states: dict[str, dict[str, Any]] = {k: _norm(k, v) for k, v in (states or {}).items()}
        self.token = token
        self.ha_version = ha_version
        self.supports_subscribe_entities = subscribe_entities
        self.registries = registries
        self.answer_pings = True
        self.connect_errors: list[BaseException] = []
        self.sockets: list[FakeHaSocket] = []
        self.service_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.service_errors: dict[str, tuple[int, Any]] = {}
        self.subscribe_error: tuple[str, str] | None = None
        self.entity_registry: list[dict[str, Any]] = []
        self.device_registry: list[dict[str, Any]] = []
        self.area_registry: list[dict[str, Any]] = []
        self.services: Any = {}

    # websockets.connect replacement ------------------------------------
    async def connect(self, uri: str, **_kwargs: Any) -> FakeHaSocket:
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        sock = FakeHaSocket(self)
        self.sockets.append(sock)
        return sock

    @property
    def socket(self) -> FakeHaSocket:
        return self.sockets[-1]

    async def _handle(self, sock: FakeHaSocket, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "auth":
            if msg.get("access_token") == self.token:
                sock.authenticated = True
                sock.push({"type": "auth_ok", "ha_version": self.ha_version})
            else:
                sock.push({"type": "auth_invalid", "message": "Invalid access token"})
            return
        msg_id = msg.get("id")
        if kind == "ping":
            if self.answer_pings:
                sock.push({"id": msg_id, "type": "pong"})
            return
        if kind == "get_states":
            sock.push(_ok(msg_id, list(self.states.values())))
            return
        if kind == "subscribe_entities":
            if not self.supports_subscribe_entities:
                sock.push(_err(msg_id, "unknown_command", "Unknown command."))
                return
            if self.subscribe_error is not None:
                sock.push(_err(msg_id, *self.subscribe_error))
                return
            wanted = msg.get("entity_ids")
            sock.subscriptions[msg_id] = {"kind": "entities", "entity_ids": set(wanted) if wanted is not None else None}
            sock.push(_ok(msg_id, None))
            snapshot = {eid: _compressed(s) for eid, s in self.states.items() if wanted is None or eid in wanted}
            sock.push({"id": msg_id, "type": "event", "event": {"a": snapshot}})
            return
        if kind == "subscribe_events":
            sock.subscriptions[msg_id] = {"kind": "events", "event_type": msg.get("event_type")}
            sock.push(_ok(msg_id, None))
            return
        if kind == "unsubscribe_events":
            sock.subscriptions.pop(msg.get("subscription"), None)
            sock.push(_ok(msg_id, None))
            return
        if kind == "get_services":
            sock.push(_ok(msg_id, self.services))
            return
        registry = {
            "config/entity_registry/list": self.entity_registry,
            "config/device_registry/list": self.device_registry,
            "config/area_registry/list": self.area_registry,
        }
        if kind in registry:
            if self.registries:
                sock.push(_ok(msg_id, registry[kind]))
            else:
                sock.push(_err(msg_id, "unauthorized", "Unauthorized"))
            return
        sock.push(_err(msg_id, "unknown_command", "Unknown command."))

    # state changes ------------------------------------------------------
    def set_state(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        old = self.states.get(entity_id)
        new = _norm(entity_id, {"state": state, "attributes": attributes if attributes is not None else (old or {}).get("attributes", {})})
        self.states[entity_id] = new
        for sock in self.sockets:
            for sub_id, sub in sock.subscriptions.items():
                if sub["kind"] == "entities" and (sub["entity_ids"] is None or entity_id in sub["entity_ids"]):
                    if old is None:
                        event = {"a": {entity_id: _compressed(new)}}
                    else:
                        changed = {k: v for k, v in new["attributes"].items() if old["attributes"].get(k) != v}
                        removed = [k for k in old["attributes"] if k not in new["attributes"]]
                        diff: dict[str, Any] = {"+": {"s": state, "a": changed}}
                        if removed:
                            diff["-"] = {"a": removed}
                        event = {"c": {entity_id: diff}}
                    sock.push({"id": sub_id, "type": "event", "event": event})
                elif sub["kind"] == "events":
                    sock.push(_state_changed(sub_id, entity_id, old, new))

    def remove_entity(self, entity_id: str) -> None:
        old = self.states.pop(entity_id, None)
        for sock in self.sockets:
            for sub_id, sub in sock.subscriptions.items():
                if sub["kind"] == "entities" and (sub["entity_ids"] is None or entity_id in sub["entity_ids"]):
                    sock.push({"id": sub_id, "type": "event", "event": {"r": [entity_id]}})
                elif sub["kind"] == "events":
                    sock.push(_state_changed(sub_id, entity_id, old, None))

    # REST ---------------------------------------------------------------
    def _rest(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Unauthorized"})
        path = request.url.path
        if request.method == "GET" and path.startswith("/api/states/"):
            entity_id = path.removeprefix("/api/states/")
            state = self.states.get(entity_id)
            return httpx.Response(200, json=state) if state else httpx.Response(404, json={"message": "Entity not found."})
        if request.method == "POST" and path.startswith("/api/services/"):
            domain, service = path.removeprefix("/api/services/").split("/", 1)
            body = json.loads(request.content or b"{}")
            error = self.service_errors.get(f"{domain}.{service}")
            if error is not None:
                return httpx.Response(error[0], json=error[1]) if not isinstance(error[1], str) else httpx.Response(error[0], text=error[1])
            self.service_calls.append((domain, service, body))
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._rest)

    def http_client_factory(self):
        """Drop-in replacement for ``httpx.AsyncClient`` that routes to this fake."""

        client_cls = httpx.AsyncClient  # captured before a test patches it

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return client_cls(transport=self.transport, **kwargs)

        return factory


def _norm(entity_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state.get("state"), "attributes": dict(state.get("attributes") or {})}


def _compressed(state: dict[str, Any]) -> dict[str, Any]:
    return {"s": state["state"], "a": dict(state["attributes"]), "lc": 1.0}


def _ok(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"id": msg_id, "type": "result", "success": True, "result": result}


def _err(msg_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"id": msg_id, "type": "result", "success": False, "error": {"code": code, "message": message}}


def _state_changed(sub_id: int, entity_id: str, old: dict | None, new: dict | None) -> dict[str, Any]:
    return {
        "id": sub_id,
        "type": "event",
        "event": {"event_type": "state_changed", "data": {"entity_id": entity_id, "old_state": old, "new_state": new}},
    }


async def settle(rounds: int = 20) -> None:
    """Let the reader/supervisor tasks process everything queued so far."""
    for _ in range(rounds):
        await asyncio.sleep(0)
