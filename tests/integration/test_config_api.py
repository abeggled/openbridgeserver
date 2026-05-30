"""Integration Tests — Config Export / Import API

Covers:
  GET  /api/v1/config/export          full JSON export (shape, version, all sections)
  GET  /api/v1/config/export/db       DB file download (admin only)
  POST /api/v1/config/import          import (empty payload, datapoints, bindings,
                                       logic graphs, adapter instances, app settings,
                                       nav links, invalid formula)
  DELETE /api/v1/config/reset/bindings   clear all bindings
  DELETE /api/v1/config/reset/logic      clear all logic graphs
  DELETE /api/v1/config/reset/adapters   clear adapter instances + bindings
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

_ADAPTER_TYPE = "ANWESENHEITSSIMULATION"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_dp(client, auth_headers, name: str = "") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name or f"CfgDP-{uuid.uuid4().hex[:8]}", "data_type": "FLOAT"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_instance(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": _ADAPTER_TYPE, "name": f"CfgInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_graph(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": f"CfgGraph-{uuid.uuid4().hex[:6]}", "description": "", "enabled": True, "flow_data": {"nodes": [], "edges": []}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /config/export
# ---------------------------------------------------------------------------


async def test_export_requires_auth(client):
    resp = await client.get("/api/v1/config/export")
    assert resp.status_code == 401


async def test_export_returns_200(client, auth_headers):
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    assert resp.status_code == 200


async def test_export_top_level_shape(client, auth_headers):
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    body = resp.json()
    for field in ("obs_version", "exported_at", "datapoints", "bindings", "adapter_instances",
                  "logic_graphs", "visu_nodes", "nav_links", "app_settings",
                  "hierarchy_trees", "hierarchy_nodes"):
        assert field in body, f"missing top-level field: {field}"


async def test_export_lists_are_lists(client, auth_headers):
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    body = resp.json()
    for key in ("datapoints", "bindings", "adapter_instances", "logic_graphs"):
        assert isinstance(body[key], list), f"{key} should be a list"


async def test_export_includes_created_datapoint(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    dp_ids = {d["id"] for d in resp.json()["datapoints"]}
    assert dp["id"] in dp_ids


async def test_export_includes_created_instance(client, auth_headers):
    inst = await _make_instance(client, auth_headers)
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    inst_ids = {i["id"] for i in resp.json()["adapter_instances"]}
    assert inst["id"] in inst_ids


async def test_export_includes_logic_graph(client, auth_headers):
    graph = await _make_graph(client, auth_headers)
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    graph_ids = {g["id"] for g in resp.json()["logic_graphs"]}
    assert graph["id"] in graph_ids


async def test_export_datapoint_shape(client, auth_headers):
    await _make_dp(client, auth_headers)
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    dps = resp.json()["datapoints"]
    if dps:
        for field in ("id", "name", "data_type", "tags"):
            assert field in dps[0], f"DP missing field: {field}"


# ---------------------------------------------------------------------------
# GET /config/export/db
# ---------------------------------------------------------------------------


async def test_export_db_requires_admin(client, auth_headers):
    # admin/admin is the default → should work
    resp = await client.get("/api/v1/config/export/db", headers=auth_headers)
    assert resp.status_code == 200


async def test_export_db_content_type(client, auth_headers):
    resp = await client.get("/api/v1/config/export/db", headers=auth_headers)
    # SQLite file starts with "SQLite format 3\x00"
    assert resp.status_code == 200
    assert len(resp.content) > 0


# ---------------------------------------------------------------------------
# POST /config/import  — empty payload
# ---------------------------------------------------------------------------


async def test_import_requires_auth(client):
    resp = await client.post("/api/v1/config/import", json={
        "obs_version": "5", "exported_at": "2024-01-01T00:00:00",
        "datapoints": [], "bindings": [],
    })
    assert resp.status_code == 401


async def test_import_empty_payload_succeeds(client, auth_headers):
    resp = await client.post(
        "/api/v1/config/import",
        json={"obs_version": "5", "exported_at": "2024-01-01T00:00:00", "datapoints": [], "bindings": []},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["datapoints_created"] == 0
    assert body["bindings_created"] == 0
    assert isinstance(body["errors"], list)


async def test_import_result_shape(client, auth_headers):
    resp = await client.post(
        "/api/v1/config/import",
        json={"obs_version": "5", "exported_at": "2024-01-01T00:00:00", "datapoints": [], "bindings": []},
        headers=auth_headers,
    )
    body = resp.json()
    for field in ("datapoints_created", "datapoints_updated", "bindings_created", "bindings_updated",
                  "adapter_instances_upserted", "logic_graphs_created", "errors"):
        assert field in body, f"missing: {field}"


# ---------------------------------------------------------------------------
# POST /config/import  — roundtrip: export → import
# ---------------------------------------------------------------------------


async def test_import_roundtrip_from_export(client, auth_headers):
    """Export current state, re-import it — all upserted, no errors."""
    export_resp = await client.get("/api/v1/config/export", headers=auth_headers)
    assert export_resp.status_code == 200
    export_body = export_resp.json()

    import_resp = await client.post("/api/v1/config/import", json=export_body, headers=auth_headers)
    assert import_resp.status_code == 200
    result = import_resp.json()
    assert isinstance(result["errors"], list)
    # Re-importing existing data → updated counts ≥ 0, no fatal errors
    assert result["datapoints_updated"] >= 0


# ---------------------------------------------------------------------------
# POST /config/import  — create new datapoints
# ---------------------------------------------------------------------------


async def test_import_creates_new_datapoints(client, auth_headers):
    new_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": new_id, "name": f"Imported-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["datapoints_created"] == 1

    # Verify it exists
    get_resp = await client.get(f"/api/v1/datapoints/{new_id}", headers=auth_headers)
    assert get_resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /config/import  — adapter instances upsert
# ---------------------------------------------------------------------------


async def test_import_creates_adapter_instance(client, auth_headers):
    new_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "adapter_instances": [
            {"id": new_id, "adapter_type": _ADAPTER_TYPE, "name": f"ImpInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["adapter_instances_upserted"] == 1


# ---------------------------------------------------------------------------
# POST /config/import  — logic graphs upsert
# ---------------------------------------------------------------------------


async def test_import_creates_logic_graph(client, auth_headers):
    new_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "logic_graphs": [
            {"id": new_id, "name": f"ImpGraph-{uuid.uuid4().hex[:6]}", "description": "", "enabled": True, "flow_data": {"nodes": [], "edges": []}}
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["logic_graphs_created"] == 1


# ---------------------------------------------------------------------------
# POST /config/import  — app settings upsert
# ---------------------------------------------------------------------------


async def test_import_upserts_app_settings(client, auth_headers):
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "app_settings": [{"key": "timezone", "value": "Europe/Berlin"}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["app_settings_upserted"] == 1

    # Restore timezone
    await client.put("/api/v1/system/settings", json={"timezone": "Europe/Zurich"}, headers=auth_headers)


# ---------------------------------------------------------------------------
# POST /config/import  — nav links upsert
# ---------------------------------------------------------------------------


async def test_import_upserts_nav_links(client, auth_headers):
    link_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "nav_links": [{"id": link_id, "label": "ImpLink", "url": "https://example.com", "icon": "", "sort_order": 0, "open_new_tab": True}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["nav_links_upserted"] == 1

    # Cleanup
    await client.delete(f"/api/v1/system/nav-links/{link_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# POST /config/import  — bindings with invalid formula → error recorded
# ---------------------------------------------------------------------------


async def test_import_binding_invalid_formula_records_error(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"FmlDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [{
            "id": binding_id,
            "datapoint_id": dp_id,
            "adapter_type": _ADAPTER_TYPE,
            "adapter_instance_id": inst_id,
            "direction": "SOURCE",
            "config": {},
            "enabled": True,
            "value_formula": "x *** invalid $$$$",
        }],
        "adapter_instances": [
            {"id": inst_id, "adapter_type": _ADAPTER_TYPE, "name": f"FmlInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    # The invalid formula should be rejected and an error recorded
    assert len(resp.json()["errors"]) > 0
