"""Integration Tests — Hierarchy Manager

Covers:
  POST   /api/v1/hierarchy/trees              create tree
  GET    /api/v1/hierarchy/trees              list trees
  PUT    /api/v1/hierarchy/trees/{id}         update tree
  DELETE /api/v1/hierarchy/trees/{id}         delete tree

  GET    /api/v1/hierarchy/trees/{id}/nodes   get tree (nested)
  POST   /api/v1/hierarchy/nodes              create node
  PUT    /api/v1/hierarchy/nodes/{id}         update node
  PUT    /api/v1/hierarchy/nodes/{id}/move    move node
  DELETE /api/v1/hierarchy/nodes/{id}         delete node

  POST   /api/v1/hierarchy/links              create link
  DELETE /api/v1/hierarchy/links              delete link
  GET    /api/v1/hierarchy/nodes/{id}/datapoints
  GET    /api/v1/hierarchy/datapoints/{id}/nodes

  POST   /api/v1/hierarchy/import-from-ets    ETS-based tree creation
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_tree(client, auth_headers, name="Gebäude", desc="Test-Ast") -> dict:
    resp = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": name, "description": desc},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_node(client, auth_headers, tree_id: str, name="Erdgeschoss", parent_id=None) -> dict:
    resp = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "parent_id": parent_id, "name": name},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_dp(client, auth_headers, name="TestDP") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "persist_value": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tree CRUD
# ---------------------------------------------------------------------------


async def test_create_and_list_trees(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "Gebäude")
    assert tree["name"] == "Gebäude"
    assert "id" in tree
    assert "created_at" in tree

    resp = await client.get("/api/v1/hierarchy/trees", headers=auth_headers)
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert tree["id"] in ids


async def test_update_tree(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "Alt")
    resp = await client.put(
        f"/api/v1/hierarchy/trees/{tree['id']}",
        json={"name": "Neu", "description": "Aktualisiert"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Neu"
    assert body["description"] == "Aktualisiert"


async def test_delete_tree(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "ZuLöschen")
    resp = await client.delete(f"/api/v1/hierarchy/trees/{tree['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get("/api/v1/hierarchy/trees", headers=auth_headers)
    ids = [t["id"] for t in resp.json()]
    assert tree["id"] not in ids


async def test_delete_tree_404(client, auth_headers):
    resp = await client.delete("/api/v1/hierarchy/trees/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Node CRUD
# ---------------------------------------------------------------------------


async def test_create_node_root(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    assert node["name"] == "Erdgeschoss"
    assert node["tree_id"] == tree["id"]
    assert node["parent_id"] is None


async def test_create_child_node(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    root = await _create_node(client, auth_headers, tree["id"], "EG")
    child = await _create_node(client, auth_headers, tree["id"], "Wohnzimmer", parent_id=root["id"])
    assert child["parent_id"] == root["id"]


async def test_get_tree_nodes_nested(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    root = await _create_node(client, auth_headers, tree["id"], "EG")
    await _create_node(client, auth_headers, tree["id"], "Wohnzimmer", parent_id=root["id"])
    await _create_node(client, auth_headers, tree["id"], "Küche", parent_id=root["id"])

    resp = await client.get(f"/api/v1/hierarchy/trees/{tree['id']}/nodes", headers=auth_headers)
    assert resp.status_code == 200
    nodes = resp.json()
    assert len(nodes) == 1  # only root at top level
    assert nodes[0]["name"] == "EG"
    assert len(nodes[0]["children"]) == 2


async def test_update_node(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"], "Alt")
    resp = await client.put(
        f"/api/v1/hierarchy/nodes/{node['id']}",
        json={"name": "Neu", "description": "Beschreibung"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Neu"


async def test_move_node(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    root1 = await _create_node(client, auth_headers, tree["id"], "Ast1")
    root2 = await _create_node(client, auth_headers, tree["id"], "Ast2")
    child = await _create_node(client, auth_headers, tree["id"], "Kind", parent_id=root1["id"])

    resp = await client.put(
        f"/api/v1/hierarchy/nodes/{child['id']}/move",
        json={"new_parent_id": root2["id"], "new_order": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["parent_id"] == root2["id"]


async def test_delete_node_cascades_children(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    root = await _create_node(client, auth_headers, tree["id"], "Root")
    child = await _create_node(client, auth_headers, tree["id"], "Child", parent_id=root["id"])

    resp = await client.delete(f"/api/v1/hierarchy/nodes/{root['id']}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/hierarchy/trees/{tree['id']}/nodes", headers=auth_headers)
    ids = [n["id"] for n in resp.json()]
    assert root["id"] not in ids
    assert child["id"] not in ids  # cascaded


async def test_create_node_wrong_tree_for_parent(client, auth_headers):
    tree1 = await _create_tree(client, auth_headers, "T1")
    tree2 = await _create_tree(client, auth_headers, "T2")
    node_in_t1 = await _create_node(client, auth_headers, tree1["id"], "N1")

    resp = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree2["id"], "parent_id": node_in_t1["id"], "name": "X"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Links (DataPoint ↔ Node)
# ---------------------------------------------------------------------------


async def test_create_and_list_links(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    dp = await _create_dp(client, auth_headers, "Sensor1")

    resp = await client.post(
        "/api/v1/hierarchy/links",
        json={"node_id": node["id"], "datapoint_id": dp["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["node_id"] == node["id"]
    assert body["datapoint_id"] == dp["id"]

    # list node datapoints
    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/datapoints", headers=auth_headers)
    assert resp.status_code == 200
    dps = resp.json()
    assert len(dps) == 1
    assert dps[0]["id"] == dp["id"]


async def test_create_link_idempotent(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    dp = await _create_dp(client, auth_headers, "SensorIdem")

    await client.post("/api/v1/hierarchy/links", json={"node_id": node["id"], "datapoint_id": dp["id"]}, headers=auth_headers)
    resp = await client.post("/api/v1/hierarchy/links", json={"node_id": node["id"], "datapoint_id": dp["id"]}, headers=auth_headers)
    assert resp.status_code == 201  # idempotent, no error

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/datapoints", headers=auth_headers)
    assert len(resp.json()) == 1  # still only one


async def test_delete_link(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    dp = await _create_dp(client, auth_headers, "SensorDel")
    await client.post("/api/v1/hierarchy/links", json={"node_id": node["id"], "datapoint_id": dp["id"]}, headers=auth_headers)

    resp = await client.delete(
        "/api/v1/hierarchy/links",
        params={"node_id": node["id"], "datapoint_id": dp["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/datapoints", headers=auth_headers)
    assert len(resp.json()) == 0


async def test_datapoint_nodes_multi_tree(client, auth_headers):
    """DataPoint can be linked to nodes in multiple trees (multi-dimensional)."""
    tree1 = await _create_tree(client, auth_headers, "Gewerke")
    tree2 = await _create_tree(client, auth_headers, "Gebäude")
    node1 = await _create_node(client, auth_headers, tree1["id"], "Heizung")
    node2 = await _create_node(client, auth_headers, tree2["id"], "EG")
    dp = await _create_dp(client, auth_headers, "Heizkreis")

    await client.post("/api/v1/hierarchy/links", json={"node_id": node1["id"], "datapoint_id": dp["id"]}, headers=auth_headers)
    await client.post("/api/v1/hierarchy/links", json={"node_id": node2["id"], "datapoint_id": dp["id"]}, headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/datapoints/{dp['id']}/nodes", headers=auth_headers)
    assert resp.status_code == 200
    refs = resp.json()
    assert len(refs) == 2
    tree_ids = {r["tree_id"] for r in refs}
    assert tree1["id"] in tree_ids
    assert tree2["id"] in tree_ids


async def test_link_deleted_when_datapoint_deleted(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    dp = await _create_dp(client, auth_headers, "CascadeDP")
    await client.post("/api/v1/hierarchy/links", json={"node_id": node["id"], "datapoint_id": dp["id"]}, headers=auth_headers)

    await client.delete(f"/api/v1/datapoints/{dp['id']}", headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/datapoints", headers=auth_headers)
    assert len(resp.json()) == 0


# ---------------------------------------------------------------------------
# ETS Import
# ---------------------------------------------------------------------------


async def test_import_from_ets_no_gas(client, auth_headers):
    resp = await client.post(
        "/api/v1/hierarchy/import-from-ets",
        json={"tree_name": "ETS Test", "mode": "groups"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_import_from_ets_groups_mode(client, auth_headers):
    # Seed some GAs first
    await client.request(
        "DELETE",
        "/api/v1/knxproj/group-addresses",
        headers=auth_headers,
    )
    from obs.db.database import get_db
    db = get_db()
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    await db.executemany(
        "INSERT OR REPLACE INTO knx_group_addresses (address, name, description, dpt, imported_at) VALUES (?,?,?,?,?)",
        [
            ("1/0/1", "Licht EG", "Schalten", "DPT1.001", now),
            ("1/0/2", "Dimmer EG", "Dimmen", "DPT5.001", now),
            ("2/0/1", "Licht OG", "Schalten", "DPT1.001", now),
        ],
    )
    await db.commit()

    resp = await client.post(
        "/api/v1/hierarchy/import-from-ets",
        json={"tree_name": "ETS Gruppen", "mode": "groups"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tree_name"] == "ETS Gruppen"
    assert body["nodes_created"] > 0

    # Verify tree exists
    resp2 = await client.get("/api/v1/hierarchy/trees", headers=auth_headers)
    ids = [t["id"] for t in resp2.json()]
    assert body["tree_id"] in ids

    # Verify nodes
    resp3 = await client.get(f"/api/v1/hierarchy/trees/{body['tree_id']}/nodes", headers=auth_headers)
    roots = resp3.json()
    assert len(roots) == 2  # Hauptgruppe 1 + 2
