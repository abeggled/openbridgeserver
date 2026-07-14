"""Codex-Runde 22: explizites Size-Budget hart deckeln + Startup-Retention während Migration aussetzen (#951).

Zwei Follow-up-Findings auf frühere Fixes:

* **F1 [P2]** – "Preserve explicit size budgets for validation": Der Uplift des
  Retention-Budgets auf die 3-Segment-Untergrenze (``_effective_store_max_file_size_bytes``)
  darf NUR im auto-abgeleiteten degenerierten Tiny-Budget-Fall greifen. Ist
  ``segment_max_bytes`` EXPLIZIT (per Config/Konstruktor) zu grob für ``max_file_size_bytes``
  gesetzt, muss das konfigurierte Budget harter Deckel bleiben und die 3-Segment-Regel greifen
  (Fehler statt stiller Aufblähung). Sonst liefe z. B. ``max_file_size_bytes=100 MiB`` mit
  explizitem ``segment_max_bytes=64 MiB`` still mit 192 MiB Store-Budget.

* **F2 [P1]** – "Defer startup retention while migration is active": Der unbedingte
  Startup-``enforce_retention()`` umgeht den Migrations-Guard. Startet der Server während einer
  chunked Legacy-Migration neu, kann das Manifest versteckte ``migrating``-Chunks enthalten,
  während die Original-Legacy-DB noch als einzige vollständige Quelle attached ist – der
  Startup-Pass löschte sie dann, bevor die restlichen Zeilen kopiert sind → Datenverlust.
  Deferral wie im Append-Pfad, solange ``list_migrating_segments()`` non-empty ist.

TDD-first: die Tests reproduzieren den jeweiligen Fehler ohne den Fix (rot) und werden durch
die surgical Fixes grün. Gegentests decken den auto-abgeleiteten Pfad (F1) bzw. den
migrations-freien Normalstart (F2) ab (kein Regress).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import (
    RingBuffer,
    _effective_store_max_file_size_bytes,
    derive_segment_max_bytes,
)
from obs.ringbuffer.store.config import RETENTION_SEGMENT_RATIO

_MIB = 1024 * 1024


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


# ---------------------------------------------------------------------------
# F1 [P2]: explizites Size-Budget bleibt harter Deckel
# ---------------------------------------------------------------------------


def test_explicit_segment_budget_is_not_uplifted():
    """Explizit zu grobes ``segment_max_bytes`` bläht das Budget NICHT auf (#951, F1).

    Reproduziert den Kern des Findings direkt an ``_effective_store_max_file_size_bytes``:
    100 MiB Budget + explizit 64 MiB Segment darf NICHT auf 192 MiB angehoben werden, sondern
    bleibt bei 100 MiB. Damit läuft der Store-Open in die 3-Segment-Validierung (Fehler statt
    stiller Aufblähung).
    """
    effective = _effective_store_max_file_size_bytes(
        100 * _MIB,
        64 * _MIB,
        explicit_segment=True,
    )
    assert effective == 100 * _MIB


def test_auto_derived_tiny_budget_still_uplifted():
    """Gegentest: der auto-abgeleitete degenerierte Tiny-Budget-Fall bleibt funktionsfähig (#951, F1/P2).

    Für ein 1-Byte-Budget clamped ``derive_segment_max_bytes`` das Segment auf 1 Byte; der
    Uplift hebt das Budget auf ``RETENTION_SEGMENT_RATIO`` (= 3) an, damit die Auto-Ableitung
    nie über ``validate_store_config`` crasht. Dieser Pfad darf NICHT regressen.
    """
    derived = derive_segment_max_bytes(1)
    assert derived == 1
    effective = _effective_store_max_file_size_bytes(1, derived, explicit_segment=False)
    assert effective == RETENTION_SEGMENT_RATIO * derived == 3


def test_none_budget_stays_none_regardless_of_explicit_flag():
    """Unbegrenztes Size-Budget (None) bleibt None in beiden Pfaden (#951, F1)."""
    assert _effective_store_max_file_size_bytes(None, 64 * _MIB, explicit_segment=True) is None
    assert _effective_store_max_file_size_bytes(None, 64 * _MIB, explicit_segment=False) is None


@pytest.mark.asyncio
async def test_segmented_start_rejects_explicit_oversized_segment(tmp_path: Path):
    """Store-Open lehnt ein explizit zu grobes Segment ab statt still 192 MiB durchzusetzen (#951, F1).

    Ende-zu-Ende: ``max_file_size_bytes=100 MiB`` + explizit ``segment_max_bytes=64 MiB``
    verletzt die 3-Segment-Regel (100 MiB < 3 * 64 MiB). Der Start muss die dokumentierte
    3-Segment-Ablehnung (``ValueError``) auslösen, NICHT still mit aufgeblähtem Budget öffnen.
    """
    rb = RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        segmented=True,
        max_file_size_bytes=100 * _MIB,
        segment_max_bytes=64 * _MIB,
    )
    with pytest.raises(ValueError, match="segment_max_bytes"):
        await rb.start()
    # Kein leaked Store nach dem fehlgeschlagenen Start.
    assert rb.store is None


@pytest.mark.asyncio
async def test_segmented_start_honours_explicit_in_budget_segment(tmp_path: Path):
    """Gegentest: ein explizit regelkonformes Segment öffnet mit dem konfigurierten harten Deckel (#951, F1).

    ``max_file_size_bytes=192 MiB`` + explizit ``segment_max_bytes=64 MiB`` erfüllt die
    3-Segment-Regel exakt. Der Store öffnet, und das effektive Retention-Budget bleibt der
    konfigurierte 192-MiB-Deckel (kein zusätzlicher Uplift).
    """
    rb = RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        segmented=True,
        max_file_size_bytes=192 * _MIB,
        segment_max_bytes=64 * _MIB,
    )
    await rb.start()
    try:
        assert rb.store is not None
        assert rb.store._retention_config.max_file_size_bytes == 192 * _MIB
    finally:
        await rb.stop()


# ---------------------------------------------------------------------------
# F2 [P1]: Startup-Retention während laufender Migration aussetzen
# ---------------------------------------------------------------------------


async def _mark_a_closed_segment_migrating(rb: RingBuffer) -> int:
    """Erzeugt ein geschlossenes v2-Segment und markiert es als ``migrating``.

    ``mark_migrating`` stuft nur ``closed``/``checkpoint_pending`` Segmente um. Mit
    ``segment_max_rows=1`` schließt der zweite Append das erste Segment, das dann als
    ``migrating`` markiert wird (simuliert eine laufende chunked Migration).
    """
    await _record(rb, 900, "2026-05-01T00:00:00.000Z")
    await _record(rb, 901, "2026-05-01T00:00:01.000Z")
    closed = [s for s in await rb.store.manifest.list_segments() if s.status == "closed"]
    assert closed, "Erwartet mindestens ein geschlossenes Segment zum Markieren"
    segment_id = closed[0].segment_id
    await rb.store.manifest.mark_migrating(segment_id)
    assert len(await rb.store.manifest.list_migrating_segments()) == 1
    return segment_id
