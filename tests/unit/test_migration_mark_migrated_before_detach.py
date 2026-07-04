"""Codex-P2 (#951, migration.py:795): ``migrated``-Status-Übergang gegen Crash absichern.

Finding: Stirbt der Prozess NACHDEM ``_detach_migrated_legacy_segment()`` erfolgreich war
(``.migrated``-Marker geschrieben + Legacy-Manifest-Zeile entfernt), aber BEVOR die
``mark_migrated``-Schleife (~migration.py:795) die kopierten Chunks DIESER Quelle vom
Zwischenrang ``closed`` in den Trailing-Rang ``migrated`` gehoben hat, bleiben die
rein-negativen v2-Chunks schlicht ``closed``. ``list_segments_for_query()`` verschiebt nur
``legacy``/``migrated`` in den Trailing-Rang – ``closed`` negative Chunks verbleiben im
POSITIVEN Segment-Prefix (``segment_id DESC``). Da sie NACH den ersten v2-Writes migriert
wurden, tragen sie eine HÖHERE segment_id als das echte positive v2-Segment und werden von
einer ``id desc``-latest-page-Query ZUERST besucht; deren negative Zeilen füllen das
Fenster und der Frühabbruch feuert VOR dem älteren positiven v2-Segment → migrierte
Legacy-Zeilen erscheinen als „latest", echte Live-Zeilen werden versteckt.

Fix (Recovery): Ein resume-fähiger Nachzieh-Schritt promotet nach erfolgreichem Detach
(Quelle NICHT mehr attached) die verbliebenen eigenen rein-negativen ``closed``-Chunks auf
``migrated``, sofern der Store den Trailing-Rang braucht (echte Positive / Fremdquelle).
Idempotent, source-gescopt, bewahrt die etablierte Promote/Detach/mark_migrated-Reihenfolge
(Rollback braucht ``closed``); die Lücke „mark_migrated NACH Detach abgebrochen" wird beim
nächsten ``migrate_chunk`` geheilt.

TDD: Der Crash-Test reproduziert den Bug auf dem ungehefteten Zustand (Chunks ``closed`` im
positiven Prefix) und wird durch die Recovery grün. Der Regression-Gegentest sichert den
normalen Abschluss unverändert ab.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.manifest import SEGMENT_STATUS_MIGRATED, SEGMENT_STATUS_MIGRATING
from obs.ringbuffer.store.migration import LegacyMigrator
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value, ts: str, *, dp: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=dp,
        topic=f"dp/{dp}/value",
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


def _build_legacy(path: Path, rows: list[tuple[str, object]]) -> None:
    """Legacy-Single-DB mit AUTOINCREMENT-rowid; ``rows`` = ``(ts, value)``."""
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


async def test_crash_after_detach_before_mark_migrated_hides_no_live_rows(store: SqliteSegmentStore, tmp_path: Path, monkeypatch):
    # Positive v2-Zeile zuerst → die migrierten (negativen) Chunks brauchen nach dem Detach
    # den ``migrated``-Trailing-Rang, sonst sitzen sie im positiven Prefix.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    # Normaler Upgrade-Pfad: Quelle read-only einhängen, damit die kopierten Segmente
    # WÄHREND der Migration als ``migrating`` (versteckt) markiert werden – dann promotet
    # ``_finalize_migrated_segments`` sie auf ``closed`` und die ``mark_migrated``-Schleife
    # ist der letzte, hier abgebrochene Schritt.
    await migrator.attach_readonly(migrator.classify())

    # Crash simulieren: der Detach war erfolgreich (Marker + Legacy-Zeile weg), aber die
    # ``mark_migrated``-Schleife bricht beim ersten Aufruf ab. Die eigenen Chunks bleiben
    # ``closed`` (nicht ``migrated``) im positiven Prefix.
    async def _boom(_segment_id: int):
        raise RuntimeError("simulierter Crash in mark_migrated-Schleife")

    monkeypatch.setattr(store.manifest, "mark_migrated", _boom)

    with pytest.raises(RuntimeError, match="simulierter Crash"):
        await migrator.migrate_chunk(batch_rows=100)

    # Zustand nach dem Crash: Detach ist publiziert (Marker da, Legacy-Zeile weg), aber die
    # eigenen migrierten Chunks stehen noch als ``closed`` im positiven Prefix.
    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATED] == []

    # Recovery: den mark_migrated-Übergang nachziehen lassen (echte Implementierung).
    monkeypatch.undo()
    # Ein Resume-Lauf heilt die Lücke: die Quelle ist detached, der Store braucht den
    # Trailing-Rang → die eigenen rein-negativen ``closed``-Chunks werden ``migrated``.
    assert await migrator.migrate_chunk(batch_rows=100) == 0

    migrated = [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATED]
    assert migrated, "eigene migrierte Chunks wurden nicht in den Trailing-Rang (migrated) gehoben"
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []

    # Kern-Assertion: eine kleine ``id desc``-latest-page-Query trifft die echte positive
    # v2-Zeile zuerst – NICHT die migrierten Legacy-Zeilen. Vor dem Fix säßen die
    # ``closed``-Negativen mit höherer segment_id im positiven Prefix und würden zuerst
    # gesammelt → ``latest_row == "L1"`` statt ``"v2"``.
    latest_page = await store.query(StoreQuery(limit=1, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in latest_page] == ["v2"], "migrierte Legacy-Zeile erscheint faelschlich als latest"

    # Vollständige Historie korrekt sortiert: v2 (neu) vor L1/L0 (migriert, trailing/aelter).
    full = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in full] == ["v2", "L1", "L0"]


async def test_normal_completion_still_marks_migrated(store: SqliteSegmentStore, tmp_path: Path):
    # Gegentest: der normale Migrationsabschluss (kein Crash) bleibt unverändert korrekt –
    # eigene Chunks landen im ``migrated``-Trailing-Rang, Historie korrekt, keine ``migrating``-Reste.
    await store.append([_event("v2", "2026-06-01T00:00:00.000Z")])

    db = tmp_path / "obs_ringbuffer.db"
    _build_legacy(db, [("2020-01-01T00:00:00.000Z", "L0"), ("2020-01-02T00:00:00.000Z", "L1")])

    migrator = LegacyMigrator(store, db)
    await migrator.attach_readonly(migrator.classify())
    assert await migrator.migrate_chunk(batch_rows=100) == 2

    assert migrator._migrated_marker_path.exists()
    assert not await store.manifest.list_legacy_segments()
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATING] == []
    assert [s for s in await store.manifest.list_segments() if s.status == SEGMENT_STATUS_MIGRATED], (
        "migrierte Chunks muessen im Trailing-Rang (migrated) liegen"
    )

    latest_page = await store.query(StoreQuery(limit=1, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in latest_page] == ["v2"]
    full = await store.query(StoreQuery(limit=100, sort_field="id", sort_order="desc"))
    assert [r["new_value"] for r in full] == ["v2", "L1", "L0"]
