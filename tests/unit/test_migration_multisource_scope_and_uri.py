"""Codex-P2 (#951, Runde 26): URI-Escaping (migration.py:593) + Source-Scoping der
Promotion (migration.py:654).

**Finding 1 (URI-Escaping):** Der Recovery-/Migrations-Lesepfad baute die SQLite-
``file:``-URI von Hand (``f"file:{path.as_posix()}?mode=ro"``). Enthält der Segment-
Root URI-Metazeichen (``?``/``#``), parst SQLite einen Teil des Pfads als Query/Fragment
→ falsche Datei/oeffnen scheitert; kopierte Chunks werden als „nicht recoverbar"
behandelt und bleiben nach einem Neustart sichtbar neben der attached Legacy-Quelle
→ Duplikate. Fix: derselbe prozent-encodierte Helper ``_sqlite_ro_uri`` wie im
read-only-Pfad (Runde 25).

**Finding 2 (Source-Scoping der Promotion):** Sind zwei Legacy-Quellen attached und
Quelle A hat bereits kopierte Chunks als ``migrating`` versteckt (abgebrochene chunked
Migration), promotete der Abschluss einer ANDEREN Quelle B die source-agnostische
``promote_migrating_segments`` JEDES ``migrating``-Segment – auch As. As Chunks würden
query-sichtbar, während As Original-Legacy-Segment noch attached ist → Doppel-Delivery.
Fix: Promotion (und Rollback) NUR auf die migrierten Segment-IDs der AKTUELLEN Quelle
scopen (eigener gid-Bucket).

TDD: Beide Tests sind auf dem alten Stand rot und werden durch den Fix grün.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_MIGRATING
from obs.ringbuffer.store.migration import LegacyMigrator
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


# ----------------------------------------------------------------------------
# Finding 1 – URI-Escaping bei Metazeichen im Segment-Root
# ----------------------------------------------------------------------------


async def test_recovery_and_migration_in_metachar_root_no_duplicates(tmp_path: Path):
    # Segment-Root mit URI-Metazeichen (``?`` und ``#``) im Pfad. Auf dem alten Stand
    # (rohe ``file:``-Interpolation) parste SQLite den Pfad-Anteil hinter dem ``?`` als
    # Query → das Recovery/Idempotenz-Öffnen der v2-Segmente scheiterte, kopierte Chunks
    # galten als „nicht recoverbar"/nicht materialisiert und blieben sichtbar neben der
    # attached Legacy-Quelle → Duplikate.
    root = tmp_path / "weird?dir#frag"
    root.mkdir()
    store = SqliteSegmentStore(root / "store")
    await store.open()
    try:
        # Ein echtes v2-Positive, damit die migrierten Chunks während der Migration als
        # ``migrating`` versteckt werden (In-Progress-Schutz greift nur mit Positive
        # ODER attached Quelle).
        await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

        db = root / "obs_ringbuffer.db"
        _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

        migrator = LegacyMigrator(store, db)
        await migrator.attach_readonly(migrator.classify())

        # Vollständige Migration in einem Batch. Intern werden dabei mehrfach v2-Segmente
        # read-only geöffnet (Idempotenz-Floor, has-positive/foreign-Checks, Recovery).
        assert await migrator.migrate_chunk(batch_rows=100) == 2

        # Keine versteckten Reste, Legacy abgekoppelt, Historie vollständig + korrekt sortiert.
        assert migrator._migrated_marker_path.exists()
        assert not await store.manifest.list_legacy_segments()
        assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

        rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
        assert [r["new_value"] for r in rows] == ["v2", "L1", "L0"]
    finally:
        await store.close()


async def test_normal_root_path_unchanged(tmp_path: Path):
    # Gegentest: normaler Pfad (ohne Metazeichen) verhält sich unverändert.
    store = SqliteSegmentStore(tmp_path / "store")
    await store.open()
    try:
        await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])
        db = tmp_path / "obs_ringbuffer.db"
        _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])
        migrator = LegacyMigrator(store, db)
        await migrator.attach_readonly(migrator.classify())
        assert await migrator.migrate_chunk(batch_rows=100) == 2
        rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
        assert [r["new_value"] for r in rows] == ["v2", "L1", "L0"]
    finally:
        await store.close()


# ----------------------------------------------------------------------------
# Finding 2 – Source-Scoping der Promotion bei zwei attached Quellen
# ----------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_finishing_one_source_keeps_other_sources_migrating_hidden(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Legacy-Quellen A und B, beide read-only attached. A wird nur TEILWEISE migriert
    # (batch < Zeilen), sodass As kopierte Chunks als ``migrating`` versteckt bleiben und A
    # WEITER attached ist. Danach wird B VOLLSTÄNDIG migriert und abgeschlossen.
    #
    # Auf dem alten Stand promotete Bs Abschluss die source-agnostische
    # ``promote_migrating_segments`` ALLE ``migrating``-Segmente – auch As – nach ``closed``
    # (sichtbar), obwohl As Legacy noch attached ist → As Zeilen kämen DOPPELT.
    #
    # Fix: nur Bs eigene migrierte Segmente promoten/detachen; As bleiben ``migrating``
    # (versteckt), solange As Legacy attached ist.
    a = tmp_path / "sourceA.db"
    b = tmp_path / "sourceB.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(6)])
    _build_legacy(b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    migrator_a = LegacyMigrator(store, a)
    migrator_b = LegacyMigrator(store, b)
    await migrator_a.attach_readonly(migrator_a.classify())
    await migrator_b.attach_readonly(migrator_b.classify())

    # A: einen einzelnen kleinen Batch → nicht fertig, A bleibt attached, As Chunks
    # sind ``migrating`` (versteckt).
    copied_a = await migrator_a.migrate_chunk(batch_rows=2)
    assert copied_a == 2
    assert await migrator_a._source_is_attached(), "A muss nach Teil-Migration noch attached sein"
    a_migrating_before = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating_before, "As Teil-Chunks muessen als migrating versteckt sein"

    # B: vollständig migrieren + abschließen (Detach).
    assert await migrator_b.migrate_chunk(batch_rows=100) == 2

    # A ist weiterhin attached; B ist abgekoppelt.
    assert await migrator_a._source_is_attached(), "A darf durch Bs Abschluss NICHT detacht werden"
    assert not await migrator_b._source_is_attached(), "B muss nach Abschluss abgekoppelt sein"

    # As migrierte Chunks bleiben ``migrating`` (versteckt) – sie dürfen durch Bs
    # Promotion NICHT sichtbar geworden sein.
    still_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating_before.issubset(still_migrating), "As Chunks wurden faelschlich durch Bs Abschluss promotet"

    # Kein Doppel-Delivery: As zwei kopierte Zeilen erscheinen genau EINMAL (aus der noch
    # attached Legacy-Quelle), Bs Zeilen ebenfalls genau einmal.
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values.count("A0") == 1
    assert values.count("A1") == 1
    assert values.count("B0") == 1
    assert values.count("B1") == 1


async def test_finishing_other_source_rollback_scoped_to_own_segments(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Rollback-Scope: schlägt Bs Detach fehl, dürfen NUR Bs gerade promotete Segmente
    # re-hidden werden – As unabhängige ``migrating``-Chunks müssen unberührt bleiben
    # (waren ohnehin schon versteckt; ein globaler Rollback dürfte sie nicht anfassen).
    a = tmp_path / "sourceA.db"
    b = tmp_path / "sourceB.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(6)])
    _build_legacy(b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    migrator_a = LegacyMigrator(store, a)
    migrator_b = LegacyMigrator(store, b)
    await migrator_a.attach_readonly(migrator_a.classify())
    await migrator_b.attach_readonly(migrator_b.classify())

    await migrator_a.migrate_chunk(batch_rows=2)
    a_migrating_before = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating_before

    # Bs Detach fehlschlagen lassen (OSError, wie ein read-only Legacy-Verzeichnis beim
    # Marker-touch) → der Rollback muss NUR Bs Segmente re-hiden.
    async def _boom():
        raise OSError("simulierter Detach-Fehler")

    monkeypatch.setattr(migrator_b, "_detach_migrated_legacy_segment", _boom)

    with pytest.raises(OSError, match="simulierter Detach-Fehler"):
        await migrator_b.migrate_chunk(batch_rows=100)

    # As Chunks blieben durchgehend ``migrating`` (versteckt); der Rollback hat sie nicht
    # angetastet, und Bs Segmente sind ebenfalls (wieder) versteckt.
    still_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating_before.issubset(still_migrating), "As Chunks durch fremden Rollback beruehrt"
    # A und B beide noch attached (kein done-Mark bei Bs Fehler).
    assert await migrator_a._source_is_attached()
    assert await migrator_b._source_is_attached()
