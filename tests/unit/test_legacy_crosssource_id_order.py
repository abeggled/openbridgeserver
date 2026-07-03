"""Cross-Source-Ordnung synthetischer Legacy-global_event_ids (#951, Codex :1558).

Codex-Finding „Preserve legacy-source order in synthetic IDs": beim read-only-Lesen
mehrerer attached Legacy-DBs behandelt der Manifest-/Retention-Vertrag eine NIEDRIGERE
``segment_id`` als die ÄLTERE Quelle (Registrierungsreihenfolge, ``_retention_victim_order``
sortiert ältestes Legacy = niedrigste segment_id zuerst). Die frühere synthetische-ID-Formel
``rowid - OFFSET - segment_id * STRIDE`` gab jedoch der ÄLTEREN Quelle (niedrige segment_id)
die HÖCHSTEN (am wenigsten negativen) IDs. Eine Default-``id desc``-Query, die den Legacy-Tail
erreicht, pagte damit durch die ÄLTERE Quelle VOR der neueren → eine latest-page-Anfrage konnte
neuere Legacy-Historie hinter älteren Zeilen weglassen (Cross-Source-Chronologie invertiert).

Der Fix spiegelt ``segment_id`` an der Bucket-Schranke ``B`` (``B - 1 - segment_id``): die
NEUERE Quelle (höhere segment_id) bekommt den WENIGER negativen Block und sortiert im
``id desc`` VOR der älteren. Erhaltene Invarianten:
* (a) innerhalb einer Quelle: höhere rowid = neuer = weniger negativ (unverändert);
* (b) alle Legacy-gids strikt negativ, strikt < jede positive v2-gid;
* (c) disjunkte, kollisionsfreie Buckets pro (Quelle, rowid);
* (d) JS-safe: alle exponierten IDs in ``[-(2**53-1), 0)`` (Runde-23-Constraint);
* (e) Eindeutigkeit pro Zeile.

Regression: der Single-Source-Fall (nur EINE attached Legacy-DB, häufiger Upgrade-Fall)
bleibt in seiner rowid-Ordnung identisch zum bisherigen Verhalten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreQuery
from obs.ringbuffer.store.migration import (
    _MIGRATION_SOURCE_BUCKETS,
    LegacyMigrator,
)
from obs.ringbuffer.store.sqlite_backend import (
    SqliteSegmentStore,
    _LEGACY_GID_OFFSET,
    _LEGACY_GID_STRIDE,
    _LEGACY_SOURCE_BUCKETS,
)

JS_MAX = 2**53 - 1  # größter exakt als IEEE-754-Double darstellbarer Integer


def _read_path_gid(rowid: int, segment_id: int) -> int:
    """Repliziert die (gespiegelte) Read-Pfad-Formel aus ``_legacy_row_to_dict``."""
    source_factor = _LEGACY_SOURCE_BUCKETS - 1 - (segment_id % _LEGACY_SOURCE_BUCKETS)
    return rowid - _LEGACY_GID_OFFSET - source_factor * _LEGACY_GID_STRIDE


async def _build_legacy_db(path: Path, values: list[int], *, base_ts: str) -> None:
    """Befüllt eine echte Legacy-``ringbuffer.db`` im ALTEN Format über RingBuffer."""
    rb = RingBuffer(storage="disk", disk_path=str(path), max_entries=None)
    await rb.start()
    try:
        for i, value in enumerate(values):
            await rb.record(
                ts=f"{base_ts}{i:02d}.000Z",
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


@pytest.fixture
async def store(tmp_path: Path) -> SqliteSegmentStore:
    s = SqliteSegmentStore(tmp_path / "root")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Multi-Source: zwei attached Legacy-Segmente, korrekte Cross-Source-Chronologie
# ---------------------------------------------------------------------------


async def test_multi_source_id_desc_newer_source_before_older(store: SqliteSegmentStore, tmp_path: Path):
    """``id desc`` liefert die NEUERE Quelle (höhere segment_id) VOR der älteren.

    Die zuerst registrierte DB ist die ältere (niedrigere segment_id), die zweite die
    neuere (höhere segment_id). Werte sind quellenweise disjunkt, sodass die Herkunft
    jeder Zeile eindeutig bleibt. Bei korrekter Cross-Source-Ordnung stehen ALLE Zeilen
    der neueren Quelle (100er) im ``id desc`` VOR allen Zeilen der älteren (10er).
    """
    older = tmp_path / "older.db"
    newer = tmp_path / "newer.db"
    await _build_legacy_db(older, [10, 11, 12, 13], base_ts="2024-01-01T00:00:")
    await _build_legacy_db(newer, [100, 101, 102, 103], base_ts="2025-06-01T00:00:")

    # Reihenfolge der Registrierung bestimmt segment_id (ältere zuerst = niedriger).
    older_rec = await LegacyMigrator(store, older).attach_readonly(LegacyMigrator(store, older).classify())
    newer_rec = await LegacyMigrator(store, newer).attach_readonly(LegacyMigrator(store, newer).classify())
    assert newer_rec.segment_id > older_rec.segment_id  # höhere segment_id = neuere Quelle

    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    assert set(values) == {10, 11, 12, 13, 100, 101, 102, 103}

    # Cross-Source: die neuere Quelle (100er) kommt komplett VOR der älteren (10er).
    newer_positions = [i for i, v in enumerate(values) if v >= 100]
    older_positions = [i for i, v in enumerate(values) if v < 100]
    assert max(newer_positions) < min(older_positions)

    # Innerhalb jeder Quelle: höhere rowid (neuer) zuerst (id desc).
    newer_seq = [v for v in values if v >= 100]
    older_seq = [v for v in values if v < 100]
    assert newer_seq == sorted(newer_seq, reverse=True)
    assert older_seq == sorted(older_seq, reverse=True)


async def test_multi_source_latest_page_keeps_newest_legacy(store: SqliteSegmentStore, tmp_path: Path):
    """Eine latest-page (``id desc`` mit kleinem limit) lässt keine neuere Legacy weg.

    Vor dem Fix pagte die ältere Quelle vorne; eine ``limit=2``-Anfrage hätte die
    ältesten Legacy-Zeilen zurückgegeben und die neuere Legacy-Historie verborgen.
    Jetzt liefert die erste Seite ausschließlich Zeilen der NEUEREN Quelle.
    """
    older = tmp_path / "older.db"
    newer = tmp_path / "newer.db"
    await _build_legacy_db(older, [10, 11, 12], base_ts="2024-01-01T00:00:")
    await _build_legacy_db(newer, [100, 101, 102], base_ts="2025-06-01T00:00:")
    await LegacyMigrator(store, older).attach_readonly(LegacyMigrator(store, older).classify())
    await LegacyMigrator(store, newer).attach_readonly(LegacyMigrator(store, newer).classify())

    first_page = await store.query(StoreQuery(limit=2, sort_field="id", sort_order="desc"))
    assert all(r["new_value"] >= 100 for r in first_page), [r["new_value"] for r in first_page]


# ---------------------------------------------------------------------------
# Regression Single-Source: Ordnung identisch zum bisherigen Verhalten
# ---------------------------------------------------------------------------


async def test_single_source_id_desc_order_unchanged(store: SqliteSegmentStore, tmp_path: Path):
    """Eine einzige attached Legacy-DB: höhere rowid (neuer) zuerst – unverändert."""
    db = tmp_path / "obs_ringbuffer.db"
    await _build_legacy_db(db, [1, 2, 3, 4, 5], base_ts="2025-01-01T00:00:")
    await LegacyMigrator(store, db).attach_readonly(LegacyMigrator(store, db).classify())

    rows = await store.query(StoreQuery(limit=10, sort_field="id", sort_order="desc"))
    values = [r["new_value"] for r in rows]
    # neueste (rowid 5) zuerst, absteigend bis älteste (rowid 1).
    assert values == [5, 4, 3, 2, 1]
    assert all(r["global_event_id"] < 0 for r in rows)


# ---------------------------------------------------------------------------
# Formel-Invarianten: Ordnung, Negativität, JS-Safety, Disjunktheit, Worst-Case
# ---------------------------------------------------------------------------


def test_alias_bucket_bound_matches_migration():
    """``_LEGACY_SOURCE_BUCKETS`` == ``migration._MIGRATION_SOURCE_BUCKETS`` (Kapazitätsparität)."""
    assert _LEGACY_SOURCE_BUCKETS == _MIGRATION_SOURCE_BUCKETS


def test_higher_segment_id_is_less_negative():
    """Höhere segment_id (neuer) ⇒ weniger negativ ⇒ ``id desc`` zuerst."""
    older_block_max = _read_path_gid(rowid=_LEGACY_GID_STRIDE - 1, segment_id=0)
    newer_block_min = _read_path_gid(rowid=1, segment_id=1)
    assert newer_block_min > older_block_max


def test_rowid_monotone_within_source():
    """Innerhalb einer Quelle: höhere rowid ⇒ höhere (weniger negative) gid (Invariante a)."""
    for seg in (0, 3, _LEGACY_SOURCE_BUCKETS - 1):
        prev = None
        for rowid in range(1, 40):
            gid = _read_path_gid(rowid, seg)
            if prev is not None:
                assert gid > prev
            prev = gid


def test_all_strictly_negative():
    """Alle synthetischen Legacy-gids strikt negativ (Invariante b)."""
    for seg in (0, 1, 5, _LEGACY_SOURCE_BUCKETS - 1):
        for rowid in (1, 2, 1000, _LEGACY_GID_STRIDE - 1):
            assert _read_path_gid(rowid, seg) < 0


def test_buckets_disjoint_no_collision():
    """Zwei Quellen kollidieren nie auf denselben synthetischen gid (Invarianten c, e)."""
    gids: set[int] = set()
    for seg in range(0, 6):
        for rowid in range(1, 200):
            gid = _read_path_gid(rowid, seg)
            assert gid not in gids, f"Kollision bei seg={seg} rowid={rowid}"
            gids.add(gid)


def test_worst_case_js_safe():
    """Worst-Case-gid (rowid 1, niedrigste segment_id ⇒ Faktor ``B-1``) bleibt JS-safe (d).

    Durch die Spiegelung liegt der maximale Betrag jetzt beim NIEDRIGSTEN segment_id
    (Faktor ``B-1``), nicht mehr beim höchsten. Explizit durchgerechnet:
    ``1 - (1<<52) - (2**20 - 1) * (1<<32) = -9_007_194_959_773_695`` (> -(2**53-1)).
    """
    worst = _read_path_gid(rowid=1, segment_id=0)
    assert worst == 1 - _LEGACY_GID_OFFSET - (_LEGACY_SOURCE_BUCKETS - 1) * _LEGACY_GID_STRIDE
    assert -JS_MAX <= worst < 0
    # Am wenigsten negativ (höchster Index, größte rowid): strikt < 0, nie ≥ 0.
    least = _read_path_gid(rowid=_LEGACY_GID_STRIDE - 1, segment_id=_LEGACY_SOURCE_BUCKETS - 1)
    assert least < 0
    # Benachbarte rowids kollabieren als float64 NICHT (JS-Consumer bleibt eindeutig).
    for seg in (0, 7, _LEGACY_SOURCE_BUCKETS - 1):
        a = _read_path_gid(rowid=1, segment_id=seg)
        b = _read_path_gid(rowid=2, segment_id=seg)
        assert float(a) != float(b), f"float-Kollision bei seg={seg}"
