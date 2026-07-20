"""Recover active segment bounds after committed appends (#919, Review #951 Runde 33, Finding 4 :998).

Wird der Prozess gekillt / läuft die Disk voll / scheitert das Manifest-Update
NACH dem Segment-Commit, aber BEVOR ``_refresh_active_segment_stats()`` in die
separate Manifest-DB committet, behält das aktive Segment über den Restart STALE
``from_ts``/``to_ts``-Metadaten. Zeitfenster-Queries wählen Segmente anhand dieser
Manifest-Grenzen (``list_segments_for_query``) → ein Query-Fenster kann das aktive
Segment ausschließen, obwohl es committete Zeilen in diesem Fenster enthält, bis ein
weiterer Append die Stats auffrischt.

Fix: die aktive-Segment-Stats bei ``open()``/Recovery aus dem tatsächlichen
Segment-Inhalt (``MIN(ts)``/``MAX(ts)``/``row_count``/``size``) neu berechnen und
ins Manifest schreiben – so verschwinden keine committeten Zeilen hinter stale
Grenzen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: Any, ts: str, *, dp: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


@pytest.mark.asyncio
async def test_open_recovers_stale_active_bounds_makes_committed_rows_visible(tmp_path: Path):
    """Stale Manifest-Grenzen am aktiven Segment werden bei ``open()`` aus dem Inhalt repariert.

    Crash-Simulation: nach dem Append werden die Manifest-``from_ts``/``to_ts`` des
    aktiven Segments künstlich auf ein enges, FALSCHES Fenster gesetzt (als hätte der
    Stats-Refresh nach dem Commit nie stattgefunden). Eine Zeitfenster-Query, die die
    committeten Zeilen abdeckt, würde das aktive Segment dann per
    ``list_segments_for_query`` ausschließen. Nach ``open()``/Recovery müssen die
    committeten Zeilen wieder sichtbar sein.
    """
    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    active_id = store._active_segment.segment_id
    await store.append(
        [
            _event(1, "2026-03-01T10:00:00.000Z"),
            _event(2, "2026-03-01T10:00:05.000Z"),
            _event(3, "2026-03-01T10:00:09.000Z"),
        ]
    )
    # Crash-Simulation: Manifest-Grenzen auf ein enges, altes Fenster verfälschen,
    # das die realen Zeilen NICHT abdeckt (row_count/size ebenfalls stale).
    await store.manifest.update_segment_stats(
        active_id,
        row_count=0,
        size_bytes=0,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:01.000Z",
    )
    await store.close()

    # Reopen auf demselben Root: der Recovery-Pfad muss die Grenzen reparieren.
    store2 = SqliteSegmentStore(root)
    await store2.open()
    try:
        # Zeitfenster-Query über das reale (committete) Fenster.
        rows = await store2.query(
            StoreQuery(
                from_ts="2026-03-01T09:59:00.000Z",
                to_ts="2026-03-01T10:01:00.000Z",
                from_exclusive=True,
                to_exclusive=True,
                limit=50,
            )
        )
        assert sorted(r["new_value"] for r in rows) == [1, 2, 3]
        # Manifest-Grenzen wurden aus dem Inhalt neu berechnet.
        active = await store2.manifest.get_segment(active_id)
        assert active is not None
        assert active.from_ts == "2026-03-01T10:00:00.000Z"
        assert active.to_ts == "2026-03-01T10:00:09.000Z"
        assert active.row_count == 3
    finally:
        await store2.close()


@pytest.mark.asyncio
async def test_open_leaves_consistent_active_stats_unchanged(tmp_path: Path):
    """Gegentest: konsistente (frische) Stats bleiben nach ``open()`` unverändert.

    Nach einem regulären Append hat ``_refresh_active_segment_stats`` bereits die
    korrekten Grenzen geschrieben. Der Recovery-Refresh bei ``open()`` darf sie zwar
    idempotent neu berechnen, muss aber DIESELBEN Werte ergeben.
    """
    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    active_id = store._active_segment.segment_id
    await store.append([_event(7, "2026-03-01T12:00:00.000Z"), _event(8, "2026-03-01T12:00:10.000Z")])
    before = await store.manifest.get_segment(active_id)
    await store.close()

    store2 = SqliteSegmentStore(root)
    await store2.open()
    try:
        after = await store2.manifest.get_segment(active_id)
        assert after is not None and before is not None
        assert after.from_ts == before.from_ts == "2026-03-01T12:00:00.000Z"
        assert after.to_ts == before.to_ts == "2026-03-01T12:00:10.000Z"
        assert after.row_count == before.row_count == 2
        # Query ohne Zeitfilter liefert weiter beide Zeilen.
        rows = await store2.query(StoreQuery(limit=50))
        assert sorted(r["new_value"] for r in rows) == [7, 8]
    finally:
        await store2.close()
