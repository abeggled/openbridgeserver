"""Scoped Value-Filter-Typvalidierung: row-lazy über die gebundene Kandidatenmenge (#919, Review #951).

Nach dem Wurzel-Refactor läuft die Value-Filter-Typprüfung row-lazy über die
gebundene Kandidatenmenge (die neuesten ``_SEGMENTED_CANDIDATE_CAP`` scoped Zeilen),
exakt wie ``segmented=False``. Eine inkompatible Zeile INNERHALB dieser Menge (Cap/
Zeitfenster) erzwingt 422 – identisch zum Legacy-Pfad. Eine inkompatible Zeile
JENSEITS des Caps in einer riesigen Historie wird bewusst NICHT gescannt (der bounded
Store scannt nicht die ganze Historie, nur um eine alte inkompatible Zeile zu finden);
das ist die dokumentierte, gewollte Divergenz des Refactors.

Diese Suite fixiert die In-Cap-Parität:

* Adapter-scoped ``gt``/``between`` – die ältere STRING-Zeile liegt INNERHALB des Caps
  → 422 (der inkompatible Datapoint wird erkannt), identisch zu ``segmented=False``.
* Gegentest: Scope rein numerisch, auch mit mehr Zeilen als der Cap → kein 422,
  korrektes Ergebnis.
* Regression: adapter-scoped rein-numerisch (Runde 28) und vollständig unscoped
  (Runde 26) unverändert.
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


_TYPES = {
    "dp-num": "FLOAT",
    "dp-num2": "FLOAT",
    "dp-str": "STRING",
}


async def _make_rb_old_string_new_numeric(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Ein Adapter mit ÄLTERER STRING-Zeile und mehreren NEUEREN numerischen Zeilen.

    Die STRING-Zeile (``dp-str``) ist die ÄLTESTE des Adapters; danach folgen weitere
    numerische Zeilen (``dp-num``). Alle Zeilen liegen INNERHALB des (default) Row-Caps,
    d. h. in der gebundenen Kandidatenmenge – die row-lazy Typprüfung sieht damit auch
    die alte STRING-Zeile, exakt wie ``segmented=False``.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Älteste Zeile: STRING-Datapoint desselben Adapters.
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", adapter="mixed-adapter")
    for i in range(1, 8):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="dp-num", adapter="mixed-adapter")
    return rb


async def _make_rb_numeric_only(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Rein numerischer Adapter mit mehr Zeilen als der Row-Cap."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    for i in range(10):
        dp = "dp-num" if i % 2 == 0 else "dp-num2"
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id=dp, adapter="numeric-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: in-scope STRING-Datapoint INNERHALB des Caps → 422 (In-Cap-Parität)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_gt_detects_old_string_datapoint_in_cap(tmp_path: Path):
    """Adapter-scoped ``gt``; die STRING-Zeile liegt INNERHALB des Caps → 422.

    Legacy ist row-lazy und typ-checkt die STRING-Zeile → ValueError. Der
    segmentierte Pfad wertet dieselbe gebundene Kandidatenmenge row-lazy aus und
    liefert identisch 422 (In-Cap-Parität zu ``segmented=False``).
    """
    legacy = await _make_rb_old_string_new_numeric(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 2}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["mixed-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["mixed-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_scoped_between_detects_string_in_cap_with_offset(tmp_path: Path):
    """``between`` mit Offset: die STRING-Zeile in der Kandidatenmenge erzwingt weiterhin 422."""
    legacy = await _make_rb_old_string_new_numeric(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["mixed-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["mixed-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2)
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Gegentest: rein numerischer Scope (auch >Cap Zeilen) → kein 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_numeric_only_beyond_cap_no_422(tmp_path: Path):
    """Rein numerischer Adapter mit mehr Zeilen als der Cap → kein 422, korrektes Ergebnis."""
    legacy = await _make_rb_numeric_only(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_numeric_only(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=20)
        seg_rows = await seg.query_v2(adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=20)
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
        assert sorted(e.new_value for e in seg_rows) == [7, 8, 9]
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Regression: Runde-28 (adapter-scoped numerisch, unrelated Typen) unverändert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_round28_unrelated_string_bool_no_422(tmp_path: Path):
    """Adapter-scoped numerisch; unrelate STRING/BOOLEAN ANDERER Adapter → kein 422."""
    rb = _rb(tmp_path / "seg", segmented=True)
    await rb.start()
    try:
        await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
        await _record(rb, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
        await _record(rb, "x", "2026-01-01T00:00:02.000Z", datapoint_id="dp-str", adapter="string-adapter")
        rows = await rb.query_v2(
            adapter_any_of=["numeric-adapter"],
            value_filters=[{"operator": "gt", "value": 6}],
            datapoint_types=_TYPES,
            limit=10,
        )
        assert [e.new_value for e in rows] == [9]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_fully_unscoped_rejects_only_with_incompatible_rows_like_legacy(tmp_path: Path):
    """Vollständig unscoped + ``gt``: 422 nur, wenn ein STRING/BOOLEAN-Datapoint auch ZEILEN hat (Runde 33).

    Korrigiert das frühere „Runde 26"-Verhalten (gegen das VOLLE Registry-Universum
    validieren): Der Legacy-Pfad ist row-lazy und typ-checkt NUR Zeilen, die real
    existieren. Ein STRING/BOOLEAN-Datapoint, der zwar in der Registry steht, aber
    KEINE Zeilen im Buffer hat, lässt Legacy NICHT scheitern (Codex :2260/:1396). Die
    STORE-Level DISTINCT-Discovery erfasst genau die Datapoints MIT Zeilen, also gilt
    die Parität in beide Richtungen.
    """
    # Fall A: nur eine numerische Zeile → kein STRING/BOOLEAN im Buffer → kein 422.
    rb = _rb(tmp_path / "numeric_only", segmented=True)
    await rb.start()
    try:
        await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
        rows = await rb.query_v2(value_filters=[{"operator": "gt", "value": 1}], datapoint_types=_TYPES, limit=10)
        assert [e.new_value for e in rows] == [5]
    finally:
        await rb.stop()

    # Fall B: ein STRING-Datapoint HAT eine Zeile → 422 (row-lazy Parität), wie Legacy.
    legacy = _rb(tmp_path / "legacy", segmented=False)
    seg = _rb(tmp_path / "seg", segmented=True)
    await legacy.start()
    await seg.start()
    try:
        for inst in (legacy, seg):
            await _record(inst, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
            await _record(inst, "x", "2026-01-01T00:00:01.000Z", datapoint_id="dp-str", adapter="string-adapter")
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()
