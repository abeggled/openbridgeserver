"""Unit tests for ringbuffer query v2 guards and validation."""

from __future__ import annotations

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


async def _record_value(
    rb: RingBuffer,
    value: object,
    ts: str,
    datapoint_id: str = "dp-query-v2",
    metadata: dict | None = None,
) -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_query_v2_returns_empty_when_not_started():
    rb = RingBuffer(storage="memory", max_entries=100)
    rows = await rb.query_v2()
    assert rows == []


@pytest.mark.asyncio
async def test_query_v2_rejects_invalid_time_window():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match="effective 'from' must be earlier than effective 'to'"):
            await rb.query_v2(
                from_ts="2026-01-01T00:00:10.000Z",
                to_ts="2026-01-01T00:00:00.000Z",
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"sort_field": "datapoint_id"}, "invalid sort field"),
        ({"sort_order": "sideways"}, "invalid sort order"),
        ({"limit": 0}, "limit must be >= 1"),
        ({"offset": -1}, "offset must be >= 0"),
    ],
)
async def test_query_v2_rejects_invalid_sort_and_pagination(kwargs: dict, msg: str):
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        await _record_value(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match=msg):
            await rb.query_v2(**kwargs)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_supports_relative_time_bound_only():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        await _record_value(rb, 1, "2099-01-01T00:00:00.000Z")
        rows = await rb.query_v2(from_relative_seconds=-60)
        assert rows
    finally:
        await rb.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operator", "spec", "expected"),
    [
        ("eq", {"value": 10.5}, [10.5]),
        ("ne", {"value": 10.5}, [11.0]),
        ("gt", {"value": 10.5}, [11.0]),
        ("lt", {"value": 11.0}, [10.5]),
        ("between", {"lower": 10.0, "upper": 10.7}, [10.5]),
    ],
)
async def test_query_v2_value_filter_numeric_matrix(operator: str, spec: dict, expected: list[float]):
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        dp_id = "dp-float"
        await _record_value(rb, 10.5, "2026-01-01T00:00:00.000Z", datapoint_id=dp_id)
        await _record_value(rb, 11.0, "2026-01-01T00:00:01.000Z", datapoint_id=dp_id)
        rows = await rb.query_v2(
            datapoint_ids=[dp_id],
            value_filters=[{"operator": operator, **spec}],
            datapoint_types={dp_id: "FLOAT"},
            sort_field="ts",
            sort_order="asc",
            limit=100,
            offset=0,
        )
        assert [row.new_value for row in rows] == expected
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_value_filter_supports_string_contains_and_regex():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        dp_id = "dp-string"
        await _record_value(rb, "Wohnzimmer Temperatur", "2026-01-01T00:00:00.000Z", datapoint_id=dp_id)
        await _record_value(rb, "Garage Licht", "2026-01-01T00:00:01.000Z", datapoint_id=dp_id)

        contains_rows = await rb.query_v2(
            datapoint_ids=[dp_id],
            value_filters=[{"operator": "contains", "value": "Wohn"}],
            datapoint_types={dp_id: "STRING"},
            sort_field="ts",
            sort_order="asc",
            limit=100,
            offset=0,
        )
        assert [row.new_value for row in contains_rows] == ["Wohnzimmer Temperatur"]

        regex_rows = await rb.query_v2(
            datapoint_ids=[dp_id],
            value_filters=[{"operator": "regex", "pattern": r"^Garage"}],
            datapoint_types={dp_id: "STRING"},
            sort_field="ts",
            sort_order="asc",
            limit=100,
            offset=0,
        )
        assert [row.new_value for row in regex_rows] == ["Garage Licht"]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_value_filter_supports_boolean_eq():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        dp_id = "dp-bool"
        await _record_value(rb, True, "2026-01-01T00:00:00.000Z", datapoint_id=dp_id)
        await _record_value(rb, False, "2026-01-01T00:00:01.000Z", datapoint_id=dp_id)
        rows = await rb.query_v2(
            datapoint_ids=[dp_id],
            value_filters=[{"operator": "eq", "value": True}],
            datapoint_types={dp_id: "BOOLEAN"},
            sort_field="ts",
            sort_order="asc",
            limit=100,
            offset=0,
        )
        assert [row.new_value for row in rows] == [True]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_value_filter_rejects_type_conflict():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        dp_id = "dp-bool-conflict"
        await _record_value(rb, True, "2026-01-01T00:00:00.000Z", datapoint_id=dp_id)
        with pytest.raises(ValueError, match="operator 'gt' is not supported for data_type 'BOOLEAN'"):
            await rb.query_v2(
                datapoint_ids=[dp_id],
                value_filters=[{"operator": "gt", "value": 0}],
                datapoint_types={dp_id: "BOOLEAN"},
                limit=100,
                offset=0,
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_value_filter_rejects_unsafe_regex_pattern():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        dp_id = "dp-regex-guard"
        await _record_value(rb, "aaaaaaaaaaaaaaaa", "2026-01-01T00:00:00.000Z", datapoint_id=dp_id)
        with pytest.raises(ValueError, match="unsafe regex pattern"):
            await rb.query_v2(
                datapoint_ids=[dp_id],
                value_filters=[{"operator": "regex", "pattern": r"(a+)+$"}],
                datapoint_types={dp_id: "STRING"},
                limit=100,
                offset=0,
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_query_v2_metadata_filters_tags_and_binding_adapter_info():
    rb = RingBuffer(storage="memory", max_entries=100)
    await rb.start()
    try:
        await _record_value(
            rb,
            21.5,
            "2026-01-01T00:00:00.000Z",
            datapoint_id="dp-meta-a",
            metadata={
                "datapoint": {"tags": ["klima", "wohnzimmer"]},
                "bindings": [
                    {
                        "adapter_type": "KNX",
                        "adapter_instance_id": "inst-knx-1",
                        "normalized": {
                            "group_address": "1/2/3",
                            "topic": "",
                            "entity_id": "",
                            "register_type": "",
                        },
                    }
                ],
            },
        )
        await _record_value(
            rb,
            18.0,
            "2026-01-01T00:00:01.000Z",
            datapoint_id="dp-meta-b",
            metadata={
                "datapoint": {"tags": ["garage"]},
                "bindings": [
                    {
                        "adapter_type": "MQTT",
                        "adapter_instance_id": "inst-mqtt-1",
                        "normalized": {
                            "group_address": "",
                            "topic": "home/garage/temp",
                            "entity_id": "",
                            "register_type": "",
                        },
                    }
                ],
            },
        )

        rows = await rb.query_v2(
            metadata_tags_any_of=["klima"],
            metadata_adapter_types_any_of=["knx"],
            metadata_group_addresses_any_of=["1/2/3"],
            limit=50,
            offset=0,
        )
        assert [row.datapoint_id for row in rows] == ["dp-meta-a"]

        mqtt_rows = await rb.query_v2(
            metadata_adapter_types_any_of=["mqtt"],
            metadata_topics_any_of=["home/garage/temp"],
            limit=50,
            offset=0,
        )
        assert [row.datapoint_id for row in mqtt_rows] == ["dp-meta-b"]
    finally:
        await rb.stop()
