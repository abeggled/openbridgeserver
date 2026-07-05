"""Persisted ringbuffer runtime config.

Stored in ``app_settings`` under ``ringbuffer.runtime_config`` as JSON. The
values mirror the ``POST /api/v1/ringbuffer/config`` payload (``enabled``,
``max_entries``, ``max_file_size_bytes``, ``max_age``). When no row exists,
``load`` returns sane defaults — the monitor is enabled and only
``max_file_size_bytes`` has a non-null fallback: 100 MiB for a fresh install,
but the prior 10 MiB for an upgrade that already has ringbuffer storage on disk
yet no saved config (#951 [P3]), so the limit does not silently jump on upgrade.

Why DB-backed rather than YAML/env: keeps UI-driven changes intact across
container restarts and rebuilds, matches the pattern already used for
history.*, autobackup.*, and ringbuffer.export_settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from obs.db.database import Database

PERSISTED_CONFIG_KEY = "ringbuffer.runtime_config"

# Entscheidungszustand des Legacy-Migrations-Assistenten (#964). Eigener
# app_settings-Key (reiner String), damit der Zustand unabhängig von der
# Runtime-Config gelesen/geschrieben werden kann.
LEGACY_MIGRATION_DECISION_KEY = "ringbuffer.legacy_migration_decision"

# Zustände: ``pending`` (Upgrade erkannt, keine Entscheidung), ``skipped``
# (Wizard dismisst – revidierbar), ``keep`` (bewusst read-only behalten bis die
# FIFO-Retention greift – revidierbar), ``migrated``/``discarded`` (terminal).
LEGACY_DECISION_PENDING = "pending"
LEGACY_DECISION_KEEP = "keep"
LEGACY_DECISION_SKIPPED = "skipped"
LEGACY_DECISION_MIGRATED = "migrated"
LEGACY_DECISION_DISCARDED = "discarded"
LEGACY_DECISIONS = (
    LEGACY_DECISION_PENDING,
    LEGACY_DECISION_KEEP,
    LEGACY_DECISION_SKIPPED,
    LEGACY_DECISION_MIGRATED,
    LEGACY_DECISION_DISCARDED,
)
# Terminale Zustände: die Legacy-Quelle existiert danach nicht mehr (migriert
# bzw. verworfen) – keine weitere Entscheidung möglich.
LEGACY_DECISIONS_TERMINAL = (LEGACY_DECISION_MIGRATED, LEGACY_DECISION_DISCARDED)
# Zustände OHNE informierte Entscheidung: das Legacy-Segment bleibt vor der
# FIFO-Retention geschützt (``StoreRetentionConfig.protect_legacy``). ``keep``
# ist bewusst NICHT enthalten – der Admin hat die Alles-oder-nichts-Rückgewinnung
# dann explizit akzeptiert.
LEGACY_DECISIONS_PROTECTED = (LEGACY_DECISION_PENDING, LEGACY_DECISION_SKIPPED)

# Sentinel: unterscheidet "``segment_max_age`` fehlt in der persistierten Config"
# (Alt-Config vor der Segmentierung) von einem explizit persistierten ``None``.
_UNSET = object()

# Muss zur ``RETENTION_SEGMENT_RATIO`` in ``obs.ringbuffer.store.config`` passen
# (3-Segment-Regel). Bewusst dupliziert, um keinen Import-Zyklus/Store-Import in
# diesem reinen DB-Config-Modul zu erzeugen.
_RETENTION_SEGMENT_RATIO = 3

DEFAULT_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB (Fresh-Install-Default, #919)

# Vormaliger Default vor der bewussten Anhebung auf 100 MiB (#919). Wird fuer
# UPGEGRADETE Installationen bewahrt, die Ringbuffer-Storage besitzen, aber nie
# Monitor-Settings gespeichert haben (keine Config-Zeile) – siehe
# ``load_persisted_ringbuffer_config`` (#951 [P3]).
DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB (Upgrade-ohne-Config-Default)

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
    abgeleiteten Wert auf ``max_age // RATIO`` klemmen. Explizit persistierte Werte
    (auch ``None``) bleiben unangetastet.

    Degenerierter Sub-3-Sekunden-Fall (#951): ist ``max_age`` kleiner als
    ``RETENTION_SEGMENT_RATIO`` (= 3, also 1 oder 2 s), ergibt ``max_age // RATIO``
    0 – es existiert KEIN positives ganzzahliges ``segment_max_age``, das die
    3-Segment-Regel ``max_age >= 3 * segment_max_age`` erfüllt. Ein früher hier auf
    1 hochgeklemmter Wert ließ ``validate_store_config`` beim Startup crashen. Für
    diese entarteten Werte wird daher ``None`` zurückgegeben (kein zeitgetriebener
    Segment-Trigger, analog zur Tiny-Budget-Behandlung): die Regel greift dann nicht,
    Size-/Row-Trigger segmentieren weiterhin, und ein Admin kann ``max_age`` später
    gefahrlos korrigieren. Ab ``max_age`` = 3 wird regulär auf ``max_age // RATIO``
    (mindestens 1) geklemmt.
    """
    if persisted_segment_max_age is not _UNSET:
        return persisted_segment_max_age
    if max_age is None or default_segment_max_age is None:
        return default_segment_max_age
    derived = max_age // _RETENTION_SEGMENT_RATIO
    if derived < 1:
        return None
    return min(default_segment_max_age, derived)


def _ringbuffer_storage_exists(storage_path: str | None) -> bool:
    """Erkennt, ob bereits Ringbuffer-Storage auf der Platte liegt (#951 [P3]).

    Als robuste, testbare Upgrade-Spur gelten:
    * die Legacy-Single-File-DB ``<stem>.db`` (oder ihre WAL/SHM-Sidecars), oder
    * das Segment-Store-Root-Verzeichnis ``<stem>_segments`` des v2-Stores.

    ``storage_path`` ist der Pfad der Legacy-Ringbuffer-DB (z. B.
    ``obs_ringbuffer.db``); fehlt er (``None`` oder In-Memory), wird als „keine Spur"
    gewertet.
    """
    if not storage_path:
        return False
    db_path = Path(storage_path)
    if db_path.suffix == "":  # ``:memory:`` o. ae. – kein Dateisystem-Pfad.
        return False
    candidates = [
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
        db_path.with_name(f"{db_path.stem}_segments"),
    ]
    return any(candidate.exists() for candidate in candidates)


async def load_persisted_ringbuffer_config(db: Database, *, storage_path: str | None = None) -> dict[str, Any]:
    """Laedt die persistierte Ringbuffer-Config oder liefert Defaults.

    Fehlt die Config-Zeile, wird zwischen frischer Installation und Upgrade ohne
    gespeicherte Monitor-Settings unterschieden (#951 [P3]): existiert bereits
    Ringbuffer-Storage auf der Platte (``storage_path`` zeigt auf eine vorhandene
    Legacy-DB oder ein ``<stem>_segments``-Root), wird das vormalige 10-MiB-Budget
    bewahrt, damit das Limit fuer bestehende Installationen nicht still auf 100 MiB
    springt. Ohne jede Storage-Spur (oder ohne ``storage_path``) gilt der bewusste
    100-MiB-Fresh-Install-Default.
    """
    row = await db.fetchone("SELECT value FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
    if not row or not row["value"]:
        defaults = _defaults()
        if _ringbuffer_storage_exists(storage_path):
            defaults["max_file_size_bytes"] = DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES
        return defaults
    try:
        data = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()

    defaults = _defaults()
    # Persistierte ``max_age: 0`` als ``None`` (unbegrenzt) normalisieren (#951): Das
    # API-Modell erlaubte frueher die 0. ``StoreRetentionConfig`` verlangt aber
    # ``>= 1`` oder ``null`` – wuerde die rohe 0 an den Store-Init durchgereicht,
    # crashte der Ringbuffer beim Startup, bevor ein Admin es korrigieren kann.
    max_age = data.get("max_age", defaults["max_age"])
    if max_age == 0:
        max_age = None
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


async def load_legacy_migration_decision(db: Database) -> str | None:
    """Liest den Entscheidungszustand des Migrations-Assistenten (#964).

    ``None`` = kein Zustand vorhanden (Fresh Install ohne Legacy-DB, oder der
    Startup lief noch nie mit vorhandener Legacy-Quelle). Unbekannte Werte
    (manueller Edit) werden konservativ als ``None`` behandelt.
    """
    row = await db.fetchone("SELECT value FROM app_settings WHERE key=?", (LEGACY_MIGRATION_DECISION_KEY,))
    value = row["value"] if row else None
    return value if value in LEGACY_DECISIONS else None


async def persist_legacy_migration_decision(db: Database, decision: str) -> None:
    """Persistiert den Entscheidungszustand des Migrations-Assistenten (#964)."""
    if decision not in LEGACY_DECISIONS:
        raise ValueError(f"unknown legacy migration decision: {decision!r}")
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (LEGACY_MIGRATION_DECISION_KEY, decision),
    )
    await db.commit()


async def ensure_legacy_migration_decision(db: Database, *, legacy_db_path: str | None) -> str | None:
    """Stellt beim Startup den Entscheidungszustand sicher (#964).

    * Existiert bereits ein Zustand → unverändert zurückgeben.
    * Sonst: liegt eine Legacy-Single-DB auf der Platte (Upgrade-Fall), wird
      ``pending`` persistiert und zurückgegeben – der Wizard erscheint, das
      Legacy-Segment bleibt bis zur Entscheidung retention-geschützt.
    * Ohne Legacy-Datei (Fresh Install, Memory-Pfad) bleibt der Zustand leer.
    """
    existing = await load_legacy_migration_decision(db)
    if existing is not None:
        return existing
    if not legacy_db_path:
        return None
    db_path = Path(legacy_db_path)
    if db_path.suffix == "" or not db_path.exists():
        return None
    await persist_legacy_migration_decision(db, LEGACY_DECISION_PENDING)
    return LEGACY_DECISION_PENDING


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
