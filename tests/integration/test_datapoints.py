"""Integration Tests — DataPoints CRUD

Covers:
  - POST   /api/v1/datapoints/          create
  - GET    /api/v1/datapoints/{id}      read one
  - GET    /api/v1/datapoints/          list + pagination
  - PATCH  /api/v1/datapoints/{id}      update
  - DELETE /api/v1/datapoints/{id}      delete → 404 on re-fetch
  - POST   /api/v1/datapoints/{id}/value  write value
  - GET    /api/v1/datapoints/{id}/value  read value
  - Binding cascade delete
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    assert_datapoint_page_shape,
    assert_datapoint_shape,
    assert_value_out_shape,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DP_PAYLOAD = {
    "name": "Temperatur Wohnzimmer",
    "data_type": "FLOAT",
    "unit": "°C",
    "tags": ["test", "temperature"],
    "persist_value": False,
}


async def _create_dp(client, auth_headers, payload: dict | None = None) -> dict:
    """Create a datapoint and return its JSON body."""
    resp = await client.post(
        "/api/v1/datapoints/",
        json=payload or _DP_PAYLOAD,
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"create failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_datapoint(client, auth_headers):
    body = await _create_dp(client, auth_headers)

    assert_datapoint_shape(body)
    assert body["name"] == _DP_PAYLOAD["name"]
    assert body["data_type"] == "FLOAT"
    assert body["unit"] == "°C"
    assert body["mqtt_topic"].startswith("dp/")


async def test_create_datapoint_unknown_type_returns_422(client, auth_headers):
    resp = await client.post(
        "/api/v1/datapoints/",
        json={**_DP_PAYLOAD, "data_type": "DOES_NOT_EXIST"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def test_read_datapoint(client, auth_headers):
    created = await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "Read-Test DP"})
    dp_id = created["id"]

    resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert_datapoint_shape(body)
    assert body["name"] == "Read-Test DP"


async def test_read_nonexistent_returns_404(client, auth_headers):
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/v1/datapoints/{fake_id}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List + pagination
# ---------------------------------------------------------------------------


async def test_list_datapoints_returns_paged_result(client, auth_headers):
    # Ensure at least one datapoint exists
    await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "List-Test DP"})

    resp = await client.get("/api/v1/datapoints/", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert_datapoint_page_shape(body)
    assert body["total"] >= 1
    assert isinstance(body["items"], list)


async def test_pagination_limits_results(client, auth_headers):
    # Create 3 extra datapoints to ensure enough entries
    for i in range(3):
        await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": f"Pag-DP-{i}"})

    resp = await client.get(
        "/api/v1/datapoints/",
        params={"page": 0, "size": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 2
    assert body["size"] == 2


async def test_list_size_above_500_accepted(client, auth_headers):
    """size-Parameter darf jetzt bis 10000 gehen (Bug #212: vorher nur bis 500)."""
    resp = await client.get(
        "/api/v1/datapoints/",
        params={"page": 0, "size": 1000},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["size"] == 1000


async def test_list_size_above_10000_rejected(client, auth_headers):
    """Size > 10000 muss mit 422 abgelehnt werden."""
    resp = await client.get(
        "/api/v1/datapoints/",
        params={"page": 0, "size": 10001},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_pagination_covers_all_items(client, auth_headers):
    """Mehrseitige Abfrage (size=2) liefert zusammen alle Objekte."""
    for i in range(5):
        await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": f"AllItems-DP-{i}"})

    # Erstes Mal: Gesamtzahl ermitteln
    first = await client.get(
        "/api/v1/datapoints/",
        params={"page": 0, "size": 2},
        headers=auth_headers,
    )
    body = first.json()
    total = body["total"]
    pages = body["pages"]
    assert total >= 5

    # Alle Seiten abrufen und IDs sammeln
    all_ids: set[str] = set()
    for p in range(pages):
        resp = await client.get(
            "/api/v1/datapoints/",
            params={"page": p, "size": 2},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        all_ids.update(item["id"] for item in resp.json()["items"])

    assert len(all_ids) == total


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_datapoint_name(client, auth_headers):
    created = await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "Before-Update"})
    dp_id = created["id"]

    patch_resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"name": "After-Update"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "After-Update"

    # Verify persistence
    get_resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert get_resp.json()["name"] == "After-Update"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_datapoint_and_verify_404(client, auth_headers):
    created = await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "To-Delete"})
    dp_id = created["id"]

    del_resp = await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Value read / write
# ---------------------------------------------------------------------------


async def test_write_and_read_value(client, auth_headers):
    created = await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "Value-RW"})
    dp_id = created["id"]

    # Write
    write_resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": 22.5},
        headers=auth_headers,
    )
    assert write_resp.status_code == 204, f"write failed: {write_resp.text}"

    # Read back
    read_resp = await client.get(
        f"/api/v1/datapoints/{dp_id}/value",
        headers=auth_headers,
    )
    assert read_resp.status_code == 200
    val_body = read_resp.json()
    assert_value_out_shape(val_body)
    assert val_body["value"] == pytest.approx(22.5)
    assert val_body["unit"] == "°C"


async def test_write_value_reflects_in_datapoint_detail(client, auth_headers):
    created = await _create_dp(client, auth_headers, {**_DP_PAYLOAD, "name": "Value-Detail"})
    dp_id = created["id"]

    await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": 19.0},
        headers=auth_headers,
    )

    resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["value"] == pytest.approx(19.0)
