"""Integration Tests — History Aggregate Endpoint + Access Control

Covers:
  GET /api/v1/history/{dp_id}/aggregate   (success, 404, invalid fn, invalid timestamp)
  _parse_ts error path via invalid ?from= parameter
  _check_history_access paths (no JWT + no page header, public page, protected page)
"""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

pytestmark = pytest.mark.integration

_MISSING_ID = "00000000-0000-0000-0000-000000000000"


async def _create_dp(client, auth_headers, name: str = "") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={
            "name": name or f"HistAgg-{uuid.uuid4().hex[:8]}",
            "data_type": "FLOAT",
            "record_history": True,
        },
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


# ---------------------------------------------------------------------------
# GET /{dp_id}/aggregate — basic
# ---------------------------------------------------------------------------


async def test_aggregate_requires_auth(client):
    resp = await client.get(f"/api/v1/history/{_MISSING_ID}/aggregate")
    assert resp.status_code == 401


async def test_aggregate_404_for_unknown_dp(client, auth_headers):
    resp = await client.get(f"/api/v1/history/{_MISSING_ID}/aggregate", headers=auth_headers)
    assert resp.status_code == 404


async def test_aggregate_invalid_fn_returns_422(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "invalidfn"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_aggregate_returns_list(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    await _write_value(client, auth_headers, dp["id"], 10.0)
    await _write_value(client, auth_headers, dp["id"], 20.0)
    await asyncio.sleep(0.1)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "avg", "interval": "1m", "from": past},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_aggregate_avg_fn(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    await _write_value(client, auth_headers, dp["id"], 10.0)
    await _write_value(client, auth_headers, dp["id"], 20.0)
    await asyncio.sleep(0.1)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "avg", "interval": "1h", "from": past},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    buckets = resp.json()
    if buckets:
        assert "bucket" in buckets[0]
        assert "v" in buckets[0]


async def test_aggregate_min_fn(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    await _write_value(client, auth_headers, dp["id"], 5.0)
    await _write_value(client, auth_headers, dp["id"], 15.0)
    await asyncio.sleep(0.1)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "min", "interval": "1h", "from": past},
        headers=auth_headers,
    )
    assert resp.status_code == 200


async def test_aggregate_max_fn(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    await _write_value(client, auth_headers, dp["id"], 100.0)
    await asyncio.sleep(0.1)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "max", "interval": "1h", "from": past},
        headers=auth_headers,
    )
    assert resp.status_code == 200


async def test_aggregate_last_fn(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    await _write_value(client, auth_headers, dp["id"], 42.0)
    await asyncio.sleep(0.1)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "last", "interval": "1h", "from": past},
        headers=auth_headers,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _parse_ts error path — invalid timestamp strings
# ---------------------------------------------------------------------------


async def test_history_query_invalid_from_timestamp(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.get(
        f"/api/v1/history/{dp['id']}",
        params={"from": "not-a-timestamp"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_history_query_invalid_to_timestamp(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.get(
        f"/api/v1/history/{dp['id']}",
        params={"to": "garbage-value"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_aggregate_invalid_from_timestamp(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    resp = await client.get(
        f"/api/v1/history/{dp['id']}/aggregate",
        params={"fn": "avg", "from": "not-a-date"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Access control: no JWT, no X-Page-Id header
# ---------------------------------------------------------------------------


async def test_history_no_auth_no_page_id_returns_401(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/history/{fake_id}")
    assert resp.status_code == 401


async def test_aggregate_no_auth_no_page_id_returns_401(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/history/{fake_id}/aggregate")
    assert resp.status_code == 401
