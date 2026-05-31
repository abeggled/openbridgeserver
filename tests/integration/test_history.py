"""Integration Tests — History Objekt-Filter (issue #178)

Deckt ab:
  - record_history=True  (Standard): Werte werden in die Historie geschrieben
  - record_history=False: Werte werden NICHT in die Historie geschrieben
  - PATCH /api/v1/datapoints/{id} setzt record_history korrekt
  - record_history wird im GET-Response zurückgeliefert
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_dp(client, auth_headers, name: str, record_history: bool = True) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={
            "name": name,
            "data_type": "FLOAT",
            "unit": "°C",
            "record_history": record_history,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"create failed: {resp.text}"
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: float) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, f"write value failed: {resp.text}"


async def _query_history(client, auth_headers, dp_id: str) -> list:
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp_id}",
        params={"from": past, "limit": 100},
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"history query failed: {resp.text}"
    return resp.json()


async def _seed_history_value(
    dp_id: str,
    ts: datetime.datetime,
    value: float,
    unit: str = "°C",
    quality: str = "good",
) -> None:
    """Insert a synthetic history row with a precise timestamp."""
    from obs.db.database import get_db

    db = get_db()
    ts_str = ts.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    await db.execute_and_commit(
        """INSERT INTO history_values (datapoint_id, value, unit, quality, ts)
           VALUES (?,?,?,?,?)""",
        (dp_id, json.dumps(value), unit, quality, ts_str),
    )


async def _set_history_default_window_hours(hours: int) -> None:
    from obs.db.database import get_db

    db = get_db()
    await db.execute_and_commit(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('history.default_window_hours', ?)",
        (str(hours),),
    )


async def _set_history_default_window_raw(value: str) -> None:
    from obs.db.database import get_db

    db = get_db()
    await db.execute_and_commit(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('history.default_window_hours', ?)",
        (value,),
    )


# ---------------------------------------------------------------------------
# Tests: record_history field returned in API responses
# ---------------------------------------------------------------------------


async def test_create_datapoint_default_record_history(client, auth_headers):
    """record_history defaults to True when not specified."""
    dp = await _create_dp(client, auth_headers, f"HistTest-Default-{uuid.uuid4().hex[:6]}")
    assert dp["record_history"] is True


async def test_create_datapoint_record_history_false(client, auth_headers):
    """record_history=False is stored and returned correctly."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Excluded-{uuid.uuid4().hex[:6]}",
        record_history=False,
    )
    assert dp["record_history"] is False


async def test_get_datapoint_returns_record_history(client, auth_headers):
    """GET /datapoints/{id} includes record_history field."""
    created = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Get-{uuid.uuid4().hex[:6]}",
        record_history=False,
    )
    resp = await client.get(f"/api/v1/datapoints/{created['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["record_history"] is False


async def test_patch_datapoint_enables_record_history(client, auth_headers):
    """PATCH can enable record_history after creation."""
    created = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Patch-{uuid.uuid4().hex[:6]}",
        record_history=False,
    )
    dp_id = created["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"record_history": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["record_history"] is True

    # Verify persisted
    resp2 = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert resp2.json()["record_history"] is True


async def test_patch_datapoint_disables_record_history(client, auth_headers):
    """PATCH can disable record_history after creation."""
    created = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Disable-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = created["id"]

    resp = await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"record_history": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["record_history"] is False


# ---------------------------------------------------------------------------
# Tests: History recording behaviour
# ---------------------------------------------------------------------------


async def test_history_recorded_when_enabled(client, auth_headers):
    """Values are written to history when record_history=True."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Enabled-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = dp["id"]

    await _write_value(client, auth_headers, dp_id, 21.5)
    # Give the async EventBus handler a moment to write
    await asyncio.sleep(0.1)

    entries = await _query_history(client, auth_headers, dp_id)
    assert len(entries) >= 1, "Expected at least one history entry"
    assert any(abs(e["v"] - 21.5) < 0.01 for e in entries), "Value 21.5 not found in history"


async def test_history_not_recorded_when_disabled(client, auth_headers):
    """Values are NOT written to history when record_history=False."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Disabled-{uuid.uuid4().hex[:6]}",
        record_history=False,
    )
    dp_id = dp["id"]

    await _write_value(client, auth_headers, dp_id, 99.9)
    await asyncio.sleep(0.1)

    entries = await _query_history(client, auth_headers, dp_id)
    assert len(entries) == 0, f"Expected no history entries, got {len(entries)}"


async def test_history_stops_after_disabling(client, auth_headers):
    """After disabling record_history, subsequent values are no longer recorded."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Stop-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = dp["id"]

    # Write while enabled
    await _write_value(client, auth_headers, dp_id, 10.0)
    await asyncio.sleep(0.1)
    entries_before = await _query_history(client, auth_headers, dp_id)
    assert len(entries_before) >= 1

    # Disable
    await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"record_history": False},
        headers=auth_headers,
    )

    # Write while disabled
    await _write_value(client, auth_headers, dp_id, 20.0)
    await asyncio.sleep(0.1)
    entries_after = await _query_history(client, auth_headers, dp_id)

    # Count must not have increased — no new entry with value 20.0
    assert not any(abs(e["v"] - 20.0) < 0.01 for e in entries_after), "Value 20.0 was recorded even though record_history=False"


async def test_history_limit_above_1000(client, auth_headers):
    """API must accept limit values above 1000 (bug #316 regression guard)."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Limit-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = dp["id"]

    # Write two values
    for v in (1.0, 2.0):
        await _write_value(client, auth_headers, dp_id, v)
    await asyncio.sleep(0.1)

    # Requesting limit=10000 must be accepted (not rejected with 422)
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp_id}",
        params={"from": past, "limit": 10000},
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"limit=10000 was rejected: {resp.text}"
    assert len(resp.json()) >= 2


async def test_history_resumes_after_enabling(client, auth_headers):
    """After re-enabling record_history, values are recorded again."""
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-Resume-{uuid.uuid4().hex[:6]}",
        record_history=False,
    )
    dp_id = dp["id"]

    # Write while disabled — must not be recorded
    await _write_value(client, auth_headers, dp_id, 5.0)
    await asyncio.sleep(0.1)
    assert len(await _query_history(client, auth_headers, dp_id)) == 0

    # Enable
    await client.patch(
        f"/api/v1/datapoints/{dp_id}",
        json={"record_history": True},
        headers=auth_headers,
    )

    # Write while enabled — must be recorded
    await _write_value(client, auth_headers, dp_id, 42.0)
    await asyncio.sleep(0.1)
    entries = await _query_history(client, auth_headers, dp_id)
    assert any(abs(e["v"] - 42.0) < 0.01 for e in entries), "Value 42.0 not found after re-enabling"


async def test_history_default_window_is_last_7d(client, auth_headers):
    """Without ?from=..., history endpoint defaults to the last 7 days."""
    await _set_history_default_window_hours(168)
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-WindowDefault-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = dp["id"]

    now = datetime.datetime.now(datetime.UTC)
    old_ts = now - datetime.timedelta(days=8)
    recent_ts = now - datetime.timedelta(days=1)
    await _seed_history_value(dp_id, old_ts, 11.0)
    await _seed_history_value(dp_id, recent_ts, 22.0)

    resp = await client.get(f"/api/v1/history/{dp_id}", headers=auth_headers)
    assert resp.status_code == 200, f"history query failed: {resp.text}"
    values = [entry["v"] for entry in resp.json()]
    assert 22.0 in values, "Recent value (within 7d) missing"
    assert 11.0 not in values, "Old value (>7d) should be filtered by default window"


async def test_history_explicit_from_can_read_older_than_7d(client, auth_headers):
    """With explicit ?from=..., values older than 7d must be returned."""
    await _set_history_default_window_hours(168)
    dp = await _create_dp(
        client,
        auth_headers,
        f"HistTest-WindowExplicit-{uuid.uuid4().hex[:6]}",
        record_history=True,
    )
    dp_id = dp["id"]

    now = datetime.datetime.now(datetime.UTC)
    old_ts = now - datetime.timedelta(days=8)
    recent_ts = now - datetime.timedelta(days=1)
    await _seed_history_value(dp_id, old_ts, 33.0)
    await _seed_history_value(dp_id, recent_ts, 44.0)

    resp = await client.get(
        f"/api/v1/history/{dp_id}",
        params={
            "from": (now - datetime.timedelta(days=14)).isoformat(),
            "to": now.isoformat(),
            "limit": 1000,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"history query failed: {resp.text}"
    values = [entry["v"] for entry in resp.json()]
    assert 33.0 in values, "Old value should be included when explicit from is provided"
    assert 44.0 in values, "Recent value should be included"


async def test_history_default_window_can_be_changed_via_settings(client, auth_headers):
    """Changing history.default_window_hours affects API default queries."""
    await _set_history_default_window_hours(24 * 10)  # 10 days
    try:
        dp = await _create_dp(
            client,
            auth_headers,
            f"HistTest-WindowConfig-{uuid.uuid4().hex[:6]}",
            record_history=True,
        )
        dp_id = dp["id"]

        now = datetime.datetime.now(datetime.UTC)
        old_ts = now - datetime.timedelta(days=8)
        recent_ts = now - datetime.timedelta(days=1)
        await _seed_history_value(dp_id, old_ts, 55.0)
        await _seed_history_value(dp_id, recent_ts, 66.0)

        resp = await client.get(f"/api/v1/history/{dp_id}", headers=auth_headers)
        assert resp.status_code == 200, f"history query failed: {resp.text}"
        values = [entry["v"] for entry in resp.json()]
        assert 55.0 in values, "Old value should be visible with 10d default window"
        assert 66.0 in values
    finally:
        # Restore project default for subsequent tests.
        await _set_history_default_window_hours(168)


async def test_history_default_window_invalid_value_falls_back_to_default(client, auth_headers):
    """Invalid history.default_window_hours falls back to 7-day default window."""
    await _set_history_default_window_raw("not-a-number")
    try:
        dp = await _create_dp(
            client,
            auth_headers,
            f"HistTest-WindowInvalid-{uuid.uuid4().hex[:6]}",
            record_history=True,
        )
        dp_id = dp["id"]

        now = datetime.datetime.now(datetime.UTC)
        old_ts = now - datetime.timedelta(days=8)
        recent_ts = now - datetime.timedelta(days=1)
        await _seed_history_value(dp_id, old_ts, 77.0)
        await _seed_history_value(dp_id, recent_ts, 88.0)

        resp = await client.get(f"/api/v1/history/{dp_id}", headers=auth_headers)
        assert resp.status_code == 200, f"history query failed: {resp.text}"
        values = [entry["v"] for entry in resp.json()]
        assert 88.0 in values, "Recent value (within 7d) missing"
        assert 77.0 not in values, "Old value (>7d) should be filtered by fallback default"
    finally:
        await _set_history_default_window_hours(168)
