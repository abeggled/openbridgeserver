"""Codex-P2-Fixes an der Legacy-Migration (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, jeweils TDD-first geschrieben –
er reproduziert den Bug ohne Fix und wird durch den Fix grün:

1. Legacy-Sidecars (``-wal``/``-shm``) werden in die Manifest-``size_bytes``
   einer read-only eingehängten Legacy-DB eingerechnet – analog zur WAL-Erfassung
   aktiver v2-Segmente. Sonst unterschätzen ``/stats`` und Size-Budget-Retention
   eine Legacy-DB, deren Hauptdatei klein ist, deren WAL aber die reale
   Disk-Nutzung übers Budget treibt.
2. ``migrate_chunk()`` bewahrt die Legacy-Ordnung: migrierte Alt-Zeilen bekommen
   synthetische NEGATIVE ``global_event_id``s (wie der read-only-Legacy-Pfad),
   damit sie NICHT vor echte neuere v2-Events rutschen, wenn die Migration NACH
   den ersten v2-Writes läuft.
3. ``migrate_chunk()`` ist idempotent gegen Wiederholung: crasht der Prozess
   zwischen Append und Resume-State-Write, importiert der nächste Lauf dieselben
   Legacy-Zeilen NICHT erneut (keine Duplikate).
4. ``migrate_chunk()``/``migrate_small()`` halten die Rotations-/Retention-
   Schwellen ein: ein Batch, der ``segment_max_rows``/``segment_max_bytes``
   reißt, wird in schwellengerechten Häppchen appended und rotiert, statt ein
   übergroßes Segment zu hinterlassen; anschließend läuft ``enforce_retention``,
   damit das Byte-Budget nicht gesprengt wird.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator, _ResumeState
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


# ---------------------------------------------------------------------------
# (1) Legacy-Sidecars in die Manifest-Größe einrechnen (:152)
# ---------------------------------------------------------------------------


async def test_attach_readonly_counts_wal_and_shm_in_size(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", 1)])
    main_size = db.stat().st_size

    # Nicht-leere Sidecars neben der Hauptdatei (Legacy-WAL noch nicht gecheckpointet).
    wal_bytes = b"x" * 4096
    shm_bytes = b"y" * 2048
    Path(f"{db}-wal").write_bytes(wal_bytes)
    Path(f"{db}-shm").write_bytes(shm_bytes)

    migrator = LegacyMigrator(store, db)
    classification = migrator.classify()
    seg = await migrator.attach_readonly(classification)

    # Ohne Fix zählt size_bytes nur die Hauptdatei → WAL+SHM fehlen und die reale
    # Disk-Nutzung wird unterschätzt (relevant für /stats und Size-Budget-Retention).
    assert seg.size_bytes == main_size + len(wal_bytes) + len(shm_bytes)
    assert seg.size_bytes > main_size


# ---------------------------------------------------------------------------
# (2) Chunk-Migration bewahrt die Legacy-Ordnung (negative gids) (:194)
# ---------------------------------------------------------------------------


async def test_migrated_legacy_rows_sort_after_newer_v2_events(store: SqliteSegmentStore, tmp_path: Path):
    # Neuere v2-Events sind BEREITS geschrieben, BEVOR die Legacy-Migration läuft.
    await store.append([_event("v2-new", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "legacy-old-1"),
            ("2020-01-02T00:00:00.000Z", "legacy-old-2"),
        ],
    )
    migrator = LegacyMigrator(store, db)
    copied = await migrator.migrate_chunk()
    assert copied == 2

    # Default-Query sortiert nach id desc. Die migrierten Legacy-Zeilen sind
    # historisch älter und MÜSSEN hinter dem echten neueren v2-Event sortieren.
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values[0] == "v2-new"
    # Alle migrierten Legacy-Zeilen bekommen NEGATIVE gids (unter allen v2-IDs).
    legacy_gids = [r["global_event_id"] for r in rows if r["new_value"] != "v2-new"]
    assert legacy_gids, "migrierte Legacy-Zeilen fehlen"
    assert all(g < 0 for g in legacy_gids)


async def test_migrated_legacy_rows_keep_internal_order(store: SqliteSegmentStore, tmp_path: Path):
    # Die interne Ordnung der Legacy-Zeilen (nach rowid) bleibt erhalten: höhere
    # rowid (neuer) ⇒ höhere (weniger negative) gid ⇒ sortiert desc davor.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "legacy-1"),  # rowid 1
            ("2020-01-02T00:00:00.000Z", "legacy-2"),  # rowid 2
            ("2020-01-03T00:00:00.000Z", "legacy-3"),  # rowid 3
        ],
    )
    await LegacyMigrator(store, db).migrate_chunk()
    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert values == ["legacy-3", "legacy-2", "legacy-1"]


# ---------------------------------------------------------------------------
# (3) Resume-State idempotent / atomar mit Append (:198)
# ---------------------------------------------------------------------------


async def test_migrate_chunk_no_duplicate_on_crash_between_append_and_state(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "row-1"),
            ("2020-01-02T00:00:00.000Z", "row-2"),
            ("2020-01-03T00:00:00.000Z", "row-3"),
        ],
    )
    migrator = LegacyMigrator(store, db)

    # Erster Lauf kopiert alle 3 Zeilen und würde den Cursor persistieren …
    copied = await migrator.migrate_chunk()
    assert copied == 3

    # … simuliere einen Crash NACH dem Append-Commit, aber BEVOR (bzw. während)
    # der Resume-State geschrieben wurde: der Cursor steht noch am Anfang.
    migrator._save_state(_ResumeState(last_rowid=0, done=False))

    # Der nächste Lauf DARF dieselben Legacy-Zeilen nicht erneut importieren.
    copied_again = await migrator.migrate_chunk()

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["row-1", "row-2", "row-3"], f"Duplikate nach Resume: {values}"
    assert copied_again == 0


async def test_idempotency_floor_ignores_attached_legacy_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Ein separat read-only eingehängtes Legacy-Segment (schema_version=LEGACY) hat
    # keine v2-``ringbuffer``-Tabelle mit gid-Spalte und darf die Idempotenz-Grenze
    # NICHT stören (es wird beim Scan übersprungen).
    attached = tmp_path / "attached_legacy.db"
    _build_legacy(attached, [("2019-01-01T00:00:00.000Z", "attached")])
    await LegacyMigrator(store, attached).attach_readonly(LegacyMigrator(store, attached).classify())

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "row-1"), ("2020-01-02T00:00:00.000Z", "row-2")])
    migrator = LegacyMigrator(store, db)
    assert await migrator.migrate_chunk() == 2

    # Crash zwischen Append und State-Write: Cursor verloren.
    migrator._save_state(_ResumeState(last_rowid=0, done=False))
    assert await migrator.migrate_chunk() == 0

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    # attached (read-only) + row-1/row-2 (migriert) – jeweils genau einmal.
    assert values == ["attached", "row-1", "row-2"]


async def test_migrate_chunk_resumes_partial_without_duplicates(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [
            ("2020-01-01T00:00:00.000Z", "row-1"),
            ("2020-01-02T00:00:00.000Z", "row-2"),
            ("2020-01-03T00:00:00.000Z", "row-3"),
            ("2020-01-04T00:00:00.000Z", "row-4"),
        ],
    )
    migrator = LegacyMigrator(store, db)

    # Batch 1 kopiert 2 Zeilen.
    assert await migrator.migrate_chunk(batch_rows=2) == 2
    # Crash: Cursor zurück auf 0 (Batch-1-Rows sind aber schon committed).
    migrator._save_state(_ResumeState(last_rowid=0, done=False))

    # Resume darf die ersten 2 Zeilen nicht doppeln, sondern nur den Rest holen.
    total = 0
    for _ in range(10):
        got = await migrator.migrate_chunk(batch_rows=2)
        total += got
        if got == 0:
            break

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["row-1", "row-2", "row-3", "row-4"]


# ---------------------------------------------------------------------------
# (4) Rotation/Retention-Schwellen bei Chunk-Migration einhalten (:234)
# ---------------------------------------------------------------------------


@pytest.fixture
async def small_segment_store(tmp_path: Path) -> SqliteSegmentStore:
    # segment_max_rows=3 → jedes Segment darf höchstens 3 Zeilen halten.
    s = SqliteSegmentStore(
        tmp_path / "root",
        segments=SegmentConfig(segment_max_rows=3),
    )
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_migrate_chunk_rotates_at_segment_max_rows(small_segment_store: SqliteSegmentStore, tmp_path: Path):
    # 7 Legacy-Zeilen in EINEM Batch bei segment_max_rows=3: ohne Fix schreibt der
    # Low-Level-Append den ganzen Batch ins EINE aktive Segment (7 Zeilen > 3) und
    # verletzt die Segmentierungs-Invariante. Mit Fix wird schwellengerecht rotiert.
    store = small_segment_store
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"row-{i}") for i in range(7)])

    copied = await LegacyMigrator(store, db).migrate_chunk(batch_rows=100)
    assert copied == 7

    # Kein einzelnes Segment darf die segment_max_rows-Schwelle überschreiten.
    segments = await store.manifest.list_segments()
    v2_rowcounts = [seg.row_count for seg in segments if seg.schema_version > 1]
    assert v2_rowcounts, "keine v2-Segmente materialisiert"
    assert all(rc <= 3 for rc in v2_rowcounts), f"übergroßes Segment: {v2_rowcounts}"

    # Alle 7 Zeilen sind trotz Rotation genau einmal lesbar.
    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == sorted(f"row-{i}" for i in range(7))


async def test_migrate_small_respects_row_budget_retention(tmp_path: Path):
    # Row-Budget-Retention: segment_max_rows=3, max_entries=9 (RATIO=3, Boden erfüllt).
    # 30 Legacy-Zeilen migriert → nach Rotation/Retention werden die ältesten
    # geschlossenen Segmente über dem Row-Budget gedroppt.
    store = SqliteSegmentStore(
        tmp_path / "root",
        segments=SegmentConfig(segment_max_rows=3),
        retention=StoreRetentionConfig(max_entries=9),
    )
    await store.open()
    try:
        db = tmp_path / "obs_ringbuffer.db"
        _build_legacy(db, [(f"2020-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T00:00:00.000Z", f"e-{i:02d}") for i in range(30)])

        total = await LegacyMigrator(store, db).migrate_small(batch_rows=100)
        assert total == 30

        # Retention hat geschlossene Segmente über dem Row-Budget entfernt.
        rows = await store.query(StoreQuery(limit=100))
        # Es bleibt deutlich weniger als die volle Menge übrig (Retention griff), aber
        # die JÜNGSTEN Zeilen bleiben erhalten (FIFO drop der ältesten Segmente).
        assert len(rows) < 30
        remaining = {r["new_value"] for r in rows}
        assert "e-29" in remaining  # jüngste migrierte Zeile bleibt
        assert "e-00" not in remaining  # älteste ist gedroppt
    finally:
        await store.close()


async def test_migrate_chunk_no_endless_rotation_on_small_batch(small_segment_store: SqliteSegmentStore, tmp_path: Path):
    # Ein Batch UNTER der Schwelle darf NICHT rotieren (kein leeres Segment,
    # keine Endlos-Rotation). Alle Zeilen liegen im selben aktiven Segment.
    store = small_segment_store
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "a"), ("2020-01-02T00:00:00.000Z", "b")])

    assert await LegacyMigrator(store, db).migrate_chunk(batch_rows=100) == 2
    segments = await store.manifest.list_segments()
    v2_segments = [seg for seg in segments if seg.schema_version > 1]
    # Genau ein v2-Segment mit beiden Zeilen (keine Rotation ausgelöst).
    assert len(v2_segments) == 1
    assert v2_segments[0].row_count == 2


# ===========================================================================
# Codex-P2-Fixes im OPTIONALEN Chunk-Migrationspfad (#919, PR #951, 2. Runde)
# ===========================================================================


def _build_legacy_raw(path: Path, *, with_metadata: bool, rows: list[tuple]) -> None:
    """Legacy-Single-DB mit/ohne metadata-Spalten; ``rows`` mit rohen Spaltenwerten.

    Ohne ``with_metadata`` fehlen ``metadata_version``/``metadata`` (pre-#388-Schema).
    ``rows`` sind ``(ts, old_value, new_value)`` – old/new als rohe TEXT-Zellen.
    """
    conn = sqlite3.connect(str(path))
    try:
        if with_metadata:
            conn.execute(
                """CREATE TABLE ringbuffer (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                       datapoint_id TEXT NOT NULL, topic TEXT NOT NULL,
                       old_value TEXT, new_value TEXT, source_adapter TEXT NOT NULL,
                       quality TEXT NOT NULL, metadata_version INTEGER NOT NULL DEFAULT 1,
                       metadata TEXT NOT NULL DEFAULT '{}')"""
            )
        else:
            conn.execute(
                """CREATE TABLE ringbuffer (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                       datapoint_id TEXT NOT NULL, topic TEXT NOT NULL,
                       old_value TEXT, new_value TEXT, source_adapter TEXT NOT NULL,
                       quality TEXT NOT NULL)"""
            )
        for ts, old_value, new_value in rows:
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, "dp-legacy", "dp/dp-legacy/value", old_value, new_value, "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (Runde 2 / 1) Legacy-Manifest-Eintrag nach Migration abkoppeln (:238)
# ---------------------------------------------------------------------------


async def test_migration_detaches_attached_legacy_entry_no_double_delivery(store: SqliteSegmentStore, tmp_path: Path):
    # Normaler Upgrade-Pfad: die Legacy-DB wird zuerst read-only als Legacy-Segment
    # eingehängt. Migriert ein späterer Wartungsjob dieselbe Datei vollständig nach
    # v2, MUSS der Legacy-Eintrag abgekoppelt werden, sonst wird jedes Event doppelt
    # geliefert (einmal v2, einmal aus dem noch eingehängten Legacy-Segment).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "one"), ("2020-01-02T00:00:00.000Z", "two")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert len(await store.manifest.list_legacy_segments()) == 1

    total = await migrator.migrate_small(batch_rows=100)
    assert total == 2

    # Der Legacy-Eintrag ist nach Abschluss abgekoppelt.
    assert await store.manifest.list_legacy_segments() == []

    # Jedes Event genau EINMAL (kein Doppel aus v2 + noch eingehängtem Legacy).
    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["one", "two"]


async def test_migrated_source_not_reattached_after_detach(store: SqliteSegmentStore, tmp_path: Path):
    # Codex #951, Pkt 2: Ist die migrierte Quelle die Default-``obs_ringbuffer.db``,
    # die der Startup beim Upgrade attached, entfernt der Detach nur die Manifest-
    # Zeile und lässt die Original-Datei liegen. Beim nächsten Restart sähe der
    # schema-basierte Attach-Guard, dass die Datei existiert und KEINE Legacy-
    # Manifest-Zeile mehr hat → er hängte dieselbe Legacy-DB erneut ein → jedes
    # migrierte Event würde DOPPELT geliefert.
    #
    # Fix: die Migration vermerkt die Quelle PERSISTENT (Markerfile neben der
    # Quelle), sodass ``classify()`` – den der Startup-Attach-Pfad konsultiert –
    # danach ``None`` liefert und die bereits vollständig migrierte Quelle NICHT
    # erneut einhängt. Die Original-Datei bleibt erhalten (Datenerhalt).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "one"), ("2020-01-02T00:00:00.000Z", "two")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_small(batch_rows=100) == 2
    assert await store.manifest.list_legacy_segments() == []

    # Original-Datei ist unangetastet (nur read-only gelesen).
    assert db.exists()

    # Der Startup-Attach-Pfad konsultiert ``classify()``. Nach abgeschlossener
    # Migration MUSS er ``None`` sehen (Marker vorhanden) → kein Re-Attach.
    reattach_migrator = LegacyMigrator(store, db)
    assert reattach_migrator.classify() is None

    # Selbst wenn der Startup-Guard laufen würde: kein neues Legacy-Segment.
    if reattach_migrator.classify() is not None:  # pragma: no cover - Guard
        await reattach_migrator.attach_readonly(reattach_migrator.classify())
    assert await store.manifest.list_legacy_segments() == []

    # Jedes Event weiterhin genau EINMAL.
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["one", "two"]


async def test_migration_detach_is_idempotent_without_attached_entry(store: SqliteSegmentStore, tmp_path: Path):
    # Wird NICHT vorher eingehängt (kein Legacy-Segment), darf der Abschluss der
    # Migration nicht scheitern – Abkoppeln ist ein No-op.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "solo")])
    total = await LegacyMigrator(store, db).migrate_small(batch_rows=100)
    assert total == 1
    assert await store.manifest.list_legacy_segments() == []
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["solo"]


# ---------------------------------------------------------------------------
# (Runde 2 / 2) id-Ordnung nach Chunk-Migration NACH Rotation bewahren (:278)
# ---------------------------------------------------------------------------


async def test_id_order_preserved_when_migration_runs_after_rotation(tmp_path: Path):
    # Positive v2-Writes wurden bereits über eine Rotation verteilt; DANACH migriert
    # ein Chunk-Job viele Legacy-Zeilen. Ohne Fix mischt das aktive Segment eine neue
    # positive Zeile mit vielen migrierten negativen und der ``id desc``-Frühabbruch
    # liefert die ALTEN Zeilen als „neueste". Mit Fix landen die Migrierten in einem
    # dedizierten ``migrated``-Segment, das ZULETZT iteriert wird.
    store = SqliteSegmentStore(tmp_path / "root", segments=SegmentConfig(segment_max_rows=2))
    await store.open()
    try:
        await store.append([_event("v2-a", "2026-01-01T00:00:00.000Z")])
        await store.append([_event("v2-b", "2026-01-02T00:00:00.000Z")])
        await store.append([_event("v2-c", "2026-01-03T00:00:00.000Z")])  # aktives Segment: 1 positive Zeile

        db = tmp_path / "obs_ringbuffer.db"
        _build_legacy(db, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"L{i}") for i in range(5)])
        assert await LegacyMigrator(store, db).migrate_chunk(batch_rows=100) == 5

        # Default id-desc-Query: die drei NEUESTEN müssen die positiven v2-Zeilen sein.
        rows = await store.query(StoreQuery(limit=3, sort_field="id", sort_order="desc"))
        assert [r["new_value"] for r in rows] == ["v2-c", "v2-b", "v2-a"]

        # Alle Zeilen bleiben genau einmal lesbar, korrekt global sortiert.
        allrows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
        assert [r["new_value"] for r in allrows] == ["v2-c", "v2-b", "v2-a", "L4", "L3", "L2", "L1", "L0"]
        # Migrierte Zeilen tragen negative gids, positive v2-Zeilen positive.
        assert all(r["global_event_id"] > 0 for r in allrows if r["new_value"].startswith("v2"))
        assert all(r["global_event_id"] < 0 for r in allrows if r["new_value"].startswith("L"))
    finally:
        await store.close()


async def test_id_order_preserved_with_positives_only_in_closed_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Positive v2-Zeilen liegen ausschließlich in einem GESCHLOSSENEN Segment; das
    # aktive Segment ist leer (nach manuellem rotate). ``_store_has_positive_rows``
    # muss die geschlossene Segmentdatei lesen und erkennen, dass Positive existieren,
    # damit die Migrierten trotzdem als ``migrated`` hinter sie sortiert werden.
    await store.append([_event("v2-a", "2026-01-01T00:00:00.000Z")])
    await store.append([_event("v2-b", "2026-01-02T00:00:00.000Z")])
    await store.rotate()  # positive Zeilen jetzt im geschlossenen Segment, aktiv leer

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])
    assert await LegacyMigrator(store, db).migrate_chunk(batch_rows=100) == 2

    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in rows] == ["v2-b", "v2-a", "L1", "L0"]
    migrated = [s for s in await store.manifest.list_segments() if s.status == "migrated"]
    assert migrated, "migrierte Zeilen wurden nicht als 'migrated' markiert"


async def test_migrated_only_store_needs_no_extra_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Rein legacy-migrierter Store OHNE positive Zeilen: segment_id-Ordnung == gid-
    # Ordnung von selbst; kein ``migrated``-Marker/Extra-Rotate nötig, ein Segment.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "a"), ("2020-01-02T00:00:00.000Z", "b")])
    assert await LegacyMigrator(store, db).migrate_chunk(batch_rows=100) == 2
    v2 = [s for s in await store.manifest.list_segments() if s.schema_version > 1]
    assert len(v2) == 1
    assert v2[0].status == "active"  # nicht als migrated markiert
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in rows] == ["b", "a"]


# ---------------------------------------------------------------------------
# (Runde 2 / 3) Replay-Detection pro Quell-DB scopen (:337)
# ---------------------------------------------------------------------------


async def test_resume_floor_scoped_per_source_db(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Legacy-DBs in denselben Store migriert. DB A hat rowids bis 5. Startet der
    # Migrator für DB B mit verlorenem Cursor, DARF der aus DB A materialisierte Floor
    # ihn NICHT dazu bringen, die ersten rowids von DB B zu überspringen.
    db_a = tmp_path / "legacy_a.db"
    _build_legacy(db_a, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"A{i}") for i in range(5)])
    db_b = tmp_path / "legacy_b.db"
    _build_legacy(db_b, [(f"2021-01-{i + 1:02d}T00:00:00.000Z", f"B{i}") for i in range(3)])

    assert await LegacyMigrator(store, db_a).migrate_small(batch_rows=100) == 5

    migrator_b = LegacyMigrator(store, db_b)
    # Crash zwischen Append und State-Write ist hier nicht nötig: mit leerem Cursor
    # würde ein GLOBALER Floor (max rowid aus A = 5) die ersten 3 rowids von B (1..3)
    # überspringen. Mit per-Quelle-Floor werden alle 3 migriert.
    assert await migrator_b.migrate_small(batch_rows=100) == 3

    rows = await store.query(StoreQuery(limit=100))
    values = sorted(r["new_value"] for r in rows)
    assert values == ["A0", "A1", "A2", "A3", "A4", "B0", "B1", "B2"]


async def test_per_source_resume_idempotent_after_crash(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Quellen; nach Crash (Cursor verloren) darf keine Quelle ihre schon
    # migrierten Zeilen doppeln und keine überspringen.
    db_a = tmp_path / "legacy_a.db"
    _build_legacy(db_a, [("2020-01-01T00:00:00.000Z", "A0"), ("2020-01-02T00:00:00.000Z", "A1")])
    db_b = tmp_path / "legacy_b.db"
    _build_legacy(db_b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    ma = LegacyMigrator(store, db_a)
    mb = LegacyMigrator(store, db_b)
    assert await ma.migrate_chunk(batch_rows=100) == 2
    assert await mb.migrate_chunk(batch_rows=100) == 2

    # Crash: beide Cursor verloren.
    ma._save_state(_ResumeState(last_rowid=0, done=False))
    mb._save_state(_ResumeState(last_rowid=0, done=False))
    assert await ma.migrate_chunk(batch_rows=100) == 0
    assert await mb.migrate_chunk(batch_rows=100) == 0

    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["A0", "A1", "B0", "B1"]


# ---------------------------------------------------------------------------
# (Runde 2 / 4) Dirty-WAL-Frames bei kleiner Legacy-DB mitmigrieren (:346)
# ---------------------------------------------------------------------------


async def test_migration_reads_committed_dirty_wal_frames(store: SqliteSegmentStore, tmp_path: Path):
    # Eine kleine Legacy-DB mit committeten, noch ungecheckpointeten WAL-Frames: die
    # jüngsten pre-upgrade-Events stehen nur im ``-wal``. Ohne Fix ignoriert
    # ``immutable=1`` diese Frames und die Zeilen gehen still verloren.
    #
    # Dirty-WAL wird erzeugt, indem die DB im WAL-Modus geschrieben wird und die
    # ``.db``+``-wal``-Dateien BEI NOCH OFFENER Writer-Connection an den Zielpfad
    # kopiert werden – so läuft kein Checkpoint (der beim letzten Close den WAL
    # falten würde) und der kopierte Snapshot behält einen nicht-leeren ``-wal``.
    src = tmp_path / "src.db"
    conn = sqlite3.connect(str(src))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(
        """CREATE TABLE ringbuffer (
               id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
               datapoint_id TEXT NOT NULL, topic TEXT NOT NULL, old_value TEXT,
               new_value TEXT, source_adapter TEXT NOT NULL, quality TEXT NOT NULL,
               metadata_version INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}')"""
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?)",
            (f"2020-01-0{i + 1}T00:00:00.000Z", "dp-legacy", "t", json.dumps(f"wal-{i}"), "legacy", "good"),
        )
    conn.commit()  # committed, aber (autocheckpoint=0) noch im -wal

    db = tmp_path / "obs_ringbuffer.db"
    # Snapshot inkl. -wal kopieren, solange der Writer offen ist (kein Checkpoint).
    db.write_bytes(src.read_bytes())
    Path(f"{db}-wal").write_bytes(Path(f"{src}-wal").read_bytes())
    conn.close()
    # Sicherstellen, dass ein nicht-leeres -wal am Zielpfad existiert (dirty).
    assert Path(f"{db}-wal").exists() and Path(f"{db}-wal").stat().st_size > 0

    total = await LegacyMigrator(store, db).migrate_small(batch_rows=100)
    assert total == 3
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["wal-0", "wal-1", "wal-2"]


# ---------------------------------------------------------------------------
# (Runde 2 / 5) pre-Metadata-Schema in der Chunk-Migration behandeln (:353)
# ---------------------------------------------------------------------------


async def test_migration_handles_pre_metadata_legacy_schema(store: SqliteSegmentStore, tmp_path: Path):
    # Alte Legacy-DB OHNE metadata_version/metadata-Spalten: das bedingungslose SELECT
    # dieser Spalten scheiterte mit „no such column" und machte die Historie unmigrierbar.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_raw(
        db,
        with_metadata=False,
        rows=[
            ("2020-01-01T00:00:00.000Z", None, json.dumps("old-1")),
            ("2020-01-02T00:00:00.000Z", None, json.dumps("old-2")),
        ],
    )
    total = await LegacyMigrator(store, db).migrate_small(batch_rows=100)
    assert total == 2
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["old-1", "old-2"]
    # Fehlende metadata-Spalten degradieren auf Defaults.
    assert all(r["metadata_version"] == 1 for r in rows)
    assert all(r["metadata"] == {} for r in rows)


# ---------------------------------------------------------------------------
# (Runde 2 / 6) Legacy-Werte bei Migration sicher decodieren (:386)
# ---------------------------------------------------------------------------


async def test_migration_safe_decodes_malformed_values(store: SqliteSegmentStore, tmp_path: Path):
    # Eine Zeile mit malformed (non-JSON) old_value/new_value darf die Migration NICHT
    # mit JSONDecodeError abbrechen und damit alle späteren Zeilen dauerhaft blockieren.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_raw(
        db,
        with_metadata=True,
        rows=[
            ("2020-01-01T00:00:00.000Z", None, json.dumps("valid-1")),
            ("2020-01-02T00:00:00.000Z", "{not json", "also not json}"),  # malformed
            ("2020-01-03T00:00:00.000Z", None, json.dumps("valid-3")),
        ],
    )
    total = await LegacyMigrator(store, db).migrate_small(batch_rows=100)
    assert total == 3  # keine Zeile blockiert
    rows = await store.query(StoreQuery(limit=100))
    values = sorted(str(r["new_value"]) for r in rows)
    # Der Rohwert der malformed-Zeile überlebt (statt Exception); gültige bleiben JSON.
    assert "valid-1" in values
    assert "valid-3" in values
    assert "also not json}" in values


# ===========================================================================
# Codex-P2-Fixes im OPTIONALEN Chunk-Migrationspfad (#919, PR #951, 3. Runde)
# ===========================================================================


# ---------------------------------------------------------------------------
# (Runde 3 / 1) Resume-State-Dateiname pro absolutem Quellpfad scopen (:179)
# ---------------------------------------------------------------------------


async def test_resume_state_file_scoped_per_source_path(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Legacy-DBs mit GLEICHEM Basename (obs_ringbuffer.db) in verschiedenen
    # Ordnern, in denselben Store migriert. Ohne per-Quellpfad-Scoping des
    # State-Dateinamens teilen sie sich dieselbe legacy_migration_<name>.json:
    # nach done=true der ersten Quelle liefert migrate_chunk() der zweiten 0 zurück,
    # BEVOR eine Zeile gelesen wird → die zweite Historie wird still übersprungen.
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    db_a = dir_a / "obs_ringbuffer.db"
    db_b = dir_b / "obs_ringbuffer.db"
    _build_legacy(db_a, [("2020-01-01T00:00:00.000Z", "A0"), ("2020-01-02T00:00:00.000Z", "A1")])
    _build_legacy(db_b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    mig_a = LegacyMigrator(store, db_a)
    mig_b = LegacyMigrator(store, db_b)
    # Verschiedene Quellpfade ⇒ verschiedene State-Dateien.
    assert mig_a._state_path != mig_b._state_path

    assert await mig_a.migrate_small(batch_rows=100) == 2
    # Die zweite Quelle darf NICHT den done-State der ersten sehen.
    assert await mig_b.migrate_small(batch_rows=100) == 2

    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["A0", "A1", "B0", "B1"]


async def test_resume_state_file_stable_across_migrator_instances(store: SqliteSegmentStore, tmp_path: Path):
    # Der per-Pfad-gescopte State-Dateiname muss über Migrator-Neuinstanzen
    # derselben Quelle STABIL sein (Resume nach Prozess-Neustart), sonst würde ein
    # Neustart den Fortschritt verlieren bzw. neu importieren.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "x")])
    assert LegacyMigrator(store, db)._state_path == LegacyMigrator(store, db)._state_path


# ---------------------------------------------------------------------------
# (Runde 3 / 2) Multi-Source-Ordnung: rein-migrierte Segmente konsistent hinter
# positiven v2-Zeilen und untereinander deterministisch iterieren (:191)
# ---------------------------------------------------------------------------


async def test_multi_source_migrated_segments_marked_migrated(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei Quellen werden – vor JEDEM positiven v2-Write – in SEPARATE Segmente
    # migriert. Ohne Fix bleiben sie als normale ``active``/``closed`` Segmente
    # stehen; der per-Source-Bucket entkoppelt die negativen gids von der
    # segment_id-Reihenfolge, sodass der ``id desc``-Frühabbruch in falscher
    # Ordnung abbrechen kann. Mit Fix werden rein-migrierte Multi-Source-Segmente
    # als ``migrated`` markiert und damit in ``list_segments_for_query`` zuletzt
    # (und untereinander stabil) iteriert.
    db_a = tmp_path / "legacy_a.db"
    db_b = tmp_path / "legacy_b.db"
    _build_legacy(db_a, [("2020-01-01T00:00:00.000Z", "A0"), ("2020-01-02T00:00:00.000Z", "A1")])
    _build_legacy(db_b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])

    assert await LegacyMigrator(store, db_a).migrate_chunk(batch_rows=100) == 2
    assert await LegacyMigrator(store, db_b).migrate_chunk(batch_rows=100) == 2

    # Sobald mehr als eine Quelle migriert wurde, müssen die migrierten Segmente MIT
    # Zeilen als ``migrated`` markiert sein (konsistente Iterationsreihenfolge). Das
    # nach der Rotation verbleibende leere aktive Segment bleibt ``active`` (kein Inhalt).
    segments = await store.manifest.list_segments()
    v2_with_rows = [s for s in segments if s.schema_version > 1 and s.row_count > 0]
    assert v2_with_rows, "keine befüllten v2-Segmente"
    assert all(s.status == "migrated" for s in v2_with_rows), f"nicht alle migriert: {[(s.status, s.row_count) for s in v2_with_rows]}"

    # Alle Zeilen bleiben genau einmal und global gid-konsistent lesbar. Mit
    # begrenztem limit darf der Frühabbruch keine Zeile fälschlich weglassen.
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["A0", "A1", "B0", "B1"]


async def test_multi_source_bounded_query_does_not_drop_rows(store: SqliteSegmentStore, tmp_path: Path):
    # Bounded id-desc-Query über zwei separat migrierte Quellen: der Frühabbruch
    # (allow_early_termination) darf nicht in falscher Segmentreihenfolge stoppen
    # und dadurch Zeilen der zweiten Quelle im Fenster verlieren.
    db_a = tmp_path / "legacy_a.db"
    db_b = tmp_path / "legacy_b.db"
    _build_legacy(db_a, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"A{i}") for i in range(3)])
    _build_legacy(db_b, [(f"2021-01-{i + 1:02d}T00:00:00.000Z", f"B{i}") for i in range(3)])
    assert await LegacyMigrator(store, db_a).migrate_chunk(batch_rows=100) == 3
    assert await LegacyMigrator(store, db_b).migrate_chunk(batch_rows=100) == 3

    # Die gesamte migrierte Menge ist über eine gebundene Query vollständig und
    # deterministisch (gid-sortiert) abrufbar.
    all_desc = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    gids = [r["global_event_id"] for r in all_desc]
    assert gids == sorted(gids, reverse=True), "gids nicht global absteigend sortiert"
    assert sorted(r["new_value"] for r in all_desc) == ["A0", "A1", "A2", "B0", "B1", "B2"]


async def test_second_source_retro_marks_first_unmarked_migrated_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Die ERSTE Quelle wird rein legacy-migriert (keine Positive, eine Quelle) und ihr
    # Segment bleibt bewusst UNMARKIERT (``active``). Kommt eine ZWEITE Quelle dazu,
    # muss das Segment der ersten Quelle nachträglich als ``migrated`` markiert werden
    # (``_mark_foreign_migrated_segments``), damit ALLE migrierten Segmente gemeinsam
    # im Trailing-Rang liegen und der id-desc-Frühabbruch konsistent bleibt.
    db_a = tmp_path / "legacy_a.db"
    _build_legacy(db_a, [("2020-01-01T00:00:00.000Z", "A0"), ("2020-01-02T00:00:00.000Z", "A1")])
    assert await LegacyMigrator(store, db_a).migrate_chunk(batch_rows=100) == 2
    # Nach der Ein-Quell-Migration liegt genau ein befülltes, UNMARKIERTES Segment vor.
    a_filled = [s for s in await store.manifest.list_segments() if s.schema_version > 1 and s.row_count > 0]
    assert len(a_filled) == 1 and a_filled[0].status == "active"

    db_b = tmp_path / "legacy_b.db"
    _build_legacy(db_b, [("2021-01-01T00:00:00.000Z", "B0"), ("2021-01-02T00:00:00.000Z", "B1")])
    assert await LegacyMigrator(store, db_b).migrate_chunk(batch_rows=100) == 2

    filled = [s for s in await store.manifest.list_segments() if s.schema_version > 1 and s.row_count > 0]
    assert all(s.status == "migrated" for s in filled), f"nicht alle migriert: {[(s.status, s.row_count) for s in filled]}"
    rows = await store.query(StoreQuery(limit=100))
    assert sorted(r["new_value"] for r in rows) == ["A0", "A1", "B0", "B1"]


async def test_single_source_migrated_only_store_still_needs_no_marker(store: SqliteSegmentStore, tmp_path: Path):
    # Regressionsschutz: EINE Quelle, rein legacy-migriert, KEINE positiven Zeilen:
    # die segment_id-Ordnung stimmt bereits mit der gid-Ordnung überein – kein
    # ``migrated``-Marker/Extra-Segment nötig (Ein-Segment-Fall bleibt schlank).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "a"), ("2020-01-02T00:00:00.000Z", "b")])
    assert await LegacyMigrator(store, db).migrate_chunk(batch_rows=100) == 2
    v2 = [s for s in await store.manifest.list_segments() if s.schema_version > 1]
    assert len(v2) == 1
    assert v2[0].status == "active"
    rows = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in rows] == ["b", "a"]


# ---------------------------------------------------------------------------
# (Runde 3 / 3) Aktuell migrierte Quell-Legacy-DB vor Migrations-Retention schützen (:407)
# ---------------------------------------------------------------------------


async def test_migration_does_not_delete_source_via_retention(tmp_path: Path):
    # Store über dem Byte-Budget, Quelle read-only attached, mehrere Chunk-Batches.
    # Ohne Fix wählt die Size-Retention nach dem ersten Batch (jetzt existiert eine
    # nicht-Legacy-Datenquelle) das Legacy-Segment ZUERST und ``_delete_segment``
    # löscht die ORIGINAL-Quelldatei, bevor die Migration fertig ist → spätere
    # Chunks lesen nichts mehr (Datenverlust). Mit Fix bleibt die in Migration
    # befindliche Quelle bis zum Abschluss vor Retention geschützt.
    store = SqliteSegmentStore(
        tmp_path / "root",
        segments=SegmentConfig(segment_max_rows=2),
        # Budget so gewählt, dass der Store WÄHREND der Migration (Quelle attached +
        # wachsende v2-Segmente) über dem Budget liegt – ohne Fix würde die
        # Size-Retention dann die (älteste) Quelle als Victim wählen und löschen.
        retention=StoreRetentionConfig(max_file_size_bytes=200 * 1024),
    )
    await store.open()
    try:
        db = tmp_path / "obs_ringbuffer.db"
        _build_legacy(db, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"L{i}") for i in range(6)])

        migrator = LegacyMigrator(store, db)
        # Normaler Upgrade: Quelle zuerst read-only einhängen.
        await migrator.attach_readonly(migrator.classify())
        assert len(await store.manifest.list_legacy_segments()) == 1

        # Chunkweise migrieren (mehrere Batches bei batch_rows=2).
        total = 0
        for _ in range(10):
            got = await migrator.migrate_chunk(batch_rows=2)
            total += got
            # Solange die Migration läuft, DARF die Quelldatei nicht gelöscht werden.
            assert db.exists(), "Quell-Legacy-DB wurde während der Migration von Retention gelöscht"
            if got == 0:
                break
        # Alle 6 Zeilen wurden vollständig migriert (kein Chunk lief ins Leere, weil
        # die Quelle mitten in der Migration weggelöscht worden wäre).
        assert total == 6
        # Die Quelle ist nach Abschluss abgekoppelt; die JÜNGSTEN migrierten Zeilen
        # bleiben nach der (nun nachgezogenen) Retention erhalten (FIFO über v2).
        assert await store.manifest.list_legacy_segments() == []
        rows = await store.query(StoreQuery(limit=100))
        remaining = {r["new_value"] for r in rows}
        assert "L5" in remaining, "jüngste migrierte Zeile fehlt"
    finally:
        await store.close()


async def test_migration_retention_reclaims_after_completion(tmp_path: Path):
    # Nach Abschluss (Abkopplung der Quelle) darf Retention wieder ganz normal
    # greifen: der Legacy-Manifest-Eintrag ist abgekoppelt und über dem Byte-Budget
    # werden migrierte v2-Segmente FIFO gedroppt.
    store = SqliteSegmentStore(
        tmp_path / "root",
        segments=SegmentConfig(segment_max_rows=2),
        retention=StoreRetentionConfig(max_file_size_bytes=200 * 1024),
    )
    await store.open()
    try:
        db = tmp_path / "obs_ringbuffer.db"
        _build_legacy(db, [(f"2020-01-{i + 1:02d}T00:00:00.000Z", f"L{i}") for i in range(6)])
        migrator = LegacyMigrator(store, db)
        await migrator.attach_readonly(migrator.classify())

        total = await migrator.migrate_small(batch_rows=2)
        assert total == 6
        # Legacy-Manifest-Eintrag abgekoppelt (kein Doppel-Delivery).
        assert await store.manifest.list_legacy_segments() == []
        # Über dem Byte-Budget hat Retention nach Abkopplung migrierte v2-Segmente
        # FIFO reduziert; die jüngsten Zeilen bleiben erhalten.
        rows = await store.query(StoreQuery(limit=100))
        assert len(rows) < 6
        assert "L5" in {r["new_value"] for r in rows}
    finally:
        await store.close()
