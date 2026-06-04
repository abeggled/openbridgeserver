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
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from obs.api.auth import decode_token
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
) -> str:
    """Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
    oder als URL-Query-Parameter '?_token=…' (nötig für <img>/<video>-Tags).
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


# ── Proxy-Endpunkt ─────────────────────────────────────────────────────────────


@router.get("/proxy")
async def proxy_camera(
    url: str = Query(..., description="Vollständige Kamera-URL (http://…)"),
    username: str = Query("", description="Basic-Auth Benutzername"),
    password: str = Query("", description="Basic-Auth Passwort"),
    apikey_param: str = Query("", description="API-Key Query-Parameter-Name"),
    apikey_value: str = Query("", description="API-Key Wert"),
    _user: str = Depends(_camera_auth),
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

    # 3. SSRF-Prüfung und DNS-Pinning auf validierte Ziel-IP
    request_urls, pinned_headers, request_extensions = await _build_fetch_targets(target)
    auth = (username, password) if username else None

    # 4. HEAD-Request: Erreichbarkeit prüfen + Content-Type holen
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

    # 5. Streaming-Generator (kein follow_redirects)
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
