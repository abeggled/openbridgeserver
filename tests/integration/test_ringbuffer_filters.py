"""Integration baseline tests for /api/v1/ringbuffer filter parameters."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "Ringbuffer Filter Test DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-filter-test"],
    "persist_value": False,
}


async def _create_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={**_DP_BASE, "name": name},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: float) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def _query_ringbuffer(client, auth_headers, params: dict) -> list[dict]:
    resp = await client.get(
        "/api/v1/ringbuffer/",
        params=params,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_ringbuffer_filter_basics_q_adapter_from_and_limit(client, auth_headers):
    dp_a = await _create_dp(client, auth_headers, "RB Filter A")
    dp_b = await _create_dp(client, auth_headers, "RB Filter B")

    await _write_value(client, auth_headers, dp_a["id"], 10.0)
    first_for_a = await _query_ringbuffer(
        client,
        auth_headers,
        {"q": dp_a["id"], "limit": 1},
    )
    assert len(first_for_a) == 1
    assert first_for_a[0]["datapoint_id"] == dp_a["id"]
    first_ts = first_for_a[0]["ts"]

    # Ensure later write gets a strictly newer timestamp than first_ts.
    await asyncio.sleep(0.02)
    await _write_value(client, auth_headers, dp_a["id"], 11.0)
    await _write_value(client, auth_headers, dp_b["id"], 20.0)

    by_adapter = await _query_ringbuffer(
        client,
        auth_headers,
        {"adapter": "api", "limit": 2},
    )
    assert len(by_adapter) == 2
    assert all(entry["source_adapter"] == "api" for entry in by_adapter)

    from_filtered = await _query_ringbuffer(
        client,
        auth_headers,
        {"q": dp_a["id"], "from": first_ts, "limit": 10},
    )
    assert from_filtered
    assert len(from_filtered) <= 10
    assert all(entry["datapoint_id"] == dp_a["id"] for entry in from_filtered)
    assert all(entry["ts"] > first_ts for entry in from_filtered)
    assert from_filtered[0]["new_value"] == pytest.approx(11.0)


async def test_ringbuffer_from_filter_is_exclusive_at_equal_timestamp(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB From Equal Boundary")
    await _write_value(client, auth_headers, dp["id"], 55.0)

    rows = await _query_ringbuffer(
        client,
        auth_headers,
        {"q": dp["id"], "limit": 1},
    )
    assert len(rows) == 1
    exact_ts = rows[0]["ts"]

    equal_boundary = await _query_ringbuffer(
        client,
        auth_headers,
        {"q": dp["id"], "from": exact_ts, "limit": 10},
    )
    assert equal_boundary == []


async def test_ringbuffer_limit_validation_rejects_zero(client, auth_headers):
    resp = await client.get(
        "/api/v1/ringbuffer/",
        params={"limit": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_ringbuffer_config_rejects_invalid_storage_mode(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"storage": "invalid", "max_entries": 100},
        headers=auth_headers,
    )
    assert resp.status_code == 422
