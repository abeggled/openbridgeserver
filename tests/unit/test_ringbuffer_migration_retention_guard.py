"""Append-getriebene Retention während laufender Live-Migration aussetzen (#951).

Codex-Finding „Defer legacy retention during live migration": eine chunked Legacy-
Migration legt versteckte ``migrating``-Segmente an (``mark_migrating``) und promotet
sie erst nach Abkopplung der noch attached Legacy-Quelle
(``promote_migrating_segments``). Ein normaler Live-Append erzeugt jedoch bereits eine
positive aktive Zeile und erfüllt damit den No-Zero-History-Guard – WÄHREND die Legacy-
Quelle noch attached ist. Liefe der append-getriebene ``enforce_retention`` dann unter
Size-Druck durch, löschte er die attached Legacy-Quelle, BEVOR alle Zeilen migriert sind
→ Datenverlust.

TDD-first: der erste Test reproduziert den Datenverlust ohne den Fix (die attached
Legacy-Quelle würde beim Append gelöscht) und wird durch das Gating grün. Der zweite Test
belegt, dass nach ``promote_migrating_segments`` (kein ``migrating``-Segment mehr) die
Retention wieder normal greift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.config import StoreRetentionConfig


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-seg") -> None:
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


async def _mark_a_closed_segment_migrating(rb: RingBuffer) -> int:
    """Erzeugt ein geschlossenes v2-Segment und markiert es als ``migrating``.

    ``mark_migrating`` stuft nur ``closed``/``checkpoint_pending`` Segmente um, nie das
    aktive Segment. Mit ``segment_max_rows=1`` schließt der zweite Append das erste
    Segment, das dann als ``migrating`` markiert wird (simuliert eine laufende chunked
    Migration).
    """
    await _record(rb, 900, "2026-05-01T00:00:00.000Z")
    await _record(rb, 901, "2026-05-01T00:00:01.000Z")
    closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
    assert closed, "Erwartet mindestens ein geschlossenes Segment zum Markieren"
    segment_id = closed[0].segment_id
    await rb.store.manifest.mark_migrating(segment_id)
    assert len(await rb.store.manifest.list_migrating_segments()) == 1
    return segment_id


@pytest.mark.asyncio
async def test_append_driven_retention_defers_while_migration_in_progress(tmp_path: Path):
    """Solange ein ``migrating``-Segment existiert, löscht der Append die attached Legacy NICHT.

    Reproduziert den Datenverlust: über-budget attached Legacy + laufende Migration +
    Live-Append mit positiver aktiver Zeile. Ohne Gating löschte der append-getriebene
    ``enforce_retention`` die noch attached (nicht fertig kopierte) Legacy-Quelle.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    # ``segment_max_rows=1`` nur, um ein geschlossenes Segment als ``migrating``
    # markieren zu können.
    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        # Vorbedingung: attached Legacy vorhanden.
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Laufende chunked Migration simulieren: ein ``migrating``-Segment existiert.
        await _mark_a_closed_segment_migrating(rb)

        # Rotations-Schwelle danach hochsetzen: der finale Append bleibt im aktiven
        # Segment (keine Rotation), der No-Zero-History-Guard schützt die positive Zeile.
        rb._segment_max_rows = 1000

        # Hartes Size-Budget: die attached Legacy ist klar über Budget und – ohne
        # Gating – beim nächsten append-getriebenen enforce reclaimbar.
        rb.store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)

        # Live-Append erzeugt eine positive aktive Zeile (No-Zero-History-Guard erfüllt).
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")

        # Gating greift: die attached Legacy-Quelle wurde NICHT gelöscht, solange die
        # Migration läuft (kein Datenverlust noch nicht kopierter Zeilen).
        assert len(await rb.store.manifest.list_legacy_segments()) == 1
        # Legacy-Zeilen bleiben lesbar.
        values = {e.new_value for e in await rb.query_v2(limit=50)}
        assert {100, 101, 102}.issubset(values)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_append_driven_retention_resumes_after_promote(tmp_path: Path):
    """Nach ``promote_migrating_segments`` (kein ``migrating`` mehr) greift die Retention wieder.

    Gegenprobe zum Gating: sobald die Migration abgeschlossen ist (Quelle abgekoppelt,
    Segmente promotet), erfüllt der nächste append-getriebene enforce wieder seinen Zweck
    und gibt die über-budget attached Legacy frei.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        await _mark_a_closed_segment_migrating(rb)
        # Finale Appends bleiben im aktiven Segment (keine Rotation).
        rb._segment_max_rows = 1000
        rb.store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)

        # Migration läuft → erster Append lässt die Legacy stehen.
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Migration abgeschlossen: Segmente promoten → kein ``migrating``-Segment mehr.
        await rb.store.manifest.promote_migrating_segments(to_migrated=True)
        assert await rb.store.manifest.list_migrating_segments() == []

        # Nächster Live-Append: Gating fällt weg → über-budget Legacy wird zurückgewonnen.
        await _record(rb, 201, "2026-06-01T00:00:01.000Z")
        assert await rb.store.manifest.list_legacy_segments() == []

        # Die frischen v2-Werte bleiben lesbar (No-Zero-History gewahrt).
        values = [e.new_value for e in await rb.query_v2(limit=5)]
        assert values[0] == 201
    finally:
        await rb.stop()
