"""Reconfigure-getriebene Retention während laufender Live-Migration aussetzen (#951).

Codex-Finding (Runde 23) „Defer reconfigure retention during migration": der
``reconfigure``/apply-config-Pfad ruft nach der Sofort-Rotation unbedingt
``store.enforce_retention()`` (``_apply_segment_config_locked``). Läuft gerade eine
chunked Legacy-Migration (versteckte ``migrating``-Segmente, noch attached Legacy-
Quelle), umgeht dieser Pass die Migrations-Guards von Append- und Startup-Pfad. Bei
positiven v2-Zeilen + Size-Druck löschte die Retention die attached Legacy-Quelle,
während die restlichen Chunks noch nicht kopiert sind → Verlust nicht kopierter
Legacy-Zeilen.

TDD-first: der erste Test reproduziert den Datenverlust ohne den Fix (ein
Config-Save/reconfigure während ``migrating``-Segmenten existieren + über-budget
attached Legacy löschte die Quelle) und wird durch das Gating grün. Der zweite Test
belegt die Gegenprobe: ohne ``migrating``-Segment läuft die Retention beim reconfigure
normal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


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
async def test_reconfigure_retention_defers_while_migration_in_progress(tmp_path: Path):
    """Ein reconfigure/Config-Save während ``migrating``-Segmenten löscht die attached Legacy NICHT.

    Reproduziert den Datenverlust: über-budget attached Legacy + laufende Migration +
    reconfigure. Ohne Gating löschte die reconfigure-getriebene ``enforce_retention`` die
    noch attached (nicht fertig kopierte) Legacy-Quelle.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Laufende chunked Migration simulieren: ein ``migrating``-Segment existiert.
        await _mark_a_closed_segment_migrating(rb)

        # Config-Save mit hartem Size-Budget: die attached Legacy ist klar über Budget.
        # Ohne Gating wählte die reconfigure-getriebene enforce_retention die (älteste,
        # größte) attached Legacy-Quelle zuerst und löschte sie mitten in der Migration.
        await rb.reconfigure(storage="file", max_file_size_bytes=1)

        # Gating greift: die attached Legacy-Quelle wurde NICHT gelöscht.
        assert len(await rb.store.manifest.list_legacy_segments()) == 1
        # Legacy-Zeilen bleiben lesbar.
        values = {e.new_value for e in await rb.query_v2(limit=50)}
        assert {100, 101, 102}.issubset(values)
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_reconfigure_retention_runs_without_migrating_segments(tmp_path: Path):
    """Ohne ``migrating``-Segment greift die reconfigure-getriebene Retention normal.

    Gegenprobe zum Gating: ist keine Migration aktiv (kein ``migrating``-Segment), räumt
    das über-budget attached Legacy-Segment beim reconfigure wie erwartet ab.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path)

    rb = RingBuffer(storage="file", disk_path=str(disk_path), segmented=True, segment_max_rows=1, max_entries=None)
    await rb.start()
    try:
        assert len(await rb.store.manifest.list_legacy_segments()) == 1

        # Eine positive v2-Zeile, damit der No-Zero-History-Guard mindestens ein
        # nicht-Legacy-Segment behält, während die attached Legacy retention-fähig ist.
        await _record(rb, 200, "2026-06-01T00:00:00.000Z")
        assert await rb.store.manifest.list_migrating_segments() == []

        # Kein ``migrating``-Segment → Gating fällt weg → das über-budget attached
        # Legacy-Segment (ältestes/größtes) wird beim reconfigure zurückgewonnen.
        await rb.reconfigure(storage="file", max_file_size_bytes=1)
        assert await rb.store.manifest.list_legacy_segments() == []
    finally:
        await rb.stop()
