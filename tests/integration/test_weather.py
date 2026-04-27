"""
Integration Tests — Wetter-Proxy

GET /api/v1/weather/fetch

Abgedeckt:
  1.  Kein Token → 401
  2.  Ungültiger Token → 401
  3.  Ungültiges URL-Schema (ftp://) → 400
  4.  Wetter-API erreichbar → 200, JSON-Daten weitergeleitet
  5.  Wetter-API nicht erreichbar → 502
  6.  Wetter-API antwortet 401 → 502 (API-Key ungültig)
  7.  Wetter-API antwortet 404 → 502
  8.  Wetter-API antwortet mit Redirect → 400
  9.  Wetter-API liefert kein JSON (HTML) → 502
  10. Auth via ?_token= Query-Param (statt Authorization-Header)
  11. Content-Type application/json;charset=utf-8 wird akzeptiert

SSRF-Schutz:
  12. Loopback IPv4 (127.0.0.1) → 400
  13. Loopback via localhost-Hostname → 400
  14. Link-local / Cloud-Metadata (169.254.169.254) → 400
  15. Loopback IPv6 (::1) → 400
"""
from __future__ import annotations

import json
import threading
import unittest.mock
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import obs.api.v1.weather as _weather_module

pytestmark = pytest.mark.integration


@pytest.fixture()
def bypass_ssrf():
    """
    Deaktiviert SSRF-Blocking für Tests die einen lokalen Mock-Server auf
    127.0.0.1 verwenden. Die SSRF-Tests (12–15) dürfen diese Fixture NICHT nutzen.
    """
    with unittest.mock.patch.object(_weather_module, "_BLOCKED_NETWORKS", []):
        yield


# ── Hilfs-HTTP-Server ──────────────────────────────────────────────────────────

_SAMPLE_WEATHER = {
    "lat": 47.3769,
    "lon": 8.5417,
    "timezone": "Europe/Zurich",
    "current": {
        "dt": 1714000000,
        "temp": 14.5,
        "feels_like": 12.3,
        "humidity": 68,
        "pressure": 1015,
        "uvi": 3.2,
        "visibility": 10000,
        "wind_speed": 3.1,
        "wind_deg": 200,
        "clouds": 40,
        "sunrise": 1713930000,
        "sunset": 1713980000,
        "weather": [{"id": 802, "main": "Clouds", "description": "bewölkt", "icon": "03d"}],
    },
    "daily": [],
}


class _MockWeatherServer:
    """
    Einfacher HTTP-Server der Wetter-API-Antworten simuliert.
    """

    def __init__(
        self,
        status: int = 200,
        content_type: str = "application/json",
        body: bytes | None = None,
        head_status: int | None = None,
        redirect_to: str | None = None,
    ):
        self.status = status
        self.content_type = content_type
        self.body = body if body is not None else json.dumps(_SAMPLE_WEATHER).encode()
        self.head_status = head_status if head_status is not None else status
        self.redirect_to = redirect_to

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if outer.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.end_headers()
                    return
                self.send_response(outer.status)
                self.send_header("Content-Type", outer.content_type)
                self.end_headers()
                if outer.status < 400:
                    self.wfile.write(outer.body)

            def log_message(self, fmt, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def shutdown(self):
        self._server.shutdown()


# ── Tests ──────────────────────────────────────────────────────────────────────

# 1. Kein Token
async def test_fetch_no_auth_returns_401(client):
    resp = await client.get("/api/v1/weather/fetch?url=http://example.com/weather")
    assert resp.status_code == 401


# 2. Ungültiger Token
async def test_fetch_invalid_token_returns_401(client):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://example.com/weather&_token=not.valid.jwt"
    )
    assert resp.status_code == 401


# 3. Ungültiges URL-Schema
async def test_fetch_invalid_scheme_returns_400(client, auth_headers):
    resp = await client.get(
        "/api/v1/weather/fetch?url=ftp://example.com/weather",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "HTTP/HTTPS" in resp.json().get("detail", "")


# 4. Wetter-API erreichbar → 200 + JSON
async def test_fetch_returns_json_data(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer()
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/onecall",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert data["timezone"] == "Europe/Zurich"
    finally:
        srv.shutdown()


# 5. Wetter-API nicht erreichbar → 502
async def test_fetch_api_unreachable_returns_502(client, auth_headers, bypass_ssrf):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://127.0.0.1:19978/weather",
        headers=auth_headers,
    )
    assert resp.status_code == 502
    assert "erreichbar" in resp.json().get("detail", "").lower()


# 6. Wetter-API antwortet 401 → 502
async def test_fetch_api_401_returns_502(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer(status=401)
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather",
            headers=auth_headers,
        )
        assert resp.status_code == 502
        detail = resp.json().get("detail", "")
        assert "401" in detail or "api-key" in detail.lower() or "authentifizierung" in detail.lower()
    finally:
        srv.shutdown()


# 7. Wetter-API antwortet 404 → 502
async def test_fetch_api_404_returns_502(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer(status=404)
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather",
            headers=auth_headers,
        )
        assert resp.status_code == 502
        assert "404" in resp.json().get("detail", "")
    finally:
        srv.shutdown()


# 8. Wetter-API leitet weiter → 400
async def test_fetch_redirect_returns_400(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer(redirect_to="http://other.example.com/weather")
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "redirect" in resp.json().get("detail", "").lower() or "weiter" in resp.json().get("detail", "").lower()
    finally:
        srv.shutdown()


# 9. Wetter-API liefert kein JSON → 502
async def test_fetch_non_json_response_returns_502(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer(
        content_type="text/html",
        body=b"<html><body>Error</body></html>",
    )
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather",
            headers=auth_headers,
        )
        assert resp.status_code == 502
        detail = resp.json().get("detail", "")
        assert "json" in detail.lower()
    finally:
        srv.shutdown()


# 10. Auth via ?_token= Query-Param
async def test_fetch_auth_via_query_token(client, auth_headers, bypass_ssrf):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    srv = _MockWeatherServer()
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather&_token={token}",
            # Bewusst kein Authorization-Header
        )
        assert resp.status_code == 200
    finally:
        srv.shutdown()


# 11. Content-Type application/json;charset=utf-8 wird akzeptiert
async def test_fetch_json_charset_accepted(client, auth_headers, bypass_ssrf):
    srv = _MockWeatherServer(content_type="application/json; charset=utf-8")
    try:
        resp = await client.get(
            f"/api/v1/weather/fetch?url={srv.base_url}/weather",
            headers=auth_headers,
        )
        assert resp.status_code == 200
    finally:
        srv.shutdown()


# ── SSRF-Schutz ────────────────────────────────────────────────────────────────

# 12. Loopback IPv4 direkt als IP-Literal
async def test_fetch_ssrf_loopback_ipv4_blocked(client, auth_headers):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://127.0.0.1:8080/secret",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "gesperrt" in resp.json().get("detail", "").lower()


# 13. Loopback via localhost-Hostname
async def test_fetch_ssrf_localhost_blocked(client, auth_headers):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://localhost/secret",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "gesperrt" in resp.json().get("detail", "").lower()


# 14. Link-local / Cloud-Metadata-Endpunkt (AWS IMDSv1)
async def test_fetch_ssrf_metadata_ip_blocked(client, auth_headers):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://169.254.169.254/latest/meta-data/",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "gesperrt" in resp.json().get("detail", "").lower()


# 15. Loopback IPv6
async def test_fetch_ssrf_loopback_ipv6_blocked(client, auth_headers):
    resp = await client.get(
        "/api/v1/weather/fetch?url=http://[::1]/secret",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "gesperrt" in resp.json().get("detail", "").lower()
