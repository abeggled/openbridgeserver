"""Migrations-Writes mit Live-Appends serialisieren (#951, Runde 23).

Codex-Finding „Serialize migration writes with live appends": ein Wartungs-
``migrate_chunk`` schreibt direkt in ``store._active_conn``, OHNE den Write-Lock
(``RingBuffer._lock``), den ``RingBuffer.record()`` hält. Ein Live-Append kann so ins
aktive Segment landen NACH dem Positive-Row-Check/Rotate, aber BEVOR die negativen
Legacy-Zeilen ``migrating``/``migrated`` markiert sind → das gemischte Segment wird
versteckt/in den Legacy-Tail verschoben und frische Live-Events verschwinden oder
sortieren als alt.

Fix: ``LegacyMigrator`` bekommt einen optionalen ``write_lock``; die Write-/Hide-
Sequenz (``_append_with_legacy_gids``) läuft ``async with self._write_lock``. Am
Konstruktionsort wird ``write_lock=self._lock`` (der Lock von ``record()``) übergeben.
Ohne Lock (``None``) bleibt alles No-Op.

TDD-first: der Serialisierungs-Test hält den gemeinsamen Lock und belegt, dass die
kritische Sektion der Migration blockiert, solange ein (simulierter) Live-Append den
Lock hält – und erst nach dessen Freigabe fortschreitet. Der No-Op-Test belegt, dass
ohne Lock die bestehende Migration unverändert durchläuft.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _build_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
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


@pytest.mark.asyncio
async def test_migration_write_section_serializes_under_shared_lock(store: SqliteSegmentStore, tmp_path: Path):
    """Solange der gemeinsame Lock (simulierter Live-``record()``) gehalten wird, blockiert die Migrations-Write-Sektion.

    Reproduziert den Race: hielte die Migration den Lock NICHT, könnte ihre Write-/Hide-
    Sequenz gleichzeitig zu einem Live-Append laufen. Mit dem geteilten Lock wartet
    ``migrate_chunk`` auf die Freigabe – die kritische Sektion ist serialisiert.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-1"), ("2020-01-02T00:00:00.000Z", "legacy-2")])

    lock = asyncio.Lock()
    migrator = LegacyMigrator(store, db, write_lock=lock)

    # Simulierter in-flight Live-Append: der Lock ist gehalten.
    await lock.acquire()
    task = asyncio.create_task(migrator.migrate_chunk())
    try:
        # Die Migration darf ihre Write-Sektion NICHT betreten, solange der Lock hängt.
        await asyncio.sleep(0.05)
        assert not task.done(), "migrate_chunk lief trotz gehaltenem Write-Lock durch"
        # Es wurde noch keine Legacy-Zeile materialisiert (Write-Sektion blockiert).
        rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
        assert not any(r["new_value"] in {"legacy-1", "legacy-2"} for r in rows)
    finally:
        # Live-Append fertig → Lock frei → Migration schreitet fort.
        lock.release()
        copied = await asyncio.wait_for(task, timeout=5)

    assert copied == 2
    values = {r["new_value"] for r in await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))}
    assert {"legacy-1", "legacy-2"}.issubset(values)


@pytest.mark.asyncio
async def test_migration_without_lock_is_noop_and_completes(store: SqliteSegmentStore, tmp_path: Path):
    """Ohne ``write_lock`` (``None``) läuft die Migration unverändert durch (bestehendes Verhalten)."""
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-a"), ("2020-01-02T00:00:00.000Z", "legacy-b")])

    migrator = LegacyMigrator(store, db)  # kein write_lock
    copied = await migrator.migrate_chunk()

    assert copied == 2
    values = {r["new_value"] for r in await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))}
    assert {"legacy-a", "legacy-b"}.issubset(values)


@pytest.mark.asyncio
async def test_live_append_does_not_get_hidden_by_migration(store: SqliteSegmentStore, tmp_path: Path):
    """Ein serialisierter Live-Append bleibt sichtbar und sortiert als neu; Legacy versteckt sich korrekt.

    Der geteilte Lock zwingt Live-Append und Migrations-Write-Sequenz in eine
    Reihenfolge. Läuft der Live-Append VOR der Migration (Lock zuerst genommen), landet
    seine positive Zeile im aktiven Segment; die Migration rotiert danach sauber und
    versteckt NUR ihre eigenen negativen Zeilen – der Live-Event bleibt sichtbar/neu.
    """
    from obs.ringbuffer.store.interface import StoreEvent

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "legacy-old")])

    lock = asyncio.Lock()
    migrator = LegacyMigrator(store, db, write_lock=lock)

    # Live-Append zuerst serialisiert einspielen (unter dem geteilten Lock, wie record()).
    async with lock:
        await store.append(
            [
                StoreEvent(
                    ts="2026-06-01T00:00:00.000Z",
                    datapoint_id="dp-live",
                    topic="dp/dp-live/value",
                    old_value=None,
                    new_value="live-new",
                    source_adapter="api",
                    quality="good",
                )
            ]
        )

    # Danach migrieren (Lock frei → Write-Sektion läuft ungestört).
    copied = await migrator.migrate_chunk()
    assert copied == 1

    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    # Der frische Live-Event bleibt sichtbar und sortiert als neuester (id desc).
    assert values[0] == "live-new"
    # Die migrierte Legacy-Zeile ist ebenfalls sichtbar, aber als älter dahinter.
    assert "legacy-old" in values
    assert values.index("live-new") < values.index("legacy-old")
