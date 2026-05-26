"""Integration tests for the multi-set CSV/TSV export endpoint (#427).

The endpoint ``POST /api/v1/ringbuffer/filtersets/export/csv`` exports the
OR-union of the rows that match any of the supplied filtersets (plus an
optional time filter), in CSV or TSV format. Optional columns ``unit`` and
``matched_set_ids`` can be toggled. Encoding ``utf8-bom`` prepends a BOM.

The set semantics (OR-union, unknown/inactive set skipping, empty set_ids ⇒
unfiltered recent entries) mirror ``POST /filtersets/query`` from #431.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _cleanup_rb427_dps(client, auth_headers):
    """Delete any DataPoints this module's tests left behind.

    Without this, the 14+ test DPs accumulate in the DB and push other tests'
    DPs off the first search page — flaking `test_search_quality_good` etc.
    """
    yield
    try:
        resp = await client.get(
            "/api/v1/search/",
            params={"q": "RB427", "size": 500},
            headers=auth_headers,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                await client.delete(f"/api/v1/datapoints/{item['id']}", headers=auth_headers)
    except Exception:  # noqa: BLE001
        pass


_DP_BASE = {
    "name": "Ringbuffer Multi Export DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-multi-export-test"],
    "persist_value": False,
}


async def _create_dp(
    client,
    auth_headers,
    name: str,
    *,
    tags: list[str] | None = None,
    unit: str | None = "W",
) -> dict:
    payload = {**_DP_BASE, "name": name, "unit": unit}
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


async def _post_multi_export(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/filtersets/export/csv",
        json=body,
        headers=auth_headers,
    )


def _parse_rows(text: str, *, delimiter: str = ",") -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


# ---------------------------------------------------------------------------
# Core OR-union behaviour
# ---------------------------------------------------------------------------


async def test_multi_export_or_union_contains_rows_from_both_sets(client, auth_headers):
    tag_a = f"rb427a-{uuid.uuid4().hex[:8]}"
    tag_b = f"rb427b-{uuid.uuid4().hex[:8]}"
    dp_a = await _create_dp(client, auth_headers, f"RB427 OR A {uuid.uuid4()}", tags=[tag_a])
    dp_b = await _create_dp(client, auth_headers, f"RB427 OR B {uuid.uuid4()}", tags=[tag_b])
    dp_both = await _create_dp(client, auth_headers, f"RB427 OR Both {uuid.uuid4()}", tags=[tag_a, tag_b])

    await _write_value(client, auth_headers, dp_a["id"], 1.0)
    await _write_value(client, auth_headers, dp_b["id"], 2.0)
    await _write_value(client, auth_headers, dp_both["id"], 3.0)

    set_a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB427 set_a {uuid.uuid4()}", "filter": {"tags": [tag_a]}},
    )
    set_b = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB427 set_b {uuid.uuid4()}", "filter": {"tags": [tag_b]}},
    )
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_a["id"], set_b["id"]]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        # Filename ends with .csv
        assert ".csv" in resp.headers.get("content-disposition", "")

        rows = _parse_rows(resp.text)
        # Each dp should appear exactly once even if it matches both sets.
        dp_ids_in_export = {row["datapoint_id"] for row in rows}
        assert dp_a["id"] in dp_ids_in_export
        assert dp_b["id"] in dp_ids_in_export
        assert dp_both["id"] in dp_ids_in_export
        # No duplicate entry rows for dp_both.
        dp_both_rows = [row for row in rows if row["datapoint_id"] == dp_both["id"]]
        assert len(dp_both_rows) == 1
    finally:
        await _delete_filterset(client, auth_headers, set_a["id"])
        await _delete_filterset(client, auth_headers, set_b["id"])


async def test_multi_export_empty_set_ids_exports_unfiltered_recent_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, f"RB427 empty {uuid.uuid4()}")
    await _write_value(client, auth_headers, dp["id"], 11.0)

    resp = await _post_multi_export(client, auth_headers, {"set_ids": []})
    assert resp.status_code == 200, resp.text
    rows = _parse_rows(resp.text)
    assert rows  # recent write must be visible
    assert any(row["datapoint_id"] == dp["id"] for row in rows)


async def test_multi_export_unknown_set_id_is_silently_skipped(client, auth_headers):
    set_a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB427 known {uuid.uuid4()}", "filter": {"adapters": ["api"]}},
    )
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_a["id"], str(uuid.uuid4())]},
        )
        # Unknown IDs are skipped, not 404.
        assert resp.status_code == 200, resp.text
    finally:
        await _delete_filterset(client, auth_headers, set_a["id"])


# ---------------------------------------------------------------------------
# Format / encoding / filename
# ---------------------------------------------------------------------------


async def test_multi_export_tab_delimiter_uses_tsv_extension(client, auth_headers):
    tag = f"rb427tsv-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 TSV {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 4.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 tsv-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_id], "delimiter": "\t"},
        )
        assert resp.status_code == 200, resp.text
        # Content-Disposition ends with .tsv
        assert ".tsv" in resp.headers.get("content-disposition", "")
        # First line uses tab separators between columns.
        lines = resp.text.splitlines()
        assert lines, "tsv body should contain at least the header line"
        assert "\t" in lines[0]
        # CSV separator (,) must not appear inside the header row of a TSV.
        # (datapoint_id, ts, name, etc.) are all single tokens without commas.
        assert "," not in lines[0]
        # Round-trip parse with tab delimiter.
        rows = _parse_rows(resp.text, delimiter="\t")
        assert any(row["datapoint_id"] == dp["id"] for row in rows)
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_export_utf8_bom_prefixes_response(client, auth_headers):
    tag = f"rb427bom-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 BOM {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 5.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 bom-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_id], "encoding": "utf8-bom"},
        )
        assert resp.status_code == 200, resp.text
        # The response bytes start with the UTF-8 BOM (EF BB BF).
        assert resp.content.startswith(b"\xef\xbb\xbf"), resp.content[:8]
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_export_default_encoding_has_no_bom(client, auth_headers):
    tag = f"rb427nobom-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 NoBOM {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 6.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 nobom-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 200, resp.text
        assert not resp.content.startswith(b"\xef\xbb\xbf"), resp.content[:8]
    finally:
        await _delete_filterset(client, auth_headers, set_id)


# ---------------------------------------------------------------------------
# Optional columns
# ---------------------------------------------------------------------------


async def test_multi_export_unit_column_default_includes_unit(client, auth_headers):
    tag = f"rb427unit-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 Unit {uuid.uuid4()}", tags=[tag], unit="kWh")
    await _write_value(client, auth_headers, dp["id"], 7.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 unit-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        row = next((r for r in rows if r["datapoint_id"] == dp["id"]), None)
        assert row is not None
        assert "unit" in row
        assert row["unit"] == "kWh"
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_export_unit_column_can_be_disabled(client, auth_headers):
    tag = f"rb427nounit-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 NoUnit {uuid.uuid4()}", tags=[tag], unit="kWh")
    await _write_value(client, auth_headers, dp["id"], 8.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 nounit-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_id], "include_unit": False},
        )
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        row = next((r for r in rows if r["datapoint_id"] == dp["id"]), None)
        assert row is not None
        assert "unit" not in row
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_export_matched_set_ids_column_off_by_default(client, auth_headers):
    tag = f"rb427msd-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 MSI default {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 9.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 msi-default {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        row = next((r for r in rows if r["datapoint_id"] == dp["id"]), None)
        assert row is not None
        assert "matched_set_ids" not in row
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_multi_export_matched_set_ids_column_lists_matching_sets(client, auth_headers):
    tag_a = f"rb427msa-{uuid.uuid4().hex[:8]}"
    tag_b = f"rb427msb-{uuid.uuid4().hex[:8]}"
    dp_both = await _create_dp(client, auth_headers, f"RB427 MSI both {uuid.uuid4()}", tags=[tag_a, tag_b])
    await _write_value(client, auth_headers, dp_both["id"], 10.0)

    set_a = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB427 msi-A {uuid.uuid4()}", "filter": {"tags": [tag_a]}},
    )
    set_b = await _create_filterset(
        client,
        auth_headers,
        {"name": f"RB427 msi-B {uuid.uuid4()}", "filter": {"tags": [tag_b]}},
    )
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {
                "set_ids": [set_a["id"], set_b["id"]],
                "include_matched_set_ids": True,
            },
        )
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        row = next((r for r in rows if r["datapoint_id"] == dp_both["id"]), None)
        assert row is not None
        assert "matched_set_ids" in row
        # Comma-separated list of set IDs (verbatim, order-independent).
        ids_in_cell = {part.strip() for part in row["matched_set_ids"].split(",") if part.strip()}
        assert ids_in_cell == {set_a["id"], set_b["id"]}
    finally:
        await _delete_filterset(client, auth_headers, set_a["id"])
        await _delete_filterset(client, auth_headers, set_b["id"])


# ---------------------------------------------------------------------------
# Time filter passthrough
# ---------------------------------------------------------------------------


async def test_multi_export_respects_time_filter(client, auth_headers):
    import asyncio
    from datetime import UTC, datetime

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    tag = f"rb427time-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 time {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 1.0)
    await asyncio.sleep(0.05)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.05)
    await _write_value(client, auth_headers, dp["id"], 2.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 time-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_id], "time": {"from": iso(cutoff)}},
        )
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        # Only the post-cutoff row (2.0) survives.
        import json as _json

        new_values = [_json.loads(row["new_value_json"]) for row in rows if row["datapoint_id"] == dp["id"]]
        assert new_values == [2.0]
    finally:
        await _delete_filterset(client, auth_headers, set_id)


# ---------------------------------------------------------------------------
# Quoting / escaping for CSV special characters
# ---------------------------------------------------------------------------


async def test_multi_export_quotes_csv_special_characters(client, auth_headers):
    tag = f"rb427q-{uuid.uuid4().hex[:8]}"
    # Comma + quote + newline are the three CSV special characters.
    dp = await _create_dp(
        client,
        auth_headers,
        f'RB427 "comma, and \nnewline" {uuid.uuid4()}',
        tags=[tag],
    )
    await _write_value(client, auth_headers, dp["id"], 13.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 q-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 200, resp.text
        rows = _parse_rows(resp.text)
        row = next((r for r in rows if r["datapoint_id"] == dp["id"]), None)
        assert row is not None
        # Name made it through round-trip intact, including comma + quote + newline.
        assert "comma, and" in row["name"]
        assert "newline" in row["name"]
    finally:
        await _delete_filterset(client, auth_headers, set_id)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_multi_export_legacy_format_key_returns_422(client, auth_headers):
    # The `format` field was removed in favour of explicit delimiter/quote_char/
    # escape_char. The request model has extra="forbid" so unknown fields fail
    # validation rather than silently dropping the user's choice.
    resp = await _post_multi_export(
        client,
        auth_headers,
        {"set_ids": [], "format": "csv"},
    )
    assert resp.status_code == 422, resp.text


async def test_multi_export_multi_char_delimiter_returns_422(client, auth_headers):
    resp = await _post_multi_export(
        client,
        auth_headers,
        {"set_ids": [], "delimiter": "||"},
    )
    assert resp.status_code == 422, resp.text


async def test_multi_export_empty_delimiter_returns_422(client, auth_headers):
    resp = await _post_multi_export(
        client,
        auth_headers,
        {"set_ids": [], "delimiter": ""},
    )
    assert resp.status_code == 422, resp.text


async def test_multi_export_invalid_encoding_returns_422(client, auth_headers):
    resp = await _post_multi_export(
        client,
        auth_headers,
        {"set_ids": [], "encoding": "latin1"},
    )
    assert resp.status_code == 422, resp.text


async def test_multi_export_custom_delimiter_and_quote(client, auth_headers):
    tag = f"rb427sep-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RB427 sep {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 4.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RB427 sep-set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]
    try:
        resp = await _post_multi_export(
            client,
            auth_headers,
            {"set_ids": [set_id], "delimiter": ";", "quote_char": "'"},
        )
        assert resp.status_code == 200, resp.text
        # Header line uses ; as separator.
        header = resp.text.splitlines()[0]
        assert ";" in header
        rows = _parse_rows(resp.text, delimiter=";")
        assert any(row["datapoint_id"] == dp["id"] for row in rows)
    finally:
        await _delete_filterset(client, auth_headers, set_id)
