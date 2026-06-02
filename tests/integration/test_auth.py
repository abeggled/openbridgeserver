"""Integration Tests — Authentication

Covers:
  - POST /api/v1/auth/login  (valid / invalid credentials)
  - POST /api/v1/auth/refresh
  - Protected endpoint behaviour (401 without token, 200 with token)
  - Malformed / expired token → 401
"""

from __future__ import annotations

import uuid

import pytest

from obs.api.auth import create_access_token
from tests.integration.conftest import assert_auth_token_shape

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_returns_token(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert_auth_token_shape(body)
    assert body["token_type"] == "bearer"


async def test_login_invalid_password(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong_password"},
    )
    assert resp.status_code == 401


async def test_login_invalid_user(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "admin"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Protected endpoint
# ---------------------------------------------------------------------------


async def test_protected_endpoint_without_token(client):
    """GET /datapoints/ without auth → 401."""
    resp = await client.get("/api/v1/datapoints/")
    assert resp.status_code == 401


async def test_protected_endpoint_with_valid_token(client, auth_headers):
    """GET /datapoints/ with a valid Bearer token → 200."""
    resp = await client.get("/api/v1/datapoints/", headers=auth_headers)
    assert resp.status_code == 200


async def test_protected_endpoint_with_malformed_token(client):
    """A random string that is not a JWT → 401."""
    resp = await client.get(
        "/api/v1/datapoints/",
        headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


async def test_refresh_token(client):
    """POST /auth/login → refresh → new access_token."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login.status_code == 200
    refresh_token = login.json()["refresh_token"]

    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh.status_code == 200
    new_body = refresh.json()
    assert "access_token" in new_body
    # New access token is different from the refresh token itself
    assert new_body["access_token"] != refresh_token


async def test_refresh_with_access_token_rejected(client, auth_headers):
    """Using an *access* token as a refresh token → 401 (wrong token type).

    We reuse the already-available access token from the session fixture to
    avoid triggering the /login rate limiter (5/minute) again.
    """
    # Extract the raw JWT from the header dict — no new login call needed
    access_token = auth_headers["Authorization"].removeprefix("Bearer ")

    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},  # wrong type — must be rejected
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /me endpoint
# ---------------------------------------------------------------------------


async def test_me_returns_admin_info(client, auth_headers):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["is_admin"] is True


async def test_api_key_owner_is_synced_on_username_rename(client, auth_headers):
    original = f"admin-rename-{uuid.uuid4().hex[:8]}"
    renamed = f"{original}-new"
    password = "pw-12345678"

    create_user = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={"username": original, "password": password, "is_admin": True},
    )
    assert create_user.status_code == 201, create_user.text

    try:
        user_headers = {"Authorization": f"Bearer {create_access_token(original)}"}

        create_key = await client.post(
            "/api/v1/auth/apikeys",
            headers=user_headers,
            json={"name": "rename-sync-test"},
        )
        assert create_key.status_code == 201, create_key.text
        api_key = create_key.json()["key"]

        rename = await client.patch(
            f"/api/v1/auth/users/{original}",
            headers=auth_headers,
            json={"username": renamed},
        )
        assert rename.status_code == 200, rename.text

        admin_call = await client.get("/api/v1/auth/users", headers={"X-API-Key": api_key})
        assert admin_call.status_code == 200, admin_call.text
    finally:
        await client.delete(f"/api/v1/auth/users/{renamed}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{original}", headers=auth_headers)
