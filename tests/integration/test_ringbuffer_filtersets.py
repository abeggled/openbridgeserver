"""Integration tests for the flat RingBuffer filterset API (#431).

Replaces the original test suite from #389 that exercised the deprecated
group/rule hierarchy. The flat schema stores one ``FilterCriteria`` per set
plus a ``color``, ``topbar_active`` and ``topbar_order`` column; the legacy
``groups[]`` body is still accepted on POST/PUT through a backwards-compat
shim that emits :class:`DeprecationWarning` (removed in #438).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "Ringbuffer Filterset Test DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-filterset-test"],
    "persist_value": False,
}


async def _create_dp(client, auth_headers, name: str, *, tags: list[str] | None = None) -> dict:
    payload = {**_DP_BASE, "name": name}
    if tags is not None:
        payload["tags"] = tags
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


async def _create_non_admin_user_and_headers(client, auth_headers, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/users",
        json={
            "username": username,
            "password": password,
            "is_admin": False,
            "mqtt_enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    from obs.api.auth import create_access_token

    token = create_access_token(username)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Flat CRUD
# ---------------------------------------------------------------------------


async def test_filterset_crud_round_trip(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB Flat CRUD {uuid.uuid4()}")

    payload = {
        "name": f"RB Flat {uuid.uuid4()}",
        "description": "flat schema round-trip",
        "is_active": True,
        "color": "#10b981",
        "topbar_active": True,
        "topbar_order": 7,
        "filter": {
            "datapoints": [dp["id"]],
            "tags": ["ringbuffer-filterset-test"],
            "adapters": ["api"],
        },
    }
    created = await _create_filterset(client, auth_headers, payload)
    try:
        assert created["color"] == "#10b981"
        assert created["topbar_active"] is True
        assert created["topbar_order"] == 7
        assert created["filter"]["datapoints"] == [dp["id"]]
        assert created["filter"]["tags"] == ["ringbuffer-filterset-test"]
        assert created["filter"]["adapters"] == ["api"]
        assert "groups" not in created  # Flat schema must not expose the legacy field.

        # GET single
        resp = await client.get(f"/api/v1/ringbuffer/filtersets/{created['id']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == created["id"]

        # LIST contains it
        list_resp = await client.get("/api/v1/ringbuffer/filtersets", headers=auth_headers)
        assert list_resp.status_code == 200, list_resp.text
        listed_ids = {row["id"] for row in list_resp.json()}
        assert created["id"] in listed_ids

        # PUT — change a single field
        put_resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{created['id']}",
            json={"name": f"{payload['name']} Updated", "color": "#ef4444"},
            headers=auth_headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        updated = put_resp.json()
        assert updated["name"].endswith("Updated")
        assert updated["color"] == "#ef4444"
        # Other fields preserved
        assert updated["filter"]["datapoints"] == [dp["id"]]
    finally:
        await _delete_filterset(client, auth_headers, created["id"])


async def test_filterset_clone_resets_topbar_active(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {
            "name": f"RB Clone Source {uuid.uuid4()}",
            "color": "#3b82f6",
            "topbar_active": True,
            "topbar_order": 4,
            "filter": {"adapters": ["api"]},
        },
    )
    clone_id = None
    try:
        clone_resp = await client.post(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/clone",
            json={"name": f"{created['name']} Clone"},
            headers=auth_headers,
        )
        assert clone_resp.status_code == 201, clone_resp.text
        clone = clone_resp.json()
        clone_id = clone["id"]
        assert clone["id"] != created["id"]
        assert clone["color"] == created["color"]
        # Clones must NOT inherit topbar activation — fresh sets are off by default.
        assert clone["topbar_active"] is False
        # Filter is preserved verbatim.
        assert clone["filter"] == created["filter"]
    finally:
        if clone_id:
            await _delete_filterset(client, auth_headers, clone_id)
        await _delete_filterset(client, auth_headers, created["id"])


async def test_filterset_color_validation_rejects_garbage(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/filtersets",
        json={
            "name": "RB color bad",
            "color": "not-a-color",
            "filter": {},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_filterset_color_validation_accepts_hex_variants(client, auth_headers):
    for color in ("#abc", "#abcdef", "#3b82f6", "#3B82F6FF"):
        created = await _create_filterset(
            client,
            auth_headers,
            {
                "name": f"RB color {color} {uuid.uuid4()}",
                "color": color,
                # Backend rejects empty FilterCriteria — add a marker tag so
                # the colour-validation path is exercised in isolation.
                "filter": {"tags": ["rb-color-test"]},
            },
        )
        try:
            assert created["color"] == color
        finally:
            await _delete_filterset(client, auth_headers, created["id"])


# ---------------------------------------------------------------------------
# Topbar PATCH
# ---------------------------------------------------------------------------


async def test_patch_topbar_toggles_active_and_order(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {
            "name": f"RB Topbar {uuid.uuid4()}",
            "filter": {"adapters": ["api"]},
        },
    )
    try:
        assert created["topbar_active"] is False
        assert created["topbar_order"] == 0

        resp = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/topbar",
            json={"topbar_active": True, "topbar_order": 12},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["topbar_active"] is True
        assert updated["topbar_order"] == 12

        # Toggle off only; order preserved.
        resp = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/topbar",
            json={"topbar_active": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["topbar_active"] is False
        assert updated["topbar_order"] == 12
    finally:
        await _delete_filterset(client, auth_headers, created["id"])


async def test_patch_topbar_unknown_id_returns_404(client, auth_headers):
    resp = await client.patch(
        f"/api/v1/ringbuffer/filtersets/{uuid.uuid4()}/topbar",
        json={"topbar_active": True},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text


async def test_patch_order_batch(client, auth_headers):
    a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB Order A {uuid.uuid4()}", "topbar_order": 0, "filter": {"tags": ["rb-order-test"]}},
    )
    b = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB Order B {uuid.uuid4()}", "topbar_order": 0, "filter": {"tags": ["rb-order-test"]}},
    )
    try:
        resp = await client.patch(
            "/api/v1/ringbuffer/filtersets/order",
            json={
                "items": [
                    {"id": a["id"], "topbar_order": 5},
                    {"id": b["id"], "topbar_order": 2},
                    # Unknown IDs must be silently ignored to survive racing deletes.
                    {"id": str(uuid.uuid4()), "topbar_order": 99},
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        by_id = {row["id"]: row for row in resp.json()}
        assert by_id[a["id"]]["topbar_order"] == 5
        assert by_id[b["id"]]["topbar_order"] == 2
    finally:
        await _delete_filterset(client, auth_headers, a["id"])
        await _delete_filterset(client, auth_headers, b["id"])


# ---------------------------------------------------------------------------
# Multi-set query — OR-union with matched_set_ids annotation
# ---------------------------------------------------------------------------


async def test_multi_query_or_union_with_matched_set_ids(client, auth_headers):
    tag_a = f"rb431a-{uuid.uuid4().hex[:8]}"
    tag_b = f"rb431b-{uuid.uuid4().hex[:8]}"
    dp_a = await _create_dp(client, auth_headers, f"RB431 OR A {uuid.uuid4()}", tags=[tag_a])
    dp_b = await _create_dp(client, auth_headers, f"RB431 OR B {uuid.uuid4()}", tags=[tag_b])
    dp_both = await _create_dp(client, auth_headers, f"RB431 OR Both {uuid.uuid4()}", tags=[tag_a, tag_b])

    await _write_value(client, auth_headers, dp_a["id"], 1.0)
    await _write_value(client, auth_headers, dp_b["id"], 2.0)
    await _write_value(client, auth_headers, dp_both["id"], 3.0)

    set_a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB431 set_a {uuid.uuid4()}", "filter": {"tags": [tag_a]}},
    )
    set_b = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB431 set_b {uuid.uuid4()}", "filter": {"tags": [tag_b]}},
    )
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets/query",
            json={"set_ids": [set_a["id"], set_b["id"]], "limit": 500},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        matched = {row["datapoint_id"]: row["matched_set_ids"] for row in rows}

        assert dp_a["id"] in matched
        assert dp_b["id"] in matched
        assert dp_both["id"] in matched
        # dp_a only matched by set_a, dp_b only by set_b, dp_both by both.
        assert matched[dp_a["id"]] == [set_a["id"]]
        assert matched[dp_b["id"]] == [set_b["id"]]
        assert sorted(matched[dp_both["id"]]) == sorted([set_a["id"], set_b["id"]])
    finally:
        await _delete_filterset(client, auth_headers, set_a["id"])
        await _delete_filterset(client, auth_headers, set_b["id"])


async def test_multi_query_empty_set_ids_returns_unfiltered_recent_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB431 empty {uuid.uuid4()}")
    await _write_value(client, auth_headers, dp["id"], 9.0)

    resp = await client.post(
        "/api/v1/ringbuffer/filtersets/query",
        json={"set_ids": [], "limit": 10},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows  # The recent write must be visible.
    assert all(row["matched_set_ids"] == [] for row in rows)


async def test_multi_query_unknown_set_id_is_skipped(client, auth_headers):
    """Unknown IDs are ignored rather than raising — see route docstring."""
    set_a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB431 known {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets/query",
            json={"set_ids": [set_a["id"], str(uuid.uuid4())], "limit": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _delete_filterset(client, auth_headers, set_a["id"])


async def test_multi_query_inactive_set_is_skipped(client, auth_headers):
    """An ``is_active=False`` set silently disappears from the union."""
    dp = await _create_dp(client, auth_headers, f"RB431 inactive {uuid.uuid4()}", tags=["rb431-inactive"])
    await _write_value(client, auth_headers, dp["id"], 8.0)

    created = await _create_filterset(
        client,
        auth_headers,
        {
            "name": f"RB431 inactive-set {uuid.uuid4()}",
            "is_active": False,
            "filter": {"tags": ["rb431-inactive"]},
        },
    )
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets/query",
            json={"set_ids": [created["id"]], "limit": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
    finally:
        await _delete_filterset(client, auth_headers, created["id"])


async def test_multi_query_time_filter_excludes_old_entries(client, auth_headers):
    """A time filter supplied alongside ``set_ids`` is AND-ed with the OR-union."""
    import asyncio
    from datetime import UTC, datetime

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    dp = await _create_dp(client, auth_headers, f"RB431 time {uuid.uuid4()}", tags=["rb431-time"])
    await _write_value(client, auth_headers, dp["id"], 1.0)
    await asyncio.sleep(0.05)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.05)
    await _write_value(client, auth_headers, dp["id"], 2.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB431 time-set {uuid.uuid4()}", "filter": {"tags": ["rb431-time"]}},
        )
    )["id"]
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets/query",
            json={"set_ids": [set_id], "time": {"from": iso(cutoff)}, "limit": 100},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows
        assert all(row["new_value"] == 2.0 for row in rows)
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_query_time_filter_with_empty_set_ids(client, auth_headers):
    """``set_ids=[]`` plus a ``time`` filter narrows the un-filtered feed."""
    import asyncio
    from datetime import UTC, datetime

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    dp = await _create_dp(client, auth_headers, f"RB431 time-empty {uuid.uuid4()}")
    await _write_value(client, auth_headers, dp["id"], 100.0)
    await asyncio.sleep(0.05)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.05)
    await _write_value(client, auth_headers, dp["id"], 200.0)

    resp = await client.post(
        "/api/v1/ringbuffer/filtersets/query",
        json={"set_ids": [], "time": {"from": iso(cutoff)}, "limit": 100},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = [row for row in resp.json() if row["datapoint_id"] == dp["id"]]
    assert rows
    assert all(row["new_value"] == 200.0 for row in rows)


# ---------------------------------------------------------------------------
# Single-set query (back-compat for /filtersets/{id}/query)
# ---------------------------------------------------------------------------


async def test_single_set_query_returns_matching_entries(client, auth_headers):
    tag = f"rb431single-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB431 single {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 7.0)

    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB431 single-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
    )
    try:
        resp = await client.post(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/query",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows
        assert all(row["datapoint_id"] == dp["id"] for row in rows)
    finally:
        await _delete_filterset(client, auth_headers, created["id"])


# ---------------------------------------------------------------------------
# Access control hardening (#590): filterset mutation routes are admin-only.
# ---------------------------------------------------------------------------


async def test_non_admin_cannot_create_filterset(client, auth_headers):
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Owned by user {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_non_admin_cannot_update_filterset(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"admin-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{created['id']}",
            json={"name": "hijacked", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _delete_filterset(client, auth_headers, created["id"])
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_non_admin_cannot_delete_filterset(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"admin-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.delete(
            f"/api/v1/ringbuffer/filtersets/{created['id']}",
            headers=user_headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _delete_filterset(client, auth_headers, created["id"])
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_non_admin_cannot_clone_filterset(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"admin-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.post(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/clone",
            json={"name": "forbidden clone"},
            headers=user_headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _delete_filterset(client, auth_headers, created["id"])
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_non_admin_can_patch_filterset_topbar(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"admin-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created['id']}/topbar",
            json={"topbar_active": True},
            headers=user_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["topbar_active"] is True
    finally:
        await _delete_filterset(client, auth_headers, created["id"])
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_non_admin_can_patch_filterset_order(client, auth_headers):
    created = await _create_filterset(
        client,
        auth_headers,
        {"name": f"admin-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    username = f"rb-na-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        resp = await client.patch(
            "/api/v1/ringbuffer/filtersets/order",
            json={"items": [{"id": created["id"], "topbar_order": 5}]},
            headers=user_headers,
        )
        assert resp.status_code == 200, resp.text
        by_id = {row["id"]: row for row in resp.json()}
        assert by_id[created["id"]]["topbar_order"] == 5
    finally:
        await _delete_filterset(client, auth_headers, created["id"])
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
