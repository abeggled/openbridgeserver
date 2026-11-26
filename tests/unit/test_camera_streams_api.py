"""Kamera-Stream-API (#1479): Scope, HTTP-Routen und WebSocket-Relay zu go2rtc."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import websockets
from fastapi import HTTPException

from obs.adapters import registry as adapter_registry
from obs.adapters.go2rtc.adapter import Go2rtcAdapter
from obs.adapters.go2rtc_base import CameraInfo, StreamInfo, StreamSourceError
from obs.api.auth import create_access_token
from obs.api.v1 import camera_streams as api
from obs.db.database import Database

setup_api = importlib.import_module("obs.api.setup")

NOW = "2026-11-20T00:00:00+00:00"
INSTANCE = "11111111-1111-1111-1111-111111111111"
SRC = "cam_hof_sub"


class Bus:
    async def publish(self, event: Any) -> None:
        return None


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> Go2rtcAdapter:
    adapter = Go2rtcAdapter(Bus(), {"host": "127.0.0.1", "port": 1, "mse_mode": "proxy"})
    adapter._cameras = [CameraInfo("hof", (StreamInfo("Main", "cam_hof"), StreamInfo("Sub", SRC)))]
    adapter._cameras_at = 1e12
    monkeypatch.setitem(adapter_registry._instances, INSTANCE, adapter)
    return adapter


@pytest.fixture(autouse=True)
def no_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_api, "setup_required", lambda: False)


async def _insert_user(db: Database, username: str, *, is_admin: bool = False) -> None:
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, 'hash', ?, ?)",
        (f"user-{username}", username, NOW, int(is_admin)),
    )


def _widget(src: str = SRC, *, instance: str = INSTANCE, source: str = "go2rtc", mini: bool = False) -> dict[str, Any]:
    widget = {
        "id": "cam",
        "type": "Kamera",
        "config": {"source": source, "streamInstanceId": instance, "tileSrc": src, "fullscreenSrc": "cam_hof"},
    }
    if mini:
        return {"id": "grp", "type": "Gruppe", "config": {"miniWidgets": [widget]}}
    return widget


async def _insert_page(db: Database, widgets: list[dict[str, Any]], *, access: str = "public") -> None:
    page_config = json.dumps({"widgets": widgets})
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-cam', NULL, 'Cam', 'PAGE', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (access, page_config, NOW, NOW),
    )
    await db.execute_and_commit("INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('page-cam', ?)", (access,))


# ── page_config_contains_stream ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("page_config", "expected"),
    [
        ({"widgets": [_widget()]}, True),
        (json.dumps({"widgets": [_widget()]}), True),
        ({"widgets": [_widget(mini=True)]}, True),
        ({"widgets": [{k: v for k, v in _widget().items() if k != "type"} | {"widgetType": "camera"}]}, True),
        ({"widgets": [{"type": "Kamera", "config": {**_widget()["config"], "tileSrc": "x", "fullscreenSrc": SRC}}]}, True),
        ({"widgets": [_widget(src="other")]}, False),
        ({"widgets": [_widget(instance="other")]}, False),
        ({"widgets": [_widget(source="url")]}, False),
        ({"widgets": [{**_widget(), "type": "Text"}]}, False),
        ({"widgets": [{"type": "Kamera", "config": "x"}, "x"]}, False),
        ({"widgets": [{"type": "Gruppe", "config": {"miniWidgets": ["x", {"type": "Text", "config": {}}]}}]}, False),
        ({"widgets": "x"}, False),
        ("{kaputt", False),
        ([], False),
    ],
)
def test_page_config_contains_stream(page_config: Any, expected: bool) -> None:
    assert api.page_config_contains_stream(page_config, INSTANCE, SRC) is expected


# ── _ensure_stream_scope ───────────────────────────────────────────────────────


async def _scope(db: Database, source: Go2rtcAdapter, **kwargs: Any) -> None:
    params: dict[str, Any] = {"user": None, "page_id": "page-cam", "session_token": "", "editor_preview": False}
    params.update(kwargs)
    src = params.pop("src", SRC)
    await api._ensure_stream_scope(db, source, INSTANCE, src, **params)


async def test_scope_public_page_ok(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_page(db, [_widget()])
    await _scope(db, source)


async def test_scope_requires_page(db: Database, source: Go2rtcAdapter) -> None:
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source, page_id="  ")
    assert exc.value.status_code == 400


async def test_scope_widget_not_on_page(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_page(db, [_widget(src="other")])
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source)
    assert exc.value.status_code == 404


async def test_scope_user_page_requires_login(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_page(db, [_widget()], access="user")
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source)
    assert exc.value.status_code == 401


async def test_scope_editor_preview_admin_only(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_user(db, "admin", is_admin=True)
    await _insert_user(db, "alice")
    await _scope(db, source, user="admin", editor_preview=True, page_id="")
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source, user="alice", editor_preview=True)
    assert exc.value.status_code == 403


async def test_scope_src_must_belong_to_instance(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_user(db, "admin", is_admin=True)
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source, user="admin", editor_preview=True, src="exec:rm -rf /")
    assert exc.value.status_code == 404


async def test_scope_source_unreachable(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    await _insert_user(db, "admin", is_admin=True)

    async def boom(src: str) -> bool:
        raise StreamSourceError("down")

    monkeypatch.setattr(source, "owns_src", boom)
    with pytest.raises(HTTPException) as exc:
        await _scope(db, source, user="admin", editor_preview=True)
    assert exc.value.status_code == 502


def test_get_source_unknown_or_wrong_type(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc:
        api._get_source("nope")
    assert exc.value.status_code == 404
    monkeypatch.setitem(adapter_registry._instances, "other", object())
    with pytest.raises(HTTPException):
        api._get_source("other")


# ── HTTP-Routen ────────────────────────────────────────────────────────────────


async def test_list_cameras(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    await _insert_user(db, "admin", is_admin=True)

    async def fetch() -> list[CameraInfo]:
        return [CameraInfo("hof", (StreamInfo("Sub", SRC),))]

    monkeypatch.setattr(source, "_fetch_cameras", fetch)
    result = await api.list_cameras(INSTANCE, _user="admin", db=db)
    assert [c.model_dump() for c in result] == [{"name": "hof", "streams": [{"label": "Sub", "src": SRC}]}]

    async def down() -> list[CameraInfo]:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(source, "_fetch_cameras", down)
    with pytest.raises(HTTPException) as exc:
        await api.list_cameras(INSTANCE, _user="admin", db=db)
    assert exc.value.status_code == 502


async def test_list_cameras_requires_admin(db: Database, source: Go2rtcAdapter) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.list_cameras(INSTANCE, _user=None, db=db)
    assert exc.value.status_code == 401


async def test_snapshot(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    await _insert_page(db, [_widget()])

    async def fetch(src: str, height: int) -> tuple[bytes, str]:
        assert (src, height) == (SRC, 240)
        return b"JPEG", "image/jpeg"

    monkeypatch.setattr(source, "fetch_snapshot", fetch)
    resp = await api.snapshot(INSTANCE, src=SRC, h=240, page_id="page-cam", session_token="", editor_preview=False, _user=None, db=db)
    assert resp.body == b"JPEG"
    assert resp.media_type == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"

    async def fail(src: str, height: int) -> tuple[bytes, str]:
        raise StreamSourceError("nope")

    monkeypatch.setattr(source, "fetch_snapshot", fail)
    with pytest.raises(HTTPException) as exc:
        await api.snapshot(INSTANCE, src=SRC, h=240, page_id="page-cam", session_token="", editor_preview=False, _user=None, db=db)
    assert exc.value.status_code == 502


async def test_stream_info(db: Database, source: Go2rtcAdapter) -> None:
    await _insert_page(db, [_widget()])
    info = await api.stream_info(INSTANCE, src=SRC, page_id="page-cam", session_token="", editor_preview=False, _user=None, db=db)
    assert info.model_dump() == {"mse_mode": "proxy", "mse_direct_url": None}
    source._cfg = source.config_schema(host="cams.lan", mse_mode="direct")
    info = await api.stream_info(INSTANCE, src=SRC, page_id="page-cam", session_token="", editor_preview=False, _user=None, db=db)
    assert info.mse_direct_url == "ws://cams.lan:1984/api/ws?src=cam_hof_sub"


# ── WebSocket ──────────────────────────────────────────────────────────────────


class FakeClientWS:
    """Minimaler Ersatz für fastapi.WebSocket aus Sicht des Endpunkts."""

    def __init__(self, query: dict[str, str], subprotocols: list[str] | None = None) -> None:
        self.query_params = query
        self.scope = {"subprotocols": subprotocols or []}
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[Any] = []
        self.accepted = False
        self.subprotocol: str | None = None
        self.closed: tuple[int, str] | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.subprotocol = subprotocol

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    def push_text(self, text: str) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "text": text})

    def push_bytes(self, data: bytes) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "bytes": data})

    def push_disconnect(self) -> None:
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})


@contextlib.asynccontextmanager
async def fake_go2rtc() -> AsyncIterator[dict[str, Any]]:
    """Echter WebSocket-Server, der sich wie go2rtcs /api/ws verhält.

    Bewusst kein Fixture: der Server muss in der Event-Loop des Tests laufen.
    """
    state: dict[str, Any] = {"requests": [], "received": []}

    async def handler(conn: Any) -> None:
        state["requests"].append((conn.request.path, dict(conn.request.headers)))
        async for message in conn:
            state["received"].append(message)
            if json.loads(message).get("type") in ("webrtc/offer", "mse"):
                await conn.send(json.dumps({"type": "answer"}))
                await conn.send(b"\x00media")
                await conn.send(json.dumps({"type": "done"}))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        state["port"] = server.sockets[0].getsockname()[1]
        yield state


async def _run_ws(client: FakeClientWS, *, until: int) -> None:
    task = asyncio.create_task(api.stream_ws(client, INSTANCE))  # type: ignore[arg-type]
    for _ in range(500):
        if len(client.sent) >= until or task.done():
            break
        await asyncio.sleep(0.01)
    client.push_disconnect()
    await asyncio.wait_for(task, 5)


@pytest.mark.parametrize(("kind", "expected_binary"), [("webrtc", False), ("mse", True)])
async def test_ws_relays_through_real_upstream(
    db: Database,
    source: Go2rtcAdapter,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_binary: bool,
) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _insert_page(db, [_widget()])
    client = FakeClientWS({"kind": kind, "src": SRC, "page_id": "page-cam"})
    client.push_bytes(b"ignored")  # Binär vom Client wird nie weitergereicht
    client.push_text(json.dumps({"type": "webrtc/offer" if kind == "webrtc" else "mse", "value": "x"}))

    async with fake_go2rtc() as upstream:
        source._cfg = source.config_schema(host="127.0.0.1", port=upstream["port"], mse_mode="proxy", username="u", password="p")
        await _run_ws(client, until=3 if expected_binary else 2)

    assert client.accepted
    path, headers = upstream["requests"][0]
    assert path == f"/api/ws?src={SRC}"
    assert headers["authorization"] == "Basic dTpw"
    assert upstream["received"] == [json.dumps({"type": "webrtc/offer" if kind == "webrtc" else "mse", "value": "x"})]
    expected: list[Any] = [json.dumps({"type": "answer"})]
    if expected_binary:
        expected.append(b"\x00media")
    expected.append(json.dumps({"type": "done"}))
    assert client.sent == expected


async def test_ws_jwt_subprotocol_editor_preview(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _insert_user(db, "admin", is_admin=True)
    proto = f"obs.jwt.{create_access_token('admin')}"
    client = FakeClientWS({"kind": "webrtc", "src": SRC, "editor_preview": "1"}, [proto])
    client.push_text(json.dumps({"type": "webrtc/offer"}))
    async with fake_go2rtc() as upstream:
        source._cfg = source.config_schema(host="127.0.0.1", port=upstream["port"])
        await _run_ws(client, until=2)
    assert client.subprotocol == proto
    assert client.sent[0] == json.dumps({"type": "answer"})


async def _expect_reject(client: FakeClientWS, code: int) -> None:
    await api.stream_ws(client, INSTANCE)  # type: ignore[arg-type]
    assert client.accepted
    assert client.closed is not None and client.closed[0] == code


async def test_ws_rejects_setup_mode(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(setup_api, "setup_required", lambda: True)
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC, "page_id": "page-cam"}), api.CLOSE_FORBIDDEN)


async def test_ws_rejects_bad_kind_or_missing_src(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _expect_reject(FakeClientWS({"kind": "hls", "src": SRC}), 4400)
    await _expect_reject(FakeClientWS({"kind": "webrtc"}), 4400)


async def test_ws_invalid_token_without_page_is_unauthorized(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC}, ["obs.jwt.kaputt"]), api.CLOSE_UNAUTHORIZED)
    # Gültiges Token eines gelöschten Benutzers zählt genauso.
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC}, [f"obs.jwt.{create_access_token('ghost')}"]), api.CLOSE_UNAUTHORIZED)


async def test_ws_invalid_token_with_page_falls_back_to_page_scope(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _insert_page(db, [_widget()], access="user")
    # Anonym auf einer Benutzer-Seite → 401 aus dem Seiten-Scope, nicht aus dem Token.
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC, "page_id": "page-cam"}, ["obs.jwt.kaputt"]), api.CLOSE_UNAUTHORIZED)


async def test_ws_session_token_from_subprotocol_or_query(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    seen: list[str] = []

    async def fake_scope(*args: Any, session_token: str, **kwargs: Any) -> None:
        seen.append(session_token)
        raise HTTPException(status_code=404)

    monkeypatch.setattr(api, "_ensure_stream_scope", fake_scope)
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC, "page_id": "p"}, ["obs.session.abc"]), api.CLOSE_NOT_FOUND)
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC, "page_id": "p", "session_token": "q"}), api.CLOSE_NOT_FOUND)
    assert seen == ["abc", "q"]


async def test_ws_mse_needs_proxy_mode(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    source._cfg = source.config_schema(host="127.0.0.1", mse_mode="direct")
    await _insert_page(db, [_widget()])
    await _expect_reject(FakeClientWS({"kind": "mse", "src": SRC, "page_id": "page-cam"}), api.CLOSE_FORBIDDEN)


async def test_ws_upstream_unreachable(db: Database, source: Go2rtcAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_db", lambda: db)
    await _insert_page(db, [_widget()])

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("refused")

    monkeypatch.setattr(api.websockets, "connect", refuse)
    await _expect_reject(FakeClientWS({"kind": "webrtc", "src": SRC, "page_id": "page-cam"}), api.CLOSE_UPSTREAM_FAILED)


# ── relay (Einzelfälle) ────────────────────────────────────────────────────────


class FakeUpstream:
    def __init__(self, messages: list[Any] | None = None, *, fail: Exception | None = None) -> None:
        self.messages = messages or []
        self.fail = fail
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> FakeUpstream:
        return self

    async def __anext__(self) -> Any:
        if self.fail is not None:
            raise self.fail
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Event().wait()  # offen, bis abgebrochen
        raise StopAsyncIteration


async def test_relay_closes_on_oversized_client_message() -> None:
    client = FakeClientWS({})
    client.push_text("x" * (api.MAX_CLIENT_MESSAGE + 1))
    upstream = FakeUpstream()
    await asyncio.wait_for(api.relay(client, upstream, allow_binary=False), 5)  # type: ignore[arg-type]
    assert client.closed == (1009, "Message too big")
    assert upstream.sent == []


async def test_relay_ends_when_upstream_ends() -> None:
    client = FakeClientWS({})

    class Ending(FakeUpstream):
        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    await asyncio.wait_for(api.relay(client, Ending(), allow_binary=True), 5)  # type: ignore[arg-type]
    assert client.sent == []


async def test_relay_logs_unexpected_upstream_error(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClientWS({})
    await asyncio.wait_for(api.relay(client, FakeUpstream(fail=RuntimeError("kaputt")), allow_binary=True), 5)  # type: ignore[arg-type]
    assert "Kamera-Stream-Relay beendet: kaputt" in caplog.text


async def test_relay_does_not_log_normal_close(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClientWS({})
    closed = websockets.ConnectionClosedOK(None, None)
    await asyncio.wait_for(api.relay(client, FakeUpstream(fail=closed), allow_binary=True), 5)  # type: ignore[arg-type]
    assert "Relay beendet" not in caplog.text


# ── Passwort-Redaktion der Instanz-Konfiguration ───────────────────────────────


@pytest.mark.parametrize("adapter_type", ["GO2RTC", "FRIGATE"])
def test_stream_source_password_is_redacted_for_non_admins_and_restored(adapter_type: str) -> None:
    from obs.api.v1 import adapters as adapters_api
    from obs.api.v1.redaction import REDACTED

    config = {"host": "h", "username": "u", "password": "secret"}
    assert adapters_api._redact_instance_config(adapter_type, config)["password"] == REDACTED
    assert adapters_api._redact_instance_config(adapter_type, config, privileged=True) == config

    restored = adapters_api._restore_redacted_token(adapter_type, config, {**config, "password": REDACTED})
    assert restored["password"] == "secret"
    changed = adapters_api._restore_redacted_token(adapter_type, config, {**config, "password": "new"})
    assert changed["password"] == "new"
    assert adapters_api._restore_redacted_token(adapter_type, {}, {"password": REDACTED}) == {"password": ""}
