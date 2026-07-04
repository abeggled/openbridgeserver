"""Codex Runde 37 – zwei P2-Findings in ``ringbuffer.py`` (#951).

F1 (:2269) „Preserve string filters for deleted datapoints":
Ein historischer Datapoint, der nicht mehr in der Registry ist (leerer ``data_type``),
darf im segmentierten Pfad NICHT für STRING-Operatoren (``contains``/``regex``) mit 422
abgelehnt werden. Der Legacy-Pfad leitet den Typ row-lazy aus dem gespeicherten STRING-
Wert ab und liefert matchende Zeilen. Nur numerische Operatoren (``gt``/``lt``/``gte``/
``lte``/``between``) bleiben ohne bekannten Typ konservativ 422 (Runde-31-Verhalten).
``eq``/``ne`` bleiben typunabhängig erlaubt.

F2 (:892) „Treat visible migration chunks as in progress":
Stirbt der Prozess NACHDEM ``_append_with_legacy_gids`` kopierte Legacy-Zeilen
committet/rotiert hat, aber BEVOR diese Segmente ``migrating`` markiert sind, liefert
``_migration_in_progress()`` fälschlich ``False`` – obwohl die Legacy-Quelle noch attached
ist UND sichtbare negative v2-Chunks existieren. Die append/startup-Retention löschte dann
die attached Legacy-DB vor Abschluss der Kopie → Datenverlust. Der Helper muss diesen
Crash-Zustand als „in progress" erkennen.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.config import StoreRetentionConfig


# ===========================================================================
# F1 – gelöschte (nicht-Registry) Datapoints: STRING-Filter nicht ablehnen
# ===========================================================================


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(storage="file", disk_path=str(tmp_path / "obs_ringbuffer.db"), **kwargs)


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


async def _make_deleted_string_rb(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Ein STRING-Datapoint, dessen Registry-Eintrag GELÖSCHT ist (fehlt in ``datapoint_types``).

    Die Zeilen liegen unter Adapter ``ghost-adapter``; der Datapoint ``dp-ghost`` ist
    NICHT im übergebenen ``datapoint_types``-Universum enthalten → leerer/unbekannter Typ.
    """
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    await _record(rb, "hello world", "2026-01-01T00:00:00.000Z", datapoint_id="dp-ghost", adapter="ghost-adapter")
    await _record(rb, "other value", "2026-01-01T00:00:01.000Z", datapoint_id="dp-ghost", adapter="ghost-adapter")
    return rb


# Registry-Universum OHNE ``dp-ghost`` (gelöschter Datapoint): nur ein unrelated Typ.
_TYPES_WITHOUT_GHOST = {"dp-live": "FLOAT"}


@pytest.mark.asyncio
async def test_deleted_datapoint_contains_delivers_rows_parity(tmp_path: Path):
    """Gelöschter STRING-Datapoint + ``contains`` via Adapter-Scope → Zeilen, kein 422.

    Legacy leitet den Typ aus dem gespeicherten String ab und matcht. Der segmentierte
    Pfad muss identisch matchen (Parität), NICHT fälschlich 422 werfen.
    """
    legacy = await _make_deleted_string_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_deleted_string_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "contains", "value": "hello"}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        seg_rows = await seg.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        assert [e.new_value for e in legacy_rows] == ["hello world"]
        assert [e.new_value for e in seg_rows] == ["hello world"]
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_deleted_datapoint_regex_delivers_rows_parity(tmp_path: Path):
    """Gelöschter STRING-Datapoint + ``regex`` via Adapter-Scope → Zeilen, kein 422."""
    legacy = await _make_deleted_string_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_deleted_string_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "regex", "pattern": "^hello"}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        seg_rows = await seg.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        assert [e.new_value for e in legacy_rows] == ["hello world"]
        assert [e.new_value for e in seg_rows] == ["hello world"]
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_deleted_datapoint_numeric_gt_still_rejects(tmp_path: Path):
    """Gegentest: gelöschter Datapoint + numerischer ``gt`` → weiterhin 422 (konservativ, Runde 31).

    Ohne bekannten Typ ist ein numerisches Prädikat nicht sicher pushdown-bar; der
    segmentierte Pfad lehnt wie Runde 31 ab.
    """
    seg = await _make_deleted_string_rb(tmp_path / "seg", segmented=True)
    try:
        with pytest.raises(ValueError):
            await seg.query_v2(
                adapter_any_of=["ghost-adapter"],
                value_filters=[{"operator": "gt", "value": 1}],
                datapoint_types=_TYPES_WITHOUT_GHOST,
                limit=10,
            )
    finally:
        await seg.stop()


@pytest.mark.asyncio
async def test_deleted_datapoint_eq_unchanged(tmp_path: Path):
    """``eq`` auf gelöschtem Datapoint bleibt typunabhängig erlaubt (kein 422), Parität."""
    legacy = await _make_deleted_string_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_deleted_string_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "eq", "value": "hello world"}]
        legacy_rows = await legacy.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        seg_rows = await seg.query_v2(adapter_any_of=["ghost-adapter"], value_filters=vf, datapoint_types=_TYPES_WITHOUT_GHOST, limit=10)
        assert [e.new_value for e in legacy_rows] == ["hello world"]
        assert [e.new_value for e in seg_rows] == ["hello world"]
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# F2 – sichtbare negative v2-Chunks als „migration in progress" behandeln
# ===========================================================================


async def _record_seg(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-seg") -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
    )


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


async def _force_active_row_negative_gid(rb: RingBuffer) -> None:
    """Simuliert den Crash-Zustand: kopierte Legacy-Zeile mit NEGATIVER gid in einem
    sichtbaren, NICHT-``migrating`` v2-Segment.

    Statt die volle Migrator-Sequenz zu fahren, wird die gid einer bereits im aktiven
    (``active``) v2-Segment liegenden Zeile auf einen negativen Wert gesetzt – exakt der
    Zustand, in dem ``_append_with_legacy_gids`` bereits committet/rotiert, aber noch nicht
    ``mark_migrating`` ausgeführt hat.
    """
    active = await rb.store.manifest.get_active_segment()
    assert active is not None
    seg_path = rb.store._segments_dir / active.filename
    async with aiosqlite.connect(str(seg_path)) as conn:
        await conn.execute("UPDATE ringbuffer SET global_event_id = -42 WHERE global_event_id >= 0")
        await conn.commit()


@pytest.mark.asyncio
async def test_visible_negative_v2_chunk_counts_as_in_progress(tmp_path: Path):
    """Attached Legacy + sichtbarer negativer v2-Chunk (nicht ``migrating``) → in progress True.

    Reproduziert das Crash-Fenster: die kopierten negativen Zeilen liegen sichtbar in einem
    ``active``/``closed`` v2-Segment, die Legacy-Quelle ist noch attached, aber es gibt KEIN
    ``migrating``-Segment. Der Helper muss ``True`` melden und die append-getriebene Retention
    darf die attached Legacy NICHT löschen.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1000, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Eine kopierte Legacy-Zeile (negative gid) sichtbar im aktiven v2-Segment ablegen.
        await _record_seg(rb, 900, "2026-05-01T00:00:00.000Z")
        await _force_active_row_negative_gid(rb)

        # KEIN migrating-Segment – dennoch muss der Helper den Crash-Zustand erkennen.
        assert await rb.store.manifest.list_migrating_segments() == []
        assert await rb._migration_in_progress() is True

        # Hartes Size-Budget: die attached Legacy wäre ohne Gating reclaimbar.
        rb.store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)

        # Live-Append läuft über die append-getriebene Retention. Gating greift → Legacy bleibt.
        await _record_seg(rb, 200, "2026-06-01T00:00:00.000Z")
        assert len(await rb.store.manifest.list_legacy_segments()) == 1
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_attached_legacy_without_negative_v2_not_over_deferred(tmp_path: Path):
    """Gegentest: attached read-only Legacy OHNE negative v2-Chunks → in progress False.

    Der Normalfall (attached Legacy, nur positive v2-Zeilen, nie migriert) darf NICHT
    über-deferred werden – die Retention muss weiterhin greifen können.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1000, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1
        # Nur positive v2-Zeilen, kein migrating-Segment.
        await _record_seg(rb, 900, "2026-05-01T00:00:00.000Z")
        assert await rb.store.manifest.list_migrating_segments() == []
        assert await rb._migration_in_progress() is False
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_no_migrating_no_legacy_is_false(tmp_path: Path):
    """Gegentest: keine migrating-Segmente UND keine attached Legacy → False."""
    rb = RingBuffer(storage="file", disk_path=str(tmp_path / "obs_ringbuffer.db"), segmented=True, max_entries=None)
    await rb.start()
    try:
        await _record_seg(rb, 1, "2026-05-01T00:00:00.000Z")
        assert await rb.store.manifest.list_legacy_segments() == []
        assert await rb.store.manifest.list_migrating_segments() == []
        assert await rb._migration_in_progress() is False
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_migrating_segment_still_true(tmp_path: Path):
    """Regression: ein ``migrating``-Segment allein ergibt weiterhin True (Grundfall)."""
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        await _record_seg(rb, 900, "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, 901, "2026-05-01T00:00:01.000Z")
        closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
        assert closed
        await rb.store.manifest.mark_migrating(closed[0].segment_id)
        assert len(await rb.store.manifest.list_migrating_segments()) == 1
        assert await rb._migration_in_progress() is True
    finally:
        await rb.stop()
