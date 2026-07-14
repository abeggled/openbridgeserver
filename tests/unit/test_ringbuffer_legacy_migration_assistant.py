"""Migrations-Assistent Phase 2 (#964): Decision-State, Retention-Guard, Overview/Discard.

Drei Ebenen:

* **Decision-Persistenz** (``persisted_config``): ``ensure_legacy_migration_decision``
  setzt ``pending`` genau dann, wenn eine Legacy-DB liegt und noch nie entschieden
  wurde; unbekannte Werte lesen sich konservativ als ``None``.
* **Retention-Guard** (``StoreRetentionConfig.protect_legacy``): solange keine
  informierte Entscheidung vorliegt, ist das attachte Legacy-Segment KEIN
  FIFO-Opfer – auch unter hartem Budget-Druck; das Aufheben des Schutzes gibt es
  wieder frei (Alles-oder-nichts-Verhalten wie dokumentiert).
* **RingBuffer-Helfer**: ``legacy_migration_overview`` (billige Ist-Analyse ohne
  Vollscan), ``discard_legacy`` (Manifest-Zeile + Dateien weg),
  ``set_legacy_retention_protected`` (Guard live umschaltbar).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.db.database import Database
from obs.ringbuffer.persisted_config import (
    LEGACY_DECISION_DISCARDED,
    LEGACY_DECISION_KEEP,
    LEGACY_DECISION_PENDING,
    LEGACY_DECISION_SKIPPED,
    LEGACY_DECISIONS_PROTECTED,
    LEGACY_MIGRATION_DECISION_KEY,
    ensure_legacy_migration_decision,
    load_legacy_migration_decision,
    persist_legacy_migration_decision,
)
from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery


# ---------------------------------------------------------------------------
# Decision-Persistenz
# ---------------------------------------------------------------------------


async def _memory_db() -> Database:
    db = Database(":memory:")
    await db.connect()
    await db.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_decision_defaults_to_none():
    db = await _memory_db()
    try:
        assert await load_legacy_migration_decision(db) is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_ensure_sets_pending_only_with_legacy_file(tmp_path: Path):
    db = await _memory_db()
    try:
        # Keine Legacy-Datei → kein Zustand (Fresh Install).
        missing = tmp_path / "obs_ringbuffer.db"
        assert await ensure_legacy_migration_decision(db, legacy_db_path=str(missing)) is None
        assert await load_legacy_migration_decision(db) is None

        # Legacy-Datei vorhanden → pending wird persistiert.
        missing.write_bytes(b"x" * 128)
        assert await ensure_legacy_migration_decision(db, legacy_db_path=str(missing)) == LEGACY_DECISION_PENDING
        assert await load_legacy_migration_decision(db) == LEGACY_DECISION_PENDING

        # Bereits entschieden → ensure überschreibt NICHT.
        await persist_legacy_migration_decision(db, LEGACY_DECISION_KEEP)
        assert await ensure_legacy_migration_decision(db, legacy_db_path=str(missing)) == LEGACY_DECISION_KEEP
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_ensure_ignores_memory_path_and_none():
    db = await _memory_db()
    try:
        assert await ensure_legacy_migration_decision(db, legacy_db_path=None) is None
        assert await ensure_legacy_migration_decision(db, legacy_db_path=":memory:") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_unknown_persisted_value_reads_as_none():
    db = await _memory_db()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (LEGACY_MIGRATION_DECISION_KEY, "kaputt"),
        )
        await db.commit()
        assert await load_legacy_migration_decision(db) is None
    finally:
        await db.disconnect()


def test_protected_states_exclude_informed_decisions():
    # pending/skipped schützen; keep/migrated/discarded nicht (informierte bzw.
    # terminale Zustände).
    assert LEGACY_DECISION_PENDING in LEGACY_DECISIONS_PROTECTED
    assert LEGACY_DECISION_SKIPPED in LEGACY_DECISIONS_PROTECTED
    assert LEGACY_DECISION_KEEP not in LEGACY_DECISIONS_PROTECTED
    assert LEGACY_DECISION_DISCARDED not in LEGACY_DECISIONS_PROTECTED


# ---------------------------------------------------------------------------
# Retention-Guard auf Store-Ebene
# ---------------------------------------------------------------------------


def _iso(i: int) -> str:
    return f"2026-01-01T00:00:{i:02d}.000Z"


def _event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-1",
        topic="dp/dp-1/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


async def _seed_legacy_db(path: Path, values: list[int]) -> None:
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, value in enumerate(values):
            await rb.record(
                ts=_iso(i),
                datapoint_id="dp-leg",
                topic="dp/dp-leg/value",
                old_value=None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_protect_legacy_blocks_fifo_reclaim_until_lifted(tmp_path: Path):
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        # v2-Datenquelle sichern (No-Zero-History-Guard erfüllt) …
        await store.append([_event(1, _iso(0))])
        await store.rotate()
        await store.append([_event(2, _iso(1))])
        # … und eine echte Legacy-DB attachen.
        legacy = tmp_path / "obs_ringbuffer.db"
        await _seed_legacy_db(legacy, [10, 11])
        migrator = LegacyMigrator(store, legacy)
        await migrator.attach_readonly(migrator.classify())

        # Hartes Über-Budget + Schutz aktiv → Legacy ist KEIN Opfer.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1, protect_legacy=True)
        await store.enforce_retention()
        assert len(await store.manifest.list_legacy_segments()) == 1, "geschützte Legacy darf nicht reclaimed werden"
        assert legacy.exists()

        # Schutz aufheben (Entscheidung ``keep``/``discard``) → FIFO greift wieder.
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=1, protect_legacy=False)
        removed = await store.enforce_retention()
        assert removed >= 1
        assert await store.manifest.list_legacy_segments() == []
        assert not legacy.exists()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stats_estimate_attached_legacy_rows_and_span(tmp_path: Path):
    """/stats zaehlt attachte Legacy-Events per Punkt-Lookup-Schaetzung mit (#964-Follow-up).

    Das Manifest traegt fuer attachte Legacy-Segmente bewusst row_count 0 (kein
    Attach-Scan). Ohne Anreicherung meldete das Dashboard "0 Eintraege", obwohl
    zigtausend Alt-Events abfragbar sind. Die Stats schaetzen jetzt lazy+gecacht
    ueber MAX(rowid) und liefern auch die ts-Spanne der Alt-Historie.
    """
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        legacy = tmp_path / "obs_ringbuffer.db"
        await _seed_legacy_db(legacy, [10, 11, 12, 13])
        migrator = LegacyMigrator(store, legacy)
        await migrator.attach_readonly(migrator.classify())
        await store.append([_event(1, _iso(50))])

        stats = await store.stats()
        assert stats.common["total"] == 5, "4 Legacy-Events (geschaetzt) + 1 Live-Event"
        assert stats.common["oldest_ts"] == _iso(0), "aelteste ts kommt aus der Legacy-Historie"
        assert stats.common["newest_ts"] == _iso(50)

        # Cache greift: zweiter Aufruf identisch (und guenstig).
        stats2 = await store.stats()
        assert stats2.common["total"] == 5
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_protected_legacy_does_not_sacrifice_live_segments(tmp_path: Path):
    """Budget-Druck durch GESCHÜTZTE Legacy darf keine Live-Segmente opfern (#964-Fix).

    Konstellation aus dem Demo-Betrieb: 10-MiB-Alt-Budget, 76-MB-Legacy attached
    und geschützt (pending). Der Store ist damit dauerhaft über Budget – aber der
    Überschuss STAMMT aus der tolerierten Legacy. Ohne Korrektur fraß jeder
    Retention-Pass das einzige ungeschützte Opfer: das jüngste geschlossene
    Live-Segment (im Feldversuch: 712 frische Events weg). Solange der Schutz
    aktiv ist, muss der Legacy-Anteil aus dem Size-Druck herausgerechnet werden.
    """
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        legacy = tmp_path / "obs_ringbuffer.db"
        # Groß genug, dass die Legacy den SQLite-Fixkosten-Overhead der
        # Live-Segmente klar dominiert (Feld-Konstellation: 76 MB vs. 10 MiB).
        await _seed_legacy_db(legacy, list(range(3000)))
        migrator = LegacyMigrator(store, legacy)
        await migrator.attach_readonly(migrator.classify())
        legacy_size = (await store.manifest.list_legacy_segments())[0].size_bytes

        # Ein geschlossenes + ein aktives Live-Segment mit frischen Daten.
        await store.append([_event(1, _iso(50))])
        await store.rotate()
        await store.append([_event(2, _iso(51))])

        # Budget: größer als der Live-Anteil, aber klein gegen die Legacy –
        # über Budget also NUR wegen der (geschützten) Legacy.
        budget = max(legacy_size // 3, 256 * 1024)
        assert budget < legacy_size
        store._retention_config = StoreRetentionConfig(max_file_size_bytes=budget, protect_legacy=True)
        removed = await store.enforce_retention()

        assert removed == 0, "geschuetzte Ueber-Budget-Lage darf keine Live-Segmente kosten"
        rows = await store.query(StoreQuery(limit=10))
        assert {r["new_value"] for r in rows} >= {1, 2}, "frische Live-Events muessen ueberleben"
        assert len(await store.manifest.list_legacy_segments()) == 1
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# RingBuffer-Helfer: Overview, Discard, Live-Umschaltung
# ---------------------------------------------------------------------------


def _segmented_rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        max_entries=None,
        segmented=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_overview_reports_attached_legacy_cheaply(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, [10, 11, 12])

    rb = _segmented_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        overview = await rb.legacy_migration_overview()
        assert overview is not None
        assert overview["size_bytes"] > 0
        assert overview["row_estimate"] == 3
        assert overview["from_ts"] == _iso(0)
        assert overview["to_ts"] == _iso(2)
        assert overview["retention_protected"] is True
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_overview_none_without_legacy(tmp_path: Path):
    rb = _segmented_rb(tmp_path)
    await rb.start()
    try:
        assert await rb.legacy_migration_overview() is None
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_discard_legacy_removes_manifest_row_and_files(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, [10, 11])

    rb = _segmented_rb(tmp_path, legacy_retention_protected=True)
    await rb.start()
    try:
        assert (await rb.legacy_migration_overview()) is not None
        result = await rb.discard_legacy()
        assert result["removed_segments"] == 1
        assert result["freed_bytes"] > 0
        assert not legacy.exists()
        assert await rb.legacy_migration_overview() is None
        # Neue Events laufen unverändert weiter.
        await rb.record(
            ts=_iso(30),
            datapoint_id="dp-1",
            topic="dp/dp-1/value",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
        )
        entries = await rb.query_v2(limit=10)
        assert [e.new_value for e in entries] == [1]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_set_legacy_retention_protected_switches_live(tmp_path: Path):
    legacy = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy_db(legacy, [10, 11])

    rb = _segmented_rb(tmp_path, legacy_retention_protected=True, max_file_size_bytes=None)
    await rb.start()
    try:
        # v2-Daten sichern, damit der No-Zero-History-Guard erfüllt ist.
        await rb.record(
            ts=_iso(40),
            datapoint_id="dp-1",
            topic="dp/dp-1/value",
            old_value=None,
            new_value=1,
            source_adapter="api",
            quality="good",
        )
        store = rb._store
        assert store is not None
        assert store._retention_config.protect_legacy is True

        # Budget hart drücken: geschützt bleibt Legacy trotzdem attached.
        from obs.ringbuffer.store.config import StoreRetentionConfig as _RC

        store.apply_config(retention=_RC(max_file_size_bytes=1, protect_legacy=True))
        await store.enforce_retention()
        assert len(await store.manifest.list_legacy_segments()) == 1

        # Live-Umschaltung über den RingBuffer (Entscheidung ``keep``).
        await rb.set_legacy_retention_protected(False)
        assert store._retention_config.protect_legacy is False
        # Budget-Deckel blieb beim Umschalten erhalten (Config wird kopiert).
        assert store._retention_config.max_file_size_bytes == 1
        await store.enforce_retention()
        assert await store.manifest.list_legacy_segments() == []
    finally:
        await rb.stop()
