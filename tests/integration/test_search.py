"""
Integration Tests — Search API (Issue #182)

Covers enhanced search features:
  - Name substring match
  - UUID exact and partial match
  - Binding config substring match
  - Type filter
  - Tag filter
  - Quality filter (runtime)
  - Sort parameter
  - Pagination
  - Empty results
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = {
    "data_type": "FLOAT",
    "unit": "°C",
    "tags": [],
    "persist_value": False,
}


async def _create(client, auth_headers, name: str, **kwargs) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, **_BASE, **kwargs},
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"create failed: {resp.text}"
    return resp.json()


async def _delete(client, auth_headers, dp_id: str) -> None:
    await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


async def _write_value(client, auth_headers, dp_id: str, value) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, f"write failed: {resp.text}"


async def _insert_binding(dp_id: str, config: dict) -> None:
    """Insert a raw adapter binding row directly into the DB.

    Used to avoid the adapter-instance requirement of the binding API.
    Only the minimum required columns are set; nullable columns use defaults.
    """
    from obs.db.database import get_db
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            dp_id,
            "KNX",
            "SOURCE",
            json.dumps(config),
            1,
            now,
            now,
        ),
    )


async def _search(client, auth_headers, **params) -> dict:
    resp = await client.get("/api/v1/search/", params=params, headers=auth_headers)
    assert resp.status_code == 200, f"search failed: {resp.text}"
    return resp.json()


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


# ---------------------------------------------------------------------------
# Name substring
# ---------------------------------------------------------------------------

async def test_search_by_name_substring(client, auth_headers):
    name = f"SRH-Name-{uuid.uuid4().hex[:8]}"
    dp = await _create(client, auth_headers, name)
    try:
        body = await _search(client, auth_headers, q=name[:12])
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_by_name_multi_token(client, auth_headers):
    """'u04 temperatur' muss 'U04 Präsenzmelder 01 Temperatur' finden (nicht-adjazente Tokens)."""
    suffix = uuid.uuid4().hex[:6]
    dp = await _create(client, auth_headers, f"U04-{suffix} Präsenzmelder 01 Temperatur")
    try:
        body = await _search(client, auth_headers, q=f"u04-{suffix} temperatur")
        assert dp["id"] in _ids(body), "Multi-Token-Suche über nicht-adjazente Wörter muss funktionieren"
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_by_name_case_insensitive(client, auth_headers):
    suffix = uuid.uuid4().hex[:8]
    name = f"SRH-Case-{suffix}"
    dp = await _create(client, auth_headers, name)
    try:
        body = await _search(client, auth_headers, q=f"srh-case-{suffix}")
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


# ---------------------------------------------------------------------------
# UUID search
# ---------------------------------------------------------------------------

async def test_search_by_full_uuid(client, auth_headers):
    dp = await _create(client, auth_headers, f"SRH-UUID-full-{uuid.uuid4().hex[:6]}")
    try:
        body = await _search(client, auth_headers, q=dp["id"])
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_by_partial_uuid(client, auth_headers):
    dp = await _create(client, auth_headers, f"SRH-UUID-partial-{uuid.uuid4().hex[:6]}")
    try:
        # First 13 characters of the UUID (enough to be unique in a test run)
        partial = dp["id"][:13]
        body = await _search(client, auth_headers, q=partial)
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


# ---------------------------------------------------------------------------
# Binding config substring
# ---------------------------------------------------------------------------

async def test_search_by_binding_config_substring(client, auth_headers):
    unique_ga = f"9/{uuid.uuid4().int % 100}/{uuid.uuid4().int % 256}"
    dp = await _create(client, auth_headers, f"SRH-Binding-{uuid.uuid4().hex[:6]}")
    try:
        await _insert_binding(dp["id"], {"group_address": unique_ga})
        body = await _search(client, auth_headers, q=unique_ga)
        assert dp["id"] in _ids(body), f"Expected {dp['id']} in results for q={unique_ga!r}"
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_binding_config_does_not_match_other_dp(client, auth_headers):
    unique_ga = f"7/{uuid.uuid4().int % 100}/{uuid.uuid4().int % 256}"
    dp_with    = await _create(client, auth_headers, f"SRH-Bind-With-{uuid.uuid4().hex[:6]}")
    dp_without = await _create(client, auth_headers, f"SRH-Bind-None-{uuid.uuid4().hex[:6]}")
    try:
        await _insert_binding(dp_with["id"], {"group_address": unique_ga})
        body = await _search(client, auth_headers, q=unique_ga)
        assert dp_with["id"] in _ids(body)
        assert dp_without["id"] not in _ids(body)
    finally:
        await _delete(client, auth_headers, dp_with["id"])
        await _delete(client, auth_headers, dp_without["id"])


# ---------------------------------------------------------------------------
# Type filter
# ---------------------------------------------------------------------------

async def test_search_type_filter(client, auth_headers):
    suffix = uuid.uuid4().hex[:6]
    dp_float = await _create(client, auth_headers, f"SRH-Type-F-{suffix}", data_type="FLOAT")
    dp_bool  = await _create(client, auth_headers, f"SRH-Type-B-{suffix}", data_type="BOOLEAN")
    try:
        body = await _search(client, auth_headers, type="BOOLEAN")
        assert dp_bool["id"] in _ids(body)
        assert dp_float["id"] not in _ids(body)
    finally:
        await _delete(client, auth_headers, dp_float["id"])
        await _delete(client, auth_headers, dp_bool["id"])


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------

async def test_search_tag_filter(client, auth_headers):
    unique_tag = f"zone-{uuid.uuid4().hex[:8]}"
    dp_tagged  = await _create(client, auth_headers, f"SRH-Tag-Y-{uuid.uuid4().hex[:6]}", tags=[unique_tag])
    dp_untagged = await _create(client, auth_headers, f"SRH-Tag-N-{uuid.uuid4().hex[:6]}", tags=[])
    try:
        body = await _search(client, auth_headers, tag=unique_tag)
        assert dp_tagged["id"] in _ids(body)
        assert dp_untagged["id"] not in _ids(body)
    finally:
        await _delete(client, auth_headers, dp_tagged["id"])
        await _delete(client, auth_headers, dp_untagged["id"])


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------

async def test_search_quality_good(client, auth_headers):
    """A DP that received a value has quality=good; must appear in quality=good search."""
    dp = await _create(client, auth_headers, f"SRH-Qual-Good-{uuid.uuid4().hex[:6]}")
    try:
        await _write_value(client, auth_headers, dp["id"], 42.0)
        body = await _search(client, auth_headers, quality="good")
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_quality_good_excludes_uncertain(client, auth_headers):
    """A DP with no value written is uncertain; must NOT appear in quality=good search."""
    dp = await _create(client, auth_headers, f"SRH-Qual-Unc-{uuid.uuid4().hex[:6]}")
    try:
        # Do NOT write a value — quality stays uncertain
        body = await _search(client, auth_headers, quality="good")
        assert dp["id"] not in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


async def test_search_quality_uncertain(client, auth_headers):
    """A DP with no value written must appear in quality=uncertain search."""
    dp = await _create(client, auth_headers, f"SRH-Qual-Unc2-{uuid.uuid4().hex[:6]}")
    try:
        body = await _search(client, auth_headers, quality="uncertain")
        assert dp["id"] in _ids(body)
    finally:
        await _delete(client, auth_headers, dp["id"])


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------

async def test_search_sort_name_asc(client, auth_headers):
    suffix = uuid.uuid4().hex[:6]
    dp_a = await _create(client, auth_headers, f"AAA-SRH-Sort-{suffix}")
    dp_z = await _create(client, auth_headers, f"ZZZ-SRH-Sort-{suffix}")
    try:
        body = await _search(client, auth_headers, q=f"srh-sort-{suffix}", sort="name", order="asc")
        names = [item["name"] for item in body["items"]]
        assert names == sorted(names, key=str.lower)
    finally:
        await _delete(client, auth_headers, dp_a["id"])
        await _delete(client, auth_headers, dp_z["id"])


async def test_search_sort_name_desc(client, auth_headers):
    suffix = uuid.uuid4().hex[:6]
    dp_a = await _create(client, auth_headers, f"AAA-SRH-SortD-{suffix}")
    dp_z = await _create(client, auth_headers, f"ZZZ-SRH-SortD-{suffix}")
    try:
        body = await _search(client, auth_headers, q=f"srh-sortd-{suffix}", sort="name", order="desc")
        names = [item["name"] for item in body["items"]]
        assert names == sorted(names, key=str.lower, reverse=True)
    finally:
        await _delete(client, auth_headers, dp_a["id"])
        await _delete(client, auth_headers, dp_z["id"])


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

async def test_search_pagination(client, auth_headers):
    prefix = f"SRH-Pag-{uuid.uuid4().hex[:6]}"
    created = []
    for i in range(5):
        dp = await _create(client, auth_headers, f"{prefix}-{i:02d}")
        created.append(dp["id"])
    try:
        body = await _search(client, auth_headers, q=prefix, size=2, page=0)
        assert len(body["items"]) == 2
        assert body["total"] >= 5
        assert body["pages"] >= 3
        assert body["size"] == 2

        body2 = await _search(client, auth_headers, q=prefix, size=2, page=1)
        ids_p0 = _ids(body)
        ids_p1 = _ids(body2)
        assert ids_p0.isdisjoint(ids_p1), "Pages must not overlap"
    finally:
        for dp_id in created:
            await _delete(client, auth_headers, dp_id)


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------

async def test_search_no_results(client, auth_headers):
    body = await _search(client, auth_headers, q="ZZZZ-NORESULT-XYZXYZ-99999")
    assert body["total"] == 0
    assert body["items"] == []
    assert body["pages"] == 1
