"""Codex-P2 Runde 29 (migration.py): drei Findings – gid-Ordnung, WAL-Marker, Seal-Lock.

Dieses Modul deckt die drei Runde-29-Findings ab (je TDD zuerst rot, dann gruen):

* **Finding 1 (:103) – „Preserve source order when assigning migrated gids":** die
  Ordnungs-Komponente einer migrierten ``global_event_id`` wird aus dem Manifest-
  ``segment_id`` der Legacy-Quelle abgeleitet (gespiegelt), NICHT mehr aus einem blake2b-
  Pfad-Hash. So bleibt die Cross-Source-``id desc``-Ordnung NACH dem Detach beider Quellen
  konsistent mit dem attached-read-Zustand (neuere Quelle = hoehere segment_id = zuerst).
  Disjunkte Buckets, strikte Negativitaet und JS-Safety bleiben erhalten; die Resume-
  Idempotenz (pro-Quelle-Floor) bricht nicht.
* **Finding 2 (:292) – „Include WAL sidecars in migrated markers":** die Marker-Identitaet
  erfasst zusaetzlich mtime+size des ``-wal`` (und ``-shm``). Eine WAL-only-Aenderung (neue
  Zeilen im legacy-file-backed Modus, Hauptdatei unveraendert) erkennt der Marker als STALE
  → Re-Attach → neue Zeilen sichtbar. Rueckwaertskompat: ein Alt-Marker im Haupt-nur-Format
  behaelt seine Semantik; ein unveraenderter Zustand unterdrueckt weiterhin.
* **Finding 3 (:813) – „Serialize final migrated-segment sealing":** der finale Seal des
  rein-negativen aktiven Segments (``_seal_pure_migrated_active_segment`` → ``rotate``) laeuft
  unter demselben ``write_lock`` wie ``record()``. Ein Live-Append kann so nicht zwischen
  Lock-Freigabe und Seal eine positive Zeile ins rein-negative Segment mischen.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.migration import LegacyMigrator, _mirror_segment_id, _source_factor_from_path
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

JS_MAX = 2**53 - 1  # groesster exakt als IEEE-754-Double darstellbarer Integer


@pytest.fixture
async def store(tmp_path: Path):
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


def _build_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
    """Legacy-Single-DB im ALTEN Format mit AUTOINCREMENT-rowid; ``rows`` = ``(ts, value)``."""
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


def _pick_pathhash_inverted_pair(base: Path) -> tuple[Path, Path]:
    """Waehlt zwei Quellpfade, deren blake2b-Pfad-Faktor die Attach-Reihenfolge invertiert.

    Liefert ``(older, newer)`` so, dass ``_source_factor_from_path(older) < _source_factor_from_path(newer)``:
    unter dem alten Pfad-Hash-Schema traegt ``older`` (zuerst attached) den kleineren Faktor
    (weniger negativ) und wuerde faelschlich VOR ``newer`` sortieren. Der Pfad-Hash haengt am
    absoluten (tmp-)Pfad, daher zur Laufzeit ueber Kandidaten gesucht statt hartkodiert.
    """
    candidates = [base / f"src_{i}.db" for i in range(200)]
    ranked = sorted(candidates, key=_source_factor_from_path)
    return ranked[0], ranked[-1]


def _live_event(value: object, ts: str) -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id="dp-live",
        topic="dp/dp-live/value",
        old_value=None,
        new_value=value,
        source_adapter="api",
        quality="good",
    )


# ===========================================================================
# Finding 1 – gid-Ordnung aus segment_id (statt Pfad-Hash)
# ===========================================================================


async def test_two_detached_sources_keep_attached_cross_source_order(store: SqliteSegmentStore, tmp_path: Path):
    """Zwei voll migrierte + detachte Quellen: ``id desc`` liefert die NEUERE VOR der aelteren.

    Die zuerst attachte Quelle bekommt die niedrigere segment_id (= aelter), die zweite die
    hoehere (= neuer) – identisch zum read-only-Retention-/Ordnungsvertrag. Nach dem Detach
    BEIDER Quellen (rein migrierte v2-Segmente) muss die ``id desc``-Ordnung dieselbe sein
    wie im attached-Zustand: ALLE Zeilen der neueren Quelle vor allen der aelteren. Vor dem
    Fix bestimmte ein blake2b-Pfad-Hash die Cross-Source-Ordnung und konnte die aeltere
    Quelle nach vorne ziehen.
    """
    # Dateinamen BEWUSST so waehlen, dass der blake2b-Pfad-Hash die Attach-Reihenfolge
    # INVERTIERT: die zuerst attachte (aeltere) Quelle bekommt den KLEINEREN Pfad-Faktor
    # (=> weniger negativ => wuerde unter dem alten Pfad-Hash-Schema faelschlich ZUERST
    # sortieren). Nur der segment_id-basierte Fix stellt die korrekte Ordnung her – so ist
    # dieser Test ein robuster Red-Proof (schlaegt auf dem alten Schema fehl) UND Green-Proof.
    older, newer = _pick_pathhash_inverted_pair(tmp_path)
    _build_legacy(older, [(f"2024-01-{i + 1:02d}T00:00:00.000Z", 10 + i) for i in range(4)])
    _build_legacy(newer, [(f"2025-06-{i + 1:02d}T00:00:00.000Z", 100 + i) for i in range(4)])
    # Vorbedingung: Pfad-Hash-Faktor(older) < Faktor(newer) → altes Schema wuerde invertieren.
    assert _source_factor_from_path(older) < _source_factor_from_path(newer)

    # Attach-Reihenfolge bestimmt segment_id (aeltere zuerst = niedriger).
    mig_older = LegacyMigrator(store, older)
    mig_newer = LegacyMigrator(store, newer)
    older_rec = await mig_older.attach_readonly(mig_older.classify())
    newer_rec = await mig_newer.attach_readonly(mig_newer.classify())
    assert newer_rec.segment_id > older_rec.segment_id

    # Ordnung im ATTACHED-Zustand festhalten (Referenz).
    attached_rows = await store.query(StoreQuery(limit=20, sort_field="id", sort_order="desc"))
    attached_values = [r["new_value"] for r in attached_rows]

    # Beide Quellen vollstaendig migrieren (detacht am Ende).
    assert await mig_older.migrate_small(batch_rows=100) == 4
    assert await mig_newer.migrate_small(batch_rows=100) == 4
    assert await store.manifest.list_legacy_segments() == []

    migrated_rows = await store.query(StoreQuery(limit=20, sort_field="id", sort_order="desc"))
    migrated_values = [r["new_value"] for r in migrated_rows]

    # Die migrierte Cross-Source-Ordnung ist deckungsgleich zum attached-Zustand.
    assert migrated_values == attached_values, f"attached={attached_values} migriert={migrated_values}"

    # Konkret: die neuere Quelle (100er) kommt komplett VOR der aelteren (10er).
    newer_positions = [i for i, v in enumerate(migrated_values) if v >= 100]
    older_positions = [i for i, v in enumerate(migrated_values) if v < 100]
    assert max(newer_positions) < min(older_positions)

    # Innerhalb jeder Quelle: rowid-monoton (neuer zuerst, id desc).
    newer_seq = [v for v in migrated_values if v >= 100]
    older_seq = [v for v in migrated_values if v < 100]
    assert newer_seq == sorted(newer_seq, reverse=True)
    assert older_seq == sorted(older_seq, reverse=True)

    # Alle migrierten gids strikt negativ und JS-safe.
    gids = [r["global_event_id"] for r in migrated_rows]
    assert all(-JS_MAX <= g < 0 for g in gids)
    assert len(set(gids)) == len(gids)  # eindeutig


async def test_latest_page_after_migration_keeps_newest_source(store: SqliteSegmentStore, tmp_path: Path):
    """Eine latest-page (``id desc`` kleines limit) nach der Migration laesst keine neuere Quelle weg."""
    older = tmp_path / "older.db"
    newer = tmp_path / "newer.db"
    _build_legacy(older, [(f"2024-01-{i + 1:02d}T00:00:00.000Z", 10 + i) for i in range(3)])
    _build_legacy(newer, [(f"2025-06-{i + 1:02d}T00:00:00.000Z", 100 + i) for i in range(3)])

    mig_older = LegacyMigrator(store, older)
    mig_newer = LegacyMigrator(store, newer)
    await mig_older.attach_readonly(mig_older.classify())
    await mig_newer.attach_readonly(mig_newer.classify())
    assert await mig_older.migrate_small(batch_rows=100) == 3
    assert await mig_newer.migrate_small(batch_rows=100) == 3

    first_page = await store.query(StoreQuery(limit=2, sort_field="id", sort_order="desc"))
    assert all(r["new_value"] >= 100 for r in first_page), [r["new_value"] for r in first_page]


async def test_migrated_gid_uses_mirrored_segment_id_factor(store: SqliteSegmentStore, tmp_path: Path):
    """Die migrierte gid traegt exakt den aus dem attached segment_id gespiegelten Faktor.

    Direkter Nachweis, dass die Ordnungs-Komponente aus dem segment_id (nicht dem Pfad-Hash)
    stammt: die materialisierte gid entspricht ``rowid - OFFSET - factor*STRIDE`` mit
    ``factor = _mirror_segment_id(legacy_segment_id)``.
    """
    from obs.ringbuffer.store.sqlite_backend import _LEGACY_GID_OFFSET, _LEGACY_GID_STRIDE

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", "L1"), ("2025-01-02T00:00:00.000Z", "L2")])
    migrator = LegacyMigrator(store, db)
    legacy_rec = await migrator.attach_readonly(migrator.classify())
    factor = _mirror_segment_id(legacy_rec.segment_id)

    assert await migrator.migrate_small(batch_rows=100) == 2

    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="asc"))
    gid_by_value = {r["new_value"]: r["global_event_id"] for r in rows}
    # Legacy-rowids sind 1 und 2 (AUTOINCREMENT), in Einfuege-Reihenfolge L1, L2.
    assert gid_by_value["L1"] == 1 - _LEGACY_GID_OFFSET - factor * _LEGACY_GID_STRIDE
    assert gid_by_value["L2"] == 2 - _LEGACY_GID_OFFSET - factor * _LEGACY_GID_STRIDE
    # rowid-monoton innerhalb der Quelle.
    assert gid_by_value["L2"] > gid_by_value["L1"]


async def test_resume_idempotent_across_chunks_per_source(store: SqliteSegmentStore, tmp_path: Path):
    """Chunk-weise Migration ist idempotent (kein Doppel-Import) und haelt die rowid-Ordnung.

    Belegt Finding-1-Punkt (b): die Resume-Korrektheit (pro-Quelle-Floor via ``MAX(gid)`` im
    eigenen Bucket) bleibt intakt, obwohl der Ordnungs-Faktor jetzt aus dem segment_id stammt.
    Ein zweiter Lauf ab dem persistierten Cursor darf keine bereits kopierte Zeile duplizieren.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [(f"2025-01-{i + 1:02d}T00:00:00.000Z", f"L{i}") for i in range(6)])
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())

    # In kleinen Chunks migrieren.
    total = 0
    while True:
        copied = await migrator.migrate_chunk(batch_rows=2)
        total += copied
        if migrator._load_state().done:
            break
    assert total == 6

    rows = await store.query(StoreQuery(limit=50, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows if str(r["new_value"]).startswith("L")]
    # Genau einmal jede Zeile, rowid-absteigend (neuer zuerst).
    assert values == ["L5", "L4", "L3", "L2", "L1", "L0"]
    assert len(values) == len(set(values))  # kein Duplikat


# ===========================================================================
# Finding 2 – WAL-Sidecar in der Marker-Identitaet
# ===========================================================================


async def test_marker_stale_on_wal_only_change(store: SqliteSegmentStore, tmp_path: Path):
    """Nur das ``-wal`` aendert sich (Hauptdatei mtime/size identisch) → Marker STALE → Re-Attach.

    Reproduziert Finding 2: kehrt ein Operator in den legacy-file-backed Modus zurueck, werden
    neue Legacy-Zeilen im WAL committet – die Haupt-DB bleibt byte- UND mtime-identisch, nur das
    ``-wal`` waechst. Deckte die Marker-Identitaet nur die Hauptdatei ab, haette ``classify()``
    weiter ``None`` (suppress) geliefert und die WAL-Zeilen still versteckt. Jetzt erfasst der
    Marker die Sidecar-Identitaet → die WAL-Aenderung gilt als stale und ``classify()`` liefert
    wieder eine Klassifikation.

    Die DB ist von Anfang an im WAL-Modus (persistente Connection offen), damit der spaetere
    Insert AUSSCHLIESSLICH das ``-wal`` beruehrt und die Hauptdatei nicht (der Moduswechsel
    selbst wuerde den DB-Header schreiben).
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", "L0")])
    # DB dauerhaft in den WAL-Modus versetzen und die Connection offen halten.
    wal_conn = sqlite3.connect(str(db))
    wal_conn.execute("PRAGMA journal_mode=WAL")
    wal_conn.execute("PRAGMA wal_autocheckpoint=0")
    wal_conn.commit()
    try:
        migrator = LegacyMigrator(store, db)
        # Marker mit der AKTUELLEN (WAL-Modus-)Identitaet schreiben – so wie es
        # ``_detach_migrated_legacy_segment`` am Migrations-Ende tut.
        migrator._mark_source_migrated()
        marker = db.with_name(f"{db.name}.migrated")
        assert marker.exists()
        # Datei unveraendert → Marker unterdrueckt Re-Attach.
        assert LegacyMigrator(store, db).classify() is None

        # Haupt-DB-Identitaet festhalten, dann NUR eine WAL-Zeile anhaengen.
        main_before = db.stat()
        wal_conn.execute(
            "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2025-02-01T00:00:00.000Z", "dp-legacy", "dp/dp-legacy/value", None, json.dumps("wal-only"), "legacy", "good"),
        )
        wal_conn.commit()
        wal = Path(f"{db}-wal")
        assert wal.exists() and wal.stat().st_size > 0
        main_after = db.stat()
        # Sicherstellen, dass die Hauptdatei WIRKLICH byte-/mtime-identisch geblieben ist –
        # sonst wuerde der Test bereits ueber die Hauptdatei-Identitaet greifen.
        assert (main_before.st_mtime_ns, main_before.st_size) == (main_after.st_mtime_ns, main_after.st_size)

        # WAL-only-Aenderung → Marker stale → classify() liefert wieder eine Klassifikation.
        reclassified = LegacyMigrator(store, db).classify()
        assert reclassified is not None, "WAL-only-Aenderung wurde als unveraendert behandelt (Finding 2)"
    finally:
        wal_conn.close()


async def test_marker_suppresses_when_nothing_changed(store: SqliteSegmentStore, tmp_path: Path):
    """Gegentest: bleibt alles unveraendert (auch das ``-wal`` fehlt/unveraendert) → suppress."""
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", "L0"), ("2025-01-02T00:00:00.000Z", "L1")])
    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_small(batch_rows=100) == 2

    # Nichts aendert sich → Re-Attach bleibt unterdrueckt.
    assert LegacyMigrator(store, db).classify() is None
    # Auch ein zweiter Aufruf bleibt stabil suppress (idempotent).
    assert LegacyMigrator(store, db).classify() is None


async def test_old_format_marker_without_wal_fields_still_suppresses(store: SqliteSegmentStore, tmp_path: Path):
    """Rueckwaertskompat: ein Alt-Marker (nur mtime+size, Runde 27) unterdrueckt bei unveraenderter Hauptdatei.

    Ein vor Runde 29 geschriebener Marker traegt keine WAL/SHM-Felder. Bei unveraenderter
    Hauptdatei muss er wie bisher suppress liefern (die neue WAL-Semantik darf bestehende
    Installs nicht ploetzlich re-attachen lassen).
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2025-01-01T00:00:00.000Z", "L0")])
    migrator = LegacyMigrator(store, db)
    st = db.stat()
    # Marker im ALTEN Format (nur mtime_ns + size) neben die Quelle schreiben.
    marker = db.with_name(f"{db.name}.migrated")
    marker.write_text(json.dumps({"mtime_ns": st.st_mtime_ns, "size": st.st_size}), encoding="utf-8")

    # Hauptdatei unveraendert → Alt-Marker suppress (Haupt-nur-Semantik erhalten).
    assert migrator.classify() is None


# ===========================================================================
# Finding 3 – finaler Seal unter geteiltem Write-Lock serialisieren
# ===========================================================================


async def test_final_seal_blocks_while_shared_lock_held(store: SqliteSegmentStore, tmp_path: Path):
    """Der finale Seal (rein-negatives aktives Segment rotieren) blockiert bei gehaltenem Write-Lock.

    Reiner Ein-Quell-Fall ohne Positive: die negativen Zeilen bleiben im aktiven NORMALEN
    Segment; der Abschluss versiegelt es via ``_seal_pure_migrated_active_segment`` → rotate.
    Mit dem geteilten Lock (wie ``record()``) darf dieser Seal NICHT laufen, solange ein
    (simulierter) Live-Append den Lock haelt.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    lock = asyncio.Lock()
    migrator = LegacyMigrator(store, db, write_lock=lock)
    await migrator.attach_readonly(migrator.classify())

    # Live-Append haelt den Lock, dann den finalen Seal starten.
    await lock.acquire()
    task = asyncio.create_task(migrator._finalize_migrated_segments())
    try:
        await asyncio.sleep(0.05)
        assert not task.done(), "finaler Seal lief trotz gehaltenem Write-Lock durch"
    finally:
        lock.release()
        await asyncio.wait_for(task, timeout=5)


async def test_final_seal_noop_without_lock(store: SqliteSegmentStore, tmp_path: Path):
    """Ohne ``write_lock`` (``None``) laeuft der Seal unveraendert durch (bestehendes Verhalten)."""
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0")])
    migrator = LegacyMigrator(store, db)  # kein write_lock
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_small(batch_rows=100) == 1
    # Migration abgeschlossen, Legacy detacht, Zeile sichtbar.
    assert await store.manifest.list_legacy_segments() == []
    values = [r["new_value"] for r in await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))]
    assert "L0" in values


async def test_live_append_not_mixed_into_pure_migrated_segment(store: SqliteSegmentStore, tmp_path: Path):
    """Ein zwischen Lock-Freigabe und Seal gereihter Live-Append landet nicht im rein-negativen Segment.

    Der geteilte Lock zwingt Live-Append und finalen Seal in eine Reihenfolge. Der Live-Append
    (unter dem Lock, wie ``record()``) laeuft VOR dem Seal; danach rotiert der Seal sauber und
    das rein-negative migrierte Segment bleibt frei von positiven Zeilen. Die positive Live-
    Zeile sortiert korrekt als neu (``id desc`` zuerst), die negativen Legacy-Zeilen dahinter –
    kein gemischtes Segment, das die negativen Zeilen in den positiven Query-Praefix zieht.
    """
    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    lock = asyncio.Lock()
    migrator = LegacyMigrator(store, db, write_lock=lock)
    await migrator.attach_readonly(migrator.classify())

    # Den finalen Seal so umschliessen, dass ein Live-Append den Lock GENAU vor dem Seal
    # haelt: wir halten den Lock, starten die Migration (deren Seal am Lock wartet), spielen
    # unter dem Lock einen positiven Live-Append ein und geben dann frei.
    await lock.acquire()
    task = asyncio.create_task(migrator.migrate_small(batch_rows=100))
    try:
        await asyncio.sleep(0.05)
        # Migration haengt am Lock (Write-Sektion oder Seal). Live-Positive unter dem Lock einspielen.
        await store.append([_live_event("live-new", "2026-06-01T00:00:00.000Z")])
    finally:
        lock.release()
        assert await asyncio.wait_for(task, timeout=5) == 2

    rows = await store.query(StoreQuery(limit=20, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    # Der frische Live-Event bleibt sichtbar und sortiert als neuester.
    assert values[0] == "live-new", values
    # Die migrierten Legacy-Zeilen liegen dahinter (nicht als "neueste" hochgezogen).
    assert "L0" in values and "L1" in values
    assert values.index("live-new") < values.index("L1")
    assert values.index("L1") < values.index("L0")
