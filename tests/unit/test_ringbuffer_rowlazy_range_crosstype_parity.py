"""v2-row-lazy Range-Filter: Cross-Typ-Historie skippen wie Legacy + Pushdown (#951, Codex :2263).

Ein datapoint-gescopter ``gt``/``between``-Filter auf einen FLOAT/INTEGER-Datapoint
lief bisher divergent: der SQL-Pushdown (segmentiert) SKIPPT nicht-numerische
Historien-Samples (``new_value_num IS NULL``) und der v1-Legacy-Pfad
(``_legacy_compare``) ebenfalls, während der v2-row-lazy-Pfad
(``_matches_value_filter``) dafür ``ValueError`` → 422 warf. Damit lieferte
derselbe Filter je nach Storage-/Query-Modus mal partielle Ergebnisse, mal 422.

Konsistenter Zielzustand: nicht-numerische HISTORIE-Werte werden überall
übersprungen (kein Match); nur ein ungültiger FILTER-Wert (``gt:null``,
``between`` mit String-Grenze) bleibt ein 422-tauglicher Fehler.
"""

from __future__ import annotations

import pytest

from obs.ringbuffer.ringbuffer import _matches_value_filter, _normalize_value_filter


async def _m(hist, op="gt", **extra):
    vf = _normalize_value_filter({"operator": op, **({"value": 5} if op != "between" else {"lower": 5, "upper": 10}), **extra})
    return await _matches_value_filter(hist, "FLOAT", vf)


async def test_cross_type_history_is_skipped_not_rejected():
    # null / String / bool in der Historie eines FLOAT-Datapoints: kein Match (skip),
    # NICHT 422 – Parität zu _legacy_compare + SQL-Pushdown.
    for hist in (None, "abc", True):
        assert await _m(hist, "gt") is False, f"{hist!r} sollte übersprungen werden, nicht werfen"
        assert await _m(hist, "between") is False


async def test_numeric_history_still_matches_correctly():
    assert await _m(15, "gt") is True
    assert await _m(3, "gt") is False
    assert await _m(7, "between") is True
    assert await _m(20, "between") is False


async def test_invalid_filter_value_still_raises():
    # Ungültiger FILTER-Wert bleibt ein 422-tauglicher Fehler (kein stiller Skip),
    # auch wenn die Historie-Zeile numerisch ist.
    with pytest.raises(ValueError):
        await _m(10, "gt", value=None)
    with pytest.raises(ValueError):
        await _m(10, "between", lower="abc", upper=10)
