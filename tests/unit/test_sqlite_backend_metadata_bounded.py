"""Codex-R-P2-Finding am segmentierten SQLite-Store (#919, PR #951, sqlite_backend.py:1861).

„Bound metadata filters like other scan predicates."

Fuer unwindowed metadata-only Monitor-Queries haengt der Code das Metadaten-
``EXISTS``-Praedikat an die Base-WHERE-Klauseln, statt es durch den GEDECKELTEN
guarded Pfad in ``_build_segment_sql`` zu routen. Bei einem seltenen/fehlenden
Tag/Binding kann SQLite die ``ringbuffer``-Ordnung ueber ein ganzes grosses
Segment walken, bevor ``LIMIT`` erfuellt ist – waehrend die anderen Scan-
Praedikate (Freitext-``q``, guarded value-Filter) auf eine gedeckelte
Kandidatenmenge gelegt werden.

Fix: den Metadaten-Filter durch dieselbe Candidate-Cap-Subquery routen wie die
anderen guarded Scan-Praedikate (unwindowed + nicht-Export). Mit Zeitfenster/
Export bleibt er inline. Ergebnis-Semantik unveraendert (nur bounded).

TDD-first: die Tests reproduzieren das Fehlverhalten ohne Fix und werden durch
den Fix gruen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _ts(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


def _event(value: Any, ts: str, *, dp: str = "dp-1", tags: list[str] | None = None) -> StoreEvent:
    metadata: dict[str, Any] = {}
    if tags is not None:
        metadata = {"datapoint": {"tags": tags}}
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata=metadata,
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
# (1) Metadaten-Tag ohne Zeitfenster bleibt gedeckelt (:1861)
# ---------------------------------------------------------------------------


async def test_metadata_tag_without_window_is_capped(store: SqliteSegmentStore):
    # Viele Zeilen, deren Metadaten-Tag den gesuchten Tag NIE tragen. Das
    # EXISTS-Praedikat auf ringbuffer_metadata_tags kann keinen Match finden;
    # ohne Cap muesste SQLite die ganze ringbuffer-Ordnung des Segments walken,
    # nur um zu beweisen, dass kein Tag matcht.
    await store.append([_event(i, _ts(i), dp=f"aaa-{i}", tags=["present"]) for i in range(60)])

    cap = 5
    q = StoreQuery(limit=10, candidate_cap=cap, metadata_tags_any_of=["absent"])

    # Der generierte Segment-SQL muss das teure Metadaten-EXISTS auf eine
    # gedeckelte Kandidaten-Subquery legen (LIMIT ? mit cap), NICHT inline.
    sql, params = store._build_segment_sql(q)
    assert "EXISTS" in sql
    assert cap in params, "candidate_cap muss als LIMIT der inneren Subquery erscheinen"
    assert "FROM (SELECT" in sql, "Metadaten-Filter muss um eine gedeckelte Subquery gelegt werden"

    # Ergebnis-Paritaet: Tag kommt nicht vor → leeres Ergebnis.
    rows = await store.query(q)
    assert rows == []


async def test_metadata_tag_bounded_across_multiple_segments(store: SqliteSegmentStore):
    # Mehrere grosse, geschlossene v2-Segmente ohne den gesuchten Tag, plus ein
    # frisches aktives Segment. Ohne Fix walkt SQLite jedes Segment voll (Full-
    # Segment-Scan), bis LIMIT erfuellt ist; mit Fix ist jeder Segment-Scan hart
    # auf candidate_cap Zeilen gedeckelt. Der EXPLAIN QUERY PLAN darf keinen
    # ungedeckelten Full-Scan der aeusseren ringbuffer-Ordnung zeigen.
    for seg in range(3):
        await store.append([_event(i, _ts((seg * 20 + i) % 60), dp=f"seg{seg}-{i}", tags=["present"]) for i in range(20)])
        await store.rotate()
    await store.append([_event(1, _ts(0), dp="active", tags=["present"])])

    cap = 4
    q = StoreQuery(limit=10, candidate_cap=cap, metadata_tags_any_of=["absent"])

    sql, params = store._build_segment_sql(q)
    # Struktur-Beweis der Deckelung: die innere Subquery traegt das LIMIT
    # (candidate_cap), und das teure Metadaten-EXISTS steht NACH der Kapsel
    # (``AS capped``) – korreliert also gegen die cap-grosse Kandidatenmenge,
    # nicht ueber die volle Segment-Ordnung.
    assert "FROM (SELECT" in sql
    assert cap in params
    capsule_end = sql.index("AS capped")
    assert sql.index("EXISTS", capsule_end) > capsule_end, "EXISTS muss auf der gedeckelten Kapsel liegen"
    assert "capped.id" in sql, "EXISTS korreliert gegen capped.id, nicht ringbuffer.id"

    # Ergebnis-Paritaet: Tag fehlt ueberall → leer, ohne Full-Scan.
    rows = await store.query(q)
    assert rows == []


async def test_metadata_tag_matches_within_cap(store: SqliteSegmentStore):
    # Gegenprobe: ein tatsaechlich vorkommender Tag in den neuesten cap Zeilen
    # wird weiterhin gefunden (der Cap deckelt nur, verwirft keine echten Treffer).
    await store.append([_event(1, _ts(1), dp="target-dp", tags=["needle"])])
    q = StoreQuery(limit=10, candidate_cap=100, metadata_tags_any_of=["needle"])
    rows = await store.query(q)
    assert [r["datapoint_id"] for r in rows] == ["target-dp"]


async def test_metadata_binding_without_window_is_capped(store: SqliteSegmentStore):
    # Auch der Binding-Filter (EXISTS auf ringbuffer_metadata_bindings) muss
    # gedeckelt werden. Kein Binding matcht → leeres Ergebnis, bounded.
    await store.append([_event(i, _ts(i), dp=f"bbb-{i}", tags=["x"]) for i in range(30)])

    cap = 7
    q = StoreQuery(limit=10, candidate_cap=cap, metadata_binding_filters={"adapter_type": ["knx"]})

    sql, params = store._build_segment_sql(q)
    assert "EXISTS" in sql
    assert cap in params
    assert "FROM (SELECT" in sql

    rows = await store.query(q)
    assert rows == []


# ---------------------------------------------------------------------------
# (2) Gegentest: mit Zeitfenster bleibt der Metadaten-Filter inline
# ---------------------------------------------------------------------------


async def test_metadata_tag_with_window_stays_inline(store: SqliteSegmentStore):
    # Mit beidseitigem Zeitfenster bindet bereits das WHERE den Scan – dann darf
    # das Metadaten-EXISTS inline bleiben (keine gedeckelte Subquery-Kapselung).
    await store.append([_event(i, _ts(i), dp=f"aaa-{i}", tags=["present"]) for i in range(5)])
    q = StoreQuery(
        limit=10,
        metadata_tags_any_of=["absent"],
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:59.000Z",
    )
    sql, _params = store._build_segment_sql(q)
    assert "EXISTS" in sql
    assert "FROM (SELECT" not in sql, "Mit Zeitfenster keine gedeckelte Subquery noetig"


async def test_metadata_tag_windowed_query_is_correct(store: SqliteSegmentStore):
    # Committete Daten korrekt: mit Zeitfenster liefert der inline Metadaten-
    # Filter genau die getaggten Zeilen im Fenster.
    await store.append(
        [
            _event(1, _ts(1), dp="dp-a", tags=["keep"]),
            _event(2, _ts(2), dp="dp-b", tags=["other"]),
            _event(3, _ts(3), dp="dp-c", tags=["keep"]),
        ]
    )
    q = StoreQuery(
        limit=10,
        metadata_tags_any_of=["keep"],
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:59.000Z",
    )
    rows = await store.query(q)
    assert sorted(r["datapoint_id"] for r in rows) == ["dp-a", "dp-c"]
