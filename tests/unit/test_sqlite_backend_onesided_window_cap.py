"""Codex-Follow-up [P2] auf den Runde-36-Fix (:2441) am ``sqlite_backend.py`` (#951).

Runde 36 behandelte JEDE einseitige Zeitgrenze (nur ``from_ts`` ODER nur ``to_ts``)
als „windowed" und ließ die scan-heavy Prädikate (``q``/Metadaten/``contains``/
``regex``) daher ts-gebunden inline laufen statt durch den ``candidate_cap``.

Das über-generalisiert: eine einseitige Grenze bindet den Scan nur dann, wenn sie
ihn in der ITERIERTEN Richtung STOPPT.

* ``sort_order='desc'`` (neueste→älteste, Default): es stoppt die UNTERE Grenze
  (``from_ts``). Eine reine OBERE Grenze (``to_ts=now``) deckt effektiv die GANZE
  retained History ab und begrenzt den desc-Scan NICHT – der Cap muss BLEIBEN.
* ``sort_order='asc'`` (älteste→neueste): es stoppt die OBERE Grenze (``to_ts``).
  Eine reine UNTERE Grenze (``from_ts``) begrenzt den asc-Scan NICHT – Cap bleibt.

Beide Grenzen gesetzt → immer gebunden (Runde-36-Verhalten). Keine ts-Grenze →
gecapped (Runde 31). Die verfeinerte Bedingung betrifft NUR die scan-heavy
Prädikate; sargable Value-Filter / reine ts-Queries (ts-Index) sind unberührt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _ts(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


_NOW = _ts(59)
_EPOCH = _ts(0)


def _event(value: Any, ts: str, *, dp: str = "dp-1", src: str = "api") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
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


def _is_capped(sql: str) -> bool:
    """True, wenn der Segment-SQL die gedeckelte Kandidaten-Subquery kapselt."""
    return "FROM (SELECT" in sql


# ---------------------------------------------------------------------------
# SQL-Routing: einseitige Grenze bindet nur in Sortier-Richtung
# ---------------------------------------------------------------------------


async def test_desc_only_to_ts_keeps_cap(store: SqliteSegmentStore):
    """desc + NUR ``to_ts=now`` + Freitext-``q``: der Cap MUSS bleiben.

    ``to_ts`` ist bei desc die NICHT-stoppende Grenze; ``ts < now`` deckt die ganze
    retained History ab und begrenzt den neueste→älteste-Scan nicht. Ohne Cap würde
    ein seltener/fehlender ``q`` jede Zeile großer Segmente berühren.
    """
    cap = 5
    q = StoreQuery(q="zzz", to_ts=_NOW, candidate_cap=cap, limit=10, sort_order="desc")
    sql, params = store._build_segment_sql(q)
    assert _is_capped(sql), "desc + nur to_ts darf den Cap NICHT umgehen (nicht-stoppende Grenze)"
    assert cap in params, "candidate_cap muss als LIMIT der inneren Subquery erscheinen"


async def test_desc_only_from_ts_bypasses_cap(store: SqliteSegmentStore):
    """desc + NUR ``from_ts``: die stoppende Grenze bindet den Scan → inline (Runde-36)."""
    q = StoreQuery(q="zzz", from_ts=_EPOCH, candidate_cap=5, limit=10, sort_order="desc")
    sql, _params = store._build_segment_sql(q)
    assert not _is_capped(sql), "desc + from_ts (stoppende Grenze) bindet den Scan → keine Cap-Subquery"


async def test_asc_only_to_ts_bypasses_cap(store: SqliteSegmentStore):
    """asc + NUR ``to_ts``: bei älteste→neueste stoppt die OBERE Grenze den Scan → inline."""
    q = StoreQuery(q="zzz", to_ts=_NOW, candidate_cap=5, limit=10, sort_order="asc")
    sql, _params = store._build_segment_sql(q)
    assert not _is_capped(sql), "asc + to_ts (stoppende Grenze) bindet den Scan → keine Cap-Subquery"


async def test_asc_only_from_ts_keeps_cap(store: SqliteSegmentStore):
    """asc + NUR ``from_ts``: die NICHT-stoppende Grenze bei asc → Cap bleibt."""
    cap = 5
    q = StoreQuery(q="zzz", from_ts=_EPOCH, candidate_cap=cap, limit=10, sort_order="asc")
    sql, params = store._build_segment_sql(q)
    assert _is_capped(sql), "asc + nur from_ts darf den Cap NICHT umgehen (nicht-stoppende Grenze)"
    assert cap in params


async def test_both_bounds_bypass_cap(store: SqliteSegmentStore):
    """Beide Grenzen gesetzt: gebunden in beiden Richtungen → inline (Runde-36-Verhalten)."""
    for order in ("desc", "asc"):
        q = StoreQuery(q="zzz", from_ts=_EPOCH, to_ts=_NOW, candidate_cap=5, limit=10, sort_order=order)
        sql, _params = store._build_segment_sql(q)
        assert not _is_capped(sql), f"beide Grenzen ({order}) binden den Scan → keine Cap-Subquery"


async def test_no_ts_bound_keeps_cap(store: SqliteSegmentStore):
    """Keine ts-Grenze: unbounded Scope bleibt gedeckelt (Runde 31)."""
    cap = 5
    for order in ("desc", "asc"):
        q = StoreQuery(q="zzz", candidate_cap=cap, limit=10, sort_order=order)
        sql, params = store._build_segment_sql(q)
        assert _is_capped(sql), f"kein ts-Bound ({order}) → Cap bleibt"
        assert cap in params


# ---------------------------------------------------------------------------
# Verfeinerte Bedingung greift auch für Metadaten-/contains-/regex-Prädikate
# ---------------------------------------------------------------------------


async def test_desc_only_to_ts_keeps_cap_metadata(store: SqliteSegmentStore):
    """desc + nur ``to_ts`` + Metadaten-Tag-Filter → Cap bleibt (analog ``q``)."""
    cap = 5
    q = StoreQuery(metadata_tags_any_of=["rare-tag"], to_ts=_NOW, candidate_cap=cap, limit=10, sort_order="desc")
    sql, params = store._build_segment_sql(q)
    assert _is_capped(sql), "desc + nur to_ts + Metadaten → Cap bleibt"
    assert cap in params


async def test_desc_only_to_ts_keeps_cap_contains(store: SqliteSegmentStore):
    """desc + nur ``to_ts`` + guarded ``contains`` → Cap bleibt."""
    cap = 5
    spec = {"operator": "contains", "field": "new_value", "value": "needle"}
    q = StoreQuery(value_filters=[spec], to_ts=_NOW, candidate_cap=cap, limit=10, sort_order="desc")
    sql, params = store._build_segment_sql(q)
    assert _is_capped(sql), "desc + nur to_ts + contains → Cap bleibt"
    assert cap in params


# ---------------------------------------------------------------------------
# Reine ts-Query / sargable Value-Filter bleiben unberührt (kein Cap-Zwang)
# ---------------------------------------------------------------------------


async def test_pure_ts_query_never_capped(store: SqliteSegmentStore):
    """Reine ts-Query (kein scan-heavy Prädikat): nutzt den ts-Index, nie gecapped."""
    q = StoreQuery(to_ts=_NOW, candidate_cap=5, limit=10, sort_order="desc")
    sql, _params = store._build_segment_sql(q)
    assert not _is_capped(sql), "reine ts-Query darf nicht in die Cap-Subquery gezwungen werden"


async def test_sargable_value_filter_never_capped(store: SqliteSegmentStore):
    """Sargabler (typisierter Pushdown) Value-Filter: inline, kein Cap-Zwang."""
    spec = {"operator": "eq", "field": "new_value", "value": 42}
    q = StoreQuery(value_filters=[spec], to_ts=_NOW, candidate_cap=5, limit=10, sort_order="desc")
    sql, _params = store._build_segment_sql(q)
    assert not _is_capped(sql), "sargabler Value-Filter braucht keinen Cap"


# ---------------------------------------------------------------------------
# Ergebnis-Korrektheit: Cap deckelt, verwirft aber keine Treffer INNERHALB des Caps
# ---------------------------------------------------------------------------


async def test_desc_only_to_ts_within_cap_still_matches(store: SqliteSegmentStore):
    """desc + nur ``to_ts``: ein Treffer in den neuesten ``cap`` Zeilen wird geliefert.

    Der Cap deckelt nur, er verwirft keine echten Treffer innerhalb der gedeckelten
    Kandidatenmenge (Parität zur un-capped Referenz für Treffer im Fenster).
    """
    await store.append([_event(1, _ts(1), dp="target-dp", src="api")])
    q = StoreQuery(q="target", to_ts=_NOW, candidate_cap=100, limit=10, sort_order="desc")
    rows = await store.query(q)
    assert [r["datapoint_id"] for r in rows] == ["target-dp"]


async def test_desc_only_to_ts_beyond_cap_is_dropped(store: SqliteSegmentStore):
    """desc + nur ``to_ts``: ein per LIKE matchender Treffer JENSEITS des Caps fällt weg.

    Dokumentierte, gewollte Begrenzung des gedeckelten unwindowed scan-heavy Scans
    (identisch zum Runde-36-Gegentest ``test_f2_leading_wildcard_like_stays_capped``).
    Ohne den Follow-up-Fix liefe der Scan inline und fände die alte Zeile faelschlich.
    """
    await store.append([_event(999, _ts(0), dp="oldmatch", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    q = StoreQuery(q="oldmatch", to_ts=_NOW, candidate_cap=5, limit=50, sort_order="desc")
    rows = await store.query(q)
    values = {r["new_value"] for r in rows}
    assert 999 not in values, "desc + nur to_ts: LIKE-Treffer jenseits des Caps bleibt gedeckelt"


async def test_desc_only_from_ts_beyond_cap_is_found(store: SqliteSegmentStore):
    """Gegenprobe: desc + nur ``from_ts`` (stoppende Grenze) findet den alten Treffer inline."""
    await store.append([_event(999, _ts(0), dp="dp-hit-old", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    q = StoreQuery(q="hit", from_ts=_ts(0), candidate_cap=5, limit=50, sort_order="desc")
    rows = await store.query(q)
    values = {r["new_value"] for r in rows}
    assert 999 in values, "desc + from_ts bindet den Scan → alter Treffer inline gefunden"
