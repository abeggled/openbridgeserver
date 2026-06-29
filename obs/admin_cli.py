"""Offline administration CLI for the open bridge configuration database."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from obs import __version__
from obs.api.v1.support import sanitize_support_data

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DOCKER_DEFAULT_ENV = {
    "OBS_CONFIG": "/data/config.yaml",
    "OBS_DATABASE__PATH": "/data/obs.db",
}


class AdminCliError(RuntimeError):
    """Raised for user-facing CLI errors."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")


def _env_case_insensitive(name: str) -> str | None:
    lookup = name.upper()
    for key, value in os.environ.items():
        if key.upper() == lookup:
            return value
    return None


def _legacy_aware_env(new_name: str, legacy_name: str) -> str | None:
    new_value = _env_case_insensitive(new_name)
    legacy_value = _env_case_insensitive(legacy_name)
    if legacy_value and (new_value is None or new_value == DOCKER_DEFAULT_ENV.get(new_name.upper())):
        return legacy_value
    return new_value or legacy_value


def _config_path() -> Path:
    return Path(_legacy_aware_env("OBS_CONFIG", "OPENTWS_CONFIG") or "config.yaml")


def _database_path_from_yaml(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        raise AdminCliError(f"Konfiguration konnte nicht gelesen werden: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AdminCliError(f"Konfiguration ist kein gueltiges YAML: {path}: {exc}") from exc
    database = data.get("database") if isinstance(data, dict) else None
    if isinstance(database, dict) and isinstance(database.get("path"), str):
        return database["path"]
    return None


def _with_legacy_db_fallback(path: Path) -> Path:
    legacy_path = path.with_name("opentws.db")
    if path.name == "obs.db" and not path.exists() and legacy_path.exists():
        return legacy_path
    return path


def resolve_database_path(db_arg: str | None = None, *, require_exists: bool = True) -> Path:
    """Resolve the OBS SQLite database path without contacting the HTTP server."""
    raw = db_arg
    use_legacy_fallback = False
    if raw is None:
        use_legacy_fallback = True
        raw = _legacy_aware_env("OBS_DATABASE__PATH", "OPENTWS_DATABASE__PATH")
    raw = raw or _database_path_from_yaml(_config_path())
    if raw is None:
        from obs.config import Settings

        raw = Settings().database.path

    path = Path(raw).expanduser()
    if use_legacy_fallback:
        path = _with_legacy_db_fallback(path)
    if require_exists and not path.exists():
        raise AdminCliError(f"Keine OBS-Konfigurationsdatenbank gefunden: {path}. Nutze --db /pfad/zu/obs.db oder setze OBS_DATABASE__PATH.")
    return path


def connect_database(path: Path, *, timeout: float = 0.2) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(path, timeout=timeout)
    except sqlite3.Error as exc:
        raise AdminCliError(f"Datenbank konnte nicht geoeffnet werden: {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error as exc:
        conn.close()
        raise AdminCliError(f"Datenbank konnte nicht initialisiert werden: {exc}") from exc
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _json_loads(raw: Any, *, context: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise AdminCliError(f"Ungueltiges JSON in {context}: {exc}") from exc


def _json_dict(raw: Any, *, context: str) -> dict[str, Any]:
    value = _json_loads(raw, context=context)
    if not isinstance(value, dict):
        raise AdminCliError(f"Ungueltiges JSON in {context}: erwartet wurde ein Objekt")
    return value


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _row(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    result = conn.execute(sql, params).fetchone()
    return dict(result) if result else None


def _basename(path: Path) -> str:
    return path.name or "[REDACTED_PATH]"


def _sqlite_sidecar_path(db_path: Path, suffix: str) -> Path:
    return Path(f"{db_path}{suffix}")


def _default_backup_target(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.stem}-{_timestamp()}.sqlite")


def _backup_target(db_path: Path, output: str | None) -> Path:
    if output is None:
        return _default_backup_target(db_path)
    target = Path(output).expanduser()
    if target.is_dir() or output.endswith(("/", os.sep)):
        target.mkdir(parents=True, exist_ok=True)
        return target / _default_backup_target(db_path).name
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _support_package_target(output: str) -> Path:
    target = Path(output).expanduser()
    if target.is_dir() or output.endswith(("/", os.sep)):
        target.mkdir(parents=True, exist_ok=True)
        return target / f"obs-support-{_timestamp()}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def create_backup(db_path: Path, output: str | None = None) -> Path:
    """Create a consistent SQLite backup using the sqlite backup API."""
    if not db_path.exists():
        raise AdminCliError(f"Datenbank nicht gefunden: {db_path}")

    target = _backup_target(db_path, output)
    if target.resolve() == db_path.resolve():
        raise AdminCliError("Backup-Ziel darf nicht identisch mit der Datenbank sein")

    source: sqlite3.Connection | None = None
    dest: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(db_path, timeout=0.2)
        dest = sqlite3.connect(target)
        with dest:
            source.backup(dest)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise AdminCliError(f"Datenbank ist gesperrt, Backup abgebrochen: {db_path}") from exc
        raise AdminCliError(f"Backup konnte nicht erzeugt werden: {exc}") from exc
    except sqlite3.Error as exc:
        raise AdminCliError(f"Backup konnte nicht erzeugt werden: {exc}") from exc
    finally:
        if source is not None:
            source.close()
        if dest is not None:
            dest.close()
    return target


def database_info(db_path: Path) -> dict[str, Any]:
    conn = connect_database(db_path)
    try:
        try:
            schema = _row(conn, "SELECT MAX(version) AS version FROM schema_version") if _table_exists(conn, "schema_version") else None
            tables = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            return {
                "path": str(db_path),
                "exists": db_path.exists(),
                "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                "schema_version": schema["version"] if schema else None,
                "tables": tables,
                "wal_exists": _sqlite_sidecar_path(db_path, "-wal").exists(),
                "shm_exists": _sqlite_sidecar_path(db_path, "-shm").exists(),
            }
        except sqlite3.Error as exc:
            raise AdminCliError(f"Datenbankinformationen konnten nicht gelesen werden: {exc}") from exc
    finally:
        conn.close()


def status(db_arg: str | None = None) -> dict[str, Any]:
    db_path = resolve_database_path(db_arg, require_exists=False)
    payload: dict[str, Any] = {
        "version": __version__,
        "config": {"path": str(_config_path()), "exists": _config_path().exists()},
        "database": {"path": str(db_path), "exists": db_path.exists()},
    }
    if db_path.exists():
        try:
            info = database_info(db_path)
            payload["database"].update(
                {
                    "size_bytes": info["size_bytes"],
                    "schema_version": info["schema_version"],
                    "wal_exists": info["wal_exists"],
                    "shm_exists": info["shm_exists"],
                }
            )
        except AdminCliError as exc:
            payload["database"]["error"] = str(exc)
    return payload


def _resolve_adapter(conn: sqlite3.Connection, reference: str) -> dict[str, Any]:
    rows = _rows(conn, "SELECT * FROM adapter_instances WHERE id=?", (reference,))
    if not rows:
        rows = _rows(conn, "SELECT * FROM adapter_instances WHERE name=?", (reference,))
    if not rows:
        raise AdminCliError(f"Adapter-Instanz nicht gefunden: {reference}")
    if len(rows) > 1:
        raise AdminCliError(f"Adapter-Name ist nicht eindeutig: {reference}. Bitte die Instanz-ID verwenden.")
    return rows[0]


def list_adapters(db_path: Path) -> list[dict[str, Any]]:
    conn = connect_database(db_path)
    try:
        if not _table_exists(conn, "adapter_instances"):
            return []
        binding_cols = _columns(conn, "adapter_bindings") if _table_exists(conn, "adapter_bindings") else set()
        binding_counts = (
            {
                row["adapter_instance_id"] or "": int(row["count"])
                for row in conn.execute("SELECT adapter_instance_id, COUNT(*) AS count FROM adapter_bindings GROUP BY adapter_instance_id").fetchall()
            }
            if "adapter_instance_id" in binding_cols
            else {}
        )
        result = []
        for row in _rows(conn, "SELECT * FROM adapter_instances ORDER BY adapter_type, name"):
            result.append(
                {
                    "id": row["id"],
                    "adapter_type": row["adapter_type"],
                    "name": row["name"],
                    "enabled": bool(row["enabled"]),
                    "bindings": binding_counts.get(row["id"], 0),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return result
    finally:
        conn.close()


def show_adapter(db_path: Path, reference: str) -> dict[str, Any]:
    conn = connect_database(db_path)
    try:
        row = _resolve_adapter(conn, reference)
        count = 0
        binding_cols = _columns(conn, "adapter_bindings") if _table_exists(conn, "adapter_bindings") else set()
        if "adapter_instance_id" in binding_cols:
            count_row = conn.execute("SELECT COUNT(*) AS count FROM adapter_bindings WHERE adapter_instance_id=?", (row["id"],)).fetchone()
            count = int(count_row["count"]) if count_row else 0
        return {
            "id": row["id"],
            "adapter_type": row["adapter_type"],
            "name": row["name"],
            "config": _config_or_placeholder(row["config"]),
            "enabled": bool(row["enabled"]),
            "bindings": count,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _begin_immediate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise AdminCliError("Datenbank ist fuer Schreibzugriffe gesperrt. OBS stoppen oder Wartungsfenster nutzen.") from exc
        raise


def set_adapter_enabled(db_path: Path, reference: str, enabled: bool, *, backup: bool = True) -> dict[str, Any]:
    backup_path = create_backup(db_path) if backup else None
    conn = connect_database(db_path)
    try:
        _begin_immediate(conn)
        row = _resolve_adapter(conn, reference)
        updated_at = _now()
        conn.execute("UPDATE adapter_instances SET enabled=?, updated_at=? WHERE id=?", (int(enabled), updated_at, row["id"]))
        conn.commit()
        updated = show_adapter(db_path, row["id"])
        updated["backup"] = str(backup_path) if backup_path else None
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _resolve_binding(conn: sqlite3.Connection, binding_id: str, *, validate_json: bool = True) -> dict[str, Any]:
    row = _row(conn, "SELECT * FROM adapter_bindings WHERE id=?", (binding_id,))
    if row is None:
        raise AdminCliError(f"Binding nicht gefunden: {binding_id}")
    if validate_json:
        _json_dict(row.get("config"), context=f"adapter_bindings.config ({binding_id})")
        if row.get("value_map"):
            _json_dict(row["value_map"], context=f"adapter_bindings.value_map ({binding_id})")
    return row


def list_bindings(db_path: Path, adapter: str | None = None) -> list[dict[str, Any]]:
    conn = connect_database(db_path)
    try:
        if not _table_exists(conn, "adapter_bindings"):
            return []
        binding_cols = _columns(conn, "adapter_bindings")
        has_adapter_instance_id = "adapter_instance_id" in binding_cols
        params: tuple[Any, ...] = ()
        where = ""
        if adapter:
            if not has_adapter_instance_id:
                raise AdminCliError(
                    "Tabelle adapter_bindings enthaelt keine Spalte adapter_instance_id. "
                    "Bitte Datenbankmigrationen ausfuehren oder bindings list ohne --adapter nutzen."
                )
            adapter_row = _resolve_adapter(conn, adapter)
            where = "WHERE ab.adapter_instance_id=?"
            params = (adapter_row["id"],)
        dp_name_expr = "dp.name" if _table_exists(conn, "datapoints") else "NULL"
        adapter_instance_expr = "ab.adapter_instance_id" if has_adapter_instance_id else "NULL"
        join = "LEFT JOIN datapoints dp ON dp.id = ab.datapoint_id" if _table_exists(conn, "datapoints") else ""
        sql = f"""
            SELECT ab.id, ab.datapoint_id, {dp_name_expr} AS datapoint_name,
                   ab.adapter_type, {adapter_instance_expr} AS adapter_instance_id, ab.direction,
                   ab.config, ab.enabled, ab.created_at, ab.updated_at
            FROM adapter_bindings ab
            {join}
            {where}
            ORDER BY ab.adapter_type, ab.created_at
        """
        return [
            {
                "id": row["id"],
                "datapoint_id": row["datapoint_id"],
                "datapoint_name": row["datapoint_name"],
                "adapter_type": row["adapter_type"],
                "adapter_instance_id": row["adapter_instance_id"],
                "direction": row["direction"],
                "config": _config_or_placeholder(row["config"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in _rows(conn, sql, params)
        ]
    finally:
        conn.close()


def set_binding_enabled(db_path: Path, binding_id: str, enabled: bool, *, backup: bool = True) -> dict[str, Any]:
    backup_path = create_backup(db_path) if backup else None
    conn = connect_database(db_path)
    try:
        _begin_immediate(conn)
        row = _resolve_binding(conn, binding_id, validate_json=False)
        updated_at = _now()
        conn.execute("UPDATE adapter_bindings SET enabled=?, updated_at=? WHERE id=?", (int(enabled), updated_at, row["id"]))
        conn.commit()
        updated = _resolve_binding(conn, binding_id, validate_json=False)
        updated["enabled"] = bool(updated["enabled"])
        updated["config"] = _config_or_placeholder(updated["config"])
        updated["backup"] = str(backup_path) if backup_path else None
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_loglevel(db_path: Path) -> dict[str, Any]:
    conn = connect_database(db_path)
    try:
        row = _row(conn, "SELECT value FROM app_settings WHERE key='server.log_level'") if _table_exists(conn, "app_settings") else None
        return {"key": "server.log_level", "value": row["value"] if row else None}
    finally:
        conn.close()


def set_loglevel(db_path: Path, level: str, *, backup: bool = True) -> dict[str, Any]:
    normalized = level.upper()
    if normalized not in VALID_LOG_LEVELS:
        raise AdminCliError(f"Ungueltiges Loglevel: {level}. Erlaubt: {', '.join(sorted(VALID_LOG_LEVELS))}")
    backup_path = create_backup(db_path) if backup else None
    conn = connect_database(db_path)
    try:
        if not _table_exists(conn, "app_settings"):
            raise AdminCliError("Tabelle app_settings fehlt. Bitte Datenbankmigrationen ausfuehren.")
        _begin_immediate(conn)
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('server.log_level', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (normalized,),
        )
        conn.commit()
        return {"key": "server.log_level", "value": normalized, "backup": str(backup_path) if backup_path else None}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_config(db_path: Path) -> dict[str, Any]:
    conn = connect_database(db_path)
    errors: list[dict[str, str]] = []
    try:
        if _table_exists(conn, "adapter_instances"):
            for row in _rows(conn, "SELECT id, config FROM adapter_instances ORDER BY id"):
                try:
                    _json_dict(row["config"], context=f"adapter_instances.config ({row['id']})")
                except AdminCliError as exc:
                    errors.append({"table": "adapter_instances", "id": row["id"], "field": "config", "error": str(exc)})
        if _table_exists(conn, "adapter_bindings"):
            cols = _columns(conn, "adapter_bindings")
            fields = ["config"] + (["value_map"] if "value_map" in cols else [])
            select_fields = ", ".join(["id", *fields])
            for row in _rows(conn, f"SELECT {select_fields} FROM adapter_bindings ORDER BY id"):
                for field in fields:
                    if field == "value_map" and not row.get(field):
                        continue
                    try:
                        _json_dict(row[field], context=f"adapter_bindings.{field} ({row['id']})")
                    except AdminCliError as exc:
                        errors.append({"table": "adapter_bindings", "id": row["id"], "field": field, "error": str(exc)})
        return {"ok": not errors, "errors": errors}
    finally:
        conn.close()


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int | None:
    if not _table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", params).fetchone()
    return int(row["count"]) if row else 0


def create_support_package(db_path: Path) -> dict[str, Any]:
    conn = connect_database(db_path)
    try:
        binding_cols = _columns(conn, "adapter_bindings") if _table_exists(conn, "adapter_bindings") else set()
        has_adapter_instance_id = "adapter_instance_id" in binding_cols
        binding_counts = (
            {
                row["adapter_instance_id"] or "": int(row["count"])
                for row in conn.execute("SELECT adapter_instance_id, COUNT(*) AS count FROM adapter_bindings GROUP BY adapter_instance_id").fetchall()
            }
            if has_adapter_instance_id
            else {}
        )
        object_counts = (
            {
                row["adapter_instance_id"] or "": int(row["count"])
                for row in conn.execute(
                    "SELECT adapter_instance_id, COUNT(DISTINCT datapoint_id) AS count FROM adapter_bindings GROUP BY adapter_instance_id"
                ).fetchall()
            }
            if has_adapter_instance_id
            else {}
        )

        adapters = []
        if _table_exists(conn, "adapter_instances"):
            for row in _rows(conn, "SELECT * FROM adapter_instances ORDER BY adapter_type, name"):
                adapters.append(
                    sanitize_support_data(
                        {
                            "id": row["id"],
                            "adapter_type": row["adapter_type"],
                            "name": row["name"],
                            "enabled": bool(row["enabled"]),
                            "registered": None,
                            "running": False,
                            "connected": False,
                            "config": _config_or_placeholder(row["config"]),
                            "objects": object_counts.get(row["id"], 0),
                            "bindings": binding_counts.get(row["id"], 0),
                            "updated_at": row["updated_at"],
                        }
                    )
                )

        app_settings = {}
        if _table_exists(conn, "app_settings"):
            app_settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()}

        history_stats: dict[str, Any] = {"available": False, "reason": "table_missing"}
        if _table_exists(conn, "history_values"):
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, COUNT(DISTINCT datapoint_id) AS datapoints, MIN(ts) AS oldest_ts, MAX(ts) AS newest_ts FROM history_values"
                ).fetchone()
                history_stats = {
                    "available": True,
                    "total_values": int(row["total"]) if row else 0,
                    "datapoints": int(row["datapoints"]) if row else 0,
                    "oldest_ts": row["oldest_ts"] if row else None,
                    "newest_ts": row["newest_ts"] if row else None,
                }
            except sqlite3.Error as exc:
                history_stats = {"available": False, "reason": exc.__class__.__name__}

        return sanitize_support_data(
            {
                "schema_version": 1,
                "generated_at": _now(),
                "generated_by": "[REDACTED]",
                "mode": "offline-cli",
                "privacy": {
                    "automatic_upload": False,
                    "remote_access": False,
                    "sanitizer": "central_recursive_v1",
                    "path_policy": "basename_only",
                },
                "installation": {
                    "obs_version": __version__,
                    "database": {"path": _basename(db_path), "size_bytes": db_path.stat().st_size if db_path.exists() else 0},
                },
                "database": {
                    "schema_version": _row(conn, "SELECT MAX(version) AS version FROM schema_version")["version"]
                    if _table_exists(conn, "schema_version")
                    else None,
                    "counts": {
                        "adapter_instances": _count(conn, "adapter_instances"),
                        "adapter_bindings": _count(conn, "adapter_bindings"),
                        "datapoints": _count(conn, "datapoints"),
                        "logic_graphs": _count(conn, "logic_graphs"),
                        "visu_nodes": _count(conn, "visu_nodes"),
                    },
                },
                "settings": app_settings,
                "adapters": adapters,
                "history": {"sqlite_storage": history_stats},
            }
        )
    finally:
        conn.close()


def _config_or_placeholder(raw_config: Any) -> dict[str, Any]:
    try:
        config = json.loads(raw_config or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"available": False, "reason": "invalid_json"}
    return config if isinstance(config, dict) else {"available": False, "reason": "invalid_config_type"}


def _print(payload: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if isinstance(payload, list):
        for item in payload:
            print(" ".join(f"{key}={value}" for key, value in item.items() if key != "config"))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, sort_keys=True)}")
            else:
                print(f"{key}: {value}")
        return
    print(payload)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="Pfad zur OBS-SQLite-Konfigurationsdatenbank")
    parser.add_argument("--json", action="store_true", help="Maschinenlesbare JSON-Ausgabe")
    parser.add_argument("--no-backup", action="store_true", help="Schreibende Operation ohne automatisches Backup ausfuehren")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obs-admin", description="Offline-Administration fuer open bridge server")
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Status und aufgeloeste Pfade anzeigen")

    db_parser = sub.add_parser("db", help="Datenbankbefehle")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("info", help="Datenbankinformationen anzeigen")
    backup_parser = db_sub.add_parser("backup", help="Datenbank sichern")
    backup_parser.add_argument("--output", help="Zielpfad oder Zielverzeichnis")

    adapters_parser = sub.add_parser("adapters", help="Adapter-Instanzen verwalten")
    adapters_sub = adapters_parser.add_subparsers(dest="adapters_command", required=True)
    adapters_sub.add_parser("list", help="Adapter-Instanzen auflisten")
    show_parser = adapters_sub.add_parser("show", help="Adapter-Instanz anzeigen")
    show_parser.add_argument("reference")
    disable_parser = adapters_sub.add_parser("disable", help="Adapter-Instanz deaktivieren")
    disable_parser.add_argument("reference")
    enable_parser = adapters_sub.add_parser("enable", help="Adapter-Instanz aktivieren")
    enable_parser.add_argument("reference")

    bindings_parser = sub.add_parser("bindings", help="Bindings verwalten")
    bindings_sub = bindings_parser.add_subparsers(dest="bindings_command", required=True)
    list_parser = bindings_sub.add_parser("list", help="Bindings auflisten")
    list_parser.add_argument("--adapter", help="Adapter-ID oder eindeutiger Adaptername")
    bind_disable = bindings_sub.add_parser("disable", help="Binding deaktivieren")
    bind_disable.add_argument("binding_id")
    bind_enable = bindings_sub.add_parser("enable", help="Binding aktivieren")
    bind_enable.add_argument("binding_id")

    log_parser = sub.add_parser("loglevel", help="Persistentes Start-Loglevel verwalten")
    log_sub = log_parser.add_subparsers(dest="loglevel_command", required=True)
    log_sub.add_parser("get", help="Persistentes Loglevel anzeigen")
    log_set = log_sub.add_parser("set", help="Persistentes Loglevel setzen")
    log_set.add_argument("level")

    support_parser = sub.add_parser("support-package", help="Offline-Supportpaket erzeugen")
    support_sub = support_parser.add_subparsers(dest="support_command", required=True)
    support_create = support_sub.add_parser("create", help="Supportpaket erzeugen")
    support_create.add_argument("--output", required=True, help="JSON-Zieldatei")

    config_parser = sub.add_parser("config", help="Konfiguration pruefen")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate", help="JSON-Konfigurationsfelder validieren")
    return parser


def _normalize_global_options(argv: list[str]) -> list[str]:
    """Allow global options before or after subcommands."""
    normalized: list[str] = []
    prepend: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--json", "--no-backup"}:
            prepend.append(token)
            index += 1
            continue
        if token == "--db":
            if index + 1 >= len(argv):
                normalized.append(token)
                index += 1
                continue
            prepend.extend([token, argv[index + 1]])
            index += 2
            continue
        if token.startswith("--db="):
            prepend.append(token)
            index += 1
            continue
        normalized.append(token)
        index += 1
    return [*prepend, *normalized]


def run(args: argparse.Namespace) -> tuple[Any, int]:
    db_path = None if args.command == "status" else resolve_database_path(args.db)
    backup = not getattr(args, "no_backup", False)

    if args.command == "status":
        return status(args.db), 0
    if args.command == "db" and args.db_command == "info":
        return database_info(db_path), 0
    if args.command == "db" and args.db_command == "backup":
        return {"backup": str(create_backup(db_path, args.output))}, 0
    if args.command == "adapters" and args.adapters_command == "list":
        return list_adapters(db_path), 0
    if args.command == "adapters" and args.adapters_command == "show":
        return show_adapter(db_path, args.reference), 0
    if args.command == "adapters" and args.adapters_command == "disable":
        return set_adapter_enabled(db_path, args.reference, False, backup=backup), 0
    if args.command == "adapters" and args.adapters_command == "enable":
        return set_adapter_enabled(db_path, args.reference, True, backup=backup), 0
    if args.command == "bindings" and args.bindings_command == "list":
        return list_bindings(db_path, args.adapter), 0
    if args.command == "bindings" and args.bindings_command == "disable":
        return set_binding_enabled(db_path, args.binding_id, False, backup=backup), 0
    if args.command == "bindings" and args.bindings_command == "enable":
        return set_binding_enabled(db_path, args.binding_id, True, backup=backup), 0
    if args.command == "loglevel" and args.loglevel_command == "get":
        return get_loglevel(db_path), 0
    if args.command == "loglevel" and args.loglevel_command == "set":
        return set_loglevel(db_path, args.level, backup=backup), 0
    if args.command == "support-package" and args.support_command == "create":
        package = create_support_package(db_path)
        output = _support_package_target(args.output)
        output.write_text(json.dumps(package, indent=2, sort_keys=True), encoding="utf-8")
        return {"output": str(output)}, 0
    if args.command == "config" and args.config_command == "validate":
        result = validate_config(db_path)
        return result, 0 if result["ok"] else 1
    raise AdminCliError("Unbekannter Befehl")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_global_options(raw_argv))
    try:
        payload, code = run(args)
    except AdminCliError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Fehler: Datenbank konnte nicht gelesen werden: {exc}", file=sys.stderr)
        return 2
    _print(payload, json_output=args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
