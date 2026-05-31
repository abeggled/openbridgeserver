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
