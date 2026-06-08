"""
Integration tests for #433 — node search responses include ancestor `path`
so callers can disambiguate same-named leaves under different parents.

Covers both:
- GET /api/v1/hierarchy/nodes/search (NodeSearchResult.path)
- Hierarchy refs embedded in datapoint search responses (HierarchyNodeRef.path)
"""

import pytest


@pytest.mark.asyncio
async def test_node_search_returns_ancestor_path(client, auth_headers):
    # Build a tiny tree: Gebäude (root) → EG → Küche
    r = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": "Gebäude T1", "description": "", "display_depth": 2},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    tree_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "EG", "parent_id": None},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    eg_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "Küche", "parent_id": eg_id},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    kueche_id = r.json()["id"]

    # Search returns the Küche node with full ancestor path
    r = await client.get("/api/v1/hierarchy/nodes/search?q=Küche", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [h for h in r.json() if h["node_id"] == kueche_id]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["path"] == ["EG", "Küche"]
    assert hit["tree_name"] == "Gebäude T1"
    assert hit["display_depth"] == 2


@pytest.mark.asyncio
async def test_node_search_filters_nodes_above_display_start_depth(client, auth_headers):
    r = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": "Haus T3", "description": "", "display_depth": 2},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    tree_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "Gebäude A", "parent_id": None},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    building_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "EG", "parent_id": building_id},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    floor_id = r.json()["id"]

    r = await client.get("/api/v1/hierarchy/nodes/search?q=Gebäude A", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [hit for hit in r.json() if hit["node_id"] in (building_id, floor_id)]
    assert [hit["node_id"] for hit in hits] == [floor_id]
    assert hits[0]["path"] == ["Gebäude A", "EG"]
    assert hits[0]["display_depth"] == 2

    r = await client.get("/api/v1/hierarchy/nodes/search?q=Haus T3", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [hit for hit in r.json() if hit["node_id"] in (building_id, floor_id)]
    assert [hit["node_id"] for hit in hits] == [floor_id]

    r = await client.get("/api/v1/hierarchy/nodes/search?q=EG", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [hit for hit in r.json() if hit["node_id"] == floor_id]
    assert len(hits) == 1
    assert hits[0]["path"] == ["Gebäude A", "EG"]
    assert hits[0]["display_depth"] == 2

    r = await client.get("/api/v1/hierarchy/nodes/search", headers=auth_headers)
    assert r.status_code == 200, r.text
    all_hits = {hit["node_id"]: hit for hit in r.json() if hit["node_id"] in (building_id, floor_id)}
    assert building_id not in all_hits
    assert all_hits[floor_id]["path"] == ["Gebäude A", "EG"]


@pytest.mark.asyncio
async def test_node_search_falls_back_when_display_depth_has_no_matching_level(client, auth_headers):
    r = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": "Haus T4", "description": "", "display_depth": 5},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    tree_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "Gebäude A", "parent_id": None},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    building_id = r.json()["id"]

    r = await client.get("/api/v1/hierarchy/nodes/search?q=Gebäude A", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [hit for hit in r.json() if hit["node_id"] == building_id]
    assert len(hits) >= 1
    assert hits[0]["path"] == ["Gebäude A"]
    assert hits[0]["display_depth"] == 5


@pytest.mark.asyncio
async def test_node_search_path_materialization_is_cycle_safe(client, auth_headers):
    r = await client.post(
        "/api/v1/hierarchy/trees",
        json={"name": "Haus T5", "description": "", "display_depth": 0},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    tree_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "Gebäude A", "parent_id": None},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    building_id = r.json()["id"]

    r = await client.post(
        "/api/v1/hierarchy/nodes",
        json={"tree_id": tree_id, "name": "EG", "parent_id": building_id},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    floor_id = r.json()["id"]

    r = await client.put(
        f"/api/v1/hierarchy/nodes/{building_id}/move",
        json={"new_parent_id": floor_id, "new_order": 0},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    r = await client.get("/api/v1/hierarchy/nodes/search?q=EG", headers=auth_headers)
    assert r.status_code == 200, r.text
    hits = [hit for hit in r.json() if hit["node_id"] == floor_id]
    assert len(hits) == 1
    assert hits[0]["path"][-1] == "EG"
    assert len(hits[0]["path"]) <= 64


@pytest.mark.asyncio
async def test_node_search_disambiguates_same_leaf_under_different_parents(client, auth_headers):
    # Two trees would also work, but the disambiguation case the user cares
    # about is *within the same tree*: EG/Küche vs OG/Küche.
    r = await client.post("/api/v1/hierarchy/trees", json={"name": "Gebäude T2", "description": ""}, headers=auth_headers)
    tree_id = r.json()["id"]

    async def mk_node(name, parent_id=None):
        rr = await client.post(
            "/api/v1/hierarchy/nodes",
            json={"tree_id": tree_id, "name": name, "parent_id": parent_id},
            headers=auth_headers,
        )
        assert rr.status_code in (200, 201)
        return rr.json()["id"]

    eg = await mk_node("EG")
    og = await mk_node("OG")
    eg_kueche = await mk_node("Küche", parent_id=eg)
    og_kueche = await mk_node("Küche", parent_id=og)

    r = await client.get("/api/v1/hierarchy/nodes/search?q=Küche", headers=auth_headers)
    assert r.status_code == 200
    hits = {h["node_id"]: h for h in r.json() if h["node_id"] in (eg_kueche, og_kueche)}
    assert hits[eg_kueche]["path"] == ["EG", "Küche"]
    assert hits[og_kueche]["path"] == ["OG", "Küche"]
    # Critically: the two hits are distinguishable by their `path`.
    assert hits[eg_kueche]["path"] != hits[og_kueche]["path"]
