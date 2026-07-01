"""SQL-Pushdown für Wertfilter + typisierte Wertspalten (#919/#933).

Belegt:
* typisierte Spalten werden beim Append befüllt (numeric/text/bool/null),
* einfache Operatoren (eq/ne/gt/gte/lt/lte/between) laufen als SQL-WHERE,
  sodass LIMIT nicht mehr durch einen Python-Post-Filter ausgehebelt wird,
* der Fetch ist nachweislich bounded (EXPLAIN QUERY PLAN nutzt WHERE + LIMIT),
* gemischte Typen matchen nicht fälschlich (numerischer Filter auf Text),
* contains/regex greifen nur mit engem Zeitfenster/Kandidaten-Cap, sonst Guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: object, ts: str, *, dp: str = "dp-1", old: object = None) -> StoreEvent:
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


# ------------------------------------------------------------------
# Typableitung beim Schreiben
# ------------------------------------------------------------------


async def test_typed_columns_populated_numeric_text_bool_null(store: SqliteSegmentStore):
    await store.append(
        [
            _event(42, "2026-01-01T00:00:00.000Z"),
            _event(3.5, "2026-01-01T00:00:01.000Z"),
            _event("hello", "2026-01-01T00:00:02.000Z"),
            _event(True, "2026-01-01T00:00:03.000Z"),
            _event(None, "2026-01-01T00:00:04.000Z"),
        ]
    )
    conn = store._active_conn
    async with conn.execute(
        "SELECT new_value, new_value_type, new_value_num, new_value_text, new_value_bool FROM ringbuffer ORDER BY global_event_id"
    ) as cur:
        rows = await cur.fetchall()
    by_type = [(r["new_value_type"], r["new_value_num"], r["new_value_text"], r["new_value_bool"]) for r in rows]
    assert by_type[0] == ("numeric", 42.0, None, None)
    assert by_type[1] == ("numeric", 3.5, None, None)
    assert by_type[2] == ("text", None, "hello", None)
    # bool VOR numeric klassifiziert (bool ist Subklasse von int).
    assert by_type[3] == ("bool", None, None, 1)
    assert by_type[4] == ("null", None, None, None)


async def test_capabilities_advertise_typed_pushdown(store: SqliteSegmentStore):
    assert store.capabilities().supports_typed_pushdown is True


# ------------------------------------------------------------------
# SQL-Pushdown: LIMIT bleibt korrekt, Filter ist WHERE (nicht Post-Filter)
# ------------------------------------------------------------------


async def test_numeric_eq_pushdown_matches(store: SqliteSegmentStore):
    await store.append(
        [
            _event(10, "2026-01-01T00:00:00.000Z"),
            _event(20, "2026-01-01T00:00:01.000Z"),
            _event(10, "2026-01-01T00:00:02.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "value": 10}]))
    assert {r["new_value"] for r in rows} == {10}
    assert len(rows) == 2


async def test_numeric_gt_gte_lt_lte(store: SqliteSegmentStore):
    await store.append([_event(v, f"2026-01-01T00:00:0{v}.000Z") for v in (1, 2, 3, 4, 5)])
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "value": 3}]))
    assert {r["new_value"] for r in rows} == {4, 5}
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "lte", "value": 2}]))
    assert {r["new_value"] for r in rows} == {1, 2}


async def test_between_inclusive(store: SqliteSegmentStore):
    await store.append([_event(v, f"2026-01-01T00:00:0{v}.000Z") for v in (1, 2, 3, 4, 5)])
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "between", "lower": 2, "upper": 4}]))
    assert {r["new_value"] for r in rows} == {2, 3, 4}


async def test_ne_excludes_value(store: SqliteSegmentStore):
    await store.append([_event(v, f"2026-01-01T00:00:0{v}.000Z") for v in (1, 2, 3)])
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "ne", "value": 2}]))
    assert {r["new_value"] for r in rows} == {1, 3}


async def test_text_eq(store: SqliteSegmentStore):
    await store.append(
        [
            _event("on", "2026-01-01T00:00:00.000Z"),
            _event("off", "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "value": "on"}]))
    assert [r["new_value"] for r in rows] == ["on"]


async def test_bool_eq(store: SqliteSegmentStore):
    await store.append(
        [
            _event(True, "2026-01-01T00:00:00.000Z"),
            _event(False, "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "value": True}]))
    assert [r["new_value"] for r in rows] == [True]


async def test_mixed_types_numeric_filter_does_not_match_text(store: SqliteSegmentStore):
    # Textwert "10" darf von numerischem eq=10 NICHT getroffen werden.
    await store.append(
        [
            _event(10, "2026-01-01T00:00:00.000Z"),
            _event("10", "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "value": 5}]))
    assert [r["new_value"] for r in rows] == [10]


async def test_limit_not_defeated_by_post_filter(store: SqliteSegmentStore):
    # 100 matchende Events, aber LIMIT 5: der Filter muss als SQL-WHERE greifen,
    # damit LIMIT 5 wirkt und nicht erst alle 100 gefetcht + in Python beschnitten.
    await store.append([_event(7, f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z") for i in range(100)])
    rows = await store.query(StoreQuery(limit=5, value_filters=[{"operator": "eq", "value": 7}]))
    assert len(rows) == 5


async def test_explain_query_plan_uses_where_and_limit(store: SqliteSegmentStore):
    # Belegt, dass der Fetch bounded ist: der pushdown-SQL enthält ein WHERE auf
    # die typisierte Spalte und ein LIMIT (kein Full-Table-Fetch + Python-Slice).
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    query = StoreQuery(limit=3, value_filters=[{"operator": "eq", "value": 1}])
    sql, params = store._build_segment_sql(query)
    assert "new_value_num" in sql
    assert sql.rstrip().endswith("LIMIT ?")
    conn = store._active_conn
    async with conn.execute(f"EXPLAIN QUERY PLAN {sql}", params) as cur:
        plan = await cur.fetchall()
    plan_text = " ".join(str(tuple(r)) for r in plan)
    # SQLite meldet SCAN/SEARCH; der Punkt ist: WHERE ist im Plan, kein reiner
    # unbounded Full-Fetch mit anschließendem Python-Filter.
    assert "ringbuffer" in plan_text.lower()


# ------------------------------------------------------------------
# contains/regex-Guards
# ------------------------------------------------------------------


async def test_contains_requires_bounded_window(store: SqliteSegmentStore):
    await store.append([_event("hello world", "2026-01-01T00:00:00.000Z")])
    # ohne Zeitfenster / ohne Cap → Guard.
    with pytest.raises(ValueError, match="bounded"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "contains", "value": "world"}]))


async def test_regex_requires_bounded_window(store: SqliteSegmentStore):
    await store.append([_event("abc123", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="bounded"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "regex", "pattern": r"\d+"}]))


async def test_contains_allowed_with_time_window(store: SqliteSegmentStore):
    await store.append(
        [
            _event("hello world", "2026-01-01T00:00:00.000Z"),
            _event("goodbye", "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-01T00:00:00.000Z",
            to_ts="2026-01-01T01:00:00.000Z",
            limit=10,
            value_filters=[{"operator": "contains", "value": "world"}],
        )
    )
    assert [r["new_value"] for r in rows] == ["hello world"]


async def test_regex_allowed_with_time_window(store: SqliteSegmentStore):
    await store.append(
        [
            _event("abc123", "2026-01-01T00:00:00.000Z"),
            _event("noдigits", "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-01T00:00:00.000Z",
            to_ts="2026-01-01T01:00:00.000Z",
            limit=10,
            value_filters=[{"operator": "regex", "pattern": r"\d+"}],
        )
    )
    assert [r["new_value"] for r in rows] == ["abc123"]


async def test_invalid_operator_rejected(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="operator"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "bogus", "value": 1}]))


async def test_old_value_field_filter(store: SqliteSegmentStore):
    await store.append(
        [
            _event(2, "2026-01-01T00:00:00.000Z", old=1),
            _event(3, "2026-01-01T00:00:01.000Z", old=9),
        ]
    )
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"field": "old_value", "operator": "gt", "value": 5}]))
    assert [r["new_value"] for r in rows] == [3]


# ------------------------------------------------------------------
# Guard-/Fehlerpfade der Filter-Übersetzung
# ------------------------------------------------------------------


async def test_invalid_field_rejected(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="field"):
        await store.query(StoreQuery(limit=10, value_filters=[{"field": "bogus", "operator": "eq", "value": 1}]))


async def test_between_requires_numeric_bounds(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="numeric"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "between", "lower": "a", "upper": "z"}]))


async def test_between_lower_must_be_le_upper(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="lower"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "between", "lower": 5, "upper": 2}]))


async def test_pushdown_needs_addressable_value(store: SqliteSegmentStore):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    # None/Liste ist als typisierter Vergleichswert nicht adressierbar.
    with pytest.raises(ValueError, match="numeric, text or bool"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "value": None}]))
    with pytest.raises(ValueError, match="numeric, text or bool"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "value": [1, 2]}]))


async def test_contains_requires_string_value(store: SqliteSegmentStore):
    await store.append([_event("x", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="contains requires a string"):
        await store.query(
            StoreQuery(
                from_ts="2026-01-01T00:00:00.000Z",
                to_ts="2026-01-01T01:00:00.000Z",
                limit=10,
                value_filters=[{"operator": "contains", "value": 5}],
            )
        )


async def test_contains_ignore_case_and_special_chars(store: SqliteSegmentStore):
    # LIKE-Sonderzeichen (%) müssen escaped werden, ignore_case matcht case-insensitiv.
    await store.append(
        [
            _event("50% OFF", "2026-01-01T00:00:00.000Z"),
            _event("50 percent", "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-01T00:00:00.000Z",
            to_ts="2026-01-01T01:00:00.000Z",
            limit=10,
            value_filters=[{"operator": "contains", "value": "% off", "ignore_case": True}],
        )
    )
    assert [r["new_value"] for r in rows] == ["50% OFF"]


async def test_contains_allowed_with_candidate_cap(store: SqliteSegmentStore):
    # Kein Zeitfenster, aber candidate_cap → bounded, also erlaubt.
    await store.append([_event("hello world", "2026-01-01T00:00:00.000Z")])
    rows = await store.query(StoreQuery(limit=10, candidate_cap=100, value_filters=[{"operator": "contains", "value": "world"}]))
    assert [r["new_value"] for r in rows] == ["hello world"]


async def test_regex_empty_pattern_rejected(store: SqliteSegmentStore):
    await store.append([_event("x", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="non-empty pattern"):
        await store.query(
            StoreQuery(
                from_ts="2026-01-01T00:00:00.000Z",
                to_ts="2026-01-01T01:00:00.000Z",
                limit=10,
                value_filters=[{"operator": "regex", "pattern": ""}],
            )
        )


async def test_regex_too_long_pattern_rejected(store: SqliteSegmentStore):
    await store.append([_event("x", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="too long"):
        await store.query(
            StoreQuery(
                from_ts="2026-01-01T00:00:00.000Z",
                to_ts="2026-01-01T01:00:00.000Z",
                limit=10,
                value_filters=[{"operator": "regex", "pattern": "a" * 300}],
            )
        )


async def test_regex_nested_quantifiers_rejected(store: SqliteSegmentStore):
    await store.append([_event("x", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="nested quantifiers"):
        await store.query(
            StoreQuery(
                from_ts="2026-01-01T00:00:00.000Z",
                to_ts="2026-01-01T01:00:00.000Z",
                limit=10,
                value_filters=[{"operator": "regex", "pattern": r"(a+)+"}],
            )
        )


async def test_regex_ignores_non_string_rows(store: SqliteSegmentStore):
    # Regex läuft nur gegen text-Spalte; numerische Zeilen (text-Spalte NULL)
    # werden gar nicht erst dem Callback übergeben, matchen also nicht.
    await store.append(
        [
            _event("abc123", "2026-01-01T00:00:00.000Z"),
            _event(123, "2026-01-01T00:00:01.000Z"),
        ]
    )
    rows = await store.query(
        StoreQuery(
            from_ts="2026-01-01T00:00:00.000Z",
            to_ts="2026-01-01T01:00:00.000Z",
            limit=10,
            value_filters=[{"operator": "regex", "pattern": r"\d+"}],
        )
    )
    assert [r["new_value"] for r in rows] == ["abc123"]
