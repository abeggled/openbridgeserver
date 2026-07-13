from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

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
        received: list[dict] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.scope = {"subprotocols": subprotocols or []}
        self.accepted = False
        self.accepted_subprotocol: str | None = None
        self.close_calls: list[tuple[int | None, str | None]] = []
        self.received = received or []
        self.sent: list[dict] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.accepted_subprotocol = subprotocol

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))

    async def receive_json(self) -> dict:
        if self.received:
            return self.received.pop(0)
        raise WebSocketDisconnect()

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


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


class _LogAccessDbStub:
    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.queries: list[str] = []

    async def fetchone(self, query: str, _params: tuple):
        self.queries.append(query)
        return self.row


class _ApiKeyOwnerDbStub:
    def __init__(self) -> None:
        self.updated = False

    async def fetchone(self, query: str, _params: tuple):
        if "SELECT name FROM api_keys" in query:
            return {"name": "automation-client"}
        if "SELECT id, owner FROM api_keys" in query:
            return {"id": "key-1", "owner": "alice"}
        if "FROM visu_nodes" in query:
            return {"type": "PAGE"}
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
async def test_websocket_endpoint_subscribe_sends_initial_registry_value(monkeypatch):
    dp_id = uuid4()
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    monkeypatch.setattr(ws_api, "get_db", lambda: _DbStub(has_key=True))

    class _RegistryStub:
        def get(self, dp_uuid):
            if dp_uuid == dp_id:
                return SimpleNamespace(unit="W")
            return None

        def get_value(self, dp_uuid):
            if dp_uuid == dp_id:
                return SimpleNamespace(
                    value=17.25,
                    quality="good",
                    ts=datetime(2026, 6, 8, 9, 10, 11, 456000, tzinfo=UTC),
                )
            return None

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub())

    ws = _FakeWebSocket(
        headers={"x-api-key": "obs_valid"},
        received=[{"action": "subscribe", "ids": [str(dp_id)]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.sent == [
        {"action": "subscribed", "ids": [str(dp_id)]},
        {
            "id": str(dp_id),
            "v": 17.25,
            "u": "W",
            "t": "2026-06-08T09:10:11.456Z",
            "q": "good",
        },
    ]


@pytest.mark.asyncio
async def test_websocket_endpoint_applies_page_archive_predicates_for_authenticated_socket(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    class _ManagerStub:
        def __init__(self) -> None:
            self.allowed_dp_ids = None
            self.allowed_message_archive_access = None

        async def connect(self, ws, *, allowed_dp_ids=None, allowed_message_archive_access=None, **_kwargs):
            self.allowed_dp_ids = allowed_dp_ids
            self.allowed_message_archive_access = allowed_message_archive_access
            await ws.accept()
            return "conn-1"

        async def disconnect(self, _conn_id: str) -> None:
            return None

    expected_access = [ws_api.MessageArchivePredicate(archive_ids={"system"})]
    predicate_calls: list[tuple[object, str]] = []

    async def _page_predicates(db, page_id: str, **_kwargs):
        predicate_calls.append((db, page_id))
        return expected_access

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    db = _DbStub(has_key=False, page_type="PAGE")
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_public_access)
    monkeypatch.setattr(ws_api, "_page_allowed_message_archive_predicates", _page_predicates)
    manager = _ManagerStub()
    monkeypatch.setattr(ws_api, "get_ws_manager", lambda: manager)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        query_params={"page_id": "page-public"},
    )
    await ws_api.websocket_endpoint(ws)

    assert ws.accepted is True
    assert predicate_calls == [(db, "page-public")]
    assert manager.allowed_dp_ids is None
    assert manager.allowed_message_archive_access is expected_access


@pytest.mark.asyncio
async def test_websocket_endpoint_rejects_authenticated_archive_scope_without_page_access(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    class _ManagerStub:
        async def connect(self, ws, **_kwargs):
            await ws.accept()
            return "conn-1"

        async def disconnect(self, _conn_id: str) -> None:
            return None

    async def _deny_user_access(_db, _node_id: str, _username: str) -> bool:
        return False

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    db = _DbStub(has_key=False, page_type="PAGE")
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_user_access)
    monkeypatch.setattr("obs.api.v1.visu._check_user_access", _deny_user_access)
    monkeypatch.setattr(ws_api, "get_ws_manager", lambda: _ManagerStub())

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        query_params={"page_id": "page-user"},
    )
    await ws_api.websocket_endpoint(ws)

    assert ws.accepted is False
    assert ws.close_calls == [(4001, "Zugriff verweigert")]


@pytest.mark.asyncio
async def test_websocket_endpoint_uses_api_key_owner_for_user_page_scope(monkeypatch):
    class _ManagerStub:
        async def connect(self, ws, **_kwargs):
            await ws.accept()
            return "conn-1"

        async def disconnect(self, _conn_id: str) -> None:
            return None

    access_checks: list[tuple[str, str]] = []

    async def _allow_owner_access(_db, node_id: str, username: str) -> bool:
        access_checks.append((node_id, username))
        return username == "alice"

    async def _page_predicates(_db, _page_id: str, **_kwargs):
        return [ws_api.MessageArchivePredicate(archive_ids={"system"})]

    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    db = _ApiKeyOwnerDbStub()
    monkeypatch.setattr(ws_api, "get_db", lambda: db)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_user_access)
    monkeypatch.setattr("obs.api.v1.visu._check_user_access", _allow_owner_access)
    monkeypatch.setattr(ws_api, "_page_allowed_message_archive_predicates", _page_predicates)
    monkeypatch.setattr(ws_api, "get_ws_manager", lambda: _ManagerStub())

    ws = _FakeWebSocket(headers={"x-api-key": "obs_valid"}, query_params={"page_id": "page-user"})
    await ws_api.websocket_endpoint(ws)

    assert ws.accepted is True
    assert ws.close_calls == []
    assert ("page-user", "alice") in access_checks


@pytest.mark.asyncio
async def test_ws_log_access_allows_authenticated_user_without_admin_lookup(monkeypatch):
    def fail_get_db():
        raise AssertionError("JWT log access should match REST read access without admin lookup")

    monkeypatch.setattr(ws_api, "get_db", fail_get_db)

    assert await ws_api._ws_has_log_access("regular-user", None) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_ws_log_access_revalidates_api_key_with_legacy_name_fallback(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    db = _LogAccessDbStub({"subject": "automation-client"})
    monkeypatch.setattr(ws_api, "get_db", lambda: db)

    assert await ws_api._ws_has_log_access("__api_key__", "obs_valid") is True  # noqa: SLF001
    assert "COALESCE(NULLIF(owner, ''), name)" in db.queries[0]


@pytest.mark.asyncio
async def test_ws_log_access_rejects_revoked_api_key(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    db = _LogAccessDbStub(None)
    monkeypatch.setattr(ws_api, "get_db", lambda: db)

    assert await ws_api._ws_has_log_access("__api_key__", "obs_revoked") is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_ws_log_access_revalidates_resolved_api_key_owner(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    db = _LogAccessDbStub(None)
    monkeypatch.setattr(ws_api, "get_db", lambda: db)

    assert await ws_api._ws_has_log_access("alice", "obs_revoked") is False  # noqa: SLF001
    assert db.queries


@pytest.mark.asyncio
async def test_ws_log_access_ignores_stray_api_key_for_jwt_identity(monkeypatch):
    def fail_get_db():
        raise AssertionError("A JWT-derived identity must not be revalidated against an unrelated api_key header")

    monkeypatch.setattr(ws_api, "get_db", fail_get_db)

    assert (
        await ws_api._ws_has_log_access("alice", "stray-invalid-key", identity_from_jwt=True) is True  # noqa: SLF001
    )


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


async def _resolve_user_access(_db, _node_id: str) -> tuple[str, str | None]:
    return "user", "node-user"
