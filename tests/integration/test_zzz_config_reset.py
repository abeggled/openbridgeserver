"""Integration Tests — Config Reset / Clear Endpoints

These tests MUST run last (filename starts with zzz) because the endpoints
are destructive: they wipe all bindings, datapoints, logic graphs, adapter
instances, or everything at once.  All prior tests will have completed before
this file runs.

Covers:
  DELETE /api/v1/config/reset/bindings    clear all bindings
  DELETE /api/v1/config/reset/logic       clear all logic graphs
  DELETE /api/v1/config/reset/adapters    clear all adapter instances + bindings
  DELETE /api/v1/config/reset/datapoints  clear all datapoints + bindings
  DELETE /api/v1/config/reset             factory reset (runs last)
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

_ADAPTER_TYPE = "ANWESENHEITSSIMULATION"


async def _create_dp(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"ResetDP-{uuid.uuid4().hex[:8]}", "data_type": "FLOAT"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_instance(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": _ADAPTER_TYPE,
            "name": f"ResetInst-{uuid.uuid4().hex[:8]}",
            "config": {},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_binding(client, auth_headers, dp_id: str, inst_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/bindings",
        json={"adapter_instance_id": inst_id, "direction": "SOURCE", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_graph(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": f"ResetGraph-{uuid.uuid4().hex[:8]}", "description": "", "enabled": True, "flow_data": {"nodes": [], "edges": []}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# DELETE /config/reset/bindings
# ---------------------------------------------------------------------------


async def test_clear_bindings_requires_admin(client):
    resp = await client.delete("/api/v1/config/reset/bindings")
    assert resp.status_code == 401


async def test_clear_bindings_success(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    inst = await _create_instance(client, auth_headers)
    await _create_binding(client, auth_headers, dp["id"], inst["id"])

    resp = await client.delete("/api/v1/config/reset/bindings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "deleted" in body
    assert body["deleted"] >= 1
    assert body["errors"] == []


async def test_clear_bindings_result_shape(client, auth_headers):
    resp = await client.delete("/api/v1/config/reset/bindings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "deleted" in body
    assert "errors" in body


async def test_clear_bindings_leaves_datapoints_intact(client, auth_headers):
    dp = await _create_dp(client, auth_headers)

    await client.delete("/api/v1/config/reset/bindings", headers=auth_headers)

    get_resp = await client.get(f"/api/v1/datapoints/{dp['id']}", headers=auth_headers)
    assert get_resp.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /config/reset/logic
# ---------------------------------------------------------------------------


async def test_clear_logic_requires_admin(client):
    resp = await client.delete("/api/v1/config/reset/logic")
    assert resp.status_code == 401


async def test_clear_logic_success(client, auth_headers):
    graph = await _create_graph(client, auth_headers)

    resp = await client.delete("/api/v1/config/reset/logic", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] >= 1
    assert body["errors"] == []

    get_resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)
    assert get_resp.status_code == 404


async def test_clear_logic_result_shape(client, auth_headers):
    resp = await client.delete("/api/v1/config/reset/logic", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "deleted" in body
    assert "errors" in body


# ---------------------------------------------------------------------------
# DELETE /config/reset/adapters
# ---------------------------------------------------------------------------


async def test_clear_adapters_requires_admin(client):
    resp = await client.delete("/api/v1/config/reset/adapters")
    assert resp.status_code == 401


async def test_clear_adapters_success(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    dp = await _create_dp(client, auth_headers)
    await _create_binding(client, auth_headers, dp["id"], inst["id"])

    resp = await client.delete("/api/v1/config/reset/adapters", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] >= 1
    assert body["errors"] == []

    get_resp = await client.get(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)
    assert get_resp.status_code == 404


async def test_clear_adapters_also_removes_bindings(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    dp = await _create_dp(client, auth_headers)
    await _create_binding(client, auth_headers, dp["id"], inst["id"])

    resp = await client.delete("/api/v1/config/reset/adapters", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["bindings_deleted"] >= 1


# ---------------------------------------------------------------------------
# DELETE /config/reset/datapoints
# ---------------------------------------------------------------------------


async def test_clear_datapoints_requires_admin(client):
    resp = await client.delete("/api/v1/config/reset/datapoints")
    assert resp.status_code == 401


async def test_clear_datapoints_success(client, auth_headers):
    dp = await _create_dp(client, auth_headers)

    resp = await client.delete("/api/v1/config/reset/datapoints", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] >= 1
    assert body["errors"] == []

    get_resp = await client.get(f"/api/v1/datapoints/{dp['id']}", headers=auth_headers)
    assert get_resp.status_code == 404


async def test_clear_datapoints_result_shape(client, auth_headers):
    resp = await client.delete("/api/v1/config/reset/datapoints", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "deleted" in body
    assert "bindings_deleted" in body
    assert "errors" in body


# ---------------------------------------------------------------------------
# DELETE /config/reset  (factory reset — runs absolutely last)
# ---------------------------------------------------------------------------


async def test_factory_reset_requires_admin(client):
    resp = await client.delete("/api/v1/config/reset")
    assert resp.status_code == 401


async def test_factory_reset_success(client, auth_headers):
    resp = await client.delete("/api/v1/config/reset", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "datapoints_deleted" in body
    assert "bindings_deleted" in body
    assert "adapter_instances_deleted" in body
    assert "logic_graphs_deleted" in body
    assert "errors" in body


async def test_factory_reset_leaves_db_empty(client, auth_headers):
    await client.delete("/api/v1/config/reset", headers=auth_headers)

    dp_resp = await client.get("/api/v1/datapoints/", headers=auth_headers)
    assert dp_resp.status_code == 200
    assert dp_resp.json()["total"] == 0

    inst_resp = await client.get("/api/v1/adapters/instances", headers=auth_headers)
    assert inst_resp.json() == []

    ga_resp = await client.get("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert ga_resp.json()["total"] == 0
