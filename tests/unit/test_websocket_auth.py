from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
    def __init__(self, has_key: bool, page_type: str | None = None, datapoint_ids: list[str] | None = None) -> None:
        self.has_key = has_key
        self.page_type = page_type
        self.datapoint_ids = datapoint_ids or []
        self.updated = False

    async def fetchone(self, query: str, _params: tuple):
        if "FROM api_keys" in query and self.has_key:
            if "SELECT id, owner" in query:
                return {"id": "key-1", "owner": None}
            return {"name": "automation-client"}
        if "FROM visu_nodes" in query and self.page_type:
            return {"type": self.page_type}
        return None

    async def fetchall(self, query: str, _params: tuple = ()):
        if "FROM datapoints" in query:
            return [{"id": dp_id} for dp_id in self.datapoint_ids]
        return []

    async def execute_and_commit(self, _query: str, _params: tuple) -> None:
        self.updated = True


class _LogAccessDbStub:
    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.queries: list[str] = []

    async def fetchone(self, query: str, _params: tuple):
        self.queries.append(query)
        return self.row


class _JwtScopeDbStub:
    def __init__(self, *, is_admin: bool = False) -> None:
        self.is_admin = is_admin

    async def fetchone(self, query: str, _params: tuple):
        if "FROM users" in query:
            return {"is_admin": int(self.is_admin)}
        return None

    async def fetchall(self, query: str, _params: tuple = ()):
        if "FROM datapoints" in query:
            return [{"id": "allowed-dp"}, {"id": "blocked-dp"}]
        return []


class _ApiKeyScopeDbStub:
    async def fetchone(self, query: str, _params: tuple):
        if "FROM api_keys" in query:
            if "SELECT id, owner" in query:
                return {"id": "key-1", "owner": None}
            return {"name": "automation-client"}
        return None

    async def fetchall(self, query: str, _params: tuple = ()):
        if "FROM datapoints" in query:
            return [{"id": "allowed-dp"}, {"id": "blocked-dp"}]
        return []

    async def execute_and_commit(self, _query: str, _params: tuple) -> None:
        pass


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
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=True))

    ws = _FakeWebSocket(subprotocols=["obs.jwt.valid.jwt.token"])
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.accepted_subprotocol == "obs.jwt.valid.jwt.token"


@pytest.mark.asyncio
async def test_websocket_endpoint_filters_jwt_subscriptions_by_datapoint_authz(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    async def _filter_authorized_datapoints(_db, principal, ids, *, action):
        assert principal.subject == "alice"
        assert action is ws_api.AuthzAction.READ
        assert ids == ["allowed-dp", "blocked-dp"]
        return ["allowed-dp"]

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=False))
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        received=[{"action": "subscribe", "ids": ["allowed-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["allowed-dp"]}]


@pytest.mark.asyncio
async def test_websocket_endpoint_adds_page_scope_for_authenticated_page_context(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    async def _filter_authorized_datapoints(_db, _principal, _ids, *, action):
        return []

    page_allowed = AsyncMock(return_value={"page-dp"})
    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=False))
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_page_allowed_datapoints", page_allowed)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_public_access)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        query_params={"page_id": "page-public"},
        received=[{"action": "subscribe", "ids": ["page-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["page-dp"]}]
    page_allowed.assert_awaited_once()


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
async def test_websocket_endpoint_filters_api_key_subscriptions_by_datapoint_authz(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")

    async def _filter_authorized_datapoints(_db, principal, ids, *, action):
        assert principal.type == "api_key"
        assert principal.subject == "api_key:key-1"
        assert action is ws_api.AuthzAction.READ
        assert ids == ["allowed-dp", "blocked-dp"]
        return ["allowed-dp"]

    monkeypatch.setattr(ws_api, "get_db", lambda: _ApiKeyScopeDbStub())
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)

    ws = _FakeWebSocket(
        headers={"x-api-key": "obs_valid"},
        received=[{"action": "subscribe", "ids": ["allowed-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["allowed-dp"]}]


@pytest.mark.asyncio
async def test_websocket_endpoint_adds_page_scope_for_api_key_page_context(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")

    async def _filter_authorized_datapoints(_db, _principal, _ids, *, action):
        assert action is ws_api.AuthzAction.READ
        return []

    page_allowed = AsyncMock(return_value={"page-dp"})
    monkeypatch.setattr(ws_api, "get_db", lambda: _ApiKeyScopeDbStub())
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_page_allowed_datapoints", page_allowed)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_public_access)

    ws = _FakeWebSocket(
        headers={"x-api-key": "obs_valid"},
        query_params={"page_id": "page-public"},
        received=[{"action": "subscribe", "ids": ["page-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["page-dp"]}]
    page_allowed.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_endpoint_api_key_page_scope_honors_explicit_deny(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    allowed_dp = str(uuid4())
    denied_dp = str(uuid4())

    async def _filter_authorized_datapoints(_db, _principal, _ids, *, action):
        assert action is ws_api.AuthzAction.READ
        return []

    async def _has_explicit_deny(_db, _principal, dp_id):
        return str(dp_id) == denied_dp

    page_allowed = AsyncMock(return_value={allowed_dp, denied_dp})
    monkeypatch.setattr(ws_api, "get_db", lambda: _ApiKeyScopeDbStub())
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_page_allowed_datapoints", page_allowed)
    monkeypatch.setattr("obs.api.v1.datapoints._has_explicit_datapoint_read_deny", _has_explicit_deny)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_public_access)

    ws = _FakeWebSocket(
        headers={"x-api-key": "obs_valid"},
        query_params={"page_id": "page-public"},
        received=[{"action": "subscribe", "ids": [allowed_dp, denied_dp]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": [allowed_dp]}]


@pytest.mark.asyncio
async def test_websocket_endpoint_disables_api_key_log_access_with_datapoint_scope(monkeypatch):
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    monkeypatch.setattr(ws_api, "get_db", lambda: _ApiKeyScopeDbStub())

    async def _filter_authorized_datapoints(_db, _principal, _ids, *, action):
        return ["allowed-dp"]

    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_ws_has_log_access", AsyncMock(return_value=True))

    manager = ws_api.init_ws_manager()
    captured: dict = {}
    original_connect = manager.connect

    async def _capture_connect(*args, **kwargs):
        captured.update(kwargs)
        return await original_connect(*args, **kwargs)

    monkeypatch.setattr(manager, "connect", _capture_connect)
    try:
        await ws_api.websocket_endpoint(_FakeWebSocket(headers={"x-api-key": "obs_valid"}))
    finally:
        ws_api.reset_ws_manager()

    assert captured["allowed_dp_ids"] == {"allowed-dp"}
    assert captured["log_access"] is False


@pytest.mark.asyncio
async def test_websocket_endpoint_prefers_decoded_jwt_when_api_key_header_is_also_present(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    async def _filter_authorized_datapoints(_db, principal, ids, *, action):
        assert principal.type == "user"
        assert principal.subject == "alice"
        return ["allowed-dp"]

    async def _unexpected_api_key_principal(*args, **kwargs):
        raise AssertionError("valid JWT should take precedence over API key scope")

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=False))
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "get_current_principal", _unexpected_api_key_principal)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token", "x-api-key": "obs_stale"},
        received=[{"action": "subscribe", "ids": ["allowed-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["allowed-dp"]}]


@pytest.mark.asyncio
async def test_websocket_endpoint_filters_user_page_scope_by_datapoint_read_policy(monkeypatch):
    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    async def _filter_authorized_datapoints(_db, _principal, ids, *, action):
        return ["allowed-dp"] if "allowed-dp" in ids else []

    async def _resolve_user_access(_db, _node_id: str) -> tuple[str, str | None]:
        return "user", "page-user"

    async def _check_user_access(_db, _node_id: str, username: str) -> bool:
        return username == "alice"

    page_allowed = AsyncMock(return_value={"allowed-dp", "blocked-dp"})
    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=False))
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_page_allowed_datapoints", page_allowed)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_user_access)
    monkeypatch.setattr("obs.api.v1.visu._check_user_access", _check_user_access)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        query_params={"page_id": "page-user"},
        received=[{"action": "subscribe", "ids": ["allowed-dp", "blocked-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    assert ws.sent == [{"action": "subscribed", "ids": ["allowed-dp"]}]


@pytest.mark.asyncio
async def test_websocket_widget_ref_user_source_page_requires_read_grants(monkeypatch):
    """Widget_ref on a protected page pointing to a user-access source page must enforce
    READ grants for the source page's datapoints (not just page-level user access)."""

    def _decode_token(token: str, expected_type: str = "access") -> str:
        if token == "valid.jwt.token" and expected_type == "access":
            return "alice"
        raise HTTPException(401, "invalid")

    async def _filter_authorized_datapoints(_db, _principal, ids, *, action):
        # Only "src-allowed-dp" passes READ grants; "src-denied-dp" does not
        return [dp for dp in ids if dp == "src-allowed-dp"]

    async def _resolve_access(db, node_id: str) -> tuple[str, str | None]:
        if node_id == "main-page":
            return "protected", None
        if node_id == "src-user-page":
            return "user", "src-user-page"
        return "protected", None

    async def _check_user_access_stub(_db, _node_id: str, username: str) -> bool:
        return username == "alice"

    async def _page_allowed(db, page_id: str, *, widget_ref_access_check=None):
        if page_id == "main-page":
            # main page has its own dp plus widget_ref datapoints from src-user-page
            if widget_ref_access_check is not None:
                # simulate widget_ref access check for the source page
                if await widget_ref_access_check("src-user-page"):
                    return {"main-dp", "src-allowed-dp", "src-denied-dp"}
            return {"main-dp"}
        if page_id == "src-user-page":
            return {"src-allowed-dp", "src-denied-dp"}
        return None

    monkeypatch.setattr(auth_api, "decode_token", _decode_token)
    monkeypatch.setattr(ws_api, "get_db", lambda: _JwtScopeDbStub(is_admin=False))
    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)
    monkeypatch.setattr(ws_api, "_page_allowed_datapoints", _page_allowed)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", _resolve_access)
    monkeypatch.setattr("obs.api.v1.visu._check_user_access", _check_user_access_stub)

    ws = _FakeWebSocket(
        headers={"authorization": "Bearer valid.jwt.token"},
        query_params={"page_id": "main-page"},
        received=[{"action": "subscribe", "ids": ["main-dp", "src-allowed-dp", "src-denied-dp"]}],
    )
    ws_api.init_ws_manager()
    try:
        await ws_api.websocket_endpoint(ws)
    finally:
        ws_api.reset_ws_manager()

    assert ws.accepted is True
    subscribed = next(m for m in ws.sent if m["action"] == "subscribed")
    subscribed_ids = set(subscribed["ids"])
    # Datapoints from user-access source page must be filtered by READ grants
    assert "src-denied-dp" not in subscribed_ids
    assert "src-allowed-dp" in subscribed_ids
    # Datapoints from the protected main page itself are not READ-grant-filtered
    assert "main-dp" in subscribed_ids


@pytest.mark.asyncio
async def test_websocket_endpoint_subscribe_sends_initial_registry_value(monkeypatch):
    dp_id = uuid4()
    monkeypatch.setattr(auth_api, "hash_api_key", lambda key: f"hash:{key}")
    monkeypatch.setattr(ws_api, "get_db", lambda: _DbStub(has_key=True, datapoint_ids=[str(dp_id)]))

    async def _filter_authorized_datapoints(_db, _principal, ids, *, action):
        return ids

    monkeypatch.setattr(ws_api, "filter_authorized_datapoints", _filter_authorized_datapoints)

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
