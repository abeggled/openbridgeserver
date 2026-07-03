"""Manifest-Schema + CRUD + globale Event-ID (#931, harte Vorbedingung aus #922).

Das Manifest ist SQLite-Backend-intern (unter der portablen Grenze). Es hält je
Segment die Metadaten und einen prozess-/root-weiten monoton wachsenden globalen
Event-ID-Zähler, damit #932 segmentübergreifend stabil sortieren/paginieren kann.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import aiosqlite

from obs.ringbuffer.store.manifest import (
    LEGACY_SCHEMA_VERSION,
    SEGMENT_STATUS_ACTIVE,
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_CLOSED,
    SEGMENT_STATUS_LEGACY,
    SEGMENT_STATUS_MIGRATED,
    SEGMENT_STATUS_QUARANTINED,
    Manifest,
    SegmentRecord,
)


@pytest.fixture
async def manifest(tmp_path: Path) -> Manifest:
    m = Manifest(tmp_path / "manifest.sqlite")
    await m.open()
    try:
        yield m
    finally:
        await m.close()


async def test_manifest_init_is_idempotent(tmp_path: Path):
    path = tmp_path / "manifest.sqlite"
    first = Manifest(path)
    await first.open()
    seg = await first.create_segment(filename="rb_0001.sqlite", schema_version=1)
    await first.close()

    # Zweites open() darf weder Schema neu anlegen (Fehler) noch Daten verlieren.
    second = Manifest(path)
    await second.open()
    try:
        segments = await second.list_segments()
        assert [s.filename for s in segments] == [seg.filename]
    finally:
        await second.close()


async def test_create_segment_persists_all_manifest_fields(manifest: Manifest):
    seg = await manifest.create_segment(filename="rb_0001.sqlite", schema_version=2)
    assert isinstance(seg, SegmentRecord)
    assert seg.segment_id >= 1
    assert seg.filename == "rb_0001.sqlite"
    assert seg.status == SEGMENT_STATUS_ACTIVE
    assert seg.schema_version == 2
    assert seg.row_count == 0
    assert seg.size_bytes == 0
    assert seg.created_at is not None
    assert seg.closed_at is None
    assert seg.from_ts is None
    assert seg.to_ts is None
    assert seg.integrity_status == "ok"


async def test_segment_ids_are_monotonic(manifest: Manifest):
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    b = await manifest.create_segment(filename="b.sqlite", schema_version=1)
    c = await manifest.create_segment(filename="c.sqlite", schema_version=1)
    assert a.segment_id < b.segment_id < c.segment_id


async def test_update_and_close_segment(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.update_segment_stats(
        seg.segment_id,
        row_count=10,
        size_bytes=2048,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T01:00:00.000Z",
    )
    await manifest.close_segment(seg.segment_id)
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_CLOSED
    assert reloaded.row_count == 10
    assert reloaded.size_bytes == 2048
    assert reloaded.from_ts == "2026-01-01T00:00:00.000Z"
    assert reloaded.to_ts == "2026-01-01T01:00:00.000Z"
    assert reloaded.closed_at is not None


async def test_active_segment_lookup(manifest: Manifest):
    assert await manifest.get_active_segment() is None
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    assert (await manifest.get_active_segment()).segment_id == a.segment_id
    await manifest.close_segment(a.segment_id)
    assert await manifest.get_active_segment() is None


async def test_mark_checkpoint_pending(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_checkpoint_pending(seg.segment_id)
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_CHECKPOINT_PENDING


async def test_mark_checkpoint_done_reopens_retention_eligibility(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_checkpoint_pending(seg.segment_id)
    # Solange pending, ist das Segment nicht in der retention-fähigen Liste.
    assert [s.segment_id for s in await manifest.list_closed_segments()] == []
    assert [s.segment_id for s in await manifest.list_checkpoint_pending_segments()] == [seg.segment_id]

    await manifest.mark_checkpoint_done(seg.segment_id)
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_CLOSED
    assert [s.segment_id for s in await manifest.list_closed_segments()] == [seg.segment_id]


async def test_mark_migrated_from_closed(manifest: Manifest):
    """mark_migrated setzt ein sauber geschlossenes Segment auf ``migrated`` (#951, Pkt 2)."""
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=2)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_migrated(seg.segment_id)
    assert (await manifest.get_segment(seg.segment_id)).status == SEGMENT_STATUS_MIGRATED


async def test_mark_migrated_from_checkpoint_pending(manifest: Manifest):
    """mark_migrated greift auch bei ``checkpoint_pending`` (#951, Pkt 2, Codex :513).

    Rotiert die Migration ein rein-migriertes Segment, während ein Reader den
    WAL-Checkpoint busy hält, bleibt es ``checkpoint_pending`` statt ``closed``. Ein
    mark_migrated, das nur ``closed`` umstuft, wäre dann ein No-op und das Segment
    (nur negative Legacy-gids) bliebe im positiven Query-Rang → ``id desc`` behandelte
    es fälschlich als „neueste". Daher muss mark_migrated auch ``checkpoint_pending``
    in den Migrated-Rang heben.
    """
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=2)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_checkpoint_pending(seg.segment_id)
    await manifest.mark_migrated(seg.segment_id)
    assert (await manifest.get_segment(seg.segment_id)).status == SEGMENT_STATUS_MIGRATED


async def test_mark_migrated_ignores_active_segment(manifest: Manifest):
    """mark_migrated stuft ein aktives Segment nie um (Guard bleibt für active)."""
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=2)
    await manifest.mark_migrated(seg.segment_id)
    assert (await manifest.get_segment(seg.segment_id)).status == SEGMENT_STATUS_ACTIVE


async def test_mark_quarantined_records_reason_and_status(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.close_segment(seg.segment_id)
    await manifest.mark_quarantined(seg.segment_id, reason="malformed database disk image")
    reloaded = await manifest.get_segment(seg.segment_id)
    assert reloaded.status == SEGMENT_STATUS_QUARANTINED
    assert reloaded.integrity_status == "corrupt"
    assert reloaded.quarantine_reason == "malformed database disk image"
    # Quarantänierte Segmente sind nicht "closed", aber seit #919 retention-fähig.
    assert [s.segment_id for s in await manifest.list_closed_segments()] == []
    assert [s.segment_id for s in await manifest.list_retention_eligible_segments()] == [seg.segment_id]


async def test_list_retention_eligible_includes_closed_and_quarantined_fifo(manifest: Manifest):
    """list_retention_eligible_segments liefert closed+quarantined, älteste zuerst; nie active/pending (#919)."""
    a = await manifest.create_segment(filename="a.sqlite", schema_version=2)  # wird closed
    b = await manifest.create_segment(filename="b.sqlite", schema_version=2)  # wird quarantined
    c = await manifest.create_segment(filename="c.sqlite", schema_version=2)  # wird checkpoint_pending
    d = await manifest.create_segment(filename="d.sqlite", schema_version=2)  # bleibt active

    await manifest.close_segment(a.segment_id)
    await manifest.close_segment(b.segment_id)
    await manifest.mark_quarantined(b.segment_id, reason="corrupt")
    await manifest.close_segment(c.segment_id)
    await manifest.mark_checkpoint_pending(c.segment_id)

    eligible = await manifest.list_retention_eligible_segments()
    # FIFO (segment_id ASC): closed a, dann quarantined b. c (pending) und d (active) fehlen.
    assert [s.segment_id for s in eligible] == [a.segment_id, b.segment_id]
    assert d.segment_id not in {s.segment_id for s in eligible}
    assert c.segment_id not in {s.segment_id for s in eligible}


async def test_list_retention_eligible_orders_migrated_before_closed(manifest: Manifest):
    """Migrierte Segmente sind für Retention die ältesten – zuerst löschbar (#951, Pkt 1).

    Materialisiert die Migration alte Legacy-Daten NACH den ersten v2-Writes, trägt
    das migrierte Segment eine HÖHERE ``segment_id`` als ein bereits geschlossenes
    v2-Segment, obwohl seine Events (negative Legacy-gids) ÄLTER sind. Rein nach
    ``segment_id ASC`` löschte Size-/Row-Retention das jüngere v2-Segment VOR dem
    älteren migrierten – falsche FIFO-Ordnung. Migrierte müssen daher trotz höherer
    id ZUERST erscheinen, konsistent mit ihrer Query-Trailing-Position.
    """
    v2_closed = await manifest.create_segment(filename="v2.sqlite", schema_version=2)
    await manifest.close_segment(v2_closed.segment_id)
    migrated = await manifest.create_segment(filename="legacy.sqlite", schema_version=2)
    await manifest.close_segment(migrated.segment_id)
    await manifest.mark_migrated(migrated.segment_id)
    # migrated hat die HÖHERE segment_id, hält aber die ÄLTESTEN (Legacy-)Daten.
    assert migrated.segment_id > v2_closed.segment_id

    eligible = await manifest.list_retention_eligible_segments()
    # Migrated zuerst (ältestes Daten-Alter), dann das jüngere v2-closed.
    assert [s.segment_id for s in eligible] == [migrated.segment_id, v2_closed.segment_id]


async def test_list_retention_eligible_orders_multiple_migrated_oldest_id_first(manifest: Manifest):
    """Innerhalb der migrated-Klasse bleibt segment_id ASC (älteres migriertes zuerst)."""
    mig_a = await manifest.create_segment(filename="m_a.sqlite", schema_version=2)
    mig_b = await manifest.create_segment(filename="m_b.sqlite", schema_version=2)
    closed = await manifest.create_segment(filename="v2.sqlite", schema_version=2)
    for seg in (mig_a, mig_b, closed):
        await manifest.close_segment(seg.segment_id)
    await manifest.mark_migrated(mig_a.segment_id)
    await manifest.mark_migrated(mig_b.segment_id)

    eligible = await manifest.list_retention_eligible_segments()
    # Beide migrated vor closed; innerhalb migrated segment_id ASC.
    assert [s.segment_id for s in eligible] == [mig_a.segment_id, mig_b.segment_id, closed.segment_id]


async def test_delete_segment_removes_manifest_entry(manifest: Manifest):
    seg = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    await manifest.close_segment(seg.segment_id)
    await manifest.delete_segment(seg.segment_id)
    assert await manifest.get_segment(seg.segment_id) is None


async def test_list_closed_segments_orders_oldest_first(manifest: Manifest):
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    b = await manifest.create_segment(filename="b.sqlite", schema_version=1)
    await manifest.close_segment(a.segment_id)
    await manifest.close_segment(b.segment_id)
    assert [s.segment_id for s in await manifest.list_closed_segments()] == [a.segment_id, b.segment_id]


async def test_open_migrates_legacy_manifest_without_quarantine_reason(tmp_path: Path):
    path = tmp_path / "manifest.sqlite"
    # Alt-Manifest ohne quarantine_reason-Spalte simulieren.
    legacy = await aiosqlite.connect(str(path))
    await legacy.executescript(
        """
        CREATE TABLE segments (
            segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active',
            from_ts TEXT, to_ts TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, closed_at TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            integrity_status TEXT NOT NULL DEFAULT 'ok',
            recovery_status TEXT NOT NULL DEFAULT 'none'
        );
        INSERT INTO segments (filename, created_at) VALUES ('old.sqlite', '2026-01-01T00:00:00Z');
        """
    )
    await legacy.commit()
    await legacy.close()

    m = Manifest(path)
    await m.open()
    try:
        segments = await m.list_segments()
        assert segments[0].quarantine_reason is None
    finally:
        await m.close()


async def test_register_legacy_segment_is_additive_and_readonly(manifest: Manifest):
    seg = await manifest.register_legacy_segment(
        source_path="/data/obs_ringbuffer.db",
        size_bytes=123456,
        dirty_wal=False,
    )
    assert seg.status == SEGMENT_STATUS_LEGACY
    assert seg.schema_version == LEGACY_SCHEMA_VERSION
    assert seg.filename == "/data/obs_ringbuffer.db"
    assert seg.size_bytes == 123456
    assert seg.recovery_status == "none"
    # Legacy ist nie aktiv und nie retention-fähig.
    assert await manifest.get_active_segment() is None
    assert [s.segment_id for s in await manifest.list_closed_segments()] == []
    assert [s.segment_id for s in await manifest.list_legacy_segments()] == [seg.segment_id]


async def test_register_legacy_segment_flags_dirty_wal(manifest: Manifest):
    seg = await manifest.register_legacy_segment(
        source_path="/data/big.db",
        size_bytes=30 * 1024**3,
        dirty_wal=True,
    )
    assert seg.recovery_status == "dirty_wal"


async def test_global_event_id_is_monotonic_and_persistent(tmp_path: Path):
    path = tmp_path / "manifest.sqlite"
    m = Manifest(path)
    await m.open()
    first_batch = [await m.next_global_event_id() for _ in range(3)]
    assert first_batch == sorted(set(first_batch))
    assert first_batch[0] >= 1
    await m.close()

    # Nach Reopen darf keine ID doppelt vergeben werden (Persistenz).
    m2 = Manifest(path)
    await m2.open()
    try:
        next_id = await m2.next_global_event_id()
        assert next_id > first_batch[-1]
    finally:
        await m2.close()


async def test_reserve_global_event_ids_returns_contiguous_block(manifest: Manifest):
    start = await manifest.reserve_global_event_ids(5)
    following = await manifest.next_global_event_id()
    # Der reservierte Block [start, start+5) darf sich nicht mit späteren IDs überschneiden.
    assert following >= start + 5


async def test_db_access_before_open_raises(tmp_path: Path):
    m = Manifest(tmp_path / "manifest.sqlite")
    with pytest.raises(RuntimeError, match="not open"):
        await m.list_segments()


async def test_reserve_zero_ids_is_rejected(manifest: Manifest):
    with pytest.raises(ValueError, match="count must be >= 1"):
        await manifest.reserve_global_event_ids(0)


async def _seg_with_bounds(manifest: Manifest, filename: str, from_ts: str | None, to_ts: str | None) -> SegmentRecord:
    seg = await manifest.create_segment(filename=filename, schema_version=1)
    await manifest.update_segment_stats(seg.segment_id, row_count=1, size_bytes=1, from_ts=from_ts, to_ts=to_ts)
    return await manifest.get_segment(seg.segment_id)


async def test_list_segments_for_query_without_filter_is_newest_first(manifest: Manifest):
    a = await manifest.create_segment(filename="a.sqlite", schema_version=1)
    b = await manifest.create_segment(filename="b.sqlite", schema_version=1)
    c = await manifest.create_segment(filename="c.sqlite", schema_version=1)
    selected = await manifest.list_segments_for_query()
    assert [s.segment_id for s in selected] == [c.segment_id, b.segment_id, a.segment_id]


async def test_list_segments_for_query_time_filter_picks_overlapping(manifest: Manifest):
    await _seg_with_bounds(manifest, "a.sqlite", "2026-01-01T00:00:00.000Z", "2026-01-10T00:00:00.000Z")
    b = await _seg_with_bounds(manifest, "b.sqlite", "2026-02-01T00:00:00.000Z", "2026-02-10T00:00:00.000Z")
    await _seg_with_bounds(manifest, "c.sqlite", "2026-03-01T00:00:00.000Z", "2026-03-10T00:00:00.000Z")
    selected = await manifest.list_segments_for_query(
        from_ts="2026-01-20T00:00:00.000Z",
        to_ts="2026-02-20T00:00:00.000Z",
    )
    assert [s.segment_id for s in selected] == [b.segment_id]


async def test_list_segments_for_query_includes_segments_with_unknown_bounds(manifest: Manifest):
    # Frisch angelegtes Segment ohne from_ts/to_ts wird konservativ einbezogen.
    unknown = await manifest.create_segment(filename="active.sqlite", schema_version=1)
    await _seg_with_bounds(manifest, "old.sqlite", "2020-01-01T00:00:00.000Z", "2020-01-02T00:00:00.000Z")
    selected = await manifest.list_segments_for_query(
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-02T00:00:00.000Z",
    )
    ids = {s.segment_id for s in selected}
    # Segment mit unbekannten Grenzen ist dabei, das eindeutig ältere nicht.
    assert unknown.segment_id in ids
    assert len(selected) == 1


async def test_list_segments_for_query_only_from_ts(manifest: Manifest):
    await _seg_with_bounds(manifest, "a.sqlite", "2026-01-01T00:00:00.000Z", "2026-01-10T00:00:00.000Z")
    b = await _seg_with_bounds(manifest, "b.sqlite", "2026-03-01T00:00:00.000Z", "2026-03-10T00:00:00.000Z")
    selected = await manifest.list_segments_for_query(from_ts="2026-02-01T00:00:00.000Z")
    assert [s.segment_id for s in selected] == [b.segment_id]
