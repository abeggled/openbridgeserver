"""Legacy-Single-DB-Migration und -Kompatibilität (#919/#934).

Deckt ab:

* (a) Legacy-Single-DB bleibt lesbar (Kompatibilität) — read-only eingehängt.
* (b) kleine Migration kopiert korrekt in v2-Segmente.
* (c) große Legacy-Datei read-only als Legacy-Segment OHNE Vollscan.
* (d) Query über gemischt Legacy(v1)+v2-Segmente liefert geordnete Ergebnisse.
* (e) Value-Filter über Legacy-Segment fällt bounded zurück statt zu brechen.
* (f) dirty-WAL-Legacy wird nicht im Startup gecheckpointet.

Die Legacy-DB wird jeweils mit dem echten Legacy-``RingBuffer`` im ALTEN Format
befüllt (Tabelle ``ringbuffer`` ohne global_event_id/typisierte Spalten).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import aiosqlite

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import (
    LEGACY_SCHEMA_VERSION,
    SEGMENT_STATUS_LEGACY,
)
from obs.ringbuffer.store.migration import (
    LARGE_MIN_BYTES,
    SMALL_MAX_BYTES,
    LegacyClass,
    LegacyMigrator,
    classify_legacy_db,
)
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


async def _build_legacy_db(path: Path, values: list[int], *, base_ts: str = "2025-01-01T00:00:0") -> None:
    """Befüllt eine echte Legacy-``ringbuffer.db`` im ALTEN Format über RingBuffer."""
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, value in enumerate(values):
            await rb.record(
                ts=f"{base_ts}{i}.000Z",
                datapoint_id="dp-legacy",
                topic="dp/dp-legacy/value",
                old_value=None,
                new_value=value,
                source_adapter="legacy",
                quality="good",
                metadata={"datapoint": {"tags": ["legacy"]}},
            )
    finally:
        await rb.stop()


def _v2_event(value: int, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-new",
        topic="dp/dp-new/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
        metadata_version=1,
        metadata={},
    )


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Klassifikation (größenbasiert, ohne Vollscan)
# ---------------------------------------------------------------------------


def test_classify_missing_file_returns_none(tmp_path: Path):
    assert classify_legacy_db(tmp_path / "nope.db") is None


async def test_classify_small_db(tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3])
    c = classify_legacy_db(db)
    assert c is not None
    assert c.klass is LegacyClass.SMALL
    assert c.size_bytes < SMALL_MAX_BYTES
    assert c.dirty_wal is False


def test_classify_large_db_by_size_without_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "big.db"
    db.write_bytes(b"\x00")  # winzige Datei; Größe wird gemockt, DB wird NIE geöffnet
    import obs.ringbuffer.store.migration as mig

    real_stat = Path.stat

    def fake_stat(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self == db:
            return type("S", (), {"st_size": LARGE_MIN_BYTES + 1})()
        return st

    monkeypatch.setattr(mig.Path, "stat", fake_stat)
    c = mig.classify_legacy_db(db)
    assert c is not None
    assert c.klass is LegacyClass.LARGE


def test_classify_medium_db_by_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "med.db"
    db.write_bytes(b"\x00")
    import obs.ringbuffer.store.migration as mig

    real_stat = Path.stat

    def fake_stat(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self == db:
            return type("S", (), {"st_size": SMALL_MAX_BYTES + 1})()
        return st

    monkeypatch.setattr(mig.Path, "stat", fake_stat)
    c = mig.classify_legacy_db(db)
    assert c is not None
    assert c.klass is LegacyClass.MEDIUM


def test_wal_dirty_check_swallows_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import obs.ringbuffer.store.migration as mig

    def boom(self, *a, **k):
        raise OSError("stat failed")

    monkeypatch.setattr(mig.Path, "stat", boom)
    # exists() ist True, stat() wirft → _wal_is_dirty muss False liefern.
    monkeypatch.setattr(mig.Path, "exists", lambda self: True)
    assert mig._wal_is_dirty(tmp_path / "obs_ringbuffer.db") is False


async def test_migrate_chunk_on_empty_legacy_db_is_immediately_done(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [])  # leere Legacy-DB (Schema, keine Zeilen)
    migrator = LegacyMigrator(store, db)
    assert await migrator.migrate_chunk() == 0
    # Zweiter Aufruf bleibt 0 (done persistiert), keine Fehler.
    assert await migrator.migrate_chunk() == 0


# ---------------------------------------------------------------------------
# (a) + (c) read-only Einhängen ohne Scan
# ---------------------------------------------------------------------------


async def test_attach_readonly_registers_legacy_segment(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [10, 20, 30])
    migrator = LegacyMigrator(store, db)
    classification = migrator.classify()
    record = await migrator.attach_readonly(classification)

    assert record.status == SEGMENT_STATUS_LEGACY
    assert record.schema_version == LEGACY_SCHEMA_VERSION
    assert record.filename == str(db.resolve())
    legacy = await store.manifest.list_legacy_segments()
    assert [s.segment_id for s in legacy] == [record.segment_id]


async def test_legacy_single_db_stays_readable_after_attach(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [10, 20, 30])
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {10, 20, 30}
    # Legacy-Zeilen tragen synthetische, streng negative global_event_ids.
    assert all(r["global_event_id"] < 0 for r in rows)


# ---------------------------------------------------------------------------
# (b) kleine Migration kopiert in v2-Segmente
# ---------------------------------------------------------------------------


async def test_migrate_small_copies_into_v2_segments(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3, 4, 5])
    migrator = LegacyMigrator(store, db)
    copied = await migrator.migrate_small(batch_rows=2)
    assert copied == 5

    rows = await store.query(StoreQuery(limit=50))
    assert {r["new_value"] for r in rows} == {1, 2, 3, 4, 5}
    # In v2 kopiert, aber mit synthetischen NEGATIVEN gids (Legacy-Ordnung bewahrt,
    # #951 Pkt 2): migrierte Historie sortiert hinter echten neueren v2-Events. Die
    # typisierten Wertspalten werden trotzdem befüllt → Value-Pushdown greift weiter.
    assert all(r["global_event_id"] < 0 for r in rows)
    filtered = await store.query(StoreQuery(limit=50, value_filters=[{"operator": "gte", "value": 3}]))
    assert {r["new_value"] for r in filtered} == {3, 4, 5}


async def test_migrate_chunk_is_resumable(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3, 4, 5])
    migrator = LegacyMigrator(store, db)
    first = await migrator.migrate_chunk(batch_rows=2)
    assert first == 2
    second = await migrator.migrate_chunk(batch_rows=2)
    assert second == 2
    third = await migrator.migrate_chunk(batch_rows=2)
    assert third == 1
    fourth = await migrator.migrate_chunk(batch_rows=2)
    assert fourth == 0  # fertig, keine Duplikate

    rows = await store.query(StoreQuery(limit=50))
    values = sorted(r["new_value"] for r in rows)
    assert values == [1, 2, 3, 4, 5]  # jede Zeile genau einmal


# ---------------------------------------------------------------------------
# (d) gemischte Legacy(v1)+v2-Query, geordnet
# ---------------------------------------------------------------------------


async def test_mixed_legacy_and_v2_query_is_ordered(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [100, 200])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())
    # Neue v2-Writes nach Aktivierung.
    await store.append([_v2_event(1, "2026-06-01T00:00:00.000Z"), _v2_event(2, "2026-06-01T00:00:01.000Z")])

    rows = await store.query(StoreQuery(limit=10))
    assert len(rows) == 4
    gids = [r["global_event_id"] for r in rows]
    assert gids == sorted(gids, reverse=True)  # newest-first, stabil gemergt
    # v2 (positive gid) sortiert vor Legacy (negative gid) → neuer.
    top_two = {r["new_value"] for r in rows[:2]}
    assert top_two == {1, 2}
    bottom_two = {r["new_value"] for r in rows[2:]}
    assert bottom_two == {100, 200}


async def test_legacy_query_applies_structural_filters(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3, 4])  # ts = 2025-01-01T00:00:00..03
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    rows = await store.query(
        StoreQuery(
            from_ts="2025-01-01T00:00:01.000Z",
            to_ts="2025-01-01T00:00:02.000Z",
            datapoint_id="dp-legacy",
            source_adapter="legacy",
            quality="good",
            limit=10,
        )
    )
    assert {r["new_value"] for r in rows} == {2, 3}


async def test_legacy_sort_ts_asc_returns_true_oldest_beyond_fetch_window(store: SqliteSegmentStore, tmp_path: Path):
    """Regression: ``sort=ts asc`` über einem Legacy-Segment, das größer als das
    Fetch-Fenster ist, muss die ECHTEN ältesten Zeilen liefern — nicht die neuesten.

    Der Bug (hartkodiertes ``ORDER BY ts DESC`` im Legacy-Fetch) zeigte sich erst
    auf echten Daten (119k Zeilen vs. Kandidaten-Cap). Hier reproduziert ihn schon
    ``limit=1``: ``fetch_limit`` = ``offset+limit`` = 1 < Zeilenzahl, sodass der
    Fetch bei falscher Richtung die echte älteste Zeile ausschließt.
    """
    db = tmp_path / "obs_ringbuffer.db"
    # ts steigt mit dem Index: 10 @ ...00 (ältester) .. 50 @ ...04 (neuester).
    await _build_legacy_db(db, [10, 20, 30, 40, 50])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    oldest = await store.query(StoreQuery(limit=1, sort_field="ts", sort_order="asc"))
    assert [r["new_value"] for r in oldest] == [10]  # echte älteste, nicht 50

    newest = await store.query(StoreQuery(limit=1, sort_field="ts", sort_order="desc"))
    assert [r["new_value"] for r in newest] == [50]

    first_two = await store.query(StoreQuery(limit=2, sort_field="ts", sort_order="asc"))
    assert [r["new_value"] for r in first_two] == [10, 20]


# ---------------------------------------------------------------------------
# (e) Value-Filter über Legacy: bounded Fallback statt Bruch
# ---------------------------------------------------------------------------


async def test_value_filter_over_legacy_segment_falls_back(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [5, 15, 25, 35])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # Einfacher typisierter Filter degradiert auf Python-Auswertung über JSON-Werte.
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "value": 15}]))
    assert {r["new_value"] for r in rows} == {25, 35}


async def test_bounded_contains_over_legacy_with_candidate_cap(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    rb = RingBuffer(storage="disk", disk_path=str(db), max_entries=None)
    await rb.start()
    try:
        await rb.record(
            ts="2025-01-01T00:00:00.000Z", datapoint_id="d", topic="t", old_value=None, new_value="alpha", source_adapter="legacy", quality="good"
        )
        await rb.record(
            ts="2025-01-01T00:00:01.000Z", datapoint_id="d", topic="t", old_value=None, new_value="beta", source_adapter="legacy", quality="good"
        )
    finally:
        await rb.stop()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    rows = await store.query(StoreQuery(limit=10, candidate_cap=1000, value_filters=[{"operator": "contains", "value": "lph"}]))
    assert {r["new_value"] for r in rows} == {"alpha"}


# ---------------------------------------------------------------------------
# (f) dirty-WAL-Legacy: kein Startup-Checkpoint
# ---------------------------------------------------------------------------


async def test_dirty_wal_legacy_is_flagged_and_not_checkpointed(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3])
    # Ein nicht-leeres -wal simuliert eine dirty WAL, ohne die DB zu öffnen.
    wal = Path(f"{db}-wal")
    wal.write_bytes(b"\x00" * 4096)

    migrator = LegacyMigrator(store, db)
    classification = migrator.classify()
    assert classification.dirty_wal is True

    record = await migrator.attach_readonly(classification)
    assert record.recovery_status == "dirty_wal"
    # Das -wal wurde NICHT abgearbeitet/getruncatet (kein Checkpoint im Startup).
    assert wal.exists() and wal.stat().st_size == 4096
    # Über den read-only immutable-Pfad bleiben die committeten Daten lesbar.
    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {1, 2, 3}


# ---------------------------------------------------------------------------
# Legacy-Value-Filter-Evaluator (Python-Fallback, spiegelt v2-Semantik)
# ---------------------------------------------------------------------------


def _rec(new_value):
    return {"new_value": new_value, "old_value": None}


def test_legacy_filter_between_matches_and_validates():
    from obs.ringbuffer.store.sqlite_backend import _legacy_filter_matches

    assert _legacy_filter_matches(_rec(5), {"operator": "between", "lower": 1, "upper": 10}) is True
    assert _legacy_filter_matches(_rec(50), {"operator": "between", "lower": 1, "upper": 10}) is False
    assert _legacy_filter_matches(_rec("x"), {"operator": "between", "lower": 1, "upper": 10}) is False
    with pytest.raises(ValueError, match="numeric lower/upper"):
        _legacy_filter_matches(_rec(5), {"operator": "between", "lower": "a", "upper": 10})
    with pytest.raises(ValueError, match="lower must be <= upper"):
        _legacy_filter_matches(_rec(5), {"operator": "between", "lower": 10, "upper": 1})


def test_legacy_filter_rejects_invalid_operator_and_field():
    from obs.ringbuffer.store.sqlite_backend import _legacy_filter_matches

    with pytest.raises(ValueError, match="invalid value filter operator"):
        _legacy_filter_matches(_rec(1), {"operator": "bogus", "value": 1})
    with pytest.raises(ValueError, match="invalid value filter field"):
        _legacy_filter_matches(_rec(1), {"operator": "eq", "field": "bogus", "value": 1})


def test_legacy_compare_is_type_true_and_ne_semantics():
    from obs.ringbuffer.store.sqlite_backend import _legacy_filter_matches

    # eq/ne/lt/lte über typgleiche Werte
    assert _legacy_filter_matches(_rec(3), {"operator": "eq", "value": 3}) is True
    assert _legacy_filter_matches(_rec(3), {"operator": "ne", "value": 4}) is True
    assert _legacy_filter_matches(_rec(2), {"operator": "lt", "value": 3}) is True
    assert _legacy_filter_matches(_rec(3), {"operator": "lte", "value": 3}) is True
    assert _legacy_filter_matches(_rec(4), {"operator": "gt", "value": 3}) is True
    assert _legacy_filter_matches(_rec(3), {"operator": "gte", "value": 3}) is True
    assert _legacy_filter_matches(_rec("b"), {"operator": "eq", "value": "b"}) is True
    assert _legacy_filter_matches(_rec(True), {"operator": "eq", "value": True}) is True
    # Cross-Typ: matcht nie bei eq, aber ne ist True.
    assert _legacy_filter_matches(_rec("x"), {"operator": "eq", "value": 3}) is False
    assert _legacy_filter_matches(_rec("x"), {"operator": "ne", "value": 3}) is True
    assert _legacy_filter_matches(_rec(3), {"operator": "ne", "value": True}) is True
    assert _legacy_filter_matches(_rec(3), {"operator": "ne", "value": "s"}) is True


def test_legacy_filter_contains_and_regex():
    from obs.ringbuffer.store.sqlite_backend import _legacy_filter_matches

    assert _legacy_filter_matches(_rec("Alpha"), {"operator": "contains", "value": "lph", "ignore_case": True}) is True
    assert _legacy_filter_matches(_rec(123), {"operator": "contains", "value": "1"}) is False
    with pytest.raises(ValueError, match="contains requires a string"):
        _legacy_filter_matches(_rec("x"), {"operator": "contains", "value": 1})
    assert _legacy_filter_matches(_rec("abc123"), {"operator": "regex", "pattern": r"\d+"}) is True
    assert _legacy_filter_matches(_rec(5), {"operator": "regex", "pattern": r"\d+"}) is False
    with pytest.raises(ValueError, match="non-empty pattern"):
        _legacy_filter_matches(_rec("x"), {"operator": "regex", "pattern": ""})
    with pytest.raises(ValueError, match="pattern too long"):
        _legacy_filter_matches(_rec("x"), {"operator": "regex", "pattern": "a" * 300})
    with pytest.raises(ValueError, match="nested quantifiers"):
        _legacy_filter_matches(_rec("x"), {"operator": "regex", "pattern": "(a+)+"})


async def test_error_never_deletes_legacy_db(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3])
    migrator = LegacyMigrator(store, db)
    # Kaputter Batch-Reader → Migration schlägt fehl, Legacy bleibt erhalten.

    async def boom(**_kwargs):
        raise aiosqlite.DatabaseError("simulated read failure")

    migrator._read_batch = boom  # type: ignore[method-assign]
    with pytest.raises(aiosqlite.DatabaseError):
        await migrator.migrate_chunk()
    assert db.exists()
