"""Integration Tests — Logic Graphs API

Covers:
  GET    /api/v1/logic/graphs                list + entry shape
  POST   /api/v1/logic/graphs                create (success, defaults)
  GET    /api/v1/logic/graphs/{id}           get (success, 404)
  PUT    /api/v1/logic/graphs/{id}           full replace (success, 404)
  PATCH  /api/v1/logic/graphs/{id}           partial update (name, enabled, description, flow_data)
  DELETE /api/v1/logic/graphs/{id}           delete (success, 404)
  POST   /api/v1/logic/graphs/{id}/run       run (empty flow, disabled 422, 404)
  POST   /api/v1/logic/graphs/{id}/duplicate duplicate (success, 404)
  GET    /api/v1/logic/graphs/{id}/export    export JSON download (success, 404)
  POST   /api/v1/logic/graphs/import         import from JSON (success, bad format)
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

_MISSING_ID = "00000000-0000-0000-0000-000000000000"
_EMPTY_FLOW = {"nodes": [], "edges": []}


async def _create_graph(client, auth_headers, name: str = "", enabled: bool = True) -> dict:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={
            "name": name or f"LG-{uuid.uuid4().hex[:8]}",
            "description": "test",
            "enabled": enabled,
            "flow_data": _EMPTY_FLOW,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /logic/graphs
# ---------------------------------------------------------------------------


async def test_list_graphs_requires_auth(client):
    resp = await client.get("/api/v1/logic/graphs")
    assert resp.status_code == 401


async def test_list_graphs_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/logic/graphs", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_graphs_includes_created(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    resp = await client.get("/api/v1/logic/graphs", headers=auth_headers)
    ids = {g["id"] for g in resp.json()}
    assert graph["id"] in ids


# ---------------------------------------------------------------------------
# POST /logic/graphs
# ---------------------------------------------------------------------------


async def test_create_graph_requires_auth(client):
    resp = await client.post("/api/v1/logic/graphs", json={"name": "x", "flow_data": _EMPTY_FLOW})
    assert resp.status_code == 401


async def test_create_graph_success(client, auth_headers):
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": f"LG-Create-{uuid.uuid4().hex[:6]}", "description": "desc", "enabled": True, "flow_data": _EMPTY_FLOW},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["enabled"] is True
    assert "id" in body
    assert "flow_data" in body


async def test_create_graph_response_shape(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    for field in ("id", "name", "description", "enabled", "flow_data", "created_at", "updated_at"):
        assert field in graph, f"missing: {field}"


async def test_create_graph_default_enabled_true(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    assert graph["enabled"] is True


async def test_create_graph_disabled(client, auth_headers):
    graph = await _create_graph(client, auth_headers, enabled=False)
    assert graph["enabled"] is False


# ---------------------------------------------------------------------------
# GET /logic/graphs/{id}
# ---------------------------------------------------------------------------


async def test_get_graph_requires_auth(client):
    resp = await client.get(f"/api/v1/logic/graphs/{_MISSING_ID}")
    assert resp.status_code == 401


async def test_get_graph_success(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == graph["id"]


async def test_get_graph_404(client, auth_headers):
    resp = await client.get(f"/api/v1/logic/graphs/{_MISSING_ID}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /logic/graphs/{id}  (full replace)
# ---------------------------------------------------------------------------


async def test_full_update_graph_requires_auth(client):
    resp = await client.put(f"/api/v1/logic/graphs/{_MISSING_ID}", json={"name": "x", "flow_data": _EMPTY_FLOW})
    assert resp.status_code == 401


async def test_full_update_graph_404(client, auth_headers):
    resp = await client.put(
        f"/api/v1/logic/graphs/{_MISSING_ID}",
        json={"name": "x", "description": "", "enabled": True, "flow_data": _EMPTY_FLOW},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_full_update_graph_success(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    new_name = f"Updated-{uuid.uuid4().hex[:6]}"
    resp = await client.put(
        f"/api/v1/logic/graphs/{graph['id']}",
        json={"name": new_name, "description": "updated desc", "enabled": False, "flow_data": _EMPTY_FLOW},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == new_name
    assert body["enabled"] is False
    assert body["description"] == "updated desc"


# ---------------------------------------------------------------------------
# PATCH /logic/graphs/{id}  (partial update)
# ---------------------------------------------------------------------------


async def test_partial_update_graph_requires_auth(client):
    resp = await client.patch(f"/api/v1/logic/graphs/{_MISSING_ID}", json={"enabled": False})
    assert resp.status_code == 401


async def test_partial_update_graph_404(client, auth_headers):
    resp = await client.patch(f"/api/v1/logic/graphs/{_MISSING_ID}", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 404


async def test_partial_update_graph_name(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    new_name = f"Patched-{uuid.uuid4().hex[:6]}"
    resp = await client.patch(f"/api/v1/logic/graphs/{graph['id']}", json={"name": new_name}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == new_name


async def test_partial_update_graph_enabled_false(client, auth_headers):
    graph = await _create_graph(client, auth_headers, enabled=True)
    resp = await client.patch(f"/api/v1/logic/graphs/{graph['id']}", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_partial_update_graph_description(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    resp = await client.patch(f"/api/v1/logic/graphs/{graph['id']}", json={"description": "new desc"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["description"] == "new desc"


async def test_partial_update_graph_persists(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    new_name = f"Persisted-{uuid.uuid4().hex[:6]}"
    await client.patch(f"/api/v1/logic/graphs/{graph['id']}", json={"name": new_name}, headers=auth_headers)
    resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)
    assert resp.json()["name"] == new_name


# ---------------------------------------------------------------------------
# DELETE /logic/graphs/{id}
# ---------------------------------------------------------------------------


async def test_delete_graph_requires_auth(client):
    resp = await client.delete(f"/api/v1/logic/graphs/{_MISSING_ID}")
    assert resp.status_code == 401


async def test_delete_graph_404(client, auth_headers):
    resp = await client.delete(f"/api/v1/logic/graphs/{_MISSING_ID}", headers=auth_headers)
    assert resp.status_code == 404


async def test_delete_graph_success(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    resp = await client.delete(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)
    assert resp.status_code == 204
    get_resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /logic/graphs/{id}/run
# ---------------------------------------------------------------------------


async def test_run_graph_requires_auth(client):
    resp = await client.post(f"/api/v1/logic/graphs/{_MISSING_ID}/run")
    assert resp.status_code == 401


async def test_run_graph_404(client, auth_headers):
    resp = await client.post(f"/api/v1/logic/graphs/{_MISSING_ID}/run", headers=auth_headers)
    assert resp.status_code == 404


async def test_run_graph_empty_flow_returns_ok(client, auth_headers):
    graph = await _create_graph(client, auth_headers)
    resp = await client.post(f"/api/v1/logic/graphs/{graph['id']}/run", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_run_disabled_graph_returns_422(client, auth_headers):
    graph = await _create_graph(client, auth_headers, enabled=False)
    resp = await client.post(f"/api/v1/logic/graphs/{graph['id']}/run", headers=auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /logic/graphs/{id}/duplicate
# ---------------------------------------------------------------------------


async def test_duplicate_graph_requires_auth(client):
    resp = await client.post(f"/api/v1/logic/graphs/{_MISSING_ID}/duplicate")
    assert resp.status_code == 401


async def test_duplicate_graph_404(client, auth_headers):
    resp = await client.post(f"/api/v1/logic/graphs/{_MISSING_ID}/duplicate", headers=auth_headers)
    assert resp.status_code == 404


async def test_duplicate_graph_success(client, auth_headers):
    original = await _create_graph(client, auth_headers, name=f"Original-{uuid.uuid4().hex[:6]}")
    resp = await client.post(f"/api/v1/logic/graphs/{original['id']}/duplicate", headers=auth_headers)
    assert resp.status_code == 201
    copy = resp.json()
    assert copy["id"] != original["id"]
    assert original["name"] in copy["name"]


async def test_duplicate_graph_new_id(client, auth_headers):
    original = await _create_graph(client, auth_headers)
    resp = await client.post(f"/api/v1/logic/graphs/{original['id']}/duplicate", headers=auth_headers)
    copy = resp.json()
    graphs = (await client.get("/api/v1/logic/graphs", headers=auth_headers)).json()
    ids = {g["id"] for g in graphs}
    assert copy["id"] in ids
    assert original["id"] in ids


# ---------------------------------------------------------------------------
# GET /logic/graphs/{id}/export
# ---------------------------------------------------------------------------


async def test_export_graph_requires_auth(client):
    resp = await client.get(f"/api/v1/logic/graphs/{_MISSING_ID}/export")
    assert resp.status_code == 401


async def test_export_graph_404(client, auth_headers):
    resp = await client.get(f"/api/v1/logic/graphs/{_MISSING_ID}/export", headers=auth_headers)
    assert resp.status_code == 404


async def test_export_graph_success(client, auth_headers):
    graph = await _create_graph(client, auth_headers, name=f"Export-{uuid.uuid4().hex[:6]}")
    resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}/export", headers=auth_headers)
    assert resp.status_code == 200
    assert "content-disposition" in {k.lower() for k in resp.headers}


async def test_export_graph_body_shape(client, auth_headers):
    graph = await _create_graph(client, auth_headers, name=f"Shape-{uuid.uuid4().hex[:6]}")
    resp = await client.get(f"/api/v1/logic/graphs/{graph['id']}/export", headers=auth_headers)
    body = resp.json()
    assert body["obs_export"] == "logic_graph"
    assert body["name"] == graph["name"]
    assert "flow_data" in body
    assert "enabled" in body


# ---------------------------------------------------------------------------
# POST /logic/graphs/import
# ---------------------------------------------------------------------------


async def test_import_graph_bad_format_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/logic/graphs/import",
        json={
            "obs_export": "not_a_logic_graph",
            "version": 1,
            "name": "bad",
            "description": "",
            "enabled": True,
            "flow_data": _EMPTY_FLOW,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_import_graph_success(client, auth_headers):
    original = await _create_graph(client, auth_headers, name=f"ToImport-{uuid.uuid4().hex[:6]}")
    export_resp = await client.get(f"/api/v1/logic/graphs/{original['id']}/export", headers=auth_headers)
    export_body = export_resp.json()

    resp = await client.post("/api/v1/logic/graphs/import", json=export_body, headers=auth_headers)
    assert resp.status_code == 201
    imported = resp.json()
    assert imported["name"] == original["name"]
    assert imported["id"] != original["id"]
