"""Kamera-Proxy — leitet Kamera-Streams vom Backend weiter.

GET /api/v1/camera/proxy   Proxyt einen HTTP-Stream zur Kamera

SSRF-Schutz:
  - Nur HTTP/HTTPS-Schemas erlaubt
  - Hostname wird per DNS aufgelöst und zentral gegen öffentliche Ziele bzw.
    die operatorgepflegte URL-Target-Allowlist geprüft
  - follow_redirects=False im Stream-Client verhindert Redirect-basiertes SSRF
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from obs.api.auth import decode_token
from obs.api.v1.sessions import validate_session
from obs.db.database import Database, get_db
from obs.security.url_targets import UrlTargetBlockedError, build_pinned_url_targets

router = APIRouter(tags=["camera"])


async def _build_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        return await asyncio.to_thread(build_pinned_url_targets, url)
    except UrlTargetBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.decision.api_detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Authentifizierung ──────────────────────────────────────────────────────────


async def _camera_auth(
    request: Request,
    _token: str = Query("", alias="_token", description="JWT als Query-Parameter"),
) -> str | None:
    """Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
    oder als URL-Query-Parameter '?_token=…' (nötig für <img>/<video>-Tags).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return decode_token(auth_header[7:])
    if _token:
        try:
            return decode_token(_token)
        except HTTPException:
            if request.query_params.get("page_id"):
                return None
            raise
    return None


def _page_config_contains_camera_url(page_config: Any, url: str, username: str = "", password: str = "") -> bool:
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

    expected_url = url.strip()
    expected_username = username.strip()
    expected_password = password

    def _normalize_camera_auth_type(value: Any) -> str:
        raw = str(value or "none").strip()
        if raw in {"basic", "apikey", "none"}:
            return raw
        lower = raw.lower()
        if lower.startswith("basic auth"):
            return "basic"
        if lower.startswith("api-key") or lower.startswith("apikey"):
            return "apikey"
        return "none"

    def _camera_target(config: dict[str, Any]) -> str:
        target = str(config.get("url", "")).strip()
        if _normalize_camera_auth_type(config.get("authType")) == "apikey":
            api_key_param = str(config.get("apiKeyParam", "")).strip()
            api_key_value = str(config.get("apiKeyValue", "")).strip()
            if api_key_param and api_key_value:
                sep = "&" if "?" in target else "?"
                target = f"{target}{sep}{api_key_param}={api_key_value}"
        return target

    def _camera_credentials_match(config: dict[str, Any]) -> bool:
        auth_type = _normalize_camera_auth_type(config.get("authType"))
        if auth_type == "basic":
            return str(config.get("username", "")).strip() == expected_username and str(config.get("password", "")) == expected_password
        return not expected_username and not expected_password

    def _is_camera_widget(widget: dict[str, Any]) -> bool:
        widget_type = widget.get("type", widget.get("widgetType"))
        return str(widget_type or "").lower() in {"kamera", "camera"}

    def _contains_camera(widget: dict[str, Any]) -> bool:
        config = widget.get("config")
        if isinstance(config, dict):
            if _is_camera_widget(widget) and _camera_target(config) == expected_url and _camera_credentials_match(config):
                return True
            mini_widgets = config.get("miniWidgets")
            if isinstance(mini_widgets, list):
                return any(isinstance(mini_widget, dict) and _contains_camera(mini_widget) for mini_widget in mini_widgets)
        return False

    for widget in widgets:
        if isinstance(widget, dict) and _contains_camera(widget):
            return True
    return False


async def _ensure_camera_page_scope(
    db: Database,
    page_id: str,
    url: str,
    user: str | None,
    session_token: str = "",
    username: str = "",
    password: str = "",
) -> None:
    row = await db.fetchone(
        "SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'",
        (page_id,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kamera nicht gefunden")

    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    if access == "protected":
        validate_id = defining_node_id or page_id
        if not session_token or not validate_session(session_token, validate_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Valid session token required")
    if access == "user" and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provide Authorization: Bearer {token} or ?_token=",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if access == "user" and not await _check_user_access(db, page_id, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")
    if not _page_config_contains_camera_url(row["page_config"], url, username, password):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kamera nicht gefunden")


async def _ensure_camera_editor_preview_access(db: Database, user: str | None) -> None:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provide Authorization: Bearer {token} or ?_token=",
            headers={"WWW-Authenticate": "Bearer"},
        )
    row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (user,))
    if not row or not row["is_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


# ── Proxy-Endpunkt ─────────────────────────────────────────────────────────────


@router.get("/proxy")
async def proxy_camera(
    url: str = Query(..., description="Vollständige Kamera-URL (http://…)"),
    username: str = Query("", description="Basic-Auth Benutzername"),
    password: str = Query("", description="Basic-Auth Passwort"),
    apikey_param: str = Query("", description="API-Key Query-Parameter-Name"),
    apikey_value: str = Query("", description="API-Key Wert"),
    page_id: str = Query("", description="Visu-Seite, die das Kamera-Widget enthält"),
    session_token: str = Query("", description="PIN-Session-Token für geschützte Visu-Seiten"),
    editor_preview: bool = False,
    _user: str | None = Depends(_camera_auth),
    db: Database = Depends(get_db),
) -> StreamingResponse:
    """Proxyt den Kamera-Stream vom Backend aus.
    Ermöglicht HTTPS-Browser → Server → HTTP-Kamera (Mixed-Content-Bypass).
    """
    # 1. Schema-Validierung
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur HTTP/HTTPS-URLs erlaubt",
        )

    # 2. API-Key anhängen
    target = url
    if apikey_param and apikey_value:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}{apikey_param}={apikey_value}"

    # 3. Verpflichtender Visu-Page-Scope für Viewer-Widgets.
    # Admin-Editor-Previews dürfen Draft-URLs testen, die noch nicht in
    # visu_nodes.page_config gespeichert sind.
    if editor_preview:
        await _ensure_camera_editor_preview_access(db, _user)
    elif not isinstance(page_id, str) or not page_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kamera-Page-Scope erforderlich",
        )
    else:
        await _ensure_camera_page_scope(db, page_id.strip(), target, _user, session_token, username, password)

    # 4. SSRF-Prüfung und DNS-Pinning auf validierte Ziel-IP
    request_urls, pinned_headers, request_extensions = await _build_fetch_targets(target)
    auth = (username, password) if username else None

    # 5. HEAD-Request: Erreichbarkeit prüfen + Content-Type holen
    content_type = "application/octet-stream"
    stream_target = request_urls[0]
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,  # Redirects nicht folgen (SSRF via Redirect)
        ) as hc:
            last_error: httpx.RequestError | None = None
            for request_url in request_urls:
                try:
                    head = await hc.head(
                        request_url,
                        auth=auth,
                        headers=pinned_headers,
                        extensions=request_extensions,
                    )
                    stream_target = request_url
                    break
                except httpx.RequestError as exc:
                    last_error = exc
            else:
                assert last_error is not None
                raise last_error

        if head.status_code in (301, 302, 307, 308):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Kamera-URL leitet weiter — Redirects sind nicht erlaubt",
            )
        if head.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Kamera: Authentifizierung fehlgeschlagen (401)",
            )
        # 405 = HEAD nicht unterstützt → optimistisch weiterfahren
        if head.status_code != 405 and head.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Kamera antwortet mit {head.status_code}",
            )
        ct = head.headers.get("content-type", "")
        if ct:
            # Header-Injection verhindern
            content_type = ct.split("\n")[0].split("\r")[0]

    except HTTPException:
        raise
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kamera nicht erreichbar: {exc}",
        ) from exc

    # 6. Streaming-Generator (kein follow_redirects)
    async def _stream() -> AsyncGenerator[bytes]:
        async with httpx.AsyncClient(
            timeout=None,
            follow_redirects=False,
        ) as hc:
            try:
                async with hc.stream(
                    "GET",
                    stream_target,
                    auth=auth,
                    headers=pinned_headers,
                    extensions=request_extensions,
                ) as resp:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            except httpx.RequestError:
                return  # Verbindung unterbrochen — Stream still beenden

    return StreamingResponse(_stream(), media_type=content_type)
