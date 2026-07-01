"""Segmentbewusste Query-Engine mit bounded LIMIT (#919/#932).

Deckt ab: Segmentauswahl zuerst über das Manifest (Zeitfilter → nur überlappende
Segmente; ohne Zeitfilter neueste zuerst), segmentübergreifend stabile Sortierung
über ``global_event_id`` DESC, deterministische Pagination (offset/limit) über
Segmentgrenzen und das *bounded* Verhalten: bei vielen Segmenten wird nicht jedes
voll geöffnet/gelesen, sobald ``offset+limit`` Zeilen sicher zusammengeführt sind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: int, ts: str, *, datapoint_id: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _fill_segments(store: SqliteSegmentStore, per_segment: list[list[StoreEvent]]) -> None:
    """Legt für jede Eventgruppe ein eigenes Segment an (rotate zwischen Gruppen)."""
    for index, events in enumerate(per_segment):
        if index > 0:
            await store.rotate()
        await store.append(events)


# ---------------------------------------------------------------------------
# (a) Zeitfilter wählt nur überlappende Segmente
# ---------------------------------------------------------------------------


async def test_manifest_time_filter_selects_only_overlapping_segments(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(1, "2026-01-01T00:00:00.000Z")],
            [_event(2, "2026-02-01T00:00:00.000Z")],
            [_event(3, "2026-03-01T00:00:00.000Z")],
        ],
    )
    # Fenster deckt nur das mittlere Segment ab.
    selected = await store.manifest.list_segments_for_query(
        from_ts="2026-01-15T00:00:00.000Z",
        to_ts="2026-02-15T00:00:00.000Z",
    )
    to_values = {s.from_ts for s in selected}
    assert to_values == {"2026-02-01T00:00:00.000Z"}


async def test_time_filtered_query_does_not_open_non_overlapping_segments(store: SqliteSegmentStore, monkeypatch):
    await _fill_segments(
        store,
        [
            [_event(1, "2026-01-01T00:00:00.000Z")],
            [_event(2, "2026-02-01T00:00:00.000Z")],
            [_event(3, "2026-03-01T00:00:00.000Z")],
        ],
    )
    opened: list[int] = []
    original = store._connection_for_read

    async def _spy(segment):
        opened.append(segment.segment_id)
        return await original(segment)

    monkeypatch.setattr(store, "_connection_for_read", _spy)
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-15T00:00:00.000Z",
            to_ts="2026-02-15T00:00:00.000Z",
            limit=10,
        )
    )
    assert [r["new_value"] for r in rows] == [2]
    # Genau ein Segment geöffnet — die beiden nicht überlappenden nicht.
    assert len(opened) == 1


# ---------------------------------------------------------------------------
# (b) Ohne Zeitfilter neueste Segmente zuerst
# ---------------------------------------------------------------------------


async def test_without_time_filter_newest_segments_first(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(1, "2026-01-01T00:00:00.000Z")],
            [_event(2, "2026-02-01T00:00:00.000Z")],
            [_event(3, "2026-03-01T00:00:00.000Z")],
        ],
    )
    selected = await store.manifest.list_segments_for_query()
    # segment_id DESC → neueste zuerst.
    assert selected == sorted(selected, key=lambda s: s.segment_id, reverse=True)
    assert len(selected) == 3


# ---------------------------------------------------------------------------
# (c) Segmentübergreifende Sortierung global_event_id stabil
# ---------------------------------------------------------------------------


async def test_cross_segment_ordering_is_stable_by_global_event_id(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(10, "2026-01-01T00:00:00.000Z"), _event(11, "2026-01-01T00:00:01.000Z")],
            [_event(20, "2026-01-02T00:00:00.000Z"), _event(21, "2026-01-02T00:00:01.000Z")],
        ],
    )
    rows = await store.query(StoreQuery(limit=10))
    gids = [r["global_event_id"] for r in rows]
    assert gids == sorted(gids, reverse=True)
    assert [r["new_value"] for r in rows] == [21, 20, 11, 10]


# ---------------------------------------------------------------------------
# (d) Pagination offset/limit über Segmentgrenzen deterministisch
# ---------------------------------------------------------------------------


async def test_pagination_is_deterministic_across_segment_boundaries(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(1, "2026-01-01T00:00:00.000Z"), _event(2, "2026-01-01T00:00:01.000Z")],
            [_event(3, "2026-01-02T00:00:00.000Z"), _event(4, "2026-01-02T00:00:01.000Z")],
        ],
    )
    # Vollständige, neueste-zuerst Reihenfolge ist [4, 3, 2, 1].
    page1 = await store.query(StoreQuery(limit=2, offset=0))
    page2 = await store.query(StoreQuery(limit=2, offset=2))
    assert [r["new_value"] for r in page1] == [4, 3]
    assert [r["new_value"] for r in page2] == [2, 1]


async def test_offset_spanning_segment_boundary(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(1, "2026-01-01T00:00:00.000Z"), _event(2, "2026-01-01T00:00:01.000Z")],
            [_event(3, "2026-01-02T00:00:00.000Z"), _event(4, "2026-01-02T00:00:01.000Z")],
        ],
    )
    # offset 1, limit 2 → [3, 2] (überspringt 4, kreuzt Segmentgrenze).
    rows = await store.query(StoreQuery(limit=2, offset=1))
    assert [r["new_value"] for r in rows] == [3, 2]


# ---------------------------------------------------------------------------
# (e) bounded: bei vielen Segmenten wird nicht jedes voll gelesen
# ---------------------------------------------------------------------------


async def test_bounded_limit_does_not_open_all_segments(store: SqliteSegmentStore, monkeypatch):
    per_segment = [[_event(i, f"2026-01-{i + 1:02d}T00:00:00.000Z")] for i in range(6)]
    await _fill_segments(store, per_segment)

    opened: list[int] = []
    original = store._connection_for_read

    async def _spy(segment):
        opened.append(segment.segment_id)
        return await original(segment)

    monkeypatch.setattr(store, "_connection_for_read", _spy)
    # limit 2, offset 0 → nur die 2 neuesten Segmente werden gebraucht.
    rows = await store.query(StoreQuery(limit=2))
    assert [r["new_value"] for r in rows] == [5, 4]
    # Früher Abbruch: nicht alle 6 Segmente geöffnet.
    assert len(opened) < len(per_segment)
    assert len(opened) == 2


async def test_bounded_with_offset_reads_enough_segments(store: SqliteSegmentStore, monkeypatch):
    per_segment = [[_event(i, f"2026-01-{i + 1:02d}T00:00:00.000Z")] for i in range(6)]
    await _fill_segments(store, per_segment)

    opened: list[int] = []
    original = store._connection_for_read

    async def _spy(segment):
        opened.append(segment.segment_id)
        return await original(segment)

    monkeypatch.setattr(store, "_connection_for_read", _spy)
    # offset 2 + limit 2 → 4 Kandidatenzeilen nötig → 4 Segmente (je 1 Zeile).
    rows = await store.query(StoreQuery(limit=2, offset=2))
    assert [r["new_value"] for r in rows] == [3, 2]
    assert len(opened) == 4


# ---------------------------------------------------------------------------
# (f) Value-Filter (#933) + Segmentauswahl zusammen
# ---------------------------------------------------------------------------


async def test_value_filter_with_segment_selection(store: SqliteSegmentStore):
    await _fill_segments(
        store,
        [
            [_event(5, "2026-01-01T00:00:00.000Z"), _event(50, "2026-01-01T00:00:01.000Z")],
            [_event(7, "2026-02-01T00:00:00.000Z"), _event(70, "2026-02-01T00:00:01.000Z")],
        ],
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-15T00:00:00.000Z",
            to_ts="2026-02-15T00:00:00.000Z",
            value_filters=[{"field": "new_value", "operator": "gte", "value": 10}],
            limit=10,
        )
    )
    # Nur das überlappende (zweite) Segment, davon nur new_value >= 10.
    assert [r["new_value"] for r in rows] == [70]
