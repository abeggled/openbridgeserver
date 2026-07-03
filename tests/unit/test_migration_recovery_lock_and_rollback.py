"""Recovery-Rotation serialisieren + Migrations-Inserts zurückrollen (#951, Runde 24).

Zwei Codex-[P2]-Follow-ups auf den Runde-23-Write-Lock (``LegacyMigrator._write_lock``):

* **Finding 1 (migration.py:524) – „Serialize recovery rotation with live appends"**
  Die Recovery-Rotation in ``_recover_visible_migrated_while_attached`` (der
  ``store.rotate()``-Aufruf, nachdem ``_active_segment_has_own_migrated_only_rows()``
  true lieferte) läuft VOR ``_append_with_legacy_gids`` und nahm NIE den ``write_lock``.
  Ein Live-``record()`` konnte zwischen Check und Rotate eine positive Zeile ins aktive
  Segment appenden; das jetzt gemischte Segment erfüllt ``_segment_is_own_migrated_only``
  nicht mehr und bleibt query-sichtbar, während die Legacy-Quelle noch attached ist →
  Doppel-Delivery + aktive-Connection-Rotation-Race. Fix: die kritische Sektion
  (Check + Rotate) unter ``self._write_lock`` serialisieren; ``None`` bleibt No-Op.

* **Finding 2 (migration.py:798) – „Roll back failed migration inserts"**
  Der direkte Migrations-Write-Pfad (``_insert_event`` + ``commit``) rollte die aktive
  Transaktion NICHT zurück, wenn Insert oder Commit fehlschlägt. Eine partielle,
  uncommittete Zeile könnte von einer späteren Operation auf derselben Connection
  fremd-committet werden. Fix: denselben ``try/except BaseException: rollback; raise``
  wie ``SqliteSegmentStore.append()`` um Insert+Commit legen.

TDD-first: die Tests belegen Finding-1-Blockade unter gehaltenem Lock (+ No-Op ohne
Lock) und Finding-2-Rollback bei einem forcierten Insert-Fehler (keine halbe Zeile
persistiert, Exception propagiert, Retry sauber).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
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
# Finding 1: Recovery-Rotation unter dem geteilten Write-Lock serialisieren
# ----------------------------------------------------------------------------


async def _arm_active_own_migrated_only(store: SqliteSegmentStore, db: Path) -> LegacyMigrator:
    """Baut den Recovery-Zustand: aktives Segment hält NUR die negativen migrierten
    Zeilen DIESER Quelle, Legacy noch attached.

    Reiner Ein-Quell-Fall (keine Positiven ⇒ ``segregate`` false): die negativen Zeilen
    landen im aktiven Segment. Der ``store.rotate()`` im ``source_attached``-Block wird
    beim ersten Lauf gecrasht, sodass die Zeilen im aktiven Segment verbleiben und die
    Batch-Segmente NICHT versteckt werden.
    """
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    real_rotate = store.rotate
    crash = {"armed": True}

    async def _rotate_maybe_crash():
        if crash["armed"]:
            crash["armed"] = False
            raise RuntimeError("simulierter Crash vor rotate im source_attached-Block")
        await real_rotate()

    store.rotate = _rotate_maybe_crash  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulierter Crash"):
            await migrator.migrate_chunk(batch_rows=100)
    finally:
        store.rotate = real_rotate  # type: ignore[method-assign]
    return migrator


async def test_recovery_rotation_serializes_under_shared_lock(store: SqliteSegmentStore, tmp_path: Path):
    """Die Recovery-Rotation blockiert, solange der geteilte Write-Lock gehalten wird.

    Reproduziert Finding 1: ohne Lock könnte ein Live-Append zwischen dem
    ``_active_segment_has_own_migrated_only_rows``-Check und ``store.rotate()`` eine
    positive Zeile ins aktive Segment schieben. Mit dem geteilten Lock wartet die
    Recovery-Rotation auf die Freigabe – die kritische Sektion ist serialisiert.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    # Erst den Recovery-Zustand ohne Lock herstellen (aktives Segment rein-negativ, attached).
    await _arm_active_own_migrated_only(store, db)

    lock = asyncio.Lock()
    migrator = LegacyMigrator(store, db, write_lock=lock)
    assert await migrator._source_is_attached()
    assert await migrator._active_segment_has_own_migrated_only_rows(*migrator._bucket_gid_bounds)

    # Simulierter in-flight Live-Append hält den Lock.
    await lock.acquire()
    task = asyncio.create_task(migrator._recover_visible_migrated_while_attached())
    try:
        await asyncio.sleep(0.05)
        assert not task.done(), "Recovery-Rotation lief trotz gehaltenem Write-Lock durch"
        # Solange blockiert, wurde das aktive Segment NICHT rotiert (Zeilen noch dort).
        assert await migrator._active_segment_has_own_migrated_only_rows(*migrator._bucket_gid_bounds)
    finally:
        lock.release()
        await asyncio.wait_for(task, timeout=5)

    # Nach Freigabe rotiert die Recovery korrekt: aktives Segment ist nicht mehr rein-negativ
    # (leer/rotiert) und die migrierten Zeilen liegen in einem versteckten Segment.
    assert not await migrator._active_segment_has_own_migrated_only_rows(*migrator._bucket_gid_bounds)


async def test_recovery_rotation_noop_without_lock(store: SqliteSegmentStore, tmp_path: Path):
    """Ohne ``write_lock`` (``None``) läuft die Recovery-Rotation unverändert durch."""
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    await _arm_active_own_migrated_only(store, db)

    migrator = LegacyMigrator(store, db)  # kein write_lock
    assert await migrator._active_segment_has_own_migrated_only_rows(*migrator._bucket_gid_bounds)

    await migrator._recover_visible_migrated_while_attached()

    # No-Op-Lock: die Rotation läuft trotzdem und heilt den Zustand.
    assert not await migrator._active_segment_has_own_migrated_only_rows(*migrator._bucket_gid_bounds)


# ----------------------------------------------------------------------------
# Finding 2: partielle Migrations-Inserts zurückrollen
# ----------------------------------------------------------------------------


async def test_failed_migration_insert_rolls_back_active_transaction(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    """Scheitert ein ``_insert_event``/Commit mitten in einer Migrations-Seite, wird die aktive
    Transaktion zurückgerollt – keine halbe Zeile wird von einer Folge-Operation fremd-committet.

    Reproduziert Finding 2: ohne Rollback bliebe eine partiell eingefügte Zeile in der offenen
    Transaktion der aktiven Connection und würde von einem späteren ``commit()`` (z. B. dem
    nächsten Live-Append) mit-committet, obwohl der Aufrufer einen Fehler sah.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1"), ("2020-01-03T00:00:00.000Z", "L2")])

    migrator = LegacyMigrator(store, db)

    # Fehler NACH dem ersten Insert (Haupt-Zeile bereits geschrieben, aber uncommittet).
    real_insert = store._insert_event
    calls = {"n": 0}

    async def _insert_then_boom(conn, gid, event):
        calls["n"] += 1
        await real_insert(conn, gid, event)
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error (simuliert)")

    monkeypatch.setattr(store, "_insert_event", _insert_then_boom)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await migrator.migrate_chunk(batch_rows=100)

    monkeypatch.setattr(store, "_insert_event", real_insert)

    # Die partielle Zeile darf NICHT persistiert sein: ein simulierter Folge-Commit auf der
    # aktiven Connection committet keine halbe Zeile mehr (Transaktion wurde zurückgerollt).
    assert store._active_conn is not None
    await store._active_conn.commit()  # dürfte nach Rollback nichts mehr durchreichen
    async with store._active_conn.execute("SELECT COUNT(*) FROM ringbuffer WHERE global_event_id < 0") as cur:
        (count,) = await cur.fetchone()
    assert count == 0, f"partielle migrierte Zeile wurde fremd-committet: {count}"

    # Query darf keine der migrierten Alt-Zeilen sehen (nichts durabel materialisiert).
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert not [r for r in rows if str(r["new_value"]).startswith("L")]


async def test_retry_after_insert_failure_is_clean_no_duplicate(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    """Nach dem Rollback schließt ein Retry die Migration sauber ab: keine Duplikate, keine
    fehlenden Zeilen, vollständige Historie.
    """
    db = tmp_path / "obs_ringbuffer.db"
    legacy_rows = [(f"2020-01-{i:02d}T00:00:00.000Z", f"L{i}") for i in range(1, 4)]
    _build_legacy(db, legacy_rows)

    migrator = LegacyMigrator(store, db)

    real_insert = store._insert_event
    fail = {"armed": True}

    async def _insert_maybe_boom(conn, gid, event):
        await real_insert(conn, gid, event)
        if fail["armed"]:
            fail["armed"] = False
            raise sqlite3.OperationalError("disk I/O error (simuliert)")

    monkeypatch.setattr(store, "_insert_event", _insert_maybe_boom)

    with pytest.raises(sqlite3.OperationalError):
        await migrator.migrate_chunk(batch_rows=100)

    # Fehler behoben → Retry bis Abschluss.
    monkeypatch.setattr(store, "_insert_event", real_insert)
    for _ in range(20):
        if (await migrator.migrate_chunk(batch_rows=100)) == 0:
            break

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows if str(r["new_value"]).startswith("L")]
    assert values == ["L3", "L2", "L1"], f"Historie nach Retry nicht vollständig/einfach: {values}"


async def test_live_append_after_rollback_only_commits_itself(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    """Ein Live-Append NACH dem gerollbackten Migrations-Insert committet ausschließlich seine
    eigene Zeile – die partielle migrierte Zeile ist weg (kein Fremd-Commit).
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)

    real_insert = store._insert_event
    calls = {"n": 0}

    async def _insert_then_boom(conn, gid, event):
        calls["n"] += 1
        await real_insert(conn, gid, event)
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error (simuliert)")

    monkeypatch.setattr(store, "_insert_event", _insert_then_boom)
    with pytest.raises(sqlite3.OperationalError):
        await migrator.migrate_chunk(batch_rows=100)
    monkeypatch.setattr(store, "_insert_event", real_insert)

    # Regulärer Live-Append danach.
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

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["live-new"], f"Fremd-Commit einer partiellen migrierten Zeile: {values}"
