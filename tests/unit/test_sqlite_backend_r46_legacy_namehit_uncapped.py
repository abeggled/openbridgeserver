"""Codex-Runde-46 [P2]: Legacy-Name-Treffer aus dem Kandidaten-Cap herauslösen (#951, :1686).

Finding „Uncap legacy name-hit matches": das v2-Name-Hit-Widening (Runde 36, :2262)
existiert, aber der attached READ-ONLY-Legacy-Pfad überspringt den SQL-
``dp_ids_by_name``-``IN``-Arm, sobald ``q`` gesetzt ist, und prüft dann nur den
EINEN gedeckelten Python-Batch. Eine per NAME gematchte Legacy-Zeile, deren rowids
älter als die neuesten ``candidate_cap`` Roh-Zeilen sind und deren id/source ``q``
nicht enthält, fiel damit aus dem Ergebnis – obwohl der ``IN``-Arm über
``datapoint_id`` indizierbar ist (Parität zur un-capped Legacy-OR-Query).

Fix analog v2: der ``IN``-Arm läuft als EIGENER, separat gedeckelter Fetch (eigener
``candidate_cap`` NUR über die Namens-Treffer statt Konkurrenz um die globalen
Cap-Slots) und wird dedupliziert in die Kandidatenmenge gemerged. Die
index-untauglichen ``LIKE``-Arme (id/source) bleiben unverändert gedeckelt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


async def _build_legacy_db(path: Path, rows: list[tuple[str, int]]) -> None:
    """Befüllt eine echte Legacy-``ringbuffer.db`` im ALTEN Format über RingBuffer."""
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, (dp, value) in enumerate(rows):
            await rb.record(
                ts=f"2025-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z",
                datapoint_id=dp,
                topic=f"dp/{dp}/value",
                old_value=None,
                new_value=value,
                source_adapter="api",
                quality="good",
                metadata={},
            )
    finally:
        await rb.stop()


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _attach_legacy(store: SqliteSegmentStore, path: Path) -> None:
    migrator = LegacyMigrator(store, path)
    classification = migrator.classify()
    assert classification is not None
    await migrator.attach_readonly(classification)


async def test_legacy_name_hit_older_than_cap_is_returned(store: SqliteSegmentStore, tmp_path: Path):
    """Unwindowed Monitor-Query auf attached Legacy: Name-Treffer jenseits des Caps.

    ``dp-target`` liegt als ÄLTESTE Zeile (rowid 1), gefolgt von 30 neueren
    ``noise``-Zeilen (matchen ``q='searchterm'`` per LIKE nicht). Mit
    ``candidate_cap=5`` fällt ``dp-target`` aus den neuesten 5 Roh-Zeilen. Weil der
    ``dp_ids_by_name``-``IN``-Arm indizierbar ist, muss der Treffer trotzdem
    geliefert werden – wie im v2-Pfad (Runde 36) und in der un-capped
    ``segmented=False``-Referenz.
    """
    legacy = tmp_path / "obs_ringbuffer.db"
    rows = [("dp-target", 111)] + [(f"noise-{i}", i) for i in range(30)]
    await _build_legacy_db(legacy, rows)
    await _attach_legacy(store, legacy)

    query = StoreQuery(q="searchterm", dp_ids_by_name=["dp-target"], candidate_cap=5, limit=50)
    got = await store.query(query)
    assert [r["new_value"] for r in got] == [111], "per NAME gematchte Legacy-Zeile jenseits des Caps fehlt"


async def test_legacy_name_hit_respects_other_filters(store: SqliteSegmentStore, tmp_path: Path):
    """Der Name-Arm umgeht NUR den Cap – Value-Filter gelten weiterhin."""
    legacy = tmp_path / "obs_ringbuffer.db"
    rows = [("dp-target", 111), ("dp-target", 5)] + [(f"noise-{i}", i) for i in range(30)]
    await _build_legacy_db(legacy, rows)
    await _attach_legacy(store, legacy)

    query = StoreQuery(
        q="searchterm",
        dp_ids_by_name=["dp-target"],
        candidate_cap=5,
        limit=50,
        value_filters=[{"field": "new_value", "operator": "gte", "value": 100}],
    )
    got = await store.query(query)
    assert [r["new_value"] for r in got] == [111], "Value-Filter muss auch fuer Name-Arm-Treffer gelten"


async def test_legacy_leading_wildcard_like_stays_capped(store: SqliteSegmentStore, tmp_path: Path):
    """Gegentest: ein reiner ``q``-LIKE-Treffer jenseits des Caps bleibt bewusst gedeckelt.

    Ohne ``dp_ids_by_name`` gibt es keinen indizierbaren Arm; der bounded
    LIKE-Scan über die gedeckelte Kandidatenmenge ist das dokumentierte Verhalten
    (kein unbounded Full-Scan über eine 20–30-GB-Legacy-Datei).
    """
    legacy = tmp_path / "obs_ringbuffer.db"
    rows = [("oldmatch-dp", 111)] + [(f"noise-{i}", i) for i in range(30)]
    await _build_legacy_db(legacy, rows)
    await _attach_legacy(store, legacy)

    query = StoreQuery(q="oldmatch", candidate_cap=5, limit=50)
    got = await store.query(query)
    assert got == [], "LIKE-Arm ohne Namens-IN bleibt gedeckelt (dokumentierte Grenze)"


async def test_legacy_no_q_keeps_inline_in_arm(store: SqliteSegmentStore, tmp_path: Path):
    """Regression: OHNE ``q`` bleibt der ``dp_ids_by_name``-``IN``-Arm inline im SQL."""
    legacy = tmp_path / "obs_ringbuffer.db"
    rows = [("dp-target", 111)] + [(f"noise-{i}", i) for i in range(30)]
    await _build_legacy_db(legacy, rows)
    await _attach_legacy(store, legacy)

    query = StoreQuery(dp_ids_by_name=["dp-target"], candidate_cap=5, limit=50)
    got = await store.query(query)
    assert [r["new_value"] for r in got] == [111]
