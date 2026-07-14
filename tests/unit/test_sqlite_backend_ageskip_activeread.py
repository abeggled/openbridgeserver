"""Codex-[P2]-Findings Runde 24 am RingBuffer-SQLite-Backend (#919, PR #951).

Zwei unabhaengige Findings, je TDD-first (rot ohne Fix, gruen mit Fix):

Finding 1 (``_enforce_age_cutoff``): ein LEERES geschlossenes v2-Segment mit
``to_ts=NULL`` (z. B. ein rotiertes idle-leeres aktives Segment) kann in der
Age-Victim-Reihenfolge VOR aelteren, ueber-Cutoff-Daten-Segmenten liegen. Der
Age-Pass ``break``te bisher am ``to_ts is None``-Zweig fuer NICHT-Legacy-Segmente
→ alle spaeteren, tatsaechlich zu alten v2-Segmente blieben unbegrenzt retained.
Fix: LEERE (``row_count <= 0``) unknown-age NICHT-Legacy-Segmente ueberspringen
(analog zur Runde-18-``continue``-Behandlung fuer unknown-age-Legacy), aber ein
NICHT-leeres v2-Segment mit ``to_ts=NULL`` weiterhin konservativ behalten (break).

Finding 2 (``_connection_for_read``): aktive Reads liefen ueber die Writer-
Connection (``_active_conn``), die auf derselben Connection uncommittete Zeilen
sieht. Eine Monitor-/API-Query konnte so kurzzeitig Zeilen zurueckgeben, die noch
nicht committet sind (oder spaeter zurueckgerollt werden). Fix: aktive Reads ueber
eine SEPARATE read-only Connection (WAL → nur committete Daten sichtbar).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: Any, ts: str, *, dp: str = "dp-1", old: Any = None) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=old,
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


# ===========================================================================
# Finding 1: Age-Retention ueberspringt LEERE unknown-age v2-Segmente
# ===========================================================================


async def test_age_retention_skips_empty_unknown_v2_and_trims_older(store: SqliteSegmentStore):
    # Reihenfolge in der Victim-Liste (aeltestes zuerst): zuerst ein altes v2-Segment
    # mit BEKANNTER, weit zurueckliegender to_ts (ueber Cutoff → muss weichen), dann
    # ein LEERES geschlossenes v2-Segment mit to_ts=NULL (rotiertes idle-leeres
    # Segment). Ohne Fix wuerde der Age-Pass an diesem leeren unknown-age-Segment
    # per break stoppen, sobald es in der Reihenfolge vor weiteren alten Daten-
    # Segmenten steht. Wir konstruieren deshalb: LEER-unknown liegt VOR einem
    # zweiten alten Daten-Segment.
    old_ts_1 = (datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old_ts_2 = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # (1) Aeltestes Daten-Segment (bekannte, alte to_ts).
    await store.append([_event(1, old_ts_1)])
    old_v2_a = await store.manifest.get_active_segment()
    await store.rotate()

    # (2) Leeres aktives Segment sofort rotieren → geschlossenes LEERES v2-Segment
    #     mit row_count=0 und to_ts=NULL (MAX(ts) ueber leere Tabelle ist NULL).
    empty_v2 = await store.manifest.get_active_segment()
    await store.rotate()

    # (3) Zweites altes Daten-Segment (bekannte, alte to_ts) NACH dem leeren Segment.
    await store.append([_event(2, old_ts_2)])
    old_v2_b = await store.manifest.get_active_segment()
    await store.rotate()

    # (4) Frisches aktives Segment sichert den No-Zero-History-Guard und bleibt.
    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    await store.append([_event(3, fresh_ts)])

    # Vorbedingung: das leere Segment hat wirklich row_count=0 und to_ts=NULL.
    empty_rec = await store.manifest.get_segment(empty_v2.segment_id)
    assert empty_rec is not None
    assert empty_rec.row_count == 0
    assert empty_rec.to_ts is None

    store._retention_config = StoreRetentionConfig(max_age=60)  # alles > 60s faellt
    removed = await store.enforce_retention()

    # BEIDE alten Daten-Segmente wurden altersbedingt getrimmt – der Pass hat NICHT
    # am leeren unknown-age-Segment abgebrochen.
    assert await store.manifest.get_segment(old_v2_a.segment_id) is None
    assert await store.manifest.get_segment(old_v2_b.segment_id) is None
    # Das leere unknown-age-Segment wurde uebersprungen ODER mitgeloescht.
    assert removed >= 2


async def test_age_retention_keeps_nonempty_unknown_v2(store: SqliteSegmentStore):
    # Gegenprobe: ein NICHT-leeres v2-Segment mit to_ts=NULL (unbekanntes, aber evtl.
    # relevantes Alter) darf NICHT faelschlich geloescht werden. Der Age-Pass bricht
    # dort weiterhin konservativ ab.
    old_ts = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    await store.append([_event(1, old_ts)])
    old_v2 = await store.manifest.get_active_segment()
    await store.rotate()

    # Zweites geschlossenes Segment mit Daten, aber kuenstlich auf to_ts=NULL gesetzt
    # (unbekanntes Alter bei vorhandenen Zeilen).
    await store.append([_event(2, old_ts)])
    unknown_v2 = await store.manifest.get_active_segment()
    await store.rotate()
    await store.manifest.update_segment_stats(
        unknown_v2.segment_id,
        row_count=1,
        size_bytes=4096,
        from_ts=None,
        to_ts=None,
    )

    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    await store.append([_event(3, fresh_ts)])

    unknown_rec = await store.manifest.get_segment(unknown_v2.segment_id)
    assert unknown_rec is not None
    assert unknown_rec.row_count == 1
    assert unknown_rec.to_ts is None

    store._retention_config = StoreRetentionConfig(max_age=60)
    await store.enforce_retention()

    # Das aelteste Daten-Segment (bekannte alte to_ts) darf weichen ...
    assert await store.manifest.get_segment(old_v2.segment_id) is None
    # ... aber das NICHT-leere unknown-age-Segment bleibt konservativ erhalten
    # (der Age-Pass hat dort abgebrochen).
    assert await store.manifest.get_segment(unknown_v2.segment_id) is not None


# ===========================================================================
# Finding 2: aktive Reads sehen keine uncommitteten/zurueckgerollten Zeilen
# ===========================================================================


async def test_active_read_ignores_uncommitted_rows(store: SqliteSegmentStore):
    # Committete Basis-Zeile im aktiven Segment.
    await store.append([_event(1, "2026-01-01T00:00:01.000Z")])

    # Simuliert einen nebenlaeufigen Append MITTEN in der Transaktion: eine Zeile
    # wird ueber die Writer-Connection eingefuegt, aber NICHT committet.
    gid = await store.manifest.reserve_global_event_ids(1)
    await store._insert_event(store._active_conn, gid, _event(999, "2026-01-01T00:00:02.000Z"))

    # Eine Query, die das aktive Segment einschliesst, darf die uncommittete Zeile
    # NICHT sehen (read-only Snapshot, WAL). Ohne Fix laeuft der Read ueber
    # _active_conn und sieht die uncommittete Zeile.
    rows = await store.query(StoreQuery(limit=100))
    values = [r["new_value"] for r in rows]
    assert 999 not in values, "uncommittete Zeile darf im aktiven Read nicht sichtbar sein"

    # Rollback der uncommitteten Zeile (simuliert fehlgeschlagenen Append).
    await store._active_conn.rollback()

    # Erneute Query: die committete Basis-Zeile bleibt normal sichtbar.
    rows_after = await store.query(StoreQuery(limit=100))
    values_after = [r["new_value"] for r in rows_after]
    assert 1 in values_after
    assert 999 not in values_after


async def test_active_read_sees_committed_rows(store: SqliteSegmentStore):
    # Gegentest: committete Daten im aktiven Segment sind ueber die read-only
    # Connection normal sichtbar (WAL sieht alle committeten Transaktionen).
    await store.append([_event(10, "2026-01-01T00:00:01.000Z")])
    await store.append([_event(20, "2026-01-01T00:00:02.000Z")])

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == [10, 20]
