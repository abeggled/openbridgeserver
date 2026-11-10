"""Binding delegation is for user principals only (issue #1303).

``docs/authz-creation-authority.md`` restricts adapter-owned binding operations
to user principals.  These tests drive the real HTTP routes with an API key
(``X-API-Key``) and with a non-admin user, both holding ``operator`` grants on
the DataPoint and on the adapter instance:

* the API key is refused (403) on create, update and delete for every adapter
  type, whether or not the adapter declares ``LINK_BINDING``;
* the user keeps access exactly where the adapter declares ``LINK_BINDING``
  (MQTT, WEBHOOK) and is refused where it does not (KNX).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from obs.api.auth import create_access_token, generate_api_key, hash_api_key
from obs.db.database import get_db

pytestmark = pytest.mark.integration


def _binding_config(adapter_type: str) -> dict:
    if adapter_type == "MQTT":
        return {"topic": f"delegation/{uuid.uuid4().hex[:8]}"}
    if adapter_type == "KNX":
        return {"group_address": "1/2/3", "dpt_id": "DPT1.001"}
    if adapter_type == "WEBHOOK":
        return {"slug": f"delegation-{uuid.uuid4().hex[:8]}"}
    raise AssertionError(adapter_type)


async def _create_dp(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"Delegation-{uuid.uuid4().hex[:8]}", "data_type": "BOOLEAN"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_instance(client, auth_headers, adapter_type: str) -> str:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": adapter_type, "name": f"deleg-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _grant_operator(client, auth_headers, principal_type: str, principal_id: str, dp_id: str, instance_id: str) -> None:
    path = f"/api/v1/authz/principals/{principal_type}/{principal_id}/grants"
    state = await client.get(path, headers=auth_headers)
    assert state.status_code == 200, state.text
    granted = await client.put(
        path,
        json={
            "grants": [
                {"node_type": "datapoint", "node_id": dp_id, "role": "operator", "effect": "allow"},
                {"node_type": "adapter_instance", "node_id": instance_id, "role": "operator", "effect": "allow"},
            ]
        },
        headers={**auth_headers, "If-Match": state.headers["etag"]},
    )
    assert granted.status_code == 200, granted.text


async def _api_key_headers(client, auth_headers, dp_id: str, instance_id: str) -> dict:
    # Stored directly rather than via POST /auth/apikeys: that route is limited to
    # 10/minute per client for the whole session, and these parametrized tests
    # would use up the budget of the integration tests that run after them.
    key = generate_api_key()
    key_id = str(uuid.uuid4())
    await get_db().execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?,?,?,?,?)",
        (key_id, f"deleg-{uuid.uuid4().hex[:8]}", hash_api_key(key), "admin", datetime.now(UTC).isoformat()),
    )
    await _grant_operator(client, auth_headers, "api_key", key_id, dp_id, instance_id)
    return {"X-API-Key": key}


async def _user_headers(client, auth_headers, dp_id: str, instance_id: str) -> tuple[str, dict]:
    username = f"deleg-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "TestPass123!", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    await _grant_operator(client, auth_headers, "user", username, dp_id, instance_id)
    return username, {"Authorization": f"Bearer {create_access_token(username)}"}


async def _admin_binding(client, auth_headers, dp_id: str, instance_id: str, adapter_type: str) -> str:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/bindings",
        json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": _binding_config(adapter_type)},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _binding_ids(client, auth_headers, dp_id: str) -> set[str]:
    resp = await client.get(f"/api/v1/datapoints/{dp_id}/bindings", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return {row["id"] for row in resp.json()}


@pytest.mark.parametrize("adapter_type", ["MQTT", "KNX", "WEBHOOK"])
async def test_api_key_with_operator_grants_cannot_create_update_or_delete_bindings(client, auth_headers, adapter_type):
    dp_id = await _create_dp(client, auth_headers)
    instance_id = await _create_instance(client, auth_headers, adapter_type)
    try:
        key_headers = await _api_key_headers(client, auth_headers, dp_id, instance_id)
        existing = await _admin_binding(client, auth_headers, dp_id, instance_id, adapter_type)

        created = await client.post(
            f"/api/v1/datapoints/{dp_id}/bindings",
            json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": _binding_config(adapter_type)},
            headers=key_headers,
        )
        assert created.status_code == 403, created.text

        updated = await client.patch(
            f"/api/v1/datapoints/{dp_id}/bindings/{existing}",
            json={"enabled": False},
            headers=key_headers,
        )
        assert updated.status_code == 403, updated.text

        deleted = await client.delete(f"/api/v1/datapoints/{dp_id}/bindings/{existing}", headers=key_headers)
        assert deleted.status_code == 403, deleted.text

        assert await _binding_ids(client, auth_headers, dp_id) == {existing}
    finally:
        await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


@pytest.mark.parametrize("adapter_type", ["MQTT", "WEBHOOK"])
async def test_user_with_operator_grants_manages_bindings_of_declared_adapters(client, auth_headers, adapter_type):
    dp_id = await _create_dp(client, auth_headers)
    instance_id = await _create_instance(client, auth_headers, adapter_type)
    username, user_headers = await _user_headers(client, auth_headers, dp_id, instance_id)
    try:
        created = await client.post(
            f"/api/v1/datapoints/{dp_id}/bindings",
            json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": _binding_config(adapter_type)},
            headers=user_headers,
        )
        assert created.status_code == 201, created.text
        binding_id = created.json()["id"]

        updated = await client.patch(
            f"/api/v1/datapoints/{dp_id}/bindings/{binding_id}",
            json={"enabled": False},
            headers=user_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["enabled"] is False

        deleted = await client.delete(f"/api/v1/datapoints/{dp_id}/bindings/{binding_id}", headers=user_headers)
        assert deleted.status_code == 204, deleted.text
        assert await _binding_ids(client, auth_headers, dp_id) == set()
    finally:
        await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_user_with_operator_grants_cannot_manage_knx_bindings(client, auth_headers):
    dp_id = await _create_dp(client, auth_headers)
    instance_id = await _create_instance(client, auth_headers, "KNX")
    username, user_headers = await _user_headers(client, auth_headers, dp_id, instance_id)
    try:
        existing = await _admin_binding(client, auth_headers, dp_id, instance_id, "KNX")

        created = await client.post(
            f"/api/v1/datapoints/{dp_id}/bindings",
            json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": _binding_config("KNX")},
            headers=user_headers,
        )
        assert created.status_code == 403, created.text
        updated = await client.patch(f"/api/v1/datapoints/{dp_id}/bindings/{existing}", json={"enabled": False}, headers=user_headers)
        assert updated.status_code == 403, updated.text
        deleted = await client.delete(f"/api/v1/datapoints/{dp_id}/bindings/{existing}", headers=user_headers)
        assert deleted.status_code == 403, deleted.text
    finally:
        await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
