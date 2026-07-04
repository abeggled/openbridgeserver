"""Row-lazy Regex-Value-Filter nutzt dasselbe gehärtete Safe-Regex-Gate (#951, Codex :1678).

Nach dem Value-Filter-Wurzel-Fix laufen scoped (adapter/q/metadata) Regex-Filter, die
nicht pushbar sind, row-lazy über ``_apply_value_filters`` → ``_match_string_operator``
→ ``_match_regex``. Vor dem Fix nutzte ``_match_regex`` nur eine schwächere, NICHT
nesting-aware Vorprüfung (``_RE_UNSAFE_NESTED_QUANTIFIERS``), die katastrophale Muster
mit Wrapper-Klammern wie ``((a+))+b`` oder quantifizierte Alternationen wie
``(a|aa){30}b`` durchließ. Ein solches Muster liefe dann gegen jeden Kandidatenstring
und verbrennte bei einem langen Non-Match den Worker/GIL, statt das intendierte
422-taugliche ``ValueError`` zu liefern.

Diese Tests fixieren, dass der row-lazy Pfad (Legacy/``segmented=False``) exakt dasselbe
gehärtete Gate ``_assert_safe_regex`` verwendet: katastrophale Muster werden VOR der
Ausführung abgelehnt, benigne Muster matchen normal. Konsistenzcheck: derselbe Filter
verhält sich für dieselben Muster identisch wie das Store-Gate ``_assert_safe_regex``.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from obs.ringbuffer.ringbuffer import RingBufferEntry, _apply_value_filters
from obs.ringbuffer.store.sqlite_backend import _assert_safe_regex

# Katastrophale Muster (nested/wrapper-quantifiers, quantifizierte Alternation), die die
# alte schwache Vorprüfung teils durchließ, das gehärtete Gate aber ablehnt.
CATASTROPHIC_PATTERNS = ["((a+))+b", "(a+)+", "(a|aa){30}b"]
# Benigne Muster, die weiterhin normal matchen müssen.
BENIGN_PATTERNS = ["(abc)+", "foo.*bar"]


def _entry(value: Any, idx: int = 0) -> RingBufferEntry:
    return RingBufferEntry(
        id=idx,
        ts=f"2026-01-01T00:00:{idx:02d}.000Z",
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
    )


async def _row_lazy_regex(value: str, pattern: str, *, data_type: str = "STRING") -> list[RingBufferEntry]:
    """Fährt den row-lazy Regex-Filter über genau einen STRING-Kandidaten."""
    entries = [_entry(value)]
    value_filters = [{"operator": "regex", "pattern": pattern}]
    datapoint_types = {"dp-1": data_type}
    return await _apply_value_filters(entries=entries, value_filters=value_filters, datapoint_types=datapoint_types)


@pytest.mark.parametrize("pattern", CATASTROPHIC_PATTERNS)
async def test_row_lazy_regex_rejects_catastrophic_pattern_before_execution(pattern: str) -> None:
    # Ein langer Non-Match-String – ohne Gate würde CPython hier sekundenlang backtracken.
    target = "a" * 40
    started = time.monotonic()
    with pytest.raises(ValueError):
        await _row_lazy_regex(target, pattern)
    elapsed = time.monotonic() - started
    # Die Ablehnung ist statisch (Muster-Scan), also praktisch sofort – kein Backtracking.
    assert elapsed < 0.5, f"Muster {pattern!r} wurde ausgeführt statt vorab abgelehnt ({elapsed:.2f}s)"


@pytest.mark.parametrize("pattern", BENIGN_PATTERNS)
async def test_row_lazy_regex_matches_benign_pattern(pattern: str) -> None:
    matched = await _row_lazy_regex("abcabc" if pattern == "(abc)+" else "foobazbar", pattern)
    assert len(matched) == 1


async def test_row_lazy_regex_benign_non_match_filters_row_out() -> None:
    # Benignes Muster, das nicht passt → Zeile fällt regulär aus dem Ergebnis (kein Fehler).
    matched = await _row_lazy_regex("nope", "foo.*bar")
    assert matched == []


@pytest.mark.parametrize("pattern", CATASTROPHIC_PATTERNS + BENIGN_PATTERNS)
async def test_row_lazy_regex_consistent_with_store_gate(pattern: str) -> None:
    """Eine Quelle der Wahrheit: row-lazy Gate == Store-Gate ``_assert_safe_regex``."""
    store_rejects = False
    try:
        _assert_safe_regex(pattern)
    except ValueError:
        store_rejects = True

    row_lazy_rejects = False
    try:
        await _row_lazy_regex("a" * 40, pattern)
    except ValueError:
        row_lazy_rejects = True

    assert row_lazy_rejects == store_rejects, f"Divergenz für Muster {pattern!r}: store={store_rejects} row_lazy={row_lazy_rejects}"
