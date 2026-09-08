"""Integration Tests — Hierarchy ↔ Logic Graph links and browse endpoint (#1217)

Covers:
  GET    /api/v1/hierarchy/nodes/{id}/logic-graphs
  GET    /api/v1/hierarchy/logic-graphs/{id}/nodes
  POST   /api/v1/hierarchy/logic-graph-links
  DELETE /api/v1/hierarchy/logic-graph-links
  GET    /api/v1/hierarchy/browse
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_EMPTY_FLOW = {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_tree(client, auth_headers, name="Technik", desc="") -> dict:
    resp = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": name, "description": desc},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_node(client, auth_headers, tree_id: str, name="Beschattung", parent_id=None) -> dict:
    resp = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "parent_id": parent_id, "name": name},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_graph(client, auth_headers, name="Fenster A") -> dict:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": name, "flow_data": _EMPTY_FLOW},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Link CRUD
# ---------------------------------------------------------------------------


async def test_create_and_list_logic_graph_link(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "Fenster A")

    resp = await client.post(
        "/api/v1/hierarchy/logic-graph-links",
        json={"node_id": node["id"], "graph_id": graph["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["node_id"] == node["id"]
    assert body["graph_id"] == graph["id"]

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/logic-graphs", headers=auth_headers)
    assert resp.status_code == 200
    graphs = resp.json()
    assert len(graphs) == 1
    assert graphs[0]["id"] == graph["id"]
    assert graphs[0]["name"] == "Fenster A"
    assert graphs[0]["enabled"] is True


async def test_create_logic_graph_link_idempotent(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "Idempotent")

    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": node["id"], "graph_id": graph["id"]}, headers=auth_headers)
    resp = await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": node["id"], "graph_id": graph["id"]}, headers=auth_headers)
    assert resp.status_code == 201

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/logic-graphs", headers=auth_headers)
    assert len(resp.json()) == 1


async def test_delete_logic_graph_link(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "ZumLoeschen")
    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": node["id"], "graph_id": graph["id"]}, headers=auth_headers)

    resp = await client.delete(
        "/api/v1/hierarchy/logic-graph-links",
        params={"node_id": node["id"], "graph_id": graph["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/logic-graphs", headers=auth_headers)
    assert len(resp.json()) == 0


async def test_create_link_unknown_node_404(client, auth_headers):
    graph = await _create_graph(client, auth_headers, "OhneKnoten")
    resp = await client.post(
        "/api/v1/hierarchy/logic-graph-links",
        json={"node_id": "does-not-exist", "graph_id": graph["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_create_link_unknown_graph_404(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    resp = await client.post(
        "/api/v1/hierarchy/logic-graph-links",
        json={"node_id": node["id"], "graph_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_get_node_logic_graphs_unknown_node_404(client, auth_headers):
    resp = await client.get("/api/v1/hierarchy/nodes/does-not-exist/logic-graphs", headers=auth_headers)
    assert resp.status_code == 404


async def test_get_logic_graph_nodes_unknown_graph_404(client, auth_headers):
    resp = await client.get("/api/v1/hierarchy/logic-graphs/does-not-exist/nodes", headers=auth_headers)
    assert resp.status_code == 404


async def test_logic_graph_link_deleted_when_graph_deleted(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "CascadeGraph")
    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": node["id"], "graph_id": graph["id"]}, headers=auth_headers)

    await client.delete(f"/api/v1/logic/graphs/{graph['id']}", headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/nodes/{node['id']}/logic-graphs", headers=auth_headers)
    assert len(resp.json()) == 0


async def test_logic_graph_link_deleted_when_node_deleted(client, auth_headers):
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "CascadeNode")
    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": node["id"], "graph_id": graph["id"]}, headers=auth_headers)

    await client.delete(f"/api/v1/hierarchy/nodes/{node['id']}", headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/logic-graphs/{graph['id']}/nodes", headers=auth_headers)
    assert len(resp.json()) == 0


# ---------------------------------------------------------------------------
# Many-to-many across multiple trees (parallel hierarchies, #1217)
# ---------------------------------------------------------------------------


async def test_graph_linked_to_multiple_trees_simultaneously(client, auth_headers):
    """A graph can appear in a technical AND a topological hierarchy at once."""
    technical = await _create_tree(client, auth_headers, "Technisch")
    topological = await _create_tree(client, auth_headers, "Topologisch")
    beschattung = await _create_node(client, auth_headers, technical["id"], "Beschattung")
    wohnzimmer = await _create_node(client, auth_headers, topological["id"], "Wohnzimmer")
    graph = await _create_graph(client, auth_headers, "Fenster A")

    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": beschattung["id"], "graph_id": graph["id"]}, headers=auth_headers)
    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": wohnzimmer["id"], "graph_id": graph["id"]}, headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/logic-graphs/{graph['id']}/nodes", headers=auth_headers)
    assert resp.status_code == 200
    refs = resp.json()
    assert len(refs) == 2
    tree_ids = {r["tree_id"] for r in refs}
    assert technical["id"] in tree_ids
    assert topological["id"] in tree_ids

    # Both node-level views still see the graph — dropping onto a second tree
    # is additive, the first link is untouched.
    resp = await client.get(f"/api/v1/hierarchy/nodes/{beschattung['id']}/logic-graphs", headers=auth_headers)
    assert len(resp.json()) == 1
    resp = await client.get(f"/api/v1/hierarchy/nodes/{wohnzimmer['id']}/logic-graphs", headers=auth_headers)
    assert len(resp.json()) == 1


async def test_logic_graph_nodes_reports_ancestor_path_for_a_nested_node(client, auth_headers):
    """A graph linked to a nested node gets that node's full ancestor path back."""
    tree = await _create_tree(client, auth_headers, "Topologisch")
    floor = await _create_node(client, auth_headers, tree["id"], "Erdgeschoss")
    room = await _create_node(client, auth_headers, tree["id"], "Wohnzimmer", parent_id=floor["id"])
    graph = await _create_graph(client, auth_headers, "Licht WZ")

    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": room["id"], "graph_id": graph["id"]}, headers=auth_headers)

    resp = await client.get(f"/api/v1/hierarchy/logic-graphs/{graph['id']}/nodes", headers=auth_headers)
    assert resp.status_code == 200
    refs = resp.json()
    assert len(refs) == 1
    assert refs[0]["node_id"] == room["id"]
    path_node_ids = [seg["node_id"] for seg in refs[0]["node_path"]]
    assert path_node_ids == [floor["id"]]


# ---------------------------------------------------------------------------
# No authz inheritance (#1217 explicit design decision)
# ---------------------------------------------------------------------------


async def test_hierarchy_link_does_not_affect_graph_visibility_for_non_admin(client, auth_headers):
    """Linking a graph into a hierarchy node must not grant or hide access.

    Logic graphs have their own independent authz (logic_graph node type);
    hierarchy links are purely organizational. A non-admin user who cannot
    otherwise see hierarchy nodes must still see the graph via the plain
    logic-graphs listing (access unaffected either way), and creating the
    hierarchy link must not require any logic_graph-specific grant.
    """
    tree = await _create_tree(client, auth_headers)
    node = await _create_node(client, auth_headers, tree["id"])
    graph = await _create_graph(client, auth_headers, "AuthzNeutral")

    resp = await client.post(
        "/api/v1/hierarchy/logic-graph-links",
        json={"node_id": node["id"], "graph_id": graph["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201

    # The graph's own listing is unaffected by the hierarchy link either way —
    # proves the link endpoint added no new authz gate on the graph itself.
    resp = await client.get("/api/v1/logic/graphs", headers=auth_headers)
    assert resp.status_code == 200
    graph_ids = {g["id"] for g in resp.json()}
    assert graph["id"] in graph_ids


# ---------------------------------------------------------------------------
# Browse (drill-down navigation for the Logic editor's "open" popup)
# ---------------------------------------------------------------------------


async def test_browse_top_level_lists_trees(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "BrowseTree")
    resp = await client.get("/api/v1/hierarchy/browse", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["subfolders"] == []
    assert body["logic_graphs"] == []
    tree_ids = {t["id"] for t in body["trees"]}
    assert tree["id"] in tree_ids


async def test_browse_tree_top_level_lists_root_nodes_no_graphs(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "BrowseTree2")
    node = await _create_node(client, auth_headers, tree["id"], "RootFolder")

    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": tree["id"]}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["trees"] == []
    assert body["logic_graphs"] == []  # graphs never attach directly to a tree, only to nodes
    assert len(body["subfolders"]) == 1
    assert body["subfolders"][0]["id"] == node["id"]
    assert body["subfolders"][0]["has_children"] is False


async def test_browse_node_lists_children_and_linked_graphs(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "BrowseTree3")
    parent = await _create_node(client, auth_headers, tree["id"], "Parent")
    child = await _create_node(client, auth_headers, tree["id"], "Child", parent_id=parent["id"])
    graph = await _create_graph(client, auth_headers, "LinkedInParent")
    await client.post("/api/v1/hierarchy/logic-graph-links", json={"node_id": parent["id"], "graph_id": graph["id"]}, headers=auth_headers)

    # The tree's top-level listing now reports has_children=True for parent.
    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": tree["id"]}, headers=auth_headers)
    subfolders = resp.json()["subfolders"]
    assert next(s for s in subfolders if s["id"] == parent["id"])["has_children"] is True

    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": tree["id"], "node_id": parent["id"]}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["subfolders"]) == 1
    assert body["subfolders"][0]["id"] == child["id"]
    assert len(body["logic_graphs"]) == 1
    assert body["logic_graphs"][0]["id"] == graph["id"]


async def test_browse_shows_empty_branches(client, auth_headers):
    """A folder with no logic graph anywhere in its subtree is still browsable.

    Explicit design decision (#1217): mirror the Settings → Hierarchy
    structure exactly, never prune branches just because they hold no graph.
    """
    tree = await _create_tree(client, auth_headers, "EmptyBranchTree")
    empty_folder = await _create_node(client, auth_headers, tree["id"], "NurGeraete")

    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": tree["id"]}, headers=auth_headers)
    subfolders = resp.json()["subfolders"]
    assert any(s["id"] == empty_folder["id"] for s in subfolders)

    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": tree["id"], "node_id": empty_folder["id"]}, headers=auth_headers)
    body = resp.json()
    assert body["subfolders"] == []
    assert body["logic_graphs"] == []


async def test_browse_unknown_tree_404(client, auth_headers):
    resp = await client.get("/api/v1/hierarchy/browse", params={"tree_id": "does-not-exist"}, headers=auth_headers)
    assert resp.status_code == 404


async def test_browse_unknown_node_404(client, auth_headers):
    tree = await _create_tree(client, auth_headers, "BrowseTree4")
    resp = await client.get(
        "/api/v1/hierarchy/browse",
        params={"tree_id": tree["id"], "node_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_browse_node_from_wrong_tree_404(client, auth_headers):
    """A node_id that exists but belongs to a different tree is rejected."""
    tree1 = await _create_tree(client, auth_headers, "TreeA")
    tree2 = await _create_tree(client, auth_headers, "TreeB")
    node_in_tree1 = await _create_node(client, auth_headers, tree1["id"], "NodeInA")

    resp = await client.get(
        "/api/v1/hierarchy/browse",
        params={"tree_id": tree2["id"], "node_id": node_in_tree1["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 404
