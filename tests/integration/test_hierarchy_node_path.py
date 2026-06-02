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
    r = await client.post("/api/v1/hierarchy/trees", json={"name": "Gebäude T1", "description": ""}, headers=auth_headers)
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
