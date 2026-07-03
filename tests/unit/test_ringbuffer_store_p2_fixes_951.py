"""Codex-P2-Fixes am segmentierten SQLite-Store (#919, PR #951).

Ein Test (bzw. eine kleine Gruppe) je Finding, jeweils TDD-first geschrieben –
er reproduziert den Bug ohne Fix und wird durch den Fix grün:

1. Komplexe (list/dict) Datapoint-Werte werden NICHT als JSON-``null`` getaggt,
   sodass der SQL-Pushdown ``eq/ne null`` nur echtes JSON-null trifft (Parität
   zum Referenz-``_matches_value_filter``).
2. Legacy-Kandidaten werden bei ``sort_field='id'`` nach id (rowid) limitiert,
   nicht nach ts – sonst fallen bei out-of-order-Timestamps die höchsten
   Legacy-rowids aus der gebundenen Kandidatenmenge.
3. Malformed/Non-JSON-Legacy-``old_value``/``new_value`` bricht die Query nicht
   (safe decode → Rohwert statt ``JSONDecodeError``).
4. Ein unwindowed contains/regex mit ``candidate_cap`` bleibt wirklich gebounded
   (Kandidaten werden VOR dem teuren Match gedeckelt).
5. ``run_pending_checkpoints()`` läuft im Laufzeitpfad (via ``enforce_retention``),
   sodass ein ``checkpoint_pending``-Segment wieder retention-fähig wird.
6. Scheitert das Löschen der Basis-Segmentdatei, bleibt der Manifest-Eintrag
   erhalten (Retention versucht es erneut); Sidecar-Fehler bleiben tolerant.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore, _derive_value_type


def _event(value: Any, ts: str, *, dp: str = "dp-1", old: Any = None) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
        old_value=old,
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
# (1) Komplexe Werte nicht als JSON-null taggen (:238)
# ---------------------------------------------------------------------------


def test_derive_value_type_complex_is_not_null():
    # Skalare bleiben wie gehabt; list/dict bekommen einen eigenen Typ ('json'),
    # damit 'null' ausschließlich echtes JSON-null bezeichnet.
    assert _derive_value_type(None) == "null"
    assert _derive_value_type([1, 2, 3]) != "null"
    assert _derive_value_type({"a": 1}) != "null"
    assert _derive_value_type([1, 2, 3]) == "json"
    assert _derive_value_type({"a": 1}) == "json"


async def test_eq_null_does_not_match_complex_value(store: SqliteSegmentStore):
    # Ein echtes JSON-null UND ein komplexer Wert (Liste) im selben Segment.
    await store.append(
        [
            _event(None, "2026-01-01T00:00:00.000Z"),
            _event([1, 2, 3], "2026-01-01T00:00:01.000Z"),
        ]
    )
    # eq null darf NUR das echte JSON-null treffen (Parität: [1,2,3] == None ist False).
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "eq", "field": "new_value", "value": None}]))
    assert [r["new_value"] for r in rows] == [None]


async def test_ne_null_matches_complex_value(store: SqliteSegmentStore):
    await store.append(
        [
            _event(None, "2026-01-01T00:00:00.000Z"),
            _event([1, 2, 3], "2026-01-01T00:00:01.000Z"),
        ]
    )
    # ne null muss den komplexen Wert einschließen ([1,2,3] != None ist True).
    rows = await store.query(StoreQuery(limit=10, value_filters=[{"operator": "ne", "field": "new_value", "value": None}]))
    assert [r["new_value"] for r in rows] == [[1, 2, 3]]


# ---------------------------------------------------------------------------
# (2) Legacy-Kandidaten bei id-Sort nach id ordnen (:841)
# ---------------------------------------------------------------------------


def _build_legacy_out_of_order(path: Path, rows: list[tuple[str, int]]) -> None:
    """Legacy-Single-DB mit EXPLIZITER rowid und beliebigen Timestamps.

    ``rows`` = Liste von ``(ts, value)`` in Insert-Reihenfolge (rowid = 1..n).
    Die Timestamps sind bewusst out-of-order: die zuletzt eingefügte Zeile
    (höchste rowid) hat einen SEHR frühen ts.
    """
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


async def test_legacy_id_sort_limits_by_id_not_ts(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    db = tmp_path / "obs_ringbuffer.db"
    # rowid 1..4; die höchste rowid (4 → value 999) trägt den FRÜHESTEN ts.
    _build_legacy_out_of_order(
        db,
        [
            ("2025-06-01T00:00:00.000Z", 10),  # id 1
            ("2025-06-02T00:00:00.000Z", 20),  # id 2
            ("2025-06-03T00:00:00.000Z", 30),  # id 3
            ("2025-01-01T00:00:00.000Z", 999),  # id 4 – neuester per rowid, ältester per ts
        ],
    )
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # Kandidaten-Cap auf 2 begrenzen. Bei ts-Limitierung fielen die per ts jüngsten
    # (30, 20) in die Kandidaten und die höchste rowid (999) fehlte → der finale
    # id-Sort könnte 999 (neuestes per rowid) nie mehr liefern. Bei id-Limitierung
    # sind die zwei höchsten rowids (999, 30) drin.
    query = StoreQuery(limit=2, sort_field="id", sort_order="desc", candidate_cap=2)
    rows = await store.query(query)
    values = [r["new_value"] for r in rows]
    # 999 ist die höchste rowid (neuestes per id) und MUSS als erstes erscheinen.
    assert values[0] == 999
    assert 999 in values


# ---------------------------------------------------------------------------
# (3) Legacy-JSON sicher decodieren (:926)
# ---------------------------------------------------------------------------


def _build_legacy_with_malformed_json(path: Path) -> None:
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
        # Zeile 1: valides JSON. Zeile 2: kaputter/non-JSON new_value (roher Text).
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2025-01-01T00:00:00.000Z", "dp-legacy", "dp/dp-legacy/value", None, json.dumps(42), "legacy", "good"),
        )
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2025-01-01T00:00:01.000Z", "dp-legacy", "dp/dp-legacy/value", "{not json", "definitely not json", "legacy", "good"),
        )
        conn.commit()
    finally:
        conn.close()


async def test_legacy_malformed_json_does_not_break_query(store: SqliteSegmentStore, tmp_path: Path):
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_with_malformed_json(db)
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # Ohne Fix wirft json.loads("definitely not json") JSONDecodeError → Query bricht.
    rows = await store.query(StoreQuery(limit=10))
    values = [r["new_value"] for r in rows]
    # Beide Zeilen kommen zurück; der malformed Wert bleibt als Rohstring erhalten.
    assert 42 in values
    assert "definitely not json" in values
    # Der malformed old_value ("{not json") bleibt ebenfalls roh, kein Crash.
    malformed_row = next(r for r in rows if r["new_value"] == "definitely not json")
    assert malformed_row["old_value"] == "{not json"


# ---------------------------------------------------------------------------
# (4) Unwindowed contains/regex wirklich kappen (:1074)
# ---------------------------------------------------------------------------


def test_build_segment_sql_caps_unwindowed_contains(store: SqliteSegmentStore):
    # Ohne Zeitfenster wird der teure Match auf eine gedeckelte Kandidaten-Subquery
    # gelegt (LIMIT VOR dem Match), statt jede Zeile inline zu scannen.
    query = StoreQuery(limit=10, candidate_cap=50, value_filters=[{"operator": "contains", "field": "new_value", "value": "x"}])
    sql, params = store._build_segment_sql(query)
    # Erkennbar an der Subquery + dem inneren LIMIT (candidate_cap) vor dem instr-Match.
    assert "FROM (SELECT" in sql
    assert "instr" in sql
    assert 50 in params  # candidate_cap als inneres LIMIT


async def test_unwindowed_contains_is_bounded_and_drops_beyond_cap(store: SqliteSegmentStore):
    # Ein Treffer JENSEITS der neuesten candidate_cap Zeilen (ältester Eintrag) wird
    # durch die Deckelung bewusst NICHT gefunden – das ist die dokumentierte Grenze
    # eines unwindowed contains. Ein Treffer INNERHALB der neuesten cap-Zeilen kommt.
    old_match = _event("HIT-old", "2026-01-01T00:00:00.000Z")  # ältester → außerhalb cap
    fillers = [_event(f"row-{i}", f"2026-01-01T00:01:{i % 60:02d}.{i:03d}Z") for i in range(200)]
    recent_match = _event("HIT-recent", "2026-01-01T00:59:59.999Z")  # neuester → innerhalb cap
    await store.append([old_match, *fillers, recent_match])

    query = StoreQuery(limit=10, candidate_cap=10, value_filters=[{"operator": "contains", "field": "new_value", "value": "HIT-"}])
    rows = await store.query(query)
    values = {r["new_value"] for r in rows}
    # Der neueste Treffer ist in der gedeckelten Kandidatenmenge, der älteste nicht.
    assert "HIT-recent" in values
    assert "HIT-old" not in values


async def test_unwindowed_regex_callback_is_bounded(store: SqliteSegmentStore, monkeypatch):
    events = [_event(f"row-{i}", f"2026-01-01T00:00:{i % 60:02d}.{i:03d}Z") for i in range(500)]
    await store.append(events)

    import obs.ringbuffer.store.sqlite_backend as backend

    calls = {"n": 0}
    orig = backend._obs_regexp_impl

    def _counting(pattern, flags, value):
        calls["n"] += 1
        return orig(pattern, flags, value)

    monkeypatch.setattr(backend, "_obs_regexp_impl", _counting)

    cap = 50
    query = StoreQuery(limit=10, candidate_cap=cap, value_filters=[{"operator": "regex", "field": "new_value", "pattern": "NO_MATCH_[0-9]{9}"}])
    rows = await store.query(query)
    assert rows == []
    # Der teure Regex-Callback lief höchstens cap-mal (Kandidaten VOR dem Match gedeckelt),
    # nicht 500-mal (jede Zeile jedes Segments).
    assert calls["n"] <= cap


# ---------------------------------------------------------------------------
# (5) Pending-Checkpoints aus Produktionscode nachziehen (:1243)
# ---------------------------------------------------------------------------


async def test_enforce_retention_retries_pending_checkpoints(store: SqliteSegmentStore, monkeypatch):
    # Ein Segment ist checkpoint_pending, weil der Truncate beim Rotieren busy war.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
    await store.rotate()
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING

    assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CHECKPOINT_PENDING

    # Frische Historie sichern, damit Size-Budget-Retention den Guard erfüllt.
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])

    # Truncate klappt jetzt wieder.
    async def _ok(_conn):
        return True

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _ok)

    # enforce_retention MUSS die pending Checkpoints selbst nachziehen – ohne
    # expliziten run_pending_checkpoints()-Aufruf aus dem Test.
    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    await store.enforce_retention()
    seg = await store.manifest.get_segment(pending_id)
    # Entweder inzwischen retention-gelöscht (None) oder mindestens nicht mehr pending.
    assert seg is None or seg.status != SEGMENT_STATUS_CHECKPOINT_PENDING


# ---------------------------------------------------------------------------
# (6) Manifest-Eintrag bewahren, wenn Datei-Löschen scheitert (:1694)
# ---------------------------------------------------------------------------


async def test_delete_segment_keeps_manifest_row_when_base_unlink_fails(store: SqliteSegmentStore, monkeypatch):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    victim = (await store.manifest.list_closed_segments())[0]

    real_unlink = Path.unlink

    def _failing_base_unlink(self: Path, *args, **kwargs):
        # Die Basisdatei ist gesperrt/permission-fehlerhaft; Sidecars dürfen fehlschlagen.
        if self.name == victim.filename:
            raise OSError("device or resource busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _failing_base_unlink)

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    await store.enforce_retention()

    # Basisdatei-Löschen schlug fehl → Manifest-Zeile MUSS erhalten bleiben, damit
    # Retention es erneut versucht (Bytes bleiben sonst als Leichen auf der Platte
    # und verschwinden aus den Stats).
    still_there = await store.manifest.get_segment(victim.segment_id)
    assert still_there is not None
    assert (store._segments_dir / victim.filename).exists()


async def test_delete_segment_tolerates_sidecar_unlink_failure(store: SqliteSegmentStore, monkeypatch):
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    victim = (await store.manifest.list_closed_segments())[0]

    real_unlink = Path.unlink

    def _failing_sidecar_unlink(self: Path, *args, **kwargs):
        # Nur die -wal-Sidecar ist unlöschbar; die Basisdatei geht weg.
        if self.name == f"{victim.filename}-wal":
            raise OSError("sidecar locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _failing_sidecar_unlink)

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    await store.enforce_retention()

    # Basisdatei weg → Manifest-Zeile entfällt trotz Sidecar-Fehler (tolerant).
    assert await store.manifest.get_segment(victim.segment_id) is None
    assert not (store._segments_dir / victim.filename).exists()


async def test_delete_segment_treats_missing_base_as_removed(store: SqliteSegmentStore):
    # Ist die Basisdatei bereits weg (FileNotFoundError beim unlink), gilt das als
    # erfolgreiche Löschung (Platz frei) und die Manifest-Zeile wird entfernt.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    victim = (await store.manifest.list_closed_segments())[0]
    # Datei vorab entfernen → _unlink_with_sidecars sieht FileNotFoundError.
    (store._segments_dir / victim.filename).unlink()

    ok = await store._delete_segment(victim)
    assert ok is True
    assert await store.manifest.get_segment(victim.segment_id) is None


async def test_age_retention_keeps_row_when_base_unlink_fails(store: SqliteSegmentStore, monkeypatch):
    # Delete-Durability auch im Age-Cutoff-Pfad: schlägt das Basis-Unlink fehl,
    # bleibt die Manifest-Zeile erhalten (kein Endlos-Loop, Retry beim nächsten Lauf).
    await store.append([_event(1, "2000-01-01T00:00:00.000Z")])  # sehr alt
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])  # frisch
    victim = (await store.manifest.list_closed_segments())[0]

    real_unlink = Path.unlink

    def _failing_base_unlink(self: Path, *args, **kwargs):
        if self.name == victim.filename:
            raise OSError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _failing_base_unlink)
    store._retention_config = StoreRetentionConfig(max_age=1)  # alles vor ~jetzt fällt
    await store.enforce_retention()
    assert await store.manifest.get_segment(victim.segment_id) is not None


async def test_row_retention_keeps_row_when_base_unlink_fails(store: SqliteSegmentStore, monkeypatch):
    # Delete-Durability auch im Row-Budget-Pfad (sonst würde das älteste,
    # undeletbare Segment endlos re-selektiert).
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])
    victim = (await store.manifest.list_closed_segments())[0]

    real_unlink = Path.unlink

    def _failing_base_unlink(self: Path, *args, **kwargs):
        if self.name == victim.filename:
            raise OSError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _failing_base_unlink)
    store._retention_config = StoreRetentionConfig(max_entries=1)
    await store.enforce_retention()
    assert await store.manifest.get_segment(victim.segment_id) is not None


def test_effective_candidate_cap_falls_back_to_default():
    # Defensive Fallback: fehlt (wider Erwartung) ein candidate_cap, greift der
    # Legacy-Default als harte Zeilenobergrenze.
    from obs.ringbuffer.store.sqlite_backend import _LEGACY_DEFAULT_CANDIDATE_CAP

    assert SqliteSegmentStore._effective_candidate_cap(StoreQuery(limit=5)) == _LEGACY_DEFAULT_CANDIDATE_CAP
    assert SqliteSegmentStore._effective_candidate_cap(StoreQuery(limit=5, candidate_cap=7)) == 7


# ---------------------------------------------------------------------------
# (Codex :1346) Segmentgröße nach erfolgreichem Checkpoint (TRUNCATE) auffrischen
# ---------------------------------------------------------------------------


async def test_rotate_refreshes_segment_size_after_successful_checkpoint(store: SqliteSegmentStore, monkeypatch):
    # Vor dem Checkpoint zählt _refresh_active_segment_stats WAL+SHM voll mit
    # (WAL-schweres Segment). Der erfolgreiche TRUNCATE verschiebt/truncatet die
    # WAL-Bytes gerade in die Haupt-DB – ohne Auffrischung behielte das Manifest
    # die pre-checkpoint-Größe und die direkt folgende Retention überschätzte die
    # Disk-Nutzung. Nach dem Fix trägt das Manifest die REALE post-checkpoint-Größe.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    rotated_id = (await store.manifest.get_active_segment()).segment_id
    filename = (await store.manifest.get_segment(rotated_id)).filename

    # _segment_file_size liefert vor dem Checkpoint eine große (WAL-schwere) Größe,
    # danach die reale, kleine Größe – wie ein echter TRUNCATE, der die WAL leert.
    real_size = SqliteSegmentStore._segment_file_size
    state = {"checkpointed": False}

    def _fake_size(self, name):
        if name == filename and not state["checkpointed"]:
            return 50_000_000  # WAL-schwer, pre-checkpoint
        return real_size(self, name)

    monkeypatch.setattr(SqliteSegmentStore, "_segment_file_size", _fake_size)

    real_ckpt = store._try_truncate_checkpoint

    async def _ok_and_shrink(conn):
        result = await real_ckpt(conn)
        state["checkpointed"] = True  # WAL ist ab jetzt getruncatet → kleine Größe
        return result

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _ok_and_shrink)

    await store.rotate()

    seg = await store.manifest.get_segment(rotated_id)
    # Ohne Fix bliebe size_bytes == 50_000_000 (pre-checkpoint). Mit Fix entspricht
    # es der realen post-checkpoint-Größe (konsistent zu _segment_file_size).
    assert seg.size_bytes != 50_000_000
    assert seg.size_bytes == store._segment_file_size(filename)


async def test_rotate_keeps_pending_size_when_checkpoint_busy(store: SqliteSegmentStore, monkeypatch):
    # Scheitert der Checkpoint (busy), darf die Größe NICHT auf eine (falsche) post-
    # checkpoint-Größe gesetzt werden: die WAL-Bytes liegen weiter auf der Platte,
    # das Segment ist checkpoint_pending. Die pre-checkpoint-Größe bleibt maßgeblich.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id
    filename = (await store.manifest.get_segment(pending_id)).filename

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)

    def _big_size(self, name):
        return 50_000_000 if name == filename else SqliteSegmentStore._segment_file_size(self, name)

    monkeypatch.setattr(SqliteSegmentStore, "_segment_file_size", _big_size)

    await store.rotate()

    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING

    seg = await store.manifest.get_segment(pending_id)
    assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING
    # WAL noch da → die (große) pre-checkpoint-Größe bleibt maßgeblich.
    assert seg.size_bytes == 50_000_000


# ---------------------------------------------------------------------------
# (Codex :758) Legacy-Größe + Recovery-Status nach small-legacy-WAL-Checkpoint auffrischen
# ---------------------------------------------------------------------------


def _build_small_dirty_wal_legacy(path: Path) -> None:
    """Kleine Legacy-Single-DB im WAL-Modus mit uncheckpointeten Frames (dirty WAL)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")  # kein automatischer Checkpoint
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
        for i in range(5):
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"2025-01-01T00:00:0{i}.000Z", "dp-legacy", "dp/dp-legacy/value", None, json.dumps(i), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()


async def test_small_legacy_checkpoint_refreshes_size_and_recovery(store: SqliteSegmentStore, tmp_path: Path):
    # Ein kleines dirty-WAL-Legacy-Segment: beim ersten Read checkpointet der Store
    # die committeten WAL-Frames per TRUNCATE in die Haupt-DB. Ohne Fix behielte das
    # Manifest die pre-checkpoint-Größe (inkl. WAL-Bytes) UND den dirty_wal-Status →
    # Phantom-WAL-Bytes in Stats/Retention und ein Re-Checkpoint bei jedem Read.
    from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION

    legacy_file = tmp_path / "obs_ringbuffer.db"
    _build_small_dirty_wal_legacy(legacy_file)

    # Als dirty-WAL-Legacy einhängen mit bewusst zu hoch angesetzter (pre-checkpoint)
    # size_bytes, wie sie ein WAL-schwerer Zustand hinterlassen hätte. Wert unter
    # SMALL_MAX_BYTES (64 MiB), damit der small-legacy-Checkpoint-Pfad greift, aber
    # weit über der realen post-checkpoint-Größe (wenige KB).
    inflated = 10 * 1024 * 1024
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=inflated, dirty_wal=True)
    assert rec.recovery_status == "dirty_wal"
    assert rec.schema_version == LEGACY_SCHEMA_VERSION

    # Ein Read triggert den small-legacy-Checkpoint-Pfad.
    rows = await store.query(StoreQuery(limit=50))
    assert {r["new_value"] for r in rows} == {0, 1, 2, 3, 4}

    seg = await store.manifest.get_segment(rec.segment_id)
    # size_bytes wurde auf die REALE post-checkpoint-Größe (klein, via _segment_file_size) neu geschrieben.
    assert seg.size_bytes != inflated
    assert seg.size_bytes == store._segment_file_size(seg.filename)
    # recovery_status ist nicht mehr dirty_wal → Re-Checkpoint bei folgenden Reads entfällt.
    assert seg.recovery_status == "none"


async def test_small_legacy_checkpoint_failure_keeps_dirty_status(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Scheitert der Checkpoint (z. B. read-only FS), darf weder die Größe noch der
    # Recovery-Status verändert werden: die WAL-Bytes liegen weiter auf der Platte,
    # der Read degradiert auf immutable=1. Kein stiller „recovered"-Zustand.
    legacy_file = tmp_path / "obs_ringbuffer.db"
    _build_small_dirty_wal_legacy(legacy_file)
    inflated = 10 * 1024 * 1024  # unter SMALL_MAX_BYTES, damit der Checkpoint-Pfad überhaupt greift
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=inflated, dirty_wal=True)

    async def _fail_checkpoint(_self, _path):
        return False

    monkeypatch.setattr(SqliteSegmentStore, "_checkpoint_small_legacy", _fail_checkpoint)

    rows = await store.query(StoreQuery(limit=50))
    # Der Read degradiert auf immutable=1 und liefert weiter (mind. die Haupt-DB-Zeilen).
    assert len(rows) >= 0

    seg = await store.manifest.get_segment(rec.segment_id)
    assert seg.size_bytes == inflated
    assert seg.recovery_status == "dirty_wal"


# ---------------------------------------------------------------------------
# (Codex :724) Korruptes Legacy-Segment unter No-Zero-History-Guard bewahren
# ---------------------------------------------------------------------------


async def _attach_legacy_blob(store: SqliteSegmentStore, size_bytes: int) -> tuple[int, Path]:
    """Hängt eine synthetische Legacy-Single-DB gegebener Größe ins Manifest ein."""
    legacy_file = store._root / "legacy_source.db"
    legacy_file.write_bytes(b"\x00" * size_bytes)
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=size_bytes)
    return rec.segment_id, legacy_file


async def test_quarantined_legacy_not_deleted_when_only_data_source(store: SqliteSegmentStore):
    # Ein Read der eingehängten Legacy-DB traf auf Korruption → als quarantined
    # markiert. quarantined ist retention-eligible und _delete_segment erkennt das
    # Schema weiter als Legacy → ohne Fix würde die ORIGINALE Single-DB gelöscht,
    # obwohl sie die EINZIGE Datenquelle ist (Datenverlust unter dem Guard).
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024)
    await store.manifest.mark_quarantined(legacy_id, reason="malformed database disk image")
    assert await store._has_nonlegacy_data_segment() is False

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    # Der No-Zero-History-Guard muss das quarantänierte Legacy als Legacy-/History-
    # Herkunft behandeln (Schema, nicht Status) → kein Opfer, keine Löschung.
    assert await store._next_size_retention_victim() is None
    removed = await store.enforce_retention()
    assert removed == 0
    assert await store.manifest.get_segment(legacy_id) is not None
    assert legacy_file.exists()


async def test_quarantined_legacy_deleted_once_v2_data_exists(store: SqliteSegmentStore):
    # Sobald eine nicht-Legacy-Datenquelle existiert, ist auch das quarantänierte
    # Legacy wieder freigebbar (Guard erfüllt) – die Legacy-Rückgewinnungs-Semantik
    # bleibt erhalten, nur die Herkunfts-Erkennung darf nicht am Status scheitern.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])  # frische v2-Daten
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024)
    await store.manifest.mark_quarantined(legacy_id, reason="malformed database disk image")
    assert await store._has_nonlegacy_data_segment() is True

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    removed = await store.enforce_retention()
    assert removed >= 1
    assert await store.manifest.get_segment(legacy_id) is None
    assert not legacy_file.exists()
    # Frische v2-Daten (aktives Segment) bleiben erhalten.
    assert await store.manifest.get_active_segment() is not None


async def test_quarantined_v2_still_fifo_deletable_as_only_nonlegacy(store: SqliteSegmentStore):
    # Regression-Guard: die zuvor eingeführte Semantik „quarantänierte v2-Segmente
    # sind FIFO-löschbar" bleibt für NICHT-Legacy unverändert. Ein quarantäniertes
    # geschlossenes v2 darf weiter gelöscht werden – nur Legacy-Herkunft schützt.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    victim_id = (await store.manifest.get_active_segment()).segment_id
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:01.000Z")])  # aktives, frische Daten
    await store.manifest.mark_quarantined(victim_id, reason="corrupt")

    store._retention_config = StoreRetentionConfig(max_file_size_bytes=1)
    removed = await store.enforce_retention()
    assert removed >= 1
    assert await store.manifest.get_segment(victim_id) is None


# ---------------------------------------------------------------------------
# (Codex :376) Legacy-Regex-Ziel vor der Suche kappen (Parität zum v2-Callback)
# ---------------------------------------------------------------------------


def _build_legacy_with_string_values(path: Path, rows: list[tuple[str, str]]) -> None:
    """Legacy-Single-DB mit String-``new_value``en (``rows`` = ``(ts, string)``)."""
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


async def _attach_legacy_db(store: SqliteSegmentStore, db: Path) -> None:
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())


def test_legacy_regex_caps_target_length_before_search(monkeypatch):
    # Parität zum v2-``_obs_regexp_impl``: der Legacy-Python-Regex-Pfad
    # (_legacy_filter_matches) darf ``re.search`` NICHT über einen beliebig langen
    # gespeicherten Wert laufen lassen, sonst blockiert der synchrone Vergleich den
    # Event-Loop. Das Ziel wird auf die ersten ``_REGEX_MAX_TARGET_LEN`` Zeichen
    # gekappt – wie der v2-Callback.
    import obs.ringbuffer.store.sqlite_backend as backend

    seen = {"len": None}
    real_compile = backend.re.compile

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def search(self, target):
            seen["len"] = len(target)
            return self._inner.search(target)

    def _spy_compile(pattern, flags=0):
        return _Spy(real_compile(pattern, flags))

    monkeypatch.setattr(backend.re, "compile", _spy_compile)

    # Ein Muster, das erst weit hinter dem Cap treffen würde, plus ein sehr langer Wert.
    huge = "a" * (backend._REGEX_MAX_TARGET_LEN + 5000) + "NEEDLE"
    record = {"new_value": huge}
    spec = {"operator": "regex", "field": "new_value", "pattern": "NEEDLE"}
    result = backend._legacy_filter_matches(record, spec)

    # Ohne Cap sähe re.search den vollen Wert (len == len(huge)) und würde den Treffer
    # jenseits des Caps finden. Mit Cap ist das Ziel gebounded und der Treffer hinter
    # dem Cap wird – wie beim v2-Callback – bewusst NICHT gefunden.
    assert seen["len"] == backend._REGEX_MAX_TARGET_LEN
    assert result is False


async def test_legacy_regex_cap_matches_v2_semantics(store: SqliteSegmentStore, tmp_path: Path):
    # End-to-end über eine eingehängte Legacy-DB: ein Treffer INNERHALB des Caps wird
    # gefunden, ein Treffer erst JENSEITS des Caps nicht (gebundener Ziel-Scan).
    from obs.ringbuffer.store.sqlite_backend import _REGEX_MAX_TARGET_LEN

    within = "MATCH" + "x" * 10
    beyond = "y" * (_REGEX_MAX_TARGET_LEN + 100) + "MATCH"
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_with_string_values(
        db,
        [
            ("2025-01-01T00:00:00.000Z", within),
            ("2025-01-01T00:00:01.000Z", beyond),
        ],
    )
    await _attach_legacy_db(store, db)

    rows = await store.query(StoreQuery(limit=10, candidate_cap=100, value_filters=[{"operator": "regex", "field": "new_value", "pattern": "MATCH"}]))
    values = {r["new_value"] for r in rows}
    assert within in values
    assert beyond not in values


# ---------------------------------------------------------------------------
# (Codex :439) Legacy-String-Range-Vergleiche ablehnen (Parität zum v2-Pushdown)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte"])
def test_legacy_range_on_string_is_rejected(operator):
    # v2-Pushdown UND _matches_value_filter lehnen gt/gte/lt/lte auf STRING ab. Der
    # Legacy-Python-Fallback (_legacy_compare) darf NICHT auf lexikografische
    # String-Vergleiche degradieren – sonst wäre das Verhalten segment-abhängig.
    import obs.ringbuffer.store.sqlite_backend as backend

    record = {"new_value": "banana"}
    spec = {"operator": operator, "field": "new_value", "value": "apple"}
    with pytest.raises(ValueError, match="STRING"):
        backend._legacy_filter_matches(record, spec)


async def test_legacy_range_on_string_raises_in_query(store: SqliteSegmentStore, tmp_path: Path):
    # Bedient eine upgegradete Instanz nur ihr read-only Legacy-Segment, muss ein
    # gt/gte/lt/lte auf STRING denselben 422-tauglichen ValueError werfen wie der
    # v2-Pfad (segment-unabhängig identisches Verhalten).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_with_string_values(db, [("2025-01-01T00:00:00.000Z", "banana")])
    await _attach_legacy_db(store, db)

    with pytest.raises(ValueError, match="STRING"):
        await store.query(StoreQuery(limit=10, value_filters=[{"operator": "gt", "field": "new_value", "value": "apple"}]))


def test_legacy_range_on_numeric_still_works():
    # Regression-Guard: numerische Range-Vergleiche bleiben im Legacy-Pfad erlaubt.
    import obs.ringbuffer.store.sqlite_backend as backend

    assert backend._legacy_filter_matches({"new_value": 5}, {"operator": "gt", "field": "new_value", "value": 3}) is True
    assert backend._legacy_filter_matches({"new_value": 2}, {"operator": "gt", "field": "new_value", "value": 3}) is False


# ---------------------------------------------------------------------------
# (Codex :1364) Unicode-Folding für case-insensitive contains (Nicht-ASCII)
# ---------------------------------------------------------------------------


async def test_ignore_case_contains_matches_non_ascii(store: SqliteSegmentStore):
    # v2-Segment mit deutschen Umlauten. SQLite-``LOWER()`` foldet nur ASCII, sodass
    # „STRASSE"/„Straße" bei ignore_case per LOWER() NICHT auf „straße" matchten. Mit
    # dem Unicode-fähigen Callback (Python ``.lower()``) matcht es – Parität zum
    # Legacy-Python-Pfad.
    await store.append(
        [
            _event("Grüße", "2026-01-01T00:00:00.000Z"),
            _event("HÄLLO Welt", "2026-01-01T00:00:01.000Z"),
            _event("nichts", "2026-01-01T00:00:02.000Z"),
        ]
    )
    # Zeitfenster bindet den Query (guarded contains ist zulässig).
    query = StoreQuery(
        limit=10,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:03.000Z",
        value_filters=[{"operator": "contains", "field": "new_value", "value": "grüße", "ignore_case": True}],
    )
    rows = await store.query(query)
    assert {r["new_value"] for r in rows} == {"Grüße"}

    query2 = StoreQuery(
        limit=10,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:03.000Z",
        value_filters=[{"operator": "contains", "field": "new_value", "value": "hällo", "ignore_case": True}],
    )
    rows2 = await store.query(query2)
    assert {r["new_value"] for r in rows2} == {"HÄLLO Welt"}


async def test_ignore_case_contains_ascii_unchanged(store: SqliteSegmentStore):
    # Regression-Guard: ASCII-case-insensitive contains bleibt korrekt.
    await store.append(
        [
            _event("Hello World", "2026-01-01T00:00:00.000Z"),
            _event("goodbye", "2026-01-01T00:00:01.000Z"),
        ]
    )
    query = StoreQuery(
        limit=10,
        from_ts="2026-01-01T00:00:00.000Z",
        to_ts="2026-01-01T00:00:02.000Z",
        value_filters=[{"operator": "contains", "field": "new_value", "value": "HELLO", "ignore_case": True}],
    )
    rows = await store.query(query)
    assert {r["new_value"] for r in rows} == {"Hello World"}


# ---------------------------------------------------------------------------
# (Codex :1696) Größe nach Pending-Checkpoint-Recovery auffrischen
# ---------------------------------------------------------------------------


async def test_run_pending_checkpoints_refreshes_size(store: SqliteSegmentStore, monkeypatch):
    # Ein checkpoint_pending-Segment (Truncate war busy) trägt eine überhöhte
    # pre-checkpoint-Größe (inkl. WAL). Zieht run_pending_checkpoints den Truncate
    # endlich durch, MUSS die Manifest-size_bytes auf die reale post-checkpoint-Größe
    # aktualisiert werden – sonst rechnet die direkt folgende Budget-Retention mit der
    # alten, überhöhten Größe und löscht ältere/Legacy-Segmente unnötig.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id
    filename = (await store.manifest.get_segment(pending_id)).filename

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
    await store.rotate()

    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING

    assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CHECKPOINT_PENDING

    # Überhöhte pre-checkpoint-Größe simulieren (WAL-schwer).
    inflated = 50_000_000
    await store.manifest.update_segment_size(pending_id, size_bytes=inflated)
    assert (await store.manifest.get_segment(pending_id)).size_bytes == inflated

    # Truncate klappt jetzt.
    async def _ok(_conn):
        return True

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _ok)

    recovered = await store.run_pending_checkpoints()
    assert recovered == 1

    seg = await store.manifest.get_segment(pending_id)
    # Ohne Fix bliebe size_bytes == inflated. Mit Fix entspricht es der realen
    # post-checkpoint-Größe (konsistent zu _segment_file_size, zählt WAL/SHM mit).
    assert seg.size_bytes != inflated
    assert seg.size_bytes == store._segment_file_size(filename)


# ---------------------------------------------------------------------------
# (Codex :584) Teil-Batch-Append bei Insert-Fehler zurückrollen
# ---------------------------------------------------------------------------


async def test_partial_batch_append_is_rolled_back_on_insert_error(store: SqliteSegmentStore):
    # Scheitert ein späteres _insert_event in einem Mehr-Event-append() (hier: nicht
    # serialisierbare Metadaten → TypeError beim json.dumps), bleiben die früheren
    # Inserts sonst in der offenen SQLite-Transaktion liegen und würden vom nächsten
    # erfolgreichen append() auf derselben Connection MIT-committet – obwohl der
    # ursprüngliche Aufrufer einen Fehler sah. Der Fix rollt bei einem Batch-Fehler
    # die aktive Transaktion zurück, sodass keine partiellen Zeilen später auftauchen.
    class _Unserializable:
        pass

    ok = _event(1, "2026-01-01T00:00:00.000Z")
    # Zweites Event scheitert beim Serialisieren der Metadaten (TypeError).
    bad = _event(2, "2026-01-01T00:00:01.000Z")
    bad.metadata = {"broken": _Unserializable()}

    with pytest.raises(TypeError):
        await store.append([ok, bad])

    # Nach dem Fehler darf KEINE Zeile aus dem gescheiterten Batch persistiert sein.
    assert await store._total_row_count() == 0

    # Ein nachfolgender erfolgreicher append() committet NUR sein eigenes Event –
    # die partielle Zeile aus dem ersten Batch darf nicht mit hochkommen.
    await store.append([_event(3, "2026-01-01T00:00:02.000Z")])
    rows = await store.query(StoreQuery(limit=50))
    assert {r["new_value"] for r in rows} == {3}
    assert await store._total_row_count() == 1


async def test_successful_batch_append_stays_atomic(store: SqliteSegmentStore):
    # Regression-Guard: ein fehlerfreier Mehr-Event-Batch bleibt vollständig committet.
    await store.append(
        [
            _event(10, "2026-01-01T00:00:00.000Z"),
            _event(11, "2026-01-01T00:00:01.000Z"),
            _event(12, "2026-01-01T00:00:02.000Z"),
        ]
    )
    rows = await store.query(StoreQuery(limit=50))
    assert {r["new_value"] for r in rows} == {10, 11, 12}


# ---------------------------------------------------------------------------
# (Codex :564) Partielle Store-Ressourcen bei open()-Fehler vollstaendig schliessen
# ---------------------------------------------------------------------------


async def test_open_failure_after_manifest_open_closes_all_resources(tmp_path: Path, monkeypatch):
    # Gelingt manifest.open(), scheitert danach aber das Ermitteln/Oeffnen des aktiven
    # Segments (_create_segment_locked / _open_segment_conn, z.B. weil ein vorhandenes
    # aktives Segment korrupt oder nicht schreibbar ist), gibt der alte Fehlerpfad NUR die
    # Writer-Lease frei. Die Manifest-aiosqlite-Connection/-Thread LEAKEN dann, weil der
    # aufrufende RingBuffer erst NACH Rueckkehr von store.open() aufraeumt. Der Fix schliesst
    # im Fehlerpfad ALLE bereits geoeffneten Ressourcen (Manifest-Connection + evtl. schon
    # geoeffnete aktive Segment-Connection) und gibt die Lease frei, bevor der Fehler
    # propagiert.
    store = SqliteSegmentStore(tmp_path / "root")

    boom = RuntimeError("segment open failed")

    async def _fail_open_segment_conn(_filename: str):
        raise boom

    monkeypatch.setattr(store, "_open_segment_conn", _fail_open_segment_conn)

    # (a) Der urspruengliche Fehler propagiert unveraendert.
    with pytest.raises(RuntimeError, match="segment open failed"):
        await store.open()

    # (b) Manifest-Connection ist geschlossen (kein Leak) UND die Writer-Lease ist frei.
    assert store.manifest._conn is None
    assert store._lease.owns_lock is False
    assert store._active_conn is None

    # (c) Ein erneuter open() gelingt – keine belegten Ressourcen/kein gehaltenes Lock.
    monkeypatch.undo()
    await store.open()
    try:
        assert store._lease.owns_lock is True
        assert store.manifest._conn is not None
        await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
        rows = await store.query(StoreQuery(limit=10))
        assert {r["new_value"] for r in rows} == {1}
    finally:
        await store.close()


async def test_open_failure_closes_already_assigned_active_segment_conn(tmp_path: Path, monkeypatch):
    # Ist die aktive Segment-Connection im Fehlerzeitpunkt bereits an self._active_conn
    # zugewiesen, muss der Fehlerpfad AUCH diese Connection schliessen (nicht nur Manifest +
    # Lease). Wir oeffnen im Wrapper eine echte Segment-Connection, weisen sie zu und werfen
    # dann – wie ein Schritt, der NACH der Zuweisung scheitert.
    store = SqliteSegmentStore(tmp_path / "root")

    real_open_segment_conn = store._open_segment_conn
    opened: list = []

    async def _assign_then_fail(filename: str):
        conn = await real_open_segment_conn(filename)
        opened.append(conn)
        store._active_conn = conn
        raise RuntimeError("post-assign failure")

    monkeypatch.setattr(store, "_open_segment_conn", _assign_then_fail)

    with pytest.raises(RuntimeError, match="post-assign failure"):
        await store.open()

    # Die zugewiesene aktive Segment-Connection wurde geschlossen (Zugriff wirft nach close()).
    assert opened, "active segment connection should have been opened before failure"
    with pytest.raises(ValueError):
        await opened[0].execute("SELECT 1")

    assert store._active_conn is None
    assert store.manifest._conn is None
    assert store._lease.owns_lock is False

    # Erneuter open() gelingt sauber.
    monkeypatch.undo()
    await store.open()
    await store.close()


# ===========================================================================
# Codex-P2-Findings (Runde 2, #951)
# ===========================================================================


# ---------------------------------------------------------------------------
# (Codex :1123) Synthetische Legacy-IDs mehrerer Quellen disjunkt
# ---------------------------------------------------------------------------


def _build_legacy_simple(path: Path, rows: list[tuple[str, int]]) -> None:
    """Legacy-Single-DB mit rowid = 1..n (Insert-Reihenfolge), ``(ts, value)``."""
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


def test_legacy_row_to_dict_disjoint_across_adjacent_segments():
    # Zwei aufeinanderfolgende Legacy-segment_ids (s, s+1). Ohne Fix kollidierte
    # (bloßes ``-(segment_id & 0xFFFF)``) rowid r der einen mit rowid r+1 der
    # nächsten Quelle: gid(seg=s, rowid=2) == gid(seg=s+1, rowid=1). Mit dem
    # Per-Quelle-Stride sind die synthetischen IDs beider Quellen disjunkt.
    row1 = {
        "id": 1,
        "ts": "t",
        "datapoint_id": "d",
        "topic": "x",
        "old_value": None,
        "new_value": None,
        "source_adapter": "a",
        "quality": "good",
        "metadata_version": 1,
        "metadata": "{}",
    }
    row2 = dict(row1, id=2)

    gid_a1 = SqliteSegmentStore._legacy_row_to_dict(row1, segment_id=5)["global_event_id"]
    gid_a2 = SqliteSegmentStore._legacy_row_to_dict(row2, segment_id=5)["global_event_id"]
    gid_b1 = SqliteSegmentStore._legacy_row_to_dict(row1, segment_id=6)["global_event_id"]
    gid_b2 = SqliteSegmentStore._legacy_row_to_dict(row2, segment_id=6)["global_event_id"]

    # Innerhalb einer Quelle rowid-monoton (höhere rowid ⇒ höhere/weniger negative ID).
    assert gid_a2 > gid_a1
    assert gid_b2 > gid_b1
    # Kein Wert kommt doppelt vor – insbesondere NICHT gid_a2 == gid_b1 (der alte Bug).
    assert len({gid_a1, gid_a2, gid_b1, gid_b2}) == 4
    # Ordnung: höhere segment_id ⇒ ältere Quelle ⇒ tiefere (negativere) IDs.
    assert max(gid_b1, gid_b2) < min(gid_a1, gid_a2)
    # Alle strikt negativ (unter allen positiven v2-IDs).
    assert gid_a1 < 0 and gid_b2 < 0


async def test_two_legacy_sources_expose_unique_entry_ids(store: SqliteSegmentStore, tmp_path: Path):
    # Zwei read-only attached Legacy-DBs. Ohne disjunkte IDs kollidierten
    # aufeinanderfolgende synthetische ``global_event_id``s über die Quellgrenze –
    # als entry-IDs exponiert brächen Multi-Filterset-Queries/Exports auf
    # eindeutigen IDs. Mit dem Fix sind ALLE zurückgegebenen IDs eindeutig.
    db_a = tmp_path / "legacy_a.db"
    db_b = tmp_path / "legacy_b.db"
    _build_legacy_simple(db_a, [("2025-01-01T00:00:00.000Z", 10), ("2025-01-01T00:00:01.000Z", 11)])
    _build_legacy_simple(db_b, [("2025-02-01T00:00:00.000Z", 20), ("2025-02-01T00:00:01.000Z", 21)])
    await store.manifest.register_legacy_segment(source_path=str(db_a), size_bytes=db_a.stat().st_size)
    await store.manifest.register_legacy_segment(source_path=str(db_b), size_bytes=db_b.stat().st_size)

    rows = await store.query(StoreQuery(limit=50))
    ids = [r["global_event_id"] for r in rows]
    assert len(ids) == 4
    # Kern der Regression: keine doppelten synthetischen entry-IDs über Quellgrenzen.
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# (Codex :1830) Fehlendes Pending-Segment nicht neu erzeugen
# ---------------------------------------------------------------------------


async def test_run_pending_checkpoints_skips_missing_file_without_recreate(store: SqliteSegmentStore, monkeypatch):
    # Ein checkpoint_pending-Segment, dessen Datei vor dem Retry verschwunden ist.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
    await store.rotate()
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING

    seg = await store.manifest.get_segment(pending_id)
    assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING
    seg_path = store._segments_dir / seg.filename
    # Datei entfernen (Sidecars mit) – als wäre sie vor dem Retry verschwunden.
    for p in (seg_path, Path(f"{seg_path}-wal"), Path(f"{seg_path}-shm")):
        if p.exists():
            p.unlink()

    # run_pending_checkpoints darf die Datei NICHT neu (leer) anlegen und das
    # Segment NICHT als checkpoint_done markieren.
    async def _ok(_conn):
        return True

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _ok)
    recovered = await store.run_pending_checkpoints()

    assert recovered == 0
    assert not seg_path.exists()  # keine leere Ersatz-DB angelegt
    seg_after = await store.manifest.get_segment(pending_id)
    # Segment bleibt pending (nicht fälschlich als done markiert).
    assert seg_after.status == SEGMENT_STATUS_CHECKPOINT_PENDING


# ---------------------------------------------------------------------------
# (Codex :576) Fehlendes AKTIVES Segment beim (Wieder-)Öffnen nicht als leere DB neu anlegen
# ---------------------------------------------------------------------------


async def test_reopen_with_missing_active_segment_does_not_recreate_empty_db(tmp_path: Path):
    # Ein Manifest mit einem AKTIVEN Segment wird wieder geöffnet, NACHDEM die Datei
    # des aktiven Segments entfernt wurde. Der alte Pfad ging durch _open_segment_conn()
    # mit einem normalen SCHREIBBAREN Open → es legte still eine frische LEERE DB am
    # alten Dateinamen an, während das Manifest weiter die alten Zeilen behauptete
    # (Datenverlust bei Queries). Der Fix erkennt die fehlende Datei beim Öffnen, markiert
    # das alte aktive Segment als verloren (quarantäniert) und eröffnet ein FRISCHES
    # aktives Segment mit neuer Manifest-Zeile.
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_QUARANTINED

    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    await store.append([_event(1, "2026-01-01T00:00:00.000Z"), _event(2, "2026-01-01T00:00:01.000Z")])
    old_active = await store.manifest.get_active_segment()
    old_id = old_active.segment_id
    old_filename = old_active.filename
    await store.close()

    # Datei des aktiven Segments (inkl. Sidecars) entfernen, als wäre sie verschwunden.
    seg_path = store._segments_dir / old_filename
    for p in (seg_path, Path(f"{seg_path}-wal"), Path(f"{seg_path}-shm")):
        if p.exists():
            p.unlink()

    # Wieder öffnen: darf KEINE leere Ersatz-DB unter dem alten Namen anlegen.
    store2 = SqliteSegmentStore(root)
    await store2.open()
    try:
        # (a) Keine leere Ersatz-DB unter dem alten aktiven Dateinamen.
        assert not (store2._segments_dir / old_filename).exists()

        # (b) Das alte (fehlende) aktive Segment ist nicht mehr aktiv, sondern als
        #     verloren markiert (quarantäniert) – das Manifest behauptet keine lebenden
        #     Zeilen mehr, die es nicht mehr gibt.
        old_after = await store2.manifest.get_segment(old_id)
        assert old_after is not None
        assert old_after.status == SEGMENT_STATUS_QUARANTINED

        # (c) Ein FRISCHES aktives Segment existiert, ist ein anderes und hat eine
        #     eigene, real existierende Datei.
        new_active = await store2.manifest.get_active_segment()
        assert new_active is not None
        assert new_active.status == SEGMENT_STATUS_ACTIVE
        assert new_active.segment_id != old_id
        assert new_active.filename != old_filename
        assert (store2._segments_dir / new_active.filename).exists()

        # (d) Der Store ist funktionsfähig: Append/Query gehen auf das frische Segment.
        await store2.append([_event(3, "2026-01-02T00:00:00.000Z")])
        rows = await store2.query(StoreQuery(limit=50))
        assert 3 in {r["new_value"] for r in rows}
    finally:
        await store2.close()


async def test_reopen_with_present_active_segment_is_unchanged(tmp_path: Path):
    # Regression-Guard: Ist die Datei des aktiven Segments beim Wiederöffnen VORHANDEN,
    # bleibt es unverändert das aktive Segment (kein neues Segment, keine Quarantäne)
    # und die alten Zeilen sind weiter lesbar.
    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    old_active = await store.manifest.get_active_segment()
    await store.close()

    store2 = SqliteSegmentStore(root)
    await store2.open()
    try:
        active = await store2.manifest.get_active_segment()
        assert active.segment_id == old_active.segment_id
        assert active.filename == old_active.filename
        rows = await store2.query(StoreQuery(limit=50))
        assert 1 in {r["new_value"] for r in rows}
    finally:
        await store2.close()


# ---------------------------------------------------------------------------
# (Codex :1837) Transiente Checkpoint-Fehler NICHT quarantänieren
# ---------------------------------------------------------------------------


async def test_run_pending_checkpoints_keeps_pending_on_transient_error(store: SqliteSegmentStore, monkeypatch):
    # Ein GESUNDES pending-Segment trifft einen TRANSIENTEN Fehler (locked/busy).
    # Es darf NICHT quarantäniert werden (sonst aus Queries versteckt UND
    # retention-eligible → Verlust gültiger Historie), sondern pending bleiben.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
    await store.rotate()
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_CHECKPOINT_PENDING

    assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_CHECKPOINT_PENDING

    import obs.ringbuffer.store.sqlite_backend as backend

    async def _transient(_conn):
        raise backend.aiosqlite.OperationalError("database is locked")

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _transient)
    recovered = await store.run_pending_checkpoints()

    assert recovered == 0
    seg = await store.manifest.get_segment(pending_id)
    # Transienter Fehler ⇒ Segment bleibt checkpoint_pending (kein quarantined).
    assert seg.status == SEGMENT_STATUS_CHECKPOINT_PENDING


async def test_run_pending_checkpoints_quarantines_corrupt_segment(store: SqliteSegmentStore, monkeypatch):
    # Kontrapunkt: echte Korruption wird weiterhin quarantäniert (Isolation bleibt).
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    pending_id = (await store.manifest.get_active_segment()).segment_id

    async def _busy(_conn):
        return False

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _busy)
    await store.rotate()

    import obs.ringbuffer.store.sqlite_backend as backend

    async def _corrupt(_conn):
        raise backend.aiosqlite.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(store, "_try_truncate_checkpoint", _corrupt)
    await store.run_pending_checkpoints()

    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_QUARANTINED

    assert (await store.manifest.get_segment(pending_id)).status == SEGMENT_STATUS_QUARANTINED


# ---------------------------------------------------------------------------
# (Codex :2126) Sidecars behalten, wenn Basis-Unlink scheitert
# ---------------------------------------------------------------------------


def test_unlink_with_sidecars_keeps_sidecars_when_base_fails(tmp_path: Path, monkeypatch):
    # Basisdatei + beide Sidecars existieren; das Basis-Unlink scheitert
    # (Permission/Lock). Ohne Fix würden -wal/-shm trotzdem entfernt → ein
    # behaltenes Segment verlöre ungecheckpointete Frames. Mit Fix bleiben die
    # Sidecars erhalten (Datenerhalt für den Retry).
    base = tmp_path / "seg.db"
    wal = tmp_path / "seg.db-wal"
    shm = tmp_path / "seg.db-shm"
    for p in (base, wal, shm):
        p.write_bytes(b"data")

    real_unlink = Path.unlink

    def _failing_base_unlink(self: Path, *args, **kwargs):
        if self == base:
            raise OSError("device or resource busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _failing_base_unlink)

    removed = SqliteSegmentStore._unlink_with_sidecars(base)

    assert removed is False
    assert base.exists()
    # Kern der Regression: die Sidecars bleiben, weil die Basis nicht gelöscht wurde.
    assert wal.exists()
    assert shm.exists()


def test_unlink_with_sidecars_removes_sidecars_when_base_removed(tmp_path: Path):
    # Positivfall: Basis geht weg → Sidecars werden ebenfalls entfernt.
    base = tmp_path / "seg.db"
    wal = tmp_path / "seg.db-wal"
    shm = tmp_path / "seg.db-shm"
    for p in (base, wal, shm):
        p.write_bytes(b"data")

    removed = SqliteSegmentStore._unlink_with_sidecars(base)

    assert removed is True
    assert not base.exists()
    assert not wal.exists()
    assert not shm.exists()


# ---------------------------------------------------------------------------
# (Codex #951, Pkt 1) Gefilterter Legacy-Export nicht per Roh-Kandidatenzahl kappen
# ---------------------------------------------------------------------------


def _build_legacy_sparse_matches(path: Path, *, total: int, match_rowids: set[int]) -> None:
    """Legacy-Single-DB: nur ``match_rowids`` tragen ``new_value='MATCH'``.

    rowid = 1..total in Insert-/ts-Reihenfolge (aufsteigend). Bei ``sort desc``
    (neueste zuerst) liegen niedrige rowids (alte Zeilen) hinten – ein Roh-Cap auf
    die neuesten k Zeilen schnitte alte Treffer ab.
    """
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
        for rowid in range(1, total + 1):
            value = "MATCH" if rowid in match_rowids else f"other-{rowid}"
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"2025-01-01T00:{rowid // 60:02d}:{rowid % 60:02d}.000Z",
                    "dp-legacy",
                    "dp/dp-legacy/value",
                    None,
                    json.dumps(value),
                    "legacy",
                    "good",
                ),
            )
        conn.commit()
    finally:
        conn.close()


async def test_legacy_export_returns_all_matches_beyond_raw_cap(store: SqliteSegmentStore, tmp_path: Path):
    # Nur die 5 ÄLTESTEN Zeilen (rowid 1..5) matchen; 45 neuere Zeilen matchen nicht.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy_sparse_matches(db, total=50, match_rowids={1, 2, 3, 4, 5})
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    # Export-Semantik: candidate_cap == offset + limit (die vom CSV-Export gesetzte,
    # mit dem Fenster wachsende Deckelung). Ohne Fix fetchte der Legacy-Reader nur die
    # neuesten 5 ROH-Zeilen (rowid 50..46, allesamt Nicht-Treffer) → 0 Matches → der
    # Export-Loop stoppte, obwohl 5 ältere Zeilen matchen.
    query = StoreQuery(limit=5, offset=0, candidate_cap=5, sort_field="id", sort_order="desc", value_filters=value_filters)
    rows = await store.query(query)
    assert {r["new_value"] for r in rows} == {"MATCH"}
    assert len(rows) == 5


async def test_legacy_export_paginates_full_matched_window(store: SqliteSegmentStore, tmp_path: Path):
    # 12 Treffer, verstreut; der Export paginiert in 5er-Chunks über die gefilterte Menge.
    db = tmp_path / "obs_ringbuffer.db"
    match_rowids = {1, 2, 3, 20, 21, 22, 40, 41, 42, 60, 61, 62}
    _build_legacy_sparse_matches(db, total=80, match_rowids=match_rowids)
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    collected: list[Any] = []
    offset = 0
    chunk = 5
    while True:
        query = StoreQuery(limit=chunk, offset=offset, candidate_cap=offset + chunk, sort_field="id", sort_order="desc", value_filters=value_filters)
        page = await store.query(query)
        if not page:
            break
        collected.extend(page)
        offset += len(page)
        if len(page) < chunk:
            break

    # Alle 12 Treffer wurden über die Paginierung eingesammelt, keiner ging verloren.
    assert len(collected) == 12
    assert all(r["new_value"] == "MATCH" for r in collected)


async def test_legacy_monitor_cap_stays_bounded_below_offset_plus_limit(store: SqliteSegmentStore, tmp_path: Path):
    # Monitor-Live-View: candidate_cap ist der (große) Roh-Scan-Budget-Cap und ÜBERSTEIGT
    # offset+limit. Er behält sein deckelndes Verhalten – ein Treffer JENSEITS der neuesten
    # cap-Roh-Zeilen wird bewusst NICHT gefunden (bounded Scan, kein Full-Scan über Legacy).
    db = tmp_path / "obs_ringbuffer.db"
    # Ein Treffer nur ganz am Anfang (rowid 1 = ältester), sonst nichts.
    _build_legacy_sparse_matches(db, total=50, match_rowids={1})
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    # cap=10 (Scan-Budget) > offset+limit (0+5) → Monitor-Modus: nur die neuesten 10 Roh-
    # Zeilen werden betrachtet, der älteste Treffer (rowid 1) liegt außerhalb und fehlt.
    query = StoreQuery(limit=5, offset=0, candidate_cap=10, sort_field="id", sort_order="desc", value_filters=value_filters)
    rows = await store.query(query)
    assert rows == []
