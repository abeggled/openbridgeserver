"""Wetter-Proxy — holt Wetterdaten von einer konfigurierten API-URL.

GET /api/v1/weather/fetch?url=…  (authenticated)

Unterstützt OpenWeatherMap One Call API 3.0 (und kompatible Dienste).

SSRF-Schutz:
  - Nur HTTP/HTTPS-Schemas erlaubt
  - Hostname wird per DNS aufgelöst und zentral gegen öffentliche Ziele bzw.
    die operatorgepflegte URL-Target-Allowlist geprüft
  - follow_redirects=False verhindert Redirect-basiertes SSRF
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from obs.api.auth import optional_current_user
from obs.api.v1.sessions import validate_session
from obs.db.database import Database, get_db
from obs.models.visu import PageConfig
from obs.security.url_targets import UrlTargetBlockedError, build_pinned_url_targets

router = APIRouter(tags=["weather"])


async def _build_fetch_targets(
    url: str,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        return await asyncio.to_thread(build_pinned_url_targets, url)
    except UrlTargetBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.decision.api_detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _check_ssrf(
    url: str,
    *,
    legacy_detail: bool = True,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        return await _build_fetch_targets(url)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            if not legacy_detail:
                raise
            message = str(detail.get("message") or "")
            if "Hostname could not be resolved" in message:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Hostname nicht auflösbar: {message}",
                ) from exc
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"URL-Ziel nicht erlaubt: {message}",
            ) from exc
        if "Hostname could not be resolved" in str(detail):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Hostname nicht auflösbar: {detail}",
            ) from exc
        raise


async def _page_has_weather_url(db: Database, page_id: str, url: str) -> bool:
    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (page_id,))
    if not row or not row["page_config"]:
        return False

    try:
        page = PageConfig.model_validate_json(row["page_config"])
    except Exception:
        return False

    requested_url = url.strip()
    return any(widget.type == "Wetter" and str(widget.config.get("url") or "").strip() == requested_url for widget in page.widgets)


async def _require_weather_access(
    request: Request,
    url: str,
    user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> None:
    if user is not None:
        return

    page_id = request.headers.get("X-Page-Id")
    session_token = request.headers.get("X-Session-Token")
    if not page_id or not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    from obs.api.v1.visu import _resolve_access_with_node

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    validate_id = defining_node_id or page_id
    if access != "protected" or not validate_session(session_token, validate_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Valid session token required")
    if not await _page_has_weather_url(db, page_id, url):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Weather URL is not configured on the page")


# ── Fetch-Endpunkt ─────────────────────────────────────────────────────────────


@router.get("/fetch")
async def fetch_weather(
    url: str = Query(..., description="Vollständige Wetter-API-URL (inkl. API-Key)"),
    _user: object = Depends(_require_weather_access),
) -> JSONResponse:
    """Holt Wetterdaten von der konfigurierten API-URL und gibt sie als JSON zurück.
    Der API-Key wird als Teil der URL übergeben (z.B. OpenWeatherMap appid=…).

    Unterstützte Dienste:
      - OpenWeatherMap One Call API 3.0 (empfohlen)
      - Jeder HTTP-Endpunkt der JSON-Wetterdaten zurückgibt
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur HTTP/HTTPS-URLs erlaubt",
        )

    request_urls, pinned_headers, request_extensions = await _check_ssrf(url, legacy_detail=False)

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as hc:
            last_error: httpx.RequestError | None = None
            for request_url in request_urls:
                try:
                    if pinned_headers or request_extensions:
                        resp = await hc.get(
                            request_url,
                            headers=pinned_headers,
                            extensions=request_extensions,
                        )
                    else:
                        resp = await hc.get(request_url)
                    break
                except httpx.RequestError as exc:
                    last_error = exc
            else:
                assert last_error is not None
                raise last_error
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Wetter-API nicht erreichbar: {exc}",
        ) from exc

    if resp.status_code in (301, 302, 307, 308):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wetter-API-URL leitet weiter — Redirects sind nicht erlaubt",
        )
    if resp.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wetter-API: Authentifizierung fehlgeschlagen (401) — API-Key prüfen",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Wetter-API antwortet mit {resp.status_code}",
        )

    ct = resp.headers.get("content-type", "")
    if "json" not in ct:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Wetter-API liefert kein JSON (Content-Type: {ct})",
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Wetter-API liefert kein gültiges JSON: {exc}",
        ) from exc

    return JSONResponse(content=data)
