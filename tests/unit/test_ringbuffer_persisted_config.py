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
    PERSISTED_CONFIG_KEY,
    load_persisted_ringbuffer_config,
    persist_ringbuffer_config,
)


def test_default_max_file_size_is_ten_mebibytes():
    assert DEFAULT_MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_load_returns_defaults_when_nothing_persisted():
    db = Database(":memory:")
    await db.connect()
    try:
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "max_entries": None,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
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
            max_entries=50_000,
            max_file_size_bytes=20 * 1024 * 1024,
            max_age=3600,
        )
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "max_entries": 50_000,
            "max_file_size_bytes": 20 * 1024 * 1024,
            "max_age": 3600,
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
            max_entries=None,
            max_file_size_bytes=5 * 1024 * 1024,
            max_age=None,
        )
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "max_entries": None,
            "max_file_size_bytes": 5 * 1024 * 1024,
            "max_age": None,
        }
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persist_overwrites_existing_row():
    db = Database(":memory:")
    await db.connect()
    try:
        await persist_ringbuffer_config(db, max_entries=100, max_file_size_bytes=1024, max_age=10)
        await persist_ringbuffer_config(db, max_entries=200, max_file_size_bytes=2048, max_age=20)
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg == {
            "max_entries": 200,
            "max_file_size_bytes": 2048,
            "max_age": 20,
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
            "max_entries": None,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
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
            "max_entries": 1234,
            "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
            "max_age": None,
        }
    finally:
        await db.disconnect()
