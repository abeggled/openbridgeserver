"""Targeted coverage boost tests for :mod:`obs.api.v1.ringbuffer`.

Rewritten for the flat filterset schema (#431) — the original suite exercised
the deprecated group/rule helpers (``_decode_query``, ``_encode_query``,
``_cap_filterset_query``, ``RingBufferFiltersetCreate``, …) which no longer
exist. The new helpers (``_decode_filter``, ``_encode_filter``,
``_filter_to_query_v2``, ``RingBufferFiltersetIn``) are exercised here
with the same pure-function approach.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from obs.api.v1 import ringbuffer as rb_api


class _RegistryStub:
    def __init__(self, entries):
        self._entries = entries

    def all(self):
        return list(self._entries)


class _RingbufferStub:
    def __init__(self, rows=None, exc: Exception | None = None):
        self.rows = rows or []
        self.exc = exc
        self.last_kwargs = None

    async def query_v2(self, **kwargs):
        self.last_kwargs = kwargs
        if self.exc:
            raise self.exc
        return list(self.rows)


class _FetchDbStub:
    def __init__(self):
        self._rows = {
            "fs-1": {
                "id": "fs-1",
                "name": "FS",
                "description": "desc",
                "dsl_version": 2,
                "is_active": 1,
                "color": "#3b82f6",
                "topbar_active": 0,
                "topbar_order": 0,
                "filter_json": '{"datapoints": ["dp-1"]}',
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        }

    async def fetchone(self, query, params=()):
        if "FROM ringbuffer_filtersets" in query:
            row = self._rows.get(params[0] if params else "")
            if row is None:
                return None
            # Mimic aiosqlite Row by exposing both __getitem__ and .keys().
            return _Row(row)
        raise AssertionError(f"Unexpected query: {query}")


class _Row:
    """Minimal stand-in for aiosqlite.Row with .keys() + __getitem__."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return list(self._data.keys())


def _mk_dp(dp_id="dp-1", name="Wohnzimmer", data_type="FLOAT"):
    return SimpleNamespace(id=dp_id, name=name, data_type=data_type)


def _mk_row(row_id=1, dp_id="dp-1"):
    return SimpleNamespace(
        id=row_id,
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id=dp_id,
        topic=f"dp/{dp_id}/value",
        old_value=1,
        new_value=2,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={"k": "v"},
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_helpers_now_uuid_decode_encode():
    assert rb_api._now_iso().endswith("+00:00")
    assert len(rb_api._new_id()) == 36

    default_filter = rb_api._decode_filter(None)
    assert isinstance(default_filter, rb_api.FilterCriteria)

    # Invalid JSON and non-object payload should both fall back to default filter
    assert isinstance(rb_api._decode_filter("{invalid"), rb_api.FilterCriteria)
    assert isinstance(rb_api._decode_filter("[]"), rb_api.FilterCriteria)

    criteria = rb_api.FilterCriteria(datapoints=["dp-1"])
    encoded = rb_api._encode_filter(criteria)
    assert '"datapoints":["dp-1"]' in encoded
    # Round-trip preserves data.
    assert rb_api._decode_filter(encoded).datapoints == ["dp-1"]


def test_csv_helpers_map_entry_fields():
    entry = rb_api.RingBufferEntryOut(
        id=11,
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-1",
        name=None,
        topic="dp/dp-1/value",
        old_value={"alt": 1},
        new_value={"neu": 2},
        source_adapter="api",
        quality="good",
        metadata_version=3,
        metadata={"tag": "küche"},
    )

    assert rb_api._csv_json({"x": "ü"}) == '{"x":"ü"}'
    row = rb_api._entry_to_csv_row(entry)
    assert row["id"] == "11"
    assert row["name"] == ""
    assert row["metadata_version"] == "3"
    assert row["metadata_json"] == '{"tag":"küche"}'


# ---------------------------------------------------------------------------
# _query_v2_entries — used by both /query and the multi-filterset endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_v2_entries_success_and_overrides(monkeypatch):
    registry = _RegistryStub([_mk_dp("dp-1", "Wohnzimmer Temp")])
    rb = _RingbufferStub(rows=[_mk_row(22, "dp-1")])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    body = rb_api.RingBufferQueryV2(
        filters=rb_api.RingBufferFiltersV2(q="wohnzimmer"),
        sort=rb_api.RingBufferSortV2(field="ts", order="asc"),
        pagination=rb_api.RingBufferPaginationV2(limit=10, offset=2),
    )

    rows = await rb_api._query_v2_entries(body, limit_override=7, offset_override=1)
    assert len(rows) == 1
    assert rows[0].name == "Wohnzimmer Temp"
    assert rb.last_kwargs is not None
    assert rb.last_kwargs["limit"] == 7
    assert rb.last_kwargs["offset"] == 1
    assert rb.last_kwargs["dp_ids_by_name"] == ["dp-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected_msg"),
    [
        (rb_api.RingBufferFiltersV2(adapters=rb_api.RingBufferAdapterFilterV2(any_of=["  "])), "filters.adapters.any_of"),
        (rb_api.RingBufferFiltersV2(datapoints=rb_api.RingBufferDatapointFilterV2(ids=["  "])), "filters.datapoints.ids"),
        (rb_api.RingBufferFiltersV2(values=[]), "filters.values"),
        (rb_api.RingBufferFiltersV2(metadata=rb_api.RingBufferMetadataFilterV2()), "filters.metadata"),
    ],
)
async def test_query_v2_entries_validation_guards(monkeypatch, filters, expected_msg):
    registry = _RegistryStub([_mk_dp()])
    rb = _RingbufferStub(rows=[])
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    with pytest.raises(HTTPException) as exc_info:
        await rb_api._query_v2_entries(rb_api.RingBufferQueryV2(filters=filters))

    assert exc_info.value.status_code == 422
    assert expected_msg in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_query_v2_entries_converts_value_error_to_http_422(monkeypatch):
    registry = _RegistryStub([_mk_dp()])
    rb = _RingbufferStub(exc=ValueError("invalid filter window"))
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    with pytest.raises(HTTPException) as exc_info:
        await rb_api._query_v2_entries(rb_api.RingBufferQueryV2())

    assert exc_info.value.status_code == 422
    assert "invalid filter window" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Flat filterset persistence helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_filterset_returns_none_or_flat_structure():
    db = _FetchDbStub()

    missing = await rb_api._fetch_filterset(db, "missing")
    assert missing is None

    fetched = await rb_api._fetch_filterset(db, "fs-1")
    assert fetched is not None
    assert fetched.id == "fs-1"
    assert fetched.color == "#3b82f6"
    assert fetched.topbar_active is False
    assert fetched.filter.datapoints == ["dp-1"]


@pytest.mark.asyncio
async def test_get_filterset_returns_404_when_missing(monkeypatch):
    async def _fetch_none(_db, _id, *, username=None):
        return None

    monkeypatch.setattr(rb_api, "_fetch_filterset", _fetch_none)

    with pytest.raises(HTTPException) as exc:
        await rb_api.get_ringbuffer_filterset("missing", db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_single_set_query_returns_404_when_missing(monkeypatch):
    async def _fetch_none(_db, _id, *, username=None):
        return None

    monkeypatch.setattr(rb_api, "_fetch_filterset", _fetch_none)

    with pytest.raises(HTTPException) as exc:
        await rb_api.query_ringbuffer_filterset("missing", db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_single_set_query_returns_empty_when_inactive(monkeypatch):
    fs = rb_api.RingBufferFiltersetOut(
        id="fs-x",
        name="FS",
        description="",
        dsl_version=2,
        is_active=False,
        color="#3b82f6",
        topbar_active=False,
        topbar_order=0,
        filter=rb_api.FilterCriteria(),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    async def _fetch(_db, _id, *, username=None):
        return fs

    monkeypatch.setattr(rb_api, "_fetch_filterset", _fetch)

    rows = await rb_api.query_ringbuffer_filterset("fs-x", db=object())
    assert rows == []


# ---------------------------------------------------------------------------
# Multi-query — cap enforcement and the empty-set_ids passthrough.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_query_rejects_too_many_set_ids():
    body = rb_api.RingBufferMultiQueryRequest(
        set_ids=[f"fs-{idx}" for idx in range(60)],
    )
    with pytest.raises(HTTPException) as exc:
        await rb_api.query_ringbuffer_filtersets_multi(body, db=object())
    assert exc.value.status_code == 422
    assert "too many filtersets" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_multi_query_empty_set_ids_invokes_underlying_query(monkeypatch):
    captured: list[rb_api.RingBufferQueryV2] = []

    async def _fake_query(query, *, limit_override=None, offset_override=None):  # noqa: ARG001
        captured.append(query)
        return []

    monkeypatch.setattr(rb_api, "_query_v2_entries", _fake_query)

    body = rb_api.RingBufferMultiQueryRequest(set_ids=[], limit=25, offset=3)
    rows = await rb_api.query_ringbuffer_filtersets_multi(body, db=object())
    assert rows == []
    assert captured, "underlying query must be invoked even for empty set_ids"
    assert captured[0].pagination.limit == 25
    assert captured[0].pagination.offset == 3
