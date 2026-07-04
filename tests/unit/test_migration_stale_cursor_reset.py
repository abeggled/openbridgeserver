"""Codex #951 [P2] (migration.py:658) – Stale-Cursor-Reset bei ersetzter/getruncateter Quelle.

Follow-up auf den Runde-30-Fix (``done_is_stale`` lässt eine geänderte Quelle weiterlaufen,
siehe ``test_migration_resume_state_identity.py``). Problem: Nachdem ``done_is_stale`` das
Weiterlaufen erlaubt, faltet ``migrate_chunk`` weiterhin den ALTEN Cursor ein
(``max(state.last_rowid, _max_migrated_rowid())``). Ersetzt/truncatet ein Operator die
Legacy-DB nach abgeschlossener Migration, sodass die NEUEN Zeilen wieder UNTERHALB des alten
Cursors beginnen (truncate: rowids < altem Cursor; replace: andere Daten), liefert
``_read_batch(after_rowid=alter_cursor)`` leer, ``_finalize_and_detach`` schreibt einen
frischen Marker und entfernt die attached Quelle → die neuen Legacy-Zeilen sind PERMANENT
versteckt.

Fix: Ist die Datei-Identität stale UND deckt der alte Cursor die aktuelle ``MAX(id)`` der
Quelle bereits ab (Cursor >= Legacy-MAX), wird der effektive Cursor generations-frisch auf 0
zurückgesetzt, sodass ``_read_batch`` die neuen Zeilen ab Beginn liest. Der bloße
``append``/Grow-Fall (neue rowids OBERHALB des Cursors) bleibt unberührt: dort liegt die
Legacy-``MAX(id)`` über dem Cursor, der Cursor überspringt die bereits kopierten Zeilen
idempotent (kein Re-Scan/Duplikat).

TDD: Der Truncate/Replace-Test ist auf dem alten Stand rot (``migrate_chunk`` liefert 0 +
verdecktes Verstecken der neuen Zeilen) und wird durch den Fix grün. Der Grow-Gegentest
bleibt grün (Cursor gültig, keine Duplikate).
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


def _truncate_and_refill(path: Path, rows: list[tuple[str, object]]) -> None:
    """Ersetzt den Inhalt der Legacy-DB und startet die rowid-Sequenz NEU bei 1.

    Analog zu einem Operator, der die Legacy-DB nach abgeschlossener Migration ersetzt/
    truncatet: die AUTOINCREMENT-Sequenz wird gelöscht, sodass die neuen Zeilen wieder mit
    rowid 1 (also UNTERHALB des alten Cursors) beginnen. Ändert size/mtime → Identität stale.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM ringbuffer")
        # AUTOINCREMENT-Zähler zurücksetzen, damit die neuen rowids wieder bei 1 beginnen.
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
# Kern (rot vor Fix): getruncatete Quelle → neue Zeilen (rowids <= alter Cursor)
# werden gelesen und eingefaltet, NICHT permanent versteckt.
# ----------------------------------------------------------------------------


async def test_truncated_source_reads_new_rows_below_old_cursor(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    # Erste Generation: 5 Zeilen → Cursor endet bei rowid 5, done=true.
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(5)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 5
    state = migrator._load_state()
    assert state.done is True
    assert state.last_rowid == 5

    # Operator ersetzt die Legacy-DB: nur noch 2 Zeilen, rowids 1..2 (UNTERHALB des alten
    # Cursors 5). Identität ändert sich (size/mtime) → done_is_stale.
    _truncate_and_refill(a, [("2020-02-01T00:00:00.000Z", "NEW0"), ("2020-02-01T01:00:00.000Z", "NEW1")])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    # Vor dem Fix: after_rowid = max(5, 5) = 5 → _read_batch(id>5) leer → migrate_chunk == 0,
    # _finalize_and_detach VOR jedem Lesen → neue Zeilen permanent versteckt.
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 2, "getruncatete Quelle: neue Zeilen (rowids <= alter Cursor) muessen gelesen werden, nicht leer"

    values = await _values(store)
    assert values.count("NEW0") == 1, "neue Zeile NEW0 muss in v2 sichtbar sein (nicht permanent versteckt)"
    assert values.count("NEW1") == 1, "neue Zeile NEW1 muss in v2 sichtbar sein (nicht permanent versteckt)"


# ----------------------------------------------------------------------------
# Replace-Variante: gleiche Zeilenzahl wie zuvor, aber ANDERE Daten (rowids 1..5
# == alter Cursor). after_rowid >= MAX → Reset greift, sonst leer.
# ----------------------------------------------------------------------------


async def test_replaced_source_same_rowcount_reads_new_rows(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"OLD{i}") for i in range(5)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 5
    assert migrator._load_state().last_rowid == 5

    # Replace mit ANDEREN Daten, aber wieder 5 Zeilen (rowids 1..5 == alter Cursor).
    _truncate_and_refill(a, [("2020-03-01T00:00:00.000Z", f"REP{i}") for i in range(5)])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    # after_rowid = max(5, 5) = 5 == Legacy-MAX(id) → Reset auf 0 → alle 5 neuen Zeilen lesen.
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 5, "ersetzte Quelle (gleiche Zeilenzahl, andere Daten) muss alle neuen Zeilen lesen"

    values = await _values(store)
    for i in range(5):
        assert values.count(f"REP{i}") == 1, f"neue Zeile REP{i} muss sichtbar sein"


# ----------------------------------------------------------------------------
# Gegentest (bleibt grün): Grow/Append – neue rowids OBERHALB des Cursors.
# Der alte Cursor bleibt gültig; kein Reset, kein Re-Scan/Duplikat.
# ----------------------------------------------------------------------------


async def test_grown_source_keeps_cursor_no_duplicates(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3
    assert migrator._load_state().last_rowid == 3

    # Grow: zwei neue Zeilen ANGEHÄNGT → rowids 4,5 (OBERHALB des Cursors 3).
    _append_legacy(a, [("2020-01-02T00:00:00.000Z", "A3"), ("2020-01-02T01:00:00.000Z", "A4")])

    migrator2 = LegacyMigrator(store, a)
    await migrator2.attach_readonly(migrator2.classify())
    # Legacy-MAX(id) = 5 > Cursor 3 → KEIN Reset; Cursor 3 überspringt A0..A2 idempotent.
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 2, "Grow-Fall: nur die neuen rowids > Cursor werden gelesen"

    values = await _values(store)
    # Keine Duplikate der bereits migrierten Zeilen (Cursor bleibt gültig).
    assert values.count("A0") == 1
    assert values.count("A1") == 1
    assert values.count("A2") == 1
    assert values.count("A3") == 1
    assert values.count("A4") == 1


# ----------------------------------------------------------------------------
# Gegentest: unveränderte Datei → done kurzschließt, _legacy_max_rowid wird
# nie aufgerufen (kein unnötiger Re-Scan/Reset).
# ----------------------------------------------------------------------------


async def test_unchanged_source_short_circuits_no_reset(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3

    migrator2 = LegacyMigrator(store, a)
    assert migrator2._load_state().done is True
    # Datei unverändert → sofort 0, keine Duplikate.
    assert await migrator2.migrate_chunk(batch_rows=100) == 0
    assert sorted(await _values(store)) == ["A0", "A1", "A2"]


# ----------------------------------------------------------------------------
# Fokus-Unit-Test: _legacy_max_rowid liest die aktuelle MAX(id) read-only.
# ----------------------------------------------------------------------------


async def test_legacy_max_rowid_reflects_current_generation(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(5)])
    migrator = LegacyMigrator(store, a)
    assert await migrator._legacy_max_rowid() == 5

    # Nach Truncate/Refill mit 2 Zeilen beginnt die Sequenz neu → MAX(id) == 2.
    _truncate_and_refill(a, [("2020-02-01T00:00:00.000Z", "N0"), ("2020-02-01T01:00:00.000Z", "N1")])
    assert await migrator._legacy_max_rowid() == 2

    # Fehlende Datei → 0 (kein Crash).
    missing = LegacyMigrator(store, tmp_path / "does_not_exist.db")
    assert await missing._legacy_max_rowid() == 0
