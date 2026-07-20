"""Live-Rotation-Fehler NACH dem Writer-Switch demotet das alte Segment sofort (#951, Codex :2574).

Follow-up auf die Runde-38/39-Rotation-Fixes. ``rotate()`` schaltet den
In-Memory-Writer ZUERST auf das neue Segment (Runde-38-Reihenfolge) und schließt
DANACH das alte Segment im Manifest. Wirft einer der post-switch-Schritte
(``_try_truncate_checkpoint`` oder die folgenden Manifest-Updates) NACHDEM der
Writer bereits umgeschaltet wurde, scheitert der Append nicht, aber die alte
Manifest-Zeile bliebe ohne Gegenmaßnahme dauerhaft ``active``. Der
Startup-Reconciler ``_reconcile_multiple_active_segments()`` läuft nur aus
``open()``, sodass dieser NICHT-fatale Live-Fehler erst nach einem Neustart
repariert würde – bis dahin ist das alte Segment nie retention-eligible und der
Store bleibt ggf. über Budget.

Der Fix demotet das alte aktive Segment im rotate()-Fehlerpfad sofort auf
``closed`` (retention-eligible), BEVOR der Fehler propagiert. Der
Runde-38-Fix (Writer bleibt schreibbar bei Fehler beim ANLEGEN des Ersatzes)
und der Runde-39-Startup-Reconciler bleiben unverändert.
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


async def test_live_rotation_error_after_switch_demotes_old_segment(store: SqliteSegmentStore, monkeypatch):
    """Post-switch-Fehler → altes Segment sofort ``closed``, genau EIN active, Writer brauchbar.

    Erzwingt einen Fehler in ``_try_truncate_checkpoint`` – einem post-switch-Schritt,
    der NACH dem Writer-Switch auf ``new_segment`` läuft. Der Writer zeigt dann bereits
    auf das neue aktive Segment; die alte Manifest-Zeile bliebe ohne Fix ``active``.
    Erwartet: das alte Segment ist danach ``closed`` (retention-eligible), es gibt genau
    EIN active-Segment (das neue), ein Folge-``append`` funktioniert, und der Fehler
    propagiert dennoch.
    """
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    old_active = await store.manifest.get_active_segment()

    async def _boom_truncate(conn):
        raise OSError("checkpoint truncate failed after writer switch")

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _boom_truncate)

    with pytest.raises(OSError):
        await store.rotate()

    monkeypatch.undo()

    # Das alte Segment ist NICHT dauerhaft active geblieben, sondern demotet.
    segments = await store.manifest.list_segments()
    old_row = next(s for s in segments if s.segment_id == old_active.segment_id)
    assert old_row.status != SEGMENT_STATUS_ACTIVE

    # Genau EIN active-Segment, und es ist NICHT das alte.
    active = [s for s in segments if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1
    assert active[0].segment_id != old_active.segment_id

    # Der Writer ist brauchbar: ein Folge-append geht durch, keine Daten verloren.
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2}


async def test_live_rotation_error_in_manifest_update_demotes_old_segment(store: SqliteSegmentStore, monkeypatch):
    """Fehler in einem post-switch-Manifest-Update → altes Segment dennoch demotet.

    Zweiter post-switch-Pfad: nach erfolgreichem Truncate wirft der atomare
    ``close_segment_with_size`` (#951, R49). Auch hier darf die alte Zeile nicht
    ``active`` bleiben.
    """
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    old_active = await store.manifest.get_active_segment()

    original_close = store.manifest.close_segment_with_size

    async def _boom_close(segment_id, *, size_bytes):
        raise OSError("manifest close_segment_with_size failed after writer switch")

    monkeypatch.setattr(store.manifest, "close_segment_with_size", _boom_close)

    with pytest.raises(OSError):
        await store.rotate()

    monkeypatch.setattr(store.manifest, "close_segment_with_size", original_close)

    segments = await store.manifest.list_segments()
    old_row = next(s for s in segments if s.segment_id == old_active.segment_id)
    assert old_row.status != SEGMENT_STATUS_ACTIVE

    active = [s for s in segments if s.status == SEGMENT_STATUS_ACTIVE]
    assert len(active) == 1
    assert active[0].segment_id != old_active.segment_id

    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2}
