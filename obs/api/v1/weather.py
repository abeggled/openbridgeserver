"""
Wetter-Proxy — holt Wetterdaten von einer konfigurierten API-URL.

GET /api/v1/weather/fetch?url=…

Unterstützt OpenWeatherMap One Call API 3.0 (und kompatible Dienste).

SSRF-Schutz:
  - Nur HTTP/HTTPS-Schemas erlaubt
  - Hostname wird per DNS aufgelöst; die resultierende IP wird gegen
    gesperrte Netzwerkbereiche geprüft (Loopback, Link-local, Metadata)
  - follow_redirects=False verhindert Redirect-basiertes SSRF
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from obs.api.auth import decode_token

router = APIRouter(tags=["weather"])

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

    Private Netzwerke (192.168.x.x, 10.x.x.x) sind bewusst erlaubt,
    da Wetter-Datenquellen auch im lokalen Netz betrieben werden können.

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

async def _weather_auth(
    request: Request,
    _token: str = Query("", alias="_token", description="JWT als Query-Parameter"),
) -> str:
    """
    Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
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
    """
    Holt Wetterdaten von der konfigurierten API-URL und gibt sie als JSON zurück.
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

    await _check_ssrf(url)

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as hc:
            resp = await hc.get(url)
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
