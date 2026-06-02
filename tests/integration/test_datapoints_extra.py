"""Integration Tests — DataPoints API (supplemental coverage)

Covers uncovered paths in datapoints.py:
  GET  /api/v1/datapoints/tags              list unique tags
  POST /api/v1/datapoints/                  invalid data_type → 422
  PATCH /api/v1/datapoints/{id}             invalid data_type → 422
  GET  /api/v1/datapoints/{id}/value        unauthenticated paths (no page header → 401)
  POST /api/v1/datapoints/{id}/value        unauthenticated paths (no page header → 401)
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def _make_dp(client, auth_headers, name: str = "", tags: list[str] | None = None, data_type: str = "FLOAT") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name or f"DpExtra-{uuid.uuid4().hex[:8]}", "data_type": data_type, "tags": tags or []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /datapoints/tags
# ---------------------------------------------------------------------------


async def test_list_tags_requires_auth(client):
    resp = await client.get("/api/v1/datapoints/tags")
    assert resp.status_code == 401


async def test_list_tags_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/datapoints/tags", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_tags_includes_created_tag(client, auth_headers):
    unique_tag = f"tag-{uuid.uuid4().hex[:8]}"
    await _make_dp(client, auth_headers, tags=[unique_tag])
    resp = await client.get("/api/v1/datapoints/tags", headers=auth_headers)
    assert unique_tag in resp.json()


async def test_list_tags_sorted(client, auth_headers):
    tags = await client.get("/api/v1/datapoints/tags", headers=auth_headers)
    lst = tags.json()
    assert lst == sorted(lst)


async def test_list_tags_unique(client, auth_headers):
    tag = f"dup-{uuid.uuid4().hex[:6]}"
    await _make_dp(client, auth_headers, tags=[tag])
    await _make_dp(client, auth_headers, tags=[tag])
    resp = await client.get("/api/v1/datapoints/tags", headers=auth_headers)
    assert resp.json().count(tag) == 1


# ---------------------------------------------------------------------------
# POST /datapoints/  — invalid data_type
# ---------------------------------------------------------------------------


async def test_create_datapoint_invalid_type_returns_422(client, auth_headers):
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"BadType-{uuid.uuid4().hex[:6]}", "data_type": "NONEXISTENT_TYPE_XYZ"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /datapoints/{id}  — invalid data_type
# ---------------------------------------------------------------------------


async def test_patch_datapoint_invalid_type_returns_422(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}",
        json={"data_type": "NONEXISTENT_TYPE_XYZ"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /datapoints/{id}/value  — unauthenticated access control
# ---------------------------------------------------------------------------


async def test_get_value_no_auth_no_page_id_returns_401(client, auth_headers):
    # DP must exist — auth check runs after the 404 guard
    dp = await _make_dp(client, auth_headers)
    resp = await client.get(f"/api/v1/datapoints/{dp['id']}/value")
    assert resp.status_code == 401


async def test_get_value_with_jwt_returns_result(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    resp = await client.get(f"/api/v1/datapoints/{dp['id']}/value", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "value" in body
    assert "quality" in body


# ---------------------------------------------------------------------------
# POST /datapoints/{id}/value  — unauthenticated access control
# ---------------------------------------------------------------------------


async def test_write_value_no_auth_no_page_id_returns_401(client, auth_headers):
    # DP must exist — auth check runs after the 404 guard
    dp = await _make_dp(client, auth_headers)
    resp = await client.post(f"/api/v1/datapoints/{dp['id']}/value", json={"value": 1.0})
    assert resp.status_code == 401


async def test_write_value_with_jwt_succeeds(client, auth_headers):
    dp = await _make_dp(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{dp['id']}/value",
        json={"value": 42.0},
        headers=auth_headers,
    )
    assert resp.status_code == 204


async def test_write_value_404_for_unknown_dp(client, auth_headers):
    resp = await client.post(
        f"/api/v1/datapoints/{uuid.uuid4()}/value",
        json={"value": 1.0},
        headers=auth_headers,
    )
    assert resp.status_code == 404
