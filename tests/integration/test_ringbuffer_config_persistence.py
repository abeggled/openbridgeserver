"""Integration tests for ringbuffer config persistence (issue: monitor-ringbuffer).

The ``POST /api/v1/ringbuffer/config`` endpoint must persist the runtime
config (max_entries, max_file_size_bytes, max_age) so values survive
container restarts. Defaults apply only when nothing is persisted yet.
"""

from __future__ import annotations

import json

import pytest

from obs.db.database import get_db
from obs.ringbuffer.persisted_config import (
    DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    PERSISTED_CONFIG_KEY,
    load_persisted_ringbuffer_config,
)

pytestmark = pytest.mark.integration


async def _read_persisted_row():
    row = await get_db().fetchone("SELECT value FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
    return json.loads(row["value"]) if row and row["value"] else None


async def _reset_to_defaults(client, auth_headers):
    """Keep the session-scoped app stable for unrelated tests.

    Mirrors the reset pattern in ``test_ringbuffer_filters.py`` so tests can
    run in any order without leaking ringbuffer state between them.
    """
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_config_post_persists_full_payload_to_app_settings(client, auth_headers):
    # ``segmented`` is now the deployed default. ``segment_max_age`` is sent
    # explicitly so the 3-segment age rule (max_age >= 3*segment_max_age) holds:
    # 7200 >= 3*2400. The full payload must round-trip verbatim.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 42_000,
            "max_file_size_bytes": 5 * 1024 * 1024,
            "max_age": 7200,
            "segment_max_age": 2400,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    persisted = await _read_persisted_row()
    assert persisted == {
        "enabled": True,
        "max_entries": 42_000,
        "max_file_size_bytes": 5 * 1024 * 1024,
        "max_age": 7200,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": 2400,
    }

    await _reset_to_defaults(client, auth_headers)


async def test_config_post_persists_null_max_entries(client, auth_headers):
    # max_age=None → the age ratio rule is inactive, so the default 6-h
    # segment_max_age passes through untouched and is persisted.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": None,
            "max_file_size_bytes": 3 * 1024 * 1024,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    persisted = await _read_persisted_row()
    assert persisted == {
        "enabled": True,
        "max_entries": None,
        "max_file_size_bytes": 3 * 1024 * 1024,
        "max_age": None,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    }

    await _reset_to_defaults(client, auth_headers)


async def test_load_persisted_ringbuffer_config_after_post_matches_payload(client, auth_headers):
    """Round-trip via the public API loader used by main.py at startup."""
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 25_000,
            "max_file_size_bytes": 8 * 1024 * 1024,
            "max_age": 3600,
            "segment_max_age": 1200,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    cfg = await load_persisted_ringbuffer_config(get_db())
    assert cfg == {
        "enabled": True,
        "max_entries": 25_000,
        "max_file_size_bytes": 8 * 1024 * 1024,
        "max_age": 3600,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": 1200,
    }

    await _reset_to_defaults(client, auth_headers)
