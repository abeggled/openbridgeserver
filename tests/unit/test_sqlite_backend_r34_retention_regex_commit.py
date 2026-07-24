"""Codex-[P2]-Findings Runde 34 am RingBuffer-SQLite-Backend (#919, PR #951).

Vier unabhaengige Findings, je TDD-first (rot ohne Fix, gruen mit Fix); wo sinnvoll
gegen den Legacy-Pfad paritaetisch geprueft:

F1 (:2575) – unlink-blocked Segmente ueber Budget muessen als NON-DELETABLE zaehlen,
sonst meldet ``/stats`` ``retention_over_budget=false`` obwohl der Store real ueber
``max_file_size_bytes`` bleibt und jeder Retention-Pass an derselben Datei blockiert.

F2 (:338) – der nesting-aware Regex-Scanner las das ``?`` in Extension-Praefixen
(``(?:...)``, ``(?P<name>...)``, ``(?i:...)`` …) als inneren Quantifier und wies
sichere Filter wie ``(?:abc)+`` faelschlich als „nested quantifiers" ab (Ueber-
Rejection / 422). Praefixe werden nun VOR dem Koerper uebersprungen.

F3 (:1013) – ein Fehler WAEHREND ``commit()`` (volle Disk / I/O nach Inserts) liess
die offene Transaktion ungerollt; ein spaeterer erfolgreicher Append committete die
Zeilen des „fehlgeschlagenen" Batches mit. ``commit`` ist nun im rollback-geschuetzten
Block.

F4 (:499) – ein gespeicherter Wert ueber ``_REGEX_MAX_TARGET_LEN`` wurde im
segmentierten Callback nur im Prefix durchsucht (Truncation), statt – wie der Legacy-
Pfad – als Validierungsfehler abgelehnt. Ergebnis haengt sonst von der Truncation-
Grenze ab.
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import (
    _REGEX_MAX_TARGET_LEN,
    SqliteSegmentStore,
    _assert_safe_regex,
    _legacy_row_matches_filters,
)


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
# F1: unlink-blocked Segmente als non-deletable zaehlen
# ===========================================================================


async def _build_over_budget_store(root: Path) -> SqliteSegmentStore:
    """Store mit einem aktiven UND einem geschlossenen Segment, Budget zwischen beiden.

    Das AKTIVE Segment allein liegt unter ``max_file_size_bytes`` (nie loeschbar),
    das GESCHLOSSENE Segment tippt den Store ueber Budget. Ist das geschlossene
    Segment loeschbar → ``retention_over_budget=false``; ist sein Unlink blockiert →
    ``true`` (Fall B).
    """
    store = SqliteSegmentStore(root, retention=StoreRetentionConfig(max_file_size_bytes=160_000))
    await store.open()
    await store.append([_event("x" * 50, "2024-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event("y" * 50, "2024-01-02T00:00:00.000Z")])
    return store


async def test_unlink_blocked_segment_reports_over_budget(tmp_path: Path):
    # F1 (rot ohne Fix): scheitert der Unlink der geschlossenen Basisdatei
    # (OSError/EBUSY), bleibt der Store ueber Budget UND jeder Pass blockiert an
    # derselben Datei. ``retention_over_budget`` MUSS true sein (Bytes non-deletable).
    store = await _build_over_budget_store(tmp_path / "root")
    try:
        closed = next(s for s in await store.manifest.list_segments() if s.status == "closed")
        real_unlink = Path.unlink

        def fake_unlink(self: Path, *a: Any, **k: Any):
            if self.name == closed.filename:
                exc = OSError()
                exc.errno = errno.EBUSY
                raise exc
            return real_unlink(self, *a, **k)

        with patch.object(Path, "unlink", fake_unlink):
            removed = await store.enforce_retention()

        assert removed == 0, "unlink-blockiertes Segment darf nicht als entfernt gezaehlt werden"
        stats = await store.stats()
        assert stats.backend_extra["retention_over_budget"] is True
        assert closed.segment_id in stats.backend_extra["unlink_blocked_segment_ids"]
    finally:
        await store.close()


async def test_successful_delete_clears_over_budget(tmp_path: Path):
    # F1 Gegentest: gelingt der Unlink, faellt der Store unter Budget →
    # ``retention_over_budget=false`` und die Blocked-Menge ist leer.
    store = await _build_over_budget_store(tmp_path / "root")
    try:
        removed = await store.enforce_retention()
        assert removed == 1
        stats = await store.stats()
        assert stats.backend_extra["retention_over_budget"] is False
        assert stats.backend_extra["unlink_blocked_segment_ids"] == []
    finally:
        await store.close()


async def test_unlink_block_cleared_on_later_success(tmp_path: Path):
    # F1: ist der Unlink erst blockiert (over_budget) und gelingt beim naechsten
    # Pass, verschwindet der persistente Fehlerzustand wieder.
    store = await _build_over_budget_store(tmp_path / "root")
    try:
        closed = next(s for s in await store.manifest.list_segments() if s.status == "closed")
        real_unlink = Path.unlink
        blocked = {"active": True}

        def fake_unlink(self: Path, *a: Any, **k: Any):
            if blocked["active"] and self.name == closed.filename:
                exc = OSError()
                exc.errno = errno.EBUSY
                raise exc
            return real_unlink(self, *a, **k)

        with patch.object(Path, "unlink", fake_unlink):
            await store.enforce_retention()
            stats_blocked = await store.stats()
            assert stats_blocked.backend_extra["retention_over_budget"] is True
            # Lock/Busy loest sich → naechster Pass loescht sauber.
            blocked["active"] = False
            removed = await store.enforce_retention()

        assert removed == 1
        stats_ok = await store.stats()
        assert stats_ok.backend_extra["retention_over_budget"] is False
        assert stats_ok.backend_extra["unlink_blocked_segment_ids"] == []
    finally:
        await store.close()


# ===========================================================================
# F2: Extension-Praefixe VOR dem Scannen des Gruppen-Koerpers ueberspringen
# ===========================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "(?:abc)+",  # non-capturing, quantifiziert
        "(?P<x>abc)+",  # named group
        "(?i:abc)+",  # scoped inline flag
        "(?im:abc)+",  # mehrere Flags
        "(?i-s:abc)+",  # gesetzte + entfernte Flags
        "(?i)abc",  # globaler Inline-Flag
        "(?=abc)",  # look-ahead
        "(?!abc)",  # negative look-ahead
        "(?<=abc)",  # look-behind
        "(?<!abc)",  # negative look-behind
        "(?>abc)+",  # atomic group
        "(?#comment)x+",  # Kommentar-Gruppe
        "(?P=name)",  # named backreference
        "(?:abc){3}",  # counted quantifier auf non-capturing
    ],
)
def test_assert_safe_regex_allows_extension_prefixes(pattern: str):
    # F2 (rot ohne Fix): das ``?`` (bzw. ``<``/``=``/``!``/Flags) des Praefixes wurde
    # als innerer Quantifier gelesen → faelschlicher 422. Muss jetzt erlaubt sein.
    _assert_safe_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+",  # klassisch nested
        "((a+))+",  # nested hinter Wrapper
        "(a|aa){30}b",  # katastrophale quantifizierte Alternation
        "(?:(a+))+",  # ECHT nested innerhalb non-capturing
        "(?:a+)+",  # non-capturing mit innerem Quantifier + aeusserem
    ],
)
def test_assert_safe_regex_still_rejects_catastrophic(pattern: str):
    # F2: die katastrophalen Faelle bleiben reject – der Praefix-Skip darf sie NICHT
    # durchlassen.
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex(pattern)


async def test_noncapturing_group_query_not_rejected(tmp_path: Path):
    # F2 End-to-end: ein Value-Filter mit ``(?:abc)+`` darf NICHT als 422 abgewiesen
    # werden, sondern regulaer matchen.
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event("abcabc", "2024-01-01T00:00:00.000Z")])
        query = StoreQuery(
            limit=10,
            candidate_cap=100,
            value_filters=[{"field": "new_value", "operator": "regex", "pattern": "(?:abc)+"}],
        )
        rows = await store.query(query)
        assert {r["new_value"] for r in rows} == {"abcabc"}
    finally:
        await store.close()


# ===========================================================================
# F3: commit-Fehler zuruckrollen (kein Batch-Leak in den naechsten Append)
# ===========================================================================


async def test_commit_failure_rolls_back_and_no_leak(tmp_path: Path):
    # F3 (rot ohne Fix): scheitert ``commit()`` selbst, blieb die Transaktion offen und
    # der naechste erfolgreiche Append committete die „fehlgeschlagenen" Zeilen mit.
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        conn = store._active_conn
        assert conn is not None
        real_rollback = conn.rollback
        rollback_calls = {"n": 0}

        async def counting_rollback():
            rollback_calls["n"] += 1
            return await real_rollback()

        async def failing_commit():
            raise aiosqlite.OperationalError("disk I/O error")

        with (
            patch.object(conn, "commit", failing_commit),
            patch.object(conn, "rollback", counting_rollback),
            pytest.raises(aiosqlite.OperationalError),
        ):
            await store.append([_event("leak", "2024-01-01T00:00:00.000Z")])

        assert rollback_calls["n"] == 1, "commit-Fehler muss die Transaktion zurueckrollen"

        # Naechster Append committet sauber – die zuvor eingereihte „leak"-Zeile darf
        # NICHT mitcommitten.
        await store.append([_event("good", "2024-01-02T00:00:00.000Z")])
        rows = await store.query(StoreQuery(limit=10))
        assert sorted(r["new_value"] for r in rows) == ["good"]
    finally:
        await store.close()


async def test_normal_append_commits(tmp_path: Path):
    # F3 Gegentest: ein normaler Append committet wie bisher korrekt.
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event("a", "2024-01-01T00:00:00.000Z")])
        await store.append([_event("b", "2024-01-02T00:00:00.000Z")])
        rows = await store.query(StoreQuery(limit=10))
        assert sorted(r["new_value"] for r in rows) == ["a", "b"]
    finally:
        await store.close()


# ===========================================================================
# F4: zu langen Regex-Zielwert ABLEHNEN statt truncaten (Parität Legacy==segmentiert)
# ===========================================================================


def test_legacy_matcher_rejects_long_target():
    # F4 Legacy-Parität (Python-Fallback-Matcher): ein Wert ueber der Grenze wird als
    # 422-tauglicher ValueError abgelehnt, NICHT auf den Prefix durchsucht.
    long_value = "a" * (_REGEX_MAX_TARGET_LEN + 10)
    record = {"new_value": long_value}
    spec = {"field": "new_value", "operator": "regex", "pattern": "a"}
    with pytest.raises(ValueError, match="target value too long"):
        _legacy_row_matches_filters(record, [spec])


def test_legacy_matcher_allows_short_target():
    # F4 Gegentest: ein kurzer Wert wird unveraendert gematcht.
    record = {"new_value": "short"}
    spec = {"field": "new_value", "operator": "regex", "pattern": "shor"}
    assert _legacy_row_matches_filters(record, [spec]) is True


def test_legacy_matcher_long_target_dollar_anchor_rejected():
    # F4: ein ``$``-verankertes Muster darf die kuenstliche Truncation-Grenze nicht
    # matchen – der lange Wert wird abgelehnt, das Ergebnis haengt nicht von 4096 ab.
    long_value = "a" * (_REGEX_MAX_TARGET_LEN + 10)
    record = {"new_value": long_value}
    spec = {"field": "new_value", "operator": "regex", "pattern": "a$"}
    with pytest.raises(ValueError, match="target value too long"):
        _legacy_row_matches_filters(record, [spec])


async def test_pushdown_regex_rejects_long_target(tmp_path: Path):
    # F4 (rot ohne Fix): der segmentierte Pushdown-Callback truncatete auf den Prefix.
    # Ein gespeicherter Wert ueber der Grenze muss den Filter als ValueError (422)
    # ablehnen – propagiert sauber aus dem SQLite-Callback bis zur API.
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        long_value = "b" * (_REGEX_MAX_TARGET_LEN + 10)
        await store.append([_event(long_value, "2024-01-01T00:00:00.000Z")])
        query = StoreQuery(
            limit=10,
            candidate_cap=100,
            value_filters=[{"field": "new_value", "operator": "regex", "pattern": "b"}],
        )
        with pytest.raises(ValueError, match="target value too long"):
            await store.query(query)
    finally:
        await store.close()


async def test_pushdown_regex_short_target_unchanged(tmp_path: Path):
    # F4 Gegentest: ein kurzer Wert matcht regulaer ueber den Pushdown-Pfad.
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append([_event("hello", "2024-01-01T00:00:00.000Z")])
        query = StoreQuery(
            limit=10,
            candidate_cap=100,
            value_filters=[{"field": "new_value", "operator": "regex", "pattern": "ell"}],
        )
        rows = await store.query(query)
        assert {r["new_value"] for r in rows} == {"hello"}
    finally:
        await store.close()
