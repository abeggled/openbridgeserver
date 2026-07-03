"""Codex-P2 (#951, migration.py:653) – Rollback der Promotion auch bei Detach-Fehlern,
die KEIN ``OSError`` sind.

Folge-Finding zum Runde-20-Fix in ``_finalize_and_detach``: der Rollback griff dort NUR
bei ``OSError`` (Marker-``touch``-Fehler). Schlägt aber der finale Manifest-Delete in
``_detach_migrated_legacy_segment()`` fehl, NACHDEM ``_mark_source_migrated()`` erfolgreich
war (transienter SQLite-I/O-/Locking-Fehler → ``sqlite3.OperationalError``/``DatabaseError``,
kein ``OSError``), lief das Except ins Leere: die kopierten ``migrating``-Chunks blieben zu
query-sichtbaren ``closed``-Zeilen promotet, während die Original-Legacy-Zeile weiter attached
war → Reads lieferten dieselbe Legacy-Historie DOPPELT bis zum nächsten Retry.

Fix: der try/except in ``_finalize_and_detach`` fängt zusätzlich zu ``OSError`` auch
``sqlite3.Error`` (== ``aiosqlite.Error``, deckt ``OperationalError``/``DatabaseError`` ab) aus
dem GESAMTEN Detach-Schritt und wendet dieselbe verlustfreie Rollback-Logik an (Segmente
wieder ``migrating``, re-raise, kein done-Mark).
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


async def test_manifest_delete_sqlite_error_rolls_back_promotion(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Reproduziert das Finding: ``_mark_source_migrated`` gelingt, aber der anschliessende
    # Manifest-Delete (``delete_segment``) wirft einen transienten SQLite-Fehler
    # (``OperationalError``, KEIN ``OSError``). Ohne den erweiterten Rollback blieben die zuvor
    # promoteten Chunks sichtbar (``closed``) waehrend die Legacy noch attached ist → jede
    # Alt-Zeile DOPPELT. Erwartung: Rollback → Chunks wieder ``migrating``, Legacy attached,
    # Exception propagiert, kein done-Mark, Query liefert single delivery.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])  # Positive → migrating-Segmente

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    calls: list[str] = []
    real_mark = migrator._mark_source_migrated

    def _spy_mark():
        calls.append("marker")
        real_mark()  # Marker erfolgreich schreiben

    monkeypatch.setattr(migrator, "_mark_source_migrated", _spy_mark)

    # Manifest-Delete schlaegt NACH erfolgreichem Marker fehl – transienter SQLite-Fehler.
    def _delete_boom(segment_id: int):  # noqa: ARG001
        calls.append("delete")
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store.manifest, "delete_segment", _delete_boom)

    with pytest.raises(sqlite3.OperationalError):
        await migrator.migrate_chunk(batch_rows=100)

    # Marker lief VOR dem fehlschlagenden Delete (Reihenfolge in _detach_migrated_legacy_segment).
    assert calls == ["marker", "delete"], f"unerwartete Reihenfolge: {calls}"

    # Rollback-Invariante: Legacy noch attached UND kopierte Chunks wieder ``migrating`` (versteckt).
    assert await migrator._source_is_attached()
    assert await store.manifest.list_legacy_segments(), "Legacy-Zeile muss nach Rollback attached bleiben"
    visible_migrating = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING]
    assert visible_migrating, "kopierte Chunks müssen nach Rollback wieder versteckt (migrating) sein"

    # Query liefert jede Alt-Zeile GENAU EINMAL (aus der attached Legacy), kein Duplikat.
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values.count("L0") == 1 and values.count("L1") == 1, f"Doppel-Delivery: {values}"
    assert values == ["v2", "L1", "L0"]


async def test_retry_after_manifest_delete_error_completes_cleanly(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Nach dem behobenen (transienten) Manifest-Delete-Fehler muss ein Retry die Migration
    # sauber abschliessen: Chunks sichtbar, Legacy detached, Marker da, Historie einfach.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    real_delete = store.manifest.delete_segment
    fail = {"boom": True}

    async def _delete_maybe_boom(segment_id: int):
        if fail["boom"]:
            raise sqlite3.OperationalError("database is locked")
        await real_delete(segment_id)

    monkeypatch.setattr(store.manifest, "delete_segment", _delete_maybe_boom)

    with pytest.raises(sqlite3.OperationalError):
        await migrator.migrate_chunk(batch_rows=100)

    # Transienter Fehler behoben → Retry.
    fail["boom"] = False
    assert await migrator.migrate_chunk(batch_rows=100) == 0  # done: keine neuen Zeilen mehr

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["v2", "L1", "L0"], f"Historie nach Retry nicht vollständig/einfach: {values}"
