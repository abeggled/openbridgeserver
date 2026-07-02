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
