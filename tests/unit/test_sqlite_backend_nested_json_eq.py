"""Nested-JSON-``eq``/``ne``: Python-Wert-Parität statt JSON-String-Vergleich (#951, Codex :393).

Für ``eq``/``ne`` mit einem LIST/OBJECT-Filterwert muss der segmentierte v2-Pushdown
dieselbe Ergebnismenge liefern wie der verbindliche Legacy-Pfad
(``_matches_value_filter`` in ``obs/ringbuffer/ringbuffer.py``): reine Python-Gleichheit
``actual == expected``. Python behandelt verschachteltes ``True == 1`` und ``1 == 1.0``
(rekursiv in Listen/Dicts) als gleich, während ein kanonischer JSON-STRING-Vergleich sie
als verschiedene Tokens rendert (``true`` vs ``1``, ``1`` vs ``1.0``). Der frühere
String-Vergleich ließ deshalb bei ``eq`` Zeilen weg bzw. nahm sie bei ``ne`` fälschlich
auf; die dekodierten Python-Werte müssen verglichen werden.

Gegentests: skalare eq/ne unverändert; nested Werte, die WIRKLICH ungleich sind, matchen
weiterhin korrekt nicht; Dict-Key-Reihenfolge bleibt irrelevant; Legacy==segmentiert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer, RingBufferEntry, _apply_value_filters
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore, _obs_json_eq_impl


def _event(value: Any, ts: str, *, dp: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata={},
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _legacy_reference(values: list[Any], value_filters: list[dict[str, Any]]) -> list[Any]:
    """Ergebnisliste des verbindlichen Legacy-Filters für ``new_value`` in ``values``."""
    entries = [
        RingBufferEntry(
            id=i,
            ts=f"2026-01-01T00:00:{i:02d}.000Z",
            datapoint_id="dp-1",
            topic="dp/dp-1/value",
            old_value=None,
            new_value=v,
            source_adapter="api",
            quality="good",
            metadata_version=1,
            metadata={},
        )
        for i, v in enumerate(values)
    ]
    filtered = await _apply_value_filters(entries=entries, value_filters=value_filters, datapoint_types={})
    return [e.new_value for e in filtered]


async def _build_legacy_store(tmp_path: Path, values: list[Any]) -> SqliteSegmentStore:
    """Baut einen Store, in den eine echte Legacy-Single-DB (v1) read-only eingehängt ist."""
    db = tmp_path / "obs_ringbuffer.db"
    rb = RingBuffer(storage="disk", disk_path=str(db), max_entries=None)
    await rb.start()
    try:
        for i, v in enumerate(values):
            await rb.record(
                ts=f"2026-01-01T00:00:{i:02d}.000Z",
                datapoint_id="dp-1",
                topic="dp/dp-1/value",
                old_value=None,
                new_value=v,
                source_adapter="api",
                quality="good",
            )
    finally:
        await rb.stop()
    s = SqliteSegmentStore(tmp_path / "legacy_root")
    await s.open()
    await LegacyMigrator(s, db).attach_readonly(LegacyMigrator(s, db).classify())
    return s


def _matched_values(rows: list[dict[str, Any]]) -> list[Any]:
    return [r["new_value"] for r in rows]


# ---------------------------------------------------------------------------
# eq: nested numerisch/bool-äquivalente Werte müssen matchen (Python ==)
# ---------------------------------------------------------------------------


async def test_eq_nested_list_int_float_equivalence(store: SqliteSegmentStore, tmp_path: Path):
    # Gespeichert [1.0, 1]; Filter [1, True]. Python: [1, True] == [1.0, 1] → True
    # (True==1, 1==1.0). Der kanonische JSON-String (``[1, true]`` vs ``[1.0, 1]``)
    # matchte hier fälschlich NICHT.
    values: list[Any] = [[1.0, 1], [1, 2], "x"]
    vf = [{"operator": "eq", "value": [1, True]}]

    # Legacy-Referenz: genau die äquivalente Liste.
    expected = await _legacy_reference(values, vf)
    assert expected == [[1.0, 1]]

    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2 = await store.query(StoreQuery(limit=50, value_filters=vf))
    assert _matched_values(v2) == [[1.0, 1]]

    # Legacy-Segment (read-only attached) liefert dasselbe.
    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        legacy = await legacy_store.query(StoreQuery(limit=50, value_filters=vf))
        assert _matched_values(legacy) == expected
    finally:
        await legacy_store.close()


async def test_eq_nested_object_int_float_equivalence(store: SqliteSegmentStore, tmp_path: Path):
    # Gespeichert {"a": 1}; Filter {"a": 1.0}. Python: {"a": 1.0} == {"a": 1} → True.
    values: list[Any] = [{"a": 1}, {"a": 2}, {"b": 1}]
    vf = [{"operator": "eq", "value": {"a": 1.0}}]

    expected = await _legacy_reference(values, vf)
    assert expected == [{"a": 1}]

    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2 = await store.query(StoreQuery(limit=50, value_filters=vf))
    assert _matched_values(v2) == [{"a": 1}]

    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        legacy = await legacy_store.query(StoreQuery(limit=50, value_filters=vf))
        assert _matched_values(legacy) == expected
    finally:
        await legacy_store.close()


async def test_eq_nested_object_bool_int_equivalence(store: SqliteSegmentStore, tmp_path: Path):
    # Gespeichert {"a": true}; Filter {"a": 1}. Python: {"a": 1} == {"a": True} → True.
    values: list[Any] = [{"a": True}, {"a": False}, {"a": 2}]
    vf = [{"operator": "eq", "value": {"a": 1}}]

    expected = await _legacy_reference(values, vf)
    assert expected == [{"a": True}]

    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2 = await store.query(StoreQuery(limit=50, value_filters=vf))
    assert _matched_values(v2) == [{"a": True}]


# ---------------------------------------------------------------------------
# ne: invertiert – die äquivalente Zeile wird ausgeschlossen, der Rest matcht
# ---------------------------------------------------------------------------


async def test_ne_nested_list_int_float_equivalence(store: SqliteSegmentStore, tmp_path: Path):
    # ne [1, True] schließt die äquivalente [1.0, 1] aus; alle anderen (inkl. null,
    # anderer Typ) matchen – Legacy ``actual != expected``.
    values: list[Any] = [[1.0, 1], [1, 2], "x", None]
    vf = [{"operator": "ne", "value": [1, True]}]

    expected = await _legacy_reference(values, vf)
    assert [1.0, 1] not in expected
    assert expected == [[1, 2], "x", None]

    # v2 sortiert per Default ``id DESC`` (neueste zuerst) → Reihenfolge-unabhängig
    # gegen die Legacy-Ergebnismenge prüfen (Werte enthalten unhashbare Listen).
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2 = await store.query(StoreQuery(limit=50, value_filters=vf))
    got = _matched_values(v2)
    assert [1.0, 1] not in got
    assert all(v in got for v in expected) and len(got) == len(expected)

    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        legacy = await legacy_store.query(StoreQuery(limit=50, value_filters=vf))
        legacy_got = _matched_values(legacy)
        assert [1.0, 1] not in legacy_got
        assert all(v in legacy_got for v in expected) and len(legacy_got) == len(expected)
    finally:
        await legacy_store.close()


# ---------------------------------------------------------------------------
# Gegentests: echt ungleiche nested Werte matchen NICHT; Skalare unverändert
# ---------------------------------------------------------------------------


async def test_eq_nested_list_truly_unequal_does_not_match(store: SqliteSegmentStore):
    # [1, 2] vs [1, 3]: keine numerisch/bool-Äquivalenz, echt ungleich → kein Treffer.
    values: list[Any] = [[1, 3], [2, 3]]
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2 = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": [1, 2]}]))
    assert _matched_values(v2) == []


async def test_eq_nested_list_order_still_matters(store: SqliteSegmentStore):
    # Listen bleiben ordnungsempfindlich (Referenz): [1, 2] != [2, 1].
    await store.append([_event([2, 1], "2026-01-01T00:00:00.000Z")])
    v2 = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": [1, 2]}]))
    assert _matched_values(v2) == []


async def test_eq_nested_object_key_order_irrelevant(store: SqliteSegmentStore):
    # Dicts bleiben key-order-unabhängig (Python-== ist es von Natur aus).
    await store.append([_event({"a": 1, "b": 2}, "2026-01-01T00:00:00.000Z")])
    v2 = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": {"b": 2, "a": 1}}]))
    assert _matched_values(v2) == [{"a": 1, "b": 2}]


async def test_scalar_eq_ne_unchanged(store: SqliteSegmentStore):
    # Skalare eq/ne dürfen sich durch den nested-Fix NICHT ändern.
    values: list[Any] = [5, 5.0, "5", True, None]
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])

    # eq 5 matcht numerische 5 UND 5.0 (Python 5 == 5.0), nicht "5"/True/None.
    eq5 = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": 5}]))
    assert len(eq5) == 2  # 5 und 5.0 kollabieren im Set, bleiben aber zwei Zeilen

    # eq "5" matcht nur den Text.
    eq_text = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": "5"}]))
    assert _matched_values(eq_text) == ["5"]

    # ne "5" schließt nur den Text aus.
    ne_text = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "ne", "value": "5"}]))
    assert "5" not in _matched_values(ne_text)
    assert len(ne_text) == 4


# ---------------------------------------------------------------------------
# Direkter Callback-Test: Python-Wert-Vergleich statt JSON-String
# ---------------------------------------------------------------------------


def test_obs_json_eq_impl_uses_python_value_equality():
    from obs.core.json import json_dumps

    # [1.0, 1] == [1, True] als Python-Werte → 1, obwohl die JSON-Strings differieren.
    expected = json_dumps([1, True])
    assert _obs_json_eq_impl("[1.0, 1]", expected) == 1
    assert _obs_json_eq_impl("[1, 2]", expected) == 0

    # {"a": 1} == {"a": 1.0} als Python-Werte → 1.
    obj_expected = json_dumps({"a": 1.0})
    assert _obs_json_eq_impl('{"a": 1}', obj_expected) == 1
    assert _obs_json_eq_impl('{"a": 2}', obj_expected) == 0

    # Defensiv: non-str/malformed Spalten matchen nie.
    assert _obs_json_eq_impl(None, expected) == 0
    assert _obs_json_eq_impl("definitely not json", expected) == 0
