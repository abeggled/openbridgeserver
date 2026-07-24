"""Value-Filter-Parität: segmentierter v2-Pushdown == Legacy-Semantik (#919, Review #951).

Für jeden der sechs am PR #951 gemeldeten Paritäts-Bugs ein Test, der die
segmentierte Umsetzung (``SqliteSegmentStore`` mit typisiertem SQL-Pushdown bzw.
Legacy-Python-Fallback) gegen die verbindliche Legacy-Semantik in
``obs/ringbuffer/ringbuffer.py`` (``_apply_value_filters``) prüft.

Vorgehen (soweit möglich): DIESELBEN Daten in ein v2-Segment (``store.append``)
UND — über eine read-only eingehängte Legacy-Single-DB — in ein Legacy-Segment
laden, DENSELBEN Filter fahren und die Ergebnismenge vergleichen. So belegt der
Test direkt „v2-Pushdown liefert dasselbe wie der Legacy-Pfad".

1. ``ne`` cross-type matcht Zeilen anderen Typs/null.
2. ``contains`` ist bei ``ignore_case=false`` echt case-sensitiv.
3. Range-Operatoren auf text/bool werden wie Legacy abgelehnt.
4. ``eq``/``ne`` mit ``value: null``.
5. Multi-Column-Binding-Filter: EINE Binding-Zeile muss ALLE Spalten erfüllen.
6. Langer Zielstring / pathologisches Muster bleibt gebounded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer, RingBufferEntry, _apply_value_filters
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: Any, ts: str, *, dp: str = "dp-1", old: Any = None, metadata: dict | None = None) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=old,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata=metadata or {},
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _legacy_reference(values: list[Any], value_filters: list[dict[str, Any]], data_type: str = "") -> set[Any]:
    """Ergebnismenge des verbindlichen Legacy-Filters für ``new_value`` in ``values``."""
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
    datapoint_types = {"dp-1": data_type} if data_type else {}
    filtered = await _apply_value_filters(entries=entries, value_filters=value_filters, datapoint_types=datapoint_types)
    return {e.new_value for e in filtered}


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
    # Eigener Root, damit die WriterLease nicht mit dem ``store``-Fixture-Root kollidiert.
    s = SqliteSegmentStore(tmp_path / "legacy_root")
    await s.open()
    await LegacyMigrator(s, db).attach_readonly(LegacyMigrator(s, db).classify())
    return s


# ---------------------------------------------------------------------------
# 1) ne cross-type: matcht Zeilen anderen Typs und null (Legacy: value != expected)
# ---------------------------------------------------------------------------


async def test_ne_matches_cross_type_and_null(store: SqliteSegmentStore, tmp_path: Path):
    values = [5, 10, "5", "x", True, False, None]
    vf = [{"operator": "ne", "value": 5}]

    # Legacy-Referenz: alles außer der numerischen 5.
    expected = await _legacy_reference(values, vf)
    assert expected == {10, "5", "x", True, False, None}

    # v2-Segment-Pushdown.
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2_rows = await store.query(StoreQuery(limit=50, value_filters=vf))
    assert {r["new_value"] for r in v2_rows} == expected

    # Legacy-Segment (Python-Fallback) muss dasselbe liefern.
    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        legacy_rows = await legacy_store.query(StoreQuery(limit=50, value_filters=vf))
        assert {r["new_value"] for r in legacy_rows} == expected
    finally:
        await legacy_store.close()


async def test_ne_text_and_bool_match_cross_type(store: SqliteSegmentStore):
    # ne mit text-Wert bzw. bool-Wert schließt ebenfalls nur die typgleiche,
    # exakt gleiche Zeile aus; alle anderen (inkl. null/anderer Typ) matchen.
    values = ["on", "off", 1, True, None]
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])

    ne_text = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "ne", "value": "on"}]))
    assert {r["new_value"] for r in ne_text} == {"off", 1, None}
    # Legacy-Referenz bestätigt.
    assert await _legacy_reference(values, [{"operator": "ne", "value": "on"}]) == {"off", 1, None}

    # Python-Äquivalenz True==1: ``ne True`` schließt sowohl bool True als auch die
    # numerische 1 aus (Legacy ``not (row == True)``).
    ne_bool = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "ne", "value": True}]))
    assert {r["new_value"] for r in ne_bool} == {"on", "off", None}
    assert await _legacy_reference(values, [{"operator": "ne", "value": True}]) == {"on", "off", None}


async def test_eq_bool_int_equivalence(store: SqliteSegmentStore, tmp_path: Path):
    # Legacy nutzt reine Python-Gleichheit → True==1, False==0. Ein bool-Filter
    # matcht die numerische 0/1-Zeile und umgekehrt.
    values = [1, 0, True, False, 2, "1"]
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])

    # eq True matcht bool True UND numerische 1 (nicht "1", nicht 2).
    eq_true = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": True}]))
    assert {r["new_value"] for r in eq_true} == {1}  # als Set kollabieren 1/True
    assert len(eq_true) == 2

    # eq 1 (numerisch) matcht ebenfalls bool True UND numerische 1.
    eq_one = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": 1}]))
    assert len(eq_one) == 2

    # eq 0 matcht numerische 0 UND bool False.
    eq_zero = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": 0}]))
    assert len(eq_zero) == 2

    # Legacy-Referenz-Parität (Anzahl, da Set 1/True kollabiert).
    ref_true = [v for v in values if v == True]
    assert len(ref_true) == 2


# ---------------------------------------------------------------------------
# 2) contains ist bei ignore_case=false echt case-sensitiv (Legacy: Python-Substring)
# ---------------------------------------------------------------------------


async def test_contains_is_case_sensitive_by_default(store: SqliteSegmentStore, tmp_path: Path):
    values = ["Hello world", "hello there", "HELLO"]
    vf = [{"operator": "contains", "value": "hello"}]

    # Legacy-Referenz (STRING): nur das kleingeschriebene "hello there".
    expected = await _legacy_reference(values, vf, data_type="STRING")
    assert expected == {"hello there"}

    window = {"from_ts": "2026-01-01T00:00:00.000Z", "to_ts": "2026-01-01T01:00:00.000Z"}
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2_rows = await store.query(StoreQuery(limit=50, value_filters=vf, **window))
    assert {r["new_value"] for r in v2_rows} == expected

    # ignore_case=true matcht alle drei.
    vf_ci = [{"operator": "contains", "value": "hello", "ignore_case": True}]
    v2_ci = await store.query(StoreQuery(limit=50, value_filters=vf_ci, **window))
    assert {r["new_value"] for r in v2_ci} == set(values)


# ---------------------------------------------------------------------------
# 3) Range-Operatoren auf text/bool werden wie Legacy abgelehnt
# ---------------------------------------------------------------------------


async def test_range_operator_on_text_value_rejected(store: SqliteSegmentStore):
    await store.append([_event("abc", "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="STRING"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "value": "abc"}]))


async def test_range_operator_on_bool_value_rejected(store: SqliteSegmentStore):
    await store.append([_event(True, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="BOOLEAN"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gte", "value": True}]))


async def test_range_operator_on_string_datapoint_rejected_legacy():
    # Legacy lehnt gt auf STRING-Datenpunkt ab.
    with pytest.raises(ValueError, match="STRING"):
        await _legacy_reference(["abc"], [{"operator": "gt", "value": "abc"}], data_type="STRING")


async def test_range_operator_on_null_value_rejected(store: SqliteSegmentStore):
    # gt mit value=null ist sinnlos → wie Legacy abgelehnt (kein stiller 0-Match).
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    with pytest.raises(ValueError, match="null"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "value": None}]))


def test_legacy_compare_range_cross_type_returns_false():
    # Direkte Abdeckung der Range-Typregeln im Legacy-Vergleich (Parität zum
    # v2-Pushdown, #951 Codex :439):
    #  * Range gegen einen STRING/BOOLEAN-Vergleichswert wird abgelehnt (422-tauglich),
    #    statt lexikografisch/0-1 zu degradieren.
    #  * Ein numerischer Vergleichswert gegen eine nicht-numerische Zeile matcht nie.
    from obs.ringbuffer.store.sqlite_backend import _legacy_compare

    with pytest.raises(ValueError, match="BOOLEAN"):
        _legacy_compare("gt", 5, True)  # bool expected → abgelehnt
    with pytest.raises(ValueError, match="STRING"):
        _legacy_compare("lt", 5, "abc")  # str expected → abgelehnt
    assert _legacy_compare("gte", "abc", 5) is False  # numeric expected, str actual → kein Treffer
    # eq/ne bleiben reine Python-Gleichheit (typübergreifend, kein Kurzschluss).
    assert _legacy_compare("ne", "abc", 5) is True
    assert _legacy_compare("eq", 1, True) is True


def test_legacy_compare_range_on_null_and_complex_value_rejected():
    # Parität zum v2-Pushdown (#951, Codex :467): ein Range-Operator gegen einen
    # ``null``- oder komplexen (list/dict) Vergleichswert ist bedeutungslos und muss
    # als 422-tauglicher ValueError abgelehnt werden, BEVOR Pythons Roh-Vergleich
    # (``actual > expected``) mit einem TypeError durchfällt (der nicht in den
    # 422-Pfad konvertiert würde). null spiegelt die v2-Meldung ("null value"),
    # list/dict die STRING-Ablehnung.
    from obs.ringbuffer.store.sqlite_backend import _legacy_compare

    for op in ("gt", "gte", "lt", "lte"):
        with pytest.raises(ValueError, match="null"):
            _legacy_compare(op, 5, None)  # null expected → abgelehnt
        with pytest.raises(ValueError, match="STRING"):
            _legacy_compare(op, 5, [1, 2])  # list expected → abgelehnt
        with pytest.raises(ValueError, match="STRING"):
            _legacy_compare(op, 5, {"a": 1})  # dict expected → abgelehnt
    # eq/ne gegen null/komplex bleiben erlaubt (reine Python-Gleichheit).
    assert _legacy_compare("eq", 5, None) is False
    assert _legacy_compare("ne", 5, None) is True
    assert _legacy_compare("eq", [1, 2], [1, 2]) is True


async def test_legacy_range_filter_excludes_cross_type_rows(tmp_path: Path):
    # v1/Legacy-Fallback: gt (numerisch) über gemischte Werte matcht nur numerische
    # Zeilen > Schwelle; text-/bool-Zeilen (Cross-Typ) fallen wie in der Referenz raus.
    values = [5, 15, "text", True, 25]
    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        rows = await legacy_store.query(StoreQuery(limit=50, value_filters=[{"operator": "gt", "value": 10}]))
        assert {r["new_value"] for r in rows} == {15, 25}
    finally:
        await legacy_store.close()


# ---------------------------------------------------------------------------
# 4) eq/ne mit value: null (Legacy vergleicht direkt gegen None)
# ---------------------------------------------------------------------------


async def test_eq_null_and_ne_null(store: SqliteSegmentStore, tmp_path: Path):
    values = [1, "x", None, True, None]
    eq_null = [{"operator": "eq", "value": None}]
    ne_null = [{"operator": "ne", "value": None}]

    # Legacy-Referenz.
    exp_eq = await _legacy_reference(values, eq_null)
    exp_ne = await _legacy_reference(values, ne_null)
    assert exp_eq == {None}
    assert exp_ne == {1, "x"}

    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2_eq = await store.query(StoreQuery(limit=50, value_filters=eq_null))
    v2_ne = await store.query(StoreQuery(limit=50, value_filters=ne_null))
    assert {r["new_value"] for r in v2_eq} == exp_eq
    assert {r["new_value"] for r in v2_ne} == exp_ne

    # Legacy-Segment liefert dasselbe.
    legacy_store = await _build_legacy_store(tmp_path, values)
    try:
        l_eq = await legacy_store.query(StoreQuery(limit=50, value_filters=eq_null))
        l_ne = await legacy_store.query(StoreQuery(limit=50, value_filters=ne_null))
        assert {r["new_value"] for r in l_eq} == exp_eq
        assert {r["new_value"] for r in l_ne} == exp_ne
    finally:
        await legacy_store.close()


# ---------------------------------------------------------------------------
# 4b) eq/ne auf komplexen JSON-Werten (list/dict): kein 422, echtes Matching
# ---------------------------------------------------------------------------


async def test_eq_json_list_value_matches(store: SqliteSegmentStore, tmp_path: Path):
    # Legacy verglich Python-Werte direkt: eq [1,2,3] matcht genau die gleiche Liste.
    values: list[Any] = [[1, 2, 3], [1, 2], {"a": 1}, "x", None]
    vf = [{"operator": "eq", "value": [1, 2, 3]}]

    # Listen sind unhashbar → nicht über die Set-Projektion in ``_legacy_reference``,
    # sondern direkt über die Referenz ``_apply_value_filters`` prüfen. Ergebnis: nur [1,2,3].
    ref_entries = await _apply_value_filters(
        entries=[
            RingBufferEntry(
                id=i,
                ts=f"2026-01-01T00:00:{i:02d}.000Z",
                datapoint_id="dp-1",
                topic="t",
                old_value=None,
                new_value=v,
                source_adapter="api",
                quality="good",
                metadata_version=1,
                metadata={},
            )
            for i, v in enumerate(values)
        ],
        value_filters=vf,
        datapoint_types={},
    )
    assert [e.new_value for e in ref_entries] == [[1, 2, 3]]

    # v2-Segment: KEIN 422; genau die gleiche Liste matcht.
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    v2_rows = await store.query(StoreQuery(limit=50, value_filters=vf))
    assert [r["new_value"] for r in v2_rows] == [[1, 2, 3]]


async def test_eq_json_dict_matches_regardless_of_key_order(store: SqliteSegmentStore):
    # Gleiche Objekte (nur andere Key-Reihenfolge) müssen matchen – Legacy vergleicht
    # dekodierte Python-Dicts (order-unabhängig).
    await store.append(
        [
            _event({"a": 1, "b": 2}, "2026-01-01T00:00:00.000Z"),
            _event({"b": 9}, "2026-01-01T00:00:01.000Z"),
        ]
    )
    # Filter mit vertauschter Key-Reihenfolge muss trotzdem das erste Objekt treffen.
    rows = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "eq", "value": {"b": 2, "a": 1}}]))
    assert [r["new_value"] for r in rows] == [{"a": 1, "b": 2}]


def test_obs_json_eq_impl_defensive_branches():
    # Direkter Callback-Test: nicht-str/malformed Spaltenwerte matchen nie (0),
    # ein kanonisch gleicher Wert matcht (1). Deckt die defensiven Zweige ab.
    from obs.ringbuffer.store.sqlite_backend import _canonical_json, _obs_json_eq_impl

    expected = _canonical_json([1, 2, 3])
    assert _obs_json_eq_impl(None, expected) == 0  # nicht-str (z. B. NULL-Spalte)
    assert _obs_json_eq_impl("definitely not json", expected) == 0  # malformed
    assert _obs_json_eq_impl("[1, 2, 3]", expected) == 1  # kanonisch gleich
    assert _obs_json_eq_impl("[3, 2, 1]", expected) == 0  # Liste ordnungsempfindlich


async def test_ne_json_value_matches_cross_type_and_null(store: SqliteSegmentStore):
    # ne [1,2,3] schließt nur die exakt gleiche Liste aus; alles andere (inkl. anderer
    # Liste, Skalar, null) matcht – wie Legacy ``actual != expected``.
    values: list[Any] = [[1, 2, 3], [1, 2], 5, None]
    await store.append([_event(v, f"2026-01-01T00:00:{i:02d}.000Z") for i, v in enumerate(values)])
    rows = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "ne", "value": [1, 2, 3]}]))
    got = [r["new_value"] for r in rows]
    assert [1, 2, 3] not in got
    assert [1, 2] in got
    assert 5 in got
    assert None in got


# ---------------------------------------------------------------------------
# 5) Multi-Column-Binding-Filter: EINE Binding-Zeile muss ALLE Spalten erfüllen
# ---------------------------------------------------------------------------


def _binding(adapter_type: str, group_address: str) -> dict[str, Any]:
    # Bindings tragen ``group_address`` unter ``normalized`` (siehe
    # _extract_metadata_binding_index_rows), ``adapter_type`` auf Top-Level.
    return {"adapter_type": adapter_type, "normalized": {"group_address": group_address}}


# Zeile A: EIN Binding erfüllt adapter_type=knx UND group_address=1/2/3 (Treffer).
_ROW_A_BINDINGS = [_binding("knx", "1/2/3")]
# Zeile B: zwei GETRENNTE Bindings — eines mit adapter_type=knx, ein anderes mit
# group_address=1/2/3, aber KEINE einzelne Zeile erfüllt beides (kein Treffer).
_ROW_B_BINDINGS = [_binding("knx", "9/9/9"), _binding("modbus", "1/2/3")]


async def test_multi_column_binding_requires_single_row(store: SqliteSegmentStore, tmp_path: Path):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z", metadata={"bindings": _ROW_A_BINDINGS})])
    await store.append([_event(2, "2026-01-01T00:00:01.000Z", metadata={"bindings": _ROW_B_BINDINGS})])

    binding_filters = {"adapter_type": ["knx"], "group_address": ["1/2/3"]}
    window = {"from_ts": "2026-01-01T00:00:00.000Z", "to_ts": "2026-01-01T01:00:00.000Z"}
    v2_rows = await store.query(StoreQuery(limit=50, metadata_binding_filters=binding_filters, **window))
    assert {r["new_value"] for r in v2_rows} == {1}

    # Legacy-Segment (Python-Auswertung in _legacy_metadata_matches) muss dieselbe
    # „eine Zeile erfüllt alle Spalten"-Semantik liefern.
    db = tmp_path / "obs_ringbuffer.db"
    rb = RingBuffer(storage="disk", disk_path=str(db), max_entries=None)
    await rb.start()
    try:
        await rb.record(
            ts="2026-01-01T00:00:00.000Z",
            datapoint_id="dp-1",
            topic="t",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
            metadata={"bindings": _ROW_A_BINDINGS},
        )
        await rb.record(
            ts="2026-01-01T00:00:01.000Z",
            datapoint_id="dp-1",
            topic="t",
            old_value=None,
            new_value=2,
            source_adapter="api",
            quality="good",
            metadata={"bindings": _ROW_B_BINDINGS},
        )
    finally:
        await rb.stop()
    legacy_store = SqliteSegmentStore(tmp_path / "root2")
    await legacy_store.open()
    try:
        await LegacyMigrator(legacy_store, db).attach_readonly(LegacyMigrator(legacy_store, db).classify())
        legacy_rows = await legacy_store.query(StoreQuery(limit=50, metadata_binding_filters=binding_filters, **window))
        assert {r["new_value"] for r in legacy_rows} == {1}
    finally:
        await legacy_store.close()


def test_legacy_metadata_matches_unknown_binding_column_never_matches():
    # Defensiver Zweig: eine angefragte Binding-Spalte, die es im Index NICHT gibt,
    # kann nie erfüllt werden → False (kein stiller Treffer). ``_legacy_metadata_matches``
    # nutzt kein ``self`` → Aufruf mit ``None`` als Instanz genügt.
    query = StoreQuery(metadata_binding_filters={"does_not_exist": ["x"]})
    record = {"metadata": {"bindings": [_binding("knx", "1/2/3")]}}
    assert SqliteSegmentStore._legacy_metadata_matches(None, record, query) is False


# ---------------------------------------------------------------------------
# 6) Langer Zielstring / pathologisches Muster bleibt gebounded
# ---------------------------------------------------------------------------


async def test_regex_long_target_rejected(store: SqliteSegmentStore):
    # Sehr langer Zielstring (#951, Codex :499): der Callback darf ihn nicht ungebremst
    # gegen ein Muster laufen lassen. Wie der Legacy-Pfad wird ein Wert über
    # ``_REGEX_MAX_TARGET_LEN`` als 422-tauglicher Validierungsfehler ABGELEHNT (statt
    # truncatet-und-durchsucht), sodass das Ergebnis nicht von der Truncation-Grenze
    # abhängt.
    long_value = "a" * 100_000
    await store.append([_event(long_value, "2026-01-01T00:00:00.000Z")])
    window = {"from_ts": "2026-01-01T00:00:00.000Z", "to_ts": "2026-01-01T01:00:00.000Z"}
    with pytest.raises(ValueError, match="target value too long"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "regex", "pattern": r"b+c"}], **window))


async def test_regex_nested_quantifier_pattern_rejected(store: SqliteSegmentStore):
    # Pathologisches (ReDoS-taugliches) Muster wird wie Legacy schon beim Clause-Bau
    # abgelehnt (422-tauglicher ValueError), statt die Query laufen zu lassen.
    await store.append([_event("x", "2026-01-01T00:00:00.000Z")])
    window = {"from_ts": "2026-01-01T00:00:00.000Z", "to_ts": "2026-01-01T01:00:00.000Z"}
    with pytest.raises(ValueError, match="nested quantifiers"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "regex", "pattern": r"(a+)+$"}], **window))


async def test_regex_target_len_boundary(store: SqliteSegmentStore):
    # #951, Codex :499: ein Wert innerhalb ``_REGEX_MAX_TARGET_LEN`` wird regulär
    # gematcht; liegt im Scope ein Wert JENSEITS der Grenze, wird der Regex-Filter als
    # Validierungsfehler abgelehnt (Legacy-Parität), statt truncatet-und-durchsucht.
    from obs.ringbuffer.store.sqlite_backend import _REGEX_MAX_TARGET_LEN

    within = "x" * 10 + "TOKEN" + "y" * 10
    window = {"from_ts": "2026-01-01T00:00:00.000Z", "to_ts": "2026-01-01T01:00:00.000Z"}

    # Nur der kurze Wert im Scope → regulärer Treffer.
    await store.append([_event(within, "2026-01-01T00:00:00.000Z")])
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "regex", "pattern": "TOKEN"}], **window))
    assert {r["new_value"] for r in rows} == {within}

    # Ein zu langer Wert im Scope → Ablehnung (kein Prefix-Scan).
    beyond = "z" * (_REGEX_MAX_TARGET_LEN + 50) + "TOKEN"
    await store.append([_event(beyond, "2026-01-01T00:00:01.000Z")])
    with pytest.raises(ValueError, match="target value too long"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "regex", "pattern": "TOKEN"}], **window))
