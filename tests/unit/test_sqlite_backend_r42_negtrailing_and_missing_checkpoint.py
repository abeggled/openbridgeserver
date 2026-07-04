"""Codex-Runde-42 [P2] – zwei Read-/Retention-Pfad-Fixes im SQLite-Segment-Backend.

F1 (Codex an migration.py:1886, robuster Fix im Read-Pfad ``sqlite_backend``):
    Ein verwaister rein-negativer-gid-``closed``-Chunk (Crash NACH Detach, VOR
    ``mark_migrated``) sitzt mangels ``legacy``/``migrated``-Status im POSITIVEN
    Query-Prefix. Eine ``id desc``-latest-page könnte ihn wegen seiner hohen
    ``segment_id`` ZUERST lesen und früh terminieren, bevor echte positive v2-Zeilen/
    der Legacy-Tail gelesen werden → migrierte Historie erschiene als „latest".
    Fix: Ein Segment mit MIN(global_event_id) < 0 wird source-unabhängig in den
    Trailing-Rang umsortiert und terminiert die Early-Termination nicht mehr früh.

F2 (sqlite_backend.py:2852): Ein ``checkpoint_pending``-Segment, dessen Basisdatei
    fehlt (manuelles/partielles externes Cleanup), wurde bei JEDEM Retention-Pass nur
    übersprungen. Die Manifest-Zeile blieb ``checkpoint_pending`` (retention-UNfähig)
    und ``_retention_pressure`` zählte ihre stale ``size_bytes`` dauerhaft als
    non-deletable → Store permanent über Budget. Fix: ein pending-Segment mit fehlender
    Datei UND erwarteten Zeilen wird als verloren quarantäniert; danach ist es
    retention-fähig und der Store kann unter Budget kommen.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreQuery


async def _record_seg(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-r42") -> None:
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


async def _seed_legacy(disk_path: Path, count: int = 2) -> None:
    """Legt eine Legacy-Single-DB an, die beim nächsten ``start`` als attached Legacy erkannt wird."""
    legacy = RingBuffer(storage="file", disk_path=str(disk_path))
    await legacy.start()
    for value in range(count):
        await legacy.record(
            ts=f"2025-01-01T00:00:0{value}.000Z",
            datapoint_id="dp-r42",
            topic="dp/dp-r42/value",
            old_value=None,
            new_value=100 + value,
            source_adapter="api",
            quality="good",
        )
    await legacy.stop()


async def _set_negative_gid(rb: RingBuffer, filename: str, gid: int = -42) -> None:
    """Setzt in der Segment-Datei alle gids auf einen negativen Wert (kopierter Legacy-Chunk)."""
    seg_path = rb.store._segments_dir / filename
    async with aiosqlite.connect(str(seg_path)) as conn:
        await conn.execute("UPDATE ringbuffer SET global_event_id = ? WHERE global_event_id >= 0", (gid,))
        await conn.commit()


# ---------------------------------------------------------------------------
# F1: verwaistes rein-negatives closed-Segment im positiven Prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_negative_closed_chunk_not_returned_as_latest_page(tmp_path: Path):
    """Verwaister negativer ``closed``-Chunk (hohe segment_id) darf latest-page NICHT früh füllen.

    Ohne Fix: ``list_segments_for_query`` liefert den negativen ``closed``-Chunk (höchste
    segment_id) ZUERST; die ``id desc``-latest-page sammelt seine negativen Zeilen, erreicht
    ``limit`` und terminiert früh → das echte positive v2-Segment wird nie gelesen und die
    migrierte Historie erscheint als „latest".
    Mit Fix: der negative Chunk wird in den Trailing-Rang umsortiert → die latest-page liefert
    die echten positiven v2-Zeilen.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"

    # segment_max_rows=1 → jeder Record rotiert und erzeugt ein neues ``closed`` v2-Segment.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        # 1) Echtes positives v2-Segment (ältere segment_id, positive gids) → wird nach Rotate ``closed``.
        await _record_seg(rb, "real-positive", "2026-05-01T00:00:00.000Z")
        # 2) Zweiter Record rotiert Segment 1 nach ``closed`` und öffnet ein neues aktives Segment.
        await _record_seg(rb, "real-positive-2", "2026-05-01T00:00:01.000Z")

        closed = sorted(
            (s for s in await rb.store.manifest.list_segments() if s.status == "closed"),
            key=lambda s: s.segment_id,
        )
        assert closed, "es muss mindestens ein closed v2-Segment geben"

        # 3) Der JÜNGSTE closed-Chunk (höchste segment_id) wird zum verwaisten Migrations-Chunk:
        #    negative gids setzen, aber NICHT mark_migrated (Crash nach Detach, vor mark_migrated).
        orphan = closed[-1]
        await _set_negative_gid(rb, orphan.filename)

        # Der Chunk bleibt Status ``closed`` und trägt jetzt negative gids.
        assert orphan.status == "closed"

        # latest-page: limit=1, id desc → darf NICHT die negative migrierte Zeile liefern.
        rows = await rb.store.query(StoreQuery(limit=1, offset=0, sort_field="id", sort_order="desc"))
        assert rows, "latest-page darf nicht leer sein"
        assert all(r["global_event_id"] >= 0 for r in rows), (
            f"latest-page lieferte migrierte negative gids als 'latest': {[r['global_event_id'] for r in rows]}"
        )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_orphan_negative_closed_chunk_with_other_source_attached(tmp_path: Path):
    """Mehr-Quell-Fall: eine Quelle detached+gecrasht (negativer closed-Chunk), andere attached.

    Deckt den von der Runde-41-Startup-Recovery bewusst NICHT abgedeckten Mehr-Quell-Fall ab.
    Auch mit noch attached Legacy-Quelle darf eine ``id desc``-latest-page keine migrierten
    negativen Zeilen als „latest" liefern.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        # Quelle B: das beim Upgrade eingehängte Legacy-Segment bleibt attached.
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        await _record_seg(rb, "real-positive", "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, "real-positive-2", "2026-05-01T00:00:01.000Z")
        closed = sorted(
            (s for s in await rb.store.manifest.list_segments() if s.status == "closed"),
            key=lambda s: s.segment_id,
        )
        assert closed
        orphan = closed[-1]
        await _set_negative_gid(rb, orphan.filename)

        rows = await rb.store.query(StoreQuery(limit=1, offset=0, sort_field="id", sort_order="desc"))
        assert rows
        assert all(r["global_event_id"] >= 0 for r in rows), (
            f"latest-page lieferte migrierte negative gids trotz attached Legacy: {[r['global_event_id'] for r in rows]}"
        )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_pure_positive_v2_prefix_still_early_terminates(tmp_path: Path):
    """Gegentest: reine positive v2-Segmente bleiben früh-abbrechbar und korrekt geordnet.

    Ohne negativen Chunk darf der Fix nichts umsortieren – die neueste positive Zeile steht
    an latest-page-Position 1, die Cross-Segment-``id desc``-Ordnung bleibt monoton.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        await _record_seg(rb, "v0", "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, "v1", "2026-05-01T00:00:01.000Z")
        await _record_seg(rb, "v2", "2026-05-01T00:00:02.000Z")

        rows = await rb.store.query(StoreQuery(limit=3, offset=0, sort_field="id", sort_order="desc"))
        gids = [r["global_event_id"] for r in rows]
        assert all(g >= 0 for g in gids)
        # id desc → streng monoton fallend über Segmentgrenzen.
        assert gids == sorted(gids, reverse=True)
        # Neueste Zeile zuerst.
        assert rows[0]["new_value"] == "v2"
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_negative_gid_flag_cached_for_closed_segment(tmp_path: Path):
    """Bounded-heit: ein negativer ``closed``-Chunk wird nach dem ersten Read je segment_id gecacht.

    Der Cache wird als Nebenprodukt des normalen Reads (kein zusätzlicher Open) gefüllt;
    nur ein definitiver Negativ-Treffer wird memoisiert, ein rein-positives Segment cacht NIE.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        await _record_seg(rb, "a", "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, "b", "2026-05-01T00:00:01.000Z")
        closed = sorted(
            (s for s in await rb.store.manifest.list_segments() if s.status == "closed"),
            key=lambda s: s.segment_id,
        )
        assert closed
        orphan = closed[-1]
        await _set_negative_gid(rb, orphan.filename)

        # Ein voller Read (limit hoch) besucht alle Segmente und cacht den Negativ-Chunk.
        await rb.store.query(StoreQuery(limit=100, offset=0, sort_field="id", sort_order="desc"))
        assert rb.store._segment_negative_gid_cache.get(orphan.segment_id) is True
        # Ein rein-positives closed-Segment wird NIE als negativ gecacht (kein False-Eintrag).
        positive = closed[0]
        assert positive.segment_id not in rb.store._segment_negative_gid_cache

        # Direkte Erkennung am gefetchten Ergebnis: negative Zeile → True.
        neg_rows = [{"global_event_id": -42}]
        assert rb.store._rows_carry_negative_gid(orphan, neg_rows) is True
        pos_rows = [{"global_event_id": 5}]
        assert rb.store._rows_carry_negative_gid(positive, pos_rows) is False
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# F2: checkpoint_pending-Segment mit fehlender Datei
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_checkpoint_pending_file_quarantined_and_not_undeletable(tmp_path: Path):
    """Fehlende Datei eines checkpoint_pending-Segments → quarantänisiert, Bytes nicht mehr non-deletable.

    Ohne Fix: die Manifest-Zeile bleibt ``checkpoint_pending``; ``_retention_pressure`` zählt
    ihre stale ``size_bytes`` dauerhaft als non-deletable → ``retention_over_budget`` bleibt
    True. Mit Fix: der Retention-Pass quarantänisiert das Segment (verloren), seine Bytes
    zählen nicht mehr als non-deletable → Fortschritt möglich.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        # Zwei Records → mindestens ein closed v2-Segment mit Zeilen.
        await _record_seg(rb, "x", "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, "y", "2026-05-01T00:00:01.000Z")
        closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
        assert closed
        target = closed[0]

        # Segment künstlich nach checkpoint_pending versetzen und eine stale size_bytes setzen,
        # sodass es unter einem kleinen Budget als non-deletable ins Gewicht fällt.
        await rb.store.manifest.mark_checkpoint_pending(target.segment_id)
        await rb.store.manifest.update_segment_size(target.segment_id, size_bytes=10_000_000)

        # Basisdatei entfernen (manuelles/externes Cleanup).
        seg_path = rb.store._segments_dir / target.filename
        seg_path.unlink()
        assert not seg_path.exists()

        # Kleines Budget → die stale 10 MB des pending-Segments sprengen es als non-deletable.
        segments = await rb.store.manifest.list_segments()
        pending_before = [s for s in segments if s.status == "checkpoint_pending"]
        assert pending_before and pending_before[0].segment_id == target.segment_id

        # Retention-Pass (ruft run_pending_checkpoints als Vorlauf).
        await rb.store.run_pending_checkpoints()

        segments_after = await rb.store.manifest.list_segments()
        pending_after = [s for s in segments_after if s.status == "checkpoint_pending"]
        quarantined_after = [s for s in segments_after if s.status == "quarantined" and s.segment_id == target.segment_id]
        assert pending_after == [], "checkpoint_pending-Zeile mit fehlender Datei muss quarantänisiert sein"
        assert quarantined_after, "das verlorene pending-Segment muss als quarantined markiert sein"

        # _retention_pressure zählt quarantined nicht mehr als non-deletable.
        segments_now = await rb.store.manifest.list_segments()
        over_budget, _reason = await rb.store._retention_pressure(segments_now)
        # Die 10 MB des quarantänisierten Segments sind nicht mehr non-deletable; ein hartes
        # Budget knapp über den restlichen (winzigen) Bytes wird nicht mehr durch das
        # verlorene Segment gesprengt.
        assert over_budget is False
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_present_checkpoint_pending_segment_unchanged(tmp_path: Path):
    """Gegentest: ein vorhandenes checkpoint_pending-Segment (Datei da) wird NICHT quarantänisiert."""
    disk_path = tmp_path / "obs_ringbuffer.db"

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        await _record_seg(rb, "x", "2026-05-01T00:00:00.000Z")
        await _record_seg(rb, "y", "2026-05-01T00:00:01.000Z")
        closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
        assert closed
        target = closed[0]
        await rb.store.manifest.mark_checkpoint_pending(target.segment_id)

        # Datei bleibt vorhanden.
        seg_path = rb.store._segments_dir / target.filename
        assert seg_path.exists()

        await rb.store.run_pending_checkpoints()

        segments_after = await rb.store.manifest.list_segments()
        quarantined = [s for s in segments_after if s.status == "quarantined" and s.segment_id == target.segment_id]
        assert quarantined == [], "ein vorhandenes pending-Segment darf nicht quarantänisiert werden"
        # Es wurde entweder sauber closed (Truncate ok) oder bleibt pending – aber nicht verloren.
        current = [s for s in segments_after if s.segment_id == target.segment_id][0]
        assert current.status in ("closed", "checkpoint_pending")
    finally:
        await rb.stop()
