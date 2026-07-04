"""Codex-Runde-36 [P2]-Findings am ``sqlite_backend.py`` (#951).

Zwei Findings am realen Read-Pfad (``store.query`` / ``_build_segment_sql``), jeweils
gegen den Legacy-``query_v2``-OR-Referenzpfad (un-capped, indiziert) geprüft:

* **F2 (:2262)** – ist ``q`` gesetzt, darf der index-taugliche
  ``dp_ids_by_name``-``IN``-Arm NICHT durch den gedeckelten leading-wildcard-Scan
  laufen: eine per Namen gematchte Zeile jenseits des Caps muss geliefert werden
  (Parität zur Legacy-OR-Query). Nur die ``LIKE``-Arme bleiben gedeckelt.
* **F3 (:2441)** – eine einseitige Zeitgrenze (nur ``from_ts`` ODER nur ``to_ts``)
  bindet den Scan bereits; die Prädikate laufen ts-gebunden inline statt gecapped.
  Ein reiner unbounded Scope (keine ts-Grenze) bleibt gedeckelt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _ts(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


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


# ---------------------------------------------------------------------------
# F2 – Name-Treffer (dp_ids_by_name / IN-Arm) aus dem Cap herauslösen (v2)
# ---------------------------------------------------------------------------


async def test_f2_name_hit_older_than_cap_is_returned(store: SqliteSegmentStore):
    """Unwindowed Monitor-Query, ``q`` matcht per NAME, Zeilen älter als der Cap.

    ``dp-target`` liegt als ÄLTESTE Zeile. 30 neuere ``noise``-Zeilen (matchen
    ``q='searchterm'`` per LIKE NICHT) folgen. Mit ``candidate_cap=5`` fällt
    ``dp-target`` aus den neuesten 5 Roh-Zeilen. Weil der ``dp_ids_by_name``-
    ``IN``-Arm indizierbar ist, muss die Zeile trotzdem kommen (Parität zur
    un-capped Legacy-OR-Query).
    """
    await store.append([_event(999, _ts(0), dp="dp-target", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    query = StoreQuery(q="searchterm", dp_ids_by_name=["dp-target"], candidate_cap=5, limit=50)
    result = await store.query(query)
    values = {r["new_value"] for r in result}
    assert 999 in values, "per Namen gematchte Zeile muss un-capped/indiziert geliefert werden"


async def test_f2_name_hit_in_arm_stays_out_of_capped_subquery(store: SqliteSegmentStore):
    """Der ``dp_ids_by_name``-``IN``-Arm steht im inline-WHERE, nicht in der Cap-Subquery."""
    q = StoreQuery(q="zzz", dp_ids_by_name=["dp-a", "dp-b"], candidate_cap=5, limit=10)
    sql, _params = store._build_segment_sql(q)
    # Der leading-wildcard-LIKE bleibt gedeckelt (Cap-Subquery vorhanden) …
    assert "FROM (SELECT" in sql, "der LIKE-Arm bleibt gedeckelt"
    # … aber der IN-Arm gehört ins BASIS-WHERE (vor der ``... AS capped``-Kapsel).
    head = sql.split("AS capped", 1)[0]
    assert "datapoint_id IN (" in head, "der index-taugliche IN-Arm muss un-capped im Basis-WHERE stehen"


async def test_f2_leading_wildcard_like_stays_capped(store: SqliteSegmentStore):
    """Gegentest: der reine leading-wildcard-``LIKE``-Arm bleibt gedeckelt.

    Ein per LIKE matchender Treffer jenseits des Caps wird NICHT gefunden
    (dokumentierte Deckelung des unwindowed LIKE-Scans). Kein ``dp_ids_by_name``.
    """
    await store.append([_event(999, _ts(0), dp="oldmatch", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    query = StoreQuery(q="oldmatch", candidate_cap=5, limit=50)
    result = await store.query(query)
    values = {r["new_value"] for r in result}
    assert 999 not in values, "leading-wildcard-LIKE-Treffer jenseits des Caps bleibt gedeckelt"


# ---------------------------------------------------------------------------
# F3 – einseitige Zeitgrenze bindet den Scan (nicht cappen) (v2)
# ---------------------------------------------------------------------------


async def test_f3_one_sided_from_ts_binds_scan(store: SqliteSegmentStore):
    """Nur ``from_ts`` gesetzt (last-24h-artig): ts-gebunden inline, nicht gecapped.

    ``dp-hit`` (matcht ``q='hit'``) liegt am Fensteranfang und ist die ÄLTESTE
    Zeile. 30 neuere Zeilen füllen den Cap. Mit ``candidate_cap=5`` würde ein
    unbounded Scope die ``dp-hit``-Zeile verlieren – die ``from_ts``-Grenze bindet
    den Scan aber, also inline und vollständig (Parität zur Legacy-OR-Query).
    """
    await store.append([_event(999, _ts(0), dp="dp-hit-old", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    query = StoreQuery(q="hit", from_ts=_ts(0), candidate_cap=5, limit=50)
    result = await store.query(query)
    values = {r["new_value"] for r in result}
    assert 999 in values, "einseitige from_ts-Grenze bindet den Scan → Treffer inline gefunden"


async def test_f3_one_sided_window_has_no_capped_subquery(store: SqliteSegmentStore):
    """Mit einseitiger ``from_ts``-Grenze kapselt der Segment-SQL KEINE Cap-Subquery."""
    q = StoreQuery(q="zzz", from_ts=_ts(0), candidate_cap=5, limit=10)
    sql, _params = store._build_segment_sql(q)
    assert "FROM (SELECT" not in sql, "einseitige ts-Grenze bindet den Scan → keine Cap-Subquery"


async def test_f3_no_ts_bound_stays_capped(store: SqliteSegmentStore):
    """Gegentest: gar keine ts-Grenze → weiterhin gedeckelt.

    Gleiche Datenlage wie F3, aber OHNE ``from_ts``. Der unbounded q-Scan bleibt
    auf die neuesten ``candidate_cap`` Roh-Zeilen begrenzt.
    """
    await store.append([_event(999, _ts(0), dp="dp-hit-old", src="other")])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other") for i in range(30)])

    query = StoreQuery(q="hit", candidate_cap=5, limit=50)
    result = await store.query(query)
    values = {r["new_value"] for r in result}
    assert 999 not in values, "unbounded q-Scan bleibt gedeckelt (keine ts-Grenze)"
