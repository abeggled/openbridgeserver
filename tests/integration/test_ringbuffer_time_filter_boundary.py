"""Characterization tests for the ringbuffer time-filter boundary logic (issue #429).

Pins the current behaviour of POST /api/v1/ringbuffer/query when both an
absolute (`from` / `to`) and a relative (`from_relative_seconds` /
`to_relative_seconds`) bound is supplied:

  * effective FROM = max(absolute_from, now + from_relative_seconds)
                     (newer-wins → tighter lower bound)
  * effective TO   = min(absolute_to, now + to_relative_seconds)
                     (older-wins → tighter upper bound)

Tests are intentionally tolerant of clock skew between the server and the
test process — they use bounds that are well-separated from `now`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "RB Time Filter DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-time-filter-test"],
    "persist_value": False,
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _create_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={**_DP_BASE, "name": name},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: object) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def _query_v2(client, auth_headers, filters: dict) -> list[dict]:
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": filters,
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 200, "offset": 0},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_from_absolute_only_excludes_older_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB429 from-abs {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 1.0)
    await asyncio.sleep(0.05)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.05)
    await _write_value(client, auth_headers, dp["id"], 2.0)

    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {"from": _iso(cutoff)},
        },
    )
    assert rows
    # The first write must NOT be in the result.
    assert all(row["new_value"] == 2.0 for row in rows)


async def test_to_absolute_only_excludes_newer_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB429 to-abs {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 10.0)
    await asyncio.sleep(0.05)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.05)
    await _write_value(client, auth_headers, dp["id"], 20.0)

    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {"to": _iso(cutoff)},
        },
    )
    assert rows
    assert all(row["new_value"] == 10.0 for row in rows)


async def test_from_relative_only_keeps_recent_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB429 from-rel {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 1.0)
    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {"from_relative_seconds": -60},
        },
    )
    assert rows


async def test_from_absolute_and_relative_picks_newer_lower_bound(client, auth_headers):
    """When both `from` and `from_relative_seconds` are present, the server
    must use the NEWER of the two as the effective lower bound (tighter).

    Strategy: pick a very old absolute `from` (1h ago) and a relative `-1`
    (i.e. only entries newer than 1s ago). Then write a value AFTER the
    query is built but the relative window is already past — the entry
    must be excluded because relative wins.
    """
    dp = await _create_dp(client, auth_headers, f"RB429 abs+rel from {uuid.uuid4()}")

    # Old write — clearly before "now - 1s".
    await _write_value(client, auth_headers, dp["id"], 100.0)
    await asyncio.sleep(2.0)  # Make sure the old write is older than 1 second.

    far_past = datetime.now(UTC) - timedelta(hours=1)
    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {
                "from": _iso(far_past),  # very loose: 1h ago
                "from_relative_seconds": -1,  # very tight: 1s ago
            },
        },
    )
    # The 100.0 write happened ~2s ago and is older than the relative -1s
    # boundary, so the tighter relative bound wins and excludes it.
    assert all(row["new_value"] != 100.0 for row in rows), f"effective from must be the newer of absolute/relative, got rows={rows}"


async def test_to_absolute_and_relative_picks_older_upper_bound(client, auth_headers):
    """When both `to` and `to_relative_seconds` are present, the server must
    use the OLDER of the two as the effective upper bound (tighter).
    """
    dp = await _create_dp(client, auth_headers, f"RB429 abs+rel to {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 5.0)
    await asyncio.sleep(0.1)

    far_future = datetime.now(UTC) + timedelta(hours=1)

    # Build cutoff that is in the PAST (relative=-3600 means "3600 s ago").
    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {
                "to": _iso(far_future),  # very loose: 1h in future
                "to_relative_seconds": -3600,  # very tight: 1h ago
            },
        },
    )
    # The recent write happened ~0.1s ago, well after "1h ago", so the tighter
    # relative upper bound (older-wins) must exclude it.
    assert rows == [], f"effective to must be older of absolute/relative, got rows={rows}"


async def test_from_and_to_relative_combined_window(client, auth_headers):
    """A combined relative window `from_relative_seconds=-3600` and
    `to_relative_seconds=-1` must select entries older than 1s but
    newer than 1h ago.
    """
    dp = await _create_dp(client, auth_headers, f"RB429 rel-window {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 42.0)
    await asyncio.sleep(2.0)  # entry now ~2s old → inside the window

    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {
                "from_relative_seconds": -3600,
                "to_relative_seconds": -1,
            },
        },
    )
    assert any(row["new_value"] == 42.0 for row in rows)


async def test_relative_window_excluding_too_new_entries(client, auth_headers):
    """`to_relative_seconds=-3600` alone must exclude very recent writes."""
    dp = await _create_dp(client, auth_headers, f"RB429 rel-newer {uuid.uuid4()}")

    await _write_value(client, auth_headers, dp["id"], 99.0)

    rows = await _query_v2(
        client,
        auth_headers,
        {
            "datapoints": {"ids": [dp["id"]]},
            "time": {"to_relative_seconds": -3600},
        },
    )
    assert all(row["new_value"] != 99.0 for row in rows)
