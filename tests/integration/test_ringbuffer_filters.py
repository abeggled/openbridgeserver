"""Integration baseline tests for /api/v1/ringbuffer filter parameters."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "Ringbuffer Filter Test DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-filter-test"],
    "persist_value": False,
}


async def _create_dp(client, auth_headers, name: str, data_type: str = "FLOAT", unit: str | None = "W") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={**_DP_BASE, "name": name, "data_type": data_type, "unit": unit},
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


async def _query_ringbuffer(client, auth_headers, params: dict) -> list[dict]:
    resp = await client.get(
        "/api/v1/ringbuffer/",
        params=params,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _query_ringbuffer_v2(client, auth_headers, body: dict) -> list[dict]:
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _export_ringbuffer_csv(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/export/csv",
        json=body,
        headers=auth_headers,
    )


def _parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


async def _insert_binding(dp_id: str, adapter_type: str, config: dict) -> None:
    from obs.db.database import get_db

    db = get_db()
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            dp_id,
            adapter_type,
            "SOURCE",
            json.dumps(config),
            1,
            now,
            now,
        ),
    )


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
    assert "storage must be 'file'" in resp.text


async def test_ringbuffer_config_rejects_memory_storage_mode(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"storage": "memory", "max_entries": 100},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "storage must be 'file'" in resp.text


async def test_ringbuffer_config_accepts_retention_fields(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 100,
            "max_file_size_bytes": 4096,
            "max_age": 60,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["max_entries"] == 100
    assert body["max_file_size_bytes"] == 4096
    assert body["max_age"] == 60

    # Keep session-scoped integration app stable for subsequent tests.
    reset_resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert reset_resp.status_code == 200, reset_resp.text


async def test_ringbuffer_config_accepts_null_max_entries(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": None,
            "max_file_size_bytes": 4096,
            "max_age": 60,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["max_entries"] is None
    assert body["max_file_size_bytes"] == 4096
    assert body["max_age"] == 60

    # Keep session-scoped integration app stable for subsequent tests.
    reset_resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert reset_resp.status_code == 200, reset_resp.text


async def test_ringbuffer_v2_adapter_or_is_combined_with_group_and(client, auth_headers):
    dp_a = await _create_dp(client, auth_headers, "RB DSL A")
    dp_b = await _create_dp(client, auth_headers, "RB DSL B")

    await _write_value(client, auth_headers, dp_a["id"], 1.0)
    await _write_value(client, auth_headers, dp_b["id"], 2.0)

    rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "adapters": {"any_of": ["api", "knx"]},
                "datapoints": {"ids": [dp_a["id"]]},
            },
            "sort": {"field": "id", "order": "desc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )

    assert rows
    assert all(row["datapoint_id"] == dp_a["id"] for row in rows)
    assert all(row["source_adapter"] in {"api", "knx"} for row in rows)


async def test_ringbuffer_v2_time_filter_supports_open_boundaries(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB DSL Open Bounds")

    await _write_value(client, auth_headers, dp["id"], 10.0)
    first = (await _query_ringbuffer(client, auth_headers, {"q": dp["id"], "limit": 1}))[0]
    await asyncio.sleep(0.02)
    await _write_value(client, auth_headers, dp["id"], 11.0)
    second = (await _query_ringbuffer(client, auth_headers, {"q": dp["id"], "limit": 1}))[0]

    only_from = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp["id"]]},
                "time": {"from": first["ts"]},
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert only_from
    assert all(row["ts"] > first["ts"] for row in only_from)

    only_to = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp["id"]]},
                "time": {"to": second["ts"]},
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert only_to
    assert all(row["ts"] < second["ts"] for row in only_to)


async def test_ringbuffer_v2_combines_absolute_and_relative_time_bounds(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB DSL Abs+Rel")

    now = datetime.now(UTC)
    old_ts = (now - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    future_ts = (now + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    await _write_value(client, auth_headers, dp["id"], 100.0)
    await asyncio.sleep(0.02)
    await _write_value(client, auth_headers, dp["id"], 101.0)

    # from=max(absolute, relative): absolute old + relative recent => rows exist.
    rows_with_old_abs = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp["id"]]},
                "time": {
                    "from": old_ts,
                    "from_relative_seconds": -30,
                },
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert rows_with_old_abs

    # from=max(absolute, relative): absolute future + relative recent => effective lower bound is future.
    rows_with_future_abs = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp["id"]]},
                "time": {
                    "from": future_ts,
                    "from_relative_seconds": -30,
                },
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert rows_with_future_abs == []


async def test_ringbuffer_v2_rejects_empty_adapter_filter_list(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": {
                "adapters": {"any_of": ["  ", ""]},
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "filters.adapters.any_of must contain at least one adapter" in resp.text


async def test_ringbuffer_v2_rejects_empty_datapoint_filter_list(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": {
                "datapoints": {"ids": ["", "  "]},
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "filters.datapoints.ids must contain at least one datapoint id" in resp.text


async def test_ringbuffer_v2_q_matches_datapoint_name(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB DSL Name Match")
    await _write_value(client, auth_headers, dp["id"], 12.5)

    rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {"q": "name match"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert rows
    assert any(row["datapoint_id"] == dp["id"] for row in rows)


async def test_ringbuffer_v2_invalid_timestamp_returns_422(client, auth_headers):
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": {
                "time": {"from": "not-a-ts"},
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "invalid timestamp: not-a-ts" in resp.text


async def test_ringbuffer_stats_endpoint_returns_current_config(client, auth_headers):
    resp = await client.get("/api/v1/ringbuffer/stats", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["storage"] == "file"
    assert "total" in body
    assert "max_entries" in body
    assert "effective_retention_seconds" in body
    assert body["effective_retention_seconds"] is None or (
        isinstance(body["effective_retention_seconds"], int) and body["effective_retention_seconds"] >= 0
    )


async def test_ringbuffer_v2_value_filters_numeric_string_boolean_and_regex(client, auth_headers):
    dp_float = await _create_dp(client, auth_headers, "RB DSL Value Float", data_type="FLOAT", unit="W")
    dp_string = await _create_dp(client, auth_headers, "RB DSL Value String", data_type="STRING", unit=None)
    dp_bool = await _create_dp(client, auth_headers, "RB DSL Value Bool", data_type="BOOLEAN", unit=None)

    await _write_value(client, auth_headers, dp_float["id"], 10.0)
    await _write_value(client, auth_headers, dp_float["id"], 20.0)
    await _write_value(client, auth_headers, dp_string["id"], "Wohnzimmer Temperatur")
    await _write_value(client, auth_headers, dp_string["id"], "Garage Licht")
    await _write_value(client, auth_headers, dp_bool["id"], True)
    await _write_value(client, auth_headers, dp_bool["id"], False)

    numeric_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp_float["id"]]},
                "values": [{"operator": "between", "lower": 15, "upper": 25}],
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert [row["new_value"] for row in numeric_rows] == [20.0]

    contains_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp_string["id"]]},
                "values": [{"operator": "contains", "value": "Wohn"}],
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert [row["new_value"] for row in contains_rows] == ["Wohnzimmer Temperatur"]

    regex_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp_string["id"]]},
                "values": [{"operator": "regex", "pattern": "^Garage"}],
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert [row["new_value"] for row in regex_rows] == ["Garage Licht"]

    bool_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp_bool["id"]]},
                "values": [{"operator": "eq", "value": True}],
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert [row["new_value"] for row in bool_rows] == [True]


async def test_ringbuffer_v2_value_filter_type_conflict_returns_422(client, auth_headers):
    dp_bool = await _create_dp(client, auth_headers, "RB DSL Value Conflict Bool", data_type="BOOLEAN", unit=None)
    await _write_value(client, auth_headers, dp_bool["id"], True)

    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": {
                "datapoints": {"ids": [dp_bool["id"]]},
                "values": [{"operator": "gt", "value": 0}],
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "operator 'gt' is not supported for data_type 'BOOLEAN'" in resp.text


async def test_ringbuffer_v2_value_filter_regex_guard_returns_422(client, auth_headers):
    dp_string = await _create_dp(client, auth_headers, "RB DSL Value Regex Guard", data_type="STRING", unit=None)
    await _write_value(client, auth_headers, dp_string["id"], "aaaaaaaaaaaaaaaa")

    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={
            "filters": {
                "datapoints": {"ids": [dp_string["id"]]},
                "values": [{"operator": "regex", "pattern": "(a+)+$"}],
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "unsafe regex pattern" in resp.text


async def test_ringbuffer_payload_snapshot_exposes_metadata_context(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB DSL Snapshot", data_type="FLOAT", unit="W")
    await _insert_binding(dp["id"], "KNX", {"group_address": "1/2/30", "state_group_address": "1/2/31"})
    await _insert_binding(dp["id"], "MQTT", {"topic": "house/living/temperature"})

    await _write_value(client, auth_headers, dp["id"], 23.5)

    rows = await _query_ringbuffer(client, auth_headers, {"q": dp["id"], "limit": 1})
    assert rows
    entry = rows[0]

    assert entry["metadata_version"] == 1
    metadata = entry["metadata"]
    assert metadata["source"]["adapter"] == "api"
    assert metadata["datapoint"]["id"] == dp["id"]
    assert "ringbuffer-filter-test" in metadata["datapoint"]["tags"]
    assert any(binding["adapter_type"] == "KNX" for binding in metadata["bindings"])
    assert any(binding["adapter_type"] == "MQTT" for binding in metadata["bindings"])

    knx_binding = next(binding for binding in metadata["bindings"] if binding["adapter_type"] == "KNX")
    assert knx_binding["normalized"]["group_address"] == "1/2/30"


async def test_ringbuffer_v2_filters_by_metadata_tags_and_adapter_info(client, auth_headers):
    dp_a = await _create_dp(client, auth_headers, "RB DSL Meta A", data_type="FLOAT", unit="W")
    dp_b = await _create_dp(client, auth_headers, "RB DSL Meta B", data_type="FLOAT", unit="W")

    await _insert_binding(dp_a["id"], "KNX", {"group_address": "1/3/10"})
    await _insert_binding(dp_b["id"], "MQTT", {"topic": "home/garage/temp"})

    await _write_value(client, auth_headers, dp_a["id"], 30.0)
    await _write_value(client, auth_headers, dp_b["id"], 15.0)

    knx_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "metadata": {
                    "tags_any_of": ["ringbuffer-filter-test"],
                    "adapter_types_any_of": ["knx"],
                    "group_addresses_any_of": ["1/3/10"],
                },
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert any(row["datapoint_id"] == dp_a["id"] for row in knx_rows)
    assert all(row["datapoint_id"] != dp_b["id"] for row in knx_rows)

    mqtt_rows = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            "filters": {
                "metadata": {
                    "adapter_types_any_of": ["mqtt"],
                    "topics_any_of": ["home/garage/temp"],
                },
            },
            "sort": {"field": "ts", "order": "asc"},
            "pagination": {"limit": 50, "offset": 0},
        },
    )
    assert any(row["datapoint_id"] == dp_b["id"] for row in mqtt_rows)
    assert all(row["datapoint_id"] != dp_a["id"] for row in mqtt_rows)


async def test_ringbuffer_csv_export_contains_full_filtered_result_independent_of_pagination(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB CSV Full Result", data_type="FLOAT", unit="W")

    await _write_value(client, auth_headers, dp["id"], 10.0)
    await _write_value(client, auth_headers, dp["id"], 11.0)
    await _write_value(client, auth_headers, dp["id"], 12.0)

    # Simulate a paginated UI query (limit=1). CSV export must still include all filtered rows.
    body = {
        "filters": {"datapoints": {"ids": [dp["id"]]}},
        "sort": {"field": "id", "order": "asc"},
        "pagination": {"limit": 1, "offset": 0},
    }
    page_rows = await _query_ringbuffer_v2(client, auth_headers, body)
    assert len(page_rows) == 1

    export_resp = await _export_ringbuffer_csv(client, auth_headers, body)
    assert export_resp.status_code == 200, export_resp.text
    assert export_resp.headers["content-type"].startswith("text/csv")

    csv_rows = _parse_csv_rows(export_resp.text)
    assert len(csv_rows) == 3

    query_all = await _query_ringbuffer_v2(
        client,
        auth_headers,
        {
            **body,
            "pagination": {"limit": 100, "offset": 0},
        },
    )
    assert len(query_all) == 3

    assert [row["id"] for row in csv_rows] == [str(row["id"]) for row in query_all]
    assert [json.loads(row["new_value_json"]) for row in csv_rows] == [row["new_value"] for row in query_all]
    assert all(row["datapoint_id"] == dp["id"] for row in csv_rows)


async def test_ringbuffer_csv_export_supports_open_time_bounds_and_large_result_sets(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "RB CSV Open Bounds", data_type="FLOAT", unit="W")

    await _write_value(client, auth_headers, dp["id"], -1.0)
    first_row = (
        await _query_ringbuffer_v2(
            client,
            auth_headers,
            {
                "filters": {"datapoints": {"ids": [dp["id"]]}},
                "sort": {"field": "id", "order": "asc"},
                "pagination": {"limit": 1, "offset": 0},
            },
        )
    )[0]

    # Enough rows to force multi-batch export processing.
    for i in range(220):
        await _write_value(client, auth_headers, dp["id"], float(i))

    export_resp = await _export_ringbuffer_csv(
        client,
        auth_headers,
        {
            "filters": {
                "datapoints": {"ids": [dp["id"]]},
                "time": {"from": first_row["ts"]},
            },
            "sort": {"field": "id", "order": "asc"},
            "pagination": {"limit": 5, "offset": 0},
        },
    )
    assert export_resp.status_code == 200, export_resp.text

    csv_rows = _parse_csv_rows(export_resp.text)
    assert len(csv_rows) == 220
    assert all(row["ts"] > first_row["ts"] for row in csv_rows)

    exported_values = [json.loads(row["new_value_json"]) for row in csv_rows]
    assert exported_values[:3] == [0.0, 1.0, 2.0]
    assert exported_values[-1] == 219.0


async def test_ringbuffer_csv_export_rejects_when_result_exceeds_limit(client, auth_headers, monkeypatch):
    import obs.api.v1.ringbuffer as ringbuffer_api

    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_MAX_ROWS", 2)
    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_CHUNK_SIZE", 2)

    dp = await _create_dp(client, auth_headers, "RB CSV Limit", data_type="FLOAT", unit="W")
    await _write_value(client, auth_headers, dp["id"], 1.0)
    await _write_value(client, auth_headers, dp["id"], 2.0)
    await _write_value(client, auth_headers, dp["id"], 3.0)

    resp = await _export_ringbuffer_csv(
        client,
        auth_headers,
        {
            "filters": {"datapoints": {"ids": [dp["id"]]}},
            "sort": {"field": "id", "order": "asc"},
            "pagination": {"limit": 10, "offset": 0},
        },
    )
    assert resp.status_code == 422
    assert "export row limit exceeded" in resp.text


async def test_ringbuffer_csv_export_returns_504_when_query_times_out(client, auth_headers, monkeypatch):
    import obs.api.v1.ringbuffer as ringbuffer_api

    async def _slow_query(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        return []

    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_QUERY_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(ringbuffer_api, "_query_v2_entries", _slow_query)

    resp = await _export_ringbuffer_csv(
        client,
        auth_headers,
        {
            "filters": {},
            "sort": {"field": "id", "order": "asc"},
            "pagination": {"limit": 10, "offset": 0},
        },
    )
    assert resp.status_code == 504
    assert "ringbuffer CSV export timed out" in resp.text
