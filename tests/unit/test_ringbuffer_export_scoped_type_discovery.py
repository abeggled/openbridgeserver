"""q-scoped EXPORT: Typ-Discovery erschöpfend (is_export durchgereicht) (#919, Review #951).

Codex-Finding (``ringbuffer.py`` :1437, Follow-up auf Runde 29): Die scoped Typ-
Discovery (``_scope_candidate_datapoint_ids``) baute ihre ``StoreQuery`` IMMER mit
``is_export=False`` – auch wenn der Aufrufer einen CSV-Export (``is_export=True``)
fährt. Für einen ``q``-scoped Export mit Value-Filter legt der Store den Freitext-
``q``-OR-Block als Guarded-Filter auf die gedeckelte Kandidatenmenge (neueste
``_SEGMENTED_CANDIDATE_CAP`` Zeilen je Segment). Sind diese neuesten Kandidaten
numerisch, existiert aber – jenseits des Caps – ein ÄLTERER ``q``-matchender STRING/
BOOLEAN-Datapoint, so entging dieser der Discovery: der eigentliche Export inlinet
``q`` (erschöpfend), pusht das numerische Prädikat und dropt die inkompatiblen Zeilen
STILL, statt das Legacy-422 zu werfen.

Fix: Der Aufrufer-Export-Modus (``is_export``) wird in die Discovery-``StoreQuery``
durchgereicht. Beim Export läuft die Discovery damit erschöpfend (kein Freitext-
``q``-Cap) und erkennt den älteren inkompatiblen Datapoint → korrektes 422 (Parität
zum Legacy). Für den Nicht-Export-Monitorpfad bleibt die bisherige (gedeckelte)
Discovery – KEIN neuer unbounded Scan.

Diese Suite fixiert:

* ``q``-scoped EXPORT ``gt``/``between``; viele neue numerische ``q``-Treffer UND
  (jenseits des Row-Caps) ein älterer ``q``-matchender STRING-Datapoint → 422.
* Gegentest: derselbe ``q``-scoped Filter als NICHT-Export-Monitor → bisheriges
  gedeckeltes Verhalten (kein 422, weil der alte Datapoint jenseits des Caps liegt).
* Regression: rein-numerischer ``q``-Export → kein 422, korrektes Ergebnis.

Der Row-Cap wird auf einen kleinen Wert gemonkeypatcht, um „jenseits des Caps" ohne
10k reale Zeilen zu simulieren.
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


# Beide Datapoints matchen den Freitext ``q="sensor"`` über ``datapoint_id LIKE
# %sensor%``. ``sensor-str`` ist STRING (inkompatibel mit numerischen Operatoren).
_TYPES = {
    "sensor-num": "FLOAT",
    "sensor-str": "STRING",
}


@pytest.fixture(autouse=True)
def _tiny_candidate_cap(monkeypatch):
    """Row-Cap klein setzen, um „jenseits des Caps" ohne 10k reale Zeilen zu simulieren."""
    monkeypatch.setattr(ringbuffer_module, "_SEGMENTED_CANDIDATE_CAP", 3, raising=True)


async def _make_rb_old_string_new_numeric(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Älterer ``q``-matchender STRING-Datapoint + viele neuere numerische ``q``-Treffer.

    Die STRING-Zeile (``sensor-str``) ist die ÄLTESTE; danach folgen mehr numerische
    Zeilen (``sensor-num``) als der (klein gemonkeypatchte) Row-Cap. Ein row-gecappter
    Discovery-Scan (neueste zuerst) sieht damit nur die numerischen Zeilen und übersieht
    die alte STRING-Zeile.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Älteste Zeile: STRING-Datapoint, matcht ``q="sensor"``.
    await _record(rb, "hello", "2026-01-01T00:00:00.000Z", datapoint_id="sensor-str", adapter="mixed-adapter")
    # Danach mehr numerische Zeilen als der Row-Cap (=3): jenseits des Caps liegt die
    # STRING-Zeile.
    for i in range(1, 8):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="sensor-num", adapter="mixed-adapter")
    return rb


async def _make_rb_numeric_only(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Rein numerischer ``q``-Scope mit mehr Zeilen als der Row-Cap."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    for i in range(0, 10):
        await _record(rb, i, f"2026-01-01T00:00:{i:02d}.000Z", datapoint_id="sensor-num", adapter="numeric-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: q-scoped EXPORT erkennt den alten STRING-Datapoint jenseits des Caps → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_scoped_export_gt_detects_old_string_beyond_cap(tmp_path: Path):
    """``q``-scoped EXPORT ``gt``; alte STRING-Zeile jenseits des Row-Caps → 422.

    Legacy ist row-lazy und typ-checkt auch die alte STRING-Zeile → ValueError. Der
    segmentierte Export-Pfad muss identisch 422 liefern: mit durchgereichtem
    ``is_export=True`` diskovert die Discovery den ``q``-Scope erschöpfend und sieht
    den älteren inkompatiblen Datapoint.
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
async def test_q_scoped_export_between_detects_old_string_beyond_cap_with_offset(tmp_path: Path):
    """``q``-scoped EXPORT ``between`` mit Offset: der alte STRING-Datapoint erzwingt 422."""
    seg = await _make_rb_old_string_new_numeric(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "between", "lower": 0, "upper": 100}]
        with pytest.raises(ValueError):
            await seg.query_v2(q="sensor", value_filters=vf, datapoint_types=_TYPES, limit=10, offset=2, is_export=True)
    finally:
        await seg.stop()


# ---------------------------------------------------------------------------
# Gegentest: NICHT-Export-Monitor bleibt gedeckelt (kein 422, kein unbounded-Scan)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_scoped_monitor_detects_old_string_like_legacy(tmp_path: Path):
    """Derselbe ``q``-scoped Filter als NICHT-Export-Monitor: 422 wie Legacy (Runde 33).

    Runde 33 (Codex Perf/:2260): Die Discovery läuft jetzt über eine STORE-Level
    ``SELECT DISTINCT datapoint_id``-Query statt Row-Pagination. Sie ist durch die
    ANZAHL DISTINKTER Datapoints begrenzt, NICHT durch einen Row-Cap – der frühere
    „jenseits des Row-Caps unsichtbar"-Blindfleck existiert nicht mehr. Der ältere
    ``q``-matchende STRING-Datapoint wird auch im NICHT-Export-Monitor diskovert →
    422, exakt wie der row-lazy Legacy-Pfad (kein stiller numerischer Drop mehr).
    """
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
