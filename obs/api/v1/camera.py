"""
Kamera-Proxy — leitet Kamera-Streams vom Backend weiter.

GET /api/v1/camera/proxy   Proxyt einen HTTP-Stream zur Kamera

SSRF-Schutz:
  - Nur HTTP/HTTPS-Schemas erlaubt
  - Hostname wird per DNS aufgelöst; die resultierende IP wird gegen
    gesperrte Netzwerkbereiche geprüft (Loopback, Link-local, Metadata)
  - follow_redirects=False im Stream-Client verhindert Redirect-basiertes SSRF
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncGenerator
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from obs.api.auth import decode_token

router = APIRouter(tags=["camera"])

# ── SSRF-Schutz: gesperrte IP-Bereiche ────────────────────────────────────────

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local / Cloud-Metadata (AWS 169.254.169.254, GCP, Azure)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # "This"-Netzwerk
    ipaddress.ip_network("0.0.0.0/8"),
    # Shared Address Space (RFC 6598, Carrier-Grade NAT)
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv4-in-IPv6 Mapped (verhindert Bypass via ::ffff:127.0.0.1)
    ipaddress.ip_network("::ffff:0:0/96"),
]


async def _check_ssrf(url: str) -> None:
    """
    Löst den Hostnamen der URL auf und verwirft alle Adressen, die in
    einem gesperrten Netzwerk liegen (SSRF-Prävention).

    Raises:
        HTTPException 400 — ungültige URL oder gesperrte Ziel-IP
        HTTPException 502 — Hostname nicht auflösbar
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ungültige URL: {exc}",
        ) from exc

    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültige URL: kein Hostname erkennbar",
        )

    # DNS-Auflösung asynchron im Thread-Pool (blockiert den Event-Loop nicht)
    try:
        addr_infos = await asyncio.to_thread(
            socket.getaddrinfo, hostname, None, 0, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hostname '{hostname}' nicht auflösbar: {exc}",
        ) from exc

    for *_, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"URL-Ziel nicht erlaubt: die aufgelöste Adresse {ip} "
                        f"liegt in einem gesperrten Netzwerkbereich"
                    ),
                )


# ── Authentifizierung ──────────────────────────────────────────────────────────

async def _camera_auth(
    request: Request,
    _token: str = Query("", alias="_token", description="JWT als Query-Parameter"),
) -> str:
    """
    Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
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
    """
    Proxyt den Kamera-Stream vom Backend aus.
    Ermöglicht HTTPS-Browser → Server → HTTP-Kamera (Mixed-Content-Bypass).
    """
    # 1. Schema-Validierung
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur HTTP/HTTPS-URLs erlaubt",
        )

    # 2. SSRF-Prüfung: gesperrte Ziel-IPs
    await _check_ssrf(url)

    # 3. API-Key anhängen
    target = url
    if apikey_param and apikey_value:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}{apikey_param}={apikey_value}"

    auth = (username, password) if username else None

    # 4. HEAD-Request: Erreichbarkeit prüfen + Content-Type holen
    content_type = "application/octet-stream"
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,  # Redirects nicht folgen (SSRF via Redirect)
        ) as hc:
            head = await hc.head(target, auth=auth)

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
    async def _stream() -> AsyncGenerator[bytes, None]:
        async with httpx.AsyncClient(
            timeout=None,
            follow_redirects=False,
        ) as hc:
            try:
                async with hc.stream("GET", target, auth=auth) as resp:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            except httpx.RequestError:
                return  # Verbindung unterbrochen — Stream still beenden

    return StreamingResponse(_stream(), media_type=content_type)
