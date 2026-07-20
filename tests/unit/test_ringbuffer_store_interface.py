"""Portabler RingBufferStore-Contract + Capability-Deskriptor (#931/#920).

Die Grenze ist engine-neutral: Segment-/Manifest-/WAL-Begriffe dürfen NICHT
im portablen Contract auftauchen. SQLite-Interna wandern in ``stats()`` unter
``backend_extra`` — nie flach ins portable Schema.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from obs.ringbuffer.store.interface import (
    OrderingGuarantee,
    RingBufferStore,
    StoreCapabilities,
    StoreEvent,
    StoreQuery,
    StoreStats,
)


def test_capability_descriptor_has_required_fields():
    names = {f.name for f in fields(StoreCapabilities)}
    assert names == {
        "supports_native_retention",
        "supports_typed_pushdown",
        "ordering_guarantee",
        "supports_streaming_export",
    }


def test_ordering_guarantee_enum_values():
    assert OrderingGuarantee.GLOBAL_MONOTONIC.value == "global_monotonic"
    assert OrderingGuarantee.PER_PARTITION.value == "per_partition"
    assert OrderingGuarantee.NONE.value == "none"


def test_store_stats_splits_common_and_backend_extra():
    stats = StoreStats(common={"total": 5}, backend_extra={"wal_size_bytes": 42})
    assert stats.common["total"] == 5
    assert stats.backend_extra["wal_size_bytes"] == 42
    dumped = stats.as_dict()
    # SQLite-Interna liegen ausschließlich unter backend_extra, nie flach.
    assert dumped["common"]["total"] == 5
    assert "wal_size_bytes" not in dumped["common"]
    assert dumped["backend_extra"]["wal_size_bytes"] == 42


def test_ringbuffer_store_is_abstract_contract():
    # Der portable Contract darf nicht direkt instanziierbar sein.
    with pytest.raises(TypeError):
        RingBufferStore()  # type: ignore[abstract]

    portable_methods = {"append", "query", "stats", "enforce_retention", "capabilities"}
    assert portable_methods <= set(dir(RingBufferStore))

    # Segment-/Backend-Begriffe dürfen nicht in der portablen Grenze auftauchen.
    leaked = {"rotate", "segment_id", "manifest", "wal_checkpoint", "lease"}
    assert leaked.isdisjoint(set(dir(RingBufferStore)))


def test_store_event_and_query_are_plain_value_objects():
    event = StoreEvent(
        ts="2026-01-01T00:00:00.000Z",
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=1,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
    )
    assert event.datapoint_id == "dp-1"

    query = StoreQuery(from_ts=None, to_ts=None, limit=100)
    assert query.limit == 100
