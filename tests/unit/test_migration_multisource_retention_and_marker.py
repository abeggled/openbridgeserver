"""Codex #951 Runde 27 – drei Multi-Source-/Marker-Follow-ups in migration.py.

**Finding 1 (migration.py:658, Defer retention while any source is still migrating):**
Sind Migrationen mehrerer Legacy-Quellen verschränkt, ruft der Abschluss EINER Quelle
den post-detach-Retention-Pass (``_run_retention_after_detach``) auf, obwohl eine ANDERE
Quelle noch versteckte ``migrating``-Chunks + ihr Original-Legacy-Segment attached hat.
Unter Byte-/Row-/Age-Druck erfüllen die frisch finalisierten non-legacy-Daten den
No-Zero-History-Guard, sodass die Retention die andere attached Legacy-Datei wählen und
löschen könnte, BEVOR deren restliche Zeilen kopiert sind. Fix: den Pass überspringen,
solange global IRGENDWELCHE ``migrating``-Segmente existieren.

**Finding 2 (migration.py:1046, Keep other sources' migrating chunks hidden):**
``_mark_foreign_migrated_segments`` übersprang nur ``migrated``-Segmente + den aktuellen
Batch. Wird eine zweite Quelle migriert, während eine andere Quelle ``migrating``-Chunks
hält, würde deren verstecktes rein-negatives Segment ``migrated`` markiert → sichtbar,
während deren Original-Legacy attached ist → Doppel-Delivery. Fix: ``migrating``-Segmente
ebenfalls überspringen.

**Finding 3 (migration.py:312, Invalidate migrated markers when the legacy DB changes):**
Der path-only ``.migrated``-Marker unterdrückte das Attachen FÜR IMMER, obwohl die
Originaldatei bewusst liegen bleibt. Rollt ein Operator zurück / setzt ``segmented=false``
und dieselbe Datei bekommt NACH dem Marker neue Zeilen, würden diese still ignoriert
(Datenverlust). Fix: Marker an Datei-Identität (mtime+size) binden; weicht sie ab, gilt
der Marker als STALE und die Datei wird wieder klassifiziert. Gewählte Semantik: leerer
Alt-Marker (ohne Identität) = weiterhin suppress (Rückwärtskompat).

TDD: alle Tests sind auf dem alten Stand rot und werden durch den Fix grün.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_MIGRATING
from obs.ringbuffer.store.migration import LegacyMigrator, classify_legacy_db
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


# ----------------------------------------------------------------------------
# Finding 1 – Retention deferred, solange irgendeine Quelle noch migriert
# ----------------------------------------------------------------------------


async def test_retention_deferred_while_other_source_still_migrating(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Legacy-Quellen A und B, beide attached. A wird nur TEILWEISE migriert (Chunks
    # bleiben ``migrating``, A weiter attached). B wird vollständig migriert + abgeschlossen.
    # Ein hartes Byte-Budget ist gesetzt, das die attached Legacy-Datei von A als ältestes
    # Segment löschen WÜRDE, sobald Bs frische v2-Zeilen den No-Zero-History-Guard erfüllen.
    #
    # Fix (Finding 1): der post-detach-Retention-Pass wird übersprungen, solange global
    # ``migrating``-Segmente (As Chunks) existieren → As Legacy bleibt attached, keine
    # Alt-Zeilen gehen verloren.
    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)  # sofort „over budget"

    a = tmp_path / "sourceA.db"
    b = tmp_path / "sourceB.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(6)])
    _build_legacy(b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    migrator_a = LegacyMigrator(store, a)
    migrator_b = LegacyMigrator(store, b)
    a_seg = await migrator_a.attach_readonly(migrator_a.classify())
    await migrator_b.attach_readonly(migrator_b.classify())

    # A: nur ein kleiner Batch → As Chunks bleiben ``migrating``, A weiter attached.
    assert await migrator_a.migrate_chunk(batch_rows=2) == 2
    assert await migrator_a._source_is_attached()
    assert {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}

    # B: vollständig migrieren + abschließen. Der post-detach-Retention-Pass läuft hier.
    assert await migrator_b.migrate_chunk(batch_rows=100) == 2

    # Fix: As Legacy-Segment DARF NICHT gelöscht worden sein (deferred), solange As Chunks
    # noch ``migrating`` sind – der Byte-Druck hätte es sonst als ältestes gewählt.
    legacy_ids = {s.segment_id for s in await store.manifest.list_legacy_segments()}
    assert a_seg.segment_id in legacy_ids, "As attached Legacy wurde trotz laufender Migration retention-gelöscht"
    assert await migrator_a._source_is_attached()


async def test_retention_runs_once_last_source_migrating_gone(store: SqliteSegmentStore, tmp_path: Path):
    # Gegentest: sind KEINE ``migrating``-Segmente mehr übrig (die letzte Quelle schließt ab),
    # läuft die deferrte Retention regulär und gibt das nun nur noch v2-gedeckte Legacy frei.
    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)

    a = tmp_path / "onlysource.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(4)])

    migrator_a = LegacyMigrator(store, a)
    await migrator_a.attach_readonly(migrator_a.classify())

    # Vollständig migrieren + abschließen; danach keine ``migrating``-Segmente mehr.
    assert await migrator_a.migrate_chunk(batch_rows=100) == 4
    assert not await migrator_a._source_is_attached()
    assert {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING} == set()

    # Retention lief: das Legacy-Segment (ältestes, Guard erfüllt durch die migrierten v2-Zeilen)
    # wurde unter dem Byte-Budget freigegeben.
    assert not await store.manifest.list_legacy_segments(), "Retention hätte nach letztem Quell-Abschluss laufen müssen"


# ----------------------------------------------------------------------------
# Finding 2 – fremde ``migrating``-Chunks bleiben versteckt (kein Doppel-Delivery)
# ----------------------------------------------------------------------------


async def test_foreign_migrating_chunks_not_marked_migrated(store: SqliteSegmentStore, tmp_path: Path):
    # A hat ``migrating``-Chunks (Teil-Migration, weiter attached). B wird migriert. Der
    # Abschluss von B ruft ``_mark_foreign_migrated_segments`` auf. Auf dem alten Stand
    # würde As verstecktes rein-negatives Segment ``migrated`` markiert → sichtbar, obwohl
    # As Legacy noch attached ist → As Zeilen doppelt.
    #
    # Fix (Finding 2): ``migrating``-Segmente werden in der Schleife übersprungen; As Chunks
    # bleiben versteckt und As Zeilen erscheinen genau EINMAL (aus der attached Legacy).
    a = tmp_path / "sourceA.db"
    b = tmp_path / "sourceB.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(6)])
    _build_legacy(b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    migrator_a = LegacyMigrator(store, a)
    migrator_b = LegacyMigrator(store, b)
    await migrator_a.attach_readonly(migrator_a.classify())
    await migrator_b.attach_readonly(migrator_b.classify())

    assert await migrator_a.migrate_chunk(batch_rows=2) == 2
    a_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating

    # B abschließen; direkt ``_mark_foreign_migrated_segments`` prüfen (source_attached-Pfad).
    assert await migrator_b.migrate_chunk(batch_rows=100) == 2

    # As Chunks blieben ``migrating`` (nicht nach ``migrated`` promotet).
    still_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating.issubset(still_migrating), "As migrating-Chunks wurden faelschlich migrated markiert"

    # Kein Doppel-Delivery: As kopierte Zeilen kommen genau einmal (aus attached Legacy).
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values.count("A0") == 1
    assert values.count("A1") == 1
    assert values.count("B0") == 1
    assert values.count("B1") == 1


async def test_mark_foreign_directly_never_calls_mark_migrated_on_migrating(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Fokussierter Direkt-Test von ``_mark_foreign_migrated_segments``: ein ``migrating``-
    # Segment einer noch attached Quelle darf gar nicht erst als ``mark_migrated``-Kandidat
    # durchgereicht werden. Ein Spy auf ``manifest.mark_migrated`` weist nach, dass der Skip
    # VOR dem Promotion-Aufruf greift.
    #
    # (Ohne den Skip ist ``mark_migrated`` zwar durch seinen SQL-``status IN
    # ('closed','checkpoint_pending')``-Guard defensiv ein No-op, aber die Absicht des
    # Codes – fremde ``migrating``-Chunks NICHT anfassen – wird hier direkt geprüft.)
    a = tmp_path / "sourceA.db"
    _build_legacy(a, [("2020-01-01T00:00:00.000Z", f"A{i}") for i in range(6)])
    migrator_a = LegacyMigrator(store, a)
    await migrator_a.attach_readonly(migrator_a.classify())
    assert await migrator_a.migrate_chunk(batch_rows=2) == 2
    a_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating

    called_on: list[int] = []
    orig_mark_migrated = store.manifest.mark_migrated

    async def _spy(segment_id: int):
        called_on.append(segment_id)
        await orig_mark_migrated(segment_id)

    monkeypatch.setattr(store.manifest, "mark_migrated", _spy)

    # Direkter Aufruf (wie ihn Bs Abschluss triggert), OHNE exclude → migrating-Skip greift.
    await migrator_a._mark_foreign_migrated_segments(exclude_ids=None)

    assert not (set(called_on) & a_migrating), "mark_migrated wurde auf einem fremden migrating-Segment aufgerufen"
    still_migrating = {s.segment_id for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING}
    assert a_migrating.issubset(still_migrating)


# ----------------------------------------------------------------------------
# Finding 3 – Marker-Invalidierung bei geänderter Legacy-DB
# ----------------------------------------------------------------------------


async def test_marker_stale_when_legacy_db_changes_reclassifies(store: SqliteSegmentStore, tmp_path: Path):
    # Migration abschließen (Marker mit Identität geschrieben) → ``classify()`` unterdrückt
    # Re-Attach. Danach neue Zeilen in dieselbe Legacy-DB (mtime/size ändert sich, z. B.
    # nach Rollback / segmented=false). Der Marker gilt jetzt als STALE → ``classify()``
    # liefert NICHT mehr ``None``: die Datei wird wieder betrachtet, neue Zeilen gehen nicht
    # verloren.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 2
    assert migrator._migrated_marker_path.exists()

    # Unverändert: Marker unterdrückt weiterhin (Gegen-Anker).
    assert migrator.classify() is None

    # Neue Zeilen anhängen → Datei-Identität (mtime+size) weicht ab.
    conn = sqlite3.connect(str(db))
    try:
        _insert_legacy_rows(conn, [("2022-01-01T00:00:00.000Z", "L2")])
        conn.commit()
    finally:
        conn.close()

    # Marker ist STALE → Datei wird wieder klassifiziert (nicht mehr ``None``).
    reclassified = migrator.classify()
    assert reclassified is not None, "geänderte Legacy-DB wird still ignoriert (Datenverlust)"
    assert reclassified == classify_legacy_db(db)


async def test_marker_suppresses_when_legacy_unchanged(store: SqliteSegmentStore, tmp_path: Path):
    # Gegentest: bleibt die Datei nach der Migration unverändert, unterdrückt der Marker das
    # Re-Attach weiterhin (``classify()`` == ``None``) – kein Doppel-Delivery bereits
    # migrierter Zeilen.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 2

    assert migrator.classify() is None


def test_marker_backwards_compat_empty_marker_suppresses(tmp_path: Path, monkeypatch):
    # Rückwärtskompat: ein LEERER Alt-Marker (vor Runde 27 via ``touch`` erzeugt, ohne
    # Identität) verhält sich wie bisher → suppress, solange die Datei existiert. Bestehende
    # Installs/Tests brechen so nicht.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0")])

    class _StoreStub:
        _root = tmp_path

    migrator = LegacyMigrator(_StoreStub(), db)
    migrator._migrated_marker_path.touch()  # leerer Alt-Marker

    assert migrator.classify() is None, "leerer Alt-Marker muss weiterhin suppress (Rückwärtskompat)"
