"""Unit tests for obs.api.v1.services.knx_traceability helpers."""

from __future__ import annotations

import json
import uuid

import pytest

from obs.api.v1.services.knx_traceability import _datapoints_by_group_address
from obs.db.database import Database

_DDL = """
CREATE TABLE IF NOT EXISTS datapoints (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    data_type   TEXT NOT NULL DEFAULT 'FLOAT',
    unit        TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    mqtt_topic  TEXT NOT NULL,
    mqtt_alias  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapter_instances (
    id           TEXT PRIMARY KEY,
    adapter_type TEXT NOT NULL,
    name         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adapter_bindings (
    id                  TEXT PRIMARY KEY,
    datapoint_id        TEXT NOT NULL REFERENCES datapoints(id) ON DELETE CASCADE,
    adapter_type        TEXT NOT NULL,
    direction           TEXT NOT NULL,
    config              TEXT NOT NULL DEFAULT '{}',
    enabled             INTEGER NOT NULL DEFAULT 1,
    adapter_instance_id TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""


async def _make_db() -> Database:
    db = Database(":memory:")
    await db.connect()
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            await db.execute(stmt)
    await db.commit()
    return db


async def _insert_dp(db: Database, dp_id: str, name: str) -> None:
    await db.execute(
        "INSERT INTO datapoints (id, name, data_type, mqtt_topic, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (dp_id, name, "FLOAT", f"dp/{dp_id}/value", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )


async def _insert_binding(db: Database, dp_id: str, config: dict, adapter_type: str = "KNX") -> None:
    await db.execute(
        "INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, direction, config, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), dp_id, adapter_type, "SOURCE", json.dumps(config), "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )


@pytest.mark.asyncio
async def test_datapoints_by_group_address_matches_clean_ga():
    db = await _make_db()
    try:
        dp_id = str(uuid.uuid4())
        await _insert_dp(db, dp_id, "Licht EG")
        await _insert_binding(db, dp_id, {"group_address": "1/2/3"})
        await db.commit()

        result = await _datapoints_by_group_address(["1/2/3"], db)
        assert len(result["1/2/3"]) == 1
        assert str(result["1/2/3"][0].id) == dp_id
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_datapoints_by_group_address_matches_ga_with_surrounding_whitespace():
    """Binding stored with padded GA must still match the trimmed lookup key."""
    db = await _make_db()
    try:
        dp_id = str(uuid.uuid4())
        await _insert_dp(db, dp_id, "Licht OG")
        # GA stored with surrounding whitespace in the JSON config
        await _insert_binding(db, dp_id, {"group_address": " 2/3/4 "})
        await db.commit()

        result = await _datapoints_by_group_address(["2/3/4"], db)
        assert len(result["2/3/4"]) == 1, "Binding with whitespace-padded GA must be found when querying with trimmed GA"
        assert str(result["2/3/4"][0].id) == dp_id
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_datapoints_by_group_address_state_ga_with_whitespace():
    """state_group_address stored with padding must also match."""
    db = await _make_db()
    try:
        dp_id = str(uuid.uuid4())
        await _insert_dp(db, dp_id, "Jalousie")
        await _insert_binding(db, dp_id, {"group_address": "3/4/5", "state_group_address": " 3/4/6 "})
        await db.commit()

        result = await _datapoints_by_group_address(["3/4/6"], db)
        assert len(result["3/4/6"]) == 1, "Binding with whitespace-padded state_group_address must be found"
        assert str(result["3/4/6"][0].id) == dp_id
    finally:
        await db.disconnect()
