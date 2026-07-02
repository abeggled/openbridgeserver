"""Persisted ringbuffer runtime config.

Stored in ``app_settings`` under ``ringbuffer.runtime_config`` as JSON. The
values mirror the ``POST /api/v1/ringbuffer/config`` payload (``enabled``,
``max_entries``, ``max_file_size_bytes``, ``max_age``). When no row exists,
``load`` returns sane defaults — the monitor is enabled and only
``max_file_size_bytes`` has a non-null fallback (100 MiB).

Why DB-backed rather than YAML/env: keeps UI-driven changes intact across
container restarts and rebuilds, matches the pattern already used for
history.*, autobackup.*, and ringbuffer.export_settings.
"""

from __future__ import annotations

import json
from typing import Any

from obs.db.database import Database

PERSISTED_CONFIG_KEY = "ringbuffer.runtime_config"

# Sentinel: unterscheidet "``segment_max_age`` fehlt in der persistierten Config"
# (Alt-Config vor der Segmentierung) von einem explizit persistierten ``None``.
_UNSET = object()

# Muss zur ``RETENTION_SEGMENT_RATIO`` in ``obs.ringbuffer.store.config`` passen
# (3-Segment-Regel). Bewusst dupliziert, um keinen Import-Zyklus/Store-Import in
# diesem reinen DB-Config-Modul zu erzeugen.
_RETENTION_SEGMENT_RATIO = 3

DEFAULT_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB (Fresh-Install-Default, #919)

# Deployter Default für die zeitgetriebene Rotation (#919): alle 6 Stunden ein
# neues Segment. Zeit ist im Normalbetrieb der PRIMÄRE Rotations-Trigger; die aus
# ``max_file_size_bytes`` abgeleitete ``segment_max_bytes`` ist nur die Größen-
# Notbremse. ``segment_max_rows`` bleibt None (kein row-getriebener Trigger).
DEFAULT_SEGMENT_MAX_AGE_SECONDS = 6 * 60 * 60  # 21600 s (6 h)


def _defaults() -> dict[str, Any]:
    return {
        "enabled": True,
        "max_entries": None,
        "max_file_size_bytes": DEFAULT_MAX_FILE_SIZE_BYTES,
        "max_age": None,
        # Segmentierter Store (#919) — DEPLOYTER DEFAULT: segmentiert. Bestehende
        # Installationen ohne persistierten ``segmented``-Key laufen damit
        # automatisch segmentiert; der Legacy-Single-File-Pfad bleibt nur intern
        # (Tests/Legacy) über ``segmented=False`` erreichbar.
        "segmented": True,
        # Segment-Parameter (#930/#919): ``segment_max_bytes`` wird beim Start aus
        # ``max_file_size_bytes`` abgeleitet, wenn hier None (siehe RingBuffer).
        # ``segment_max_age`` ist der zeitgetriebene Default-Trigger (6 h).
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    }


def _resolve_migrated_segment_max_age(
    *,
    persisted_segment_max_age: Any,
    default_segment_max_age: int | None,
    max_age: int | None,
) -> int | None:
    """Leitet ``segment_max_age`` für migrierte Alt-Configs so ab, dass die 3-Segment-Regel hält (#951).

    Eine Config aus der Zeit vor der Segmentierung kennt ``max_age`` (die Monitor-
    Retention), aber keinen ``segment_max_age``-Key. Würde hier stur der 6-h-Default
    (21600 s) eingesetzt, verlangt die 3-Segment-Regel des Stores
    ``max_age >= 3 * segment_max_age`` (= 64800 s) — jede Installation mit kürzerer
    Retention (z. B. 15 min / 1 h) crasht beim Ringbuffer-Init, bevor ein Admin die
    Config ändern kann.

    Fix: nur wenn ``segment_max_age`` FEHLT und ``max_age`` gesetzt ist, den
    abgeleiteten Wert auf ``max_age // RATIO`` klemmen (mindestens 1). Explizit
    persistierte Werte (auch ``None``) bleiben unangetastet.
    """
    if persisted_segment_max_age is not _UNSET:
        return persisted_segment_max_age
    if max_age is None or default_segment_max_age is None:
        return default_segment_max_age
    return min(default_segment_max_age, max(1, max_age // _RETENTION_SEGMENT_RATIO))


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
    max_age = data.get("max_age", defaults["max_age"])
    segment_max_age = _resolve_migrated_segment_max_age(
        persisted_segment_max_age=data.get("segment_max_age", _UNSET),
        default_segment_max_age=defaults["segment_max_age"],
        max_age=max_age,
    )
    return {
        "enabled": bool(data.get("enabled", defaults["enabled"])),
        "max_entries": data.get("max_entries", defaults["max_entries"]),
        "max_file_size_bytes": data.get("max_file_size_bytes", defaults["max_file_size_bytes"]),
        "max_age": max_age,
        "segmented": bool(data.get("segmented", defaults["segmented"])),
        "segment_max_bytes": data.get("segment_max_bytes", defaults["segment_max_bytes"]),
        "segment_max_rows": data.get("segment_max_rows", defaults["segment_max_rows"]),
        "segment_max_age": segment_max_age,
    }


async def persist_ringbuffer_config(
    db: Database,
    *,
    enabled: bool,
    max_entries: int | None,
    max_file_size_bytes: int | None,
    max_age: int | None,
    segmented: bool = False,
    segment_max_bytes: int | None = None,
    segment_max_rows: int | None = None,
    segment_max_age: int | None = None,
) -> None:
    payload = json.dumps(
        {
            "enabled": bool(enabled),
            "max_entries": max_entries,
            "max_file_size_bytes": max_file_size_bytes,
            "max_age": max_age,
            "segmented": bool(segmented),
            "segment_max_bytes": segment_max_bytes,
            "segment_max_rows": segment_max_rows,
            "segment_max_age": segment_max_age,
        }
    )
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (PERSISTED_CONFIG_KEY, payload),
    )
    await db.commit()
