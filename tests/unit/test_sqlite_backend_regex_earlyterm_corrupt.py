"""Codex-R21-P2-Findings am segmentierten SQLite-Store (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, TDD-first – er reproduziert den
Bug ohne Fix und wird durch den Fix grün:

1. (Exact-Count-Alternation) Das safe-regex-Gate lehnt auch quantifizierte
   Alternationen mit EXAKTER Zählung (``(a|aa){30}b``) ab – nicht nur ``*``/``+``/
   ``{m,n}``. Ambiguous alternation mit ``{m}`` löst dasselbe katastrophale
   Backtracking aus und muss VOR der Ausführung verworfen werden. Gegentest:
   bislang erlaubte lineare Muster bleiben erlaubt.
2. (Early-Termination vor Legacy-Tail) Eine latest-N-``id desc``-Query OHNE
   Zeitfenster, die bereits vollständig aus positiven v2-Zeilen befüllbar ist,
   terminiert früh und liest den attached Legacy-Tail (bzw. ältere v2-Segmente)
   NICHT – auch wenn ein read-only ``legacy``-Segment attached ist. Gegentest:
   reicht die Seite bis in den negativen Legacy-Bereich, wird Legacy korrekt
   einbezogen und die Ordnung (positive vor negative gid) stimmt.
3. (Schemaloses closed Segment) Ein retained, geschlossenes v2-Segment, dessen
   Datei auf 0 Bytes truncated ist (read-only-Open gelingt, SELECT wirft
   ``no such table: ringbuffer``), wird beim Read quarantäniert statt einen 500
   zu werfen; die übrigen Segmente liefern normal weiter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import (
    SEGMENT_STATUS_CLOSED,
    SEGMENT_STATUS_QUARANTINED,
)
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore, _assert_safe_regex


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
# (1) Exact-Count-Alternation (``{m}``) wird abgelehnt (:247)
# ---------------------------------------------------------------------------


def test_safe_regex_rejects_exact_count_alternation():
    # Kern des Findings: ``{30}`` (exakte Zählung, KEIN Komma) nach einer
    # Alternations-Gruppe umging das Gate bislang und ließ ``re.search`` gegen einen
    # langen Wert sekundenlang backtracken (Worker/GIL blockiert).
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex("(a|aa){30}b")
    # Auch ``{m,}`` und ``{m,n}`` nach einer Alternation müssen weiterhin abgelehnt werden.
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex("(a|b){5,}")
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex("(x|y){2,3}")


def test_safe_regex_still_allows_benign_counted_patterns():
    # Gegentest: lineare/benigne Muster – auch mit counted quantifiers OHNE
    # vorausgehende Alternation – bleiben erlaubt.
    _assert_safe_regex("foo.*bar")
    _assert_safe_regex("[ab]+")
    _assert_safe_regex("(abc)+")
    _assert_safe_regex("a{3}")
    _assert_safe_regex("x{2,5}")


async def test_exact_count_alternation_pushdown_is_rejected_fast(store: SqliteSegmentStore):
    import asyncio
    import time

    long_nomatch = "a" * 28 + "!"
    await store.append([_event(long_nomatch, "2026-01-01T00:00:00.000Z")])
    q = StoreQuery(
        limit=10,
        candidate_cap=100,
        value_filters=[{"operator": "regex", "field": "new_value", "pattern": "(a|aa){30}b"}],
    )
    # Muster wird VOR der Ausführung als unsafe abgelehnt (schnell, ohne Worker-Block).
    start = time.time()
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        await asyncio.wait_for(store.query(q), timeout=3.0)
    assert time.time() - start < 3.0, "Regex-Gate hätte VOR der Ausführung ablehnen müssen"


# ---------------------------------------------------------------------------
# (2) Early-Termination im positiven v2-Prefix VOR dem Legacy-Tail (:980)
# ---------------------------------------------------------------------------


async def _build_legacy_db(path: Path, values: list[int]) -> None:
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, value in enumerate(values):
            await rb.record(
                ts=f"2025-01-01T00:00:0{i}.000Z",
                datapoint_id="dp-legacy",
                topic="dp/dp-legacy/value",
                old_value=None,
                new_value=value,
                source_adapter="legacy",
                quality="good",
                metadata={"datapoint": {"tags": ["legacy"]}},
            )
    finally:
        await rb.stop()


def _spy_reads(store: SqliteSegmentStore) -> list[Any]:
    """Zeichnet auf, welche Segmente ``_read_segment_rows`` tatsächlich öffnet."""
    opened: list[Any] = []
    original = store._read_segment_rows

    async def wrapper(segment: Any, query: StoreQuery) -> Any:
        opened.append(segment)
        return await original(segment, query)

    store._read_segment_rows = wrapper  # type: ignore[method-assign]
    return opened


async def test_latest_page_early_terminates_before_attached_legacy(store: SqliteSegmentStore, tmp_path: Path):
    # Attached read-only Legacy-Tail (negative gids, ÄLTER) + mehrere positive
    # v2-Segmente (neuer). Eine latest-N-``id desc``-Query OHNE Zeitfenster, die
    # allein aus positiven v2-Zeilen befüllbar ist, darf den Legacy-Tail NICHT
    # anfassen (bounded latest-page-Read).
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [100, 200, 300])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # Drei separate positive v2-Segmente (je 1 Zeile), neuestes zuletzt geschrieben.
    await store.append([_event(1, "2026-06-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-06-01T00:00:01.000Z")])
    await store.rotate()
    await store.append([_event(3, "2026-06-01T00:00:02.000Z")])

    opened = _spy_reads(store)
    rows = await store.query(StoreQuery(limit=2, sort_field="id", sort_order="desc"))
    # Korrektes Ergebnis: die zwei neuesten positiven v2-Werte.
    assert [r["new_value"] for r in rows] == [3, 2]

    # Kern: das attached Legacy-Segment wird NICHT geöffnet (bounded), weil die Seite
    # bereits aus positiven v2-Zeilen voll ist.
    opened_legacy = [s for s in opened if s.status == "legacy"]
    assert opened_legacy == [], "Legacy-Tail darf bei voll gefüllter latest-page nicht gelesen werden"


async def test_latest_page_descends_into_legacy_when_positive_prefix_short(store: SqliteSegmentStore, tmp_path: Path):
    # Gegentest: reicht die angeforderte Seite über die positiven v2-Zeilen hinaus,
    # MUSS der Legacy-Bereich einbezogen werden – Ordnung positive vor negative gid.
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [100, 200])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    await store.append([_event(1, "2026-06-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-06-01T00:00:01.000Z")])

    opened = _spy_reads(store)
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    # Zwei positive v2-Werte (neuer) VOR den beiden Legacy-Werten (älter). Legacy ist
    # ebenfalls newest-first (200 hat den späteren ts als 100).
    assert [r["new_value"] for r in rows] == [2, 1, 200, 100]
    gids = [r["global_event_id"] for r in rows]
    assert gids == sorted(gids, reverse=True)

    opened_legacy = [s for s in opened if s.status == "legacy"]
    assert opened_legacy, "Legacy muss einbezogen werden, wenn die Seite über die positiven v2-Zeilen hinausreicht"


# ---------------------------------------------------------------------------
# (3) Schemaloses closed Segment beim Read quarantänisieren (:1057)
# ---------------------------------------------------------------------------


async def test_truncated_closed_segment_is_quarantined_on_read(store: SqliteSegmentStore, tmp_path: Path):
    # Ein älteres v2-Segment schließen (rotate) und seine Datei auf 0 Bytes truncaten.
    # Der read-only-Open gelingt (gültige, aber schemalose DB); das SELECT würfe
    # ``no such table: ringbuffer`` → ohne Fix ein 500 für JEDE Query, die es berührt.
    await store.append([_event(1, "2026-06-01T00:00:00.000Z")])
    active_before = await store.manifest.get_active_segment()
    await store.rotate()  # das eben befüllte Segment ist jetzt closed
    await store.append([_event(2, "2026-06-01T00:00:01.000Z")])

    closed = {s.filename: s for s in await store.manifest.list_segments()}[active_before.filename]
    assert closed.status == SEGMENT_STATUS_CLOSED
    assert closed.row_count > 0
    seg_path = tmp_path / "root" / "segments" / closed.filename
    with open(seg_path, "wb") as fh:  # noqa: ASYNC230 -- test setup, no concurrent async work in flight
        fh.truncate(0)
    assert seg_path.stat().st_size == 0

    # Query, die BEIDE Segmente berühren würde (kein Early-Terminate: limit deckt beide):
    # kein 500 / ``no such table``, sondern das kaputte Segment wird isoliert.
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in rows] == [2], "aktives Segment liefert normal, kaputtes wird übersprungen"

    segments = {s.filename: s for s in await store.manifest.list_segments()}
    assert segments[closed.filename].status == SEGMENT_STATUS_QUARANTINED
