"""Codex-Runde-39 [P2]-Findings am ``sqlite_backend.py`` (#951, PR #951).

Zwei Findings, jeweils TDD-first (Test reproduziert das Fehlverhalten ohne Fix):

* **F1 (:2485)** – Crash WÄHREND ``rotate()`` NACH Aktivierung des Ersatz-Segments,
  aber BEVOR das alte Segment geschlossen ist, hinterlässt ZWEI ``active``-Zeilen im
  Manifest. ``get_active_segment()`` schreibt beim Restart ins neuere; das ältere
  ``active``-Segment ist nie retention-eligible → Alt-Daten permanent unlöschbar.
  Der ``open()``-Recovery-Pfad demotet ältere ``active``-Segmente auf ``closed``.
* **F2 (:3068)** – ist die DB-Datei eines NICHT-Legacy-v2-Segments außerhalb des
  Retention-Pfads verschwunden, überspringt der Read-Pfad sie – aber der
  No-Zero-History-Guard behandelte ihren Manifest-``row_count`` weiter als lesbare
  Historie und konnte so die attached Legacy-Datei löschen (letzte lesbare Kopie
  verloren). Der Guard prüft jetzt zusätzlich die Datei-Existenz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_RETENTION_ELIGIBLE
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


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _crash_mid_rotate_second_active(store: SqliteSegmentStore):
    """Mimt einen Crash WÄHREND ``rotate()``: das Ersatz-Segment ist durabel angelegt
    UND aktiviert (Manifest-Zeile ``active`` + DB-Datei auf Platte), das alte Segment
    aber noch NICHT geschlossen. Es bleiben zwei ``active``-Zeilen zurück. Die neue
    Segment-Datei wird real erzeugt (wie in rotate() via ``_open_segment_conn``), damit
    die Missing-Active-Recovery beim Reopen sie nicht als fehlend behandelt.
    """
    new_segment = await store._create_segment_locked()
    conn = await store._open_segment_conn(new_segment.filename)
    await conn.close()
    return new_segment


async def _attach_legacy_blob(store: SqliteSegmentStore, size_bytes: int, *, name: str) -> tuple[int, Path]:
    """Registriert eine Legacy-Blob-Datei als read-only Segment (row_count=0, to_ts=NULL)."""
    legacy_file = store._root / name
    legacy_file.write_bytes(b"\x00" * size_bytes)
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=size_bytes)
    return rec.segment_id, legacy_file


# ===========================================================================
# F1 – Zwei-active-Zustand beim Öffnen auflösen (Crash mitten in rotate())
# ===========================================================================


async def test_open_demotes_older_of_two_active_segments(tmp_path: Path):
    # Crash-Simulation: rotate() hat das Ersatz-Segment aktiviert, das alte aber
    # noch nicht geschlossen → zwei ``active``-Zeilen im Manifest. Beim Reopen muss
    # genau EINS (das neueste) aktiv bleiben, das ältere auf ``closed`` demotet und
    # retention-eligible werden; Append muss weiter funktionieren.
    root = tmp_path / "root"
    s = SqliteSegmentStore(root)
    await s.open()
    old_active = await s.manifest.get_active_segment()
    await s.append([_event(1, "2026-01-01T00:00:01.000Z")])
    # Zweites aktives Segment durabel anlegen (mimt die durabel-gemachte, aber nicht
    # abgeschlossene Rotation): Manifest-Zeile ``active`` + DB-Datei auf Platte.
    new_active = await _crash_mid_rotate_second_active(s)
    assert new_active.segment_id > old_active.segment_id
    # Vor dem Fix wären hier zwei active-Segmente – nachweisen:
    actives_before = [x for x in await s.manifest.list_segments() if x.status == SEGMENT_STATUS_ACTIVE]
    assert {a.segment_id for a in actives_before} == {old_active.segment_id, new_active.segment_id}
    await s.close()

    # Reopen: Recovery muss den Zwei-active-Zustand auflösen.
    s2 = SqliteSegmentStore(root)
    await s2.open()
    try:
        actives_after = [x for x in await s2.manifest.list_segments() if x.status == SEGMENT_STATUS_ACTIVE]
        assert len(actives_after) == 1, "nach Recovery darf nur EIN Segment aktiv sein"
        assert actives_after[0].segment_id == new_active.segment_id, "das neueste Segment bleibt aktiv"

        # Das ältere Segment ist jetzt retention-eligible (closed), nicht mehr active.
        demoted = await s2.manifest.get_segment(old_active.segment_id)
        assert demoted is not None
        assert demoted.status != SEGMENT_STATUS_ACTIVE
        assert demoted.status in SEGMENT_STATUS_RETENTION_ELIGIBLE, "das demotete Segment muss retention-eligible sein"

        # Append funktioniert weiter (schreibt ins neue aktive Segment).
        await s2.append([_event(2, "2026-01-01T00:00:02.000Z")])
        assert (await s2.manifest.get_active_segment()).segment_id == new_active.segment_id
    finally:
        await s2.close()


async def test_demoted_stuck_active_becomes_retention_eligible(tmp_path: Path):
    # Ende-zu-Ende: die Alt-Daten des gestuckten active-Segments dürfen nach der
    # Recovery nicht permanent unlöschbar bleiben. Unter Size-Druck muss das demotete
    # (jetzt closed) Segment als Opfer freigegeben werden.
    root = tmp_path / "root"
    s = SqliteSegmentStore(root)
    await s.open()
    old_active = await s.manifest.get_active_segment()
    await s.append([_event(1, "2026-01-01T00:00:01.000Z")])
    # Manifest-Größe des alten Segments künstlich hoch setzen, damit Size-Retention greift.
    await s.manifest.update_segment_size(old_active.segment_id, size_bytes=8 * 1024 * 1024)
    await _crash_mid_rotate_second_active(s)  # zweites active (Crash mitten in rotate)
    await s.close()

    s2 = SqliteSegmentStore(root)
    await s2.open()
    try:
        # Frische Daten ins (neue) aktive Segment → Guard/Nicht-leer.
        await s2.append([_event(2, "2026-01-01T00:00:02.000Z")])
        s2._retention_config = StoreRetentionConfig(max_file_size_bytes=1024 * 1024)
        removed = await s2.enforce_retention()
        assert removed >= 1, "das demotete alte active-Segment muss retention-eligible sein"
        assert await s2.manifest.get_segment(old_active.segment_id) is None
    finally:
        await s2.close()


async def test_open_single_active_start_unchanged(tmp_path: Path):
    # Gegentest: ein normaler Ein-active-Start bleibt unverändert – kein Segment wird
    # demotet, das aktive Segment bleibt aktiv, Append funktioniert.
    root = tmp_path / "root"
    s = SqliteSegmentStore(root)
    await s.open()
    active = await s.manifest.get_active_segment()
    await s.append([_event(1, "2026-01-01T00:00:01.000Z")])
    await s.close()

    s2 = SqliteSegmentStore(root)
    await s2.open()
    try:
        actives = [x for x in await s2.manifest.list_segments() if x.status == SEGMENT_STATUS_ACTIVE]
        assert len(actives) == 1
        assert actives[0].segment_id == active.segment_id, "das einzige active-Segment bleibt unverändert aktiv"
        await s2.append([_event(2, "2026-01-01T00:00:02.000Z")])
    finally:
        await s2.close()


# ===========================================================================
# F2 – No-Zero-History-Guard: fehlende v2-Datei zählt nicht als lesbare Historie
# ===========================================================================


async def test_guard_ignores_v2_segment_with_missing_file(store: SqliteSegmentStore):
    # attached Legacy über Budget PLUS ein geschlossenes v2-Segment mit row_count>0,
    # dessen DATEI fehlt. Ohne Fix hält der Guard den Manifest-row_count für lesbare
    # Historie → er hebt ab und die attached LESBARE Legacy-DB wird als Size-Opfer
    # gelöscht (letzte lesbare Kopie verloren). Der Fix wertet die fehlende v2-Datei
    # NICHT als lesbare Historie → das Legacy bleibt.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024, name="legacy_hist.db")

    # Geschlossenes v2-Segment mit row_count>0 anlegen, dann seine Datei entfernen.
    await store.append([_event(1, "2026-01-01T00:00:01.000Z")])
    v2 = await store.manifest.get_active_segment()
    await store.rotate()  # v2 wird geschlossen; frisches aktives Segment folgt.
    v2_after = await store.manifest.get_segment(v2.segment_id)
    assert v2_after.row_count > 0
    # Datei des geschlossenen v2-Segments außerhalb des Retention-Pfads entfernen.
    (store._segments_dir / v2_after.filename).unlink()

    # Guard darf das fehlende v2-Segment NICHT als lesbare Historie zählen.
    assert await store._has_nonlegacy_data_segment() is False

    # Konsequenz: unter Size-Druck bleibt die lesbare Legacy-DB erhalten (Guard hebt
    # nicht ab).
    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1024 * 1024)
    await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is not None, "Legacy-DB darf nicht gelöscht werden"
    assert legacy_file.exists()


async def test_guard_released_by_readable_v2_segment(store: SqliteSegmentStore):
    # Gegentest: ein VORHANDENES lesbares geschlossenes v2-Segment mit row_count>0
    # gibt den Guard normal frei → das attached Legacy ist unter Size-Druck löschbar.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024, name="legacy_hist.db")

    await store.append([_event(1, "2026-01-01T00:00:01.000Z")])
    v2 = await store.manifest.get_active_segment()
    await store.rotate()  # v2 geschlossen, Datei bleibt vorhanden.
    v2_after = await store.manifest.get_segment(v2.segment_id)
    assert v2_after.row_count > 0
    assert (store._segments_dir / v2_after.filename).exists()

    # Guard ist durch das lesbare v2-Segment erfüllt.
    assert await store._has_nonlegacy_data_segment() is True

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1024 * 1024)
    await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is None, "Legacy-DB muss bei erfülltem Guard löschbar sein"
    assert not legacy_file.exists()
