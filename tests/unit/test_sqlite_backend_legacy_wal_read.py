"""Legacy-Read: immutable-vs-WAL-aware-Entscheidung bei busy Checkpoint (#951, Codex :1214).

Hintergrund: ``_open_legacy_read_conn`` checkpointet eine kleine dirty-WAL-Legacy-DB
einmalig und liest sie sonst über ``mode=ro&immutable=1``. ``immutable=1`` IGNORIERT
aber committete ``-wal``-Frames. Konnte der Checkpoint NICHT abgeschlossen werden
(BUSY/Fehler), stünden die jüngsten committeten Zeilen weiterhin nur im ``-wal`` und
ein ``immutable=1``-Read ließe sie still weg. Die Fix-Entscheidung fällt anhand des
physischen ``-wal``-Dirty-Zustands NACH dem Checkpoint-Versuch:

* Checkpoint erfolgreich ODER kein dirty WAL → ``immutable=1`` (schnell, korrekt).
* Dirty WAL bleibt (busy/nicht abgeschlossen) → ``mode=ro`` (WAL-aware, sieht die
  neuesten committeten Zeilen).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS ringbuffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    datapoint_id   TEXT    NOT NULL,
    topic          TEXT    NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    source_adapter TEXT    NOT NULL,
    quality        TEXT    NOT NULL,
    metadata_version INTEGER NOT NULL DEFAULT 1,
    metadata       TEXT    NOT NULL DEFAULT '{}'
);
"""


def _insert(conn: sqlite3.Connection, value: int, i: int) -> None:
    conn.execute(
        """INSERT INTO ringbuffer
               (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality)
           VALUES (?, 'dp-legacy', 'dp/dp-legacy/value', NULL, ?, 'legacy', 'good')""",
        (f"2025-01-01T00:00:0{i}.000Z", str(value)),
    )


def _build_legacy_db_with_wal_only_rows(db: Path, *, checkpointed: list[int], wal_only: list[int]) -> sqlite3.Connection:
    """Baut eine kleine Legacy-DB, deren ``wal_only``-Zeilen NUR im ``-wal`` committet sind.

    Die ``checkpointed``-Zeilen wandern per TRUNCATE-Checkpoint in die Haupt-DB. Die
    ``wal_only``-Zeilen werden danach committet, aber NICHT gecheckpointet – der Rückgabe-
    Connection wird offen gehalten, damit SQLite den WAL nicht beim letzten Close
    automatisch checkpointet. Der Aufrufer schließt sie am Testende.
    """
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")  # kein impliziter Checkpoint
    conn.executescript(_LEGACY_SCHEMA)
    for i, value in enumerate(checkpointed):
        _insert(conn, value, i)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # Basis in Haupt-DB falten
    conn.commit()
    for j, value in enumerate(wal_only):
        _insert(conn, value, len(checkpointed) + j)
    conn.commit()  # committet, aber NUR im -wal (kein Checkpoint)
    return conn


@pytest.fixture
async def store(tmp_path: Path):
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def test_dirty_wal_busy_checkpoint_reads_newest_wal_committed_rows(store: SqliteSegmentStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Busy Checkpoint → WAL-aware ``mode=ro`` → neueste committete WAL-Zeilen sichtbar."""
    db = tmp_path / "obs_ringbuffer.db"
    holder = _build_legacy_db_with_wal_only_rows(db, checkpointed=[1, 2, 3], wal_only=[42, 43])
    try:
        wal = Path(f"{db}-wal")
        assert wal.exists() and wal.stat().st_size > 0  # dirty WAL liegt vor

        await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

        # Busy-Checkpoint simulieren: der Checkpoint schlägt fehl/ist busy → False,
        # das -wal bleibt dirty. Der Read DARF dann nicht auf immutable=1 fallen.
        async def _busy(_legacy_path: Path) -> bool:
            return False

        monkeypatch.setattr(store, "_checkpoint_small_legacy", _busy)

        rows = await store.query(StoreQuery(limit=50))
        values = {r["new_value"] for r in rows}
        # Die NEUESTEN nur-im-WAL committeten Zeilen (42, 43) müssen sichtbar sein –
        # ein immutable=1-Snapshot hätte sie still weggelassen.
        assert values == {1, 2, 3, 42, 43}
    finally:
        holder.close()


async def test_clean_legacy_without_dirty_wal_uses_immutable_path(store: SqliteSegmentStore, tmp_path: Path):
    """Gegentest: keine dirty WAL → weiterhin immutable=1-Pfad, korrektes Ergebnis."""
    db = tmp_path / "obs_ringbuffer.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_LEGACY_SCHEMA)
    for i, value in enumerate([10, 20, 30]):
        _insert(conn, value, i)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()  # letzter Close checkpointet/leert das -wal → sauber

    wal = Path(f"{db}-wal")
    assert not (wal.exists() and wal.stat().st_size > 0)  # kein dirty WAL

    # Ohne dirty WAL wählt der Read immutable=1.
    assert store._legacy_wal_still_dirty(db) is False

    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())
    rows = await store.query(StoreQuery(limit=50))
    assert {r["new_value"] for r in rows} == {10, 20, 30}


def test_legacy_wal_still_dirty_reflects_wal_presence(store: SqliteSegmentStore, tmp_path: Path):
    """``_legacy_wal_still_dirty`` steuert die immutable-vs-WAL-aware-Wahl rein über das -wal."""
    db = tmp_path / "obs_ringbuffer.db"
    db.write_bytes(b"\x00" * 64)
    assert store._legacy_wal_still_dirty(db) is False

    wal = Path(f"{db}-wal")
    wal.write_bytes(b"\x00" * 4096)
    assert store._legacy_wal_still_dirty(db) is True

    wal.write_bytes(b"")  # leeres -wal zählt nicht als dirty
    assert store._legacy_wal_still_dirty(db) is False
