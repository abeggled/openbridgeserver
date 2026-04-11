"""
Kamera-Proxy — leitet Kamera-Streams vom Backend weiter.

GET /api/v1/camera/proxy   Proxyt einen HTTP-Stream zur Kamera
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from obs.api.auth import get_current_user

router = APIRouter(tags=["camera"])


@router.get("/proxy")
async def proxy_camera(
    url: str = Query(..., description="Vollständige Kamera-URL (http://…)"),
    username: str = Query("", description="Basic-Auth Benutzername"),
    password: str = Query("", description="Basic-Auth Passwort"),
    apikey_param: str = Query("", description="API-Key Query-Parameter-Name"),
    apikey_value: str = Query("", description="API-Key Wert"),
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    """
    Proxyt den Kamera-Stream vom Backend aus.
    Ermöglicht HTTPS-Browser → Server → HTTP-Kamera (Mixed-Content-Bypass).
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur HTTP/HTTPS-URLs erlaubt",
        )

    # API-Key in URL einbauen
    target = url
    if apikey_param and apikey_value:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}{apikey_param}={apikey_value}"

    auth = (username, password) if username else None

    # Content-Type via HEAD ermitteln (Fallback: application/octet-stream)
    content_type = "application/octet-stream"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            head = await client.head(target, auth=auth)
            ct = head.headers.get("content-type", "")
            if ct:
                # Header-Injection verhindern
                content_type = ct.split("\n")[0].split("\r")[0]
    except Exception:
        pass  # Fallback bleibt

    async def _stream() -> AsyncGenerator[bytes, None]:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            try:
                async with client.stream("GET", target, auth=auth) as resp:
                    if resp.status_code == 401:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Kamera: Authentifizierung fehlgeschlagen (401)",
                        )
                    if resp.status_code >= 400:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Kamera antwortet mit {resp.status_code}",
                        )
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Kamera nicht erreichbar: {exc}",
                ) from exc

    return StreamingResponse(_stream(), media_type=content_type)
