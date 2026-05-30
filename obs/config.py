"""open bridge server Configuration

Priority (highest → lowest):
  1. Environment variables  OBS_<SECTION>__<KEY>=value
  2. config.yaml            (path via OBS_CONFIG env var, default: ./config.yaml)
  3. Built-in defaults

Example env overrides:
  OBS_MQTT__HOST=192.168.1.10
  OBS_DATABASE__PATH=/mnt/data/obs.db
  OBS_SECURITY__JWT_SECRET=supersecret
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# ---------------------------------------------------------------------------
# Sub-sections
# ---------------------------------------------------------------------------


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"


class MqttSettings(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None


class DatabaseSettings(BaseModel):
    path: str = "/data/obs.db"
    history_plugin: str = "sqlite"  # sqlite | influxdb | timescaledb | questdb


class SecuritySettings(BaseModel):
    jwt_secret: str = "changeme"
    jwt_expire_minutes: int = 1440

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret_strength(cls, v: str) -> str:
        import logging

        if len(v) < 32:
            logging.getLogger(__name__).warning(
                "⚠️  JWT secret is too short (%d chars) — use at least 32 random characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"',
                len(v),
            )
        return v


class CorsSettings(BaseModel):
    origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    allow_credentials: bool = True


class MosquittoSettings(BaseModel):
    """Settings for managing the internal Mosquitto passwd file."""

    passwd_file: str = "/mosquitto/passwd/passwd"
    # PID to send SIGHUP to after passwd file changes.
    # In Docker Compose with pid: "container:mosquitto", Mosquitto runs as PID 1.
    reload_pid: int | None = None
    # Shell command to trigger Mosquitto reload (takes precedence over reload_pid).
    # Example bare-metal: "kill -HUP $(cat /var/run/mosquitto/mosquitto.pid)"
    reload_command: str | None = None
    # Credentials open bridge server uses to connect to Mosquitto (must match OBS_MQTT__*).
    service_username: str = "obs"
    service_password: str = "changeme"


# ---------------------------------------------------------------------------
# YAML source
# ---------------------------------------------------------------------------


class YamlConfigSource(PydanticBaseSettingsSource):
    """Load settings from a YAML file. Missing file is silently ignored."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as fh:
                self._data = yaml.safe_load(fh) or {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def field_is_complex(self, field: Any) -> bool:
        return True

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


# ---------------------------------------------------------------------------
# Backward-compatibility helpers
# ---------------------------------------------------------------------------


def _has_env_key_case_insensitive(env_key: str) -> bool:
    lookup = env_key.upper()
    return any(existing.upper() == lookup for existing in os.environ)


def _get_existing_env_key_case_insensitive(env_key: str) -> str | None:
    lookup = env_key.upper()
    return next((existing for existing in os.environ if existing.upper() == lookup), None)


def _get_env_case_insensitive(*env_keys: str) -> str | None:
    for key in env_keys:
        if key in os.environ:
            return os.environ[key]
        upper_key = key.upper()
        for existing, value in os.environ.items():
            if existing.upper() == upper_key:
                return value
    return None


def _import_legacy_env_vars() -> None:
    """Import OPENTWS_* variables as OBS_* when OBS_* is not set.

    Docker images may predefine OBS_* defaults. If those exact defaults are
    present, prefer an explicitly supplied OPENTWS_* value for compatibility.
    """
    legacy_prefix = "OPENTWS_"
    legacy_prefix_upper = legacy_prefix.upper()
    new_prefix = "OBS_"
    docker_defaults = {
        "OBS_CONFIG": "/data/config.yaml",
        "OBS_DATABASE__PATH": "/data/obs.db",
    }

    for key, value in list(os.environ.items()):
        if not key.upper().startswith(legacy_prefix_upper):
            continue
        mapped_key = f"{new_prefix}{key[len(legacy_prefix) :]}"
        existing_key = _get_existing_env_key_case_insensitive(mapped_key)
        if existing_key is None:
            os.environ[mapped_key] = value
            continue

        expected_default = docker_defaults.get(mapped_key.upper())
        if expected_default is not None and os.environ.get(existing_key) == expected_default:
            os.environ[existing_key] = value


def _resolve_default_db_path(default_path: str = "/data/obs.db") -> str:
    """Prefer legacy DB path if new default file does not exist yet."""
    new_path = Path(default_path)
    legacy_path = new_path.with_name("opentws.db")
    if not new_path.exists() and legacy_path.exists():
        return str(legacy_path)
    return default_path


def _is_builtin_default_db_path(path: str) -> bool:
    return Path(path).as_posix() == "/data/obs.db"


_import_legacy_env_vars()


# ---------------------------------------------------------------------------
# Main settings class
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """Resolve the YAML config path at construction time."""
    return Path(_get_env_case_insensitive("OBS_CONFIG", "OPENTWS_CONFIG") or "config.yaml")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBS_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    database: DatabaseSettings = Field(default_factory=lambda: DatabaseSettings(path=_resolve_default_db_path()))
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    mosquitto: MosquittoSettings = Field(default_factory=MosquittoSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)

    @model_validator(mode="before")
    @classmethod
    def _inject_database_path_fallback(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        result = dict(data)
        database_key = next(
            (key for key in result if isinstance(key, str) and key.lower() == "database"),
            None,
        )
        default_path = _resolve_default_db_path()

        if database_key is None:
            result["database"] = {"path": default_path}
            return result

        database_value = result.get(database_key)
        if database_value is None:
            result[database_key] = {"path": default_path}
            return result

        if isinstance(database_value, dict):
            path_key = next(
                (key for key in database_value if isinstance(key, str) and key.lower() == "path"),
                None,
            )
            if path_key is None:
                merged_database = dict(database_value)
                merged_database["path"] = default_path
                result[database_key] = merged_database
            else:
                current_path = database_value.get(path_key)
                if isinstance(current_path, str) and _is_builtin_default_db_path(current_path):
                    merged_database = dict(database_value)
                    merged_database[path_key] = default_path
                    result[database_key] = merged_database

        return result

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **kwargs: Any,  # absorbs secrets_settings / file_secret_settings
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSource(settings_cls, _config_path()),
        )


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application-wide Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(s: Settings) -> None:
    """Replace the singleton (useful in tests)."""
    global _settings
    _settings = s
