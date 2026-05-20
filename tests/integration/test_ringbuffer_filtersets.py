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
# Access control (#478) — fine-grained ownership: admin can do everything,
# non-admin users may only edit/delete sets they themselves created.
# ---------------------------------------------------------------------------


async def test_create_filterset_sets_created_by_to_current_user(client, auth_headers):
    """POST /filtersets stamps created_by with the calling user's username."""
    username = f"rb-owner-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    created_id = None
    try:
        resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Owned by user {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        created_id = body["id"]
        assert body["created_by"] == username
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_admin_can_mutate_any_filterset(client, auth_headers):
    """Admin can rename and delete a set created by another user."""
    username = f"rb-other-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    created_id = None
    try:
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Owned by {username}", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        created_id = create_resp.json()["id"]

        # Admin renames the user's set.
        put_resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            json={"name": "renamed by admin", "filter": {"adapters": ["api"]}},
            headers=auth_headers,
        )
        assert put_resp.status_code == 200, put_resp.text

        # Admin deletes it.
        del_resp = await client.delete(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            headers=auth_headers,
        )
        assert del_resp.status_code == 204, del_resp.text
        created_id = None
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_user_can_mutate_own_filterset(client, auth_headers):
    """Non-admin users may rename and delete sets they themselves created."""
    username = f"rb-self-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    created_id = None
    try:
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Self-owned {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        created_id = create_resp.json()["id"]

        put_resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            json={"name": "renamed by owner", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert put_resp.status_code == 200, put_resp.text

        del_resp = await client.delete(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            headers=user_headers,
        )
        assert del_resp.status_code == 204, del_resp.text
        created_id = None
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_user_cannot_update_others_filterset(client, auth_headers):
    """A non-admin trying to PUT another user's filterset must get 403."""
    owner = f"rb-owner-{uuid.uuid4().hex[:8]}"
    intruder = f"rb-intruder-{uuid.uuid4().hex[:8]}"
    owner_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=owner, password="pw-12345678")
    intruder_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=intruder, password="pw-12345678")
    created_id = None
    try:
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Owner's set {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=owner_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        created_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            json={"name": "hijacked", "filter": {"adapters": ["api"]}},
            headers=intruder_headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{owner}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{intruder}", headers=auth_headers)


async def test_user_cannot_delete_others_filterset(client, auth_headers):
    """A non-admin trying to DELETE another user's filterset must get 403."""
    owner = f"rb-owner-{uuid.uuid4().hex[:8]}"
    intruder = f"rb-intruder-{uuid.uuid4().hex[:8]}"
    owner_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=owner, password="pw-12345678")
    intruder_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=intruder, password="pw-12345678")
    created_id = None
    try:
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Owner's set {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=owner_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        created_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/ringbuffer/filtersets/{created_id}",
            headers=intruder_headers,
        )
        assert resp.status_code == 403, resp.text

        # Owner still sees their set.
        check = await client.get(f"/api/v1/ringbuffer/filtersets/{created_id}", headers=owner_headers)
        assert check.status_code == 200, check.text
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{owner}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{intruder}", headers=auth_headers)


async def test_user_can_clone_others_filterset_and_owns_clone(client, auth_headers):
    """Cloning stays open for everyone; the clone's created_by is the cloning user."""
    owner = f"rb-owner-{uuid.uuid4().hex[:8]}"
    cloner = f"rb-cloner-{uuid.uuid4().hex[:8]}"
    owner_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=owner, password="pw-12345678")
    cloner_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=cloner, password="pw-12345678")
    source_id = None
    clone_id = None
    try:
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"Original by {owner}", "filter": {"adapters": ["api"]}},
            headers=owner_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        source_id = create_resp.json()["id"]

        clone_resp = await client.post(
            f"/api/v1/ringbuffer/filtersets/{source_id}/clone",
            json={"name": f"Clone by {cloner}"},
            headers=cloner_headers,
        )
        assert clone_resp.status_code == 201, clone_resp.text
        clone = clone_resp.json()
        clone_id = clone["id"]
        assert clone["created_by"] == cloner

        # The cloner can now edit their clone …
        edit_resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{clone_id}",
            json={"name": "renamed clone", "filter": {"adapters": ["api"]}},
            headers=cloner_headers,
        )
        assert edit_resp.status_code == 200, edit_resp.text

        # … but they still cannot edit the source.
        forbid = await client.put(
            f"/api/v1/ringbuffer/filtersets/{source_id}",
            json={"name": "hijack source", "filter": {"adapters": ["api"]}},
            headers=cloner_headers,
        )
        assert forbid.status_code == 403, forbid.text
    finally:
        if clone_id:
            await _delete_filterset(client, auth_headers, clone_id)
        if source_id:
            await _delete_filterset(client, auth_headers, source_id)
        await client.delete(f"/api/v1/auth/users/{owner}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{cloner}", headers=auth_headers)


async def test_legacy_set_without_owner_is_admin_only(client, auth_headers):
    """Migration leaves existing rows with created_by=NULL.

    Such "shared" sets must remain visible to everyone (read-only) but only
    admins may mutate them; non-admins get 403 on PUT/DELETE.
    """
    from obs.db.database import get_db

    db = get_db()
    legacy_id = str(uuid.uuid4())
    now = "2025-01-01T00:00:00Z"
    await db.execute_and_commit(
        """INSERT INTO ringbuffer_filtersets
           (id, name, description, dsl_version, is_active, color,
            topbar_active, topbar_order, filter_json, created_at, updated_at, created_by)
           VALUES (?, 'Legacy set', '', 2, 1, '#3b82f6', 0, 0,
                   '{"adapters":["api"]}', ?, ?, NULL)""",
        (legacy_id, now, now),
    )
    username = f"rb-noowner-{uuid.uuid4().hex[:8]}"
    user_headers = await _create_non_admin_user_and_headers(client, auth_headers, username=username, password="pw-12345678")
    try:
        # User can read it.
        get_resp = await client.get(f"/api/v1/ringbuffer/filtersets/{legacy_id}", headers=user_headers)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["created_by"] is None

        # User cannot edit it.
        put_resp = await client.put(
            f"/api/v1/ringbuffer/filtersets/{legacy_id}",
            json={"name": "user-renamed", "filter": {"adapters": ["api"]}},
            headers=user_headers,
        )
        assert put_resp.status_code == 403, put_resp.text

        # Admin can edit it.
        admin_put = await client.put(
            f"/api/v1/ringbuffer/filtersets/{legacy_id}",
            json={"name": "admin-renamed", "filter": {"adapters": ["api"]}},
            headers=auth_headers,
        )
        assert admin_put.status_code == 200, admin_put.text
    finally:
        await db.execute_and_commit("DELETE FROM ringbuffer_filtersets WHERE id=?", (legacy_id,))
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Per-user topbar state (#478)
# ---------------------------------------------------------------------------


async def test_topbar_state_is_per_user(client, auth_headers):
    """user A pins a set; user B must NOT see it as pinned."""
    user_a = f"rb-a-{uuid.uuid4().hex[:8]}"
    user_b = f"rb-b-{uuid.uuid4().hex[:8]}"
    headers_a = await _create_non_admin_user_and_headers(client, auth_headers, username=user_a, password="pw-12345678")
    headers_b = await _create_non_admin_user_and_headers(client, auth_headers, username=user_b, password="pw-12345678")
    created_id = None
    try:
        # User A creates a set (and pins it on creation).
        create_resp = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"per-user topbar {uuid.uuid4()}", "filter": {"adapters": ["api"]}, "topbar_active": True},
            headers=headers_a,
        )
        assert create_resp.status_code == 201, create_resp.text
        created_id = create_resp.json()["id"]
        # A sees it pinned.
        assert create_resp.json()["topbar_active"] is True

        # User B reads the same set: must see it un-pinned.
        get_b = await client.get(f"/api/v1/ringbuffer/filtersets/{created_id}", headers=headers_b)
        assert get_b.status_code == 200, get_b.text
        assert get_b.json()["topbar_active"] is False

        # User B pins it for himself.
        patch_b = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created_id}/topbar",
            json={"topbar_active": True},
            headers=headers_b,
        )
        assert patch_b.status_code == 200, patch_b.text
        assert patch_b.json()["topbar_active"] is True

        # User B un-pins it again — must not flip A's view.
        patch_b_off = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created_id}/topbar",
            json={"topbar_active": False},
            headers=headers_b,
        )
        assert patch_b_off.status_code == 200, patch_b_off.text

        get_a = await client.get(f"/api/v1/ringbuffer/filtersets/{created_id}", headers=headers_a)
        assert get_a.status_code == 200, get_a.text
        assert get_a.json()["topbar_active"] is True
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{user_a}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{user_b}", headers=auth_headers)


async def test_is_active_is_per_user_and_open_to_everyone(client, auth_headers):
    """``is_active`` is a per-user override too: every authenticated user may
    toggle their own active state on any set without Owner / Admin restriction
    (#478 — corrected). User A deactivating a set must not affect user B's view.
    """
    owner = f"rb-act-owner-{uuid.uuid4().hex[:8]}"
    other = f"rb-act-other-{uuid.uuid4().hex[:8]}"
    headers_owner = await _create_non_admin_user_and_headers(client, auth_headers, username=owner, password="pw-12345678")
    headers_other = await _create_non_admin_user_and_headers(client, auth_headers, username=other, password="pw-12345678")
    created_id = None
    try:
        # Owner creates the set, active by default.
        create = await client.post(
            "/api/v1/ringbuffer/filtersets",
            json={"name": f"per-user is_active {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
            headers=headers_owner,
        )
        assert create.status_code == 201, create.text
        created_id = create.json()["id"]
        assert create.json()["is_active"] is True

        # User "other" deactivates it for themselves — must succeed (no 403).
        patch = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created_id}/topbar",
            json={"is_active": False},
            headers=headers_other,
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["is_active"] is False

        # Owner's view is unaffected.
        get_owner = await client.get(f"/api/v1/ringbuffer/filtersets/{created_id}", headers=headers_owner)
        assert get_owner.status_code == 200, get_owner.text
        assert get_owner.json()["is_active"] is True

        # And the owner can flip their own is_active too.
        patch_owner = await client.patch(
            f"/api/v1/ringbuffer/filtersets/{created_id}/topbar",
            json={"is_active": False},
            headers=headers_owner,
        )
        assert patch_owner.status_code == 200, patch_owner.text
        assert patch_owner.json()["is_active"] is False
    finally:
        if created_id:
            await _delete_filterset(client, auth_headers, created_id)
        await client.delete(f"/api/v1/auth/users/{owner}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{other}", headers=auth_headers)


async def test_topbar_order_is_per_user(client, auth_headers):
    """PATCH /filtersets/order writes per-user ordering, not the global default."""
    user_a = f"rb-ord-a-{uuid.uuid4().hex[:8]}"
    user_b = f"rb-ord-b-{uuid.uuid4().hex[:8]}"
    headers_a = await _create_non_admin_user_and_headers(client, auth_headers, username=user_a, password="pw-12345678")
    headers_b = await _create_non_admin_user_and_headers(client, auth_headers, username=user_b, password="pw-12345678")
    a_id = None
    b_id = None
    try:
        a = (
            await client.post(
                "/api/v1/ringbuffer/filtersets",
                json={"name": f"Order A {uuid.uuid4()}", "filter": {"tags": ["rb-order-per-user"]}},
                headers=headers_a,
            )
        ).json()
        a_id = a["id"]
        b = (
            await client.post(
                "/api/v1/ringbuffer/filtersets",
                json={"name": f"Order B {uuid.uuid4()}", "filter": {"tags": ["rb-order-per-user"]}},
                headers=headers_a,
            )
        ).json()
        b_id = b["id"]

        # User A orders A=5, B=2 …
        resp = await client.patch(
            "/api/v1/ringbuffer/filtersets/order",
            json={"items": [{"id": a_id, "topbar_order": 5}, {"id": b_id, "topbar_order": 2}]},
            headers=headers_a,
        )
        assert resp.status_code == 200, resp.text
        by_id_a = {row["id"]: row for row in resp.json()}
        assert by_id_a[a_id]["topbar_order"] == 5
        assert by_id_a[b_id]["topbar_order"] == 2

        # User B sees the defaults (0/0) — A's order must not leak.
        list_b = await client.get("/api/v1/ringbuffer/filtersets", headers=headers_b)
        assert list_b.status_code == 200, list_b.text
        by_id_b = {row["id"]: row for row in list_b.json() if row["id"] in (a_id, b_id)}
        assert by_id_b[a_id]["topbar_order"] == 0
        assert by_id_b[b_id]["topbar_order"] == 0
    finally:
        if a_id:
            await _delete_filterset(client, auth_headers, a_id)
        if b_id:
            await _delete_filterset(client, auth_headers, b_id)
        await client.delete(f"/api/v1/auth/users/{user_a}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/users/{user_b}", headers=auth_headers)
