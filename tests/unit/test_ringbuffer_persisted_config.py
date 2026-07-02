"""Unit tests for persisted ringbuffer runtime config.

Background:
    Previously the ringbuffer config (``max_entries``, ``max_file_size_bytes``,
    ``max_age``) lived in ``Settings.ringbuffer`` and was sourced from env vars
    or YAML — never persisted. Any UI change via ``POST /api/v1/ringbuffer/config``
    was lost on container restart because startup re-read the defaults (which
    pinned ``max_entries`` to 10 000).

    These tests pin the new behavior: the config lives in ``app_settings``
    under ``ringbuffer.runtime_config`` and falls back to sane defaults only
    when nothing is persisted yet. The single hard default is
    ``max_file_size_bytes = 10 MiB``; ``max_entries`` and ``max_age`` default
    to ``None`` (unbounded).
"""

from __future__ import annotations

import json

import pytest

from obs.db.database import Database
from obs.ringbuffer.persisted_config import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    PERSISTED_CONFIG_KEY,
    load_persisted_ringbuffer_config,
    persist_ringbuffer_config,
)


def test_default_max_file_size_is_hundred_mebibytes():
    assert DEFAULT_MAX_FILE_SIZE_BYTES == 100 * 1024 * 1024


def test_default_segment_max_age_is_six_hours():
    assert DEFAULT_SEGMENT_MAX_AGE_SECONDS == 6 * 60 * 60


@pytest.mark.asyncio
async def test_load_returns_defaults_when_nothing_persisted():
    db = Database(":memory:")
    await db.connect()
    try:
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": True,
            "max_entries": None,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
            # Deployter Default (#919): segmentiert + 6-h-Zeitrotation.
            "segmented": True,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persist_then_load_roundtrip():
    db = Database(":memory:")
    await db.connect()
    try:
        await persist_ringbuffer_config(
            db,
            enabled=True,
            max_entries=50_000,
            max_file_size_bytes=20 * 1024 * 1024,
            max_age=3600,
        )
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": True,
            "max_entries": 50_000,
            "max_file_size_bytes": 20 * 1024 * 1024,
            "max_age": 3600,
            "segmented": False,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": None,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persist_supports_unbounded_max_entries_and_age():
    db = Database(":memory:")
    await db.connect()
    try:
        await persist_ringbuffer_config(
            db,
            enabled=False,
            max_entries=None,
            max_file_size_bytes=5 * 1024 * 1024,
            max_age=None,
        )
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": False,
            "max_entries": None,
            "max_file_size_bytes": 5 * 1024 * 1024,
            "max_age": None,
            "segmented": False,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": None,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persist_overwrites_existing_row():
    db = Database(":memory:")
    await db.connect()
    try:
        await persist_ringbuffer_config(db, enabled=True, max_entries=100, max_file_size_bytes=1024, max_age=10)
        await persist_ringbuffer_config(db, enabled=False, max_entries=200, max_file_size_bytes=2048, max_age=20)
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": False,
            "max_entries": 200,
            "max_file_size_bytes": 2048,
            "max_age": 20,
            "segmented": False,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": None,
        }
        rows = await db.fetchall("SELECT key FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
        assert len(rows) == 1
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_load_handles_corrupt_json_by_returning_defaults():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, "{not valid json"),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": True,
            "max_entries": None,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
            "segmented": True,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_load_fills_missing_keys_with_defaults():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_entries": 1234})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "enabled": True,
            "max_entries": 1234,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
            "segmented": True,
            "segment_max_bytes": None,
            "segment_max_rows": None,
            "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persist_then_load_segment_params_roundtrip():
    db = Database(":memory:")
    await db.connect()
    try:
        await persist_ringbuffer_config(
            db,
            enabled=True,
            max_entries=None,
            max_file_size_bytes=None,
            max_age=None,
            segment_max_bytes=1000,
            segment_max_rows=100,
            segment_max_age=60,
        )
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_bytes"] == 1000
        assert cfg["segment_max_rows"] == 100
        assert cfg["segment_max_age"] == 60
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migrated_old_config_with_short_max_age_clamps_segment_max_age():
    """#951: Alt-Config mit ``max_age`` aber ohne Segment-Keys darf den Startup nicht crashen.

    Eine pre-Segmentierung-Config kennt nur ``max_age`` (kurze Monitor-Retention,
    z. B. 1 h). Ohne Klemmung würde ``load`` den 6-h-Default (21600 s) einsetzen;
    die 3-Segment-Regel des Stores verlangt dann ``max_age >= 3 * segment_max_age``
    (= 64800 s) → Ringbuffer-Init crasht. ``load`` muss stattdessen ein
    ``segment_max_age`` liefern, das die Regel erfüllt.
    """
    from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig, validate_store_config

    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": 3600})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        # 3600 // 3 = 1200 s (unter dem 6-h-Default) → gewählt.
        assert cfg["segment_max_age"] == 1200
        # Die 3-Segment-Regel hält jetzt und der Store-Init crasht nicht.
        validate_store_config(
            SegmentConfig(segment_max_age=cfg["segment_max_age"]),
            StoreRetentionConfig(max_age=cfg["max_age"]),
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migrated_config_without_max_age_keeps_default_segment_max_age():
    """Ohne ``max_age`` (unbegrenzte Retention) greift die 3-Segment-Regel nicht → 6-h-Default bleibt."""
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_entries": 5000})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_age"] == DEFAULT_SEGMENT_MAX_AGE_SECONDS
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_explicit_persisted_segment_max_age_is_not_clamped():
    """Ein explizit persistiertes ``segment_max_age`` bleibt unangetastet (auch bei kurzem max_age)."""
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": 3600, "segment_max_age": 900})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_age"] == 900
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_load_returns_defaults_when_persisted_value_is_not_a_dict():
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps([1, 2, 3])),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_bytes"] is None
        assert cfg["max_file_size_bytes"] == DEFAULT_MAX_FILE_SIZE_BYTES
    finally:
        await db.disconnect()
