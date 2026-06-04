"""Wetter-Proxy — holt Wetterdaten von einer konfigurierten API-URL.

GET /api/v1/weather/fetch?url=…

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

from obs.api.auth import decode_token
from obs.security.url_targets import UrlTargetBlockedError, build_pinned_url_targets

router = APIRouter(tags=["weather"])


async def _build_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    try:
        return await asyncio.to_thread(build_pinned_url_targets, url)
    except UrlTargetBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.decision.api_detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Authentifizierung ──────────────────────────────────────────────────────────


async def _weather_auth(
    request: Request,
    _token: str = Query("", alias="_token", description="JWT als Query-Parameter"),
) -> str:
    """Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
    oder als URL-Query-Parameter '?_token=…'.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return decode_token(auth_header[7:])
    if _token:
        return decode_token(_token)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide Authorization: Bearer {token} or ?_token=",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Fetch-Endpunkt ─────────────────────────────────────────────────────────────


@router.get("/fetch")
async def fetch_weather(
    url: str = Query(..., description="Vollständige Wetter-API-URL (inkl. API-Key)"),
    _user: str = Depends(_weather_auth),
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

    request_urls, pinned_headers, request_extensions = await _build_fetch_targets(url)

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as hc:
            last_error: httpx.RequestError | None = None
            for request_url in request_urls:
                try:
                    resp = await hc.get(
                        request_url,
                        headers=pinned_headers,
                        extensions=request_extensions,
                    )
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
