"""Integration tests for the export preflight count endpoint.

``POST /api/v1/ringbuffer/filtersets/export/count`` returns the number of rows
that the matching ``POST /filtersets/export/csv`` call would write. The GUI
uses this to warn the user before triggering a large download.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "Ringbuffer Export Count DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-export-count-test"],
    "persist_value": False,
}


@pytest.fixture(autouse=True)
async def _cleanup_dps(client, auth_headers):
    yield
    try:
        resp = await client.get(
            "/api/v1/search/",
            params={"q": "RBCOUNT", "size": 500},
            headers=auth_headers,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                await client.delete(f"/api/v1/datapoints/{item['id']}", headers=auth_headers)
    except Exception:  # noqa: BLE001
        pass


async def _create_dp(client, auth_headers, name: str, *, tags: list[str]) -> dict:
    payload = {**_DP_BASE, "name": name, "tags": tags}
    resp = await client.post("/api/v1/datapoints/", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: object) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def _create_filterset(client, auth_headers, payload: dict) -> dict:
    resp = await client.post("/api/v1/ringbuffer/filtersets", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_filterset(client, auth_headers, filterset_id: str) -> None:
    await client.delete(f"/api/v1/ringbuffer/filtersets/{filterset_id}", headers=auth_headers)


async def _post_count(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/filtersets/export/count",
        json=body,
        headers=auth_headers,
    )


async def _post_export(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/filtersets/export/csv",
        json=body,
        headers=auth_headers,
    )


async def test_count_matches_export_row_count(client, auth_headers):
    """Preflight count must equal the X-RingBuffer-Export-Rows header of the actual export."""
    tag = f"rbcount-{uuid.uuid4().hex[:8]}"
    dp_a = await _create_dp(client, auth_headers, f"RBCOUNT A {uuid.uuid4()}", tags=[tag])
    dp_b = await _create_dp(client, auth_headers, f"RBCOUNT B {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp_a["id"], 1.0)
    await _write_value(client, auth_headers, dp_b["id"], 2.0)
    await _write_value(client, auth_headers, dp_a["id"], 3.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RBCOUNT set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        count_resp = await _post_count(client, auth_headers, {"set_ids": [set_id]})
        assert count_resp.status_code == 200, count_resp.text
        count = count_resp.json()["row_count"]
        assert count >= 3

        export_resp = await _post_export(client, auth_headers, {"set_ids": [set_id]})
        assert export_resp.status_code == 200, export_resp.text
        export_rows = int(export_resp.headers["x-ringbuffer-export-rows"])
        assert count == export_rows
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_count_with_empty_set_ids_returns_unfiltered_total(client, auth_headers):
    """Empty set_ids ⇒ unfiltered recent entries; count must be > 0 after a write."""
    dp = await _create_dp(client, auth_headers, f"RBCOUNT empty {uuid.uuid4()}", tags=["rbcount-empty"])
    await _write_value(client, auth_headers, dp["id"], 11.0)

    resp = await _post_count(client, auth_headers, {"set_ids": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] >= 1


async def test_count_unknown_set_id_is_skipped_like_export(client, auth_headers):
    """Unknown filterset IDs are silently skipped — matches the export's tolerance."""
    set_a = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RBCOUNT known {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
        )
    )["id"]
    try:
        resp = await _post_count(
            client,
            auth_headers,
            {"set_ids": [set_a, str(uuid.uuid4())]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["row_count"] >= 0
    finally:
        await _delete_filterset(client, auth_headers, set_a)


async def test_count_rejects_unknown_fields(client, auth_headers):
    """Delimiter/encoding options on the count endpoint are a programming error → 422."""
    resp = await _post_count(client, auth_headers, {"set_ids": [], "delimiter": ","})
    assert resp.status_code == 422
