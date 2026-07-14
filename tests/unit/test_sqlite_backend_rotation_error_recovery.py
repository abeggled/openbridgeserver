"""Rotation-Fehler-Recovery des SQLite-Segment-Backends (#951, Codex :2463).

Scheitert eine Rotation NACH dem Punkt, an dem die alte aktive Connection
sonst geschlossen würde (z. B. Disk voll beim Anlegen/Öffnen des Ersatz-
Segments), darf der Store nicht dauerhaft kaputtgehen: der aktive Writer
muss brauchbar bleiben, sodass ein Folge-``append`` weiter funktioniert.
Gegentest: eine normale Rotation bleibt unverändert korrekt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={"datapoint": {"tags": ["t"]}},
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_rotation_error_after_close_point_keeps_active_writer_usable(store: SqliteSegmentStore, monkeypatch):
    """Fehler beim Öffnen des Ersatz-Segments → aktiver Writer bleibt brauchbar.

    Erzwingt einen Fehler im Rotate-Pfad, der beim naiven Ablauf ERST NACH dem
    Schließen der alten aktiven Connection aufträte (Disk voll beim Öffnen des
    neuen Segments). Der Fehler propagiert, aber der Store bleibt schreibbar:
    ein Folge-``append`` darf NICHT dauerhaft auf einer geschlossenen Connection
    scheitern.
    """
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])

    original_open = store._open_segment_conn
    calls = {"n": 0}

    async def _boom_on_new_segment(filename: str):
        # Nur den Rotation-Öffnen-Aufruf sprengen (disk-full simuliert), nicht
        # einen späteren re-open im append-Pfad.
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full while opening replacement segment")
        return await original_open(filename)

    monkeypatch.setattr(store, "_open_segment_conn", _boom_on_new_segment)

    with pytest.raises(OSError):
        await store.rotate()

    # Kern der Regression: der aktive Zustand ist NICHT dauerhaft kaputt.
    monkeypatch.undo()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])

    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2}

    active = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1


async def test_rotation_error_on_create_segment_keeps_active_writer_usable(store: SqliteSegmentStore, monkeypatch):
    """Fehler beim Anlegen der neuen Manifest-Zeile → aktiver Writer bleibt brauchbar."""
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])

    async def _boom_create():
        raise OSError("manifest db full while creating segment")

    monkeypatch.setattr(store, "_create_segment_locked", _boom_create)

    with pytest.raises(OSError):
        await store.rotate()

    monkeypatch.undo()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])

    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2}


async def test_normal_rotation_still_correct(store: SqliteSegmentStore):
    """Gegentest: eine unveränderte, fehlerfreie Rotation bleibt korrekt."""
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    old_active = await store.manifest.get_active_segment()

    new_segment = await store.rotate()

    active = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1
    assert active[0].segment_id == new_segment.segment_id
    assert new_segment.segment_id != old_active.segment_id

    # Rotation löscht keine Daten, und der neue Writer nimmt Appends an.
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2}
