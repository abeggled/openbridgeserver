"""
Integration Tests — Kamera-Proxy

GET /api/v1/camera/proxy

Abgedeckt:
  1. Kein Token → 401
  2. Ungültiger Token → 401
  3. Ungültiges URL-Schema (ftp://) → 400
  4. Kamera erreichbar → 200, Stream + Content-Type weitergeleitet
  5. Kamera nicht erreichbar (kein offener Port) → 502
  6. Kamera antwortet 401 → 502
  7. Kamera antwortet 404 → 502
  8. Auth via ?_token= Query-Param (statt Authorization-Header)
  9. Basic-Auth-Credentials werden an Kamera weitergeleitet
 10. API-Key wird als Query-Parameter an Kamera-URL angehängt
 11. HEAD antwortet 405 (nicht unterstützt) → Proxy fährt trotzdem fort
"""
from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.integration


# ── Hilfs-HTTP-Server ──────────────────────────────────────────────────────────

class _MockCameraServer:
    """
    Einfacher HTTP-Server in einem Daemon-Thread der Testanfragen der Kamera simuliert.
    Über `status` und `content_type` lässt sich das Verhalten pro Test steuern.
    """

    def __init__(self, status: int = 200, content_type: str = "image/jpeg",
                 body: bytes = b"\xff\xd8\xff\xe0JFIF", head_status: int | None = None):
        self.status = status
        self.content_type = content_type
        self.body = body
        # head_status=None → gleicher Status wie GET
        self.head_status = head_status if head_status is not None else status

        # Letzte empfangene Request-Daten (für Assertions)
        self.last_path: str = ""
        self.last_headers: dict[str, str] = {}

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                outer.last_path = self.path
                outer.last_headers = {k.lower(): v for k, v in self.headers.items()}
                self.send_response(outer.head_status)
                self.send_header("Content-Type", outer.content_type)
                self.end_headers()

            def do_GET(self):
                outer.last_path = self.path
                outer.last_headers = {k.lower(): v for k, v in self.headers.items()}
                self.send_response(outer.status)
                self.send_header("Content-Type", outer.content_type)
                self.end_headers()
                if outer.status < 400:
                    self.wfile.write(outer.body)

            def log_message(self, fmt, *args):
                pass  # Logs unterdrücken

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def shutdown(self):
        self._server.shutdown()


# ── Tests ──────────────────────────────────────────────────────────────────────

# 1. Kein Token
async def test_proxy_no_auth_returns_401(client):
    resp = await client.get("/api/v1/camera/proxy?url=http://example.com/cam")
    assert resp.status_code == 401


# 2. Ungültiger Token
async def test_proxy_invalid_token_returns_401(client):
    resp = await client.get(
        "/api/v1/camera/proxy?url=http://example.com/cam&_token=not.a.valid.jwt"
    )
    assert resp.status_code == 401


# 3. Ungültiges URL-Schema
async def test_proxy_invalid_scheme_returns_400(client, auth_headers):
    resp = await client.get(
        "/api/v1/camera/proxy?url=ftp://example.com/cam",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "HTTP/HTTPS" in resp.json().get("detail", "")


# 4. Kamera erreichbar → 200 + Stream
async def test_proxy_streams_camera_response(client, auth_headers):
    cam = _MockCameraServer(body=b"\xff\xd8\xff\xe0JFIF", content_type="image/jpeg")
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/snapshot.jpg",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "image/jpeg" in resp.headers.get("content-type", "")
        assert b"\xff\xd8" in resp.content
    finally:
        cam.shutdown()


# 5. Kamera nicht erreichbar → 502
async def test_proxy_camera_unreachable_returns_502(client, auth_headers):
    # Port 19979 sollte nichts laufen haben
    resp = await client.get(
        "/api/v1/camera/proxy?url=http://127.0.0.1:19979/cam",
        headers=auth_headers,
    )
    assert resp.status_code == 502
    assert "erreichbar" in resp.json().get("detail", "").lower()


# 6. Kamera antwortet 401 → 502
async def test_proxy_camera_401_returns_502(client, auth_headers):
    cam = _MockCameraServer(status=401)
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam",
            headers=auth_headers,
        )
        assert resp.status_code == 502
        detail = resp.json().get("detail", "")
        assert "401" in detail or "Authentifizierung" in detail
    finally:
        cam.shutdown()


# 7. Kamera antwortet 404 → 502
async def test_proxy_camera_404_returns_502(client, auth_headers):
    cam = _MockCameraServer(status=404)
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam",
            headers=auth_headers,
        )
        assert resp.status_code == 502
        assert "404" in resp.json().get("detail", "")
    finally:
        cam.shutdown()


# 8. Auth via ?_token= Query-Param
async def test_proxy_auth_via_query_token(client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    cam = _MockCameraServer()
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam&_token={token}",
            # Bewusst kein Authorization-Header
        )
        assert resp.status_code == 200
    finally:
        cam.shutdown()


# 9. Basic-Auth-Credentials werden weitergeleitet
async def test_proxy_basic_auth_forwarded(client, auth_headers):
    cam = _MockCameraServer()
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam"
            "&username=testuser&password=s3cr3t",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        auth_val = cam.last_headers.get("authorization", "")
        assert auth_val.startswith("Basic ")
        decoded = base64.b64decode(auth_val[6:]).decode()
        assert decoded == "testuser:s3cr3t"
    finally:
        cam.shutdown()


# 10. API-Key als Query-Parameter angehängt
async def test_proxy_apikey_appended_to_url(client, auth_headers):
    cam = _MockCameraServer()
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam"
            "&apikey_param=token&apikey_value=secret123",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        params = parse_qs(urlparse(cam.last_path).query)
        assert params.get("token") == ["secret123"]
    finally:
        cam.shutdown()


# 11. HEAD antwortet 405 (nicht unterstützt) → Proxy fährt fort
async def test_proxy_head_405_proceeds(client, auth_headers):
    # HEAD → 405, GET → 200 (z. B. alte IP-Kameras)
    cam = _MockCameraServer(status=200, head_status=405)
    try:
        resp = await client.get(
            f"/api/v1/camera/proxy?url={cam.base_url}/cam",
            headers=auth_headers,
        )
        assert resp.status_code == 200
    finally:
        cam.shutdown()
