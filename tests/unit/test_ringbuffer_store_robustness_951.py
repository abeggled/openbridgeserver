"""Robustheits-/Legacy-Fixes am segmentierten Store (#919, Review #951).

Ein Test je Review-Punkt:

1. pre-#388 Legacy-DB OHNE ``metadata_version``/``metadata``-Spalten → Query
   liefert die Alt-Historie mit Default-Metadaten, kein „no such column".
2. Segment-Datei nach ``list_segments_for_query()`` gelöscht → Query überspringt
   das Segment sauber (kein 500, keine leere Ersatz-DB).
3. retention-bedingt gelöschtes Legacy-Segment → in-place Datei (inkl. -wal/-shm)
   ist physisch weg und wird bei erneutem Attach NICHT wieder registriert.
4. kleine Legacy-DB mit dirty WAL → committete WAL-Frames werden EINMAL
   gecheckpointet und sind sichtbar; große Legacy-DB bleibt immutable/geflaggt
   ohne Startup-Checkpoint.
5. aktives Segment mit großer WAL → Manifest-``size_bytes`` zählt -wal/-shm mit,
   sodass Rotation/Budget die reale Disk-Nutzung sehen.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import SMALL_MAX_BYTES, LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


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


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# (1) pre-#388 Legacy-Schema ohne metadata-Spalten bleibt lesbar
# ---------------------------------------------------------------------------


def _build_pre388_legacy_db(path: Path, values: list[int], *, base_ts: str = "2025-01-01T00:00:0") -> None:
    """Baut eine sehr alte Single-DB OHNE ``metadata_version``/``metadata``-Spalten."""
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
                   quality        TEXT NOT NULL
               )"""
        )
        for i, value in enumerate(values):
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{base_ts}{i}.000Z", "dp-legacy", "dp/dp-legacy/value", None, str(value), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()


async def test_pre388_legacy_without_metadata_columns_is_readable(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_pre388_legacy_db(db, [10, 20, 30])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # Ohne Fix scheiterte das SELECT mit „no such column: metadata_version".
    rows = await store.query(StoreQuery(limit=10))
    assert {r["new_value"] for r in rows} == {10, 20, 30}
    # Default-Metadaten für fehlende Spalten.
    assert all(r["metadata_version"] == 1 for r in rows)
    assert all(r["metadata"] == {} for r in rows)


# ---------------------------------------------------------------------------
# (2) Segment nach Manifest-Auswahl gelöscht → sauber überspringen (kein 500)
# ---------------------------------------------------------------------------


async def test_query_skips_segment_deleted_after_manifest_selection(store: SqliteSegmentStore, monkeypatch):
    # Zwei geschlossene v2-Segmente + aktives Segment.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-02-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(3, "2026-03-01T00:00:00.000Z")])

    segments = await store.manifest.list_segments_for_query(None, None)
    # Ein geschlossenes (nicht-aktives) Segment nach der Auswahl unter dem Store
    # weglöschen — simuliert die Retention-Race zwischen list_* und Open.
    victim = next(s for s in segments if store._active_segment is not None and s.segment_id != store._active_segment.segment_id)
    (store._segments_dir / victim.filename).unlink()

    async def _stale_list(*_args, **_kwargs):
        # Query sieht das inzwischen gelöschte Segment noch in der Auswahl.
        return segments

    monkeypatch.setattr(store.manifest, "list_segments_for_query", _stale_list)

    rows = await store.query(StoreQuery(limit=10))
    values = {r["new_value"] for r in rows}
    # Das gelöschte Segment fehlt, aber die Query bricht NICHT (kein „no such table").
    assert values.issubset({1, 2, 3})
    assert victim.row_count == 1 and len(values) == 2
    # Keine leere Ersatz-DB wurde am gelöschten Pfad angelegt.
    assert not (store._segments_dir / victim.filename).exists()


# ---------------------------------------------------------------------------
# (3) retention-gelöschtes Legacy-Segment → Datei weg, kein Re-Attach
# ---------------------------------------------------------------------------


async def _attach_legacy_blob(store: SqliteSegmentStore, path: Path, size_bytes: int):
    path.write_bytes(b"\x00" * size_bytes)
    return await store.manifest.register_legacy_segment(source_path=str(path.resolve()), size_bytes=size_bytes)


async def test_retention_deleted_legacy_file_is_gone_and_not_reattached(store: SqliteSegmentStore, tmp_path: Path):
    # Ein geschlossenes v2-Segment sichert frische Historie (No-Zero-History-Guard).
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-02T00:00:00.000Z")])

    legacy_file = tmp_path / "obs_ringbuffer.db"
    legacy_wal = Path(f"{legacy_file}-wal")
    legacy_shm = Path(f"{legacy_file}-shm")
    await _attach_legacy_blob(store, legacy_file, 8 * 1024 * 1024)
    legacy_wal.write_bytes(b"\x00" * 1024)
    legacy_shm.write_bytes(b"\x00" * 1024)

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    removed = await store.enforce_retention()
    assert removed >= 1

    # Datei inkl. -wal/-shm physisch entfernt (Platz wirklich freigegeben).
    assert not legacy_file.exists()
    assert not legacy_wal.exists()
    assert not legacy_shm.exists()
    assert await store.manifest.list_legacy_segments() == []

    # Ein erneuter Attach-Versuch findet die Datei nicht mehr → keine Re-Registrierung.
    assert LegacyMigrator(store, legacy_file).classify() is None


# ---------------------------------------------------------------------------
# (4) dirty-WAL: kleine Legacy einmal checkpointen; große bleibt immutable
# ---------------------------------------------------------------------------


async def _build_legacy_with_dirty_wal(path: Path, committed: list[int], tmp_path: Path) -> None:
    """Erzeugt eine Legacy-DB mit persistentem, nicht-gecheckpointetem ``-wal``.

    SQLite checkpointet den WAL beim Schließen der letzten Connection — ein dirty
    ``-wal`` überlebt ``close()`` also normalerweise nicht. Trick: die Frames werden
    in einer Quell-DB committet (ohne Auto-Checkpoint) und die Datei + ``-wal``
    danach dateiweise an den Zielpfad kopiert, während die schreibende Connection
    noch offen ist. So bleibt der committete WAL-Frame ungemergt auf der Kopie liegen.
    """
    src = tmp_path / "src_ringbuffer.db"
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
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
        for i, value in enumerate(committed):
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"2025-01-01T00:00:0{i}.000Z", "dp-legacy", "dp/dp-legacy/value", None, str(value), "legacy", "good"),
            )
        conn.commit()
        # Schema + Basiszeilen in die Haupt-DB checkpointen: so ist die Tabelle auch
        # über den ``immutable=1``-Pfad (der den WAL ignoriert) sichtbar.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # EIN weiterer committeter Frame bleibt danach ungemergt im WAL liegen (999).
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2025-01-01T00:00:09.000Z", "dp-legacy", "dp/dp-legacy/value", None, "999", "legacy", "good"),
        )
        conn.commit()
        # Kopie ziehen, SOLANGE der WAL noch nicht gemergt ist (Connection offen).
        path.write_bytes(src.read_bytes())
        Path(f"{path}-wal").write_bytes(Path(f"{src}-wal").read_bytes())
    finally:
        conn.close()


async def test_small_dirty_wal_legacy_checkpoints_and_shows_committed_frames(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_with_dirty_wal(db, [1, 2, 3], tmp_path)
    wal = Path(f"{db}-wal")
    assert wal.exists() and wal.stat().st_size > 0  # dirty WAL vorhanden

    # Klein → dirty_wal-Flag + Checkpoint beim Read.
    classification = LegacyMigrator(store, db).classify()
    assert classification.klass.value == "small"
    assert classification.dirty_wal is True
    await LegacyMigrator(store, db).attach_readonly(classification)

    rows = await store.query(StoreQuery(limit=20))
    # Der committete WAL-Frame (999) ist nach dem Checkpoint sichtbar.
    assert 999 in {r["new_value"] for r in rows}
    assert {1, 2, 3}.issubset({r["new_value"] for r in rows})


async def test_large_dirty_wal_legacy_is_not_checkpointed(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_with_dirty_wal(db, [1, 2, 3], tmp_path)
    wal = Path(f"{db}-wal")
    wal_size_before = wal.stat().st_size

    # Größe über den SMALL-Schwellwert heben (Manifest-size_bytes), ohne die Datei
    # wirklich zu vergrößern → der Checkpoint-Pfad darf NICHT greifen.
    record = await store.manifest.register_legacy_segment(source_path=str(db.resolve()), size_bytes=SMALL_MAX_BYTES + 1, dirty_wal=True)
    assert record.recovery_status == "dirty_wal"

    # Ein Checkpoint würde den WAL truncaten; wir stellen sicher, dass er nie läuft.
    async def _fail_checkpoint(*_a, **_k):
        raise AssertionError("large legacy DB must not be checkpointed at read time")

    monkeypatch.setattr(store, "_checkpoint_small_legacy", _fail_checkpoint)

    rows = await store.query(StoreQuery(limit=20))
    # immutable=1 liest nur die committete Haupt-DB (ohne WAL-Frame 999).
    assert {1, 2, 3}.issubset({r["new_value"] for r in rows})
    # WAL blieb unangetastet (kein Checkpoint/Truncate).
    assert wal.exists() and wal.stat().st_size == wal_size_before


# ---------------------------------------------------------------------------
# (5) size_bytes zählt -wal/-shm des aktiven Segments mit
# ---------------------------------------------------------------------------


async def test_active_segment_size_includes_wal_and_shm(store: SqliteSegmentStore):
    # Viele Appends erzeugen einen nennenswerten WAL des aktiven Segments.
    for i in range(200):
        await store.append([_event(i, f"2026-01-01T00:00:{i % 60:02d}.{i:03d}Z")])

    active = store._active_segment
    assert active is not None
    seg = await store.manifest.get_segment(active.segment_id)

    base = store._segments_dir / active.filename
    main_only = base.stat().st_size
    wal_size = store._active_wal_size()
    shm_size = store._active_shm_size()
    assert wal_size > 0  # WAL-schwer, sonst testet nichts

    # Manifest-size_bytes ist die reale Disk-Nutzung (Haupt + -wal + -shm).
    assert seg.size_bytes == main_only + wal_size + shm_size
    # Und damit strikt größer als die reine Hauptdatei (Budget sieht die WAL).
    assert seg.size_bytes > main_only


# ---------------------------------------------------------------------------
# Defensive Fehlerpfade der neuen Helfer
# ---------------------------------------------------------------------------


async def test_skip_or_quarantine_read_skips_when_file_vanished_after_open(store: SqliteSegmentStore):
    # Race-Variante zu (2): Datei verschwindet erst NACH erfolgreichem Open, beim
    # Lesen. _skip_or_quarantine_read erkennt die fehlende Datei und überspringt.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    closed = (await store.manifest.list_closed_segments())[0]
    (store._segments_dir / closed.filename).unlink()

    result = await store._skip_or_quarantine_read(closed, aiosqlite.OperationalError("no such table: ringbuffer"))
    assert result is None


async def test_checkpoint_small_legacy_swallows_errors(store: SqliteSegmentStore, tmp_path: Path):
    # Ein nicht-öffenbarer/kaputter Pfad darf den Read nicht brechen — der Fehler
    # wird geschluckt, der Read degradiert danach auf den immutable-Pfad.
    bogus = tmp_path / "does-not-exist-dir" / "legacy.db"
    await store._checkpoint_small_legacy(bogus)  # kein Fehler


def test_unlink_with_sidecars_ignores_missing_and_unremovable(store: SqliteSegmentStore, tmp_path: Path):
    # Nur die Hauptdatei existiert; -wal/-shm fehlen → OSError wird geschluckt.
    only_main = tmp_path / "only_main.db"
    only_main.write_bytes(b"\x00")
    store._unlink_with_sidecars(only_main)
    assert not only_main.exists()


# ---------------------------------------------------------------------------
# P2 (#951, :854): Legacy-WAL-Checkpoint muss die busy-Spalte der Ergebnis-Zeile
# auswerten, bevor „recovered" markiert wird.
# ---------------------------------------------------------------------------


async def test_checkpoint_small_legacy_busy_row_is_not_success(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # wal_checkpoint(TRUNCATE) wirft bei BUSY nicht, sondern meldet busy=1 in der
    # Ergebnis-Zeile (busy, log, checkpointed). Der Helper darf das NICHT als Erfolg
    # werten, sonst würde das Segment fälschlich „recovered" markiert und danach mit
    # immutable=1 gelesen (nicht-gecheckpointete WAL-Frames unsichtbar).
    legacy = tmp_path / "legacy.db"
    legacy.write_bytes(b"\x00")

    real_connect = aiosqlite.connect

    class _BusyCursor:
        async def fetchone(self):
            return (1, 5, 0)  # busy=1 → Checkpoint NICHT vollständig

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _BusyConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint" in sql:
                return _BusyCursor()
            return self._inner.execute(sql, *args, **kwargs)

        async def commit(self):
            await self._inner.commit()

        async def close(self):
            await self._inner.close()

    async def _busy_connect(*args, **kwargs):
        inner = await real_connect(*args, **kwargs)
        return _BusyConn(inner)

    monkeypatch.setattr(aiosqlite, "connect", _busy_connect)

    ok = await store._checkpoint_small_legacy(legacy)
    assert ok is False  # busy != 0 → kein Erfolg, kein „recovered"-Mark


async def test_checkpoint_small_legacy_success_row_is_success(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Gegenprobe: busy=0 in der Ergebnis-Zeile → echter Erfolg → True.
    legacy = tmp_path / "legacy_ok.db"
    legacy.write_bytes(b"\x00")

    real_connect = aiosqlite.connect

    class _OkCursor:
        async def fetchone(self):
            return (0, 3, 3)  # busy=0 → vollständig gecheckpointet

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _OkConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint" in sql:
                return _OkCursor()
            return self._inner.execute(sql, *args, **kwargs)

        async def commit(self):
            await self._inner.commit()

        async def close(self):
            await self._inner.close()

    async def _ok_connect(*args, **kwargs):
        inner = await real_connect(*args, **kwargs)
        return _OkConn(inner)

    monkeypatch.setattr(aiosqlite, "connect", _ok_connect)

    ok = await store._checkpoint_small_legacy(legacy)
    assert ok is True


# ---------------------------------------------------------------------------
# P2 (#951, :1737): unlesbare/korrupte checkpoint_pending-Segmente werden
# quarantäniert statt den (segmentierten) Startup abzubrechen.
# ---------------------------------------------------------------------------


async def test_run_pending_checkpoints_quarantines_unreadable_segment(store: SqliteSegmentStore, tmp_path: Path):
    # Ein checkpoint_pending-Segment, dessen Datei korrupt/unlesbar ist, darf beim
    # Checkpoint-Retry nicht propagieren, sondern muss quarantäniert werden — analog
    # zur Korruptions-Isolation im Read-Pfad.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    closed = (await store.manifest.list_closed_segments())[0]
    await store.manifest.mark_checkpoint_pending(closed.segment_id)

    # Segment-Datei mit Garbage überschreiben → wal_checkpoint wirft „not a database".
    seg_path = store._segments_dir / closed.filename
    seg_path.write_bytes(b"this is not a sqlite database at all\x00\x01\x02")
    Path(f"{seg_path}-wal").unlink(missing_ok=True)
    Path(f"{seg_path}-shm").unlink(missing_ok=True)

    recovered = await store.run_pending_checkpoints()
    assert recovered == 0

    seg = await store.manifest.get_segment(closed.segment_id)
    assert seg is not None
    assert seg.status == "quarantined"
    # Nicht mehr als pending gelistet.
    assert closed.segment_id not in {s.segment_id for s in await store.manifest.list_checkpoint_pending_segments()}


async def test_run_pending_checkpoints_does_not_abort_startup_on_corrupt_segment(store: SqliteSegmentStore, tmp_path: Path):
    # enforce_retention() ruft run_pending_checkpoints() als Vorlauf. Ein korruptes
    # pending-Segment darf enforce_retention nicht mit einem aiosqlite-Fehler abbrechen.
    from obs.ringbuffer.store.config import StoreRetentionConfig

    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    closed = (await store.manifest.list_closed_segments())[0]
    await store.manifest.mark_checkpoint_pending(closed.segment_id)
    seg_path = store._segments_dir / closed.filename
    seg_path.write_bytes(b"garbage-not-a-db\x00")
    Path(f"{seg_path}-wal").unlink(missing_ok=True)
    Path(f"{seg_path}-shm").unlink(missing_ok=True)

    store._retention_config = StoreRetentionConfig(max_entries=1)
    # Darf nicht werfen.
    await store.enforce_retention()

    seg = await store.manifest.get_segment(closed.segment_id)
    assert seg is not None and seg.status == "quarantined"
