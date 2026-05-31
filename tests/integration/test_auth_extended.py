"""Integration Tests — Auth API (extended coverage)

Covers uncovered paths in auth.py:
  - Invalid API key → 401                                    (line 143)
  - API key auth via X-API-Key header                       (line 147)
  - Non-admin hits admin endpoint → 403                     (line 175)
  - list_api_keys non-admin branch                          (lines 332-341)
  - delete_api_key 404 / 403 cross-user                     (lines 376-383)
  - create_user 409 conflict                                 (line 410)
  - create_user with mqtt_password → _sync_mqtt              (line 432)
  - get_user non-admin forbidden / 404                       (lines 443-451)
  - update_user 404 / 409 rename conflict                    (lines 463, 471)
  - update_user mqtt_enabled toggle → _sync_mqtt             (line 489)
  - delete_user self-delete 400                              (line 501)
  - delete_user mqtt_enabled user → _sync_mqtt               (line 507)
  - set_mqtt_password 403 / 404 / success                    (lines 523-539)
  - delete_mqtt_password 404 / success                       (lines 549-556)
  - get_me user-not-found 404                                (line 571)
  - change_password wrong current → 400 / success            (lines 581-584)
  - decode_token wrong type (access token used as refresh)   (line 110)
"""

from __future__ import annotations

import uuid

import pytest
from jose import jwt

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration

_ADMIN = "admin"
_ADMIN_PW = "admin"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_test_user(
    client,
    auth_headers,
    *,
    username: str | None = None,
    password: str = "TestPass123!",
    is_admin: bool = False,
    mqtt_enabled: bool = False,
    mqtt_password: str | None = None,
) -> dict:
    username = username or f"testuser-{uuid.uuid4().hex[:8]}"
    body: dict = {"username": username, "password": password, "is_admin": is_admin}
    if mqtt_enabled and mqtt_password:
        body["mqtt_enabled"] = True
        body["mqtt_password"] = mqtt_password
    resp = await client.post("/api/v1/auth/users", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_user(client, auth_headers, username: str) -> None:
    await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


# ---------------------------------------------------------------------------
# API key: invalid key → 401
# ---------------------------------------------------------------------------


async def test_invalid_api_key_returns_401(client):
    resp = await client.get("/api/v1/datapoints/", headers={"X-API-Key": "obs_" + "0" * 64})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API key: create, use, delete
# ---------------------------------------------------------------------------


async def test_api_key_auth_works(client, auth_headers):
    resp = await client.post("/api/v1/auth/apikeys", json={"name": f"test-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
    assert resp.status_code == 201
    key = resp.json()["key"]
    key_id = resp.json()["id"]

    try:
        protected = await client.get("/api/v1/datapoints/", headers={"X-API-Key": key})
        assert protected.status_code == 200
    finally:
        await client.delete(f"/api/v1/auth/apikeys/{key_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Non-admin hits admin endpoint → 403
# ---------------------------------------------------------------------------


async def test_non_admin_hits_admin_endpoint_returns_403(client, auth_headers):
    user = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        nonadmin_headers = _headers(create_access_token(user["username"]))
        resp = await client.get("/api/v1/auth/users", headers=nonadmin_headers)
        assert resp.status_code == 403
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# list_api_keys — non-admin sees only own keys
# ---------------------------------------------------------------------------


async def test_list_api_keys_non_admin_sees_own_only(client, auth_headers):
    user = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user_headers = _headers(create_access_token(user["username"]))

        # Create a key as the non-admin user
        key_resp = await client.post("/api/v1/auth/apikeys", json={"name": "my-key"}, headers=user_headers)
        assert key_resp.status_code == 201
        key_id = key_resp.json()["id"]

        # Non-admin lists keys — should only see their own
        list_resp = await client.get("/api/v1/auth/apikeys", headers=user_headers)
        assert list_resp.status_code == 200
        ids = {k["id"] for k in list_resp.json()}
        assert key_id in ids

        # Admin lists keys — sees all (including that key)
        admin_list = await client.get("/api/v1/auth/apikeys", headers=auth_headers)
        admin_ids = {k["id"] for k in admin_list.json()}
        assert key_id in admin_ids
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# delete_api_key — 404 for unknown, 403 cross-user
# ---------------------------------------------------------------------------


async def test_delete_api_key_404(client, auth_headers):
    resp = await client.delete(f"/api/v1/auth/apikeys/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


async def test_delete_api_key_cross_user_returns_403(client, auth_headers):
    user = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user_headers = _headers(create_access_token(user["username"]))

        # Admin creates a key
        admin_key_resp = await client.post("/api/v1/auth/apikeys", json={"name": "admin-key"}, headers=auth_headers)
        admin_key_id = admin_key_resp.json()["id"]

        # Non-admin tries to delete admin's key → 403
        resp = await client.delete(f"/api/v1/auth/apikeys/{admin_key_id}", headers=user_headers)
        assert resp.status_code == 403

        # Cleanup the admin key
        await client.delete(f"/api/v1/auth/apikeys/{admin_key_id}", headers=auth_headers)
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# create_user — 409 conflict
# ---------------------------------------------------------------------------


async def test_create_user_duplicate_username_returns_409(client, auth_headers):
    user = await _create_test_user(client, auth_headers)
    try:
        resp = await client.post(
            "/api/v1/auth/users",
            json={"username": user["username"], "password": "pw"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# create_user with mqtt_password → _sync_mqtt
# ---------------------------------------------------------------------------


async def test_create_user_with_mqtt_password(client, auth_headers):
    user = await _create_test_user(client, auth_headers, mqtt_enabled=True, mqtt_password="mqttpass123")
    try:
        assert user["mqtt_enabled"] is True
        assert user["mqtt_password_set"] is True
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# get_user — non-admin forbidden / 404
# ---------------------------------------------------------------------------


async def test_get_user_non_admin_other_user_returns_403(client, auth_headers):
    user1 = await _create_test_user(client, auth_headers, is_admin=False)
    user2 = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user1_headers = _headers(create_access_token(user1["username"]))
        resp = await client.get(f"/api/v1/auth/users/{user2['username']}", headers=user1_headers)
        assert resp.status_code == 403
    finally:
        await _delete_user(client, auth_headers, user1["username"])
        await _delete_user(client, auth_headers, user2["username"])


async def test_get_user_not_found_returns_404(client, auth_headers):
    resp = await client.get("/api/v1/auth/users/nonexistent-user-xyz-999", headers=auth_headers)
    assert resp.status_code == 404


async def test_get_user_non_admin_can_get_own_profile(client, auth_headers):
    user = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user_headers = _headers(create_access_token(user["username"]))
        resp = await client.get(f"/api/v1/auth/users/{user['username']}", headers=user_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == user["username"]
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# update_user — 404 / 409 rename conflict
# ---------------------------------------------------------------------------


async def test_update_user_not_found_returns_404(client, auth_headers):
    resp = await client.patch("/api/v1/auth/users/nobody-xyz-999", json={"is_admin": False}, headers=auth_headers)
    assert resp.status_code == 404


async def test_update_user_rename_conflict_returns_409(client, auth_headers):
    user1 = await _create_test_user(client, auth_headers)
    user2 = await _create_test_user(client, auth_headers)
    try:
        resp = await client.patch(
            f"/api/v1/auth/users/{user1['username']}",
            json={"username": user2["username"]},
            headers=auth_headers,
        )
        assert resp.status_code == 409
    finally:
        await _delete_user(client, auth_headers, user1["username"])
        await _delete_user(client, auth_headers, user2["username"])


# ---------------------------------------------------------------------------
# update_user — mqtt_enabled toggle → _sync_mqtt
# ---------------------------------------------------------------------------


async def test_update_user_toggle_mqtt_enabled(client, auth_headers):
    user = await _create_test_user(client, auth_headers, mqtt_enabled=True, mqtt_password="mqttpass123")
    try:
        assert user["mqtt_enabled"] is True
        resp = await client.patch(
            f"/api/v1/auth/users/{user['username']}",
            json={"mqtt_enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["mqtt_enabled"] is False
        assert resp.json()["mqtt_password_set"] is False
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# delete_user — 400 self-delete / mqtt_enabled → _sync_mqtt
# ---------------------------------------------------------------------------


async def test_delete_user_self_returns_400(client, auth_headers):
    resp = await client.delete(f"/api/v1/auth/users/{_ADMIN}", headers=auth_headers)
    assert resp.status_code == 400


async def test_delete_user_with_mqtt_enabled_syncs_mqtt(client, auth_headers):
    user = await _create_test_user(client, auth_headers, mqtt_enabled=True, mqtt_password="mqttpass123")
    resp = await client.delete(f"/api/v1/auth/users/{user['username']}", headers=auth_headers)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# set_mqtt_password
# ---------------------------------------------------------------------------


async def test_set_mqtt_password_non_admin_other_user_returns_403(client, auth_headers):
    user1 = await _create_test_user(client, auth_headers, is_admin=False)
    user2 = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user1_headers = _headers(create_access_token(user1["username"]))
        resp = await client.post(
            f"/api/v1/auth/users/{user2['username']}/mqtt-password",
            json={"password": "newpass"},
            headers=user1_headers,
        )
        assert resp.status_code == 403
    finally:
        await _delete_user(client, auth_headers, user1["username"])
        await _delete_user(client, auth_headers, user2["username"])


async def test_set_mqtt_password_user_not_found_returns_404(client, auth_headers):
    resp = await client.post(
        "/api/v1/auth/users/nobody-xyz-999/mqtt-password",
        json={"password": "newpass"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_set_mqtt_password_success(client, auth_headers):
    user = await _create_test_user(client, auth_headers)
    try:
        resp = await client.post(
            f"/api/v1/auth/users/{user['username']}/mqtt-password",
            json={"password": "newmqttpass"},
            headers=auth_headers,
        )
        assert resp.status_code == 204

        profile = await client.get(f"/api/v1/auth/users/{user['username']}", headers=auth_headers)
        assert profile.json()["mqtt_enabled"] is True
        assert profile.json()["mqtt_password_set"] is True
    finally:
        await _delete_user(client, auth_headers, user["username"])


async def test_set_mqtt_password_self_allowed(client, auth_headers):
    user = await _create_test_user(client, auth_headers, is_admin=False)
    try:
        user_headers = _headers(create_access_token(user["username"]))
        resp = await client.post(
            f"/api/v1/auth/users/{user['username']}/mqtt-password",
            json={"password": "selfpass"},
            headers=user_headers,
        )
        assert resp.status_code == 204
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# delete_mqtt_password
# ---------------------------------------------------------------------------


async def test_delete_mqtt_password_user_not_found_returns_404(client, auth_headers):
    resp = await client.delete(
        "/api/v1/auth/users/nobody-xyz-999/mqtt-password",
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_delete_mqtt_password_success(client, auth_headers):
    user = await _create_test_user(client, auth_headers, mqtt_enabled=True, mqtt_password="mqttpass")
    try:
        resp = await client.delete(
            f"/api/v1/auth/users/{user['username']}/mqtt-password",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        profile = await client.get(f"/api/v1/auth/users/{user['username']}", headers=auth_headers)
        assert profile.json()["mqtt_enabled"] is False
        assert profile.json()["mqtt_password_set"] is False
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# get_me — user deleted after JWT issued → 404
# ---------------------------------------------------------------------------


async def test_get_me_user_deleted_returns_404(client, auth_headers):
    user = await _create_test_user(client, auth_headers)
    user_headers = _headers(create_access_token(user["username"]))
    await _delete_user(client, auth_headers, user["username"])

    resp = await client.get("/api/v1/auth/me", headers=user_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------


async def test_change_password_wrong_current_returns_400(client, auth_headers):
    user = await _create_test_user(client, auth_headers, password="OldPass123!")
    try:
        user_headers = _headers(create_access_token(user["username"]))
        resp = await client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": "WrongPass!", "new_password": "NewPass123!"},
            headers=user_headers,
        )
        assert resp.status_code == 400
    finally:
        await _delete_user(client, auth_headers, user["username"])


async def test_change_password_success(client, auth_headers):
    user = await _create_test_user(client, auth_headers, password="OldPass123!")
    try:
        user_headers = _headers(create_access_token(user["username"]))
        resp = await client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": "OldPass123!", "new_password": "NewPass456!"},
            headers=user_headers,
        )
        assert resp.status_code == 204
    finally:
        await _delete_user(client, auth_headers, user["username"])


# ---------------------------------------------------------------------------
# decode_token — wrong token type (access used as refresh)
# ---------------------------------------------------------------------------


async def test_access_token_rejected_as_refresh(client, auth_headers):
    access_token = auth_headers["Authorization"].removeprefix("Bearer ")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# decode_token — token with no sub claim
# ---------------------------------------------------------------------------


async def test_token_without_sub_returns_401(client):
    # Craft a JWT with type=access but no sub — hits the "if not sub" branch
    from obs.config import get_settings

    secret = get_settings().security.jwt_secret
    token = jwt.encode({"type": "access"}, secret, algorithm="HS256")
    resp = await client.get("/api/v1/datapoints/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
