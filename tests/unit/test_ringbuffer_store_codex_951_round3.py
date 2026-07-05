"""Codex-Findings Runde 3 am RingBuffer (#919, PR #951) – 1x P1, 6x P2.

Ein Test (bzw. eine kleine Gruppe) je Finding, TDD-first: reproduziert den Bug
ohne Fix und wird durch den Fix grün.

1. [P1] ``_record_segmented_locked``: nach der VOR-Append-Age-Rotation läuft
   ``enforce_retention()`` (zeitgetriebene Default-Rotation ist bei niedrigem
   Traffic der EINZIGE Rotationspfad – ohne enforce sammeln sich geschlossene
   Segmente an).
2. [P2] Korruptes AKTIVES Segment beim Startup: als quarantined markieren und ein
   frisches aktives Segment eröffnen, statt den ganzen Startup zu blockieren.
3. [P2] Freitext-``q`` über Legacy bounded halten (Zeitfenster/Candidate-Cap),
   kein unwindowed Full-Scan über die 20–30 GB Legacy-Datei.
4. [P2] Live-Query vs. Export über EXPLIZITES ``StoreQuery.is_export``-Flag
   unterscheiden, nicht über die ``candidate_cap <= offset+limit``-Heuristik.
5. [P2] CSV-Export (v2) guarded-Filter batch-scannt bis genug Matches, statt den
   guarded-Filter erst NACH dem inneren LIMIT anzuwenden (leere Chunks stoppen
   den Export sonst still).
6. [P2] ``migrating``-Chunks zählen NICHT als non-legacy-Historie (read-
   ausgeschlossen) → der No-Zero-History-Guard schützt die attached Legacy-Quelle
   während der Migration.
7. [P2] Age-/Row-Retention laufen durch dieselbe Guard-/Victim-Logik wie die
   Size-Retention: gesundes Legacy ist age/row-retention-fähig, quarantäniertes
   Legacy bleibt unter dem Guard geschützt.
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
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


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


def _build_legacy(path: Path, rows: list[tuple[str, Any]], *, dp: str = "dp-legacy", adapter: str = "legacy") -> None:
    """Legacy-Single-DB mit rowid = 1..n (Insert-Reihenfolge), ``(ts, new_value)``."""
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
                (ts, dp, f"dp/{dp}/value", None, json.dumps(value), adapter, "good"),
            )
        conn.commit()
    finally:
        conn.close()


# ===========================================================================
# (1) [P1] enforce_retention nach VOR-Append-Age-Rotation
# ===========================================================================


async def test_pre_append_age_rotation_enforces_retention(tmp_path: Path, monkeypatch):
    # Zeitgetriebene Default-Rotation bei niedrigem Traffic: die Age-Rotation läuft
    # VOR dem Append. Ohne enforce_retention() in diesem Zweig sammeln sich
    # geschlossene Segmente an, obwohl das harte max_file_size_bytes-Budget längst
    # gerissen ist. Der Fix ruft nach der VOR-Append-Age-Rotation enforce_retention().
    from obs.ringbuffer.ringbuffer import RingBuffer

    rb = RingBuffer(storage="disk", segmented=True, segment_max_age=1)
    store = SqliteSegmentStore(
        tmp_path / "root",
        retention=StoreRetentionConfig(max_file_size_bytes=1),
    )
    await store.open()
    rb._store = store
    rb._segment_max_bytes = None
    rb._segment_max_rows = None

    try:
        calls = {"enforce": 0}
        real_enforce = store.enforce_retention

        async def _counting_enforce():
            calls["enforce"] += 1
            return await real_enforce()

        monkeypatch.setattr(store, "enforce_retention", _counting_enforce)

        # Post-Append-Rotation NICHT auslösen (nur die VOR-Append-Age-Rotation testen).
        async def _no_post_rotation():
            return False

        monkeypatch.setattr(rb, "_segment_rotation_due", _no_post_rotation)

        # Kein attached Legacy → der elif-Zweig (Post-Upgrade-enforce) läuft nicht;
        # nur die VOR-Append-Age-Rotation kann enforce auslösen.
        async def _no_legacy():
            return False

        monkeypatch.setattr(rb, "_has_attached_legacy_segment", _no_legacy)

        # Erstes Event: aktives Segment nicht leer (Age-Rotation rotiert nur ein
        # nicht-leeres Segment). created_at in die Vergangenheit setzen.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        async with rb._lock:
            await rb._record_segmented_locked("2026-01-01T00:00:00.000Z", "dp-1", "dp/dp-1/value", None, 1, "api", "good", 1, {})
        calls["enforce"] = 0  # ab hier zählen

        # Zweites Event: das aktive Segment ist jetzt "über" der Age-Grenze → VOR-
        # Append-Age-Rotation greift und MUSS enforce_retention() nachziehen.
        rb._segment_created_at = "2000-01-01T00:00:00.000Z"
        async with rb._lock:
            await rb._record_segmented_locked("2026-01-01T00:00:01.000Z", "dp-1", "dp/dp-1/value", None, 2, "api", "good", 1, {})
        assert calls["enforce"] >= 1
    finally:
        await store.close()


# ===========================================================================
# (2) [P2] Korruptes aktives Segment beim Startup recovern
# ===========================================================================


async def test_reopen_with_corrupt_active_segment_recovers(tmp_path: Path):
    # Ein Manifest mit einem AKTIVEN Segment wird wieder geöffnet, NACHDEM die Datei
    # des aktiven Segments korrupt (überschrieben, keine gültige SQLite-DB) wurde.
    # Ohne Fix scheitert open() an _open_segment_conn(active.filename) und blockiert
    # den ganzen RingBuffer-/OBS-Startup. Mit Fix wird das korrupte aktive Segment
    # als quarantined markiert und ein frisches aktives Segment eröffnet.
    from obs.ringbuffer.store.manifest import SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_QUARANTINED

    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    await store.append([_event(1, "2026-01-01T00:00:00.000Z"), _event(2, "2026-01-01T00:00:01.000Z")])
    old_active = await store.manifest.get_active_segment()
    old_id = old_active.segment_id
    old_filename = old_active.filename
    await store.close()

    # Datei des aktiven Segments mit Müll überschreiben (inkl. Sidecars entfernen),
    # sodass sie existiert, aber keine gültige SQLite-DB ist.
    seg_path = store._segments_dir / old_filename
    for p in (Path(f"{seg_path}-wal"), Path(f"{seg_path}-shm")):
        if p.exists():
            p.unlink()
    seg_path.write_bytes(b"this is not a sqlite database at all" * 4)

    store2 = SqliteSegmentStore(root)
    # Ohne Fix wirft dieser open() – der Startup wäre blockiert.
    await store2.open()
    try:
        # (a) Das alte korrupte aktive Segment ist nicht mehr aktiv, sondern quarantäniert.
        old_after = await store2.manifest.get_segment(old_id)
        assert old_after is not None
        assert old_after.status == SEGMENT_STATUS_QUARANTINED

        # (b) Ein frisches aktives Segment existiert und ist funktionsfähig.
        new_active = await store2.manifest.get_active_segment()
        assert new_active is not None
        assert new_active.status == SEGMENT_STATUS_ACTIVE
        assert new_active.segment_id != old_id
        assert (store2._segments_dir / new_active.filename).exists()

        await store2.append([_event(3, "2026-01-02T00:00:00.000Z")])
        rows = await store2.query(StoreQuery(limit=50))
        assert 3 in {r["new_value"] for r in rows}
    finally:
        await store2.close()


async def test_reopen_with_healthy_active_segment_unchanged(tmp_path: Path):
    # Regression-Guard: ein gesundes aktives Segment bleibt beim Wiederöffnen aktiv
    # (kein Quarantäne-/Neuanlage-Pfad), Zeilen bleiben lesbar.
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


async def test_corrupt_active_probe_reraises_non_corruption_error(tmp_path: Path, monkeypatch):
    # Ist der Probe-Fehler KEINE echte SQLite-Korruption (z. B. Permission/Programmier-
    # fehler), darf er NICHT als Korruption maskiert werden: das aktive Segment bleibt
    # unangetastet und der Fehler propagiert (kein stiller Neuanlage-Pfad).
    import aiosqlite

    root = tmp_path / "root"
    store = SqliteSegmentStore(root)
    await store.open()
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])
    await store.close()

    store2 = SqliteSegmentStore(root)
    boom = aiosqlite.OperationalError("disk I/O error, not corruption")

    async def _fail_probe(_filename: str):
        raise boom

    monkeypatch.setattr(store2, "_open_segment_conn", _fail_probe)
    with pytest.raises(aiosqlite.OperationalError, match="not corruption"):
        await store2.open()
    # Aufräumen (open() hat im Fehlerpfad Lease/Manifest bereits freigegeben).


# ===========================================================================
# (3) [P2] Freitext-q über Legacy bounden
# ===========================================================================


async def test_legacy_freetext_q_scan_is_bounded(store: SqliteSegmentStore, tmp_path: Path):
    # Ein Freitext-``q`` matcht datapoint_id/source_adapter per LIKE '%...%' und kann
    # die Index-Scans nicht nutzen. Ohne Cap scannte er die ganze Legacy-DB. Der Fix
    # deckelt den Legacy-Kandidaten-Scan auf candidate_cap Roh-Zeilen (Monitor-Modus):
    # ein Treffer JENSEITS der neuesten cap-Zeilen wird bewusst NICHT gefunden.
    db = tmp_path / "obs_ringbuffer.db"
    # rowid 1 (ältestes) trägt den einzigen q-Treffer über datapoint_id.
    rows = [(f"2025-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z", i) for i in range(1, 51)]
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE ringbuffer (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, datapoint_id TEXT NOT NULL,
                   topic TEXT NOT NULL, old_value TEXT, new_value TEXT, source_adapter TEXT NOT NULL,
                   quality TEXT NOT NULL, metadata_version INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}')"""
        )
        for rowid, (ts, value) in enumerate(rows, start=1):
            dp = "NEEDLE-dp" if rowid == 1 else f"other-{rowid}"
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?,?,?,?,?,?,?)",
                (ts, dp, f"dp/{dp}/value", None, json.dumps(value), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # q-Treffer liegt bei rowid 1 (ältestes). Mit candidate_cap=10 (Monitor) wird nur
    # das neueste Fenster betrachtet → der alte Treffer liegt außerhalb, kein Full-Scan.
    query = StoreQuery(limit=5, candidate_cap=10, sort_field="id", sort_order="desc", q="NEEDLE")
    rows_out = await store.query(query)
    assert rows_out == []


async def test_legacy_freetext_q_windowed_still_matches(store: SqliteSegmentStore, tmp_path: Path):
    # Regression-Guard: mit Zeitfenster (bounded) findet der Freitext-q seine Treffer.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", 1)], dp="NEEDLE-dp")
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    query = StoreQuery(
        limit=10,
        from_ts="2024-12-31T00:00:00.000Z",
        to_ts="2025-01-02T00:00:00.000Z",
        q="NEEDLE",
    )
    rows_out = await store.query(query)
    assert {r["new_value"] for r in rows_out} == {1}


async def test_legacy_dp_ids_by_name_and_q_or_combined(store: SqliteSegmentStore, tmp_path: Path):
    # dp_ids_by_name (index-taugliches SQL-IN) OR-verknüpft mit dem bounded Python-q.
    # Eine per Namen aufgelöste datapoint_id matcht über den IN-Zweig, auch wenn ihr
    # datapoint_id/source_adapter den q-Teilstring NICHT enthält.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(
        db,
        [("2025-01-01T00:00:00.000Z", 10), ("2025-01-01T00:00:01.000Z", 20)],
        dp="resolved-dp",
    )
    # Zweite Zeile mit anderem dp, das den q-Teilstring trägt.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?,?,?,?,?,?,?)",
            ("2025-01-01T00:00:02.000Z", "NEEDLE-other", "dp/NEEDLE-other/value", None, json.dumps(30), "legacy", "good"),
        )
        conn.commit()
    finally:
        conn.close()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    # q="NEEDLE" (Teilstring-Match) OR dp_ids_by_name=["resolved-dp"] (IN-Match).
    query = StoreQuery(
        limit=10,
        from_ts="2024-12-31T00:00:00.000Z",
        to_ts="2025-01-02T00:00:00.000Z",
        q="NEEDLE",
        dp_ids_by_name=["resolved-dp"],
    )
    rows_out = await store.query(query)
    # resolved-dp (10, 20) über den Namens-Zweig + NEEDLE-other (30) über den q-Zweig.
    assert {r["new_value"] for r in rows_out} == {10, 20, 30}


async def test_legacy_dp_ids_by_name_only_uses_sql_in(store: SqliteSegmentStore, tmp_path: Path):
    # Ohne ``q`` bleibt der reine ``dp_ids_by_name``-Teil index-tauglich als SQL-``IN``
    # (kein Scan-Risiko) und selektiert genau die aufgelösten Datapoints.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", 10)], dp="resolved-dp")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?,?,?,?,?,?,?)",
            ("2025-01-01T00:00:01.000Z", "other-dp", "dp/other-dp/value", None, json.dumps(20), "legacy", "good"),
        )
        conn.commit()
    finally:
        conn.close()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    query = StoreQuery(limit=10, dp_ids_by_name=["resolved-dp"])
    rows_out = await store.query(query)
    assert {r["new_value"] for r in rows_out} == {10}


# ===========================================================================
# (4) [P2] Explizites is_export-Flag statt Cap-Heuristik
# ===========================================================================


def test_store_query_has_is_export_flag_defaulting_false():
    # Der Contract trägt ein explizites is_export-Flag; Default False (Live-Query).
    assert StoreQuery(limit=10).is_export is False
    assert StoreQuery(limit=10, is_export=True).is_export is True


async def test_live_query_high_offset_stays_bounded_not_export(store: SqliteSegmentStore, tmp_path: Path):
    # Live-Query mit hohem limit/offset (candidate_cap == offset+limit >= 10000) darf
    # NICHT als Export eingestuft werden. Ohne is_export-Flag stufte die alte Heuristik
    # (candidate_cap <= offset+limit) sie fälschlich als Export ein und scannte die
    # ganze Legacy-Datei. Mit dem Flag bleibt sie bounded (genau EIN gedeckelter Batch).
    db = tmp_path / "obs_ringbuffer.db"
    # Ein Value-Post-Filter-Treffer nur ganz am Anfang (rowid 1 = ältester).
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE ringbuffer (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, datapoint_id TEXT NOT NULL,
                   topic TEXT NOT NULL, old_value TEXT, new_value TEXT, source_adapter TEXT NOT NULL,
                   quality TEXT NOT NULL, metadata_version INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}')"""
        )
        for rowid in range(1, 51):
            value = "MATCH" if rowid == 1 else f"other-{rowid}"
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?,?,?,?,?,?,?)",
                (f"2025-01-01T00:00:{rowid % 60:02d}.000Z", "dp-legacy", "dp/dp-legacy/value", None, json.dumps(value), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    # is_export=False + candidate_cap == offset+limit (10). Monitor-Modus: nur die
    # neuesten 10 Roh-Zeilen; der Treffer bei rowid 1 liegt außerhalb → leer.
    query = StoreQuery(
        limit=5,
        offset=5,
        candidate_cap=10,
        is_export=False,
        sort_field="id",
        sort_order="desc",
        value_filters=value_filters,
    )
    rows_out = await store.query(query)
    assert rows_out == []


async def test_export_flag_returns_all_matches_beyond_raw_cap(store: SqliteSegmentStore, tmp_path: Path):
    # Mit is_export=True liefert der Legacy-Reader die vollständige gematchte Menge
    # (batch-scan bis Fenster voll/Segment erschöpft), auch wenn die Treffer die
    # ältesten Zeilen sind – unabhängig davon, ob candidate_cap <= offset+limit ist.
    db = tmp_path / "obs_ringbuffer.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE ringbuffer (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, datapoint_id TEXT NOT NULL,
                   topic TEXT NOT NULL, old_value TEXT, new_value TEXT, source_adapter TEXT NOT NULL,
                   quality TEXT NOT NULL, metadata_version INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}')"""
        )
        for rowid in range(1, 51):
            value = "MATCH" if rowid in {1, 2, 3, 4, 5} else f"other-{rowid}"
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?,?,?,?,?,?,?)",
                (f"2025-01-01T00:00:{rowid % 60:02d}.000Z", "dp-legacy", "dp/dp-legacy/value", None, json.dumps(value), "legacy", "good"),
            )
        conn.commit()
    finally:
        conn.close()
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    query = StoreQuery(
        limit=5,
        offset=0,
        candidate_cap=5,
        is_export=True,
        sort_field="id",
        sort_order="desc",
        value_filters=value_filters,
    )
    rows_out = await store.query(query)
    assert {r["new_value"] for r in rows_out} == {"MATCH"}
    assert len(rows_out) == 5


# ===========================================================================
# (5) [P2] CSV-Export (v2) guarded-Filter batch-scannen
# ===========================================================================


async def test_v2_export_guarded_returns_matches_beyond_inner_limit(store: SqliteSegmentStore):
    # v2-Segment, contains ohne Zeitfenster (guarded, candidate_cap-gebunden). Die
    # ersten (neuesten) candidate_cap Roh-Zeilen matchen NICHT, ältere schon. Ohne
    # Fix wendet der v2-guarded-Pfad das Prädikat erst NACH dem inneren LIMIT an →
    # leerer Chunk, Export stoppt. Mit is_export=True batch-scannt er bis genug Matches.
    matches = {1, 2, 3}  # global_event_ids der ältesten Zeilen matchen
    events = []
    for i in range(1, 51):
        value = "MATCH" if i in matches else f"other-{i}"
        events.append(_event(value, f"2026-01-01T00:00:{i % 60:02d}.{i:03d}Z"))
    await store.append(events)

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    # Export: candidate_cap klein (5), aber is_export=True → vollständige Matches.
    query = StoreQuery(
        limit=5,
        offset=0,
        candidate_cap=5,
        is_export=True,
        sort_field="id",
        sort_order="desc",
        value_filters=value_filters,
    )
    rows_out = await store.query(query)
    assert {r["new_value"] for r in rows_out} == {"MATCH"}
    assert len(rows_out) == 3


async def test_v2_live_guarded_stays_capped(store: SqliteSegmentStore):
    # Regression-Guard: der v2-Live-guarded-Pfad (is_export=False) bleibt hart auf
    # die neuesten candidate_cap Zeilen gedeckelt – ein Treffer nur in den ÄLTESTEN
    # Zeilen wird bewusst NICHT gefunden.
    events = []
    for i in range(1, 51):
        value = "MATCH" if i == 1 else f"other-{i}"
        events.append(_event(value, f"2026-01-01T00:00:{i % 60:02d}.{i:03d}Z"))
    await store.append(events)

    value_filters = [{"operator": "contains", "field": "new_value", "value": "MATCH"}]
    query = StoreQuery(
        limit=5,
        offset=0,
        candidate_cap=10,
        is_export=False,
        sort_field="id",
        sort_order="desc",
        value_filters=value_filters,
    )
    rows_out = await store.query(query)
    assert rows_out == []


# ===========================================================================
# (6) [P2] migrating-Chunks nicht als non-legacy zählen
# ===========================================================================


async def _attach_legacy_blob(store: SqliteSegmentStore, size_bytes: int) -> tuple[int, Path]:
    legacy_file = store._root / "legacy_source.db"
    legacy_file.write_bytes(b"\x00" * size_bytes)
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=size_bytes)
    return rec.segment_id, legacy_file


async def test_age_retention_reclaims_healthy_legacy_under_guard(store: SqliteSegmentStore, tmp_path: Path):
    # Upgrade mit NUR max_age: die attached gesunde Legacy-DB (status='legacy') fehlt
    # in list_retention_eligible_segments() und wurde daher vom Age-Pfad NIE
    # zurückgewonnen. Sobald frische v2-Daten existieren (Guard erfüllt) und die
    # Legacy-to_ts bekannt und alt genug ist, muss die alte Legacy-DB age-retention-
    # fähig sein (durch dieselbe guard-geschützte Victim-Route wie Size-Retention).
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2000-01-01T00:00:00.000Z", 1)])  # sehr alt
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())
    legacy_seg = (await store.manifest.list_legacy_segments())[0]
    # Legacy-Bounds bekannt + alt (attach_readonly scannt bewusst nicht; hier gesetzt).
    await store.manifest.update_segment_stats(
        legacy_seg.segment_id,
        row_count=1,
        size_bytes=legacy_seg.size_bytes,
        from_ts="2000-01-01T00:00:00.000Z",
        to_ts="2000-01-01T00:00:00.000Z",
    )

    # Frische v2-Daten sichern den Guard.
    await store.append([_event(2, "2026-01-01T00:00:00.000Z")])

    store._retention_config = StoreRetentionConfig(max_age=1)  # alles vor ~jetzt fällt
    removed = await store.enforce_retention()
    # Die alte Legacy-DB wurde zurückgewonnen.
    assert await store.manifest.get_segment(legacy_seg.segment_id) is None
    assert removed >= 1


async def test_age_retention_protects_quarantined_legacy_under_guard(store: SqliteSegmentStore):
    # Kontrapunkt: ein quarantäniertes Legacy ist die EINZIGE Datenquelle (Guard
    # NICHT erfüllt). Der Age-Pfad iteriert list_retention_eligible_segments()
    # (schließt quarantäniertes Legacy EIN) – ohne Guard würde es gelöscht
    # (Datenverlust). Mit dem Guard bleibt es geschützt.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024)
    # Alt genug für den Age-Cutoff.
    await store.manifest.update_segment_stats(
        legacy_id, row_count=1, size_bytes=8 * 1024 * 1024, from_ts="2000-01-01T00:00:00.000Z", to_ts="2000-01-01T00:00:00.000Z"
    )
    await store.manifest.mark_quarantined(legacy_id, reason="malformed database disk image")
    assert await store._has_nonlegacy_data_segment() is False

    store._retention_config = StoreRetentionConfig(max_age=1)
    removed = await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is not None
    assert legacy_file.exists()
    assert removed == 0


async def test_row_retention_protects_quarantined_legacy_under_guard(store: SqliteSegmentStore):
    # Wie oben, aber Row-Budget-Pfad: ein quarantäniertes Legacy als einzige Quelle
    # bleibt unter dem Guard geschützt.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 8 * 1024 * 1024)
    await store.manifest.update_segment_stats(legacy_id, row_count=100, size_bytes=8 * 1024 * 1024, from_ts=None, to_ts=None)
    await store.manifest.mark_quarantined(legacy_id, reason="malformed database disk image")
    assert await store._has_nonlegacy_data_segment() is False

    # max_entries=1, aber die Legacy hält 100 Zeilen → Row-Budget wäre gerissen,
    # DOCH der Guard schützt das quarantänierte Legacy (einzige Quelle).
    store._retention_config = StoreRetentionConfig(max_entries=1)
    removed = await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is not None
    assert legacy_file.exists()
    assert removed == 0


async def test_row_retention_reclaims_healthy_legacy_under_guard(store: SqliteSegmentStore, tmp_path: Path):
    # Upgrade mit NUR max_entries: gesundes Legacy muss row-retention-fähig sein,
    # sobald frische v2-Daten den Guard erfüllen.
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2000-01-01T00:00:00.000Z", 1)])
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())
    legacy_seg = (await store.manifest.list_legacy_segments())[0]
    # Legacy hält (bekannt) mehrere Zeilen → trägt zum Row-Budget bei (attach_readonly
    # scannt bewusst nicht; hier gesetzt).
    await store.manifest.update_segment_stats(legacy_seg.segment_id, row_count=5, size_bytes=legacy_seg.size_bytes, from_ts=None, to_ts=None)

    await store.append([_event(2, "2026-01-01T00:00:00.000Z")])

    store._retention_config = StoreRetentionConfig(max_entries=1)
    await store.enforce_retention()
    # Legacy (älteste Quelle) wurde unter Row-Druck zurückgewonnen (guard erfüllt).
    assert await store.manifest.get_segment(legacy_seg.segment_id) is None
