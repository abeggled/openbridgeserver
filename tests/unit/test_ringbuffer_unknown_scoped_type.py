"""Scoped Value-Filter über GELÖSCHTE Datapoints: unbekannter Typ → 422 (#919, Review #951).

Codex-Finding (``ringbuffer.py`` :2227, Follow-up auf Runde 28/29/30): Liefert die
scoped Discovery einen historischen Datapoint, der NICHT (mehr) in der Registry
(``datapoint_types``) ist, wird ``datapoint_types.get(dp_id)`` zu einem LEEREN Typ.
Der Validator ``_validate_segmented_value_filter_types`` lehnte bisher für non-``eq``/
``ne``-Filter nur BEKANNTE STRING/BOOLEAN-Typen ab. Ein leerer/unbekannter Kandidaten-
Typ rutschte durch → der segmentierte Pfad pushte trotzdem ein numerisches SQL-Prädikat
und droppte string/bool-Zeilen des gelöschten Datapoints STILL.

Der Legacy-Pfad ist dagegen row-lazy: bei leerem ``data_type`` leitet er den Typ aus
dem Zeilenwert ab (``_is_string_type``/``_is_boolean_type``) und wirft für eine
STRING-/BOOLEAN-Zeile unter ``gt``/``between`` einen ``ValueError`` (→ 422). Da der
segmentierte Pfad den Row-Wert beim Pushdown nicht kennt, ist die konservative
Ablehnung (422) die parität-wahrende Wahl statt still numerisch zu pushen.

Diese Suite fixiert:

* scoped ``gt``/``between`` über einen Datapoint MIT Zeilen im Buffer, aber OHNE
  Registry-Typ → 422 (statt still gedroppter Zeilen).
* Gegentest: bekannter numerischer Typ im selben Scope → KEIN 422.
* Gegentest: ``eq``/``ne`` mit unbekanntem Typ → KEIN falsches 422 (typunabhängig).
* Regression: bekannter STRING → weiterhin 422 (unverändert).
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


# Registry-Typuniversum: der gelöschte Datapoint ``dp-gone`` fehlt bewusst, obwohl
# er Zeilen im Buffer hat (historischer Datapoint, aus der Registry entfernt).
_TYPES_WITH_GAP = {
    "dp-num": "FLOAT",
}


async def _make_rb_with_deleted_dp(tmp_path: Path, *, segmented: bool, deleted_value: object) -> RingBuffer:
    """Installation mit einem gelöschten Datapoint ``dp-gone`` (kein Registry-Typ).

    ``deleted-adapter`` liefert ausschließlich den gelöschten Datapoint ``dp-gone``.
    ``numeric-adapter`` liefert den bekannten FLOAT-Datapoint ``dp-num``.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, deleted_value, "2026-01-01T00:00:02.000Z", datapoint_id="dp-gone", adapter="deleted-adapter")
    return rb


# ---------------------------------------------------------------------------
# Case A: scoped gt über gelöschten Datapoint (STRING-Zeile) → 422 wie Legacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_gt_over_deleted_string_dp_rejects_like_legacy(tmp_path: Path):
    """``adapter_any_of=['deleted-adapter']`` + ``gt`` über gelöschten Datapoint mit STRING-Zeile.

    Legacy leitet aus dem STRING-Zeilenwert den Typ ab und wirft ``ValueError``.
    Der segmentierte Pfad darf die Zeile nicht still numerisch droppen, sondern
    muss den unbekannten Kandidatentyp konservativ mit 422 ablehnen.
    """
    legacy = await _make_rb_with_deleted_dp(tmp_path / "legacy", segmented=False, deleted_value="hello")
    seg = await _make_rb_with_deleted_dp(tmp_path / "seg", segmented=True, deleted_value="hello")
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(adapter_any_of=["deleted-adapter"], value_filters=vf, datapoint_types=_TYPES_WITH_GAP, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["deleted-adapter"], value_filters=vf, datapoint_types=_TYPES_WITH_GAP, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_adapter_scoped_between_over_deleted_string_dp_rejects(tmp_path: Path):
    """``between`` über gelöschten Datapoint mit STRING-Zeile → segmentiert 422."""
    seg = await _make_rb_with_deleted_dp(tmp_path / "seg", segmented=True, deleted_value="hello")
    try:
        vf = [{"operator": "between", "lower": 1, "upper": 10}]
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["deleted-adapter"], value_filters=vf, datapoint_types=_TYPES_WITH_GAP, limit=10)
    finally:
        await seg.stop()


# ---------------------------------------------------------------------------
# Gegentest: bekannter numerischer Typ im selben Scope → KEIN 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_gt_over_known_numeric_dp_unchanged(tmp_path: Path):
    """``adapter_any_of=['numeric-adapter']`` + ``gt`` auf bekannten FLOAT → kein 422, liefert ``[9]``."""
    seg = await _make_rb_with_deleted_dp(tmp_path / "seg", segmented=True, deleted_value="hello")
    try:
        rows = await seg.query_v2(
            adapter_any_of=["numeric-adapter"],
            value_filters=[{"operator": "gt", "value": 6}],
            datapoint_types=_TYPES_WITH_GAP,
            limit=10,
        )
        assert [e.new_value for e in rows] == [9]
    finally:
        await seg.stop()


# ---------------------------------------------------------------------------
# Gegentest: eq/ne mit unbekanntem Typ → KEIN falsches 422 (typunabhängig)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_eq_over_deleted_dp_not_rejected(tmp_path: Path):
    """``eq`` über gelöschten Datapoint mit unbekanntem Typ bleibt erlaubt (kein 422).

    ``eq``/``ne`` sind typunabhängig und werden vom Validator nie eingeschränkt.
    Legacy und segmentiert liefern gleichermaßen die matchende STRING-Zeile.
    """
    legacy = await _make_rb_with_deleted_dp(tmp_path / "legacy", segmented=False, deleted_value="hello")
    seg = await _make_rb_with_deleted_dp(tmp_path / "seg", segmented=True, deleted_value="hello")
    try:
        vf = [{"operator": "eq", "value": "hello"}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["deleted-adapter"], value_filters=vf, datapoint_types=_TYPES_WITH_GAP, limit=10)
        seg_rows = await seg.query_v2(adapter_any_of=["deleted-adapter"], value_filters=vf, datapoint_types=_TYPES_WITH_GAP, limit=10)
        assert [e.new_value for e in legacy_rows] == ["hello"]
        assert [e.new_value for e in seg_rows] == ["hello"]
    finally:
        await legacy.stop()
        await seg.stop()


# ---------------------------------------------------------------------------
# Regression: bekannter STRING-Typ bleibt 422 (unverändert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_scoped_gt_over_known_string_dp_still_rejects(tmp_path: Path):
    """Bekannter STRING-Datapoint + ``gt`` → weiterhin 422 (Verhalten unverändert)."""
    types = {"dp-num": "FLOAT", "dp-gone": "STRING"}
    seg = await _make_rb_with_deleted_dp(tmp_path / "seg", segmented=True, deleted_value="hello")
    try:
        with pytest.raises(ValueError):
            await seg.query_v2(adapter_any_of=["deleted-adapter"], value_filters=[{"operator": "gt", "value": 1}], datapoint_types=types, limit=10)
    finally:
        await seg.stop()
