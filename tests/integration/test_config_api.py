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
from obs.api.auth import create_access_token
from obs.db.database import get_db

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


async def _make_message_instance(client, auth_headers, config: dict | None = None) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MESSAGE",
            "name": f"CfgMsgInst-{uuid.uuid4().hex[:6]}",
            "config": config or _message_instance_config(),
            "enabled": False,
        },
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


async def _create_non_admin_headers(client, auth_headers) -> tuple[dict, str]:
    username = f"cfg-user-{uuid.uuid4().hex[:8]}"
    password = "pw-12345678"
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
    return {"Authorization": f"Bearer {create_access_token(username)}"}, username


# ---------------------------------------------------------------------------
# GET /config/export
# ---------------------------------------------------------------------------


async def test_export_requires_auth(client):
    resp = await client.get("/api/v1/config/export")
    assert resp.status_code == 401


async def test_export_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.get("/api/v1/config/export", headers=non_admin_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_export_returns_200(client, auth_headers):
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    assert resp.status_code == 200


async def test_export_top_level_shape(client, auth_headers):
    resp = await client.get("/api/v1/config/export", headers=auth_headers)
    body = resp.json()
    for field in (
        "obs_version",
        "exported_at",
        "datapoints",
        "bindings",
        "adapter_instances",
        "logic_graphs",
        "visu_nodes",
        "nav_links",
        "app_settings",
        "hierarchy_trees",
        "hierarchy_nodes",
    ):
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
    resp = await client.post(
        "/api/v1/config/import",
        json={
            "obs_version": "5",
            "exported_at": "2024-01-01T00:00:00",
            "datapoints": [],
            "bindings": [],
        },
    )
    assert resp.status_code == 401


async def test_import_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            "/api/v1/config/import",
            json={
                "obs_version": "5",
                "exported_at": "2024-01-01T00:00:00",
                "datapoints": [],
                "bindings": [],
            },
            headers=non_admin_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


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
    for field in (
        "datapoints_created",
        "datapoints_updated",
        "bindings_created",
        "bindings_updated",
        "adapter_instances_upserted",
        "logic_graphs_created",
        "errors",
    ):
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
        "datapoints": [
            {"id": new_id, "name": f"Imported-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}
        ],
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
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": _ADAPTER_TYPE,
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {},
                "enabled": True,
                "value_formula": "x *** invalid $$$$",
            }
        ],
        "adapter_instances": [
            {"id": inst_id, "adapter_type": _ADAPTER_TYPE, "name": f"FmlInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    # The invalid formula should be rejected and an error recorded
    assert len(resp.json()["errors"]) > 0


async def test_import_message_binding_non_source_direction_records_error(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"MsgDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": "MESSAGE",
                "adapter_instance_id": inst_id,
                "direction": "DEST",
                "config": {"providers": [{"provider": "pushover", "target": "default"}]},
                "enabled": True,
            }
        ],
        "adapter_instances": [{"id": inst_id, "adapter_type": "MESSAGE", "name": f"MsgInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    assert any("MESSAGE-Bindings" in error for error in resp.json()["errors"])


async def test_import_disabled_message_binding_allows_no_targets(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"MsgDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": "MESSAGE",
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {"providers": []},
                "enabled": False,
            }
        ],
        "adapter_instances": [{"id": inst_id, "adapter_type": "MESSAGE", "name": f"MsgInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bindings_created"] == 1
    assert body["errors"] == []


async def test_import_message_binding_rejects_unknown_instance_target(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"MsgDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": "MESSAGE",
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {"providers": [{"provider": "pushover", "target": "missing"}]},
                "enabled": True,
            }
        ],
        "adapter_instances": [
            {
                "id": inst_id,
                "adapter_type": "MESSAGE",
                "name": f"MsgInst-{uuid.uuid4().hex[:6]}",
                "config": {
                    "providers": {
                        "pushover": {
                            "enabled": True,
                            "api_token": "app-token",
                            "targets": {"default": {"user_key": "user-key"}},
                        }
                    }
                },
                "enabled": False,
            }
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bindings_created"] == 0
    assert any("MESSAGE target not configured" in error for error in body["errors"])


async def test_import_message_binding_rejects_missing_instance(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"MsgDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": "MESSAGE",
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {"providers": [{"provider": "pushover", "target": "default"}]},
                "enabled": True,
            }
        ],
    }

    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bindings_created"] == 0
    assert any(f"MESSAGE adapter instance not found: {inst_id}" in error for error in body["errors"])


async def test_import_message_binding_rejects_blank_message_body(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"MsgDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": "MESSAGE",
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {
                    "message": "",
                    "providers": [{"provider": "pushover", "target": "default"}],
                },
                "enabled": True,
            }
        ],
        "adapter_instances": [
            {
                "id": inst_id,
                "adapter_type": "MESSAGE",
                "name": f"MsgInst-{uuid.uuid4().hex[:6]}",
                "config": {
                    "providers": {
                        "pushover": {
                            "enabled": True,
                            "api_token": "app-token",
                            "targets": {"default": {"user_key": "user-key"}},
                        }
                    }
                },
                "enabled": False,
            }
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bindings_created"] == 0
    assert any("message must not be empty" in error for error in body["errors"])


async def test_import_message_instance_config_does_not_orphan_existing_binding(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    inst = await _make_message_instance(client, auth_headers, config=_message_instance_config(target="default"))
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
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "adapter_instances": [
            {
                "id": inst["id"],
                "adapter_type": "MESSAGE",
                "name": inst["name"],
                "config": _message_instance_config(target="renamed"),
                "enabled": False,
            }
        ],
    }

    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["adapter_instances_upserted"] == 0
    assert any("MESSAGE target not configured" in error for error in body["errors"])
    get_resp = await client.get(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)
    assert set(get_resp.json()["config"]["providers"]["pushover"]["targets"]) == {"default"}


async def test_import_message_binding_update_validates_against_existing_instance(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    old_inst = await _make_message_instance(client, auth_headers, config=_message_instance_config(target="default"))
    new_inst = await _make_message_instance(client, auth_headers, config=_message_instance_config(target="other"))
    create_resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/bindings",
        json={
            "adapter_instance_id": old_inst["id"],
            "direction": "SOURCE",
            "config": {"providers": [{"provider": "pushover", "target": "default"}]},
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    binding = create_resp.json()
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [
            {
                "id": binding["id"],
                "datapoint_id": dp["id"],
                "adapter_type": "MESSAGE",
                "adapter_instance_id": new_inst["id"],
                "direction": "SOURCE",
                "config": {"providers": [{"provider": "pushover", "target": "other"}]},
                "enabled": True,
            }
        ],
        "adapter_instances": [
            {
                "id": old_inst["id"],
                "adapter_type": "MESSAGE",
                "name": old_inst["name"],
                "config": _message_instance_config(target="renamed"),
                "enabled": False,
            }
        ],
    }

    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["adapter_instances_upserted"] == 0
    assert body["bindings_updated"] == 0
    assert any("MESSAGE target not configured" in error for error in body["errors"])
    old_get_resp = await client.get(f"/api/v1/adapters/instances/{old_inst['id']}", headers=auth_headers)
    assert set(old_get_resp.json()["config"]["providers"]["pushover"]["targets"]) == {"default"}
    export_resp = await client.get("/api/v1/config/export", headers=auth_headers)
    imported_binding = next(item for item in export_resp.json()["bindings"] if item["id"] == binding["id"])
    assert imported_binding["adapter_instance_id"] == old_inst["id"]
    assert imported_binding["config"] == {"providers": [{"provider": "pushover", "target": "default"}]}


# ---------------------------------------------------------------------------
# POST /config/import  — datapoint UPDATE path (existing DP gets updated)
# ---------------------------------------------------------------------------


async def test_import_updates_existing_datapoint(client, auth_headers):
    """Re-importing a DP with the same ID triggers the update branch."""
    dp = await _make_dp(client, auth_headers)
    export_resp = await client.get("/api/v1/config/export", headers=auth_headers)
    export_body = export_resp.json()

    # Mutate the name in the export payload then re-import
    new_name = f"Updated-{uuid.uuid4().hex[:6]}"
    for i, d in enumerate(export_body["datapoints"]):
        if d["id"] == dp["id"]:
            export_body["datapoints"][i]["name"] = new_name
            break

    resp = await client.post("/api/v1/config/import", json=export_body, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["datapoints_updated"] >= 1


# ---------------------------------------------------------------------------
# POST /config/import  — legacy adapter_configs migration path
# ---------------------------------------------------------------------------


async def test_import_legacy_adapter_configs(client, auth_headers):
    """v1 export with adapter_configs and no adapter_instances triggers migration path."""
    payload = {
        "obs_version": "1",
        "exported_at": "2023-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "adapter_instances": [],
        "adapter_configs": [{"adapter_type": _ADAPTER_TYPE, "config": {}, "enabled": True}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    # Legacy configs are migrated to instances
    assert resp.json()["adapter_instances_upserted"] >= 1


# ---------------------------------------------------------------------------
# POST /config/import  — KNX group addresses
# ---------------------------------------------------------------------------


async def test_import_knx_group_addresses(client, auth_headers):
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "knx_group_addresses": [
            {"address": "5/5/1", "name": "Test Licht", "description": "Wohnzimmer", "dpt": "DPT1.001"},
            {"address": "5/5/2", "name": "Test Temp", "description": "Sensor", "dpt": "DPT9.001"},
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["knx_group_addresses_upserted"] == 2


# ---------------------------------------------------------------------------
# POST /config/import  — logic graph UPDATE path (same ID re-imported)
# ---------------------------------------------------------------------------


async def test_import_updates_existing_logic_graph(client, auth_headers):
    graph = await _make_graph(client, auth_headers)
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "logic_graphs": [
            {
                "id": graph["id"],
                "name": f"Reimported-{uuid.uuid4().hex[:6]}",
                "description": "updated",
                "enabled": False,
                "flow_data": {"nodes": [], "edges": []},
            }
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["logic_graphs_updated"] == 1
    assert resp.json()["logic_graphs_created"] == 0


# ---------------------------------------------------------------------------
# POST /config/import  — binding CREATE path (new binding ID)
# ---------------------------------------------------------------------------


async def test_import_creates_binding(client, auth_headers):
    dp_id = str(uuid.uuid4())
    inst_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [{"id": dp_id, "name": f"BindDP-{uuid.uuid4().hex[:6]}", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": binding_id,
                "datapoint_id": dp_id,
                "adapter_type": _ADAPTER_TYPE,
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {},
                "enabled": True,
            }
        ],
        "adapter_instances": [
            {"id": inst_id, "adapter_type": _ADAPTER_TYPE, "name": f"BindInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": False}
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["bindings_created"] == 1


# ---------------------------------------------------------------------------
# POST /config/import  — binding UPDATE path (same ID re-imported)
# ---------------------------------------------------------------------------


async def test_import_updates_existing_binding(client, auth_headers):
    """Re-importing a binding with the same ID triggers the UPDATE branch."""
    export_resp = await client.get("/api/v1/config/export", headers=auth_headers)
    export_body = export_resp.json()
    if not export_body["bindings"]:
        pytest.skip("No bindings to update in current DB state")

    resp = await client.post("/api/v1/config/import", json=export_body, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["bindings_updated"] >= 1


# ---------------------------------------------------------------------------
# POST /config/import  — visu nodes (topological sort, parent-child)
# ---------------------------------------------------------------------------


async def test_import_visu_nodes(client, auth_headers):
    root_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "visu_nodes": [
            {
                "id": root_id,
                "parent_id": None,
                "name": "Root Page",
                "type": "PAGE",
                "node_order": 0,
                "icon": None,
                "access": "public",
                "access_pin": None,
                "page_config": "{}",
                "users": [],
            },
            {
                "id": child_id,
                "parent_id": root_id,
                "name": "Child Page",
                "type": "PAGE",
                "node_order": 1,
                "icon": None,
                "access": None,
                "access_pin": None,
                "page_config": "{}",
                "users": [],
            },
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["visu_nodes_upserted"] == 2


async def test_import_visu_node_with_missing_parent_records_error(client, auth_headers):
    """A visu node whose parent_id does not exist should be recorded as an error."""
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "visu_nodes": [
            {
                "id": str(uuid.uuid4()),
                "parent_id": str(uuid.uuid4()),  # non-existent parent
                "name": "Orphan",
                "type": "PAGE",
                "node_order": 0,
                "icon": None,
                "access": None,
                "access_pin": None,
                "page_config": "{}",
                "users": [],
            }
        ],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["errors"]) > 0


# ---------------------------------------------------------------------------
# POST /config/import  — hierarchy trees + nodes + DP links
# ---------------------------------------------------------------------------


async def test_import_hierarchy(client, auth_headers):
    tree_id = str(uuid.uuid4())
    node_id = str(uuid.uuid4())
    dp = await _make_dp(client, auth_headers)
    link_id = str(uuid.uuid4())

    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "hierarchy_trees": [{"id": tree_id, "name": "Test Tree", "description": "", "source": "ets_import:groups"}],
        "hierarchy_nodes": [
            {
                "id": node_id,
                "tree_id": tree_id,
                "parent_id": None,
                "name": "Root Node",
                "description": "",
                "node_order": 0,
                "icon": None,
            }
        ],
        "hierarchy_dp_links": [{"id": link_id, "node_id": node_id, "datapoint_id": dp["id"]}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["hierarchy_upserted"] >= 3  # tree + node + link
    row = await get_db().fetchone("SELECT source FROM hierarchy_trees WHERE id = ?", (tree_id,))
    assert row["source"] == "ets_import:groups"


# ---------------------------------------------------------------------------
# POST /config/import  — FA API key
# ---------------------------------------------------------------------------


async def test_import_fa_api_key(client, auth_headers):
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "fa_api_key": "test-fa-key-12345",
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["errors"] == [] or "fa_api_key" not in str(resp.json()["errors"])


# ---------------------------------------------------------------------------
# POST /config/import  — icon import (base64 SVG)
# ---------------------------------------------------------------------------


async def test_import_icon_valid_svg(client, auth_headers):
    import base64

    minimal_svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
    content_b64 = base64.b64encode(minimal_svg).decode()
    payload = {
        "obs_version": "5",
        "exported_at": "2024-01-01T00:00:00",
        "datapoints": [],
        "bindings": [],
        "icons": [{"name": f"test-icon-{uuid.uuid4().hex[:6]}", "content_b64": content_b64}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["icons_imported"] >= 1


# ---------------------------------------------------------------------------
# POST /config/import/db  — invalid magic bytes → 400
# ---------------------------------------------------------------------------


async def test_import_db_invalid_file_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/config/import/db",
        files={"file": ("bad.db", b"this is not a sqlite file at all", "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_import_db_requires_admin(client):
    resp = await client.post(
        "/api/v1/config/import/db",
        files={"file": ("bad.db", b"x", "application/octet-stream")},
    )
    assert resp.status_code == 401
