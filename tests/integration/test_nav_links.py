"""
Integration Tests — Nav Links (Issue #223)

Covers:
  GET    /api/v1/system/nav-links  → list
  POST   /api/v1/system/nav-links  → create (admin only)
  PATCH  /api/v1/system/nav-links/{id} → update (admin only)
  DELETE /api/v1/system/nav-links/{id} → delete (admin only)
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_link(client, auth_headers, **kwargs) -> dict:
    payload = {"label": "Test Link", "url": "https://example.com", **kwargs}
    resp = await client.post("/api/v1/system/nav-links", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _cleanup(client, auth_headers, link_id: str) -> None:
    await client.delete(f"/api/v1/system/nav-links/{link_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# GET /nav-links
# ---------------------------------------------------------------------------

async def test_list_nav_links_requires_auth(client):
    resp = await client.get("/api/v1/system/nav-links")
    assert resp.status_code == 401


async def test_list_nav_links_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/system/nav-links", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# POST /nav-links
# ---------------------------------------------------------------------------

async def test_create_nav_link_requires_auth(client):
    resp = await client.post("/api/v1/system/nav-links",
                             json={"label": "X", "url": "https://x.com"})
    assert resp.status_code == 401


async def test_create_nav_link_success(client, auth_headers):
    link = await _create_link(client, auth_headers,
                              label="Grafana", url="https://grafana.local",
                              icon="&#9881;", sort_order=0, open_new_tab=True)
    try:
        assert link["id"]
        assert link["label"] == "Grafana"
        assert link["url"] == "https://grafana.local"
        assert link["icon"] == "&#9881;"
        assert link["open_new_tab"] is True
    finally:
        await _cleanup(client, auth_headers, link["id"])


async def test_create_nav_link_appears_in_list(client, auth_headers):
    link = await _create_link(client, auth_headers, label="Visible", url="https://visible.test")
    try:
        resp = await client.get("/api/v1/system/nav-links", headers=auth_headers)
        ids = [l["id"] for l in resp.json()]
        assert link["id"] in ids
    finally:
        await _cleanup(client, auth_headers, link["id"])


async def test_create_nav_link_defaults(client, auth_headers):
    link = await _create_link(client, auth_headers)
    try:
        assert link["icon"] == ""
        assert link["sort_order"] == 0
        assert link["open_new_tab"] is True
    finally:
        await _cleanup(client, auth_headers, link["id"])


# ---------------------------------------------------------------------------
# PATCH /nav-links/{id}
# ---------------------------------------------------------------------------

async def test_update_nav_link_success(client, auth_headers):
    link = await _create_link(client, auth_headers, label="Old", url="https://old.com")
    try:
        resp = await client.patch(
            f"/api/v1/system/nav-links/{link['id']}",
            json={"label": "New", "open_new_tab": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["label"] == "New"
        assert updated["url"] == "https://old.com"  # unchanged
        assert updated["open_new_tab"] is False
    finally:
        await _cleanup(client, auth_headers, link["id"])


async def test_update_nav_link_not_found(client, auth_headers):
    resp = await client.patch(
        "/api/v1/system/nav-links/nonexistent-id",
        json={"label": "X"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /nav-links/{id}
# ---------------------------------------------------------------------------

async def test_delete_nav_link_success(client, auth_headers):
    link = await _create_link(client, auth_headers)
    resp = await client.delete(f"/api/v1/system/nav-links/{link['id']}", headers=auth_headers)
    assert resp.status_code == 204

    # Verify gone from list
    list_resp = await client.get("/api/v1/system/nav-links", headers=auth_headers)
    ids = [l["id"] for l in list_resp.json()]
    assert link["id"] not in ids


async def test_delete_nav_link_not_found(client, auth_headers):
    resp = await client.delete("/api/v1/system/nav-links/nonexistent-id", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------

async def test_nav_links_sorted_by_sort_order(client, auth_headers):
    link_b = await _create_link(client, auth_headers, label="B", url="https://b.com", sort_order=10)
    link_a = await _create_link(client, auth_headers, label="A", url="https://a.com", sort_order=1)
    try:
        resp = await client.get("/api/v1/system/nav-links", headers=auth_headers)
        links = resp.json()
        ids = [l["id"] for l in links]
        assert ids.index(link_a["id"]) < ids.index(link_b["id"])
    finally:
        await _cleanup(client, auth_headers, link_a["id"])
        await _cleanup(client, auth_headers, link_b["id"])
