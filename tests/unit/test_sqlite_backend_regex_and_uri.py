"""Codex-[P2]-Findings Runde 25 am RingBuffer-SQLite-Backend (#919, PR #951).

Zwei unabhaengige Follow-up-Findings, je TDD-first (rot ohne Fix, gruen mit Fix):

Finding 1 (``_assert_safe_regex`` / :265): der nested-quantifier-Detektor deckte
nur inneres ``+``/``*`` mit aeusserem ``+``/``*`` ab. Muster wie ``(a?){30}a{30}``,
``(a?)+`` oder ``(a+){30}`` passierten beide Guards und konnten den synchronen
SQLite-/Legacy-Callback (GIL) ueber Sekunden backtracken lassen. Fix: Detektor um
(a) inneres ``?`` in gruppierten Wiederholungen und (b) counted aeussere
Quantifier (``{m}``/``{m,}``/``{m,n}``) erweitern. Benigne Muster bleiben erlaubt.

Finding 2 (``_connection_for_read`` / :1142 u. a.): der read-only Open interpolierte
den rohen Filesystem-Pfad in ``file:...?mode=ro``. Enthaelt das RingBuffer-Verzeichnis
SQLite-URI-Metazeichen (``?``, ``#``), parste SQLite einen Teil des Pfads als
Query/Fragment → falscher DB-Pfad → 500er oder fehlerhafte ``no such table``-
Quarantaene. Fix: URI mit Prozent-Encoding via ``Path.as_uri()`` bauen, bevor
``mode=ro`` als echter Query-Parameter angehaengt wird.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import _assert_safe_regex


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


# ===========================================================================
# Finding 1: optionale nested quantifiers + counted aeussere Quantifier
# ===========================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "(a?){30}a{30}",  # das im Finding genannte katastrophale Muster
        "(a?){30}",  # inneres ``?`` + counted aeusserer Quantifier
        "(a?)+",  # inneres ``?`` + ``+``
        "(a?)*",  # inneres ``?`` + ``*``
        "(a+){30}",  # inneres ``+`` + counted aeusserer Quantifier
        "(a*){5,}",  # inneres ``*`` + counted-open aeusserer Quantifier
        "(a+){2,5}",  # inneres ``+`` + counted-range aeusserer Quantifier
    ],
)
def test_assert_safe_regex_rejects_nested_quantifiers(pattern: str):
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "(abc)+",  # gruppierte Wiederholung OHNE inneren Quantifier
        "a?",  # einzelnes optionales Zeichen
        "a{3}",  # counted Quantifier ohne Gruppe
        "x{2,5}",  # counted-range Quantifier ohne Gruppe
        "foo.*bar",  # ``.*`` ist linear, kein nested quantifier
        "(abc){2,5}",  # counted Gruppe OHNE inneren Quantifier
    ],
)
def test_assert_safe_regex_allows_benign_patterns(pattern: str):
    _assert_safe_regex(pattern)


async def test_regex_value_filter_rejects_nested_quantifier_query(tmp_path: Path):
    # End-to-end ueber den Query-Pfad: ein Value-Filter mit ``(a?){30}a{30}`` muss
    # VOR jeglicher Ausfuehrung als ValueError abgewiesen werden (422-tauglich).
    from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event("aaaa", "2024-01-01T00:00:00.000Z")])
        # Bounded query (candidate_cap), sonst wird der regex-Operator schon vor dem
        # safe-regex-Gate wegen fehlender Bindung abgewiesen.
        query = StoreQuery(
            limit=10,
            candidate_cap=100,
            value_filters=[{"field": "new_value", "operator": "regex", "pattern": "(a?){30}a{30}"}],
        )
        with pytest.raises(ValueError, match="unsafe regex pattern"):
            await store.query(query)
    finally:
        await store.close()


# ===========================================================================
# Finding 2: URI-Metazeichen im RingBuffer-Verzeichnis-Pfad
# ===========================================================================


@pytest.mark.parametrize("subdir", ["weird?dir#x", "with space", "pct%dir"])
async def test_reads_work_in_dir_with_uri_metacharacters(tmp_path: Path, subdir: str):
    # Store-Root in einem Verzeichnis mit SQLite-URI-Metazeichen. Ohne Fix parst
    # SQLite ``?``/``#`` im rohen ``file:``-Pfad als Query/Fragment → falscher
    # DB-Pfad → 500 (aktives Segment) oder fehlerhafte ``no such table``-Quarantaene
    # (geschlossenes Segment).
    from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

    root = tmp_path / subdir / "root"
    root.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteSegmentStore(root)
    await store.open()
    try:
        # Geschlossenes Segment (rotiert) + aktives Segment mit je einer Zeile.
        await store.append([_event("closed", "2024-01-01T00:00:00.000Z")])
        await store.rotate()
        await store.append([_event("active", "2024-01-02T00:00:00.000Z")])

        rows = await store.query(StoreQuery(limit=10))
        values = {r["new_value"] for r in rows}
        assert values == {"closed", "active"}, f"expected beide Segmente lesbar, got {values}"
    finally:
        await store.close()


async def test_reads_work_in_normal_dir_unchanged(tmp_path: Path):
    # Gegentest: normaler Pfad (keine Metazeichen) bleibt unveraendert lesbar.
    from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event("closed", "2024-01-01T00:00:00.000Z")])
        await store.rotate()
        await store.append([_event("active", "2024-01-02T00:00:00.000Z")])
        rows = await store.query(StoreQuery(limit=10))
        assert {r["new_value"] for r in rows} == {"closed", "active"}
    finally:
        await store.close()


async def test_integrity_probe_finds_segment_in_uri_metachar_dir(tmp_path: Path):
    # Der read-only Integrity-Probe-Pfad (``check_segment_integrity``) baut denselben
    # ``file:``-URI. In einem Metazeichen-Verzeichnis darf ein gesundes closed-
    # Segment NICHT faelschlich als korrupt quarantaeniert werden.
    from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

    root = tmp_path / "weird?dir#x" / "root"
    root.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteSegmentStore(root)
    await store.open()
    try:
        await store.append([_event("closed", "2024-01-01T00:00:00.000Z")])
        closed = await store.manifest.get_active_segment()
        await store.rotate()
        ok = await store.check_segment_integrity(closed.segment_id)
        assert ok is True, "gesundes closed-Segment im Metazeichen-Pfad wurde faelschlich als korrupt gewertet"
    finally:
        await store.close()
