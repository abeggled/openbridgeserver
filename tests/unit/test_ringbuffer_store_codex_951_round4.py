"""Codex-Findings Runde 4 am RingBuffer (#919, PR #951) – 1x P1, 2x P2.

Verfeinerungen der gemeinsamen Retention-Victim-Logik fuer attached read-only
Legacy-Quellen mit UNBEKANNTEN Stats (attach_readonly scannt die 20–30 GB-Datei
bewusst nicht → row_count=0, to_ts=NULL im Manifest). TDD-first: jeder Test
reproduziert das Fehlverhalten ohne Fix und wird durch den Fix gruen.

1. [P2] ``_retention_victims_in_order``: mehrere attached Legacy-Quellen muessen
   FIFO-konform (aeltestes = kleinste segment_id ZUERST) einsortiert werden. Die
   frueherere DESCENDING-Sortierung waehlte die NEUESTE Legacy-Quelle zuerst und
   kehrte den FIFO-Vertrag um.
2. [P2] Age-Retention: ein Legacy-Segment mit UNBEKANNTER ``to_ts`` (NULL) darf
   die Age-Schleife nicht per ``break`` stoppen, sondern wird uebersprungen, damit
   nachfolgende geschlossene v2-Segmente weiterhin altersbedingt getrimmt werden.
3. [P1] Row-Retention: ein Legacy-Segment mit UNBEKANNTEM (0/unscanned)
   ``row_count`` darf NICHT vom Row-Budget getrimmt werden. Sonst wirft Row-Druck
   aus neuen v2-Daten die ganze Legacy-DB weg, ohne ``_total_row_count`` zu senken.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from obs.ringbuffer.store.config import StoreRetentionConfig
from obs.ringbuffer.store.interface import StoreEvent
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


async def _attach_legacy_blob(store: SqliteSegmentStore, size_bytes: int, *, name: str) -> tuple[int, Path]:
    """Registriert eine Legacy-Blob-Datei als read-only Segment (row_count=0, to_ts=NULL)."""
    legacy_file = store._root / name
    legacy_file.write_bytes(b"\x00" * size_bytes)
    rec = await store.manifest.register_legacy_segment(source_path=str(legacy_file), size_bytes=size_bytes)
    return rec.segment_id, legacy_file


# ===========================================================================
# (1) [P2] FIFO-Ordnung bei mehreren attached Legacy-Quellen
# ===========================================================================


async def test_multiple_legacy_victims_ordered_oldest_first(store: SqliteSegmentStore):
    # Zwei attached Legacy-Quellen: die zuerst registrierte hat die kleinere
    # segment_id (aeltestes zuerst, wie list_legacy_segments dokumentiert). Der
    # Guard ist durch frische v2-Daten erfuellt. Die Victim-Reihenfolge muss die
    # AELTESTE Legacy-Quelle (kleinste segment_id) ZUERST liefern.
    older_id, _ = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_old.db")
    newer_id, _ = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_new.db")
    assert older_id < newer_id

    # Frische v2-Daten sichern den Guard.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])

    victims = await store._retention_victims_in_order()
    legacy_victim_ids = [v.segment_id for v in victims if v.schema_version <= 0 or v.status == "legacy"]
    # Aeltestes Legacy (kleinste segment_id) muss VOR dem neueren stehen.
    assert legacy_victim_ids[:2] == [older_id, newer_id]


# ===========================================================================
# (2) [P2] Age-Retention ueberspringt Legacy mit unbekannter to_ts
# ===========================================================================


async def test_age_retention_skips_unknown_legacy_and_trims_old_v2(store: SqliteSegmentStore):
    # Attached Legacy mit UNBEKANNTER to_ts (NULL, unscanned). Ein geschlossenes
    # v2-Segment ist aelter als der Cutoff. Ohne Fix stoppt die Age-Schleife am
    # Legacy (steht als aeltestes vorne) per break → das alte v2-Segment wird NIE
    # per Alter getrimmt. Der Fix ueberspringt das Legacy und trimmt das v2-Segment.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_unknown.db")

    # Altes geschlossenes v2-Segment (to_ts weit vor dem Cutoff).
    old_ts = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    await store.append([_event(1, old_ts)])
    old_v2 = await store.manifest.get_active_segment()
    await store.rotate()

    # Frisches aktives v2-Segment sichert den Guard und bleibt (aktiv, nicht loeschbar).
    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    await store.append([_event(2, fresh_ts)])

    store._retention_config = StoreRetentionConfig(max_age=60)  # 30 Tage alt faellt
    removed = await store.enforce_retention()

    # Das alte v2-Segment wurde altersbedingt getrimmt ...
    assert await store.manifest.get_segment(old_v2.segment_id) is None
    # ... die Legacy-Quelle mit unbekannter to_ts bleibt (konservativ nicht per Alter geloescht).
    assert await store.manifest.get_segment(legacy_id) is not None
    assert legacy_file.exists()
    assert removed >= 1


async def test_age_retention_deletes_legacy_with_known_old_to_ts(store: SqliteSegmentStore):
    # Gegenprobe: ein Legacy mit BEKANNTER, alter to_ts bleibt age-retention-faehig.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_known.db")
    await store.manifest.update_segment_stats(
        legacy_id,
        row_count=1,
        size_bytes=4 * 1024 * 1024,
        from_ts="2000-01-01T00:00:00.000Z",
        to_ts="2000-01-01T00:00:00.000Z",
    )
    # Frische v2-Daten sichern den Guard.
    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])

    store._retention_config = StoreRetentionConfig(max_age=1)
    removed = await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is None
    assert not legacy_file.exists()
    assert removed >= 1


# ===========================================================================
# (3) [P1] Row-Retention schliesst Legacy mit unbekanntem row_count aus
# ===========================================================================


async def test_row_retention_excludes_unknown_legacy_and_trims_v2(store: SqliteSegmentStore):
    # Attached Legacy mit UNBEKANNTEM row_count (0, unscanned). Nur die v2-Zeilen
    # uebersteigen das Row-Budget. Ohne Fix ist victims[0] das Legacy → das Loeschen
    # wirft die GANZE Legacy-DB weg, senkt _total_row_count aber NICHT (row_count=0),
    # sodass die Schleife weiterlaeuft und auch v2-Segmente loescht. Der Fix schliesst
    # das unscanned Legacy vom Row-Trimming aus → nur v2 wird getrimmt, Legacy bleibt.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_unscanned.db")

    # Mehrere geschlossene v2-Segmente mit je einer Zeile.
    await store.append([_event(1, "2026-01-01T00:00:01.000Z")])
    v2_old = await store.manifest.get_active_segment()
    await store.rotate()
    await store.append([_event(2, "2026-01-01T00:00:02.000Z")])
    v2_mid = await store.manifest.get_active_segment()
    await store.rotate()
    # Frisches aktives Segment (Guard + bleibt).
    await store.append([_event(3, "2026-01-01T00:00:03.000Z")])

    # Insgesamt 3 v2-Zeilen; Budget 1 → 2 aeltere v2-Segmente muessen weichen.
    store._retention_config = StoreRetentionConfig(max_entries=1)
    await store.enforce_retention()

    # Legacy mit unbekanntem row_count wurde NICHT als Row-Opfer geloescht.
    assert await store.manifest.get_segment(legacy_id) is not None
    assert legacy_file.exists()
    # Die aelteren v2-Segmente wurden per Row-Budget getrimmt.
    assert await store.manifest.get_segment(v2_old.segment_id) is None
    assert await store.manifest.get_segment(v2_mid.segment_id) is None


async def test_row_retention_reclaims_legacy_with_known_row_count(store: SqliteSegmentStore):
    # Gegenprobe: ein Legacy mit BEKANNTEM positiven row_count bleibt row-retention-
    # faehig (aeltestes zuerst), sobald der Guard erfuellt ist.
    legacy_id, legacy_file = await _attach_legacy_blob(store, 4 * 1024 * 1024, name="legacy_scanned.db")
    await store.manifest.update_segment_stats(legacy_id, row_count=5, size_bytes=4 * 1024 * 1024, from_ts=None, to_ts=None)

    await store.append([_event(1, "2026-01-01T00:00:00.000Z")])

    store._retention_config = StoreRetentionConfig(max_entries=1)
    await store.enforce_retention()
    assert await store.manifest.get_segment(legacy_id) is None
    assert not legacy_file.exists()
