"""
OpenTWS Server Configuration

Priority (highest → lowest):
  1. Environment variables  OPENTWS_<SECTION>__<KEY>=value
  2. config.yaml            (path via OPENTWS_CONFIG env var, default: ./config.yaml)
  3. Built-in defaults

Example env overrides:
  OPENTWS_MQTT__HOST=192.168.1.10
  OPENTWS_DATABASE__PATH=/mnt/data/opentws.db
  OPENTWS_SECURITY__JWT_SECRET=supersecret
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


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
    username: Optional[str] = None
    password: Optional[str] = None


class DatabaseSettings(BaseModel):
    path: str = "/data/opentws.db"
    history_plugin: str = "sqlite"  # sqlite | influxdb | timescaledb | questdb


class RingBufferSettings(BaseModel):
    storage: str = "memory"  # memory | disk
    max_entries: int = 10000


class SecuritySettings(BaseModel):
    jwt_secret: str = "changeme"
    jwt_expire_minutes: int = 1440


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

_CONFIG_PATH = Path(os.environ.get("OPENTWS_CONFIG", "config.yaml"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENTWS_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    ringbuffer: RingBufferSettings = Field(default_factory=RingBufferSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **kwargs: Any,                  # absorbs secrets_settings / file_secret_settings
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSource(settings_cls, _CONFIG_PATH),
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
