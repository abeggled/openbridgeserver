"""Targeted coverage boost tests for :mod:`obs.api.v1.ringbuffer`.

Rewritten for the flat filterset schema (#431) — the original suite exercised
the deprecated group/rule helpers (``_decode_query``, ``_encode_query``,
``_cap_filterset_query``, ``RingBufferFiltersetCreate``, …) which no longer
exist. The new helpers (``_decode_filter``, ``_encode_filter``,
``_filter_to_query_v2``, ``RingBufferFiltersetIn``) are exercised here
with the same pure-function approach.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from obs.api.v1 import ringbuffer as rb_api
from obs.db.database import Database
from obs.ringbuffer.ringbuffer import (
    RingBufferStorageDeleteIncompleteError,
    get_optional_ringbuffer,
    init_ringbuffer,
    reset_ringbuffer,
)


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


@pytest.fixture(autouse=True)
async def _cleanup_ringbuffer_singleton():
    rb = get_optional_ringbuffer()
    if rb is not None:
        await rb.stop()
    reset_ringbuffer()
    try:
        yield
    finally:
        rb = get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()


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


def test_runtime_ringbuffer_helpers_cover_settings_and_event_bus(monkeypatch):
    monkeypatch.setattr(
        "obs.config.get_settings",
        lambda: SimpleNamespace(database=SimpleNamespace(path="/tmp/custom.sqlite")),
    )
    assert rb_api._ringbuffer_disk_path() == "/tmp/custom_ringbuffer.db"

    calls: list[tuple[str, object, object]] = []

    class _BusStub:
        def subscribe(self, event_type, handler):
            calls.append(("subscribe", event_type, handler))

        def unsubscribe(self, event_type, handler):
            calls.append(("unsubscribe", event_type, handler))

    rb = SimpleNamespace(handle_value_event=object())
    monkeypatch.setattr("obs.core.event_bus.get_event_bus", lambda: _BusStub())

    rb_api._subscribe_ringbuffer(rb)
    rb_api._unsubscribe_ringbuffer(rb)

    assert [call[0] for call in calls] == ["subscribe", "unsubscribe"]


def test_runtime_ringbuffer_helpers_ignore_missing_event_bus(monkeypatch):
    def _missing_bus():
        raise RuntimeError("event bus not initialized")

    monkeypatch.setattr("obs.core.event_bus.get_event_bus", _missing_bus)
    rb = SimpleNamespace(handle_value_event=object())

    rb_api._subscribe_ringbuffer(rb)
    rb_api._unsubscribe_ringbuffer(rb)


def test_configure_ringbuffer_requires_admin_dependency():
    dependency = inspect.signature(rb_api.configure_ringbuffer).parameters["_user"].default

    assert dependency.dependency is rb_api.get_admin_user


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


@pytest.mark.asyncio
async def test_query_v2_entries_returns_empty_when_monitor_disabled(monkeypatch):
    monkeypatch.setattr(rb_api, "is_ringbuffer_enabled", lambda: False)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: pytest.fail("registry should not be loaded"))

    rows = await rb_api._query_v2_entries(rb_api.RingBufferQueryV2())

    assert rows == []


@pytest.mark.asyncio
async def test_legacy_query_returns_empty_when_monitor_disabled(monkeypatch):
    monkeypatch.setattr(rb_api, "is_ringbuffer_enabled", lambda: False)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: pytest.fail("registry should not be loaded"))

    rows = await rb_api.query_ringbuffer()

    assert rows == []


@pytest.mark.asyncio
async def test_ringbuffer_stats_returns_disabled_and_enabled_shapes(monkeypatch):
    db = Database(":memory:")
    await db.connect()
    try:
        monkeypatch.setattr(rb_api, "is_ringbuffer_enabled", lambda: False)
        monkeypatch.setattr(rb_api, "get_optional_ringbuffer", lambda: None)
        disabled = await rb_api.ringbuffer_stats(_user="admin", db=db)
        assert disabled.enabled is False
        assert disabled.total == 0

        class _StatsRingBuffer:
            async def stats(self):
                return {
                    "total": 4,
                    "oldest_ts": "2026-01-01T00:00:00Z",
                    "newest_ts": "2026-01-01T00:01:00Z",
                    "storage": "file",
                    "max_entries": 100,
                    "effective_retention_seconds": None,
                    "max_file_size_bytes": None,
                    "max_age": None,
                    "file_size_bytes": 2048,
                }

        monkeypatch.setattr(rb_api, "is_ringbuffer_enabled", lambda: True)
        monkeypatch.setattr(rb_api, "get_optional_ringbuffer", lambda: _StatsRingBuffer())
        enabled = await rb_api.ringbuffer_stats(_user="admin", db=db)
        assert enabled.enabled is True
        assert enabled.total == 4
        assert enabled.file_size_bytes == 2048
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_configure_disable_stops_ringbuffer_persists_flag_and_deletes_storage(tmp_path, monkeypatch):
    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    rb = await init_ringbuffer("file", max_entries=10, disk_path=str(rb_path), max_file_size_bytes=1024 * 1024)
    await rb.record(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-api-disable",
        topic="dp/dp-api-disable/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
    )
    assert rb_path.exists()
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))

    try:
        stats = await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=False), _user="admin", db=db)
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
    finally:
        reset_ringbuffer()
        await db.disconnect()

    assert stats.enabled is False
    assert stats.total == 0
    assert cfg["enabled"] is False
    assert not rb_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cleanup", "persist"])
async def test_configure_disable_restores_running_ringbuffer_when_disable_fails(tmp_path, monkeypatch, failure):
    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    rb = await init_ringbuffer("file", max_entries=10, disk_path=str(rb_path), max_file_size_bytes=1024 * 1024)
    subscribed: list[object] = []
    deleted: list[str] = []

    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda restored_rb: subscribed.append(restored_rb))
    if failure == "cleanup":

        def _fail_delete(_path):
            deleted.append(_path)
            raise PermissionError("locked")

        monkeypatch.setattr(rb_api, "delete_ringbuffer_storage_files", _fail_delete)
    else:

        async def _fail_persist(*_args, **_kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(rb_api, "persist_ringbuffer_config", _fail_persist)

    try:
        with pytest.raises((PermissionError, RuntimeError)):
            await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=False), _user="admin", db=db)

        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert rb_api.is_ringbuffer_enabled() is True
        assert rb_api.get_optional_ringbuffer() is rb
        assert subscribed == ([rb] if failure == "cleanup" else [])
        assert deleted == ([str(rb_path)] if failure == "cleanup" else [])
        assert cfg["enabled"] is True
        assert (await rb.stats())["storage"] == "file"
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_configure_disable_does_not_restart_after_partial_storage_delete(tmp_path, monkeypatch):
    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    rb = await init_ringbuffer("file", max_entries=10, disk_path=str(rb_path), max_file_size_bytes=1024 * 1024)
    subscribed: list[object] = []

    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda restored_rb: subscribed.append(restored_rb))

    def _fail_partial_delete(_path):
        raise RingBufferStorageDeleteIncompleteError("locked db")

    monkeypatch.setattr(rb_api, "delete_ringbuffer_storage_files", _fail_partial_delete)

    try:
        with pytest.raises(RingBufferStorageDeleteIncompleteError, match="locked db"):
            await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=False), _user="admin", db=db)

        cfg = await rb_api.load_persisted_ringbuffer_config(db)
        assert rb_api.is_ringbuffer_enabled() is False
        assert rb_api.get_optional_ringbuffer() is None
        assert subscribed == []
        assert cfg["enabled"] is False
    finally:
        await rb.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_configure_enable_initializes_ringbuffer_when_missing(tmp_path, monkeypatch):
    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    subscribed = {"called": False}

    def _mark_subscribed(_rb):
        subscribed["called"] = True

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", _mark_subscribed)

    try:
        stats = await rb_api.configure_ringbuffer(
            rb_api.RingBufferConfig(
                enabled=True,
                max_entries=12,
                max_file_size_bytes=1024 * 1024,
                max_age=3600,
                # Default segment_max_age (6 h) would violate max_age >= 3*age; set
                # an explicit compatible value for this default-flip world (#919).
                segment_max_age=1200,
            ),
            _user="admin",
            db=db,
        )
        cfg = await rb_api.load_persisted_ringbuffer_config(db)
    finally:
        rb = rb_api.get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()
        await db.disconnect()

    assert subscribed["called"] is True
    assert stats.enabled is True
    assert stats.max_entries == 12
    assert cfg["enabled"] is True
    assert cfg["max_entries"] == 12


@pytest.mark.asyncio
async def test_configure_enable_rolls_back_runtime_when_persist_fails(tmp_path, monkeypatch):
    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    subscribed: list[object] = []
    unsubscribed: list[object] = []

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda rb: subscribed.append(rb))
    monkeypatch.setattr(rb_api, "_unsubscribe_ringbuffer", lambda rb: unsubscribed.append(rb))

    async def _fail_persist(*_args, **_kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(rb_api, "persist_ringbuffer_config", _fail_persist)

    try:
        with pytest.raises(RuntimeError, match="db locked"):
            await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True), _user="admin", db=db)

        assert subscribed
        assert unsubscribed == subscribed
        assert rb_api.is_ringbuffer_enabled() is False
        assert rb_api.get_optional_ringbuffer() is None
    finally:
        active_rb = rb_api.get_optional_ringbuffer()
        if active_rb is not None:
            await active_rb.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_configure_ringbuffer_serializes_concurrent_requests(monkeypatch):
    active = 0
    max_active = 0

    async def _fake_locked_config(_body, _db):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return rb_api.RingBufferStats(
            enabled=True,
            total=0,
            oldest_ts=None,
            newest_ts=None,
            storage="file",
            max_entries=None,
            effective_retention_seconds=None,
            max_file_size_bytes=1024,
            max_age=None,
            file_size_bytes=0,
        )

    monkeypatch.setattr(rb_api, "_configure_ringbuffer_locked", _fake_locked_config)

    await asyncio.gather(
        rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True), _user="admin", db=object()),
        rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True), _user="admin", db=object()),
    )

    assert max_active == 1


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


@pytest.mark.asyncio
async def test_runtime_enable_with_legacy_sets_pending_protection(tmp_path, monkeypatch):
    """Runtime-Enable des segmentierten Monitors bei vorhandener Legacy-DB spiegelt
    das Startup-Setup: Entscheidung ``pending`` wird persistiert und der Ersatz-Buffer
    startet retention-geschützt (#968, Codex :2369). Ohne den Fix bliebe die Entscheidung
    ``None`` und die FIFO-Retention könnte die Legacy-Quelle vorzeitig zurückgewinnen."""
    from obs.ringbuffer.persisted_config import LEGACY_DECISION_PENDING, load_legacy_migration_decision
    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    # Legacy-Single-DB (v1) anlegen, die der segmentierte Init als Legacy attacht.
    leg = _RB(storage="disk", disk_path=str(rb_path), max_entries=None)
    await leg.start()
    await leg.record(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-leg",
        topic="dp/dp-leg/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
    )
    await leg.stop()

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)
    try:
        await rb_api.configure_ringbuffer(
            rb_api.RingBufferConfig(
                enabled=True,
                segmented=True,
                max_entries=100,
                max_file_size_bytes=1024 * 1024,
                max_age=3600,
                segment_max_age=1200,
            ),
            _user="admin",
            db=db,
        )
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_PENDING
        rb = rb_api.get_optional_ringbuffer()
        assert rb is not None
        assert rb._legacy_retention_protected is True, "Legacy muss beim Runtime-Enable retention-geschützt starten"
    finally:
        rb = rb_api.get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_runtime_enable_keeps_buffer_when_stale_marker_repair_fails(tmp_path, monkeypatch):
    """Runtime init stays online and protected while a stale terminal marker is retried."""
    from obs.ringbuffer.persisted_config import (
        LEGACY_DECISION_MIGRATED,
        load_legacy_migration_decision,
        persist_legacy_migration_decision,
    )
    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    db = Database(":memory:")
    await db.connect()
    await persist_legacy_migration_decision(db, LEGACY_DECISION_MIGRATED)
    rb_path = tmp_path / "obs_ringbuffer.db"
    leg = _RB(storage="disk", disk_path=str(rb_path), max_entries=None)
    await leg.start()
    await leg.record(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-leg",
        topic="dp/dp-leg/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
    )
    await leg.stop()

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    subscribed: list[object] = []
    unsubscribed: list[object] = []
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda rb: subscribed.append(rb))
    monkeypatch.setattr(rb_api, "_unsubscribe_ringbuffer", lambda rb: unsubscribed.append(rb))

    async def _fail_repair(*_args, **_kwargs):
        raise RuntimeError("app db is locked")

    monkeypatch.setattr(rb_api, "ensure_legacy_migration_decision", _fail_repair)
    monkeypatch.setattr(rb_api, "persist_legacy_migration_decision", _fail_repair)

    try:
        stats = await rb_api.configure_ringbuffer(
            rb_api.RingBufferConfig(
                enabled=True,
                segmented=True,
                max_entries=100,
                max_file_size_bytes=1024 * 1024,
                max_age=3600,
                segment_max_age=1200,
            ),
            _user="admin",
            db=db,
        )

        rb = rb_api.get_optional_ringbuffer()
        assert stats.enabled is True
        assert rb is not None
        assert rb._legacy_retention_protected is True
        assert subscribed == [rb]
        assert unsubscribed == []
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_MIGRATED
    finally:
        rb = rb_api.get_optional_ringbuffer()
        if rb is not None:
            await rb.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_discard_rejected_when_ringbuffer_not_running():
    """``discard`` bei nicht laufendem Monitor (Singleton None/deaktiviert) → 409 statt
    terminaler ``discarded``-Persistenz ohne Löschung (#968, Codex :2084). Sonst bliebe
    die Legacy-DB auf der Platte und würde beim nächsten Start wieder attached, während
    der Assistent wegen der terminalen Entscheidung versteckt ist."""
    from obs.ringbuffer.persisted_config import load_legacy_migration_decision

    db = Database(":memory:")
    await db.connect()
    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    try:
        with pytest.raises(HTTPException) as exc:
            await rb_api.legacy_migration_decision(rb_api.LegacyMigrationDecisionIn(decision="discard"), _user="admin", db=db)
        assert exc.value.status_code == 409
        # Keine terminale Entscheidung persistiert – der Assistent bleibt bedienbar.
        assert await load_legacy_migration_decision(db) != "discarded"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_modeswitch_rollback_preserves_legacy_protection(tmp_path, monkeypatch):
    """Scheitert ein Mode-Switch-Rebuild, während die Legacy-Entscheidung pending/skipped
    ist, muss der Rollback den vorherigen Retention-Schutz bewahren (#968, Codex :2443).
    Sonst defaultet ``protect_legacy`` auf false und die FIFO-Retention könnte die
    über-budget Legacy-Quelle vor einer informierten Entscheidung löschen."""
    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    # Legacy-Single-DB, damit der segmentierte Buffer sie geschützt attacht.
    leg = _RB(storage="disk", disk_path=str(rb_path), max_entries=None)
    await leg.start()
    await leg.record(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-leg",
        topic="dp/dp-leg/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
    )
    await leg.stop()

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)
    monkeypatch.setattr(rb_api, "_unsubscribe_ringbuffer", lambda _rb: None)

    # Ausgangszustand: segmentierter Buffer mit geschütztem Legacy als Singleton.
    from obs.ringbuffer.ringbuffer import init_ringbuffer as _real_init

    await _real_init(
        storage="file",
        max_entries=100,
        disk_path=str(rb_path),
        max_file_size_bytes=1024 * 1024,
        max_age=3600,
        segmented=True,
        segment_max_age=1200,
        legacy_retention_protected=True,
    )
    rb_api.set_ringbuffer_enabled(True)

    # Beim Switch soll der Neuaufbau (non-segmented) fehlschlagen, das Restore aber gelingen.
    state = {"n": 0}

    async def _init_fail_first(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("rebuild boom")
        return await _real_init(*args, **kwargs)

    monkeypatch.setattr(rb_api, "init_ringbuffer", _init_fail_first)
    try:
        with pytest.raises(RuntimeError, match="rebuild boom"):
            await rb_api.configure_ringbuffer(
                rb_api.RingBufferConfig(enabled=True, segmented=False, max_entries=100, max_file_size_bytes=1024 * 1024, max_age=3600),
                _user="admin",
                db=db,
            )
        restored = rb_api.get_optional_ringbuffer()
        assert restored is not None, "Rollback muss den alten Buffer wiederherstellen"
        assert restored.segmented is True, "auf den alten (segmentierten) Modus zurueckgerollt"
        assert restored._legacy_retention_protected is True, "Rollback muss den Legacy-Schutz bewahren"
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_estimated_copy_bytes_accounts_for_live_bytes(tmp_path, monkeypatch):
    """``estimated_copy_bytes`` spiegelt das Ziel-Volumen des Migrators
    (``budget − headroom − live_bytes``), nicht das volle Budget (#968, Codex :2020).
    Verbrauchen Live-Segmente bereits Budget, muss die Schätzung sinken – sonst
    blockierte die UI eine valide Migration."""
    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    # Legacy-Quelle, deren v2-Äquivalent (2×) das Budget übersteigt → ohne Live-Abzug
    # wäre estimated == budget; mit Abzug muss es kleiner sein.
    leg = _RB(storage="disk", disk_path=str(rb_path), max_entries=None)
    await leg.start()
    for i in range(300):
        await leg.record(
            ts=f"2026-01-01T00:00:{i % 60:02d}.000Z",
            datapoint_id="dp-leg",
            topic="t",
            old_value=None,
            new_value=i,
            source_adapter="api",
            quality="good",
        )
    await leg.stop()
    legacy_size = rb_path.stat().st_size

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)
    # Budget kleiner als 2× Legacy, damit die Kappung am Ziel-Volumen (nicht an 2×Legacy) greift.
    budget = int(legacy_size * 1.5)
    rb = await init_ringbuffer(
        storage="file",
        max_entries=None,
        disk_path=str(rb_path),
        max_file_size_bytes=budget,
        max_age=None,
        segmented=True,
        legacy_retention_protected=True,
    )
    rb_api.set_ringbuffer_enabled(True)
    try:
        # Live-Bestand aufbauen, der einen Teil des Budgets belegt.
        for i in range(400):
            await rb.record(
                ts=f"2026-02-01T00:00:{i % 60:02d}.000Z",
                datapoint_id="dp-live",
                topic="t",
                old_value=None,
                new_value=i,
                source_adapter="api",
                quality="good",
            )

        status = await rb_api._legacy_migration_status(db)
        assert status.estimated_copy_bytes is not None
        assert status.budget_bytes == budget
        # Der Live-Bestand ist abgezogen → Schätzung strikt unter dem Budget.
        assert status.estimated_copy_bytes < status.budget_bytes, "Live-Bestand muss den Copy-Bedarf senken"
        assert status.estimated_copy_bytes >= 0
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_migration_status_reclaims_stale_copy_bytes_from_estimate_and_free_space(tmp_path, monkeypatch):
    """Der Wizard muss denselben stale-Copy-Cleanup wie ``/migration/start`` vorwegnehmen (#1009)."""

    class _MigrationStatusStub:
        def legacy_migration_progress(self):
            return {"phase": "idle"}

        async def legacy_migration_overview(self):
            return {"size_bytes": 300}

        async def stats(self):
            return {
                "max_file_size_bytes": 1000,
                "file_size_bytes": 800,
                "prognosis": {"effective_segment_max_bytes": 100},
                "store": {"backend_extra": {"retention_over_budget": False}},
            }

        async def attached_legacy_total_bytes(self):
            return 300

        async def reclaimable_migrating_total_bytes(self):
            return 200

    db = Database(":memory:")
    await db.connect()
    monkeypatch.setattr(rb_api, "get_optional_ringbuffer", lambda: _MigrationStatusStub())
    monkeypatch.setattr(rb_api, "is_ringbuffer_enabled", lambda: True)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(tmp_path / "obs_ringbuffer.db"))
    monkeypatch.setattr(rb_api.shutil, "disk_usage", lambda _path: SimpleNamespace(free=50))
    try:
        status = await rb_api._legacy_migration_status(db)
        # live = 800 total - 300 legacy - 200 stale; target = 1000 - 100 headroom - 300 live
        assert status.estimated_copy_bytes == 600
        assert status.disk_free_bytes == 250
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_enable_normalizes_segmented_off_for_memory_db(tmp_path, monkeypatch):
    """Aktiviert ein Admin den Monitor bei einer in-memory-DB (``:memory:``) ohne
    explizites ``segmented``, darf der persistierte Default ``segmented=true`` nicht
    überleben (#968, Codex :2221) – sonst schriebe ``init_ringbuffer`` ein reales
    ``:memory:_segments``-Verzeichnis. Gleiche Normalisierung wie der Startup."""
    db = Database(":memory:")
    await db.connect()
    subscribed = {"called": False}

    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: ":memory:")
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: subscribed.__setitem__("called", True))
    # Persistierten segmented=true-Default hinterlegen, den die Normalisierung kippen muss.
    await rb_api.persist_ringbuffer_config(db, enabled=False, max_entries=50, max_file_size_bytes=None, max_age=None, segmented=True)
    try:
        await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True, max_entries=50), _user="admin", db=db)
        rb = rb_api.get_optional_ringbuffer()
        assert rb is not None
        assert rb.segmented is False, "in-memory-DB darf nicht segmentiert werden"
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_enable_explicit_segmented_normalized_off_for_memory_db(tmp_path, monkeypatch):
    """Auch ein EXPLIZIT gepostetes ``segmented=true`` wird bei einer in-memory-DB auf
    False normalisiert (#968, Codex :2470): Clients wie das Config-Modal senden das Feld
    stets, ein reiner Implizit-Guard würde umgangen und schriebe ``:memory:_segments``."""
    db = Database(":memory:")
    await db.connect()
    reset_ringbuffer()
    rb_api.set_ringbuffer_enabled(False)
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: ":memory:")
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)
    try:
        await rb_api.configure_ringbuffer(rb_api.RingBufferConfig(enabled=True, segmented=True, max_entries=50), _user="admin", db=db)
        rb = rb_api.get_optional_ringbuffer()
        assert rb is not None
        assert rb.segmented is False, "explizit segmented=true muss bei :memory: normalisiert werden"
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()


@pytest.mark.asyncio
async def test_discard_failure_preserves_protection(tmp_path, monkeypatch):
    """Schlägt ``discard_legacy()`` fehl, muss der Retention-Schutz erhalten bleiben und die
    Entscheidung nicht-terminal (#968, Codex :2099) – sonst könnte die Retention die
    ungeschützte Legacy-Quelle zurückgewinnen, obwohl nie ``discarded`` erreicht wurde."""
    from obs.ringbuffer.persisted_config import load_legacy_migration_decision
    from obs.ringbuffer.ringbuffer import RingBuffer as _RB

    db = Database(":memory:")
    await db.connect()
    rb_path = tmp_path / "obs_ringbuffer.db"
    leg = _RB(storage="disk", disk_path=str(rb_path), max_entries=None)
    await leg.start()
    await leg.record(
        ts="2026-01-01T00:00:00.000Z", datapoint_id="dp-leg", topic="t", old_value=None, new_value=1, source_adapter="api", quality="good"
    )
    await leg.stop()

    reset_ringbuffer()
    monkeypatch.setattr(rb_api, "_ringbuffer_disk_path", lambda: str(rb_path))
    monkeypatch.setattr(rb_api, "_subscribe_ringbuffer", lambda _rb: None)
    rb = await init_ringbuffer(
        storage="file",
        max_entries=100,
        disk_path=str(rb_path),
        max_file_size_bytes=1024 * 1024,
        max_age=3600,
        segmented=True,
        segment_max_age=1200,
        legacy_retention_protected=True,
    )
    rb_api.set_ringbuffer_enabled(True)
    try:

        async def _boom():
            raise PermissionError("legacy DB locked")

        monkeypatch.setattr(rb, "discard_legacy", _boom)
        with pytest.raises(PermissionError):
            await rb_api.legacy_migration_decision(rb_api.LegacyMigrationDecisionIn(decision="discard"), _user="admin", db=db)
        # Schutz erhalten (discard lief unter Schutz und schlug fehl), Entscheidung nicht terminal.
        assert rb._legacy_retention_protected is True, "Schutz muss nach fehlgeschlagenem discard erhalten bleiben"
        assert await load_legacy_migration_decision(db) != "discarded"
    finally:
        active = rb_api.get_optional_ringbuffer()
        if active is not None:
            await active.stop()
        reset_ringbuffer()
        await db.disconnect()
