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


# ---------------------------------------------------------------------------
# PATCH /datapoints/{id}  — value field
# ---------------------------------------------------------------------------


async def test_patch_with_value_publishes_and_readable(client, auth_headers):
    dp = await _make_dp(client, auth_headers, data_type="FLOAT")
    dp_id = dp["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"value": 3.14},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    val_resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert val_resp.status_code == 200
    assert val_resp.json()["value"] == pytest.approx(3.14)


async def test_patch_value_and_metadata_together(client, auth_headers):
    dp = await _make_dp(client, auth_headers, name="PatchBoth", data_type="FLOAT")
    dp_id = dp["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"name": "PatchBothRenamed", "value": 7.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "PatchBothRenamed"

    val_resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert val_resp.json()["value"] == pytest.approx(7.0)


async def test_patch_metadata_only_does_not_overwrite_value(client, auth_headers):
    dp = await _make_dp(client, auth_headers, data_type="FLOAT")
    dp_id = dp["id"]

    await client.patch(f"/api/v1/datapoints/{dp_id}", json={"value": 99.0}, headers=auth_headers)

    resp = await client.patch(f"/api/v1/datapoints/{dp_id}", json={"unit": "kW"}, headers=auth_headers)
    assert resp.status_code == 200

    val_resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert val_resp.json()["value"] == pytest.approx(99.0)


async def test_patch_unit_null_clears_existing_unit(client, auth_headers):
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"ClearUnit-{uuid.uuid4().hex[:8]}", "data_type": "FLOAT", "unit": "km"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    dp_id = resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"unit": None},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["unit"] is None

    get_resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["unit"] is None


async def test_patch_value_type_mismatch_returns_422(client, auth_headers):
    dp = await _make_dp(client, auth_headers, data_type="INTEGER")
    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}",
        json={"value": "not-a-number"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_patch_value_mismatch_does_not_mutate_metadata(client, auth_headers):
    dp = await _make_dp(client, auth_headers, name="OriginalName", data_type="INTEGER")
    dp_id = dp["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"name": "ShouldNotChange", "value": "bad"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    get_resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert get_resp.json()["name"] == "OriginalName"


async def test_patch_value_int_coerced_from_float(client, auth_headers):
    dp = await _make_dp(client, auth_headers, data_type="INTEGER")
    resp = await client.patch(
        f"/api/v1/datapoints/{dp['id']}",
        json={"value": 5.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    val_resp = await client.get(f"/api/v1/datapoints/{dp['id']}/value", headers=auth_headers)
    assert val_resp.json()["value"] == 5


async def test_patch_explicit_null_publishes_uncertain(client, auth_headers):
    dp = await _make_dp(client, auth_headers, data_type="FLOAT")
    dp_id = dp["id"]
    await client.patch(f"/api/v1/datapoints/{dp_id}", json={"value": 42.0}, headers=auth_headers)

    resp = await client.patch(f"/api/v1/datapoints/{dp_id}", json={"value": None}, headers=auth_headers)
    assert resp.status_code == 200

    val_resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert val_resp.json()["quality"] == "uncertain"


async def test_patch_value_404_for_unknown_dp(client, auth_headers):
    resp = await client.patch(
        f"/api/v1/datapoints/{uuid.uuid4()}",
        json={"value": 1.0},
        headers=auth_headers,
    )
    assert resp.status_code == 404
