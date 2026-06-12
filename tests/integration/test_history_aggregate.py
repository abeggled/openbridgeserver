"""Integration Tests — History Aggregate Endpoint + Access Control

Covers:
  GET /api/v1/history/{dp_id}/aggregate   (success, 404, invalid fn, invalid timestamp)
  _parse_ts error path via invalid ?from= parameter
  _check_history_access paths (no JWT + no page header, public page, protected page)
"""

from __future__ import annotations

import asyncio
import datetime
import json
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


async def _create_page(client, auth_headers, name: str, access: str, *, access_pin: str | None = None) -> str:
    payload: dict[str, object] = {
        "name": name,
        "type": "PAGE",
        "order": 999,
        "access": access,
    }
    if access_pin is not None:
        payload["access_pin"] = access_pin

    resp = await client.post("/api/v1/visu/nodes", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_history_value(dp_id: str, ts: datetime.datetime, value: float) -> None:
    from obs.db.database import get_db

    db = get_db()
    ts_str = ts.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    await db.execute_and_commit(
        """INSERT INTO history_values (datapoint_id, value, unit, quality, ts)
           VALUES (?,?,?,?,?)""",
        (dp_id, json.dumps(value), "°C", "good", ts_str),
    )


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


async def test_aggregate_bucket_is_returned_as_utc_iso_timestamp(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    dp_id = dp["id"]
    await _seed_history_value(dp_id, datetime.datetime(2026, 6, 3, 12, 34, tzinfo=datetime.UTC), 12.0)

    resp = await client.get(
        f"/api/v1/history/{dp_id}/aggregate",
        params={
            "fn": "avg",
            "interval": "1h",
            "from": "2026-06-03T12:00:00Z",
            "to": "2026-06-03T13:00:00Z",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    buckets = resp.json()
    assert buckets
    assert buckets[0]["bucket"] == "2026-06-03T12:00:00Z"
    assert buckets[0]["n"] == 1
    assert datetime.datetime.fromisoformat(buckets[0]["bucket"].replace("Z", "+00:00")).tzinfo is not None


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


async def test_history_allows_public_page_without_jwt(client, auth_headers):
    dp = await _create_dp(client, auth_headers, name=f"HistPublic-{uuid.uuid4().hex[:8]}")
    page_id = await _create_page(client, auth_headers, f"hist-public-{uuid.uuid4().hex[:8]}", "public")
    try:
        resp = await client.get(f"/api/v1/history/{dp['id']}", headers={"X-Page-Id": page_id})
        assert resp.status_code == 200, resp.text
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp['id']}", headers=auth_headers)


async def test_history_protected_page_requires_valid_session_token(client, auth_headers):
    pin = "1234"
    dp = await _create_dp(client, auth_headers, name=f"HistProtected-{uuid.uuid4().hex[:8]}")
    page_id = await _create_page(
        client,
        auth_headers,
        f"hist-protected-{uuid.uuid4().hex[:8]}",
        "protected",
        access_pin=pin,
    )
    try:
        denied = await client.get(f"/api/v1/history/{dp['id']}", headers={"X-Page-Id": page_id})
        assert denied.status_code == 401, denied.text

        auth_resp = await client.post(f"/api/v1/visu/nodes/{page_id}/auth", json={"pin": pin})
        assert auth_resp.status_code == 200, auth_resp.text
        session_token = auth_resp.json()["session_token"]

        allowed = await client.get(
            f"/api/v1/history/{dp['id']}",
            headers={"X-Page-Id": page_id, "X-Session-Token": session_token},
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp['id']}", headers=auth_headers)
