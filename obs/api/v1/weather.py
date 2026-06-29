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
    return await _page_has_weather_url_for_session(db, page_id, url, session_token=None)


async def _load_page_config(db: Database, page_id: str) -> PageConfig | None:
    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (page_id,))
    if not row or not row["page_config"]:
        return None

    try:
        return PageConfig.model_validate_json(row["page_config"])
    except Exception:
        return None


def _widget_type_key(widget_type: object) -> str:
    return str(widget_type or "").replace("_", "").casefold()


async def _source_page_allows_session(db: Database, page_id: str, session_token: str | None) -> bool:
    from obs.api.v1.visu import _resolve_access_with_node

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    if access in ("public", "readonly"):
        return True
    if access == "protected" and session_token:
        return validate_session(session_token, defining_node_id or page_id)
    return False


async def _widget_has_weather_url(
    db: Database,
    *,
    widget_type: object,
    config: dict[str, object],
    requested_url: str,
    session_token: str | None,
    visited_refs: set[tuple[str, str]],
) -> bool:
    widget_key = _widget_type_key(widget_type)
    if widget_key == "wetter":
        return str(config.get("url") or "").strip() == requested_url

    if widget_key == "grundriss":
        mini_widgets = config.get("miniWidgets")
        if not isinstance(mini_widgets, list):
            return False
        for mini_widget in mini_widgets:
            if not isinstance(mini_widget, dict):
                continue
            mini_config = mini_widget.get("config")
            if not isinstance(mini_config, dict):
                mini_config = {}
            if await _widget_has_weather_url(
                db,
                widget_type=mini_widget.get("widgetType"),
                config=mini_config,
                requested_url=requested_url,
                session_token=session_token,
                visited_refs=visited_refs,
            ):
                return True
        return False

    if widget_key != "widgetref":
        return False

    source_page_id = str(config.get("source_page_id") or "").strip()
    source_widget_name = str(config.get("source_widget_name") or "").strip()
    if not source_page_id or not source_widget_name:
        return False
    ref_key = (source_page_id, source_widget_name)
    if ref_key in visited_refs:
        return False
    visited_refs.add(ref_key)

    if not await _source_page_allows_session(db, source_page_id, session_token):
        return False

    source_page = await _load_page_config(db, source_page_id)
    if source_page is None:
        return False

    source_widget = next((candidate for candidate in source_page.widgets if candidate.name == source_widget_name), None)
    if source_widget is None:
        return False
    return await _widget_has_weather_url(
        db,
        widget_type=source_widget.type,
        config=source_widget.config,
        requested_url=requested_url,
        session_token=session_token,
        visited_refs=visited_refs,
    )


async def _page_has_weather_url_for_session(db: Database, page_id: str, url: str, *, session_token: str | None) -> bool:
    page = await _load_page_config(db, page_id)
    if page is None:
        return False

    requested_url = url.strip()
    visited_refs: set[tuple[str, str]] = set()
    for widget in page.widgets:
        if await _widget_has_weather_url(
            db,
            widget_type=widget.type,
            config=widget.config,
            requested_url=requested_url,
            session_token=session_token,
            visited_refs=visited_refs,
        ):
            return True
    return False


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
    if not await _page_has_weather_url_for_session(db, page_id, url, session_token=session_token):
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
