from __future__ import annotations

import pytest
from fastapi import HTTPException, WebSocketDisconnect

import obs.api.auth as auth_api
from obs.api.v1 import websocket as ws_api


class _FakeWebSocket:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.scope = {"subprotocols": subprotocols or []}
        self.accepted = False
        self.accepted_subprotocol: str | None = None
        self.close_calls: list[tuple[int | None, str | None]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.accepted_subprotocol = subprotocol

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))

    async def receive_json(self) -> dict:
        raise WebSocketDisconnect()

    async def send_json(self, _msg: dict) -> None:
        return None


class _DbStub:
    def __init__(self, has_key: bool, page_type: str | None = None) -> None:
        self.has_key = has_key
        self.page_type = page_type
        self.updated = False

    async def fetchone(self, query: str, _params: tuple):
        if "FROM api_keys" in query and self.has_key:
            return {"name": "automation-client"}
        if "FROM visu_nodes" in query and self.page_type:
            return {"type": self.page_type}
        return None

    async def execute_and_commit(self, _query: str, _params: tuple) -> None:
        self.updated = True


@pytest.mark.asyncio
async def test_authenticate_ws_rejects_missing_credentials():
    ws = _FakeWebSocket()
    ok, reason = await ws_api._authenticate_ws_request(ws)  # noqa: SLF001
    assert ok is False
    assert reason == "Missing credentials"


@pytest.mark.asyncio
async def test_websocket_endpoint_rejects_query_token_without_supported_auth():
    ws = _FakeWebSocket(query_params={"token": "legacy-query-token"})
    await ws_api.websocket_endpoint(ws)
    assert ws.accepted is True
    assert ws.close_calls == [(4001, "Missing credentials")]


@pytest.mark.asyncio
async def test_websocket_endpoint_closes_invalid_subprotocol_token_with_4001(monkeypatch):
    def _decode_token(_token: str, expected_type: str = "access") -> str:
        raise HTTPException(401, f"Wrong token type: {expected_type}")

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)

    ws = _FakeWebSocket(subprotocols=["obs.jwt.invalid.jwt.token"])
    await ws_api.websocket_endpoint(ws)
    assert ws.accepted is True
    assert ws.accepted_subprotocol == "obs.jwt.invalid.jwt.token"
    assert ws.close_calls == [(4001, "Invalid token")]


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_subprotocol_jwt(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "admin"
        raise HTTPException(401, "invalid")

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)

    ws = _FakeWebSocket(subprotocols=["obs.jwt.valid.jwt.token"])
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.accepted_subprotocol == "obs.jwt.valid.jwt.token"


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_api_key(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    db = _DbStub(has_key=True)
    monkeypatch.setattr(ws_api, "get_db", lambda: db)

    ws = _FakeWebSocket(headers={"x-api-key": "obs_valid"})
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert db.updated is True


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_public_visu_page_scope(monkeypatch):
    db = _DbStub(has_key=False, page_type="PAGE")
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_public_access)

    ws = _FakeWebSocket(query_params={"page_id": "page-public"})
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.close_calls == [(None, None)]


@pytest.mark.asyncio
async def test_websocket_endpoint_rejects_protected_visu_page_without_valid_session(monkeypatch):
    db = _DbStub(has_key=False, page_type="PAGE")
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_protected_access)
    monkeypatch.setattr("obs.api.v1.sessions.validate_session", lambda _token, _node_id: False)

    ws = _FakeWebSocket(query_params={"page_id": "page-protected"})
    await ws_api.websocket_endpoint(ws)

    assert ws.accepted is True
    assert ws.close_calls == [(4001, "Valid session token required")]


@pytest.mark.asyncio
async def test_websocket_endpoint_accepts_protected_visu_page_with_valid_session(monkeypatch):
    db = _DbStub(has_key=False, page_type="PAGE")
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_protected_access)
    monkeypatch.setattr("obs.api.v1.sessions.validate_session", lambda token, node_id: token == "ok" and node_id == "node-protected")

    ws = _FakeWebSocket(query_params={"page_id": "page-protected", "session_token": "ok"})
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.close_calls == [(None, None)]


async def _resolve_public_access(_db, _node_id: str) -> tuple[str, str | None]:
    return "public", None


async def _resolve_protected_access(_db, _node_id: str) -> tuple[str, str | None]:
    return "protected", "node-protected"
