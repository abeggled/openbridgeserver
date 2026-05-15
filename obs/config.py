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
from pydantic import BaseModel, Field, field_validator
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


class RingBufferSettings(BaseModel):
    storage: str = "disk"  # memory | disk
    max_entries: int = 10000


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
# Main settings class
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """Resolve the YAML config path at construction time (honours OBS_CONFIG)."""
    return Path(os.environ.get("OBS_CONFIG", "config.yaml"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBS_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    ringbuffer: RingBufferSettings = Field(default_factory=RingBufferSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    mosquitto: MosquittoSettings = Field(default_factory=MosquittoSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)

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
