"""Codex-P2-Fixes an der Legacy-Migration (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, jeweils TDD-first geschrieben –
er reproduziert den Bug ohne Fix und wird durch den Fix grün:

1. Legacy-Sidecars (``-wal``/``-shm``) werden in die Manifest-``size_bytes``
   einer read-only eingehängten Legacy-DB eingerechnet – analog zur WAL-Erfassung
   aktiver v2-Segmente. Sonst unterschätzen ``/stats`` und Size-Budget-Retention
   eine Legacy-DB, deren Hauptdatei klein ist, deren WAL aber die reale
   Disk-Nutzung übers Budget treibt.
2. ``migrate_chunk()`` bewahrt die Legacy-Ordnung: migrierte Alt-Zeilen bekommen
   synthetische NEGATIVE ``global_event_id``s (wie der read-only-Legacy-Pfad),
   damit sie NICHT vor echte neuere v2-Events rutschen, wenn die Migration NACH
   den ersten v2-Writes läuft.
3. ``migrate_chunk()`` ist idempotent gegen Wiederholung: crasht der Prozess
   zwischen Append und Resume-State-Write, importiert der nächste Lauf dieselben
   Legacy-Zeilen NICHT erneut (keine Duplikate).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator, _ResumeState
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


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


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


# ---------------------------------------------------------------------------
# (1) Legacy-Sidecars in die Manifest-Größe einrechnen (:152)
# ---------------------------------------------------------------------------


async def test_attach_readonly_counts_wal_and_shm_in_size(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", 1)])
    main_size = db.stat().st_size

    # Nicht-leere Sidecars neben der Hauptdatei (Legacy-WAL noch nicht gecheckpointet).
    wal_bytes = b"x" * 4096
    shm_bytes = b"y" * 2048
    Path(f"{db}-wal").write_bytes(wal_bytes)
    Path(f"{db}-shm").write_bytes(shm_bytes)

    migrator = LegacyMigrator(store, db)
    classification = migrator.classify()
    seg = await migrator.attach_readonly(classification)

    # Ohne Fix zählt size_bytes nur die Hauptdatei → WAL+SHM fehlen und die reale
    # Disk-Nutzung wird unterschätzt (relevant für /stats und Size-Budget-Retention).
    assert seg.size_bytes == main_size + len(wal_bytes) + len(shm_bytes)
    assert seg.size_bytes > main_size


# ---------------------------------------------------------------------------
# (2) Chunk-Migration bewahrt die Legacy-Ordnung (negative gids) (:194)
# ---------------------------------------------------------------------------


async def test_migrated_legacy_rows_sort_after_newer_v2_events(store: SqliteSegmentStore, tmp_path: Path):
    # Neuere v2-Events sind BEREITS geschrieben, BEVOR die Legacy-Migration läuft.
    await store.append([_event("v2-new", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "legacy-old-1"),
            ("2020-01-02T00:00:00.000Z", "legacy-old-2"),
        ],
    )
    migrator = LegacyMigrator(store, db)
    copied = await migrator.migrate_chunk()
    assert copied == 2

    # Default-Query sortiert nach id desc. Die migrierten Legacy-Zeilen sind
    # historisch älter und MÜSSEN hinter dem echten neueren v2-Event sortieren.
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values[0] == "v2-new"
    # Alle migrierten Legacy-Zeilen bekommen NEGATIVE gids (unter allen v2-IDs).
    legacy_gids = [r["global_event_id"] for r in rows if r["new_value"] != "v2-new"]
    assert legacy_gids, "migrierte Legacy-Zeilen fehlen"
    assert all(g < 0 for g in legacy_gids)


async def test_migrated_legacy_rows_keep_internal_order(store: SqliteSegmentStore, tmp_path: Path):
    # Die interne Ordnung der Legacy-Zeilen (nach rowid) bleibt erhalten: höhere
    # rowid (neuer) ⇒ höhere (weniger negative) gid ⇒ sortiert desc davor.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "legacy-1"),  # rowid 1
            ("2020-01-02T00:00:00.000Z", "legacy-2"),  # rowid 2
            ("2020-01-03T00:00:00.000Z", "legacy-3"),  # rowid 3
        ],
    )
    await LegacyMigrator(store, db).migrate_chunk()
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["legacy-3", "legacy-2", "legacy-1"]


# ---------------------------------------------------------------------------
# (3) Resume-State idempotent / atomar mit Append (:198)
# ---------------------------------------------------------------------------


async def test_migrate_chunk_no_duplicate_on_crash_between_append_and_state(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "row-1"),
            ("2020-01-02T00:00:00.000Z", "row-2"),
            ("2020-01-03T00:00:00.000Z", "row-3"),
        ],
    )
    migrator = LegacyMigrator(store, db)

    # Erster Lauf kopiert alle 3 Zeilen und würde den Cursor persistieren …
    copied = await migrator.migrate_chunk()
    assert copied == 3

    # … simuliere einen Crash NACH dem Append-Commit, aber BEVOR (bzw. während)
    # der Resume-State geschrieben wurde: der Cursor steht noch am Anfang.
    migrator._save_state(_ResumeState(last_rowid=0, done=False))

    # Der nächste Lauf DARF dieselben Legacy-Zeilen nicht erneut importieren.
    copied_again = await migrator.migrate_chunk()

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["row-1", "row-2", "row-3"], f"Duplikate nach Resume: {values}"
    assert copied_again == 0


async def test_idempotency_floor_ignores_attached_legacy_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Ein separat read-only eingehängtes Legacy-Segment (schema_version=LEGACY) hat
    # keine v2-``ringbuffer``-Tabelle mit gid-Spalte und darf die Idempotenz-Grenze
    # NICHT stören (es wird beim Scan übersprungen).
    attached = tmp_path / "attached_legacy.db"
    _build_legacy(attached, [("2019-01-01T00:00:00.000Z", "attached")])
    await LegacyMigrator(store, attached).attach_readonly(LegacyMigrator(store, attached).classify())

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "row-1"), ("2020-01-02T00:00:00.000Z", "row-2")])
    migrator = LegacyMigrator(store, db)
    assert await migrator.migrate_chunk() == 2

    # Crash zwischen Append und State-Write: Cursor verloren.
    migrator._save_state(_ResumeState(last_rowid=0, done=False))
    assert await migrator.migrate_chunk() == 0

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    # attached (read-only) + row-1/row-2 (migriert) – jeweils genau einmal.
    assert values == ["attached", "row-1", "row-2"]


async def test_migrate_chunk_resumes_partial_without_duplicates(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "row-1"),
            ("2020-01-02T00:00:00.000Z", "row-2"),
            ("2020-01-03T00:00:00.000Z", "row-3"),
            ("2020-01-04T00:00:00.000Z", "row-4"),
        ],
    )
    migrator = LegacyMigrator(store, db)

    # Batch 1 kopiert 2 Zeilen.
    assert await migrator.migrate_chunk(batch_rows=2) == 2
    # Crash: Cursor zurück auf 0 (Batch-1-Rows sind aber schon committed).
    migrator._save_state(_ResumeState(last_rowid=0, done=False))

    # Resume darf die ersten 2 Zeilen nicht doppeln, sondern nur den Rest holen.
    total = 0
    for _ in range(10):
        got = await migrator.migrate_chunk(batch_rows=2)
        total += got
        if got == 0:
            break

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["row-1", "row-2", "row-3", "row-4"]
