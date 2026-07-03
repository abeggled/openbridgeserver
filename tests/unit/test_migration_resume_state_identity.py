"""Codex #951 [P2] (migration.py:610) – Resume-``done``-State an Datei-Identität binden.

Follow-up auf die Marker-Staleness (Runde 27/29): nach einer ABGESCHLOSSENEN Migration
(``done=true``) ändert sich die Legacy-Datei (neue Zeilen). Der ``.migrated``-Marker wird
korrekt STALE, der Startup hängt die Datei wieder ein – ABER das per-Path Resume-JSON hält
weiter ``done=true``. Eine Wartungs-``migrate_chunk`` kehrte dann früh (``if state.done:
return 0``) zurück, BEVOR sie Zeilen liest oder die re-attachte Quelle detacht. Die neuen
Legacy-Zeilen wurden nie nach v2 gefaltet und es gab keine ``migrating``-Chunks, die die
Quelle vor Retention schützen → unter Budget-Druck konnte die attached Legacy-DB samt neuer,
nicht kopierter Zeilen gelöscht werden.

Fix: der Resume-``_ResumeState`` trägt jetzt die Datei-Identität (``_current_identity_fields``:
mtime+size der Hauptdatei UND ``-wal``/``-shm`` – DIESELBE Definition wie der Marker). Beim
``done``-Kurzschluss in ``migrate_chunk`` wird die gespeicherte gegen die aktuelle Identität
geprüft (``done_is_stale``). Weicht sie ab, gilt der ``done``-State als STALE und die neuen
Zeilen werden ab der materialisierten Grenze (``_max_migrated_rowid``) eingefaltet.

Gewählte Rückwärtskompat-Semantik: ein Alt-State OHNE ``identity``-Feld (vor diesem Fix
geschrieben) wird konservativ als „nicht stale" behandelt – ``done`` bleibt wie bisher
wirksam, keine unnötige Re-Migration bestehender Installs.

TDD: die Staleness-Tests sind auf dem alten Stand rot (früher Rückgabe mit 0) und werden
durch den Fix grün; die Gegentests (unveränderte Datei, Alt-State) bleiben grün.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator, _ResumeState
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


def _append_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
    """Hängt weitere Zeilen an eine bestehende Legacy-DB an (ändert size/mtime)."""
    conn = sqlite3.connect(str(path))
    try:
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
# Kern: geänderte Quelle nach ``done`` → neue Zeilen werden eingefaltet
# ----------------------------------------------------------------------------


async def test_changed_source_after_done_folds_new_rows(store: SqliteSegmentStore, tmp_path: Path):
    # Quelle A vollständig migrieren + abschließen (``done=true`` + Identität persistiert).
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3
    assert migrator._load_state().done is True
    assert await _values(store) == ["A2", "A1", "A0"]

    # Legacy-Datei ändert sich NACH dem ``done``: zwei neue Zeilen (Identität ändert sich).
    _append_legacy(a, [("2020-01-02T00:00:00.000Z", "A3"), ("2020-01-02T01:00:00.000Z", "A4")])

    # Ein neuer Migrator (frischer Prozess/Startup) auf denselben Pfad: der ``done``-State ist
    # STALE → migrate_chunk kehrt NICHT sofort mit 0 zurück, sondern faltet die neuen Zeilen ein.
    migrator2 = LegacyMigrator(store, a)
    copied = await migrator2.migrate_chunk(batch_rows=100)
    assert copied == 2, "neue Legacy-Zeilen muessen migriert werden, statt done kurzzuschliessen"

    values = await _values(store)
    assert values.count("A3") == 1
    assert values.count("A4") == 1
    # Die bereits migrierten Zeilen wurden idempotent uebersprungen (materialisierte Grenze).
    assert values.count("A0") == 1
    assert values.count("A2") == 1
    # Danach ist wieder ``done`` mit der NEUEN Identität; ein Folgeaufruf kurzschliesst.
    assert migrator2._load_state().done is True
    assert await migrator2.migrate_chunk(batch_rows=100) == 0


# ----------------------------------------------------------------------------
# Gegentest: unveränderte Datei → ``done`` bleibt wirksam (kein Re-Scan)
# ----------------------------------------------------------------------------


async def test_unchanged_source_short_circuits(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 3

    # Datei UNVERÄNDERT: ein Folgeaufruf kehrt sofort mit 0 zurück (kein unnoetiger Re-Scan).
    migrator2 = LegacyMigrator(store, a)
    assert migrator2._load_state().done is True
    assert await migrator2.migrate_chunk(batch_rows=100) == 0
    # Keine Duplikate durch fehlerhaftes Re-Falten.
    assert sorted(await _values(store)) == ["A0", "A1", "A2"]


# ----------------------------------------------------------------------------
# Rückwärtskompat: Alt-State ohne Identität → ``done`` respektiert
# ----------------------------------------------------------------------------


async def test_legacy_state_without_identity_respects_done(store: SqliteSegmentStore, tmp_path: Path):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(3)])
    migrator = LegacyMigrator(store, a)

    # Alt-State simulieren: ``done=true`` OHNE ``identity`` (vor diesem Fix geschrieben).
    migrator._state_path.write_text(json.dumps({"last_rowid": 3, "done": True}), encoding="utf-8")
    loaded = migrator._load_state()
    assert loaded.done is True
    assert loaded.identity is None

    # Selbst wenn die Datei existiert/geändert wäre: ein Alt-State ohne Identität gilt
    # konservativ als NICHT stale → ``done`` bleibt wirksam (Rückwärtskompat, kein Re-Scan).
    _append_legacy(a, [("2020-01-02T00:00:00.000Z", "A3")])
    assert await migrator.migrate_chunk(batch_rows=100) == 0
    # Nichts wurde migriert (Alt-State respektiert done).
    assert await _values(store) == []


# ----------------------------------------------------------------------------
# Fokus-Unit-Tests der Staleness-Semantik (_ResumeState.done_is_stale)
# ----------------------------------------------------------------------------


def test_done_is_stale_semantics():
    ident = {"mtime_ns": 111, "size": 222, "wal_mtime_ns": 0, "wal_size": 0, "shm_mtime_ns": 0, "shm_size": 0}

    # done + identische aktuelle Identität → nicht stale.
    assert _ResumeState(last_rowid=3, done=True, identity=ident).done_is_stale(dict(ident)) is False
    # done + abweichende Identität (neue size) → stale.
    changed = dict(ident, size=999)
    assert _ResumeState(last_rowid=3, done=True, identity=ident).done_is_stale(changed) is True
    # done + reine WAL-Änderung → stale (Identität deckt -wal/-shm mit ab).
    wal_changed = dict(ident, wal_size=4096)
    assert _ResumeState(last_rowid=3, done=True, identity=ident).done_is_stale(wal_changed) is True
    # done, aber keine gespeicherte Identität (Alt-State) → nie stale.
    assert _ResumeState(last_rowid=3, done=True, identity=None).done_is_stale(changed) is False
    # done + Identität, aber aktuelle Datei fehlt (None) → nicht stale (keine neuen Zeilen).
    assert _ResumeState(last_rowid=3, done=True, identity=ident).done_is_stale(None) is False
    # nicht-done → nie stale (Zwischen-Stand).
    assert _ResumeState(last_rowid=3, done=False, identity=ident).done_is_stale(changed) is False


def test_resume_state_roundtrip_with_identity(tmp_path: Path, store: SqliteSegmentStore):
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", "A0")])
    migrator = LegacyMigrator(store, a)
    ident = migrator._current_identity_fields()
    assert ident is not None

    migrator._save_state(_ResumeState(last_rowid=1, done=True, identity=ident))
    loaded = migrator._load_state()
    assert loaded.last_rowid == 1
    assert loaded.done is True
    assert loaded.identity == ident

    # done=False speichert bewusst KEINE Identität (Zwischen-Stand); Alt-State-kompatibel.
    migrator._save_state(_ResumeState(last_rowid=1, done=False))
    raw = json.loads(migrator._state_path.read_text(encoding="utf-8"))
    assert "identity" not in raw
    assert migrator._load_state().identity is None
