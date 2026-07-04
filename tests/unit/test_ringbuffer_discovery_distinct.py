"""Discovery-Overhaul: STORE-Level ``SELECT DISTINCT datapoint_id`` statt Row-Pagination (#919, Review #951 Runde 33).

Drei Codex-[P2]-Findings mit gemeinsamer Wurzel (die scoped Typ-Discovery aus
Runde 29-32 arbeitete per Row-Pagination über den Scope):

**:2260 – Unscoped Value-Filter validieren gelöschte Datapoints.**
Für VOLLSTÄNDIG unscoped Queries validierte ``_validate_segmented_value_filter_types``
nur die AKTUELLE Registry. Alte Buffer-Zeilen GELÖSCHTER (nicht mehr registrierter)
Datapoints bleiben aber in der Store-Query enthalten; bei einem non-``eq``/``ne``-
Value-Filter wirft der Legacy-Pfad row-lazy 422 (STRING/BOOLEAN-Row), der
segmentierte Pushdown droppte sie still. → Auch der unscoped Fall braucht die
in-buffer-Kandidaten (inkl. gelöschter IDs), nicht nur die Registry.

**:1396 – Zeitfilter als Validierungs-Scope behandeln.**
Ein Value-Filter NUR mit Zeitfenster galt als „unscoped" und wurde gegen JEDEN
Registry-Typ validiert. Der Legacy-Pfad wendet die Zeit-Prädikate aber VOR der
Typprüfung an – ein unrelated STRING/BOOLEAN-Datapoint OHNE Zeilen im Fenster lässt
Legacy NICHT scheitern; der segmentierte Pfad warf trotzdem 422 (Über-Rejection). →
Die effektiven Zeit-Grenzen gehen in die Kandidaten-Discovery ein.

**Perf – Bounded scoped type discovery ohne Full-Row-Pagination.**
Die Row-Pagination stoppte (nicht-Export) erst, wenn so viele distinct Datapoints
gesehen wurden wie die GESAMTE Registry hat. Ein Scope-Datapoint mit Millionen
Zeilen bei vielen unrelated Registry-Datapoints ließ ``len(candidate_ids) >=
max_distinct`` nie true werden → Pagination über den ganzen Scope. → Ersetzt durch
eine STORE-Level ``SELECT DISTINCT datapoint_id``-Query (index-nutzbar, durch
distinct-count begrenzt statt durch Zeilenzahl).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, adapter: str = "api") -> None:
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
# :2260 – Unscoped Value-Filter über einen GELÖSCHTEN STRING/BOOLEAN-Datapoint
# mit Buffer-Zeilen → 422 (Parität Legacy), auch wenn die Registry ihn nicht kennt.
# ===========================================================================


async def _make_rb_unscoped_deleted(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Buffer enthält Zeilen eines gelöschten STRING-Datapoints (nicht in Registry)."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num")
    await _record(rb, "hello", "2026-01-01T00:00:01.000Z", datapoint_id="dp-gone-str")
    return rb


@pytest.mark.asyncio
async def test_unscoped_gt_deleted_string_dp_rejects_like_legacy(tmp_path: Path):
    """Unscoped ``gt``; ein gelöschter STRING-Datapoint (nicht in Registry) hat Zeilen → 422."""
    # Registry kennt NUR den numerischen Datapoint; ``dp-gone-str`` ist gelöscht.
    types = {"dp-num": "FLOAT"}
    legacy = await _make_rb_unscoped_deleted(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_unscoped_deleted(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=types, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=types, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_unscoped_between_deleted_bool_dp_rejects_like_legacy(tmp_path: Path):
    """Unscoped ``between``; ein gelöschter BOOLEAN-Datapoint hat Zeilen → 422 (Parität)."""
    types = {"dp-num": "FLOAT"}
    legacy = _rb(tmp_path / "legacy", segmented=False)
    seg = _rb(tmp_path / "seg", segmented=True)
    await legacy.start()
    await seg.start()
    try:
        for rb in (legacy, seg):
            await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num")
            await _record(rb, True, "2026-01-01T00:00:01.000Z", datapoint_id="dp-gone-bool")
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=types, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=types, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_unscoped_numeric_only_no_422(tmp_path: Path):
    """Gegentest: unscoped rein-numerischer Buffer → kein 422, korrektes Ergebnis."""
    types = {"dp-num": "FLOAT", "dp-num2": "INTEGER"}
    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        await _record(seg, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num")
        await _record(seg, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num2")
        rows = await seg.query_v2(value_filters=[{"operator": "gt", "value": 6}], datapoint_types=types, limit=10)
        assert [e.new_value for e in rows] == [9]
    finally:
        await seg.stop()


# ===========================================================================
# :1396 – Value-Filter NUR mit Zeitfenster: ein unrelated STRING/BOOLEAN-Datapoint
# OHNE Zeilen im Fenster darf KEIN 422 erzwingen (Legacy-Parität); MIT Zeilen im
# Fenster → 422.
# ===========================================================================


_TW_TYPES = {
    "dp-num": "FLOAT",
    "dp-str": "STRING",
}


@pytest.mark.asyncio
async def test_time_window_excludes_unrelated_string_dp_no_422(tmp_path: Path):
    """``gt`` + Zeitfenster; die STRING-Zeile liegt AUSSERHALB des Fensters → kein 422.

    Legacy wendet die Zeit-Prädikate VOR der Typprüfung an; die STRING-Zeile fällt
    aus dem Fenster, also feuert der row-lazy Typ-Check nicht. Der segmentierte Pfad
    muss die Zeit-Grenzen in die Kandidaten-Discovery ziehen und darf denselben
    STRING-Datapoint nicht mehr blind aus der Registry validieren.
    """
    legacy = _rb(tmp_path / "legacy", segmented=False)
    seg = _rb(tmp_path / "seg", segmented=True)
    await legacy.start()
    await seg.start()
    try:
        for rb in (legacy, seg):
            # STRING-Zeile FRÜH (vor dem Fenster).
            await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str")
            # Numerische Zeilen IM Fenster.
            await _record(rb, 5, "2026-01-01T00:01:00.000Z", datapoint_id="dp-num")
            await _record(rb, 9, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num")
        vf = [{"operator": "gt", "value": 1}]
        # Fenster deckt nur die numerischen Zeilen ab.
        legacy_rows = await legacy.query_v2(value_filters=vf, datapoint_types=_TW_TYPES, from_ts="2026-01-01T00:00:30.000Z", limit=10)
        seg_rows = await seg.query_v2(value_filters=vf, datapoint_types=_TW_TYPES, from_ts="2026-01-01T00:00:30.000Z", limit=10)
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
        assert sorted(e.new_value for e in seg_rows) == [5, 9]
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_time_window_includes_string_dp_rejects_like_legacy(tmp_path: Path):
    """``gt`` + Zeitfenster; die STRING-Zeile liegt INNERHALB des Fensters → 422 (Parität)."""
    legacy = _rb(tmp_path / "legacy", segmented=False)
    seg = _rb(tmp_path / "seg", segmented=True)
    await legacy.start()
    await seg.start()
    try:
        for rb in (legacy, seg):
            # STRING-Zeile IM Fenster.
            await _record(rb, "hello", "2026-01-01T00:01:00.000Z", datapoint_id="dp-str")
            await _record(rb, 5, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num")
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=_TW_TYPES, from_ts="2026-01-01T00:00:30.000Z", limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=_TW_TYPES, from_ts="2026-01-01T00:00:30.000Z", limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Perf – scoped Query, Scope = 1 Datapoint mit vielen Zeilen, Registry hat viele
# unrelated Datapoints → Discovery ist bounded (EINE DISTINCT-Query, nicht viele
# Row-Seiten). Via Aufruf-/Scan-Zähler nachgewiesen.
# ===========================================================================


@pytest.mark.asyncio
async def test_scoped_discovery_bounded_by_distinct_not_rows(tmp_path: Path):
    """Ein Scope-Datapoint mit vielen Zeilen bei großer unrelated Registry: EINE DISTINCT-Query.

    Ohne Fix paginierte die Discovery über ALLE Scope-Zeilen (der Registry-Größen-
    Stop wird nie erreicht, weil der Scope nur EINEN distinkten Datapoint hat, die
    Registry aber viele). Mit der STORE-Level DISTINCT-Query läuft die Discovery pro
    relevantem Segment als GENAU EINE Query – unabhängig von der Zeilenzahl.
    """
    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        # Scope: ein Adapter mit EINEM numerischen Datapoint, viele Zeilen.
        for i in range(50):
            await _record(seg, i, f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z", datapoint_id="dp-hot", adapter="scope-adapter")
        # Registry hat viele unrelated Datapoints (mehr als der Scope distinct hat).
        types = {"dp-hot": "FLOAT"}
        for k in range(100):
            types[f"dp-unrelated-{k}"] = "FLOAT"

        # Discovery-Aufrufe zählen: die neue DISTINCT-Discovery ruft
        # ``distinct_datapoint_ids`` auf (EINE Store-Interaktion), NICHT die
        # seitenweise ``_store_query_serialized``-Pagination.
        call_count = {"distinct": 0}
        orig = seg._store.distinct_datapoint_ids

        async def _counting(store_query):
            call_count["distinct"] += 1
            return await orig(store_query)

        seg._store.distinct_datapoint_ids = _counting  # type: ignore[method-assign]
        try:
            rows = await seg.query_v2(
                adapter_any_of=["scope-adapter"],
                value_filters=[{"operator": "gt", "value": 47}],
                datapoint_types=types,
                limit=20,
                is_export=True,
            )
        finally:
            seg._store.distinct_datapoint_ids = orig  # type: ignore[method-assign]
        # Kein 422 (rein numerisch), korrektes Ergebnis.
        assert sorted(e.new_value for e in rows) == [48, 49]
        # Genau EIN Discovery-Aufruf (kein Row-Paging über 50 Zeilen).
        assert call_count["distinct"] == 1
    finally:
        await seg.stop()
