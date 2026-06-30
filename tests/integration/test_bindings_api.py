"""Integration Tests — Bindings API

Covers:
  GET    /api/v1/datapoints/{dp_id}/bindings             (404 dp, success)
  POST   /api/v1/datapoints/{dp_id}/bindings             (404 dp, 422 invalid instance, success)
  PATCH  /api/v1/datapoints/{dp_id}/bindings/{bid}       (404, success)
  DELETE /api/v1/datapoints/{dp_id}/bindings/{bid}       (404, success)
"""

from __future__ import annotations

import uuid

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration

_MISSING_ID = "00000000-0000-0000-0000-000000000000"


async def _create_dp(client, auth_headers, suffix: str = "") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={
            "name": f"BindingsTest-{suffix or uuid.uuid4().hex[:8]}",
            "data_type": "FLOAT",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_instance(client, auth_headers, name: str = "") -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "ANWESENHEITSSIMULATION",
            "name": name or f"BindTest-{uuid.uuid4().hex[:6]}",
            "config": {},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _message_instance_config() -> dict:
    return {
        "providers": {
            "pushover": {
                "enabled": True,
                "api_token": "app-token",
                "targets": {"default": {"user_key": "user-key"}},
            }
        }
    }


async def _create_message_instance(client, auth_headers, name: str = "", config: dict | None = None) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MESSAGE",
            "name": name or f"MsgBindTest-{uuid.uuid4().hex[:6]}",
            "config": config or {},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_non_admin_headers(client, auth_headers) -> tuple[str, dict]:
    username = f"bind-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "TestPass123!", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return username, {"Authorization": f"Bearer {create_access_token(username)}"}


# ---------------------------------------------------------------------------
# GET /{dp_id}/bindings
# ---------------------------------------------------------------------------


async def test_list_bindings_requires_auth(client):
    resp = await client.get(f"/api/v1/datapoints/{_MISSING_ID}/bindings")
    assert resp.status_code == 401


async def test_list_bindings_404_for_unknown_dp(client, auth_headers):
    resp = await client.get(f"/api/v1/datapoints/{_MISSING_ID}/bindings", headers=auth_headers)
    assert resp.status_code == 404


async def test_list_bindings_empty_for_new_dp(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.get(f"/api/v1/datapoints/{dp['id']}/bindings", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_bindings_shows_created_binding(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )

    resp = await client.get(f"/api/v1/datapoints/{dp['id']}/bindings", headers=auth_headers)
    assert resp.status_code == 200
    bindings = resp.json()
    assert len(bindings) == 1
    assert bindings[0]["adapter_type"] == "ANWESENHEITSSIMULATION"
    assert bindings[0]["direction"] == "SOURCE"


# ---------------------------------------------------------------------------
# POST /{dp_id}/bindings
# ---------------------------------------------------------------------------


async def test_create_binding_requires_auth(client):
    resp = await client.post(
        f"/api/v1/datapoints/{_MISSING_ID}/bindings",
        json={"adapter_instance_id": _MISSING_ID, "direction": "SOURCE", "config": {}},
    )
    assert resp.status_code == 401


async def test_create_binding_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            f"/api/v1/datapoints/{_MISSING_ID}/bindings",
            json={"adapter_instance_id": _MISSING_ID, "direction": "SOURCE", "config": {}},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_create_binding_404_for_unknown_dp(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{_MISSING_ID}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_create_binding_422_for_unknown_instance(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": _MISSING_ID, "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_create_binding_success(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "DEST", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["adapter_type"] == "ANWESENHEITSSIMULATION"
    assert body["direction"] == "DEST"
    assert body["datapoint_id"] == dp["id"]
    assert body["instance_name"] == inst["name"]


async def test_create_binding_response_shape(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    body = resp.json()
    for field in ("id", "datapoint_id", "adapter_type", "direction", "config", "enabled", "created_at", "updated_at"):
        assert field in body, f"missing field: {field}"


async def test_create_binding_with_formula(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {},
            "value_formula": "x * 2",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["value_formula"] == "x * 2"


async def test_create_disabled_message_binding_allows_no_targets(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_message_instance(client, auth_headers)

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {"providers": []},
            "enabled": False,
        },
        headers=auth_headers,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["enabled"] is False


async def test_create_message_binding_rejects_unknown_instance_target(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_message_instance(client, auth_headers, config=_message_instance_config())

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {"providers": [{"provider": "pushover", "target": "missing"}]},
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "MESSAGE target not configured" in resp.text


async def test_create_message_binding_uses_parsed_provider_enabled_flag(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_message_instance(
        client,
        auth_headers,
        config={
            "providers": {
                "pushover": {
                    "enabled": "false",
                    "api_token": "app-token",
                    "targets": {"default": {"user_key": "user-key"}},
                }
            }
        },
    )

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {"providers": [{"provider": "pushover", "target": "default"}]},
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "MESSAGE provider is disabled" in resp.text


async def test_create_message_binding_rejects_blank_message_body(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_message_instance(client, auth_headers, config=_message_instance_config())

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {
                "message": "   ",
                "providers": [{"provider": "pushover", "target": "default"}],
            },
        },
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "message must not be empty" in resp.text


async def test_create_binding_invalid_formula_returns_422(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {},
            "value_formula": "x *** invalid $$",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /{dp_id}/bindings/{binding_id}
# ---------------------------------------------------------------------------


async def test_update_binding_requires_auth(client):
    resp = await client.patch(
        f"/api/v1/datapoints/{_MISSING_ID}/bindings/{_MISSING_ID}",
        json={"enabled": False},
    )
    assert resp.status_code == 401


async def test_update_binding_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.patch(
            f"/api/v1/datapoints/{_MISSING_ID}/bindings/{_MISSING_ID}",
            json={"enabled": False},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_update_binding_404_for_unknown(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}/bindings/{_MISSING_ID}",
        json={"enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_update_binding_success(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    binding_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}/bindings/{binding_id}",
        json={"enabled": False, "direction": "BOTH"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["direction"] == "BOTH"


async def test_update_disabled_message_binding_allows_no_targets(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_message_instance(client, auth_headers, config=_message_instance_config())
    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": inst["id"],
            "direction": "SOURCE",
            "config": {"providers": [{"provider": "pushover", "target": "default"}]},
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}/bindings/{create_resp.json()['id']}",
        json={"enabled": False, "config": {"providers": []}},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
    assert resp.json()["config"] == {"providers": []}


async def test_update_binding_formula(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    binding_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}/bindings/{binding_id}",
        json={"value_formula": "x / 10"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["value_formula"] == "x / 10"


async def test_update_binding_invalid_formula_returns_422(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    binding_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}/bindings/{binding_id}",
        json={"value_formula": "x *** invalid $$"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /{dp_id}/bindings/{binding_id}
# ---------------------------------------------------------------------------


async def test_delete_binding_requires_auth(client):
    resp = await client.delete(f"/api/v1/datapoints/{_MISSING_ID}/bindings/{_MISSING_ID}")
    assert resp.status_code == 401


async def test_delete_binding_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.delete(
            f"/api/v1/datapoints/{_MISSING_ID}/bindings/{_MISSING_ID}",
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_delete_binding_404_for_unknown(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.delete(
        f"/api/v1/datapoints/{dp['id']}/bindings/{_MISSING_ID}",
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_delete_binding_success(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)

    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={"adapter_instance_id": inst["id"], "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    binding_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/v1/datapoints/{dp['id']}/bindings/{binding_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 204

    list_resp = await client.get(f"/api/v1/datapoints/{dp['id']}/bindings", headers=auth_headers)
    ids = [b["id"] for b in list_resp.json()]
    assert binding_id not in ids
