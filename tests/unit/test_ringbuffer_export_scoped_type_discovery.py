"""q-scoped Value-Filter: row-lazy Typprüfung über die gebundene Kandidatenmenge (#919, Review #951).

Nach dem Wurzel-Refactor läuft die Value-Filter-Typprüfung für einen ``q``-Scope
row-lazy über die gebundene Kandidatenmenge, exakt wie ``segmented=False``. Eine
inkompatible ``q``-matchende Zeile INNERHALB dieser Menge (Cap/Zeitfenster) erzwingt
422 – identisch zum Legacy-Pfad. Eine inkompatible Zeile JENSEITS des Caps in einer
riesigen Historie wird bewusst NICHT gescannt (bounded Store); das ist die
dokumentierte, gewollte Divergenz des Refactors und gilt für Monitor UND Export.

Diese Suite fixiert:

* ``q``-scoped ``gt``/``between`` (Monitor und Export); die ältere ``q``-matchende
  STRING-Zeile liegt INNERHALB des Caps → 422, identisch zu ``segmented=False``.
* Regression: rein-numerischer ``q``-Export → kein 422, korrektes Ergebnis.
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


# Beide Datapoints matchen den Freitext ``q="sensor"`` über ``datapoint_id LIKE
# %sensor%``. ``sensor-str`` ist STRING (inkompatibel mit numerischen Operatoren).
_TYPES = {
    "sensor-num": "FLOAT",
    "sensor-str": "STRING",
}


async def _make_rb_old_string_new_numeric(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Älterer ``q``-matchender STRING-Datapoint + weitere numerische ``q``-Treffer.

    Die STRING-Zeile (``sensor-str``) ist die ÄLTESTE; danach folgen weitere numerische
    Zeilen (``sensor-num``). Alle Zeilen liegen INNERHALB des (default) Row-Caps, also in
    der gebundenen Kandidatenmenge – die row-lazy Typprüfung sieht damit auch die alte
    STRING-Zeile, exakt wie ``segmented=False``.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Älteste Zeile: STRING-Datapoint, matcht ``q="sensor"``.
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="sensor-str", adapter="mixed-adapter")
    for i in range(1, 8):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="sensor-num", adapter="mixed-adapter")
    return rb


async def _make_rb_numeric_only(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Rein numerischer ``q``-Scope mit mehr Zeilen als der Row-Cap."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    for i in range(10):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="sensor-num", adapter="numeric-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: q-scoped erkennt den STRING-Datapoint INNERHALB des Caps → 422 (In-Cap-Parität)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_scoped_export_gt_detects_string_in_cap(tmp_path: Path):
    """``q``-scoped EXPORT ``gt``; STRING-Zeile INNERHALB des Caps → 422.

    Legacy ist row-lazy und typ-checkt die STRING-Zeile → ValueError. Der segmentierte
    Export-Pfad wertet dieselbe gebundene Kandidatenmenge row-lazy aus → identisch 422.
    """
    legacy = await _make_rb_old_string_new_numeric(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 2}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_scoped_export_between_detects_string_in_cap_with_offset(tmp_path: Path):
    """``q``-scoped EXPORT ``between`` mit Offset: die STRING-Zeile in der Kandidatenmenge erzwingt 422."""
    legacy = await _make_rb_old_string_new_numeric(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_scoped_monitor_detects_string_in_cap_like_legacy(tmp_path: Path):
    """Derselbe ``q``-scoped Filter als NICHT-Export-Monitor: STRING in-cap → 422 wie Legacy."""
    legacy = await _make_rb_old_string_new_numeric(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Regression: rein-numerischer q-Export → kein 422, korrektes Ergebnis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_scoped_numeric_only_export_no_422(tmp_path: Path):
    """Rein numerischer ``q``-Export mit mehr Zeilen als der Cap → kein 422, korrektes Ergebnis."""
    legacy = await _make_rb_numeric_only(tmp_path / "legacy", segmented=False)
    seg = await _make_rb_numeric_only(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=20, is_export=True)
        seg_rows = await seg.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=20, is_export=True)
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
        assert sorted(e.new_value for e in seg_rows) == [7, 8, 9]
    finally:
        await legacy.stop()
        await seg.stop()
