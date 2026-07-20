"""Safe-Decode im v2-Read-Pfad (#951, Codex P2 :2526).

Bleibt ein v2-Segment SQLite-lesbar, aber der JSON-Wert EINER Zeile ist malformed
(z. B. nach partieller Datei-Korruption oder einem fremden/fehlerhaften Write), darf
ein direktes ``json.loads`` im v2-Row→dict-Mapping die gesamte Query nicht mit einer
``JSONDecodeError`` (→ 500) brechen. Analog zum Legacy-Reader degradiert der Wert auf
den Rohwert (``metadata`` auf ``{}``); valide Werte werden unverändert dekodiert.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore


def _event(value: object, ts: str, *, datapoint_id: str = "dp-1") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
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
# (1) Direkter Row→dict-Test: malformed degradiert, valide bleibt korrekt
# ---------------------------------------------------------------------------


def test_row_to_dict_degrades_malformed_json_value():
    """Ein malformed ``new_value``/``old_value`` degradiert auf den Rohwert, wirft nicht."""
    row = {
        "global_event_id": 1,
        "ts": "2026-01-01T00:00:00.000Z",
        "datapoint_id": "dp-1",
        "topic": "dp/dp-1/value",
        "old_value": "{not json",
        "new_value": "definitely-not-json}",
        "source_adapter": "api",
        "quality": "good",
        "metadata_version": 1,
        "metadata": "{broken",
    }
    result = SqliteSegmentStore._row_to_dict(row)
    # Rohwert statt JSONDecodeError.
    assert result["old_value"] == "{not json"
    assert result["new_value"] == "definitely-not-json}"
    # metadata degradiert auf leeres dict (kein Metadaten-Treffer), nie Exception.
    assert result["metadata"] == {}


def test_row_to_dict_decodes_valid_json_values():
    """Gegentest: valide JSON-Werte (nested, Zahl, String, null) bleiben korrekt."""
    row = {
        "global_event_id": 2,
        "ts": "2026-01-01T00:00:00.000Z",
        "datapoint_id": "dp-1",
        "topic": "dp/dp-1/value",
        "old_value": "null",
        "new_value": '{"a": [1, 2, {"b": true}], "c": "x"}',
        "source_adapter": "api",
        "quality": "good",
        "metadata_version": 1,
        "metadata": '{"unit": "C"}',
    }
    result = SqliteSegmentStore._row_to_dict(row)
    assert result["old_value"] is None
    assert result["new_value"] == {"a": [1, 2, {"b": True}], "c": "x"}
    assert result["metadata"] == {"unit": "C"}

    # Skalare
    for encoded, expected in [("42", 42), ("3.5", 3.5), ('"hi"', "hi"), ("null", None)]:
        r = dict(row)
        r["new_value"] = encoded
        assert SqliteSegmentStore._row_to_dict(r)["new_value"] == expected

    # NULL-Spalte bleibt None.
    r = dict(row)
    r["new_value"] = None
    assert SqliteSegmentStore._row_to_dict(r)["new_value"] is None


# ---------------------------------------------------------------------------
# (2) End-to-end: eine korrupte Zeile bricht die Query über das Segment nicht
# ---------------------------------------------------------------------------


async def _corrupt_new_value(store: SqliteSegmentStore, filename: str, *, datapoint_id: str, raw: str) -> None:
    """Setzt die rohe ``new_value``-Spalte einer Segmentzeile auf einen non-JSON-String."""
    path = store._segments_dir / filename
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "UPDATE ringbuffer SET new_value = ? WHERE datapoint_id = ?",
            (raw, datapoint_id),
        )
        await conn.commit()


async def test_v2_query_survives_malformed_row_and_returns_others(store: SqliteSegmentStore):
    # Segment mit drei Zeilen: eine wird nachträglich korrumpiert.
    await store.append(
        [
            _event(10, "2026-01-01T00:00:00.000Z", datapoint_id="good-a"),
            _event({"nested": [1, True, None]}, "2026-01-01T00:00:01.000Z", datapoint_id="broken"),
            _event(30, "2026-01-01T00:00:02.000Z", datapoint_id="good-b"),
        ]
    )
    segment = store._active_segment
    assert segment is not None
    await _corrupt_new_value(store, segment.filename, datapoint_id="broken", raw="{not-json")

    # Query über das Segment darf NICHT mit 500/JSONDecodeError scheitern.
    rows = await store.query(StoreQuery(limit=10))

    by_dp = {r["datapoint_id"]: r for r in rows}
    # Die betroffene Zeile degradiert auf den Rohwert.
    assert by_dp["broken"]["new_value"] == "{not-json"
    # Die übrigen Zeilen kommen unverändert korrekt zurück.
    assert by_dp["good-a"]["new_value"] == 10
    assert by_dp["good-b"]["new_value"] == 30
