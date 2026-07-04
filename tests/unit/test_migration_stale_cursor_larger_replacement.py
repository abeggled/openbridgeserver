"""Codex #951 [P2] (migration.py:680) – Stale-Cursor-Reset bei GRÖSSERER Ersatzquelle.

Follow-up auf den Runde-31-Fix (``test_migration_stale_cursor_reset.py``). Dessen Reset
greift NUR, wenn der alte Cursor die aktuelle ``MAX(id)`` bereits abdeckt
(``after_rowid >= _legacy_max_rowid()``). Ersetzt/restauriert ein Operator die Legacy-DB
nach abgeschlossener Migration jedoch durch eine ANDERE Datei mit MEHR Zeilen als der alte
Cursor N, ist die Identität zwar stale, aber ``_legacy_max_rowid()`` bleibt ÜBER
``after_rowid`` → der alte Reset feuert NICHT → ``_read_batch(after_rowid=N)`` überspringt die
Zeilen ``1..N`` der NEUEN Generation → ``_finalize_and_detach`` versteckt sie PERMANENT.

Fix: Ist die Datei-Identität stale, den Cursor generations-frisch auf 0 setzen (Cursor UND
materialisierter Floor ignoriert), ES SEI DENN, die Änderung ist beweisbar append-only (die
bereits migrierte Boundary-Zeile bei rowid N ist in der neuen Datei unverändert vorhanden).
Der reine Grow/Append-Fall bleibt damit unberührt (Cursor gültig, keine Re-Migration), jede
ersetzende Änderung (auch replace-larger) setzt zurück.

TDD: Der replace-larger-Test ist auf dem alten Stand rot (``migrate_chunk`` überspringt
Zeilen ``1..N`` der neuen Generation) und wird durch den Fix grün. Der Grow-Gegentest sowie
der Truncate/replace-smaller-Fall (Runde 31) bleiben grün.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


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
        _insert_legacy_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()


def _insert_legacy_rows(conn: sqlite3.Connection, rows: list[tuple[str, object]]) -> None:
    for ts, value in rows:
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, "dp-legacy", "dp/dp-legacy/value", None, json.dumps(value), "legacy", "good"),
        )


def _replace_larger(path: Path, rows: list[tuple[str, object]]) -> None:
    """Ersetzt den Inhalt durch eine NEUE Generation mit MEHR Zeilen (rowids neu ab 1).

    Analog zu einem Operator, der die Legacy-DB nach abgeschlossener Migration durch eine
    andere Datei mit mehr Zeilen ersetzt/restauriert: die AUTOINCREMENT-Sequenz wird gelöscht,
    sodass die neuen rowids wieder bei 1 beginnen, ihr ``MAX(id)`` aber ÜBER dem alten Cursor
    liegt. Ändert size/mtime → Identität stale.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM ringbuffer")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'ringbuffer'")
        _insert_legacy_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()


def _append_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
    """Hängt weitere Zeilen an (rowids OBERHALB des Cursors) – Grow-Fall."""
    conn = sqlite3.connect(str(path))
    try:
        _insert_legacy_rows(conn, rows)
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


async def _values(store: SqliteSegmentStore) -> list[object]:
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    return [r["new_value"] for r in rows]


# ----------------------------------------------------------------------------
# Kern (rot vor Fix): GRÖSSERE Ersatzquelle – neue Generation hat MEHR Zeilen als
# der alte Cursor N. Die Zeilen 1..N der neuen Generation dürfen NICHT übersprungen
# werden (alter Reset feuerte nicht, weil MAX(id) > alter Cursor).
# ----------------------------------------------------------------------------


async def test_larger_replacement_reads_rows_below_old_cursor(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    # Erste Generation: 3 Zeilen → Cursor endet bei rowid 3, done=true.
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"OLD{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3
    state = migrator._load_state()
    assert state.done is True
    assert state.last_rowid == 3

    # Operator ersetzt die Legacy-DB durch eine ANDERE, GRÖSSERE Datei: 6 Zeilen, rowids 1..6.
    # MAX(id) = 6 > alter Cursor 3 → der alte Reset (after_rowid >= MAX) feuert NICHT.
    _replace_larger(a, [("2020-05-01T00:00:00.000Z", f"NEW{i}") for i in range(6)])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    # Vor dem Fix: after_rowid = max(3, 3) = 3, MAX=6, Reset feuert nicht → _read_batch(id>3)
    # liest nur NEW3..NEW5; NEW0..NEW2 (rowids 1..3) permanent versteckt.
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 6, "groessere Ersatzquelle: ALLE 6 neuen Zeilen muessen gelesen werden (nicht nur 1..N uebersprungen)"

    values = await _values(store)
    for i in range(6):
        assert values.count(f"NEW{i}") == 1, f"neue Zeile NEW{i} muss in v2 sichtbar sein (nicht permanent versteckt)"


# ----------------------------------------------------------------------------
# Gegentest (bleibt grün): Grow/Append – neue rowids OBERHALB des Cursors, die
# bereits migrierte Boundary-Zeile bleibt unverändert. Kein Reset, keine Duplikate.
# ----------------------------------------------------------------------------


async def test_grown_source_keeps_cursor_no_duplicates(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3
    assert migrator._load_state().last_rowid == 3

    # Grow: zwei neue Zeilen ANGEHÄNGT → rowids 4,5 (OBERHALB des Cursors 3), Boundary-Zeile
    # (rowid 3) unverändert. Beweisbar append-only → Cursor bleibt gültig.
    _append_legacy(a, [("2020-01-02T00:00:00.000Z", "A3"), ("2020-01-02T01:00:00.000Z", "A4")])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 2, "Grow-Fall: nur die neuen rowids > Cursor werden gelesen (kein Reset)"

    values = await _values(store)
    for i in range(5):
        assert values.count(f"A{i}") == 1, f"A{i} genau einmal – kein Reset/Duplikat im append-only-Fall"


# ----------------------------------------------------------------------------
# Gegentest (Runde 31, bleibt grün): replace-SMALLER – neue Generation hat WENIGER
# Zeilen als der alte Cursor; after_rowid >= MAX → Reset greift weiterhin.
# ----------------------------------------------------------------------------


async def test_smaller_replacement_still_resets(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(5)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 5
    assert migrator._load_state().last_rowid == 5

    # Replace kleiner: nur 2 Zeilen, rowids 1..2 (UNTERHALB des alten Cursors 5).
    _replace_larger(a, [("2020-02-01T00:00:00.000Z", "S0"), ("2020-02-01T01:00:00.000Z", "S1")])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 2, "kleinere Ersatzquelle: neue Zeilen (rowids <= alter Cursor) muessen gelesen werden"

    values = await _values(store)
    assert values.count("S0") == 1
    assert values.count("S1") == 1
