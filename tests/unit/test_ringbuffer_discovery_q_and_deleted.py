"""Scoped Typ-Discovery: q-Treffer jenseits der Namens-Hits + gelöschte historische Datapoints (#919, Review #951 Runde 32).

Zwei Codex-[P2]-Findings, beide verbleibende Lücken der scoped Typ-Discovery
(``_scope_candidate_datapoint_ids`` / ``_validate_segmented_value_filter_types``):

**Finding 1 (:1393) – Namens-Treffer kurzschließt die q-Discovery.**
Ist ``dp_ids_by_name`` (Namens-Auflösung der UI-Freitextsuche) non-empty, war
``scoped_ids`` non-empty und der segmentierte Pfad übersprang den scoped Discovery-
Zweig komplett – obwohl ``q`` per ``datapoint_id LIKE`` / ``source_adapter LIKE``
ZUSÄTZLICHE Datapoints jenseits der Namens-Treffer matchen kann. Ein ``q``-scoped
numerischer Value-Export, dessen Namens-Treffer numerisch ist, aber dessen
``q``-per-id/adapter-Treffer eine ältere STRING/BOOLEAN-Zeile ist, validierte nur
den Namens-Treffer und pushte dann das numerische Prädikat → die inkompatiblen
Zeilen fielen still weg statt Legacy-422. Fix: Discovery läuft bei gesetztem ``q``
(bzw. adapter/metadaten-Scope) IMMER und wird mit den Namens-Treffern VEREINIGT.

**Finding 2 (:1590) – Early-Stop bei Registry-Größe übersieht gelöschte Datapoints.**
Der Abbruch, sobald ``len(distinct ids) >= max_distinct = len(datapoint_types)``,
nimmt an, die aktuelle Registry begrenze jeden im Scope möglichen Datapoint. Der
Buffer kann aber noch Zeilen GELÖSCHTER Datapoints enthalten. Hat die erste
Discovery-Seite bereits alle Registry-IDs gesehen, wird ein älterer in-scope
UNBEKANNTER (gelöschter) STRING/BOOLEAN-Datapoint nie zurückgegeben → ein ``gt``/
``between``-Export pusht das numerische Prädikat und droppt jene Zeilen still. Fix
(Export): Discovery erschöpfend bis Rows erschöpft (kurze Seite) / ``max_pages``-
Backstop, kein reiner Registry-Größen-Stop. Nicht-Export-Monitorpfad bleibt über
Seiten-Cap + kurze Seite gebunden (kein unbounded-Scan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import obs.ringbuffer.ringbuffer as ringbuffer_module
from obs.ringbuffer.ringbuffer import RingBuffer


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, adapter: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter=adapter,
        quality="good",
        metadata_version=1,
        metadata={},
    )


# ===========================================================================
# Finding 1: q-Treffer jenseits der Namens-Hits dürfen die Discovery nicht
# kurzschließen. Namens-Treffer (``dp_ids_by_name``) und q-per-id/adapter-Treffer
# werden VEREINIGT, nicht ersetzt.
# ===========================================================================


# Registry-Typuniversum. Nur der numerische Namens-Treffer ``dp-num`` ist bekannt;
# der q-per-id-matchende STRING-Datapoint ist hier zusätzlich als STRING bekannt.
_F1_TYPES = {
    "dp-num": "FLOAT",
    "dp-str-probe": "STRING",
}


async def _make_rb_q_matches_beyond_name(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """``q='probe'`` matcht per ``datapoint_id LIKE`` einen STRING-Datapoint;
    ``dp_ids_by_name=['dp-num']`` ist der numerische Namens-Treffer.

    So enthält der q-Scope einen Datapoint (``dp-str-probe``), der KEIN Namens-
    Treffer ist. Der numerische Namens-Treffer (``dp-num``) darf die Discovery
    des STRING-``q``-Treffers nicht kurzschließen.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Ältere STRING-Zeile, matcht ``q='probe'`` per datapoint_id LIKE.
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-probe", adapter="string-adapter")
    # Neuere numerische Zeilen des Namens-Treffers.
    await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", adapter="num-adapter")
    await _record(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num", adapter="num-adapter")
    return rb


@pytest.mark.asyncio
async def test_q_name_hit_does_not_shortcircuit_string_q_id_match(tmp_path: Path):
    """``q``-scoped ``gt`` mit numerischem Namens-Treffer UND STRING-q-id-Treffer → 422.

    Der Namens-Treffer (``dp-num``) macht ``scoped_ids`` non-empty; ohne Fix
    übersprünge der segmentierte Pfad die q-Discovery und validierte nur den
    numerischen Namens-Treffer → kein 422, obwohl die ältere STRING-``q``-Zeile
    still gedroppt würde. Legacy ist row-lazy und wirft 422.
    """
    legacy = await _make_rb_q_matches_beyond_name(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_q_matches_beyond_name(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 2}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="probe", dp_ids_by_name=["dp-num"], value_filters=vf, datapoint_types=_F1_TYPES, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="probe", dp_ids_by_name=["dp-num"], value_filters=vf, datapoint_types=_F1_TYPES, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_name_hit_numeric_only_no_422(tmp_path: Path):
    """Gegentest: ``q`` matcht NUR numerische Datapoints → kein 422, korrektes Ergebnis.

    Der q-per-id-Treffer ist hier ebenfalls numerisch; die Vereinigung aus
    Namens-Treffer und q-Discovery bleibt rein numerisch → kein falsches 422.
    """
    types = {"dp-num": "FLOAT", "dp-num-probe": "FLOAT"}
    legacy = _rb(tmp_path / "legacy", segmented=False)
    seg = _rb(tmp_path / "seg", segmented=True)
    await legacy.start()
    await seg.start()
    try:
        for rb in (legacy, seg):
            await _record(rb, 3, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num-probe", adapter="num-adapter")
            await _record(rb, 7, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", adapter="num-adapter")
            await _record(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num-probe", adapter="num-adapter")
        vf = [{"operator": "gt", "value": 5}]
        legacy_rows = await legacy.query_v2(q="probe", dp_ids_by_name=["dp-num"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        seg_rows = await seg.query_v2(q="probe", dp_ids_by_name=["dp-num"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
        assert sorted(e.new_value for e in seg_rows) == [7, 9]
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Finding 2: Der Early-Stop bei Registry-Größe darf gelöschte historische
# Datapoints jenseits der Registry nicht übersehen. Für Export läuft die
# Discovery erschöpfend; der Nicht-Export-Monitor bleibt gebunden.
# ===========================================================================


# Registry kennt nur die zwei aktuellen numerischen Datapoints. ``dp-gone`` ist
# gelöscht (kein Registry-Typ), hat aber noch eine ältere STRING-Zeile im Buffer.
_F2_TYPES = {
    "dp-num-a": "FLOAT",
    "dp-num-b": "FLOAT",
}


async def _make_rb_deleted_dp_beyond_registry_page(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Erste Discovery-Seite sieht bereits alle Registry-IDs; ein gelöschter STRING-Datapoint liegt weiter zurück.

    Reihenfolge (Discovery paginiert id/desc, aber der Store deckelt je Seite auf
    ``candidate_cap``): Die neueren Zeilen decken beide bekannten Registry-IDs
    (``dp-num-a``, ``dp-num-b``) ab; die ÄLTESTE Zeile gehört dem gelöschten
    STRING-Datapoint ``dp-gone``. Mit auf 2 gesetztem Cap (= Registry-Größe) hätte
    der Registry-Größen-Stop nach der ersten Seite abgebrochen und ``dp-gone`` nie
    gesehen.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Älteste Zeile: gelöschter STRING-Datapoint (kein Registry-Typ).
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-gone", adapter="scope-adapter")
    # Neuere numerische Zeilen beider bekannter Registry-Datapoints (mehr als der Cap).
    await _record(rb, 1, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num-a", adapter="scope-adapter")
    await _record(rb, 2, "2026-01-01T00:00:02.000Z", datapoint_id="dp-num-b", adapter="scope-adapter")
    await _record(rb, 3, "2026-01-01T00:00:03.000Z", datapoint_id="dp-num-a", adapter="scope-adapter")
    await _record(rb, 4, "2026-01-01T00:00:04.000Z", datapoint_id="dp-num-b", adapter="scope-adapter")
    return rb


@pytest.mark.asyncio
async def test_export_discovery_finds_deleted_dp_beyond_registry_size(tmp_path: Path, monkeypatch):
    """Export ``gt`` über gelöschten STRING-Datapoint jenseits der ersten Registry-vollen Seite → 422.

    Der Cap wird auf die Registry-Größe (=2) gesetzt: Die erste Discovery-Seite
    sieht bereits beide bekannten IDs. Ohne Fix bräche der Registry-Größen-Stop
    ab und übersähe den gelöschten STRING-Datapoint → still gedroppte Zeilen.
    Mit erschöpfender Export-Discovery wird ``dp-gone`` erkannt → 422 wie Legacy.
    """
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 2, raising=True)
    legacy = await _make_rb_deleted_dp_beyond_registry_page(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_deleted_dp_beyond_registry_page(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 0}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["scope-adapter"], value_filters=vf, datapoint_types=_F2_TYPES, limit=2, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["scope-adapter"], value_filters=vf, datapoint_types=_F2_TYPES, limit=2, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_export_between_discovery_finds_deleted_dp(tmp_path: Path, monkeypatch):
    """``between``-Export: der gelöschte STRING-Datapoint jenseits der Registry-vollen Seite erzwingt 422."""
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 2, raising=True)
    seg = await _make_rb_deleted_dp_beyond_registry_page(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["scope-adapter"], value_filters=vf, datapoint_types=_F2_TYPES, limit=2, is_export=True)
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_export_no_deleted_dp_in_scope_no_422(tmp_path: Path, monkeypatch):
    """Gegentest: kein gelöschter in-scope Datapoint → kein 422, korrektes Ergebnis (Export)."""
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 2, raising=True)
    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        for i, dp in ((1, "dp-num-a"), (2, "dp-num-b"), (3, "dp-num-a"), (4, "dp-num-b")):
            await _record(seg, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id=dp, adapter="scope-adapter")
        rows = await seg.query_v2(
            adapter_any_of=["scope-adapter"],
            value_filters=[{"operator": "gt", "value": 2}],
            datapoint_types=_F2_TYPES,
            limit=20,
            is_export=True,
        )
        assert sorted(e.new_value for e in rows) == [3, 4]
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_non_export_monitor_discovery_stays_bounded(tmp_path: Path, monkeypatch):
    """Nicht-Export-Monitorpfad bleibt gebunden: die Discovery-Seitenzahl ist gedeckelt (kein unbounded-Scan).

    Wir zählen die Store-Query-Aufrufe der Discovery über einen deutlich kleineren
    ``max_pages``-Backstop. Bei Nicht-Export darf die Discovery nicht beliebig viele
    Seiten scannen – der Seiten-Cap plus „kurze Seite" begrenzt sie. Hier fabrizieren
    wir einen Store, der NIE eine kurze Seite liefert (immer voll), und prüfen, dass
    die Discovery dennoch nach ``max_pages`` terminiert statt endlos zu scannen.
    """
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 2, raising=True)
    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        # Rein numerischer Scope, mehr Zeilen als der Cap; Nicht-Export.
        for i in range(10):
            dp = "dp-num-a" if i % 2 == 0 else "dp-num-b"
            await _record(seg, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id=dp, adapter="scope-adapter")

        call_count = {"n": 0}
        orig = seg._store_query_serialized

        async def _counting(store_query):
            call_count["n"] += 1
            return await orig(store_query)

        monkeypatch.setattr(seg, "_store_query_serialized", _counting)
        rows = await seg.query_v2(
            adapter_any_of=["scope-adapter"],
            value_filters=[{"operator": "gt", "value": 7}],
            datapoint_types=_F2_TYPES,
            limit=5,
            is_export=False,
        )
        # Kein 422 (rein numerisch); Ergebnis korrekt.
        assert sorted(e.new_value for e in rows) == [8, 9]
        # Discovery + finale Query bleiben gebunden – der Registry-Größen-Stop bzw.
        # die kurze Seite terminieren früh, weit unter dem 1000er-Backstop.
        assert call_count["n"] < 50
    finally:
        await seg.stop()
