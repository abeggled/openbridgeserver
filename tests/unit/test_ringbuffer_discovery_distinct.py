"""Value-Filter-Typprüfung row-lazy über die gebundene Kandidatenmenge (#919, Review #951).

Nach dem Wurzel-Refactor läuft die Value-Filter-Typprüfung row-lazy über die gebundene
Kandidatenmenge, exakt wie ``segmented=False``. Diese Suite prüft die Parität für zwei
realistische in-cap-Fälle:

**Unscoped Value-Filter über einen gelöschten Datapoint.**
Alte Buffer-Zeilen GELÖSCHTER (nicht mehr registrierter) Datapoints bleiben in der
Kandidatenmenge. Bei einem non-``eq``/``ne``-Value-Filter typ-checkt der row-lazy Pfad
auch diese Zeile und wirft 422 (STRING/BOOLEAN-Row) – identisch zu ``segmented=False``.

**Zeitfilter als Scope.**
Der Zeitrahmen bindet die Kandidatenmenge: ein unrelated STRING/BOOLEAN-Datapoint OHNE
Zeilen im Fenster erzwingt kein 422; einer MIT Zeilen im Fenster wirft 422 – exakt wie
der row-lazy Legacy-Pfad.
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
