"""Opt-in-Verdrahtung des Segment-Stores in den RingBuffer (#919).

Kerninvariante: solange ``segmented=False`` (Default), ändert sich das Verhalten
des RingBuffers in keiner Weise — der Legacy-Single-File-Pfad bleibt aktiv. Diese
Suite deckt den *eingeschalteten* Pfad ab (Konstruktion, Schreiben→Segment,
Read-back, Rotation nach ``segment_max_*``, Retention nach Rotation, Legacy-DB
read-only attach + gemischte Ordnung, Stats mit Segmentzahl) sowie die bewusst
deklariert-unsupported query_v2-Features (ValueError → 422 im API-Layer).

Die Flag-AUS-Regression selbst wird durch die unveränderten bestehenden
Ringbuffer-Unit-Tests abgesichert; hier wird zusätzlich explizit geprüft, dass im
Default kein Store gebaut wird.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import (
    RingBuffer,
    RingBufferStorageDeleteIncompleteError,
    delete_ringbuffer_storage_files,
    derive_segment_max_bytes,
)
from obs.ringbuffer.store.config import RETENTION_SEGMENT_RATIO, SegmentConfig, StoreRetentionConfig, validate_store_config


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-seg", adapter: str = "api") -> None:
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


# ---------------------------------------------------------------------------
# Default (Flag AUS): kein Store, unveränderter Legacy-Pfad
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_is_not_segmented_and_builds_no_store(tmp_path: Path):
    rb = _rb(tmp_path)
    assert rb.segmented is False
    await rb.start()
    try:
        assert rb.store is None
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        entries = await rb.query_v2()
        assert [e.new_value for e in entries] == [1]
        stats = await rb.stats()
        assert "store" not in stats
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Schreiben → Segment, Read-back über den Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_query_returns_empty_when_not_started(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    # Kein start() → kein Store; Query darf nicht crashen, sondern liefert [].
    assert await rb.query_v2() == []


@pytest.mark.asyncio
async def test_segmented_write_goes_to_store_and_reads_back(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        assert rb.store is not None
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        entries = await rb.query_v2(limit=10)
        # newest-first
        assert [e.new_value for e in entries] == [2, 1, 0]
        # Store trägt die Zeilen, nicht die Legacy-Connection.
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["total"] == 3
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Rotation nach segment_max_rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_rows(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        for value in range(5):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        # 5 rows, rotate every 2 → mehrere Segmente.
        assert store_stats["common"]["segment_count"] >= 2
        # Read-back bleibt korrekt segmentübergreifend geordnet.
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [4, 3, 2, 1, 0]
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: enforce_retention nach Rotation (max_entries segmentgenau)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_retention_drops_closed_segments(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2, max_entries=6)
    await rb.start()
    try:
        for value in range(10):
            await _record(rb, value, f"2026-01-01T00:00:{value:02d}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        # Retention hält segmentgenau unter/nahe max_entries — jedenfalls < 10.
        assert store_stats["common"]["total"] < 10
        # Das jüngste Event bleibt erhalten.
        entries = await rb.query_v2(limit=1)
        assert entries[0].new_value == 9
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Legacy-DB beim Start read-only attached; gemischte Ordnung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_attaches_legacy_db_and_merges_ordered(tmp_path: Path):
    disk_path = tmp_path / "obs_ringbuffer.db"
    # 1) Legacy-Single-File-RingBuffer befüllen und schließen.
    legacy = RingBuffer(storage="file", disk_path=str(disk_path))
    await legacy.start()
    for value in range(3):
        await legacy.record(
            ts=f"2025-01-01T00:00:0{value}.000Z",
            datapoint_id="dp-seg",
            topic="dp/dp-seg/value",
            old_value=None,
            new_value=100 + value,
            source_adapter="api",
            quality="good",
        )
    await legacy.stop()

    # 2) Segmentierter RingBuffer auf demselben disk_path: Legacy read-only attach.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb.start()
    try:
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")
        entries = await rb.query_v2(limit=10)
        values = [e.new_value for e in entries]
        # Neuer v2-Wert zuerst, danach die Legacy-Werte (newest-first).
        assert values[0] == 200
        assert set(values) == {200, 102, 101, 100}
        assert values == [200, 102, 101, 100]
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: Stats zeigen Segmentzahl / aktives Segment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_stats_expose_segment_info(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        stats = await rb.stats()
        assert stats["storage"] == "file"
        assert "store" in stats
        assert stats["store"]["common"]["segment_count"] >= 2
        assert stats["store"]["backend_extra"]["active_segment_id"] is not None
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: deklariert-unsupported query_v2-Features → ValueError (422-tauglich)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_query_multi_filters_and_sort_have_parity(tmp_path: Path):
    """Features, die früher segmented abgelehnt wurden, liefern jetzt echte Ergebnisse (#919).

    Freitext-``q``, ``dp_ids_by_name``, mehrere Adapter/datapoint_ids sowie
    Sortierung nach ``id``/``ts`` × ``asc``/``desc`` werden gebunden über den Store
    bedient statt als 422 abgewiesen.
    """
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z", datapoint_id="dp-a", adapter="api")
        await _record(rb, 2, "2026-01-01T00:00:01.000Z", datapoint_id="dp-b", adapter="knx")

        # Freitext-q matcht datapoint_id/source_adapter per LIKE (Legacy-Semantik).
        by_q = await rb.query_v2(q="dp-a", limit=10)
        assert [e.new_value for e in by_q] == [1]

        # dp_ids_by_name → IN (...) auf datapoint_id.
        by_name = await rb.query_v2(dp_ids_by_name=["dp-b"], limit=10)
        assert [e.new_value for e in by_name] == [2]

        # Mehrere Adapter (any_of) → IN (...).
        by_adapters = await rb.query_v2(adapter_any_of=["api", "knx"], sort_field="ts", sort_order="asc", limit=10)
        assert [e.new_value for e in by_adapters] == [1, 2]

        # Mehrere datapoint_ids → IN (...).
        by_dps = await rb.query_v2(datapoint_ids=["dp-a", "dp-b"], sort_field="id", sort_order="asc", limit=10)
        assert [e.new_value for e in by_dps] == [1, 2]

        # Sortierung: id/desc (Default), id/asc, ts/desc, ts/asc.
        assert [e.new_value for e in await rb.query_v2(limit=10)] == [2, 1]
        assert [e.new_value for e in await rb.query_v2(sort_field="id", sort_order="asc", limit=10)] == [1, 2]
        assert [e.new_value for e in await rb.query_v2(sort_field="ts", sort_order="desc", limit=10)] == [2, 1]
        assert [e.new_value for e in await rb.query_v2(sort_field="ts", sort_order="asc", limit=10)] == [1, 2]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_metadata_tag_and_binding_pushdown(tmp_path: Path):
    """Metadaten-Tag/Binding-Filter werden als EXISTS-Subquery pro Segment gepusht (#919)."""
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await rb.record(
            ts="2026-01-01T00:00:00.000Z",
            datapoint_id="dp-knx",
            topic="dp/dp-knx/value",
            old_value=None,
            new_value=10,
            source_adapter="api",
            quality="good",
            metadata_version=1,
            metadata={
                "datapoint": {"tags": ["living-room"]},
                "bindings": [{"adapter_type": "KNX", "normalized": {"group_address": "1/2/3"}}],
            },
        )
        await rb.record(
            ts="2026-01-01T00:00:01.000Z",
            datapoint_id="dp-mqtt",
            topic="dp/dp-mqtt/value",
            old_value=None,
            new_value=20,
            source_adapter="api",
            quality="good",
            metadata_version=1,
            metadata={
                "datapoint": {"tags": ["garage"]},
                "bindings": [{"adapter_type": "MQTT", "normalized": {"topic": "home/garage"}}],
            },
        )

        by_tag = await rb.query_v2(metadata_tags_any_of=["living-room"], limit=10)
        assert [e.new_value for e in by_tag] == [10]

        by_binding = await rb.query_v2(
            metadata_adapter_types_any_of=["knx"],
            metadata_group_addresses_any_of=["1/2/3"],
            limit=10,
        )
        assert [e.new_value for e in by_binding] == [10]

        by_topic = await rb.query_v2(metadata_topics_any_of=["home/garage"], limit=10)
        assert [e.new_value for e in by_topic] == [20]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_value_filter_type_conflict_raises(tmp_path: Path):
    """Numerischer Operator auf BOOLEAN-Datenpunkt → ValueError (Legacy-Parität, #919)."""
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, True, "2026-01-01T00:00:00.000Z", datapoint_id="dp-bool")
        with pytest.raises(ValueError, match="not supported for data_type 'BOOLEAN'"):
            await rb.query_v2(
                datapoint_ids=["dp-bool"],
                value_filters=[{"operator": "gt", "value": 0}],
                datapoint_types={"dp-bool": "BOOLEAN"},
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_value_filter_type_conflict_for_name_resolved_dp_raises(tmp_path: Path):
    """Namensaufgelöste Datenpunkte (``dp_ids_by_name``) müssen dieselbe Typkonflikt-422 liefern (#951, Pkt 2).

    Zielt eine Freitextsuche über den Datapoint-NAMEN (→ ``dp_ids_by_name``) auf ein
    BOOLEAN-Datapoint und sendet einen numerischen Operator, darf der segmentierte
    Pfad NICHT still ``[]`` zurückgeben, sondern muss – wie bei id-aufgelösten
    Datapoints – einen ``ValueError`` (422-tauglich) werfen.
    """
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, True, "2026-01-01T00:00:00.000Z", datapoint_id="dp-bool")
        with pytest.raises(ValueError, match="not supported for data_type 'BOOLEAN'"):
            await rb.query_v2(
                dp_ids_by_name=["dp-bool"],
                value_filters=[{"operator": "gt", "value": 0}],
                datapoint_types={"dp-bool": "BOOLEAN"},
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_rejects_invalid_sort(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match="invalid sort field"):
            await rb.query_v2(sort_field="bogus")
        with pytest.raises(ValueError, match="invalid sort order"):
            await rb.query_v2(sort_order="bogus")
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_bytes(tmp_path: Path):
    # Sehr kleines Byte-Budget → Rotation greift schon nach wenigen Zeilen.
    rb = _rb(tmp_path, segmented=True, segment_max_bytes=1)
    await rb.start()
    try:
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["segment_count"] >= 2
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_rotation_after_segment_max_age(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, segment_max_age=3600)
    await rb.start()
    try:
        # Aktives Segment künstlich altern lassen → nächster Write rotiert.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        await _record(rb, 2, "2026-01-01T00:00:01.000Z")
        store_stats = (await rb.store.stats()).as_dict()
        assert store_stats["common"]["segment_count"] >= 2
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_rejects_invalid_pagination_and_time(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await rb.query_v2(limit=0)
        with pytest.raises(ValueError, match="offset must be >= 0"):
            await rb.query_v2(offset=-1)
        with pytest.raises(ValueError, match="effective 'from' must be earlier"):
            await rb.query_v2(from_ts="2026-01-01T00:00:10.000Z", to_ts="2026-01-01T00:00:00.000Z")
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_handle_value_event_records_to_store(tmp_path: Path):
    from datetime import UTC, datetime

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:

        class _Evt:
            datapoint_id = "dp-evt"
            value = 42
            source_adapter = "api"
            quality = "good"
            ts = datetime(2026, 1, 1, tzinfo=UTC)

        await rb.handle_value_event(_Evt())
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [42]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_query_supports_core_filters(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z", datapoint_id="dp-a", adapter="api")
        await _record(rb, 2, "2026-01-01T00:00:01.000Z", datapoint_id="dp-b", adapter="knx")
        # Single datapoint_id + single adapter + time window: unterstützt.
        entries = await rb.query_v2(datapoint_ids=["dp-a"], limit=10)
        assert [e.new_value for e in entries] == [1]
        entries = await rb.query_v2(adapter_any_of=["knx"], limit=10)
        assert [e.new_value for e in entries] == [2]
        # value_filter (Kernfeld) wird an den Store gepusht.
        entries = await rb.query_v2(value_filters=[{"operator": "gte", "value": 2}], limit=10)
        assert [e.new_value for e in entries] == [2]
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# (f) segment_max_bytes-Ableitung aus max_file_size_bytes erfüllt 3-Segment-Regel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "max_file_size_bytes",
    [
        None,  # unbegrenzt → fester Default (budget-unabhängig)
        3,  # kleinste Größe, für die die 3-Segment-Regel überhaupt erfüllbar ist
        1024,  # winziges Budget: KEIN Floor, damit Auto-Start nie 422 auslöst
        10 * 1024 * 1024,  # 10 MiB
        100 * 1024 * 1024,  # 100 MiB (der deployte Default)
        4 * 1024 * 1024 * 1024,  # 4 GiB (Ceil = 256 MiB greift)
    ],
)
def test_derive_segment_max_bytes_satisfies_three_segment_rule(max_file_size_bytes):
    derived = derive_segment_max_bytes(max_file_size_bytes)
    assert derived >= 1
    if max_file_size_bytes is None:
        # Kein Size-Budget → fester Default von 256 MiB, NICHT budgetabhängig.
        assert derived == 256 * 1024 * 1024
        return
    # Ableitung ist immer <= budget // 3 → die 3-Segment-Regel hält automatisch,
    # kein 422 im Auto-Startpfad, auch für winzige Budgets.
    assert derived == max(1, min(256 * 1024 * 1024, max_file_size_bytes // RETENTION_SEGMENT_RATIO))
    assert max_file_size_bytes >= RETENTION_SEGMENT_RATIO * derived
    validate_store_config(
        SegmentConfig(segment_max_bytes=derived),
        StoreRetentionConfig(max_file_size_bytes=max_file_size_bytes),
    )


@pytest.mark.parametrize("max_file_size_bytes", [1, 2])
def test_derive_segment_max_bytes_sub_floor_budget_stays_rule_conform(max_file_size_bytes):
    """Winzige Budgets < RETENTION_SEGMENT_RATIO Bytes (#951 P2).

    ``max_file_size_bytes`` ist per API-Modell ``ge=1`` – Budgets von 1 oder 2
    Byte sind also gültige (wenn auch degenerierte) Eingaben. Für sie ist die
    3-Segment-Regel mit einem POSITIVEN Segment mathematisch unerfüllbar
    (``3 * 1 = 3 > 2``). Die Auto-Ableitung muss trotzdem ein positives Segment
    liefern, das ``validate_store_config`` NICHT scheitern lässt (kein Startup-
    Crash): dazu wird das effektive Budget für die Regel auf die technische
    Untergrenze ``RETENTION_SEGMENT_RATIO`` gehoben.
    """
    derived = derive_segment_max_bytes(max_file_size_bytes)
    assert derived >= 1
    # Regel gegen das auf die technische Untergrenze angehobene Budget: der
    # abgeleitete Wert scheitert NIE an validate_store_config im Auto-Start.
    effective_budget = max(max_file_size_bytes, RETENTION_SEGMENT_RATIO)
    assert RETENTION_SEGMENT_RATIO * derived <= effective_budget
    validate_store_config(
        SegmentConfig(segment_max_bytes=derived),
        StoreRetentionConfig(max_file_size_bytes=effective_budget),
    )


def test_derive_segment_max_bytes_ceiling_for_large_budget():
    # 4 GiB // 3 = ~1.33 GiB → auf 256 MiB gedeckelt, und 4 GiB >= 3*256 MiB.
    derived = derive_segment_max_bytes(4 * 1024 * 1024 * 1024)
    assert derived == 256 * 1024 * 1024


def test_derive_segment_max_bytes_small_budget_has_no_floor():
    # 1024 Bytes // 3 = 341 → kein 4-MiB-Floor, damit Auto-Start nie 422 auslöst.
    assert derive_segment_max_bytes(1024) == 341
    # Winzigstes gültiges Budget: 3 // 3 = 1.
    assert derive_segment_max_bytes(3) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("max_file_size_bytes", [1, 2])
async def test_segmented_start_does_not_crash_for_sub_floor_budget(tmp_path: Path, max_file_size_bytes):
    """Persistiertes Winzig-Budget (1/2 Byte) darf den Startup NICHT crashen (#951 P2).

    Der API-Layer akzeptiert ``max_file_size_bytes >= 1`` bei Auto-Segment
    (``segment_max_bytes=None``, ratio-Check übersprungen). Ein solcher Wert
    landet persistiert und muss beim segmentierten Start über die Auto-Ableitung
    OHNE ``validate_store_config``-Crash öffnen.
    """
    rb = _rb(tmp_path, segmented=True, max_file_size_bytes=max_file_size_bytes)
    await rb.start()
    try:
        assert rb.store is not None
        assert rb._segment_max_bytes >= 1
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_start_derives_segment_max_bytes_from_file_size(tmp_path: Path):
    # 2 GiB Size-Budget → derived = min(256 MiB, 2GiB//3) = 256 MiB.
    rb = _rb(tmp_path, segmented=True, max_file_size_bytes=2 * 1024 * 1024 * 1024)
    await rb.start()
    try:
        assert rb._segment_max_bytes == 256 * 1024 * 1024
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_start_respects_explicit_segment_max_bytes(tmp_path: Path):
    rb = _rb(tmp_path, segmented=True, max_file_size_bytes=None, segment_max_bytes=1234567)
    await rb.start()
    try:
        assert rb._segment_max_bytes == 1234567
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# (a)/(b) Legacy auto-attach beim Start + Idempotenz bei Neustart
# ---------------------------------------------------------------------------


async def _seed_legacy(disk_path: Path, count: int = 3) -> None:
    legacy = RingBuffer(storage="file", disk_path=str(disk_path))
    await legacy.start()
    for value in range(count):
        await legacy.record(
            ts=f"2025-01-01T00:00:0{value}.000Z",
            datapoint_id="dp-seg",
            topic="dp/dp-seg/value",
            old_value=None,
            new_value=100 + value,
            source_adapter="api",
            quality="good",
        )
    await legacy.stop()


@pytest.mark.asyncio
async def test_segmented_start_auto_attaches_legacy_readonly(tmp_path: Path):
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb.start()
    try:
        legacy_segments = await rb.store.manifest.list_legacy_segments()
        assert len(legacy_segments) == 1
        # Read-only in place: filename ist der absolute Legacy-Pfad, nicht unter segments/.
        assert legacy_segments[0].filename == str(disk_path.resolve())
        # Legacy-Daten sind lesbar.
        entries = await rb.query_v2(limit=10)
        assert {e.new_value for e in entries} == {100, 101, 102}
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_post_upgrade_append_reclaims_over_budget_legacy_without_rotation(tmp_path: Path):
    """#951 Pkt 1: über-budget-Legacy wird nach dem ERSTEN Post-Upgrade-Append zurückgewonnen – auch ohne fällige Rotation.

    Beim Start greift der No-Zero-History-Guard (noch keine v2-Zeile) → der Startup-
    Retention-Lauf kann die attached Legacy-DB nicht löschen. Erst NACH dem ersten
    segmentierten Append ist der Guard erfüllt. Mit hohen Rotations-Schwellen ist
    KEINE Rotation fällig; trotzdem muss die über-budget-Legacy zeitnah freigegeben
    werden (sonst bliebe sie bis zur ersten Rotation liegen, #919-Kernszenario).
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    # Hohe Rotations-Schwelle (kein Row-Budget): der erste Append löst keine Rotation aus.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1000, max_entries=None)
    await rb.start()
    try:
        # Vorbedingung: Legacy attached, Guard greift (keine v2-Zeile) → noch nicht löschbar.
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Hartes Budget nachträglich auf 1 Byte: die attached Legacy-DB ist damit
        # klar über Budget und – sobald der Guard erfüllt ist – reclaimbar.
        rb.store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)

        # Erster Post-Upgrade-Append: Guard jetzt erfüllt, aber KEINE Rotation fällig.
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")

        # Kein neues geschlossenes Segment durch Rotation …
        assert (await rb.store.stats()).as_dict()["backend_extra"]["active_segment_id"] is not None
        # … dennoch ist die über-budget-Legacy zurückgewonnen (Fix greift ohne Rotation).
        assert await rb.store.manifest.list_legacy_segments() == []
        # Der frische v2-Wert bleibt lesbar (No-Zero-History gewahrt).
        assert [e.new_value for e in await rb.query_v2(limit=10)] == [200]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_append_without_attached_legacy_skips_extra_enforce(tmp_path: Path, monkeypatch):
    """#951 Pkt 1: im Normalbetrieb (kein attached Legacy) läuft KEIN zusätzliches enforce_retention pro Append.

    Kostenbegrenzung: der Post-Upgrade-Zweig darf nur solange feuern, wie ein
    attached Legacy-Segment existiert. Ohne Legacy und ohne fällige Rotation wird
    ``enforce_retention`` je Append NICHT aufgerufen.
    """
    rb = _rb(tmp_path, segmented=True, segment_max_rows=1000, max_entries=None)
    await rb.start()
    try:
        assert await rb.store.manifest.list_legacy_segments() == []

        calls = 0
        real_enforce = rb.store.enforce_retention

        async def counting_enforce():
            nonlocal calls
            calls += 1
            return await real_enforce()

        monkeypatch.setattr(rb.store, "enforce_retention", counting_enforce)

        for value in range(3):
            await _record(rb, value, f"2026-06-01T00:00:0{value}.000Z")

        # Kein attached Legacy + keine Rotation fällig → kein zusätzliches enforce.
        assert calls == 0
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_start_legacy_attach_is_idempotent_across_restarts(tmp_path: Path):
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb1 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb1.start()
    assert len(await rb1.store.manifest.list_legacy_segments()) == 1
    await rb1.stop()

    # Neustart auf derselben Root: darf NICHT ein zweites Legacy-Segment einhängen.
    rb2 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb2.start()
    try:
        assert len(await rb2.store.manifest.list_legacy_segments()) == 1
    finally:
        await rb2.stop()


@pytest.mark.asyncio
async def test_segmented_start_does_not_reattach_quarantined_legacy(tmp_path: Path):
    """#951 Pkt 1: eine quarantänierte Legacy-Datei darf beim Neustart NICHT erneut eingehängt werden.

    Quarantiniert ein Read-Fehler die attached Legacy-DB, ändert ``mark_quarantined``
    den Status von ``legacy`` auf ``quarantined``, behält aber Dateiname +
    ``schema_version``. Der bisherige Idempotenz-Guard prüfte nur
    ``list_legacy_segments()`` (``status='legacy'``) und sah die quarantänierte
    Legacy-Zeile NICHT → beim nächsten Startup versuchte er, denselben absoluten
    Dateinamen erneut zu inserten → Manifest-``UNIQUE``-Constraint → Startup-Abbruch.
    Der Guard muss schema-version-basiert ALLE Legacy-Zeilen (auch ``quarantined``)
    berücksichtigen.
    """
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_QUARANTINED

    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    # 1) Segmentierter Start hängt die Legacy-DB read-only ein.
    rb1 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb1.start()
    legacy = await rb1.store.manifest.list_legacy_segments()
    assert len(legacy) == 1
    legacy_segment_id = legacy[0].segment_id
    # 2) Ein Read-Fehler quarantiniert genau diese Legacy-Zeile: Status wechselt
    #    von ``legacy`` weg, Dateiname + schema_version bleiben.
    await rb1.store.manifest.mark_quarantined(legacy_segment_id, "simulierter Read-Fehler")
    await rb1.stop()

    # Vorbedingung: die Legacy-Zeile ist jetzt quarantiniert, nicht mehr ``legacy``.
    from obs.ringbuffer.store.manifest import Manifest

    manifest = Manifest(disk_path.parent / "obs_ringbuffer_segments" / "manifest.sqlite")
    await manifest.open()
    assert await manifest.list_legacy_segments() == []
    quarantined = await manifest.get_segment(legacy_segment_id)
    assert quarantined.status == SEGMENT_STATUS_QUARANTINED
    assert quarantined.filename == str(disk_path.resolve())
    await manifest.close()

    # 3) Neustart auf derselben Root darf NICHT erneut einhängen (kein UNIQUE-Crash),
    #    d.h. start() gelingt und es entsteht KEINE zweite Zeile für dieselbe Datei.
    rb2 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb2.start()
    try:
        all_segments = await rb2.store.manifest.list_segments()
        matches = [s for s in all_segments if s.filename == str(disk_path.resolve())]
        assert len(matches) == 1
        assert matches[0].status == SEGMENT_STATUS_QUARANTINED
    finally:
        await rb2.stop()


# ---------------------------------------------------------------------------
# Zeitgetriebene Rotation als PRIMÄRER Trigger — 6-h-Default (#919)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Live-Reconfigure der Segment-/Retention-Config (#919/#938)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_segment_max_age_updates_store_and_prognosis(tmp_path: Path):
    """reconfigure(segment_max_age=…) aktualisiert self._segment_max_age UND den Store-SegmentConfig live."""
    rb = _rb(tmp_path, segmented=True, segment_max_age=3600)
    await rb.start()
    try:
        assert rb._segment_max_age == 3600
        assert rb.store._segment_config.segment_max_age == 3600

        await rb.reconfigure("file", segment_max_age=600)

        # RingBuffer-Feld + Store-SegmentConfig tragen sofort den neuen Wert.
        assert rb._segment_max_age == 600
        assert rb.store._segment_config.segment_max_age == 600
        # Die Prognose spiegelt den effektiven Segment-Cap über self._segment_config.
        assert rb.store._compute_prognosis([])["effective_segment_max_bytes"] == rb.store._segment_config.segment_max_bytes
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_segment_max_age_triggers_immediate_rotation(tmp_path: Path):
    """Sofort-Rotation: ein künstlich gealtertes aktives Segment wird bei reconfigure sofort rotiert."""
    rb = _rb(tmp_path, segmented=True, segment_max_age=86400)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        segments_before = (await rb.store.stats()).as_dict()["common"]["segment_count"]

        # Aktives Segment weit in die Vergangenheit setzen → mit kleinem
        # segment_max_age liegt es sofort über der Schwelle.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        await rb.reconfigure("file", segment_max_age=300)

        segments_after = (await rb.store.stats()).as_dict()["common"]["segment_count"]
        # SOFORT rotiert, ohne weiteres Append.
        assert segments_after > segments_before
        # Nach der Rotation ist das aktive Segment frisch (created_at zurückgesetzt).
        assert rb._segment_created_at > "2000-01-01T00:00:00.000Z"
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_max_file_size_propagates_retention_to_store(tmp_path: Path):
    """reconfigure(max_file_size_bytes=…) propagiert Retention live an den Store; enforce_retention wirkt."""
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        # Mehrere geschlossene Segmente erzeugen (rotate alle 2 Zeilen).
        for value in range(8):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")
        stats_before = (await rb.store.stats()).as_dict()["common"]
        total_before = stats_before["total"]
        assert total_before == 8

        # Size-Budget knapp unter dem aktuellen Volumen setzen → Store-Retention
        # muss die ältesten geschlossenen Segmente sofort freigeben, ohne alles zu
        # löschen (die jüngsten Segmente bleiben unter Budget erhalten).
        budget = int(stats_before["size_bytes"] * 0.6)
        await rb.reconfigure("file", max_file_size_bytes=budget)

        assert rb.store._retention_config.max_file_size_bytes == budget
        total_after = (await rb.store.stats()).as_dict()["common"]["total"]
        assert 0 < total_after < total_before
        # Jüngstes Event bleibt erhalten (aktives/neuestes Segment nie zuerst gelöscht).
        entries = await rb.query_v2(limit=1)
        assert entries[0].new_value == 7
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_segment_max_bytes_none_rederives_from_file_size(tmp_path: Path):
    """segment_max_bytes=None → Neu-Ableitung aus dem effektiven max_file_size_bytes."""
    rb = _rb(tmp_path, segmented=True, max_file_size_bytes=None, segment_max_bytes=1234567)
    await rb.start()
    try:
        assert rb._segment_max_bytes == 1234567

        # Neues Size-Budget + Auto-Ableitung (segment_max_bytes=None) in einem Schritt.
        await rb.reconfigure("file", max_file_size_bytes=30 * 1024 * 1024, segment_max_bytes=None)

        expected = derive_segment_max_bytes(30 * 1024 * 1024)
        assert rb._segment_max_bytes == expected
        assert rb.store._segment_config.segment_max_bytes == expected
        assert rb.store._retention_config.max_file_size_bytes == 30 * 1024 * 1024
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_legacy_path_unaffected_by_segment_args(tmp_path: Path):
    """Legacy-Pfad (segmented=False): kein Store, Segment-Args ändern nur die Felder, kein Crash."""
    rb = _rb(tmp_path, max_entries=100)
    await rb.start()
    try:
        assert rb.segmented is False
        assert rb.store is None
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")

        # Segment-Args im Legacy-Pfad: keine Store-Propagation, kein Crash.
        await rb.reconfigure("file", max_entries=50, segment_max_age=600)
        assert rb._max_entries == 50
        assert rb.store is None

        entries = await rb.query(q="dp-seg", limit=10)
        assert [e.new_value for e in entries] == [1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_noop_when_nothing_changes_keeps_segments(tmp_path: Path):
    """Kein gesetzter Parameter → früher Return, Store-Config unverändert."""
    rb = _rb(tmp_path, segmented=True, segment_max_rows=5, segment_max_age=3600)
    await rb.start()
    try:
        before_seg = rb.store._segment_config
        await rb.reconfigure("file")
        # Identisches Objekt → apply_config wurde nicht aufgerufen.
        assert rb.store._segment_config is before_seg
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_delete_storage_files_removes_segment_store_root(tmp_path: Path):
    """#951: Monitor-Disable muss auch das ``<stem>_segments``-Verzeichnis entfernen.

    Der bisherige Disable-Pfad löschte nur die Legacy-Single-DB + Sidecars, nicht
    aber Manifest und Segment-DBs → Re-Enable öffnete die alten Daten wieder statt
    Speicher freizugeben.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb.start()
    await _record(rb, 1, "2026-01-01T00:00:00.000Z")
    await rb.stop()

    segments_root = tmp_path / "obs_ringbuffer_segments"
    assert segments_root.exists() and any(segments_root.iterdir())

    delete_ringbuffer_storage_files(str(disk_path))

    # Segment-Store-Root (Manifest + Segment-DBs) ist rekursiv weg.
    assert not segments_root.exists()


@pytest.mark.asyncio
async def test_delete_storage_files_surfaces_incomplete_segment_root_removal(tmp_path: Path, monkeypatch):
    """#951, Codex :1521: unvollständige Segment-Root-Löschung wird gemeldet, nicht stillgeschluckt.

    ``shutil.rmtree(..., ignore_errors=True)`` ließ die API weitermachen, als wäre der
    Segment-Store gelöscht, obwohl gelockte/permission-blockierte Segmentdaten auf der
    Platte blieben — ein späteres Re-Enable öffnete die vermeintlich verworfene
    Historie wieder. Analog zum Legacy-Datei-Löschpfad muss eine unvollständige
    Löschung des Segment-Roots SURFACED werden (raise), damit der Aufrufer den
    unvollständigen Zustand erkennt.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True)
    await rb.start()
    await _record(rb, 1, "2026-01-01T00:00:00.000Z")
    await rb.stop()

    segments_root = tmp_path / "obs_ringbuffer_segments"
    assert segments_root.exists()

    # Löschung des Segment-Roots scheitert realistisch (gelockte Datei/Permissions):
    # das echte ``shutil.rmtree`` schluckt solche Fehler via ``onexc``-Callback und
    # lässt das Verzeichnis physisch bestehen. Hier emuliert: ein rmtree-Ersatz, der
    # den übergebenen ``onexc``-Callback mit einer PermissionError aufruft und den
    # Segment-Root NICHT entfernt. Der Legacy-Teil (os.remove) läuft zuvor bereits real.
    import obs.ringbuffer.ringbuffer as rbmod

    def _rmtree_reports_via_onexc(path, *, onexc=None, **kwargs):
        exc = PermissionError(f"segment file locked: {path}")
        if onexc is not None:
            onexc(_rmtree_reports_via_onexc, str(path), exc)
        # Verzeichnis bleibt bewusst bestehen (unvollständige Löschung).

    monkeypatch.setattr(rbmod.shutil, "rmtree", _rmtree_reports_via_onexc)

    with pytest.raises(RingBufferStorageDeleteIncompleteError):
        delete_ringbuffer_storage_files(str(disk_path))

    # Der Legacy-Teil ist trotzdem abgeschlossen (Sidecars/Hauptdatei weg), nur der
    # nicht vollständig gelöschte Segment-Root wird als Fehler gemeldet.
    assert not disk_path.exists()
    assert segments_root.exists()


@pytest.mark.asyncio
async def test_segment_created_at_initialized_from_manifest_on_restart(tmp_path: Path):
    """#264: Neustart übernimmt das Segment-Alter aus dem Manifest, NICHT ab now().

    Kernbeleg: ein langlebiges aktives Segment, das Stunden vor dem (Neu-)Start
    angelegt wurde, muss nach ``start()`` sein echtes ``created_at`` tragen — sonst
    misst die Alters-Rotation ab dem Neustart und das Segment altert nie über die
    Schwelle (Nutzer: 2,7-h-Segment rotiert bei segment_max_age=1h nicht).
    """
    disk_path = tmp_path / "obs_ringbuffer.db"

    # 1) Segmentierten Store anlegen und ein aktives Segment schreiben.
    rb1 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_age=3600)
    await rb1.start()
    await _record(rb1, 1, "2026-01-01T00:00:00.000Z")
    active = await rb1.store.manifest.get_active_segment()
    original_created_at = active.created_at
    await rb1.stop()

    # 2) Das aktive Segment künstlich weit in die Vergangenheit datieren, um ein
    #    langlebiges (vor dem Neustart angelegtes) Segment zu simulieren. Der
    #    Manifest-Store liegt unter ``<stem>_segments/manifest.sqlite``.
    old_created_at = "2020-01-01T00:00:00.000Z"
    from obs.ringbuffer.store.manifest import Manifest

    manifest = Manifest(disk_path.parent / "obs_ringbuffer_segments" / "manifest.sqlite")
    await manifest.open()
    await manifest._db.execute("UPDATE segments SET created_at=? WHERE status='active'", (old_created_at,))
    await manifest._db.commit()
    await manifest.close()

    # 3) Neustart: _segment_created_at MUSS das alte Manifest-created_at sein,
    #    NICHT now().
    rb2 = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_age=3600)
    await rb2.start()
    try:
        assert rb2._segment_created_at == old_created_at
        assert rb2._segment_created_at != original_created_at
        # Und die Alters-Rotation ist damit fällig: das Segment ist >> 1 h alt →
        # der nächste Write rotiert (statt ab now() nie zu altern).
        segments_before = (await rb2.store.stats()).as_dict()["common"]["segment_count"]
        await _record(rb2, 2, "2026-01-01T01:00:00.000Z")
        segments_after = (await rb2.store.stats()).as_dict()["common"]["segment_count"]
        assert segments_after > segments_before
    finally:
        await rb2.stop()


# ---------------------------------------------------------------------------
# Flag AN: Read/Rotate-Serialisierung (#951)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_read_survives_concurrent_rotation(tmp_path: Path):
    """Ein Read, der das aktive Segment liest, darf durch eine gleichzeitige
    Rotation (schließt die aktive Connection) nicht mit „closed database" brechen.

    Reproduziert die #951-Pkt-1-Race: ``store.query`` läuft außerhalb ``self._lock``,
    während der Write-Pfad ``_active_conn`` unter genau diesem Lock schließt/tauscht.
    Wir lassen den ersten ``store.query``-Versuch die transiente „closed database" der
    Rotation nachstellen; der Read muss dann korrekt (unter Lock) retryen statt 500.
    """
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        for value in range(3):
            await _record(rb, value, f"2026-01-01T00:00:0{value}.000Z")

        real_query = rb.store.query
        calls = {"n": 0}

        async def flaky_query(store_query):
            calls["n"] += 1
            if calls["n"] == 1:
                # Genau die transiente Rotations-Fehlermeldung, die aiosqlite auf
                # einer während des Reads geschlossenen Connection wirft.
                raise ValueError("cannot operate on a closed database")
            return await real_query(store_query)

        rb.store.query = flaky_query
        entries = await rb.query_v2(limit=10)
        # Retry hat gegriffen: Ergebnis vollständig, kein 500.
        assert calls["n"] == 2
        assert [e.new_value for e in entries] == [2, 1, 0]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_read_reraises_non_rotation_errors(tmp_path: Path):
    """Nur die transiente Rotations-Race („closed database") wird geretryt.

    Ein anderer, echter Fehler aus dem Store-Read darf NICHT als Rotationsrace
    maskiert und still geschluckt werden, sondern muss unverändert propagieren.
    """
    rb = _rb(tmp_path, segmented=True, segment_max_rows=2)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")

        async def broken_query(store_query):
            raise RuntimeError("boom")

        rb.store.query = broken_query
        with pytest.raises(RuntimeError, match="boom"):
            await rb.query_v2(limit=10)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_segmented_rotation_after_default_six_hour_age(tmp_path: Path):
    from obs.ringbuffer.persisted_config import DEFAULT_SEGMENT_MAX_AGE_SECONDS

    # Zeitgetrieben: Segment altert über 6 h → nächster Write rotiert, unabhängig
    # von der Größe (segment_max_bytes bleibt groß, wird nie erreicht).
    rb = _rb(tmp_path, segmented=True, segment_max_age=DEFAULT_SEGMENT_MAX_AGE_SECONDS)
    await rb.start()
    try:
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        segments_before = (await rb.store.stats()).as_dict()["common"]["segment_count"]
        # Aktives Segment künstlich > 6 h altern lassen.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        await _record(rb, 2, "2026-01-01T00:00:01.000Z")
        segments_after = (await rb.store.stats()).as_dict()["common"]["segment_count"]
        assert segments_after > segments_before
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# Flag AN: fehlgeschlagener Startup NACH store.open() schließt den Store (#951)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segmented_startup_failure_after_open_closes_store_and_allows_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wirft ein Folge-Schritt nach ``store.open()``, wird der Store geschlossen.

    ``store.open()`` gelingt (Writer-Lease + Connections offen), aber der
    Startup-Retention-Schritt wirft. Ohne Cleanup bliebe der Store an einer
    Instanz hängen, die ``start()`` nie zurückgibt → Lease/Root belegt, Retry
    scheitert. Erwartet: (a) Fehler propagiert, (b) Store geschlossen und
    ``self._store``/``_segment_created_at`` zurückgesetzt, (c) erneuter Start
    gelingt (kein belegter Segment-Root).
    """
    from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

    calls = {"n": 0}
    real_enforce = SqliteSegmentStore.enforce_retention

    async def flaky_enforce(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("simulierter Manifest-/Permission-Fehler beim Startup")
        return await real_enforce(self)

    monkeypatch.setattr(SqliteSegmentStore, "enforce_retention", flaky_enforce)

    rb = _rb(tmp_path, segmented=True)

    # (a) Der Originalfehler propagiert unverschluckt.
    with pytest.raises(PermissionError):
        await rb.start()

    # (b) Kein Leak: Store geschlossen, Instanzzustand zurückgesetzt.
    assert rb.store is None
    assert rb._segment_created_at is None

    # (c) Retry setzt sauber neu auf (Writer-Lease frei, Segment-Root nicht belegt).
    await rb.start()
    try:
        assert rb.store is not None
        await _record(rb, 1, "2026-01-01T00:00:00.000Z")
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [1]
    finally:
        await rb.stop()
