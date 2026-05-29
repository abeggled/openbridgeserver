"""Persisted ringbuffer runtime config.

Stored in ``app_settings`` under ``ringbuffer.runtime_config`` as JSON. The
values mirror the ``POST /api/v1/ringbuffer/config`` payload (``max_entries``,
``max_file_size_bytes``, ``max_age``). When no row exists, ``load`` returns
sane defaults — only ``max_file_size_bytes`` has a non-null fallback (10 MiB).

Why DB-backed rather than YAML/env: keeps UI-driven changes intact across
container restarts and rebuilds, matches the pattern already used for
history.*, autobackup.*, and ringbuffer.export_settings.
"""

from __future__ import annotations

import json
from typing import Any

from obs.db.database import Database

PERSISTED_CONFIG_KEY = "ringbuffer.runtime_config"

DEFAULT_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB


def _defaults() -> dict[str, Any]:
    return {
        "max_entries": None,
        "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
        "max_age": None,
    }


async def load_persisted_ringbuffer_config(db: Database) -> dict[str, Any]:
    row = await db.fetchone("SELECT value FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
    if not row or not row["value"]:
        return _defaults()
    try:
        data = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()

    defaults = _defaults()
    return {
        "max_entries": data.get("max_entries", defaults["max_entries"]),
        "max_file_size_bytes": data.get("max_file_size_bytes", defaults["max_file_size_bytes"]),
        "max_age": data.get("max_age", defaults["max_age"]),
    }


async def persist_ringbuffer_config(
    db: Database,
    *,
    max_entries: int | None,
    max_file_size_bytes: int | None,
    max_age: int | None,
) -> None:
    payload = json.dumps(
        {
            "max_entries": max_entries,
            "max_file_size_bytes": max_file_size_bytes,
            "max_age": max_age,
        }
    )
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (PERSISTED_CONFIG_KEY, payload),
    )
    await db.commit()
