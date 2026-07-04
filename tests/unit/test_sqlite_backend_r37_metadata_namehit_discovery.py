"""Codex-Runde-37 [P2]-Finding am ``sqlite_backend.py`` (#951, :1566).

„Mirror name-hit widening in metadata discovery" – Follow-up auf die Runde-36-
:2262-Zerlegung. Der echte Read-Pfad (``_build_segment_sql``) hebt den index-
tauglichen ``dp_ids_by_name``-``IN``-Arm per ``UNION`` UN-CAPPED aus der Cap-
Subquery heraus. Die Metadaten-Discovery (``_distinct_ids_capped_candidates``,
Discovery-Quelle für einen UNWINDOWED metadaten-scoped Value-Filter) tat das
NICHT: sie legte den ganzen Freitext-OR-Block (LIKE + IN) auf die gedeckelte
Kandidatenmenge. Ein älterer, per NAME erreichbarer metadaten-matchender
STRING/BOOLEAN/gelöschter Datapoint, dessen Zeilen ÄLTER als der ``candidate_cap``
sind, fehlte daher in ``candidate_ids`` → der numerische Pushdown wurde erlaubt
und die inkompatiblen Zeilen still gedroppt, statt das Legacy-422 zu werfen.

Der Fix spiegelt das :2262-Widening: der ``IN``-Arm wird auch in der Discovery
un-capped in die Kandidatenmenge gehoben (und im Metadaten-Scope validiert),
sodass so ein älterer inkompatibler Datapoint erfasst wird (→ 422-Parität).
Bounded-ness bleibt: nur die leading-wildcard-``LIKE``-Arme bleiben gedeckelt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _ts(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


def _event(value: Any, ts: str, *, dp: str = "dp-1", src: str = "api", tags: list[str] | None = None) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
        new_value=value,
        source_adapter=src,
        quality="good",
        metadata={"datapoint": {"tags": tags or []}},
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
# Kernfall – Metadaten-Discovery erfasst den un-capped Name-Treffer
# ---------------------------------------------------------------------------


async def test_metadata_discovery_captures_name_hit_older_than_cap(store: SqliteSegmentStore):
    """Unwindowed metadata-scoped Value-Filter + ``q``-Name-Treffer auf einen älteren
    STRING-Datapoint, dessen Zeilen ÄLTER als der ``candidate_cap`` sind.

    ``dp-target`` (STRING-Wert, tagged ``room``) liegt als ÄLTESTE Zeile; 30 neuere
    ``noise``-Zeilen (ebenfalls tagged ``room``, aber weder per LIKE noch per Namen
    getroffen) füllen den Cap. Mit ``candidate_cap=5`` fällt ``dp-target`` aus den
    neuesten 5 Roh-Zeilen. Weil der ``dp_ids_by_name``-``IN``-Arm indizierbar ist
    (kein LIKE-Scan), MUSS die Metadaten-Discovery ihn un-capped erfassen – sonst
    fehlt der inkompatible Datapoint in ``candidate_ids`` und der numerische
    Pushdown droppt seine STRING-Zeilen still statt das 422 zu werfen.
    """
    await store.append([_event("hello", _ts(0), dp="dp-target", src="other", tags=["room"])])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other", tags=["room"]) for i in range(30)])

    query = StoreQuery(
        q="searchterm",
        dp_ids_by_name=["dp-target"],
        metadata_tags_any_of=["room"],
        value_filters=[{"field": "new_value", "operator": "gt", "value": 5}],
        candidate_cap=5,
        limit=50,
    )
    ids = await store.distinct_datapoint_ids(query)
    assert "dp-target" in ids, "per Namen erreichbarer metadaten-matchender Datapoint muss un-capped in die Discovery"


async def test_metadata_discovery_name_hit_between_filter(store: SqliteSegmentStore):
    """Gleicher Kernfall mit ``between`` statt ``gt`` – auch hier muss der Name-Treffer erfasst werden."""
    await store.append([_event(True, _ts(0), dp="dp-bool", src="other", tags=["room"])])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other", tags=["room"]) for i in range(30)])

    query = StoreQuery(
        q="searchterm",
        dp_ids_by_name=["dp-bool"],
        metadata_tags_any_of=["room"],
        value_filters=[{"field": "new_value", "operator": "between", "value": [1, 10]}],
        candidate_cap=5,
        limit=50,
    )
    ids = await store.distinct_datapoint_ids(query)
    assert "dp-bool" in ids, "BOOLEAN-Datapoint via un-capped Name-Treffer muss im Metadaten-Scope erfasst werden"


# ---------------------------------------------------------------------------
# Gegentests – Bounded-ness + kein falsches 422
# ---------------------------------------------------------------------------


async def test_metadata_discovery_pure_numeric_no_name_hit_stays_capped(store: SqliteSegmentStore):
    """Kein Name-Treffer (rein numerischer/metadaten-Scope) → älterer Datapoint bleibt gedeckelt.

    ``dp-old`` liegt jenseits des Caps und ist NICHT per Namen erreichbar. Ohne
    ``dp_ids_by_name`` bleibt die Metadaten-Discovery auf die neuesten
    ``candidate_cap`` Roh-Zeilen begrenzt (dokumentierter Cap-Kompromiss) – die
    Bounded-ness bleibt erhalten, kein Full-History-Scan.
    """
    await store.append([_event(1, _ts(0), dp="dp-old", src="other", tags=["room"])])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other", tags=["room"]) for i in range(30)])

    query = StoreQuery(
        metadata_tags_any_of=["room"],
        value_filters=[{"field": "new_value", "operator": "gt", "value": 5}],
        candidate_cap=5,
        limit=50,
    )
    ids = await store.distinct_datapoint_ids(query)
    assert "dp-old" not in ids, "ohne Name-Treffer bleibt der Metadaten-Scan gedeckelt (Bounded-ness)"


async def test_metadata_discovery_leading_wildcard_like_stays_capped(store: SqliteSegmentStore):
    """Leading-wildcard-``LIKE``-Treffer jenseits des Caps bleibt in der Discovery gedeckelt.

    ``oldmatch`` (matcht ``q='oldmatch'`` NUR per LIKE, kein ``dp_ids_by_name``)
    liegt jenseits des Caps. Der LIKE-Arm bleibt gedeckelt (nur der IN-Arm wird
    un-capped gehoben) → der Datapoint wird NICHT erfasst.
    """
    await store.append([_event("x", _ts(0), dp="oldmatch", src="other", tags=["room"])])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other", tags=["room"]) for i in range(30)])

    query = StoreQuery(
        q="oldmatch",
        metadata_tags_any_of=["room"],
        value_filters=[{"field": "new_value", "operator": "gt", "value": 5}],
        candidate_cap=5,
        limit=50,
    )
    ids = await store.distinct_datapoint_ids(query)
    assert "oldmatch" not in ids, "leading-wildcard-LIKE bleibt gedeckelt (nur der IN-Arm wird un-capped gehoben)"


async def test_metadata_discovery_name_hit_respects_metadata_scope(store: SqliteSegmentStore):
    """Ein Name-Treffer OHNE passendes Metadaten-Tag bleibt außerhalb des Metadaten-Scopes.

    ``dp-untagged`` ist per Namen erreichbar, trägt aber NICHT das gefilterte Tag.
    Der un-capped IN-Arm hebt die Zeile zwar in die Kandidatenmenge, doch das
    Metadaten-``EXISTS`` filtert sie wieder heraus – kein falsches Erfassen eines
    out-of-scope Datapoints (Parität zum Metadaten-Scope des echten Read-Pfads).
    """
    await store.append([_event("hello", _ts(0), dp="dp-untagged", src="other", tags=["other-tag"])])
    await store.append([_event(i, _ts(i + 1), dp=f"noise-{i}", src="other", tags=["room"]) for i in range(30)])

    query = StoreQuery(
        q="searchterm",
        dp_ids_by_name=["dp-untagged"],
        metadata_tags_any_of=["room"],
        value_filters=[{"field": "new_value", "operator": "gt", "value": 5}],
        candidate_cap=5,
        limit=50,
    )
    ids = await store.distinct_datapoint_ids(query)
    assert "dp-untagged" not in ids, "Name-Treffer außerhalb des Metadaten-Scopes darf nicht miterfasst werden"
