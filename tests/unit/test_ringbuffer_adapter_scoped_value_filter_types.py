"""Adapter-/source-scoped Value-Filter-Typparität: segmentiert == Legacy (#919, Review #951).

Codex-Finding (``ringbuffer.py`` :2095, Follow-up auf Runde 26): Die unscoped-
Typvalidierung ``_validate_segmented_value_filter_types`` prüfte einen non-``eq``/``ne``-
Value-Filter gegen das VOLLE ``datapoint_types``-Universum (alle Registry-Datapoints).
Für eine ADAPTER-scopte Query (z. B. ``adapter_any_of=['numeric-adapter']`` + ``gt``)
ohne explizite ``datapoint_ids`` fiel damit bereits ein 422, sobald IRGENDEIN
unrelated STRING-/BOOLEAN-Datapoint eines ANDEREN Adapters existierte – obwohl der
Adapter-Filter diese Datapoints nie zurückgeben kann.

Der Legacy-Pfad ist dagegen row-lazy: er wendet erst den ``source_adapter``-SQL-Filter
an und typ-checkt nur die ZURÜCKGEGEBENEN Zeilen. Diese Suite fixiert die Parität:

* Adapter-scoped ``gt`` gegen einen NUMERISCHEN Adapter, bei gemischter Installation
  mit unrelated STRING/BOOLEAN-Datapoints ANDERER Adapter → KEIN 422 (die unrelated
  Datapoints sind keine Kandidaten), korrektes numerisches Ergebnis.
* Adapter-scoped ``gt`` gegen einen Adapter, dessen EIGENER Datapoint STRING ist →
  weiterhin 422 (Parität zum Legacy, der die zurückgegebene STRING-Zeile typ-checkt).
* Vollständig unscoped (kein datapoint_id, kein adapter/source) + non-``eq``/``ne``
  gegen gemischte Typen → weiterhin 422 (Runde-26-Verhalten unverändert).
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


# Registry-Typuniversum wie es der API-Layer aus ``registry_entries`` baut
# (``{str(dp.id): dp.data_type}`` – ALLE Datapoints, adapter-übergreifend).
_TYPES = {
    "dp-num": "FLOAT",
    "dp-str": "STRING",
    "dp-bool": "BOOLEAN",
}


async def _make_mixed_rb(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Gemischte Installation: numerischer Adapter + unrelate STRING/BOOLEAN-Adapter.

    ``numeric-adapter`` liefert ausschließlich den FLOAT-Datapoint ``dp-num``.
    ``string-adapter``/``bool-adapter`` liefern unrelate STRING/BOOLEAN-Datapoints,
    die der numerische Adapter-Filter NIE zurückgeben kann.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, "hello", "2026-01-01T00:00:02.000Z", datapoint_id="dp-str", adapter="string-adapter")
    await _record(rb, True, "2026-01-01T00:00:03.000Z", datapoint_id="dp-bool", adapter="bool-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: adapter-scoped auf numerischen Adapter → KEIN 422 wegen unrelated Typen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_numeric_gt_ignores_unrelated_string_bool(tmp_path: Path):
    """``adapter_any_of=['numeric-adapter']`` + ``gt``: unrelate STRING/BOOLEAN sind keine Kandidaten.

    Legacy filtert erst nach ``source_adapter`` und typ-checkt nur die numerischen
    Zeilen → kein 422, liefert ``[9]``. Der segmentierte Pfad muss identisch sein.
    """
    legacy = await _make_mixed_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_mixed_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        seg_rows = await seg.query_v2(adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        assert [e.new_value for e in legacy_rows] == [9]
        assert [e.new_value for e in seg_rows] == [9]
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Case B: adapter-scoped auf STRING-Adapter → weiterhin 422 (Parität zum Legacy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_own_string_datapoint_gt_rejects_like_legacy(tmp_path: Path):
    """Adapter-Filter, dessen EIGENER Datapoint STRING ist, mit ``gt`` → beide 422.

    Legacy gibt die STRING-Zeile zurück und ``_matches_value_filter`` wirft für
    ``gt`` gegen STRING einen ``ValueError``. Der segmentierte Pfad darf hier NICHT
    still leer laufen (kein Row-Drop für die adressierten Datapoints).
    """
    legacy = await _make_mixed_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_mixed_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["string-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["string-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Regression: vollständig unscoped bleibt Runde-26-Verhalten (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fully_unscoped_gt_against_mixed_types_still_rejects(tmp_path: Path):
    """Kein datapoint_id UND kein adapter/source + ``gt`` gegen gemischte Typen → weiterhin 422."""
    seg = await _make_mixed_rb(tmp_path / "seg", segmented=True)
    try:
        with pytest.raises(ValueError):
            await seg.query_v2(value_filters=[{"operator": "gt", "value": 1}], datapoint_types=_TYPES, limit=10)
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_scoped_datapoint_id_numeric_gt_unchanged(tmp_path: Path):
    """Scoped per datapoint_id + ``gt`` auf einen FLOAT-Datapoint bleibt gültig (kein 422)."""
    seg = await _make_mixed_rb(tmp_path / "seg", segmented=True)
    try:
        rows = await seg.query_v2(
            datapoint_ids=["dp-num"],
            value_filters=[{"operator": "gt", "value": 6}],
            datapoint_types=_TYPES,
            limit=10,
        )
        assert [e.new_value for e in rows] == [9]
    finally:
        await seg.stop()
