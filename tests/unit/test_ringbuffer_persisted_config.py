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
    DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES,
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
async def test_load_migrates_legacy_all_unbounded_config_to_visible_age_rotation():
    """An old all-null segmented config must not restart without any rotation trigger."""
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (
                PERSISTED_CONFIG_KEY,
                json.dumps(
                    {
                        "enabled": True,
                        "max_entries": None,
                        "max_file_size_bytes": None,
                        "max_age": None,
                        "segmented": True,
                        "segment_max_bytes": None,
                        "segment_max_rows": None,
                        "segment_max_age": None,
                    }
                ),
            ),
        )
        await db.commit()

        cfg = await load_persisted_ringbuffer_config(db)

        assert cfg["segment_max_bytes"] is None
        assert cfg["segment_max_rows"] is None
        assert cfg["segment_max_age"] == DEFAULT_SEGMENT_MAX_AGE_SECONDS
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
@pytest.mark.parametrize("tiny_max_age", [1, 2])
async def test_migrated_config_with_sub_three_second_max_age_does_not_crash_startup(tiny_max_age):
    """#951: Alt-Config mit ``max_age`` = 1 oder 2 s (ohne Segment-Keys) darf nicht crashen.

    ``max_age // 3`` ist hier 0; das frühere ``max(1, 0)`` = 1 setzte
    ``segment_max_age = 1``, doch die 3-Segment-Regel verlangt
    ``max_age >= 3 * segment_max_age`` (= 3) → 1 bzw. 2 < 3 → ``validate_store_config``
    crasht beim Startup. Für diesen degenerierten Sub-3-Sekunden-Fall darf kein
    positives ``segment_max_age`` abgeleitet werden; ``load`` liefert stattdessen
    ``None`` (kein zeitgetriebener Rotations-Trigger), sodass die Regel nicht greift
    und der Store-Init nicht crasht.
    """
    from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig, validate_store_config

    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": tiny_max_age})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_age"] is None
        # 3-Segment-Regel greift bei segment_max_age=None nicht → kein Crash.
        validate_store_config(
            SegmentConfig(segment_max_age=cfg["segment_max_age"]),
            StoreRetentionConfig(max_age=cfg["max_age"]),
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migrated_config_with_max_age_three_derives_one_second_segment():
    """Grenzfall ``max_age`` = 3 s: ``3 // 3`` = 1 erfüllt die Regel (``3 >= 3 * 1``) noch."""
    from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig, validate_store_config

    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": 3})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["segment_max_age"] == 1
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
async def test_migrated_config_with_zero_max_age_is_normalized_to_none():
    """#951: Eine Alt-Config mit ``max_age: 0`` darf den Startup nicht crashen.

    Das API-Modell erlaubte frueher ``max_age: 0``. Ohne Segment-Keys schaltet der
    Default jetzt Segmentierung ein und reichte die persistierte ``0`` unveraendert
    an ``StoreRetentionConfig`` weiter, dessen Validierung ``>= 1`` oder ``null``
    verlangt → Ringbuffer-Init crasht, bevor ein Admin es korrigieren kann. ``load``
    muss ``max_age: 0`` daher als ``None`` (unbegrenzt) normalisieren.
    """
    from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig, validate_store_config

    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": 0})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["max_age"] is None
        # Ohne max_age greift die 3-Segment-Regel nicht → 6-h-Default bleibt.
        assert cfg["segment_max_age"] == DEFAULT_SEGMENT_MAX_AGE_SECONDS
        # StoreRetentionConfig akzeptiert None → kein Startup-Crash.
        StoreRetentionConfig(max_age=cfg["max_age"])
        validate_store_config(
            SegmentConfig(segment_max_age=cfg["segment_max_age"]),
            StoreRetentionConfig(max_age=cfg["max_age"]),
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_persisted_positive_max_age_is_preserved():
    """Ein gueltiges positives ``max_age`` bleibt unveraendert (kein Ueberklemmen auf None)."""
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (PERSISTED_CONFIG_KEY, json.dumps({"max_age": 7200})),
        )
        await db.commit()
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["max_age"] == 7200
    finally:
        await db.disconnect()


def test_default_upgrade_max_file_size_is_ten_mebibytes():
    assert DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_no_config_row_but_existing_storage_keeps_legacy_10mib_budget(tmp_path):
    """#951 [P3]: Upgrade ohne Config-Zeile bewahrt das vorherige 10-MiB-Budget.

    Eine upgegradete Installation, die nie Monitor-Settings gespeichert hat, hat KEINE
    ``ringbuffer.runtime_config``-Zeile, aber bereits Ringbuffer-Storage auf der Platte
    (Legacy-DB oder Segment-Root). Ohne Sonderbehandlung springt das Budget still von
    vormals 10 MiB auf den neuen 100-MiB-Fresh-Install-Default. Existiert eine
    Storage-Spur, muss der Legacy-10-MiB-Default bewahrt werden.
    """
    db = Database(":memory:")
    await db.connect()
    try:
        storage_path = tmp_path / "obs_ringbuffer.db"
        storage_path.write_bytes(b"")  # Legacy-DB-Datei existiert → Upgrade-Indikator.
        cfg = await load_persisted_ringbuffer_config(db, storage_path=str(storage_path))
        assert cfg["max_file_size_bytes"] == DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_no_config_row_but_existing_segment_root_keeps_legacy_10mib_budget(tmp_path):
    """#951 [P3]: Auch ein vorhandener Segment-Root (v2-Store) zaehlt als Upgrade-Spur."""
    db = Database(":memory:")
    await db.connect()
    try:
        storage_path = tmp_path / "obs_ringbuffer.db"
        (tmp_path / "obs_ringbuffer_segments").mkdir()  # Segment-Root existiert.
        cfg = await load_persisted_ringbuffer_config(db, storage_path=str(storage_path))
        assert cfg["max_file_size_bytes"] == DEFAULT_UPGRADE_MAX_FILE_SIZE_BYTES
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_no_config_row_and_no_storage_trace_is_fresh_install_100mib(tmp_path):
    """#951 [P3]: Wirklich frische Installation (keine Storage-Spur) bekommt den 100-MiB-Default."""
    db = Database(":memory:")
    await db.connect()
    try:
        storage_path = tmp_path / "obs_ringbuffer.db"  # existiert NICHT.
        cfg = await load_persisted_ringbuffer_config(db, storage_path=str(storage_path))
        assert cfg["max_file_size_bytes"] == DEFAULT_MAX_FILE_SIZE_BYTES
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_no_storage_path_defaults_to_fresh_install_budget():
    """Ohne ``storage_path`` (z. B. In-Memory-DB) bleibt der 100-MiB-Fresh-Install-Default."""
    db = Database(":memory:")
    await db.connect()
    try:
        cfg = await load_persisted_ringbuffer_config(db)
        assert cfg["max_file_size_bytes"] == DEFAULT_MAX_FILE_SIZE_BYTES
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_existing_config_row_ignores_storage_trace(tmp_path):
    """Existiert eine Config-Zeile, ist die Upgrade-Heuristik irrelevant – der persistierte Wert gilt."""
    db = Database(":memory:")
    await db.connect()
    try:
        storage_path = tmp_path / "obs_ringbuffer.db"
        storage_path.write_bytes(b"")
        await persist_ringbuffer_config(
            db,
            enabled=True,
            max_entries=None,
            max_file_size_bytes=42 * 1024 * 1024,
            max_age=None,
        )
        cfg = await load_persisted_ringbuffer_config(db, storage_path=str(storage_path))
        assert cfg["max_file_size_bytes"] == 42 * 1024 * 1024
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
