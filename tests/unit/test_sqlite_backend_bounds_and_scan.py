"""Codex-R20-P2-Findings am segmentierten SQLite-Store (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, TDD-first – er reproduziert den
Bug ohne Fix und wird durch den Fix grün:

1. (Free-Text-Cap) Eine live segmentierte Query mit Freitext-``q`` OHNE Zeitfenster,
   deren Text in den v2-Daten NICHT vorkommt, wird gedeckelt gescannt (candidate_cap
   greift wie beim Guarded-Filter/Legacy-``q``), statt jedes selektierte v2-Segment
   voll zu scannen. Ergebnis-Parität zum Legacy-``q``-Verhalten.
2. (Empty-Pending-Checkpoint) Eine ``checkpoint_pending``-Datei, die auf 0 Bytes
   truncated ist, obwohl das Manifest ``row_count > 0`` erwartet, wird als verloren/
   korrupt behandelt (quarantäniert), NICHT sauber ``closed`` + resize 0 gemeldet;
   spätere Reads treffen dann keine leere DB mit fehlender ``ringbuffer``-Tabelle.
3. (Fractional-Between) Ein ``between`` mit EINER unsicheren Integer-Grenze und der
   ANDEREN fraktionalen Grenze truncatet die fraktionale Grenze NICHT mehr per
   ``int(...)``; nur die unsichere Integer-Seite läuft über den exakten Callback,
   die fraktionale Seite bleibt fraktional (Parität zum Legacy/JSON-Vergleich).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import (
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_QUARANTINED,
)
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore, _legacy_row_matches_filters


def _event(value: Any, ts: str, *, dp: str = "dp-1", src: str = "api", old: Any = None) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=old,
        new_value=value,
        source_adapter=src,
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


# ---------------------------------------------------------------------------
# (1) Freitext-``q`` ohne Zeitfenster bleibt gedeckelt (:1603)
# ---------------------------------------------------------------------------


def _ts(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


async def test_free_text_q_without_window_is_capped(store: SqliteSegmentStore):
    # Mehrere Zeilen, deren datapoint_id/source_adapter den Suchtext NIE enthalten.
    # Ein leading-wildcard LIKE auf diesen Spalten kann keinen Index nutzen; ohne
    # Cap müsste SQLite jede Zeile berühren, nur um zu beweisen, dass keine matcht.
    await store.append([_event(i, _ts(i), dp=f"aaa-{i}", src="api") for i in range(60)])

    cap = 5
    q = StoreQuery(limit=10, candidate_cap=cap, q="zzz")

    # Der generierte Segment-SQL muss den teuren Freitext-LIKE auf eine gedeckelte
    # Kandidaten-Subquery legen (LIMIT ? mit cap), NICHT inline über alle Zeilen.
    sql, params = store._build_segment_sql(q)
    assert "LIKE" in sql
    assert cap in params, "candidate_cap muss als LIMIT der inneren Subquery erscheinen"
    # Der LIKE darf nicht im inneren (ungedeckelten) WHERE stehen, sondern muss die
    # gedeckelte Kandidatenmenge umschließen: das kapselnde SELECT ... FROM (...) muss da sein.
    assert "FROM (SELECT" in sql, "Freitext-q muss um eine gedeckelte Subquery gelegt werden"

    # Ergebnis-Parität: Text kommt nicht vor → leeres Ergebnis, wie Legacy-q.
    rows = await store.query(q)
    assert rows == []


async def test_free_text_q_matches_within_cap(store: SqliteSegmentStore):
    # Gegenprobe: ein tatsächlich vorkommender Text in den neuesten cap Zeilen wird
    # weiterhin gefunden (der Cap deckelt nur, verwirft keine echten Treffer im Fenster).
    await store.append([_event(1, _ts(1), dp="target-dp", src="api")])
    q = StoreQuery(limit=10, candidate_cap=100, q="target")
    rows = await store.query(q)
    assert [r["datapoint_id"] for r in rows] == ["target-dp"]
    for r in rows:
        # Parität zum Legacy-Row-Match: der q-Treffer entspricht dem Legacy-Verhalten.
        assert "target" in r["datapoint_id"]


async def test_free_text_q_with_window_stays_inline(store: SqliteSegmentStore):
    # Mit beidseitigem Zeitfenster bindet bereits das WHERE den Scan – dann darf der
    # Freitext-LIKE inline bleiben (keine unnötige Subquery-Kapselung).
    await store.append([_event(i, _ts(i), dp=f"aaa-{i}") for i in range(5)])
    q = StoreQuery(limit=10, q="zzz", from_ts=0.0, to_ts=10_000_000_000.0)
    sql, _params = store._build_segment_sql(q)
    assert "FROM (SELECT" not in sql, "Mit Zeitfenster keine gedeckelte Subquery nötig"


# ---------------------------------------------------------------------------
# (2) Leeres ``checkpoint_pending``-Segment als verloren behandeln (:2220)
# ---------------------------------------------------------------------------


async def _seed_two_segments_second_pending(root: Path) -> tuple[str, int]:
    """Legt zwei Segmente an; rotiert, markiert das erste als checkpoint_pending.

    Liefert (Dateiname des pending Segments, dessen segment_id).
    """
    s = SqliteSegmentStore(root)
    await s.open()
    await s.append([_event(1, _ts(1))])
    first = await s.manifest.get_active_segment()
    assert first.row_count > 0
    # rotate: das erste Segment wird geschlossen, ein neues aktives eröffnet.
    await s.rotate()
    # Das erste (jetzt closed) Segment künstlich als checkpoint_pending markieren.
    await s.manifest.mark_checkpoint_pending(first.segment_id)
    pending = await s.manifest.get_segment(first.segment_id)
    assert pending.status == SEGMENT_STATUS_CHECKPOINT_PENDING
    assert pending.row_count > 0
    filename = pending.filename
    seg_id = pending.segment_id
    await s.close()
    return filename, seg_id


async def test_empty_pending_checkpoint_is_treated_as_lost(tmp_path: Path):
    root = tmp_path / "root"
    filename, seg_id = await _seed_two_segments_second_pending(root)
    seg_path = root / "segments" / filename
    # pending-Datei auf 0 Bytes truncaten (abgeschnittener Write / Crash). Ein
    # schreibendes connect + wal_checkpoint(TRUNCATE) meldet auf der leeren DB busy=0,
    # sodass der Läufer das Segment sonst faelschlich als sauber closed + resize 0 markiert.
    with open(seg_path, "wb") as fh:
        fh.truncate(0)
    assert seg_path.stat().st_size == 0

    s = SqliteSegmentStore(root)
    await s.open()
    try:
        recovered = await s.run_pending_checkpoints()
        # Das leere pending-Segment darf NICHT als recovered (sauber closed) gelten.
        assert recovered == 0
        seg = await s.manifest.get_segment(seg_id)
        # Es wird als verloren/korrupt quarantäniert, nicht sauber closed + auf 0 resized.
        assert seg.status == SEGMENT_STATUS_QUARANTINED
        # Ein Read darf nicht mit "no such table: ringbuffer" scheitern – das
        # quarantänierte Segment wird sauber übersprungen.
        rows = await s.query(StoreQuery(limit=10))
        assert isinstance(rows, list)
    finally:
        await s.close()


async def test_nonempty_pending_checkpoint_is_recovered(tmp_path: Path):
    # Gegenprobe: ein intaktes, befülltes pending-Segment wird korrekt gecheckpointet
    # und wieder als sauber closed markiert (kein Quarantäne-Fehlalarm).
    root = tmp_path / "root"
    filename, seg_id = await _seed_two_segments_second_pending(root)

    s = SqliteSegmentStore(root)
    await s.open()
    try:
        recovered = await s.run_pending_checkpoints()
        assert recovered == 1
        seg = await s.manifest.get_segment(seg_id)
        assert seg.status != SEGMENT_STATUS_QUARANTINED
        assert seg.status != SEGMENT_STATUS_CHECKPOINT_PENDING
        # Die alte Zeile bleibt lesbar.
        rows = await s.query(StoreQuery(limit=10))
        assert [r["new_value"] for r in rows] == [1]
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# (3) Fraktionale Grenzen im exact-between-Fallback erhalten (:1708)
# ---------------------------------------------------------------------------

_BIG = 2**60  # unsicherer Integer (>= 2**53), zwingt den exact-between-Zweig


async def test_between_fractional_lower_unsafe_upper_preserves_fraction(store: SqliteSegmentStore):
    # lower=1.5 (fraktional, sicher), upper=2**60 (unsicherer Integer).
    # Ohne Fix truncatet der exact-Zweig BEIDE Grenzen: >= int(1.5)=1, sodass 1.2
    # faelschlich matcht. Mit Fix bleibt die untere Grenze fraktional (>= 1.5).
    await store.append(
        [
            _event(1.2, _ts(1)),  # < 1.5 → darf NICHT matchen
            _event(2.0, _ts(2)),  # in [1.5, 2**60] → matcht
            _event(_BIG, _ts(3)),  # obere Grenze exakt → matcht
        ]
    )
    spec = {"operator": "between", "field": "new_value", "lower": 1.5, "upper": _BIG}
    rows = await store.query(StoreQuery(limit=10, value_filters=[spec]))
    values = sorted(r["new_value"] for r in rows)
    assert 1.2 not in values, "1.2 liegt unter der fraktionalen unteren Grenze 1.5"
    assert 2.0 in values
    assert _BIG in values
    # Parität zum Legacy/JSON-Vergleich.
    for v in (1.2, 2.0, _BIG):
        legacy = _legacy_row_matches_filters({"new_value": v}, [spec])
        assert legacy == (v in values)


async def test_between_unsafe_lower_fractional_upper_preserves_fraction(store: SqliteSegmentStore):
    # Gegentest: lower=-2**60 (unsicherer Integer), upper=2.5 (fraktional, sicher).
    # Ohne Fix wird upper auf int(2.5)=2 truncatet, sodass 2.4 faelschlich AUSGESCHLOSSEN
    # wird. Mit Fix bleibt die obere Grenze fraktional (<= 2.5).
    await store.append(
        [
            _event(2.4, _ts(1)),  # <= 2.5 → matcht
            _event(2.6, _ts(2)),  # > 2.5 → darf NICHT matchen
            _event(-_BIG, _ts(3)),  # untere Grenze exakt → matcht
        ]
    )
    spec = {"operator": "between", "field": "new_value", "lower": -_BIG, "upper": 2.5}
    rows = await store.query(StoreQuery(limit=10, value_filters=[spec]))
    values = sorted(r["new_value"] for r in rows)
    assert 2.4 in values, "2.4 liegt unter der fraktionalen oberen Grenze 2.5"
    assert 2.6 not in values
    assert -_BIG in values
    for v in (2.4, 2.6, -_BIG):
        legacy = _legacy_row_matches_filters({"new_value": v}, [spec])
        assert legacy == (v in values)


async def test_between_both_unsafe_int_still_exact(store: SqliteSegmentStore):
    # Regression: sind BEIDE Grenzen unsichere Integer, bleibt der bestehende
    # exakte int-Vergleich korrekt (kein Kollaps auf REAL).
    await store.append(
        [
            _event(_BIG, _ts(1)),
            _event(_BIG + 5, _ts(2)),
            _event(2 * _BIG, _ts(3)),  # ueber der oberen Grenze
        ]
    )
    spec = {"operator": "between", "field": "new_value", "lower": _BIG, "upper": _BIG + 5}
    rows = await store.query(StoreQuery(limit=10, value_filters=[spec]))
    values = sorted(r["new_value"] for r in rows)
    assert values == [_BIG, _BIG + 5]
