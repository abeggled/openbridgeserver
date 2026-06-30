"""Integration Tests — Adapter Instances API

Covers:
  GET    /api/v1/adapters/instances              list instances
  POST   /api/v1/adapters/instances              create (valid, unregistered type, invalid config)
  GET    /api/v1/adapters/instances/{id}         get (success, 404)
  PATCH  /api/v1/adapters/instances/{id}         update name/enabled/config (success, 404)
  DELETE /api/v1/adapters/instances/{id}         delete (success, 404)
  POST   /api/v1/adapters/instances/{id}/test    connection test
  POST   /api/v1/adapters/instances/{id}/restart restart
  GET    /api/v1/adapters/{type}/schema          config schema
  GET    /api/v1/adapters/{type}/binding-schema  binding schema
  PATCH  /api/v1/adapters/{type}/config          legacy config update
"""

from __future__ import annotations

import uuid

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration

_ADAPTER_TYPE = "ANWESENHEITSSIMULATION"
_MISSING_ID = "00000000-0000-0000-0000-000000000000"


async def _create_instance(client, auth_headers, name: str = "", adapter_type: str = _ADAPTER_TYPE) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": adapter_type, "name": name or f"AdpTest-{uuid.uuid4().hex[:8]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _message_instance_config(target: str = "default") -> dict:
    return {
        "providers": {
            "pushover": {
                "enabled": True,
                "api_token": "app-token",
                "targets": {target: {"user_key": "user-key"}},
            }
        }
    }


async def _create_message_instance(client, auth_headers, config: dict | None = None) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MESSAGE",
            "name": f"MsgInst-{uuid.uuid4().hex[:6]}",
            "config": config or _message_instance_config(),
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_dp(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"AdpMsgDP-{uuid.uuid4().hex[:8]}", "data_type": "FLOAT"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_non_admin_headers(client, auth_headers) -> tuple[str, dict]:
    username = f"adp-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "TestPass123!", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return username, {"Authorization": f"Bearer {create_access_token(username)}"}


# ---------------------------------------------------------------------------
# GET /instances
# ---------------------------------------------------------------------------


async def test_list_instances_requires_auth(client):
    resp = await client.get("/api/v1/adapters/instances")
    assert resp.status_code == 401


async def test_list_instances_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/adapters/instances", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_instances_entry_shape(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.get("/api/v1/adapters/instances", headers=auth_headers)
    ids = {i["id"] for i in resp.json()}
    assert inst["id"] in ids
    entry = next(i for i in resp.json() if i["id"] == inst["id"])
    for field in ("id", "adapter_type", "name", "config", "enabled", "registered", "running", "connected", "bindings"):
        assert field in entry, f"missing field: {field}"


# ---------------------------------------------------------------------------
# POST /instances
# ---------------------------------------------------------------------------


async def test_create_instance_requires_auth(client):
    resp = await client.post("/api/v1/adapters/instances", json={"adapter_type": _ADAPTER_TYPE, "name": "x", "config": {}})
    assert resp.status_code == 401


async def test_create_instance_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            "/api/v1/adapters/instances",
            json={"adapter_type": _ADAPTER_TYPE, "name": "x", "config": {}, "enabled": False},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_create_instance_success(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": _ADAPTER_TYPE, "name": f"AdpCreate-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["adapter_type"] == _ADAPTER_TYPE
    assert body["registered"] is True


async def test_create_instance_unregistered_type_returns_422(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "NONEXISTENT_ADAPTER_XYZ", "name": "bad", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_create_instance_response_shape(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": _ADAPTER_TYPE, "name": f"Shape-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    body = resp.json()
    for field in ("id", "adapter_type", "name", "enabled", "registered", "running", "connected", "bindings", "created_at", "updated_at"):
        assert field in body, f"missing: {field}"


# ---------------------------------------------------------------------------
# GET /instances/{id}
# ---------------------------------------------------------------------------


async def test_get_instance_requires_auth(client):
    resp = await client.get(f"/api/v1/adapters/instances/{_MISSING_ID}")
    assert resp.status_code == 401


async def test_get_instance_success(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.get(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == inst["id"]
    assert resp.json()["adapter_type"] == _ADAPTER_TYPE


async def test_get_instance_404(client, auth_headers):
    resp = await client.get(f"/api/v1/adapters/instances/{_MISSING_ID}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /instances/{id}
# ---------------------------------------------------------------------------


async def test_update_instance_requires_auth(client):
    resp = await client.patch(f"/api/v1/adapters/instances/{_MISSING_ID}", json={"name": "x"})
    assert resp.status_code == 401


async def test_update_instance_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.patch(
            f"/api/v1/adapters/instances/{_MISSING_ID}",
            json={"name": "x"},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_update_instance_404(client, auth_headers):
    resp = await client.patch(f"/api/v1/adapters/instances/{_MISSING_ID}", json={"name": "x"}, headers=auth_headers)
    assert resp.status_code == 404


async def test_update_instance_name(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    new_name = f"Renamed-{uuid.uuid4().hex[:6]}"
    resp = await client.patch(f"/api/v1/adapters/instances/{inst['id']}", json={"name": new_name}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == new_name


async def test_update_instance_enabled(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    assert inst["enabled"] is False
    resp = await client.patch(f"/api/v1/adapters/instances/{inst['id']}", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # Disable it again to keep test state clean
    await client.patch(f"/api/v1/adapters/instances/{inst['id']}", json={"enabled": False}, headers=auth_headers)


async def test_update_instance_config(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.patch(
        f"/api/v1/adapters/instances/{inst['id']}",
        json={"config": {"offset_override": 5}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["config"].get("offset_override") == 5


async def test_update_message_instance_rejects_target_rename_used_by_binding(client, auth_headers):
    inst = await _create_message_instance(client, auth_headers)
    dp = await _create_dp(client, auth_headers)
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
        f"/api/v1/adapters/instances/{inst['id']}",
        json={"config": _message_instance_config(target="renamed")},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "MESSAGE target not configured" in resp.text


# ---------------------------------------------------------------------------
# DELETE /instances/{id}
# ---------------------------------------------------------------------------


async def test_delete_instance_requires_auth(client):
    resp = await client.delete(f"/api/v1/adapters/instances/{_MISSING_ID}")
    assert resp.status_code == 401


async def test_delete_instance_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.delete(f"/api/v1/adapters/instances/{_MISSING_ID}", headers=user_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_delete_instance_404(client, auth_headers):
    resp = await client.delete(f"/api/v1/adapters/instances/{_MISSING_ID}", headers=auth_headers)
    assert resp.status_code == 404


async def test_delete_instance_success(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.delete(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)
    assert resp.status_code == 204

    get_resp = await client.get(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /instances/{id}/test
# ---------------------------------------------------------------------------


async def test_test_instance_requires_auth(client):
    resp = await client.post(f"/api/v1/adapters/instances/{_MISSING_ID}/test")
    assert resp.status_code == 401


async def test_test_instance_404(client, auth_headers):
    resp = await client.post(f"/api/v1/adapters/instances/{_MISSING_ID}/test", headers=auth_headers)
    assert resp.status_code == 404


async def test_test_instance_returns_result(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.post(f"/api/v1/adapters/instances/{inst['id']}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "success" in body
    assert "detail" in body
    # issue #779: response carries a translatable code + params alongside the raw detail
    assert "detail_code" in body
    assert "detail_params" in body
    if body["success"]:
        assert body["detail_code"] == "connectOk"
        assert body["detail_params"] == {"type": _ADAPTER_TYPE}


# ---------------------------------------------------------------------------
# POST /{adapter_type}/test  (type-level, ephemeral)
# ---------------------------------------------------------------------------


async def test_adapter_type_test_requires_auth(client):
    resp = await client.post(f"/api/v1/adapters/{_ADAPTER_TYPE}/test", json={"config": {}})
    assert resp.status_code == 401


async def test_adapter_type_test_unregistered_returns_404(client, auth_headers):
    resp = await client.post("/api/v1/adapters/NOPE/test", json={"config": {}}, headers=auth_headers)
    assert resp.status_code == 404


async def test_adapter_type_test_returns_result_with_code(client, auth_headers):
    resp = await client.post(f"/api/v1/adapters/{_ADAPTER_TYPE}/test", json={"config": {}}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["success"], bool)
    assert "detail_code" in body
    if body["success"]:
        assert body["detail_code"] == "connectOk"
        assert body["detail_params"] == {"type": _ADAPTER_TYPE}


async def test_adapter_type_test_invalid_config_returns_validation_code(client, auth_headers):
    # offset_days expects an int; a list cannot be coerced → config validation error.
    resp = await client.post(
        f"/api/v1/adapters/{_ADAPTER_TYPE}/test",
        json={"config": {"offset_days": [1, 2]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["detail_code"] == "configValidationError"
    assert "error" in body["detail_params"]


# ---------------------------------------------------------------------------
# POST /instances/{id}/restart
# ---------------------------------------------------------------------------


async def test_restart_instance_requires_auth(client):
    resp = await client.post(f"/api/v1/adapters/instances/{_MISSING_ID}/restart")
    assert resp.status_code == 401


async def test_restart_instance_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(f"/api/v1/adapters/instances/{_MISSING_ID}/restart", headers=user_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_restart_instance_404(client, auth_headers):
    resp = await client.post(f"/api/v1/adapters/instances/{_MISSING_ID}/restart", headers=auth_headers)
    assert resp.status_code == 404


async def test_restart_instance_success(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    resp = await client.post(f"/api/v1/adapters/instances/{inst['id']}/restart", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == inst["id"]


# ---------------------------------------------------------------------------
# GET /{type}/schema
# ---------------------------------------------------------------------------


async def test_get_adapter_schema_requires_auth(client):
    resp = await client.get(f"/api/v1/adapters/{_ADAPTER_TYPE}/schema")
    assert resp.status_code == 401


async def test_get_adapter_schema_returns_dict(client, auth_headers):
    resp = await client.get(f"/api/v1/adapters/{_ADAPTER_TYPE}/schema", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_get_adapter_schema_unknown_type_404(client, auth_headers):
    resp = await client.get("/api/v1/adapters/NONEXISTENT_XYZ/schema", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /{type}/binding-schema
# ---------------------------------------------------------------------------


async def test_get_binding_schema_returns_dict(client, auth_headers):
    resp = await client.get(f"/api/v1/adapters/{_ADAPTER_TYPE}/binding-schema", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# PATCH /{type}/config  (legacy config update)
# ---------------------------------------------------------------------------


async def test_update_adapter_config_requires_auth(client):
    resp = await client.patch(f"/api/v1/adapters/{_ADAPTER_TYPE}/config", json={"config": {}, "enabled": True})
    assert resp.status_code == 401


async def test_update_adapter_config_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.patch(
            f"/api/v1/adapters/{_ADAPTER_TYPE}/config",
            json={"config": {}, "enabled": True},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_update_adapter_config_success(client, auth_headers):
    resp = await client.patch(
        f"/api/v1/adapters/{_ADAPTER_TYPE}/config",
        json={"config": {}, "enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["adapter_type"] == _ADAPTER_TYPE
    assert body["enabled"] is True


async def test_update_adapter_config_unknown_type_404(client, auth_headers):
    resp = await client.patch(
        "/api/v1/adapters/NONEXISTENT_XYZ/config",
        json={"config": {}, "enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_migrate_instance_bindings_non_admin_forbidden(client, auth_headers):
    username, user_headers = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            f"/api/v1/adapters/instances/{_MISSING_ID}/bindings/migrate",
            json={"target_instance_id": _MISSING_ID},
            headers=user_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_migrate_message_bindings_rejects_missing_target_on_target_instance(client, auth_headers):
    source = await _create_message_instance(client, auth_headers, config=_message_instance_config(target="default"))
    target = await _create_message_instance(client, auth_headers, config=_message_instance_config(target="other"))
    dp = await _create_dp(client, auth_headers)
    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": source["id"],
            "direction": "SOURCE",
            "config": {"providers": [{"provider": "pushover", "target": "default"}]},
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    resp = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "MESSAGE target not configured" in resp.text


async def test_migrate_message_bindings_prevalidates_before_updates(client, auth_headers):
    source = await _create_message_instance(
        client,
        auth_headers,
        config={
            "providers": {
                "pushover": {
                    "enabled": True,
                    "api_token": "app-token",
                    "targets": {
                        "default": {"user_key": "user-key"},
                        "other": {"user_key": "other-key"},
                    },
                }
            }
        },
    )
    target = await _create_message_instance(client, auth_headers, config=_message_instance_config(target="default"))
    dp_ok = await _create_dp(client, auth_headers)
    dp_bad = await _create_dp(client, auth_headers)
    for dp, target_name in ((dp_ok, "default"), (dp_bad, "other")):
        create_resp = await client.post(
            f"/api/v1/datapoints/{dp['id']}/bindings",
            json={
                "adapter_instance_id": source["id"],
                "direction": "SOURCE",
                "config": {"providers": [{"provider": "pushover", "target": target_name}]},
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, create_resp.text

    resp = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    source_bindings = await client.get(f"/api/v1/adapters/instances/{source['id']}/bindings", headers=auth_headers)
    target_bindings = await client.get(f"/api/v1/adapters/instances/{target['id']}/bindings", headers=auth_headers)
    assert len(source_bindings.json()) == 2
    assert target_bindings.json() == []
