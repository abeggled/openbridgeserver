"""Integration Tests — Duplizierung (Issue #240)

Logic Graphs:
  POST /api/v1/logic/graphs/{id}/duplicate  → Kopie mit neuen Node/Edge-IDs
  GET  /api/v1/logic/graphs/{id}/export     → JSON-Download
  POST /api/v1/logic/graphs/import          → Import (mit missing_node Fallback)

Visu Nodes:
  GET  /api/v1/visu/nodes/{id}/export       → Teilbaum als JSON
  POST /api/v1/visu/nodes/import            → Import mit neuen IDs
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


async def _create_graph(
    client,
    auth_headers,
    name: str = "Test Graph",
    *,
    control_class: str = "room_local",
) -> dict:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={
            "name": name,
            "description": "Test",
            "enabled": True,
            "control_class": control_class,
            "flow_data": {
                "nodes": [
                    {
                        "id": "n1",
                        "type": "and",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "AND", "input_count": 2},
                    },
                    {
                        "id": "n2",
                        "type": "or",
                        "position": {"x": 200, "y": 0},
                        "data": {"label": "OR", "input_count": 2},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n1",
                        "target": "n2",
                        "sourceHandle": "out",
                        "targetHandle": "in1",
                    },
                ],
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_graph(client, auth_headers, gid: str) -> None:
    await client.delete(f"/api/v1/logic/graphs/{gid}", headers=auth_headers)


async def _create_visu_node(client, auth_headers, name: str = "Test Seite") -> dict:
    resp = await client.post(
        "/api/v1/visu/nodes",
        json={"name": name, "type": "PAGE", "order": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_visu_node(client, auth_headers, nid: str) -> None:
    await client.delete(f"/api/v1/visu/nodes/{nid}", headers=auth_headers)


# ===========================================================================
# Logic — Duplizieren
# ===========================================================================


async def test_duplicate_graph_creates_copy(client, auth_headers):
    g = await _create_graph(client, auth_headers, "Original")
    try:
        resp = await client.post(
            f"/api/v1/logic/graphs/{g['id']}/duplicate",
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        copy = resp.json()

        assert copy["id"] != g["id"]
        assert copy["name"] == f"Kopie von {g['name']}"

        orig_node_ids = {n["id"] for n in g["flow_data"]["nodes"]}
        copy_node_ids = {n["id"] for n in copy["flow_data"]["nodes"]}
        assert orig_node_ids.isdisjoint(copy_node_ids), "Node-IDs müssen sich unterscheiden"

        orig_edge_ids = {e["id"] for e in g["flow_data"]["edges"]}
        copy_edge_ids = {e["id"] for e in copy["flow_data"]["edges"]}
        assert orig_edge_ids.isdisjoint(copy_edge_ids), "Edge-IDs müssen sich unterscheiden"

        # Kantenverbindungen müssen auf neue Node-IDs zeigen
        _ = {old_id: new_id for old_id, new_id in zip(orig_node_ids, copy_node_ids)}
        for edge in copy["flow_data"]["edges"]:
            assert edge["source"] in copy_node_ids
            assert edge["target"] in copy_node_ids
    finally:
        await _delete_graph(client, auth_headers, g["id"])
        if "copy" in dir() and copy:
            await _delete_graph(client, auth_headers, copy["id"])


async def test_central_graph_class_roundtrips_duplicate_export_and_import(client, auth_headers):
    graph = await _create_graph(client, auth_headers, "Central Graph", control_class="central_plant")
    duplicate = None
    imported = None
    try:
        duplicate_response = await client.post(
            f"/api/v1/logic/graphs/{graph['id']}/duplicate",
            headers=auth_headers,
        )
        assert duplicate_response.status_code == 201
        duplicate = duplicate_response.json()
        assert duplicate["control_class"] == "central_plant"

        export_response = await client.get(
            f"/api/v1/logic/graphs/{graph['id']}/export",
            headers=auth_headers,
        )
        assert export_response.status_code == 200
        payload = export_response.json()
        assert payload["control_class"] == "central_plant"

        import_response = await client.post(
            "/api/v1/logic/graphs/import",
            json=payload,
            headers=auth_headers,
        )
        assert import_response.status_code == 201
        imported = import_response.json()
        assert imported["control_class"] == "central_plant"
    finally:
        await _delete_graph(client, auth_headers, graph["id"])
        if duplicate is not None:
            await _delete_graph(client, auth_headers, duplicate["id"])
        if imported is not None:
            await _delete_graph(client, auth_headers, imported["id"])


async def test_duplicate_graph_not_found(client, auth_headers):
    resp = await client.post(
        "/api/v1/logic/graphs/nonexistent-id/duplicate",
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_duplicate_graph_requires_auth(client):
    resp = await client.post("/api/v1/logic/graphs/any-id/duplicate")
    assert resp.status_code == 401


# ===========================================================================
# Logic — Exportieren
# ===========================================================================


async def test_export_graph_returns_json(client, auth_headers):
    g = await _create_graph(client, auth_headers, "Export Test")
    try:
        resp = await client.get(
            f"/api/v1/logic/graphs/{g['id']}/export",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["obs_export"] == "logic_graph"
        assert data["version"] == 1
        assert data["name"] == g["name"]
        assert "flow_data" in data
        assert "nodes" in data["flow_data"]
        assert len(data["flow_data"]["nodes"]) == 2
    finally:
        await _delete_graph(client, auth_headers, g["id"])


async def test_export_graph_not_found(client, auth_headers):
    resp = await client.get(
        "/api/v1/logic/graphs/nonexistent-id/export",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ===========================================================================
# Logic — Importieren
# ===========================================================================


async def test_import_graph_creates_graph(client, auth_headers):
    payload = {
        "obs_export": "logic_graph",
        "version": 1,
        "name": "Importierter Graph",
        "description": "Aus Export",
        "enabled": True,
        "flow_data": {
            "nodes": [
                {
                    "id": "x1",
                    "type": "and",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "AND"},
                },
            ],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/logic/graphs/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported = resp.json()
    assert imported["name"] == "Importierter Graph"
    await _delete_graph(client, auth_headers, imported["id"])


async def test_import_graph_unknown_type_becomes_missing_node(client, auth_headers):
    payload = {
        "obs_export": "logic_graph",
        "version": 1,
        "name": "Import mit unbekanntem Typ",
        "description": "",
        "enabled": True,
        "flow_data": {
            "nodes": [
                {
                    "id": "u1",
                    "type": "does_not_exist_v99",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Unbekannt"},
                },
                {
                    "id": "u2",
                    "type": "and",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "AND"},
                },
            ],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/logic/graphs/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported = resp.json()
    nodes_by_id = {n["id"]: n for n in imported["flow_data"]["nodes"]}

    assert nodes_by_id["u1"]["type"] == "missing_node"
    assert nodes_by_id["u1"]["data"]["original_type"] == "does_not_exist_v99"
    # The user-defined block name survives the placeholder conversion (#1157)
    # so a renamed block stays identifiable after its type disappeared.
    assert nodes_by_id["u1"]["data"]["label"] == "Unbekannt"
    assert nodes_by_id["u2"]["type"] == "and"
    await _delete_graph(client, auth_headers, imported["id"])


async def test_import_graph_unknown_type_without_block_name(client, auth_headers):
    """A block that was never renamed produces a placeholder without a `label`,
    so the frontend renders its own localized heading (#1157)."""
    payload = {
        "obs_export": "logic_graph",
        "version": 1,
        "name": "Import ohne Blockname",
        "description": "",
        "enabled": True,
        "flow_data": {
            "nodes": [
                {
                    "id": "u1",
                    "type": "does_not_exist_v99",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "   "},
                },
                {
                    "id": "u2",
                    "type": "also_gone_v99",
                    "position": {"x": 100, "y": 0},
                    "data": {},
                },
            ],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/logic/graphs/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported = resp.json()
    nodes_by_id = {n["id"]: n for n in imported["flow_data"]["nodes"]}

    assert nodes_by_id["u1"]["data"] == {"original_type": "does_not_exist_v99"}
    assert nodes_by_id["u2"]["data"] == {"original_type": "also_gone_v99"}
    await _delete_graph(client, auth_headers, imported["id"])


async def test_import_graph_strips_the_legacy_missing_node_marker(client, auth_headers):
    """An export taken before issue #1157 carries `missing_node` placeholders
    whose `label` is the generated German type marker. That key now means
    "user-defined block name", so the marker must not survive into the editor.
    """
    payload = {
        "obs_export": "logic_graph",
        "version": 1,
        "name": "Alter Export mit Platzhalter",
        "description": "",
        "enabled": True,
        "flow_data": {
            "nodes": [
                {
                    "id": "u1",
                    "type": "missing_node",
                    "position": {"x": 0, "y": 0},
                    "data": {"original_type": "does_not_exist_v99", "label": "[Fehlend: does_not_exist_v99]"},
                },
                {
                    "id": "u2",
                    "type": "missing_node",
                    "position": {"x": 100, "y": 0},
                    "data": {"original_type": "does_not_exist_v99", "label": "Treppenhaus"},
                },
            ],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/logic/graphs/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported = resp.json()
    nodes_by_id = {n["id"]: n for n in imported["flow_data"]["nodes"]}

    assert nodes_by_id["u1"]["data"] == {"original_type": "does_not_exist_v99"}
    # A name the user actually typed on the placeholder survives.
    assert nodes_by_id["u2"]["data"]["label"] == "Treppenhaus"

    # And it stays gone when the sheet is read back.
    resp = await client.get(f"/api/v1/logic/graphs/{imported['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    reread = {n["id"]: n for n in resp.json()["flow_data"]["nodes"]}
    assert "label" not in reread["u1"]["data"]

    await _delete_graph(client, auth_headers, imported["id"])


async def test_import_graph_promotes_a_missing_type_carried_in_label(client, auth_headers):
    """`label` is the user-defined block name since #1157, so a placeholder that
    keeps its missing type there must have it moved to `original_type` — the
    rename field would otherwise present the type as a name and overwrite the
    only copy of it on the next save.
    """
    payload = {
        "obs_export": "logic_graph",
        "version": 1,
        "name": "Platzhalter ohne original_type",
        "description": "",
        "enabled": True,
        "flow_data": {
            "nodes": [
                {
                    "id": "u1",
                    "type": "missing_node",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "does_not_exist_v99"},
                },
            ],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/logic/graphs/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported = resp.json()

    node = imported["flow_data"]["nodes"][0]
    assert node["data"] == {"original_type": "does_not_exist_v99"}

    # Renaming the placeholder now leaves the missing type intact.
    node["data"]["label"] = "Treppenhaus"
    resp = await client.put(
        f"/api/v1/logic/graphs/{imported['id']}",
        json={
            "name": imported["name"],
            "description": imported["description"],
            "enabled": imported["enabled"],
            "flow_data": imported["flow_data"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["flow_data"]["nodes"][0]
    assert saved["data"] == {"original_type": "does_not_exist_v99", "label": "Treppenhaus"}

    await _delete_graph(client, auth_headers, imported["id"])


async def test_import_graph_wrong_format(client, auth_headers):
    resp = await client.post(
        "/api/v1/logic/graphs/import",
        json={
            "obs_export": "wrong_format",
            "version": 1,
            "name": "x",
            "flow_data": {"nodes": [], "edges": []},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ===========================================================================
# Visu — Exportieren
# ===========================================================================


async def test_export_visu_node_returns_json(client, auth_headers):
    node = await _create_visu_node(client, auth_headers, "Export Seite")
    try:
        resp = await client.get(
            f"/api/v1/visu/nodes/{node['id']}/export",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["obs_export"] == "visu_subtree"
        assert data["version"] == 1
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["name"] == "Export Seite"
    finally:
        await _delete_visu_node(client, auth_headers, node["id"])


async def test_export_visu_node_not_found(client, auth_headers):
    resp = await client.get(
        "/api/v1/visu/nodes/nonexistent-node/export",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ===========================================================================
# Visu — Importieren
# ===========================================================================


async def test_import_visu_node_creates_node(client, auth_headers):
    payload = {
        "obs_export": "visu_subtree",
        "version": 1,
        "target_parent_id": None,
        "nodes": [
            {
                "id": "old-id-1",
                "parent_id": None,
                "name": "Importierte Seite",
                "type": "PAGE",
                "node_order": 0,
                "icon": None,
                "access": None,
                "page_config": {
                    "grid_cols": 12,
                    "grid_row_height": 80,
                    "background": None,
                    "widgets": [],
                },
            },
        ],
    }
    resp = await client.post("/api/v1/visu/nodes/import", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "Importierte Seite"
    assert created["id"] != "old-id-1"  # neue ID vergeben
    await _delete_visu_node(client, auth_headers, created["id"])


async def test_import_visu_node_wrong_format(client, auth_headers):
    resp = await client.post(
        "/api/v1/visu/nodes/import",
        json={"obs_export": "wrong", "version": 1, "nodes": []},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 422)


async def test_import_visu_node_requires_auth(client):
    resp = await client.post("/api/v1/visu/nodes/import", json={})
    assert resp.status_code == 401


# ===========================================================================
# Visu — Kopieren (vorhandene copy-Route, nun mit null parent)
# ===========================================================================


async def test_copy_visu_node_to_root(client, auth_headers):
    node = await _create_visu_node(client, auth_headers, "Original Seite")
    try:
        resp = await client.post(
            f"/api/v1/visu/nodes/{node['id']}/copy",
            json={"target_parent_id": None, "new_name": "Kopie Seite"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        copy = resp.json()
        assert copy["name"] == "Kopie Seite"
        assert copy["id"] != node["id"]
    finally:
        await _delete_visu_node(client, auth_headers, node["id"])
        if "copy" in dir() and copy:
            await _delete_visu_node(client, auth_headers, copy["id"])
