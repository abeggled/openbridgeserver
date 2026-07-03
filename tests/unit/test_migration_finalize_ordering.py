"""Codex-P2 (#951, migration.py:386): Chunks sichtbar machen VOR Marker/Detach.

Finding: Crasht der Prozess ODER wirft ``_finalize_migrated_segments()``, NACHDEM der
Detach den ``.migrated``-Marker geschrieben und die Legacy-Manifest-Zeile entfernt hat,
aber BEVOR die kopierten ``migrating``-Segmente promotet (query-sichtbar) wurden, dann
überspringt der nächste Startup das Re-Attach der Legacy-Datei (Marker vorhanden,
Manifest-Zeile weg) UND ``list_segments_for_query()`` versteckt weiterhin die kopierten
Chunks (Status ``migrating``). Die migrierte Historie bleibt unsichtbar → verdeckter
Datenverlust.

Fix (Richtung a): erst promoten (sichtbar machen), DANN Marker schreiben + detachen. So
existiert kein Zustand, in dem Marker/Detach publiziert ist, während kopierte Chunks noch
versteckt (``migrating``) sind.

TDD: Die Tests reproduzieren den Bug auf der alten Reihenfolge (Detach vor Promote) und
werden durch den Fix grün.
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


async def test_promote_runs_before_marker_and_detach(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Reihenfolge-Nachweis: die kopierten ``migrating``-Segmente müssen query-sichtbar
    # promotet werden (``_finalize_migrated_segments``), BEVOR der Marker geschrieben und
    # die Legacy-Manifest-Zeile entfernt wird (``_detach_migrated_legacy_segment``). Auf
    # der alten Reihenfolge liefe der Detach zuerst → dieser Test schlägt fehl.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])  # Positive → migrating-Segmente entstehen

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    # Normaler Upgrade-Pfad: Quelle read-only einhängen, damit die kopierten Segmente
    # WÄHREND der Migration als ``migrating`` (versteckt) markiert werden – nur dann
    # existiert die Sichtbarkeits-Lücke, die der Fix adressiert.
    await migrator.attach_readonly(migrator.classify())
    calls: list[str] = []

    real_finalize = migrator._finalize_migrated_segments
    real_detach = migrator._detach_migrated_legacy_segment

    async def _spy_finalize():
        calls.append("finalize")
        await real_finalize()

    async def _spy_detach():
        calls.append("detach")
        await real_detach()

    monkeypatch.setattr(migrator, "_finalize_migrated_segments", _spy_finalize)
    monkeypatch.setattr(migrator, "_detach_migrated_legacy_segment", _spy_detach)

    assert await migrator.migrate_chunk(batch_rows=100) == 2

    # Promote (finalize) muss VOR dem Detach laufen.
    assert calls == ["finalize", "detach"], f"unerwartete Reihenfolge: {calls}"


async def test_crash_after_detach_leaves_no_hidden_migrating(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Simuliert einen Crash/Raise am Detach-Punkt: der (gepatchte) Detach schreibt den
    # Marker + entfernt die Legacy-Zeile (echte Arbeit) und wirft DANACH. Mit der alten
    # Reihenfolge (Detach zuerst) wäre zu diesem Zeitpunkt der Marker publiziert, die
    # Legacy-Zeile weg – aber die kopierten Chunks noch ``migrating`` (versteckt): ein
    # Startup ließe sie unsichtbar (verdeckter Datenverlust). Mit dem Fix (Promote zuerst)
    # sind die Chunks bereits sichtbar, BEVOR der Detach überhaupt läuft.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])  # Positive → migrating-Segmente

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    # Quelle read-only einhängen (normaler Upgrade-Pfad): erst dann werden die kopierten
    # Segmente während der Migration als ``migrating`` (versteckt) markiert – ohne Attach
    # gingen sie direkt auf ``migrated`` und die Sichtbarkeits-Lücke entstünde nie.
    await migrator.attach_readonly(migrator.classify())
    real_detach = migrator._detach_migrated_legacy_segment

    async def _detach_then_crash():
        await real_detach()  # Marker + Legacy-Zeile entfernen (publiziert)
        raise RuntimeError("simulierter Crash direkt nach Detach")

    monkeypatch.setattr(migrator, "_detach_migrated_legacy_segment", _detach_then_crash)

    with pytest.raises(RuntimeError, match="simulierter Crash"):
        await migrator.migrate_chunk(batch_rows=100)

    # Nach dem Crash: der Marker ist geschrieben und die Legacy-Zeile entfernt (Detach lief),
    # ALSO müssen die kopierten Chunks BEREITS sichtbar sein – kein verstecktes ``migrating``.
    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    migrating = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING]
    assert migrating == [], "kopierte Chunks bleiben nach publiziertem Marker/Detach versteckt (verdeckter Datenverlust)"

    # Die migrierte Historie ist über den normalen Query-Pfad vollständig sichtbar.
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["v2", "L1", "L0"]


async def test_full_migration_history_visible_and_complete(store: SqliteSegmentStore, tmp_path: Path):
    # Regression-Guard für die korrigierte Sequenz ohne Crash: nach Abschluss sind alle
    # migrierten Zeilen sichtbar, keine ``migrating``-Reste, Legacy abgekoppelt, Marker da.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 2

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in rows] == ["v2", "L1", "L0"]
