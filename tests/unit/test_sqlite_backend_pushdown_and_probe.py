"""Codex-P2-Findings am segmentierten SQLite-Store (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, TDD-first — er reproduziert den
Bug ohne Fix und wird durch den Fix grün:

1. (Probe) Ein aktives Segment, dessen Datei auf 0 Bytes truncated (oder durch eine
   frische leere SQLite-DB ersetzt) wurde, während das Manifest ``row_count > 0``
   erwartet, wird als verloren/korrupt behandelt (quarantäniert + frisches Segment),
   statt still als leer akzeptiert zu werden.
2. (Pushdown-Integer) Ein Integer-Datapoint außerhalb des IEEE-754-exakten Bereichs
   (|v| > 2**53) kollabiert bei ``eq``/Range-Pushdown NICHT auf benachbarte 64-bit-
   Werte; das Pushdown-Ergebnis stimmt mit dem Legacy/JSON-Vergleich überein.
3. (Regex-Timeout) Ein katastrophales Regex-Pattern (ambiguous alternation, das der
   nested-quantifier-Check nicht verwirft) gegen einen langen Textwert bleibt
   gebounded (Timeout/Reject) statt den Worker unbegrenzt zu blockieren — Parität
   zum Legacy-Timeout-Verhalten.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_QUARANTINED
from obs.ringbuffer.store.sqlite_backend import _SEGMENT_SCHEMA, SqliteSegmentStore, _legacy_row_matches_filters


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


# ---------------------------------------------------------------------------
# (1) Leere/truncatete aktive Segment-Datei als verloren behandeln (:658)
# ---------------------------------------------------------------------------


async def _seed_and_close(root: Path, value: Any = 42) -> str:
    """Legt einen Store an, schreibt eine Zeile, gibt den aktiven Dateinamen zurück."""
    s = SqliteSegmentStore(root)
    await s.open()
    await s.append([_event(value, "2026-01-01T00:00:00.000Z")])
    active = await s.manifest.get_active_segment()
    filename = active.filename
    assert active.row_count > 0
    await s.close()
    return filename


async def test_truncated_active_segment_is_treated_as_lost(tmp_path: Path):
    # Aktives Segment mit row_count>0 anlegen, dann die Datei auf 0 Bytes truncaten
    # (simuliert einen abgeschnittenen Write / Crash). Ein schreibendes Re-Open legt
    # sonst still das Schema neu an und akzeptiert das Segment als leer.
    root = tmp_path / "root"
    filename = await _seed_and_close(root)
    seg_path = root / "segments" / filename
    with open(seg_path, "wb") as fh:  # noqa: PTH123 – bewusst truncate auf 0 Bytes
        fh.truncate(0)
    assert seg_path.stat().st_size == 0

    s = SqliteSegmentStore(root)
    await s.open()
    try:
        # Das alte (jetzt leere) Segment darf NICHT mehr aktiv sein: es wird
        # quarantäniert und ein frisches aktives Segment eröffnet.
        segments = await s.manifest.list_segments()
        by_name = {seg.filename: seg for seg in segments}
        old = by_name[filename]
        assert old.status == SEGMENT_STATUS_QUARANTINED
        active = await s.manifest.get_active_segment()
        assert active is not None
        assert active.filename != filename
        assert active.status == SEGMENT_STATUS_ACTIVE
    finally:
        await s.close()


async def test_empty_replacement_db_active_segment_is_treated_as_lost(tmp_path: Path):
    # Statt Truncate: die Datei durch eine frische, gültige aber LEERE SQLite-DB
    # ersetzen (kein Corruption-Wurf). row_count>0 im Manifest, aber 0 Zeilen im File.
    root = tmp_path / "root"
    filename = await _seed_and_close(root)
    seg_path = root / "segments" / filename
    seg_path.unlink()
    # gültige, korrekt geschemate, aber LEERE Segment-DB an derselben Stelle: ein
    # schreibendes Re-Open wirft KEINE Korruption (Schema passt), akzeptierte das
    # Segment aber still als leer, obwohl das Manifest Zeilen erwartet.
    conn = sqlite3.connect(str(seg_path))
    conn.executescript(_SEGMENT_SCHEMA)
    conn.commit()
    conn.close()

    s = SqliteSegmentStore(root)
    await s.open()
    try:
        segments = await s.manifest.list_segments()
        old = {seg.filename: seg for seg in segments}[filename]
        assert old.status == SEGMENT_STATUS_QUARANTINED
        active = await s.manifest.get_active_segment()
        assert active is not None and active.filename != filename
    finally:
        await s.close()


async def test_nonempty_active_segment_survives_open(tmp_path: Path):
    # Gegenprobe: ein intaktes, befülltes aktives Segment bleibt unverändert aktiv
    # und behält seine Zeile über Re-Open hinweg.
    root = tmp_path / "root"
    filename = await _seed_and_close(root)

    s = SqliteSegmentStore(root)
    await s.open()
    try:
        active = await s.manifest.get_active_segment()
        assert active.filename == filename
        assert active.status == SEGMENT_STATUS_ACTIVE
        rows = await s.query(StoreQuery(limit=10))
        assert [r["new_value"] for r in rows] == [42]
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# (2) Exakte Integer-Werte in den Pushdown-Spalten (:332)
# ---------------------------------------------------------------------------

# Zwei benachbarte Integer an der IEEE-754-Grenze: als float sind sie NICHT
# unterscheidbar (float(2**53) == float(2**53+1) == 2**53.0, weil 2**53+1 nicht
# exakt als double darstellbar ist und auf 2**53 gerundet wird). Der REAL-Pushdown
# kollabiert sie ohne Fix; der Legacy/JSON-Vergleich behandelt sie exakt.
_BIG_A = 2**53
_BIG_B = 2**53 + 1


def test_big_ints_collapse_under_float():
    # Vorbedingung des Findings: die beiden Werte sind als float ununterscheidbar.
    assert float(_BIG_A) == float(_BIG_B)
    assert _BIG_A != _BIG_B


async def test_pushdown_eq_big_int_matches_only_exact_value(store: SqliteSegmentStore):
    await store.append(
        [
            _event(_BIG_A, "2026-01-01T00:00:00.000Z"),
            _event(_BIG_B, "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "field": "new_value", "value": _BIG_A}]))
    # Parität zum JSON/Legacy-Vergleich: nur die exakte Zeile, kein Kollaps auf B.
    assert [r["new_value"] for r in rows] == [_BIG_A]
    for r in rows:
        assert _legacy_row_matches_filters(r, [{"operator": "eq", "field": "new_value", "value": _BIG_A}])


async def test_pushdown_range_big_int_matches_legacy(store: SqliteSegmentStore):
    await store.append(
        [
            _event(_BIG_A, "2026-01-01T00:00:00.000Z"),
            _event(_BIG_B, "2026-01-01T00:00:01.000Z"),
        ]
    )
    # gt _BIG_A darf NUR _BIG_B liefern (nicht _BIG_A selbst durch Float-Kollaps).
    spec = {"operator": "gt", "field": "new_value", "value": _BIG_A}
    rows = await store.query(StoreQuery(limit=10, value_filters=[spec]))
    assert [r["new_value"] for r in rows] == [_BIG_B]

    # lte _BIG_A darf NUR _BIG_A liefern.
    spec2 = {"operator": "lte", "field": "new_value", "value": _BIG_A}
    rows2 = await store.query(StoreQuery(limit=10, value_filters=[spec2]))
    assert [r["new_value"] for r in rows2] == [_BIG_A]


# ---------------------------------------------------------------------------
# (3) Regex-Pushdown unter demselben Timeout-/Safe-Regex-Guard (:307)
# ---------------------------------------------------------------------------

# Katastrophales Pattern mit ambiguous alternation — der nested-quantifier-Check
# (_RE_UNSAFE_NESTED_QUANTIFIERS) verwirft es NICHT. Gegen einen langen Nicht-Match-
# Wert läuft die Backtracking-Explosion sehr lange, wenn ungebounded.
# (a|a)* auf einem langen Nicht-Match-Wert explodiert exponentiell im Backtracking:
# 28 Zeichen brauchen roh ~20 s (die 4-KiB-Längengrenze allein hilft NICHT). Der
# synchrone SQLite-Callback ist in CPython (GIL) NICHT per Timeout abbrechbar, daher
# muss das Muster als unsafe abgelehnt werden, BEVOR die Query läuft.
_CATASTROPHIC_PATTERN = "(a|a)*$"
_LONG_NOMATCH = "a" * 28 + "!"


async def test_catastrophic_regex_pushdown_is_rejected_fast(store: SqliteSegmentStore):
    import asyncio
    import time

    await store.append([_event(_LONG_NOMATCH, "2026-01-01T00:00:00.000Z")])
    q = StoreQuery(
        limit=10,
        candidate_cap=100,
        value_filters=[{"operator": "regex", "field": "new_value", "pattern": _CATASTROPHIC_PATTERN}],
    )
    # Das safe-regex-Gate lehnt die quantifizierte Alternation als 422-tauglichen
    # ValueError ab — schnell und ohne den Worker zu blockieren. wait_for stellt
    # sicher, dass NICHT in den katastrophalen Callback gelaufen wird.
    start = time.time()
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        await asyncio.wait_for(store.query(q), timeout=3.0)
    assert time.time() - start < 3.0, "Regex-Gate hätte VOR der Ausführung ablehnen müssen"


def test_safe_regex_gate_allows_benign_and_rejects_catastrophic():
    from obs.ringbuffer.store.sqlite_backend import _assert_safe_regex

    # Gutartige lineare Muster bleiben erlaubt.
    _assert_safe_regex("foo.*bar")
    _assert_safe_regex("[ab]+")
    _assert_safe_regex("(abc)+")
    # Katastrophale Strukturen werden abgelehnt.
    for pat in ("(a|a)*$", "(a|ab)+", "(a|b){0,5}", "(x+)+"):
        with pytest.raises(ValueError, match="unsafe regex pattern"):
            _assert_safe_regex(pat)


async def test_benign_regex_pushdown_still_matches(store: SqliteSegmentStore):
    # Regression: das Gate darf legitime Regex-Pushdowns nicht kaputtmachen.
    await store.append(
        [
            _event("hello world", "2026-01-01T00:00:00.000Z"),
            _event("goodbye", "2026-01-01T00:00:01.000Z"),
        ]
    )
    q = StoreQuery(
        limit=10,
        candidate_cap=100,
        value_filters=[{"operator": "regex", "field": "new_value", "pattern": "^hello"}],
    )
    rows = await store.query(q)
    assert [r["new_value"] for r in rows] == ["hello world"]
