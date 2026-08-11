"""Tests for CORS header configuration (authz If-Match / ETag round-trip).

These tests verify that CORS preflights for PUT /authz/.../grants pass through
correctly when the GUI is served from a cross-origin frontend.  The authz
editor sends If-Match on PUT and reads ETag from GET; both headers must be
declared in the CORS middleware or the rights editor fails in cross-origin
deployments.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient


def _app_with_cors(allow_headers: list[str], expose_headers: list[str]) -> FastAPI:
    """Minimal app that mirrors the CORSMiddleware config from obs/main.py."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=allow_headers,
        expose_headers=expose_headers,
    )

    @app.get("/sentinel")
    async def sentinel():
        from fastapi.responses import Response

        r = Response(content="ok")
        r.headers["ETag"] = '"test-etag"'
        return r

    return app


ORIGIN = "http://localhost:3000"


@pytest.mark.asyncio
async def test_if_match_allowed_in_cors_preflight() -> None:
    """CORS preflight for PUT with If-Match must return the header as allowed.

    The authz grants endpoint requires If-Match on every PUT (line 409 of
    obs/api/v1/authz.py).  Without If-Match in allow_headers the preflight
    returns a 400 / empty AC-Allow-Headers and the browser blocks the request.
    """
    app = _app_with_cors(
        allow_headers=["Authorization", "X-API-Key", "Content-Type", "If-Match"],
        expose_headers=["ETag"],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/sentinel",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Content-Type, If-Match",
            },
        )
    assert resp.status_code == 200
    allowed = resp.headers.get("access-control-allow-headers", "")
    assert "if-match" in allowed.lower(), f"If-Match not in AC-Allow-Headers: {allowed!r}"


@pytest.mark.asyncio
async def test_etag_exposed_in_cors_response() -> None:
    """The ETag returned by GET /authz/.../grants must be accessible cross-origin.

    Without expose_headers=["ETag"] browsers hide the header from JS even
    though it is present in the HTTP response.  The GUI reads the ETag from
    the GET and sends it back as If-Match on the PUT; missing exposure breaks
    that round-trip.
    """
    app = _app_with_cors(
        allow_headers=["Authorization", "X-API-Key", "Content-Type", "If-Match"],
        expose_headers=["ETag"],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/sentinel", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    exposed = resp.headers.get("access-control-expose-headers", "")
    assert "etag" in exposed.lower(), f"ETag not in AC-Expose-Headers: {exposed!r}"


@pytest.mark.asyncio
async def test_old_config_missing_if_match_in_preflight() -> None:
    """Regression guard: the old allow_headers list silently blocked If-Match.

    This test documents the broken state so we can verify the fix is necessary.
    Without If-Match in allow_headers, the preflight response omits it and the
    browser would block the PUT.
    """
    old_app = _app_with_cors(
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        expose_headers=[],
    )
    async with AsyncClient(transport=ASGITransport(app=old_app), base_url="http://testserver") as client:
        resp = await client.options(
            "/sentinel",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Content-Type, If-Match",
            },
        )
    allowed = resp.headers.get("access-control-allow-headers", "")
    assert "if-match" not in allowed.lower(), "Regression: If-Match should NOT be allowed in the old config (confirms fix is needed)"
