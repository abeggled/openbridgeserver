"""Integration tests for visu access/auth combinations.

Focus:
  - Unauthenticated access to public/protected visu pages
  - Datapoint value bootstrap reads for unauthenticated visu sessions
  - Regular (non-admin) user access to user-scoped visu pages
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def _create_non_admin_user_and_headers(client, auth_headers, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/users",
        json={
            "username": username,
            "password": password,
            "is_admin": False,
            "mqtt_enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    from obs.api.auth import create_access_token

    token = create_access_token(username)
    return {"Authorization": f"Bearer {token}"}


async def _create_dp(client, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "unit": "°C", "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_page(client, auth_headers, name: str, access: str, *, access_pin: str | None = None) -> str:
    payload: dict[str, object] = {
        "name": name,
        "type": "PAGE",
        "order": 999,
        "access": access,
    }
    if access_pin is not None:
        payload["access_pin"] = access_pin

    resp = await client.post("/api/v1/visu/nodes", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _save_page_with_single_dp_widget(client, auth_headers, page_id: str, dp_id: str) -> None:
    resp = await client.put(
        f"/api/v1/visu/pages/{page_id}",
        json={
            "grid_cols": 12,
            "grid_row_height": 80,
            "grid_cell_width": 80,
            "background": None,
            "widgets": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "Auth Test Widget",
                    "type": "ValueDisplay",
                    "datapoint_id": dp_id,
                    "status_datapoint_id": None,
                    "x": 0,
                    "y": 0,
                    "w": 3,
                    "h": 2,
                    "config": {},
                },
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 204), resp.text


async def _write_value(client, auth_headers, dp_id: str, value: float) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def test_public_visu_allows_unauth_page_and_value_bootstrap(client, auth_headers):
    dp_id = await _create_dp(client, auth_headers, f"public-visu-dp-{uuid.uuid4().hex[:8]}")
    page_id = await _create_page(client, auth_headers, f"public-visu-page-{uuid.uuid4().hex[:8]}", "public")

    try:
        await _save_page_with_single_dp_widget(client, auth_headers, page_id, dp_id)
        await _write_value(client, auth_headers, dp_id, 21.5)

        page_resp = await client.get(f"/api/v1/visu/pages/{page_id}")
        assert page_resp.status_code == 200, page_resp.text

        value_resp = await client.get(
            f"/api/v1/datapoints/{dp_id}/value",
            headers={"X-Page-Id": page_id},
        )
        assert value_resp.status_code == 200, value_resp.text
        assert value_resp.json()["value"] == pytest.approx(21.5)
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


async def test_protected_visu_requires_session_token_for_unauth_access(client, auth_headers):
    pin = "1234"
    dp_id = await _create_dp(client, auth_headers, f"protected-visu-dp-{uuid.uuid4().hex[:8]}")
    page_id = await _create_page(
        client,
        auth_headers,
        f"protected-visu-page-{uuid.uuid4().hex[:8]}",
        "protected",
        access_pin=pin,
    )

    try:
        await _save_page_with_single_dp_widget(client, auth_headers, page_id, dp_id)
        await _write_value(client, auth_headers, dp_id, 19.25)

        no_token_page = await client.get(f"/api/v1/visu/pages/{page_id}")
        assert no_token_page.status_code == 401, no_token_page.text

        auth_resp = await client.post(
            f"/api/v1/visu/nodes/{page_id}/auth",
            json={"pin": pin},
        )
        assert auth_resp.status_code == 200, auth_resp.text
        session_token = auth_resp.json()["session_token"]

        page_resp = await client.get(
            f"/api/v1/visu/pages/{page_id}",
            headers={"X-Session-Token": session_token},
        )
        assert page_resp.status_code == 200, page_resp.text

        value_resp = await client.get(
            f"/api/v1/datapoints/{dp_id}/value",
            headers={"X-Page-Id": page_id, "X-Session-Token": session_token},
        )
        assert value_resp.status_code == 200, value_resp.text
        assert value_resp.json()["value"] == pytest.approx(19.25)

        bad_value_resp = await client.get(
            f"/api/v1/datapoints/{dp_id}/value",
            headers={"X-Page-Id": page_id, "X-Session-Token": "invalid-token"},
        )
        assert bad_value_resp.status_code == 403, bad_value_resp.text
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


async def test_user_visu_allows_assigned_non_admin_user(client, auth_headers):
    username = f"visu-user-{uuid.uuid4().hex[:8]}"
    password = "pw-12345678"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username, password)
    dp_id = await _create_dp(client, auth_headers, f"user-visu-dp-{uuid.uuid4().hex[:8]}")
    page_id = await _create_page(client, auth_headers, f"user-visu-page-{uuid.uuid4().hex[:8]}", "user")

    try:
        set_users = await client.put(
            f"/api/v1/visu/nodes/{page_id}/users",
            json={"usernames": [username]},
            headers=auth_headers,
        )
        assert set_users.status_code == 204, set_users.text

        await _save_page_with_single_dp_widget(client, auth_headers, page_id, dp_id)
        await _write_value(client, auth_headers, dp_id, 23.0)

        page_resp = await client.get(
            f"/api/v1/visu/pages/{page_id}",
            headers=user_headers,
        )
        assert page_resp.status_code == 200, page_resp.text

        # Authenticated non-admin user can bootstrap values for visible widgets.
        value_resp = await client.get(
            f"/api/v1/datapoints/{dp_id}/value",
            headers=user_headers,
        )
        assert value_resp.status_code == 200, value_resp.text
        assert value_resp.json()["value"] == pytest.approx(23.0)
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
