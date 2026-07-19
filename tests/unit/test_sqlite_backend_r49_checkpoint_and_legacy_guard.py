"""Codex-Runde-49 [P2]: Checkpoint-Lifecycle + Legacy-First-Append-Guard (#951).

Drei Feinschliff-Findings am verbliebenen Store-/RingBuffer-Code:

* **A (:3063)** – ``run_pending_checkpoints`` muss VOR dem Unlimited-Retention-
  Early-Return laufen, sonst bleiben ``checkpoint_pending``-Segmente auf
  Installationen ohne Retention-Limits (alle Limits ``None``) dauerhaft hängen.
* **B (:2594)** – nach erfolgreichem WAL-Truncate wird Status ``closed`` + reale
  post-checkpoint-``size_bytes`` atomar geschrieben; ein Crash zwischen zwei
  Writes ließ sonst ein retention-eligible ``closed`` Segment mit der alten
  WAL-schweren Größe zurück.
* **C (:919)** – der First-Append-Retention-Guard erkennt attached Legacy per
  Schema, nicht per ``status='legacy'``, sodass ein VOR dem ersten Event
  quarantäniertes Legacy den Extra-Retention-Pass nicht verhindert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING, SEGMENT_STATUS_CLOSED
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _iso(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


def _event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(ts=ts, datapoint_id="dp-1", topic="dp/dp-1/value", old_value=None, new_value=value, source_adapter="api", quality="good")


async def _make_store(root: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(root)
    await s.open()
    return s


# ---------------------------------------------------------------------------
# A: Checkpoint-Retry läuft auch bei unbegrenzter Retention
# ---------------------------------------------------------------------------


async def test_A_pending_checkpoint_retried_under_unlimited_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Alle Retention-Limits ``None`` (unbegrenzt): ``enforce_retention`` retryt trotzdem den Checkpoint."""
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        pending_id = (await store.manifest.get_active_segment()).segment_id

        # Erster Close busy → checkpoint_pending.
        monkeypatch.setattr(store, "_try_truncate_checkpoint", lambda _c: _false())
        await store.rotate()
        assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CHECKPOINT_PENDING

        # Unbegrenzte Retention (alle Limits None) – der WAL ist jetzt frei.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=None, max_age=None, max_entries=None)
        monkeypatch.setattr(store, "_try_truncate_checkpoint", lambda _c: _true())
        await store.enforce_retention()

        # Ohne den Fix bliebe das Segment ewig checkpoint_pending; jetzt wird es closed.
        assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CLOSED
    finally:
        await store.close()


async def _false() -> bool:
    return False


async def _true() -> bool:
    return True


# ---------------------------------------------------------------------------
# B: Close + post-checkpoint-Größe atomar
# ---------------------------------------------------------------------------


async def test_B_close_and_post_checkpoint_size_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Erfolgs-Rotation persistiert Status ``closed`` und post-checkpoint-Größe in EINEM Write.

    Kein separates ``close_segment`` (das ein retention-eligible ``closed`` mit
    alter WAL-Größe hinterlassen könnte), sondern ``close_segment_with_size``.
    """
    store = await _make_store(tmp_path / "root")
    try:
        await store.append([_event(1, _iso(0))])
        seg_id = (await store.manifest.get_active_segment()).segment_id

        # Spionieren: das transiente close_segment darf im Erfolgs-Pfad NICHT gerufen werden.
        transient_close: list[int] = []
        orig_close = store.manifest.close_segment

        async def _spy_close(sid):
            transient_close.append(sid)
            await orig_close(sid)

        monkeypatch.setattr(store.manifest, "close_segment", _spy_close)
        # checkpoint_ok = True (Default-Pfad ohne busy).
        await store.rotate()

        seg = await store.manifest.get_segment(seg_id)
        assert seg.status == SEGMENT_STATUS_CLOSED
        assert seg.closed_at is not None
        # Größe = reale post-checkpoint-Dateigröße (kein alter WAL-Überhang), in einem Rutsch gesetzt.
        assert seg.size_bytes == store._segment_file_size(seg.filename)
        assert seg_id not in transient_close, "kein transientes close_segment im Erfolgs-Pfad"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# C: quarantäniertes Legacy zählt weiter für den First-Append-Guard
# ---------------------------------------------------------------------------


async def _seed_legacy(path: Path, values: list[int]) -> None:
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, v in enumerate(values):
            await rb.record(
                ts=_iso(i), datapoint_id="dp-leg", topic="dp/dp-leg/value", old_value=None, new_value=v, source_adapter="api", quality="good"
            )
    finally:
        await rb.stop()


async def test_C_quarantined_legacy_still_counts_for_first_append_guard(tmp_path: Path):
    """Ein VOR dem ersten Event quarantäniertes Legacy hält den First-Append-Guard aktiv."""
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(legacy, [10, 11])

    rb = RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        max_entries=None,
        segmented=True,
        segment_max_age=24 * 60 * 60,
    )
    await rb.start()
    try:
        assert await rb._has_attached_legacy_segment() is True

        # Read-Fehler simulieren: Legacy-Segment quarantinieren (Status -> quarantined).
        from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION

        for seg in [s for s in await rb._store.manifest.list_segments() if s.schema_version <= LEGACY_SCHEMA_VERSION]:
            await rb._store.manifest.mark_quarantined(seg.segment_id, "corrupt-read (Test)")

        # status='legacy' ist jetzt leer – der Guard muss trotzdem True liefern (schema-basiert).
        assert await rb._store.manifest.list_legacy_segments() == []
        assert await rb._has_attached_legacy_segment() is True, "quarantäniertes Legacy muss weiter zählen"
    finally:
        await rb.stop()
