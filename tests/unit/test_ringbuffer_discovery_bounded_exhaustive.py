"""q-/Metadaten-scoped Value-Filter: row-lazy In-Cap-Parität zu ``segmented=False`` (#919, Review #951).

Nach dem Wurzel-Refactor läuft die Value-Filter-Typprüfung row-lazy über die gebundene
Kandidatenmenge, exakt wie ``segmented=False``. Diese Suite prüft die Parität für die
q-/Metadaten-/Adapter-scoped Fälle, in denen die inkompatible Zeile INNERHALB der
Kandidatenmenge (Cap/Zeitfenster) liegt:

* ``q`` matcht per ``datapoint_id`` oder ``source_adapter`` – ein gelöschter/älterer
  STRING-``q``-Treffer in der Kandidatenmenge erzwingt 422 wie Legacy.
* Metadaten-Tag-Scope (windowed inline-``EXISTS`` und unwindowed gedeckelt) – ein
  getaggter STRING-Datapoint erzwingt 422; rein numerische Tag-Treffer nicht.
* ``q`` + Adapter-Scope – ein STRING-``q``-Treffer AUSSERHALB des Adapter-Scopes
  erzwingt kein 422; einer INNERHALB schon.
* ``q`` + Metadaten kombiniert – beide Prädikate in der Kandidatenmenge → 422.

Alle Parität-Aussagen werden gegen ``segmented=False`` (Legacy row-lazy) validiert.
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


async def _record_tagged(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, tags: list[str]) -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter="ada",
        quality="good",
        metadata_version=1,
        metadata={"datapoint": {"tags": tags}},
    )


# ===========================================================================
# q-Scope: ein STRING-``q``-Treffer in der Kandidatenmenge → 422 wie Legacy.
# ===========================================================================


@pytest.mark.asyncio
async def test_q_discovery_parity_deleted_string_dp_rejects_like_legacy(tmp_path: Path):
    """``q``-Scope bleibt row-lazy: ein gelöschter STRING-``q``-Treffer in-cap → 422 wie Legacy."""
    types = {"dp-num-hot": "FLOAT"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # Älterer STRING-Datapoint, matcht q='hot' per id.
        await _record(rb, "warm", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", adapter="ada")
        # Neuere numerische Zeilen eines anderen q-Treffers.
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", adapter="ada")
        await _record(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num-hot", adapter="ada")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="hot", value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="hot", value_filters=vf, datapoint_types=types, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_matches_source_adapter_discovery(tmp_path: Path):
    """``q`` matcht per ``source_adapter`` (nicht id): der STRING-Datapoint in-cap → 422 wie Legacy."""
    types = {"dp-num": "FLOAT"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-Zeile, deren ADAPTER q='knx' matcht (die id NICHT).
        await _record(rb, "state", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", adapter="knx-bus")
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", adapter="knx-bus")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="knx", value_filters=vf, datapoint_types={**types, "dp-str": "STRING"}, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="knx", value_filters=vf, datapoint_types={**types, "dp-str": "STRING"}, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Metadaten-Tag-Scope: ein getaggter STRING-Datapoint in-cap → 422 wie Legacy;
# rein numerische Tag-Treffer nicht.
# ===========================================================================


@pytest.mark.asyncio
async def test_metadata_scope_unwindowed_string_rejects_like_legacy(tmp_path: Path):
    """Metadaten-Tag-Scope + ``gt`` über einen getaggten STRING-Datapoint → 422 wie Legacy."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_metadata_scope_unwindowed_numeric_only_no_422(tmp_path: Path):
    """Gegentest: Metadaten-Tag-Scope matcht nur numerische Datapoints → kein 422."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-Datapoint trägt einen ANDEREN Tag, ist also NICHT im Scope.
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", tags=["licht"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", tags=["klima"])
        await _record_tagged(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        seg_rows = await seg.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        assert sorted(e.new_value for e in seg_rows) == [9]
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Deckungs-/Parität-Fälle: windowed ``q`` (inline ``LIKE``), windowed Metadaten
# (inline ``EXISTS``), ``q`` mit Adapter-Scope und ``q``+Metadaten kombiniert.
# ===========================================================================


@pytest.mark.asyncio
async def test_windowed_q_inline_string_rejects_like_legacy(tmp_path: Path):
    """WINDOWED ``q`` + ``gt`` über eine STRING-Zeile im Fenster → 422 (inline-``LIKE``-Pfad)."""
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record(rb, "state", "2026-01-01T00:01:00.000Z", datapoint_id="dp-str-hot")
        await _record(rb, 5, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num-hot")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(
            q="hot",
            value_filters=vf,
            datapoint_types=types,
            from_ts="2026-01-01T00:00:30.000Z",
            to_ts="2026-01-01T00:02:00.000Z",
            limit=10,
            is_export=True,
        )
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_windowed_metadata_inline_string_rejects_like_legacy(tmp_path: Path):
    """WINDOWED Metadaten-Tag + ``gt`` über eine getaggte STRING-Zeile im Fenster → 422 (inline-``EXISTS``)."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:01:00.000Z", datapoint_id="dp-str", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(
            metadata_tags_any_of=["klima"],
            value_filters=vf,
            datapoint_types=types,
            from_ts="2026-01-01T00:00:30.000Z",
            to_ts="2026-01-01T00:02:00.000Z",
            limit=10,
            is_export=True,
        )
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_with_adapter_scope_summary_filter(tmp_path: Path):
    """``q`` + ``adapter_any_of``-Scope: der Adapter-Scope bindet die Kandidatenmenge (Parität).

    Ein STRING-``q``-Treffer AUSSERHALB des Adapter-Scopes darf KEIN 422 erzwingen;
    ein STRING-``q``-Treffer INNERHALB des Scopes schon.
    """
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-q-Treffer, aber auf einem ANDEREN Adapter (out of scope).
        await _record(rb, "x", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", adapter="other")
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", adapter="scope")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        # Scope = 'scope': der STRING-Treffer auf 'other' ist ausgeschlossen → kein 422.
        legacy_rows = await legacy.query_v2(q="hot", adapter_any_of=["scope"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        seg_rows = await seg.query_v2(q="hot", adapter_any_of=["scope"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        assert sorted(e.new_value for e in seg_rows) == [5]
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_and_metadata_combined_capped_rejects_like_legacy(tmp_path: Path):
    """``q`` UND Metadaten-Tag über eine STRING-Zeile in der Kandidatenmenge → 422 wie Legacy."""
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(q="hot", metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()
