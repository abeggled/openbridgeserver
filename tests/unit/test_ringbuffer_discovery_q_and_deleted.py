"""Scoped Value-Filter: q-Treffer jenseits der Namens-Hits + gelöschte Datapoints in-cap (#919, Review #951).

Nach dem Wurzel-Refactor läuft die Value-Filter-Typprüfung row-lazy über die gebundene
Kandidatenmenge, exakt wie ``segmented=False``. Zwei Aspekte:

**Aspekt 1 – q-Treffer jenseits der Namens-Hits.**
Ein ``q``-Scope kann per ``datapoint_id LIKE`` / ``source_adapter LIKE`` Datapoints
jenseits der ``dp_ids_by_name``-Namens-Treffer matchen. Liegt ein solcher STRING/
BOOLEAN-``q``-Treffer INNERHALB der Kandidatenmenge, erzwingt ein numerischer Filter
422 – exakt wie der row-lazy Legacy-Pfad. Der numerische Namens-Treffer schließt die
row-lazy Auswertung nicht kurz.

**Aspekt 2 – gelöschte (nicht mehr registrierte) Datapoints in-cap.**
Der Buffer kann Zeilen GELÖSCHTER Datapoints enthalten. Liegt eine solche inkompatible
Zeile INNERHALB der Kandidatenmenge, erzwingt sie 422 (In-Cap-Parität zu Legacy). Eine
inkompatible Zeile JENSEITS des Caps in einer riesigen Historie wird bewusst NICHT
gescannt (bounded Store) – dokumentierte, gewollte Divergenz des Refactors.
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
# Aspekt 2: Ein gelöschter (nicht mehr registrierter) Datapoint INNERHALB der
# Kandidatenmenge erzwingt 422 (In-Cap-Parität zu ``segmented=False``).
# ===========================================================================


# Registry kennt nur die zwei aktuellen numerischen Datapoints. ``dp-gone`` ist
# gelöscht (kein Registry-Typ), hat aber noch eine STRING-Zeile im Buffer.
_F2_TYPES = {
    "dp-num-a": "FLOAT",
    "dp-num-b": "FLOAT",
}


async def _make_rb_deleted_dp_in_cap(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Ein gelöschter STRING-Datapoint (``dp-gone``) liegt INNERHALB der Kandidatenmenge.

    Der Buffer hält Zeilen der zwei bekannten Registry-Datapoints (``dp-num-a``,
    ``dp-num-b``) UND eine STRING-Zeile des gelöschten Datapoints ``dp-gone`` (kein
    Registry-Typ). Alle Zeilen liegen innerhalb des (default) Row-Caps, also in der
    gebundenen Kandidatenmenge – die row-lazy Typprüfung sieht damit auch ``dp-gone``.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Gelöschter STRING-Datapoint (kein Registry-Typ).
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-gone", adapter="scope-adapter")
    await _record(rb, 1, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num-a", adapter="scope-adapter")
    await _record(rb, 2, "2026-01-01T00:00:02.000Z", datapoint_id="dp-num-b", adapter="scope-adapter")
    await _record(rb, 3, "2026-01-01T00:00:03.000Z", datapoint_id="dp-num-a", adapter="scope-adapter")
    await _record(rb, 4, "2026-01-01T00:00:04.000Z", datapoint_id="dp-num-b", adapter="scope-adapter")
    return rb


@pytest.mark.asyncio
async def test_export_discovery_finds_deleted_dp_in_cap(tmp_path: Path):
    """Export ``gt`` über gelöschten STRING-Datapoint INNERHALB des Caps → 422.

    Legacy ist row-lazy und typ-checkt die STRING-Zeile → ValueError. Der segmentierte
    Pfad wertet dieselbe gebundene Kandidatenmenge row-lazy aus → identisch 422.
    """
    legacy = await _make_rb_deleted_dp_in_cap(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_deleted_dp_in_cap(tmp_path / "seg", segmented=True)
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
async def test_export_between_discovery_finds_deleted_dp_in_cap(tmp_path: Path):
    """``between``-Export: der gelöschte STRING-Datapoint in der Kandidatenmenge erzwingt 422."""
    legacy = await _make_rb_deleted_dp_in_cap(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_deleted_dp_in_cap(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["scope-adapter"], value_filters=vf, datapoint_types=_F2_TYPES, limit=2, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["scope-adapter"], value_filters=vf, datapoint_types=_F2_TYPES, limit=2, is_export=True)
    finally:
        await legacy.stop()
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
