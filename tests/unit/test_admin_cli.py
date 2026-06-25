from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from obs.admin_cli import (
    AdminCliError,
    build_parser,
    create_backup,
    create_support_package,
    database_info,
    list_bindings,
    resolve_database_path,
    set_adapter_enabled,
    set_binding_enabled,
    set_loglevel,
    validate_config,
)
from obs.admin_cli import _normalize_global_options


def _make_db(path: Path) -> dict[str, str]:
    instance_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    dp_id = str(uuid.uuid4())
    now = "2026-06-25T12:00:00+00:00"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_version (version, applied_at) VALUES (37, 'now');
        CREATE TABLE adapter_instances (
            id TEXT PRIMARY KEY,
            adapter_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE datapoints (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            data_type TEXT NOT NULL DEFAULT 'UNKNOWN',
            unit TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            mqtt_topic TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE adapter_bindings (
            id TEXT PRIMARY KEY,
            datapoint_id TEXT NOT NULL,
            adapter_type TEXT NOT NULL,
            adapter_instance_id TEXT,
            direction TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            value_map TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        CREATE TABLE history_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datapoint_id TEXT NOT NULL,
            value TEXT NOT NULL,
            quality TEXT NOT NULL,
            ts TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO adapter_instances VALUES (?,?,?,?,?,?,?)",
        (
            instance_id,
            "MQTT",
            "mqtt.internal.local",
            json.dumps({"host": "192.168.1.10", "password": "secret", "client_id": "client-a"}),
            1,
            now,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO datapoints VALUES (?,?,?,?,?,?,?,?)",
        (dp_id, "DP 1", "STRING", None, "[]", "dp/topic", now, now),
    )
    conn.execute(
        "INSERT INTO adapter_bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            binding_id,
            dp_id,
            "MQTT",
            instance_id,
            "SOURCE",
            json.dumps({"topic": "home/kitchen", "token": "binding-secret"}),
            1,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "binding_id": binding_id, "dp_id": dp_id}


def test_resolve_database_path_from_explicit_arg(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    db_path.write_bytes(b"")

    assert resolve_database_path(str(db_path)) == db_path


def test_global_options_are_accepted_after_subcommands():
    parser = build_parser()

    args = parser.parse_args(_normalize_global_options(["adapters", "list", "--json", "--db", "/tmp/obs.db"]))

    assert args.command == "adapters"
    assert args.adapters_command == "list"
    assert args.json is True
    assert args.db == "/tmp/obs.db"


def test_resolve_database_path_from_yaml_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "from-config.db"
    db_path.write_bytes(b"")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {db_path}\n", encoding="utf-8")
    monkeypatch.setenv("OBS_CONFIG", str(config_path))
    monkeypatch.delenv("OBS_DATABASE__PATH", raising=False)

    assert resolve_database_path() == db_path


def test_resolve_database_path_uses_legacy_opentws_fallback_for_auto_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    obs_path = tmp_path / "obs.db"
    legacy_path = tmp_path / "opentws.db"
    legacy_path.write_bytes(b"legacy")
    monkeypatch.setenv("OBS_DATABASE__PATH", str(obs_path))

    assert resolve_database_path() == legacy_path


def test_resolve_database_path_explicit_arg_does_not_use_legacy_fallback(tmp_path: Path):
    obs_path = tmp_path / "obs.db"
    legacy_path = tmp_path / "opentws.db"
    legacy_path.write_bytes(b"legacy")

    with pytest.raises(AdminCliError, match="Keine OBS-Konfigurationsdatenbank"):
        resolve_database_path(str(obs_path))


def test_database_info_handles_suffixless_db_paths(tmp_path: Path):
    db_path = tmp_path / "obs"
    _make_db(db_path)
    Path(f"{db_path}-wal").write_bytes(b"wal")
    Path(f"{db_path}-shm").write_bytes(b"shm")

    info = database_info(db_path)

    assert info["path"] == str(db_path)
    assert info["wal_exists"] is True
    assert info["shm_exists"] is True


def test_automatic_backup_names_are_unique_within_same_second(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    first = create_backup(db_path)
    second = create_backup(db_path)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_adapter_enable_disable_offline_updates_timestamp_and_backup(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)

    result = set_adapter_enabled(db_path, ids["instance_id"], False)

    assert result["enabled"] is False
    assert result["updated_at"] != "2026-06-25T12:00:00+00:00"
    assert result["backup"]
    assert Path(result["backup"]).exists()

    conn = sqlite3.connect(db_path)
    enabled = conn.execute("SELECT enabled FROM adapter_instances WHERE id=?", (ids["instance_id"],)).fetchone()[0]
    conn.close()
    assert enabled == 0


def test_binding_enable_disable_offline_updates_timestamp_and_backup(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)

    result = set_binding_enabled(db_path, ids["binding_id"], False)

    assert result["enabled"] is False
    assert result["updated_at"] != "2026-06-25T12:00:00+00:00"
    assert Path(result["backup"]).exists()
    assert list_bindings(db_path, ids["instance_id"])[0]["enabled"] is False


def test_loglevel_set_persists_in_app_settings(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    result = set_loglevel(db_path, "debug", backup=False)

    assert result["value"] == "DEBUG"
    conn = sqlite3.connect(db_path)
    value = conn.execute("SELECT value FROM app_settings WHERE key='server.log_level'").fetchone()[0]
    conn.close()
    assert value == "DEBUG"


def test_support_package_sanitizes_adapter_secret_fields(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)

    package = create_support_package(db_path)
    adapter = next(entry for entry in package["adapters"] if entry["id"] == ids["instance_id"])

    assert adapter["name"] == "[REDACTED_DOMAIN]"
    assert adapter["config"]["host"] == "[REDACTED_ENDPOINT]"
    assert adapter["config"]["password"] == "[REDACTED]"
    assert adapter["config"]["client_id"] == "client-a"
    assert "/" not in package["installation"]["database"]["path"]


def test_config_validate_reports_invalid_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_instances SET config=? WHERE id=?", ("{broken", ids["instance_id"]))
    conn.commit()
    conn.close()

    result = validate_config(db_path)

    assert result["ok"] is False
    assert result["errors"][0]["table"] == "adapter_instances"


def test_write_fails_clearly_when_database_locked(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    lock = sqlite3.connect(db_path, timeout=0.2)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(AdminCliError, match="gesperrt"):
            set_adapter_enabled(db_path, ids["instance_id"], False, backup=False)
    finally:
        lock.rollback()
        lock.close()


def test_resolve_database_path_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("OBS_DATABASE__PATH", str(missing))

    with pytest.raises(AdminCliError, match="Keine OBS-Konfigurationsdatenbank"):
        resolve_database_path()
