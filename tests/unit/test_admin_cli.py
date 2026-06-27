from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from obs.admin_cli import (
    AdminCliError,
    main as admin_main,
    build_parser,
    create_backup,
    create_support_package,
    database_info,
    get_loglevel,
    list_adapters,
    list_bindings,
    resolve_database_path,
    set_adapter_enabled,
    set_binding_enabled,
    set_loglevel,
    show_adapter,
    status,
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


def _make_legacy_binding_db(path: Path) -> dict[str, str]:
    instance_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    dp_id = str(uuid.uuid4())
    now = "2026-06-25T12:00:00+00:00"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
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
            mqtt_topic TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE adapter_bindings (
            id TEXT PRIMARY KEY,
            datapoint_id TEXT NOT NULL,
            adapter_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO adapter_instances VALUES (?,?,?,?,?,?,?)",
        (instance_id, "MQTT", "legacy mqtt", "{}", 1, now, now),
    )
    conn.execute(
        "INSERT INTO datapoints VALUES (?,?,?,?,?,?)",
        (dp_id, "DP legacy", "STRING", "legacy/topic", now, now),
    )
    conn.execute(
        "INSERT INTO adapter_bindings VALUES (?,?,?,?,?,?,?,?)",
        (binding_id, dp_id, "MQTT", "SOURCE", "{}", 1, now, now),
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


def test_global_option_normalizer_handles_db_edge_cases():
    assert _normalize_global_options(["db", "info", "--db"]) == ["db", "info", "--db"]
    assert _normalize_global_options(["db", "info", "--db=/tmp/obs.db"]) == ["--db=/tmp/obs.db", "db", "info"]


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


def test_resolve_database_path_prefers_legacy_when_obs_env_is_docker_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy_path = tmp_path / "custom-opentws.db"
    legacy_path.write_bytes(b"legacy")
    monkeypatch.setenv("OBS_DATABASE__PATH", "/data/obs.db")
    monkeypatch.setenv("OPENTWS_DATABASE__PATH", str(legacy_path))

    assert resolve_database_path() == legacy_path


def test_resolve_database_path_prefers_explicit_obs_over_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    obs_path = tmp_path / "custom-obs.db"
    legacy_path = tmp_path / "custom-opentws.db"
    obs_path.write_bytes(b"obs")
    legacy_path.write_bytes(b"legacy")
    monkeypatch.setenv("OBS_DATABASE__PATH", str(obs_path))
    monkeypatch.setenv("OPENTWS_DATABASE__PATH", str(legacy_path))

    assert resolve_database_path() == obs_path


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


def test_status_includes_database_details_for_existing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {db_path}\n", encoding="utf-8")
    monkeypatch.setenv("OBS_CONFIG", str(config_path))

    result = status()

    assert result["config"]["exists"] is True
    assert result["database"]["path"] == str(db_path)
    assert result["database"]["schema_version"] == 37


def test_status_reports_error_for_corrupt_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "obs.db"
    db_path.write_bytes(b"not a sqlite database")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database:\n  path: {db_path}\n", encoding="utf-8")
    monkeypatch.setenv("OBS_CONFIG", str(config_path))

    result = status()

    assert result["database"]["path"] == str(db_path)
    assert "Datenbankinformationen konnten nicht gelesen werden" in result["database"]["error"]


def test_automatic_backup_names_are_unique_within_same_second(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    first = create_backup(db_path)
    second = create_backup(db_path)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_create_backup_accepts_output_directory(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backup = create_backup(db_path, str(backup_dir))

    assert backup.parent == backup_dir
    assert backup.exists()


def test_create_backup_treats_new_trailing_slash_output_as_directory(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    backup_dir = tmp_path / "new-backups"

    backup = create_backup(db_path, f"{backup_dir}/")

    assert backup.parent == backup_dir
    assert backup.exists()
    assert backup.name.startswith("obs-")


def test_create_backup_rejects_database_as_output(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    with pytest.raises(AdminCliError, match="nicht identisch"):
        create_backup(db_path, str(db_path))


def test_create_backup_rejects_missing_database(tmp_path: Path):
    with pytest.raises(AdminCliError, match="Datenbank nicht gefunden"):
        create_backup(tmp_path / "missing.db")


def test_list_helpers_return_empty_when_tables_are_missing(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    assert list_adapters(db_path) == []
    assert list_bindings(db_path) == []


def test_list_and_show_adapters_include_binding_counts(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)

    adapters = list_adapters(db_path)
    shown = show_adapter(db_path, ids["instance_id"])

    assert adapters == [
        {
            "id": ids["instance_id"],
            "adapter_type": "MQTT",
            "name": "mqtt.internal.local",
            "enabled": True,
            "bindings": 1,
            "created_at": "2026-06-25T12:00:00+00:00",
            "updated_at": "2026-06-25T12:00:00+00:00",
        }
    ]
    assert shown["config"]["password"] == "secret"
    assert shown["bindings"] == 1


def test_list_and_show_adapters_tolerate_legacy_binding_schema(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    ids = _make_legacy_binding_db(db_path)

    adapters = list_adapters(db_path)
    shown = show_adapter(db_path, ids["instance_id"])

    assert adapters[0]["id"] == ids["instance_id"]
    assert adapters[0]["bindings"] == 0
    assert shown["bindings"] == 0


def test_show_adapter_rejects_ambiguous_names(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO adapter_instances VALUES (?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            "MQTT",
            "mqtt.internal.local",
            "{}",
            1,
            "2026-06-25T12:00:00+00:00",
            "2026-06-25T12:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    with pytest.raises(AdminCliError, match="nicht eindeutig"):
        show_adapter(db_path, "mqtt.internal.local")


def test_show_adapter_reports_missing_adapter(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    with pytest.raises(AdminCliError, match="Adapter-Instanz nicht gefunden"):
        show_adapter(db_path, str(uuid.uuid4()))


def test_adapter_disable_tolerates_broken_config_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_instances SET config=? WHERE id=?", ("{broken", ids["instance_id"]))
    conn.commit()
    conn.close()

    result = set_adapter_enabled(db_path, ids["instance_id"], False, backup=False)

    assert result["enabled"] is False
    assert result["config"] == {"available": False, "reason": "invalid_json"}


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


def test_binding_disable_tolerates_broken_config_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE adapter_bindings SET config=?, value_map=? WHERE id=?",
        ("{broken", "{also-broken", ids["binding_id"]),
    )
    conn.commit()
    conn.close()

    result = set_binding_enabled(db_path, ids["binding_id"], False, backup=False)

    assert result["enabled"] is False
    assert result["config"] == {"available": False, "reason": "invalid_json"}

    conn = sqlite3.connect(db_path)
    enabled = conn.execute("SELECT enabled FROM adapter_bindings WHERE id=?", (ids["binding_id"],)).fetchone()[0]
    conn.close()
    assert enabled == 0


def test_bindings_list_tolerates_broken_config_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_bindings SET config=? WHERE id=?", ("{broken", ids["binding_id"]))
    conn.commit()
    conn.close()

    bindings = list_bindings(db_path)

    assert bindings[0]["id"] == ids["binding_id"]
    assert bindings[0]["config"] == {"available": False, "reason": "invalid_json"}


def test_bindings_list_handles_legacy_rows_without_adapter_instance_id(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    ids = _make_legacy_binding_db(db_path)

    bindings = list_bindings(db_path)

    assert bindings[0]["id"] == ids["binding_id"]
    assert bindings[0]["adapter_instance_id"] is None
    assert bindings[0]["datapoint_name"] == "DP legacy"


def test_bindings_list_with_adapter_filter_reports_legacy_schema(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    ids = _make_legacy_binding_db(db_path)

    with pytest.raises(AdminCliError, match="adapter_instance_id"):
        list_bindings(db_path, ids["instance_id"])


def test_missing_binding_raises_user_facing_error(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    with pytest.raises(AdminCliError, match="Binding nicht gefunden"):
        set_binding_enabled(db_path, str(uuid.uuid4()), False, backup=False)


def test_loglevel_set_persists_in_app_settings(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    result = set_loglevel(db_path, "debug", backup=False)

    assert result["value"] == "DEBUG"
    conn = sqlite3.connect(db_path)
    value = conn.execute("SELECT value FROM app_settings WHERE key='server.log_level'").fetchone()[0]
    conn.close()
    assert value == "DEBUG"


def test_get_loglevel_returns_persisted_value(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    set_loglevel(db_path, "ERROR", backup=False)

    assert get_loglevel(db_path) == {"key": "server.log_level", "value": "ERROR"}


def test_loglevel_set_rejects_unknown_level(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)

    with pytest.raises(AdminCliError, match="Ungueltiges Loglevel"):
        set_loglevel(db_path, "TRACE", backup=False)


def test_loglevel_set_requires_app_settings_table(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    with pytest.raises(AdminCliError, match="app_settings"):
        set_loglevel(db_path, "INFO", backup=False)


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


def test_support_package_marks_invalid_adapter_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_instances SET config=? WHERE id=?", ("{broken", ids["instance_id"]))
    conn.commit()
    conn.close()

    package = create_support_package(db_path)

    assert package["adapters"][0]["config"] == {"available": False, "reason": "invalid_json"}


def test_support_package_handles_legacy_bindings_without_adapter_instance_id(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    ids = _make_legacy_binding_db(db_path)

    package = create_support_package(db_path)

    assert package["database"]["counts"]["adapter_bindings"] == 1
    adapter = next(entry for entry in package["adapters"] if entry["id"] == ids["instance_id"])
    assert adapter["bindings"] == 0
    assert adapter["objects"] == 0


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


def test_config_validate_reports_non_object_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_instances SET config=? WHERE id=?", ("[]", ids["instance_id"]))
    conn.commit()
    conn.close()

    result = validate_config(db_path)

    assert result["ok"] is False
    assert "erwartet wurde ein Objekt" in result["errors"][0]["error"]


def test_config_validate_reports_invalid_value_map_json(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE adapter_bindings SET value_map=? WHERE id=?", ("{broken", ids["binding_id"]))
    conn.commit()
    conn.close()

    result = validate_config(db_path)

    assert result["ok"] is False
    assert result["errors"][0]["field"] == "value_map"


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


def test_main_dispatches_json_list_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)

    code = admin_main(["adapters", "list", "--db", str(db_path), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == ids["instance_id"]


def test_main_dispatches_support_package_create(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    output = tmp_path / "support.json"

    code = admin_main(["--db", str(db_path), "support-package", "create", "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert "output:" in capsys.readouterr().out


def test_main_dispatches_offline_admin_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    ids = _make_db(db_path)
    backup_dir = tmp_path / "backups"

    commands = [
        ["--db", str(db_path), "status"],
        ["--db", str(db_path), "db", "info"],
        ["--db", str(db_path), "db", "backup", "--output", f"{backup_dir}/"],
        ["--db", str(db_path), "adapters", "show", ids["instance_id"]],
        ["--db", str(db_path), "adapters", "disable", ids["instance_id"], "--no-backup"],
        ["--db", str(db_path), "adapters", "enable", ids["instance_id"], "--no-backup"],
        ["--db", str(db_path), "bindings", "list", "--adapter", ids["instance_id"]],
        ["--db", str(db_path), "bindings", "disable", ids["binding_id"], "--no-backup"],
        ["--db", str(db_path), "bindings", "enable", ids["binding_id"], "--no-backup"],
        ["--db", str(db_path), "loglevel", "get"],
        ["--db", str(db_path), "loglevel", "set", "WARNING", "--no-backup"],
        ["--db", str(db_path), "config", "validate"],
    ]

    for command in commands:
        assert admin_main(command) == 0
        capsys.readouterr()

    assert any(backup_dir.glob("obs-*.sqlite"))


def test_main_support_package_create_accepts_output_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    output_dir = tmp_path / "support"
    output_dir.mkdir()

    code = admin_main(["--db", str(db_path), "support-package", "create", "--output", str(output_dir)])

    files = list(output_dir.glob("obs-support-*.json"))
    assert code == 0
    assert len(files) == 1
    assert f"output: {files[0]}" in capsys.readouterr().out


def test_main_support_package_create_accepts_new_output_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    _make_db(db_path)
    output_dir = tmp_path / "new-support"

    code = admin_main(["--db", str(db_path), "support-package", "create", "--output", f"{output_dir}/"])

    files = list(output_dir.glob("obs-support-*.json"))
    assert code == 0
    assert len(files) == 1
    assert f"output: {files[0]}" in capsys.readouterr().out


def test_main_returns_error_code_for_user_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    missing = tmp_path / "missing.db"

    code = admin_main(["--db", str(missing), "db", "info"])

    captured = capsys.readouterr()
    assert code == 2
    assert "Keine OBS-Konfigurationsdatenbank" in captured.err


def test_main_returns_error_code_for_unreadable_sqlite_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    db_path = tmp_path / "obs.db"
    db_path.write_bytes(b"not a sqlite database")

    code = admin_main(["--db", str(db_path), "adapters", "list"])

    captured = capsys.readouterr()
    assert code == 2
    assert "Fehler: Datenbank konnte nicht gelesen werden" in captured.err
    assert "Traceback" not in captured.err
