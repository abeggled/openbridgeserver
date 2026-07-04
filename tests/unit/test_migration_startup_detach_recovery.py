"""Startup-Recovery verwaister Migrations-Chunks DETACHTER Quellen (#919, PR #951, Runde 41).

Der Runde-40-Fix (``_recover_detached_migrated_closed_segments`` +
``_promote_own_closed_migrated_to_trailing``) hat zwei Lücken, die hier TDD-first
reproduziert werden:

* **F1 (kein garantierter Aufruf):** die Recovery läuft NUR am Anfang von
  ``migrate_chunk`` – einem Wartungsjob ohne Produktions-Treiber. Crasht der Prozess
  NACH dem Detach (Marker gesetzt, Legacy-Zeile weg), aber BEVOR die eigenen
  rein-negativen ``closed``-Chunks in den Trailing-Rang (``migrated``) gehoben sind,
  bleiben sie im positiven Prefix. Ein NEUER Store-Startup ohne ``migrate_chunk``-Aufruf
  muss sie trotzdem promoten (``recover_detached_migrated_chunks`` auf dem
  ``_open_segment_store_locked``-Pfad).
* **F2 (source_factor post-Detach):** in einem NEUEN Prozess ist
  ``_attached_legacy_segment_id()`` ``None`` und ``_ensure_source_factor`` fiele auf den
  Pfad-Hash zurück – die vor dem Detach mit dem ATTACHED-``segment_id``-Bucket
  geschriebenen gids lägen außerhalb von ``[low, high)``. Die Startup-Recovery muss
  source-factor-UNABHÄNGIG greifen.

Gegentest: ist die Quelle NOCH attached (in-progress), fasst die Startup-Recovery die
Chunks NICHT an – dafür ist der attached-/round-37-Pfad zuständig.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CLOSED, SEGMENT_STATUS_MIGRATED
from obs.ringbuffer.store.migration import (
    LegacyMigrator,
    recover_detached_migrated_chunks,
)
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value, ts: str, *, dp: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


def _build_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
    """Legacy-Single-DB mit AUTOINCREMENT-rowid; ``rows`` = ``(ts, value)``."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """CREATE TABLE ringbuffer (
                   id             INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts             TEXT NOT NULL,
                   datapoint_id   TEXT NOT NULL,
                   topic          TEXT NOT NULL,
                   old_value      TEXT,
                   new_value      TEXT,
                   source_adapter TEXT NOT NULL,
                   quality        TEXT NOT NULL,
                   metadata_version INTEGER NOT NULL DEFAULT 1,
                   metadata       TEXT NOT NULL DEFAULT '{}'
               )"""
        )
        for ts, value in rows:
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, "dp-legacy", "dp/dp-legacy/value", None, json.dumps(value), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _closed_pure_negative_segment_ids(store: SqliteSegmentStore) -> list[int]:
    """``closed``-Segmente, die ausschließlich negative gids halten (verwaiste Migrations-Chunks)."""
    ids: list[int] = []
    active_id = store._active_segment.segment_id if store._active_segment else None
    for segment in await store.manifest.list_segments():
        if segment.schema_version <= 0 or segment.status != SEGMENT_STATUS_CLOSED or segment.segment_id == active_id:
            continue
        path = store._segments_dir / segment.filename
        conn = sqlite3.connect(str(path))
        try:
            has_pos = conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id >= 0 LIMIT 1").fetchone()
            has_neg = conn.execute("SELECT 1 FROM ringbuffer WHERE global_event_id < 0 LIMIT 1").fetchone()
        finally:
            conn.close()
        if has_pos is None and has_neg is not None:
            ids.append(segment.segment_id)
    return ids


async def _crash_detach_leaving_closed_chunks(store: SqliteSegmentStore, db: Path, monkeypatch) -> LegacyMigrator:
    """Simuliert einen Crash zwischen Detach und Trailing-Promotion.

    Migriert die Quelle vollständig (Detach: Marker gesetzt, Legacy-Zeile entfernt),
    unterdrückt aber die ``_promote_own_closed_migrated_to_trailing``-Phase – die
    eigenen rein-negativen Chunks bleiben ``closed`` statt ``migrated`` (der Zustand,
    den ein Crash NACH dem Detach hinterlässt).
    """
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    async def _noop(self) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(LegacyMigrator, "_promote_own_closed_migrated_to_trailing", _noop)
    total = await migrator.migrate_small(batch_rows=100)
    assert total == 2
    return migrator


# ===========================================================================
# F1: Startup-Recovery ohne migrate_chunk-Aufruf promotet verwaiste closed-Chunks
# ===========================================================================


async def test_startup_recovery_promotes_detached_closed_chunks(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Echte, NEUERE positive v2-Zeile existiert bereits (Trailing-Rang nötig).
    await store.append([_event("v2-live", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])

    await _crash_detach_leaving_closed_chunks(store, db, monkeypatch)

    # Detach erfolgt (keine Legacy mehr attached), aber die Chunks blieben ``closed``.
    assert await store.manifest.list_legacy_segments() == []
    orphaned = await _closed_pure_negative_segment_ids(store)
    assert orphaned, "Vorbedingung: es gibt verwaiste rein-negative ``closed``-Chunks"

    # Ohne Fix: eine ``id desc``-latest-page träfe die migrierten Legacy-Zeilen ZUERST,
    # weil die ``closed``-Chunks (höhere segment_id) im positiven Prefix sitzen.

    # Startup-Recovery OHNE migrate_chunk-Aufruf.
    await recover_detached_migrated_chunks(store)

    # Die verwaisten Chunks sind jetzt ``migrated`` (Trailing-Rang).
    for seg_id in orphaned:
        assert (await store.manifest.get_segment(seg_id)).status == SEGMENT_STATUS_MIGRATED

    # Eine ``id desc``-latest-page trifft zuerst die echte positive v2-Zeile.
    latest = await store.query(StoreQuery(limit=1))
    assert latest[0]["new_value"] == "v2-live"

    # Kein Doppel-Delivery: jede migrierte Zeile genau einmal.
    all_rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in all_rows)
    assert values == ["legacy-1", "legacy-2", "v2-live"]


async def test_startup_recovery_via_open_segment_store(tmp_path: Path, monkeypatch):
    # Wie oben, aber die Recovery läuft über den echten Startup-Pfad
    # ``RingBuffer._open_segment_store_locked`` (garantierter Aufruf, F1). Der Store-Root
    # wird von RingBuffer aus ``disk_path`` abgeleitet (``<stem>_segments``); die
    # Vorbedingung wird daher an EBEN DIESEM Root aufgebaut.
    from obs.ringbuffer.ringbuffer import RingBuffer

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])

    rb = RingBuffer(disk_path=str(db), segmented=True)
    seed = SqliteSegmentStore(rb._segment_store_root())
    await seed.open()
    try:
        await seed.append([_event("v2-live", "2026-06-01T00:00:00.000Z")])
        await _crash_detach_leaving_closed_chunks(seed, db, monkeypatch)
        orphaned = await _closed_pure_negative_segment_ids(seed)
        assert orphaned
    finally:
        await seed.close()

    # NEUER Store-Startup auf demselben Root, OHNE migrate_chunk.
    await rb.start()
    try:
        assert rb._store is not None
        for seg_id in orphaned:
            assert (await rb._store.manifest.get_segment(seg_id)).status == SEGMENT_STATUS_MIGRATED
        latest = await rb._store.query(StoreQuery(limit=1))
        assert latest[0]["new_value"] == "v2-live"
    finally:
        await rb.stop()


async def test_startup_recovery_noop_while_source_attached(store: SqliteSegmentStore, tmp_path: Path):
    # Gegentest: die Quelle ist NOCH attached (in-progress). Die Startup-Recovery darf
    # die (versteckten/geschlossenen) Chunks NICHT anfassen – dafür ist der attached-/
    # round-37-Pfad zuständig.
    await store.append([_event("v2-live", "2026-06-01T00:00:00.000Z")])
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    # Ein einzelner Chunk kopiert Zeilen, lässt die Quelle aber attached (in-progress).
    copied = await migrator.migrate_chunk(batch_rows=1)
    assert copied == 1
    assert len(await store.manifest.list_legacy_segments()) == 1  # noch attached

    statuses_before = {s.segment_id: s.status for s in await store.manifest.list_segments()}
    await recover_detached_migrated_chunks(store)
    statuses_after = {s.segment_id: s.status for s in await store.manifest.list_segments()}
    assert statuses_before == statuses_after  # unangetastet


# ===========================================================================
# F2: Recovery greift auch mit source-factor=Pfad-Hash (frischer Prozess)
# ===========================================================================


async def test_startup_recovery_source_factor_independent(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # In einem frischen Prozess ist ``_attached_legacy_segment_id()`` None → der
    # ``source_factor`` fiele auf den Pfad-Hash zurück, dessen ``[low, high)``-Bucket
    # die vor dem Detach mit dem ATTACHED-``segment_id``-Bucket geschriebenen gids NICHT
    # abdeckt. Die Startup-Recovery ist source-factor-UNABHÄNGIG und erfasst sie trotzdem.
    await store.append([_event("v2-live", "2026-06-01T00:00:00.000Z")])
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])
    await _crash_detach_leaving_closed_chunks(store, db, monkeypatch)
    orphaned = await _closed_pure_negative_segment_ids(store)
    assert orphaned

    # Beweis, dass ein bucket-gebundener Ansatz (Pfad-Hash-source_factor) die Chunks
    # VERFEHLEN würde: ein frischer Migrator (Quelle detached → Pfad-Hash-Faktor) hat
    # Bucket-Bounds, die die tatsächlich geschriebenen (attached-Bucket-)gids nicht
    # abdecken. Sein bucket-gescoptes ``_promote_own_closed_migrated_to_trailing`` lässt
    # sie daher ``closed``.
    fresh_migrator = LegacyMigrator(store, db)
    await fresh_migrator._promote_own_closed_migrated_to_trailing()
    still_closed = await _closed_pure_negative_segment_ids(store)
    assert still_closed == orphaned, "bucket-gebundener Pfad verfehlt die attached-Bucket-gids (F2)"

    # Die source-factor-UNABHÄNGIGE Startup-Recovery promotet sie dagegen.
    await recover_detached_migrated_chunks(store)
    for seg_id in orphaned:
        assert (await store.manifest.get_segment(seg_id)).status == SEGMENT_STATUS_MIGRATED


async def test_startup_recovery_noop_pure_legacy_single_source(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Rein legacy-migrierter Ein-Quell-Store OHNE echte positive v2-Zeile: die
    # segment_id-Ordnung == gid-Ordnung, ``closed`` genügt, kein Trailing-Rang nötig.
    # Die Recovery bleibt ein No-op (promotet nichts nach ``migrated``).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_small(batch_rows=100) == 2
    assert await store.manifest.list_legacy_segments() == []

    before = {s.segment_id: s.status for s in await store.manifest.list_segments()}
    await recover_detached_migrated_chunks(store)
    after = {s.segment_id: s.status for s in await store.manifest.list_segments()}
    assert before == after
    assert not any(s == SEGMENT_STATUS_MIGRATED for s in after.values())
