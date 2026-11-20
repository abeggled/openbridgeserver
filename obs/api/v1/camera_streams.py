"""Kamera-Streams über go2rtc / Frigate (#1479).

GET       /api/v1/camera/streams/{instance_id}/cameras      Kamera-/Streamliste (Widget-Editor, nur Admin)
GET       /api/v1/camera/streams/{instance_id}/snapshot     JPEG eines Streams (Platzhalter im Widget)
GET       /api/v1/camera/streams/{instance_id}/stream-info  MSE-Modus und ggf. direkte MSE-URL
WEBSOCKET /api/v1/camera/streams/{instance_id}/ws           Relay zum go2rtc-WebSocket

Videodaten laufen hier nur im Ausnahmefall durch: bei ``kind=webrtc`` reicht das Relay
ausschließlich die Signalisierung (Offer/Answer/ICE-Kandidaten, Text-Frames) weiter, die
Medien gehen direkt von go2rtc zum Browser. ``kind=mse`` ist nur erlaubt, wenn die Instanz
``mse_mode = "proxy"`` gesetzt hat — dann reicht OBS auch die Binär-Frames durch.

Zugriff: wie beim Kamera-Proxy (``camera.py``) braucht ein Visu-Widget den Seiten-Scope
(``page_id``, ggf. PIN-Session), und die Seite muss ein Kamera-Widget enthalten, das genau
diese Instanz und genau diesen Stream zeigt. Der Widget-Editor (Admin) arbeitet mit
``editor_preview=1``. Ein ``src`` wird außerdem nur angenommen, wenn er in der Streamliste
der Instanz steht — go2rtc kennt auch Quellen wie ``exec:``, die ein Client nie nennen darf.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Literal

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, status
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from obs.adapters import registry as adapter_registry
from obs.adapters.go2rtc_base import StreamSourceAdapter, StreamSourceError
from obs.api.auth import decode_token
from obs.api.v1.camera import _camera_auth, _ensure_camera_editor_preview_access, _ensure_camera_page_access
from obs.db.database import Database, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["camera"])

# Eine SDP-Offer ist wenige kB groß; mehr schickt ein Player nie an go2rtc.
MAX_CLIENT_MESSAGE = 64 * 1024
UPSTREAM_OPEN_TIMEOUT = 10.0
SNAPSHOT_MAX_HEIGHT = 2160

# Close-Codes des Relays (4xxx = anwendungsspezifisch).
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_UPSTREAM_FAILED = 4502
_HTTP_TO_CLOSE = {
    status.HTTP_401_UNAUTHORIZED: CLOSE_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN: CLOSE_FORBIDDEN,
    status.HTTP_404_NOT_FOUND: CLOSE_NOT_FOUND,
}


class StreamOut(BaseModel):
    label: str
    src: str


class CameraOut(BaseModel):
    name: str
    streams: list[StreamOut]


class StreamInfoOut(BaseModel):
    mse_mode: Literal["direct", "proxy"]
    mse_direct_url: str | None


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────


def _get_source(instance_id: str) -> StreamSourceAdapter:
    instance = adapter_registry.get_instance_by_id(instance_id)
    if not isinstance(instance, StreamSourceAdapter):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kamera-Server nicht gefunden")
    return instance


def _is_camera_widget(widget: dict[str, Any]) -> bool:
    return str(widget.get("type", widget.get("widgetType")) or "").lower() in {"kamera", "camera"}


def page_config_contains_stream(page_config: Any, instance_id: str, src: str) -> bool:
    """Enthält die Seite ein Kamera-Widget, das *src* der Instanz *instance_id* zeigt?"""
    if isinstance(page_config, str):
        try:
            page_config = json.loads(page_config)
        except json.JSONDecodeError:
            return False
    if not isinstance(page_config, dict):
        return False
    widgets = page_config.get("widgets")
    if not isinstance(widgets, list):
        return False

    def _matches(widget: dict[str, Any]) -> bool:
        config = widget.get("config")
        if not isinstance(config, dict):
            return False
        if (
            _is_camera_widget(widget)
            and config.get("source") == "go2rtc"
            and config.get("streamInstanceId") == instance_id
            and src in (config.get("tileSrc"), config.get("fullscreenSrc"))
        ):
            return True
        mini_widgets = config.get("miniWidgets")
        return isinstance(mini_widgets, list) and any(isinstance(mini, dict) and _matches(mini) for mini in mini_widgets)

    return any(isinstance(widget, dict) and _matches(widget) for widget in widgets)


async def _ensure_stream_scope(
    db: Database,
    source: StreamSourceAdapter,
    instance_id: str,
    src: str,
    *,
    user: str | None,
    page_id: str,
    session_token: str,
    editor_preview: bool,
) -> None:
    """Darf der Aufrufer *src* sehen? Wirft HTTPException mit 400/401/403/404."""
    if editor_preview:
        await _ensure_camera_editor_preview_access(db, user)
    elif not page_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kamera-Page-Scope erforderlich")
    else:
        page_config = await _ensure_camera_page_access(db, page_id.strip(), user, session_token)
        if not page_config_contains_stream(page_config, instance_id, src):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kamera nicht gefunden")
    try:
        owned = await source.owns_src(src)
    except (httpx.HTTPError, StreamSourceError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Kamera-Server nicht erreichbar") from exc
    if not owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream nicht gefunden")


# ── HTTP-Endpunkte ─────────────────────────────────────────────────────────────


@router.get("/{instance_id}/cameras", response_model=list[CameraOut])
async def list_cameras(
    instance_id: str,
    _user: str | None = Depends(_camera_auth),
    db: Database = Depends(get_db),
) -> list[CameraOut]:
    await _ensure_camera_editor_preview_access(db, _user)
    source = _get_source(instance_id)
    try:
        cameras = await source.list_cameras(force=True)
    except (httpx.HTTPError, StreamSourceError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Kamera-Server nicht erreichbar") from exc
    return [CameraOut(name=c.name, streams=[StreamOut(label=s.label, src=s.src) for s in c.streams]) for c in cameras]


@router.get("/{instance_id}/snapshot")
async def snapshot(
    instance_id: str,
    src: str = Query(...),
    h: int = Query(360, ge=1, le=SNAPSHOT_MAX_HEIGHT),
    page_id: str = Query(""),
    session_token: str = Query(""),
    editor_preview: bool = False,
    _user: str | None = Depends(_camera_auth),
    db: Database = Depends(get_db),
) -> Response:
    source = _get_source(instance_id)
    await _ensure_stream_scope(db, source, instance_id, src, user=_user, page_id=page_id, session_token=session_token, editor_preview=editor_preview)
    try:
        data, content_type = await source.fetch_snapshot(src, h)
    except (httpx.HTTPError, StreamSourceError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Snapshot nicht verfügbar") from exc
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})


@router.get("/{instance_id}/stream-info", response_model=StreamInfoOut)
async def stream_info(
    instance_id: str,
    src: str = Query(...),
    page_id: str = Query(""),
    session_token: str = Query(""),
    editor_preview: bool = False,
    _user: str | None = Depends(_camera_auth),
    db: Database = Depends(get_db),
) -> StreamInfoOut:
    source = _get_source(instance_id)
    await _ensure_stream_scope(db, source, instance_id, src, user=_user, page_id=page_id, session_token=session_token, editor_preview=editor_preview)
    return StreamInfoOut(mse_mode=source.cfg.mse_mode, mse_direct_url=source.mse_direct_url(src))


# ── WebSocket-Relay ────────────────────────────────────────────────────────────


async def _ws_user(ws: WebSocket, db: Database) -> tuple[str | None, str, str | None]:
    """(Benutzer, PIN-Session, gewähltes Subprotokoll) aus dem Handshake."""
    from obs.api.v1.websocket import _extract_subprotocol_tokens

    jwt_token, session_sub, selected = _extract_subprotocol_tokens(ws)
    session_token = session_sub or ws.query_params.get("session_token", "")
    user: str | None = None
    if jwt_token:
        try:
            name = decode_token(jwt_token)
        except HTTPException:
            name = None
        if name and await db.fetchone("SELECT 1 FROM users WHERE username=?", (name,)):
            user = name
        elif not ws.query_params.get("page_id"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user, session_token, selected


async def _reject(ws: WebSocket, selected: str | None, code: int, reason: str) -> None:
    await ws.accept(subprotocol=selected)
    await ws.close(code=code, reason=reason)


async def _pump_client_to_upstream(ws: WebSocket, upstream: Any) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None:
            continue  # Player schicken nur Text; Binär-Frames vom Client werden verworfen.
        if len(text) > MAX_CLIENT_MESSAGE:
            await ws.close(code=1009, reason="Message too big")
            return
        await upstream.send(text)


async def _pump_upstream_to_client(ws: WebSocket, upstream: Any, *, allow_binary: bool) -> None:
    async for data in upstream:
        if isinstance(data, str):
            await ws.send_text(data)
        elif allow_binary:
            await ws.send_bytes(data)


async def relay(ws: WebSocket, upstream: Any, *, allow_binary: bool) -> None:
    """Text in beide Richtungen; Binär nur von go2rtc zum Browser und nur wenn erlaubt."""
    tasks = {
        asyncio.create_task(_pump_client_to_upstream(ws, upstream)),
        asyncio.create_task(_pump_upstream_to_client(ws, upstream, allow_binary=allow_binary)),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, (WebSocketDisconnect, websockets.ConnectionClosed)):
            logger.warning("Kamera-Stream-Relay beendet: %s", exc)


@router.websocket("/{instance_id}/ws")
async def stream_ws(ws: WebSocket, instance_id: str) -> None:
    from obs.api.setup import setup_required

    db = get_db()
    selected: str | None = None
    kind = ws.query_params.get("kind", "")
    src = ws.query_params.get("src", "")
    try:
        if setup_required():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Setup required")
        user, session_token, selected = await _ws_user(ws, db)
        if kind not in ("webrtc", "mse") or not src:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="kind/src fehlt")
        source = _get_source(instance_id)
        await _ensure_stream_scope(
            db,
            source,
            instance_id,
            src,
            user=user,
            page_id=ws.query_params.get("page_id", ""),
            session_token=session_token,
            editor_preview=ws.query_params.get("editor_preview") in ("1", "true"),
        )
        if kind == "mse" and source.cfg.mse_mode != "proxy":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MSE über OBS ist für diese Instanz nicht freigegeben")
    except HTTPException as exc:
        await _reject(ws, selected, _HTTP_TO_CLOSE.get(exc.status_code, 4400), str(exc.detail))
        return

    try:
        upstream = await websockets.connect(
            source.ws_url(kind, src),  # type: ignore[arg-type]
            additional_headers=await source.auth_headers(),
            ssl=source.ssl_context(),
            open_timeout=UPSTREAM_OPEN_TIMEOUT,
            max_size=None,
        )
    except Exception as exc:  # noqa: BLE001 — jeder Verbindungsfehler endet gleich
        logger.warning("Kamera-Server %s: WebSocket zu %s fehlgeschlagen: %s", instance_id, src, exc)
        await _reject(ws, selected, CLOSE_UPSTREAM_FAILED, "Kamera-Server nicht erreichbar")
        return

    try:
        await ws.accept(subprotocol=selected)
        await relay(ws, upstream, allow_binary=kind == "mse")
    finally:
        await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close()
