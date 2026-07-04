"""Scoped Value-Filter-Typvalidierung: Discovery unabhängig vom Row-/Kandidaten-Cap (#919, Review #951).

Codex-Finding (``ringbuffer.py`` :1431, Follow-up auf Runde 28): Für adapter-/
source-/metadaten-scoped Value-Filter OHNE explizite ``datapoint_ids`` bestimmt der
segmentierte Pfad die Kandidaten-Datapoints aus einem Row-gecappten Discovery-Scan
(die neuesten ``_SEGMENTED_CANDIDATE_CAP`` scoped Zeilen). Sind diese neuesten Zeilen
alle numerisch, existiert aber – WEITER ZURÜCK, jenseits des Row-Caps – ein älterer
in-scope STRING/BOOLEAN-Datapoint, so entging dieser der Typprüfung: eine ``gt``/
``between``-Query lieferte still Teilergebnisse statt des Legacy-422.

Diese Suite fixiert, dass die Discovery die scoped Datapoint-IDs UNABHÄNGIG vom
Row-Cap enumeriert (gebunden durch die Anzahl distinkter Datapoints, nicht durch
Rows):

* Adapter-scoped ``gt`` – Scope enthält viele NEUE numerische Rows UND (jenseits des
  Row-Caps) mind. eine ältere STRING-Zeile → 422 (der ältere inkompatible Datapoint
  wird erkannt), unabhängig von Offset/Export.
* Gegentest: Scope rein numerisch, auch mit mehr Zeilen als der Cap → kein 422,
  korrektes Ergebnis.
* Regression: adapter-scoped rein-numerisch (Runde 28) und vollständig unscoped
  (Runde 26) unverändert.

Der Row-Cap wird in den Tests auf einen kleinen Wert gesetzt (Monkeypatch von
``_SEGMENTED_CANDIDATE_CAP``), um „jenseits des Caps" ohne 10k reale Zeilen zu
simulieren.
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


_TYPES = {
    "dp-num": "FLOAT",
    "dp-num2": "FLOAT",
    "dp-str": "STRING",
}


@pytest.fixture(autouse=True)
def _tiny_candidate_cap(monkeypatch):
    """Row-Cap klein setzen, um „jenseits des Caps" ohne 10k reale Zeilen zu simulieren."""
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 3, raising=True)


async def _make_rb_old_string_new_numeric(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Ein Adapter mit ÄLTERER STRING-Zeile und vielen NEUEREN numerischen Zeilen.

    Die STRING-Zeile (``dp-str``) ist die ÄLTESTE des Adapters; danach folgen mehr
    numerische Zeilen (``dp-num``) als der (klein gemonkeypatchte) Row-Cap. Ein
    row-gecappter Discovery-Scan (neueste zuerst) sieht damit nur die numerischen
    Zeilen und übersieht die alte STRING-Zeile.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Älteste Zeile: STRING-Datapoint desselben Adapters.
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", adapter="mixed-adapter")
    # Danach mehr numerische Zeilen als der Row-Cap (=3): jenseits des Caps liegt
    # die STRING-Zeile.
    for i in range(1, 8):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="dp-num", adapter="mixed-adapter")
    return rb


async def _make_rb_numeric_only(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Rein numerischer Adapter mit mehr Zeilen als der Row-Cap."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    for i in range(0, 10):
        dp = "dp-num" if i % 2 == 0 else "dp-num2"
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id=dp, adapter="numeric-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: älterer in-scope STRING-Datapoint jenseits des Caps → 422 (Parität)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_gt_detects_old_string_datapoint_beyond_cap(tmp_path: Path):
    """Adapter-scoped ``gt``; alte STRING-Zeile liegt jenseits des Row-Caps → 422.

    Legacy ist row-lazy und typ-checkt auch die alte STRING-Zeile → ValueError.
    Der segmentierte Pfad muss identisch 422 liefern, obwohl der Row-gecappte
    Discovery-Scan die STRING-Zeile nicht sieht.
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
async def test_scoped_between_detects_old_string_beyond_cap_with_offset(tmp_path: Path):
    """``between`` mit größerem Offset: der alte STRING-Datapoint erzwingt weiterhin 422."""
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["mixed-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2)
    finally:
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
