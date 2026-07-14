"""Unscoped Value-Filter-Typparität: segmentierter Pfad == Legacy (#919, Review #951).

Codex-Finding (``ringbuffer.py`` :2081): ein Value-Filter OHNE expliziten
``datapoint_id`` (adapter-weite bzw. all-datapoints ``gt``/``contains``-Query)
übersprang im segmentierten Pfad die Legacy-Typkonflikt-Prüfung. Trifft dieselbe
Query im LEGACY-Pfad einen STRING-/BOOLEAN-Datapoint, wirft ``_matches_value_filter``
einen ``ValueError`` (→ 422 im API-Layer); der segmentierte Pushdown liefert dagegen
still Teilergebnisse (inkompatible Zeilen fallen leise weg).

Diese Suite fixiert die Parität: ein unscoped non-``eq``/``ne``-Filter gegen die im
``datapoint_types``-Universum bekannten STRING/BOOLEAN-Typen wird im segmentierten
Pfad mit demselben ``ValueError`` abgewiesen wie im Legacy-Pfad. ``eq``/``ne``,
scoped Filter und numerische Filter gegen numerische Datapoints bleiben unangetastet.
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


# Registry-Typuniversum wie es der API-Layer aus ``registry_entries`` baut
# (``{str(dp.id): dp.data_type}`` – ALLE Datapoints, nicht nur die gefilterten).
_TYPES = {
    "dp-num": "FLOAT",
    "dp-str": "STRING",
    "dp-bool": "BOOLEAN",
}


async def _make_rb(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num")
    await _record(rb, "hello", "2026-01-01T00:00:01.000Z", datapoint_id="dp-str")
    await _record(rb, True, "2026-01-01T00:00:02.000Z", datapoint_id="dp-bool")
    return rb


# ---------------------------------------------------------------------------
# Kernparität: unscoped non-eq/ne gegen STRING/BOOLEAN → 422 (wie Legacy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unscoped_gt_against_mixed_types_rejects_like_legacy(tmp_path: Path):
    """``gt`` ohne datapoint_id: Legacy 422 (STRING/BOOLEAN-Zeile) == segmentiert 422."""
    legacy = await _make_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_unscoped_contains_against_numeric_type_rejects_like_legacy(tmp_path: Path):
    """``contains`` ohne datapoint_id gegen ein FLOAT-Universum: beide 422."""
    legacy = await _make_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "contains", "value": "ell"}]
        with pytest.raises(ValueError):
            await legacy.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Gegentests: keine Überabweisung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unscoped_eq_ne_untouched(tmp_path: Path):
    """``eq``/``ne`` sind typunabhängig und dürfen NICHT abgewiesen werden."""
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        eq = await seg.query_v2(value_filters=[{"operator": "eq", "value": 5}], datapoint_types=_TYPES, limit=10)
        assert [e.new_value for e in eq] == [5]
        ne = await seg.query_v2(value_filters=[{"operator": "ne", "value": 5}], datapoint_types=_TYPES, limit=10)
        # alle Zeilen außer der numerischen 5 (typübergreifend inkl. bool/str)
        assert {repr(e.new_value) for e in ne} == {repr("hello"), repr(True)}
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_scoped_numeric_filter_untouched(tmp_path: Path):
    """Scoped ``gt`` auf einen FLOAT-Datapoint bleibt gültig und liefert korrekt."""
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        rows = await seg.query_v2(
            datapoint_ids=["dp-num"],
            value_filters=[{"operator": "gt", "value": 1}],
            datapoint_types=_TYPES,
            limit=10,
        )
        assert [e.new_value for e in rows] == [5]
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_unscoped_numeric_filter_all_numeric_universe(tmp_path: Path):
    """Unscoped ``gt`` gegen ein rein numerisches Typuniversum liefert korrekt (kein 422)."""
    rb = _rb(tmp_path / "seg", segmented=True)
    await rb.start()
    try:
        await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num")
        await _record(rb, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num2")
        types = {"dp-num": "FLOAT", "dp-num2": "INTEGER"}
        rows = await rb.query_v2(value_filters=[{"operator": "gt", "value": 6}], datapoint_types=types, limit=10)
        assert [e.new_value for e in rows] == [9]
    finally:
        await rb.stop()
