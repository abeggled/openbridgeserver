"""Codex-P2 (#951) – Invariante: kopierte v2-Chunks sind query-sichtbar GENAU DANN,
wenn die Legacy-Quelle detached ist. Nie beides (Doppel-Delivery), nie beides versteckt
+ Marker (verdeckter Verlust).

Zwei Folge-Findings der Runde-19-Reihenfolge (finalize/promote VOR detach/Marker):

* **Finding 1 (migration.py:392)** – Schlägt ``_detach_migrated_legacy_segment()`` NACH
  der Promotion fehl (z. B. ``_mark_source_migrated()`` kann den ``.migrated``-Marker im
  read-only Legacy-Verzeichnis nicht schreiben), sind die kopierten Chunks bereits
  ``closed``/``migrated`` (sichtbar), die Legacy-Zeile aber noch attached → jede migrierte
  Zeile käme bis zum Retry DOPPELT.
  Fix (Option B): PROMOTE zuerst (nach ``closed``, re-hidebar), dann Marker/Detach.
  Scheitert Marker/Detach (``OSError``), wird die Promotion ZURÜCKGEROLLT (Segmente wieder
  ``migrating``/versteckt) und der Fehler re-raised → Chunks versteckt + Legacy attached →
  Single-Delivery, retry-sicher. Der Marker wird NIE geschrieben, solange die Chunks noch
  versteckt wären → kein „Marker gesetzt + Chunks versteckt + Legacy nicht re-attachbar"-
  Verlustfenster (das ein Marker-zuerst-Ansatz eingeführt hätte).

* **Finding 2 (migration.py:596)** – Crasht der Prozess nach dem Row-Commit, aber VOR
  ``mark_migrating`` (source noch attached), bleiben die durablen v2-Kopien sichtbar
  (``active``/``closed``) und werden zusammen mit der attached Legacy DOPPELT geliefert.
  Fix: beim (Re-)Lauf der Migration die Invariante durchsetzen – sichtbare, rein aus DIESER
  noch attached Quelle stammende (rein-negative) Segmente werden re-hidden (``migrating``).
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


# ----------------------------------------------------------------------------
# Finding 1: Detach-Fehler nach Promotion → keine sichtbaren Chunks bei attached Quelle
# ----------------------------------------------------------------------------


async def test_marker_failure_rolls_back_promotion_no_double_delivery(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Reproduziert Finding 1: der Marker-Schreibfehler (read-only Legacy-Verzeichnis) tritt
    # im Detach NACH der Promotion auf. Ohne Rollback wären die Chunks sichtbar promotet,
    # während die Legacy noch attached ist → Doppel-Delivery. Option B rollt die Promotion
    # zurück (Segmente wieder ``migrating``); die Zeilen kommen genau EINMAL (aus der Legacy).
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])  # Positive → migrating-Segmente

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    calls: list[str] = []
    real_finalize = migrator._finalize_migrated_segments

    async def _spy_finalize(own_migrating_ids=None):
        calls.append("finalize")
        return await real_finalize(own_migrating_ids)

    monkeypatch.setattr(migrator, "_finalize_migrated_segments", _spy_finalize)

    def _mark_boom():
        calls.append("marker")
        raise OSError("read-only legacy dir")

    monkeypatch.setattr(migrator, "_mark_source_migrated", _mark_boom)

    with pytest.raises(OSError):
        await migrator.migrate_chunk(batch_rows=100)

    # Option-B-Reihenfolge: PROMOTE (finalize) VOR dem fail-prone Marker.
    assert calls == ["finalize", "marker"], f"unerwartete Reihenfolge: {calls}"

    # Invariante nach Rollback: Legacy noch attached UND kopierte Chunks wieder ``migrating``
    # (versteckt) – KEIN sichtbar-promotetes Segment.
    assert await migrator._source_is_attached()
    visible_migrating = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING]
    assert visible_migrating, "kopierte Chunks müssen nach Rollback wieder versteckt (migrating) sein"

    # Query liefert jede Alt-Zeile GENAU EINMAL (aus der attached Legacy), kein Duplikat.
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values.count("L0") == 1 and values.count("L1") == 1, f"Doppel-Delivery: {values}"
    assert values == ["v2", "L1", "L0"]


async def test_marker_never_set_while_chunks_hidden(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Regressionsschutz gegen ein Marker-zuerst-Fenster: der ``.migrated``-Marker darf NIE
    # geschrieben sein, während kopierte Chunks noch versteckt (``migrating``) sind und die
    # Legacy nicht mehr re-attachbar wäre (verdeckter Verlust). Prüfung: schlägt der
    # Marker-touch fehl, existiert der Marker DANACH nicht, die Chunks sind wieder versteckt
    # und die Legacy ist weiter attached (also re-attachbar / liefert die Zeilen).
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    def _mark_boom():
        raise OSError("read-only legacy dir")

    monkeypatch.setattr(migrator, "_mark_source_migrated", _mark_boom)

    with pytest.raises(OSError):
        await migrator.migrate_chunk(batch_rows=100)

    # Kein publizierter Marker, solange die Chunks (nach Rollback) versteckt sind.
    assert not migrator._migrated_marker_path.exists(), "Marker darf bei versteckten Chunks NIE gesetzt sein"
    assert await migrator._source_is_attached(), "Legacy muss attached bleiben (Zeilen weiter lieferbar)"
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING], "Chunks müssen versteckt sein"


async def test_retry_after_marker_failure_completes_cleanly(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Nach dem behobenen Marker-Fehler (jetzt schreibbar) muss ein Retry die Migration
    # sauber abschließen: Chunks sichtbar, Legacy detached, Marker da, Historie einfach.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    real_mark = migrator._mark_source_migrated
    fail = {"boom": True}

    def _mark_maybe_boom():
        if fail["boom"]:
            raise OSError("read-only legacy dir")
        real_mark()

    monkeypatch.setattr(migrator, "_mark_source_migrated", _mark_maybe_boom)

    with pytest.raises(OSError):
        await migrator.migrate_chunk(batch_rows=100)

    # Verzeichnis wieder schreibbar → Retry.
    fail["boom"] = False
    assert await migrator.migrate_chunk(batch_rows=100) == 0  # done: keine neuen Zeilen mehr

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["v2", "L1", "L0"], f"Historie nach Retry nicht vollständig/einfach: {values}"


# ----------------------------------------------------------------------------
# Finding 2: Crash nach Row-Commit vor mark_migrating → Recovery beim (Re-)Lauf
# ----------------------------------------------------------------------------


async def test_visible_copied_rows_with_attached_source_get_rehidden(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Simuliert Finding 2: ein erster Batch committet die kopierten Zeilen, crasht aber VOR
    # ``mark_migrating`` (source noch attached). Die Segmente bleiben sichtbar (nicht
    # ``migrating``) → Doppel-Delivery mit der attached Legacy. Der nächste Migrationslauf
    # muss die Invariante durchsetzen: sichtbare, rein-negative Segmente DIESER attached
    # Quelle werden re-hidden (``migrating``), bevor sie erneut lesbar sind.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])  # Positive → segregate-Pfad

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [(f"2020-01-{i:02d}T00:00:00.000Z", f"L{i}") for i in range(1, 6)])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    # Crash nach dem Row-Commit, aber bevor die Batch-Segmente versteckt werden.
    real_mark_migrating = store.manifest.mark_migrating

    async def _mark_migrating_then_crash(segment_id: int):  # noqa: ARG001
        raise RuntimeError("simulierter Crash vor mark_migrating")

    monkeypatch.setattr(store.manifest, "mark_migrating", _mark_migrating_then_crash)

    with pytest.raises(RuntimeError, match="simulierter Crash"):
        await migrator.migrate_chunk(batch_rows=2)

    # Post-Crash-Zustand: kopierte Zeilen sind durabel und (fälschlich) sichtbar, Quelle attached.
    monkeypatch.setattr(store.manifest, "mark_migrating", real_mark_migrating)
    assert await migrator._source_is_attached()

    # Ohne Recovery kämen L1/L2 doppelt (v2-Kopie sichtbar + attached Legacy). Der nächste
    # Migrationslauf MUSS die Invariante herstellen → keine Duplikate.
    await migrator.migrate_chunk(batch_rows=2)
    assert await migrator._source_is_attached(), "Quelle sollte während laufender Migration attached bleiben"
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows if str(r["new_value"]).startswith("L")]
    assert len(values) == len(set(values)), f"Doppel-Delivery kopierter Zeilen: {values}"


async def test_full_migration_after_crash_recovery_is_complete_and_simple(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Nach dem Crash im ersten Batch muss die Migration – über weitere reguläre Läufe –
    # vollständig und ohne Duplikate abschließen: alle Alt-Zeilen genau einmal, Legacy
    # detached, Marker da, keine ``migrating``-Reste.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    legacy_rows = [(f"2020-01-{i:02d}T00:00:00.000Z", f"L{i}") for i in range(1, 6)]
    _build_legacy(db, legacy_rows)

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    real_mark_migrating = store.manifest.mark_migrating
    crash = {"armed": True}

    async def _maybe_crash(segment_id: int):
        if crash["armed"]:
            crash["armed"] = False
            raise RuntimeError("simulierter Crash vor mark_migrating")
        await real_mark_migrating(segment_id)

    monkeypatch.setattr(store.manifest, "mark_migrating", _maybe_crash)

    with pytest.raises(RuntimeError, match="simulierter Crash"):
        await migrator.migrate_chunk(batch_rows=2)

    # Migration bis zum Abschluss weiterlaufen lassen (Recovery + restliche Batches).
    for _ in range(20):
        if (await migrator.migrate_chunk(batch_rows=2)) == 0:
            break

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    legacy_values = [v for v in values if str(v).startswith("L")]
    assert legacy_values == ["L5", "L4", "L3", "L2", "L1"], f"Historie unvollständig/dupliziert: {values}"
    assert values[0] == "v2"
