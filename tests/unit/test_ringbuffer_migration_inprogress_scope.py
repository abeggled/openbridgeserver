"""Codex-Finding (#951, ringbuffer.py:911) – „Scope visible negative chunks to the attached source".

Follow-up auf den Runde-37-Fix (``_has_visible_negative_v2_chunk`` in
``_migration_in_progress``). Hat eine Legacy-Quelle ihre Migration bereits ABGESCHLOSSEN,
bleiben ihre sichtbaren negativ-gid-``migrated``-Segmente im Manifest. Ist eine ANDERE
Legacy-Quelle noch attached, behandelte der source-agnostische Check jene ABGESCHLOSSENEN
``migrated``-Chunks als „migration in progress" und machte die startup/append/reconfigure-
Retention-Pässe zum No-op → über-budget-Legacy blieb unreklamiert, obwohl für die attached
Quelle gerade KEINE Chunks kopiert werden.

Fix: ``_has_visible_negative_v2_chunk`` schließt ``migrated``-Segmente aus. Nur nicht-
``migrated`` negative Chunks (Crash-Fenster ``closed``/``active``, negative gid, noch nicht
``migrating`` markiert) zählen weiterhin als in-progress-Signal.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


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
    """Legt eine Legacy-Single-DB an, die beim nächsten ``start`` als attached Legacy erkannt wird."""
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


async def _set_negative_gid(rb: RingBuffer, filename: str) -> None:
    """Setzt in der Segment-Dw-Datei alle gids auf einen negativen Wert (kopierter Legacy-Chunk)."""
    seg_path = rb.store._segments_dir / filename
    async with aiosqlite.connect(str(seg_path)) as conn:
        await conn.execute("UPDATE ringbuffer SET global_event_id = -42 WHERE global_event_id >= 0")
        await conn.commit()


@pytest.mark.asyncio
async def test_completed_migrated_chunk_does_not_block_retention(tmp_path: Path):
    """Quelle A abgeschlossen (``migrated`` negativ), Quelle B noch attached → in progress False.

    Ein abgeschlossenes ``migrated``-Segment mit sichtbaren negativen gids gehört zu einer
    bereits FERTIG migrierten Quelle. Solange eine ANDERE Legacy-Quelle attached ist, darf
    dieses Segment die Retention NICHT deferren – der Check muss ``migrated`` ausschließen.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    # segment_max_rows=1 → jeder Record rotiert und erzeugt ein ``closed`` v2-Segment.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        # Quelle B bleibt attached (das beim Upgrade eingehängte Legacy-Segment).
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Zwei Records → das erste Segment rotiert nach ``closed``.
        await _record_seg(rb, 900, "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, 901, "2026-05-01T00:00:01.000Z")
        closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
        assert closed

        # Quelle A: abgeschlossener negativer Chunk → negative gid setzen, dann ``migrated``.
        target = closed[0]
        await _set_negative_gid(rb, target.filename)
        await rb.store.manifest.mark_migrated(target.segment_id)
        migrated = [s for s in await rb.store.manifest.list_segments() if s.status == "migrated"]
        assert len(migrated) == 1

        # Kein migrating-Segment, aber Quelle B noch attached: der ``migrated``-Chunk darf
        # NICHT als in-progress zählen → Retention der attached Quelle bleibt aktiv.
        assert await rb.store.manifest.list_migrating_segments() == []
        assert await rb._has_visible_negative_v2_chunk() is False
        assert await rb._migration_in_progress() is False
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_non_migrated_negative_chunk_still_defers(tmp_path: Path):
    """Runde-37-Schutz bleibt: sichtbarer NICHT-``migrated`` negativer Chunk + attached Legacy → True.

    Crash-Fenster: kopierte Legacy-Zeile mit negativer gid in einem sichtbaren ``closed``
    v2-Segment (noch nicht ``migrating``/``migrated`` markiert). Der Helper muss ``True``
    melden, damit die Retention die attached Legacy nicht vorzeitig löscht.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        await _record_seg(rb, 900, "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, 901, "2026-05-01T00:00:01.000Z")
        closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
        assert closed

        # Negative gid, aber KEINE Umstufung nach migrating/migrated → sichtbarer Crash-Chunk.
        await _set_negative_gid(rb, closed[0].filename)

        assert await rb.store.manifest.list_migrating_segments() == []
        assert await rb._has_visible_negative_v2_chunk() is True
        assert await rb._migration_in_progress() is True
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_migrating_segment_still_true(tmp_path: Path):
    """Regression: ein ``migrating``-Segment ergibt weiterhin True (Grundfall unverändert)."""
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
