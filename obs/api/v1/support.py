"""Support diagnostics API.

Phase 1 deliberately only builds an explicit, sanitized export package.
It does not send data anywhere and does not create remote access sessions.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from obs import __version__
from obs.adapters import registry as adapter_registry
from obs.api.auth import get_admin_user
from obs.config import get_settings
from obs.db.database import Database, get_db
from obs.log_buffer import get_log_buffer, set_log_buffer_level

router = APIRouter(tags=["support"])

_PROCESS_STARTED_AT = datetime.now(UTC)
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "username",
    "apikey",
    "api_key",
    "auth",
    "bearer",
    "ca_cert",
    "client_cert",
    "cert",
    "keyfile",
    "keyring",
    "passphrase",
    "pin",
    "pre_shared_key",
    "private_key",
    "psk",
    "jwt",
)
_SENSITIVE_KEYS = {
    "backbone_key",
    "community",
    "chat_id",
    "knxkeys_file_path",
    "to",
    "priv_key",
    "user_key",
}
_PASSTHROUGH_KEYS = {
    "auth_protocol",
    "individual_address",
    "logger",
    "sniffer.process",
}
_PASSTHROUGH_VALUES = {
    "sniffer.process",
}
_ENDPOINT_KEY_PARTS = (
    "host",
    "hostname",
    "ip",
    "address",
    "url",
    "uri",
    "dsn",
    "endpoint",
    "server",
)
_BASENAME_ONLY_KEYS = {
    "config_source",
    "path",
}
_BASENAME_FILE_EXTENSIONS = {
    "conf",
    "db",
    "json",
    "key",
    "sqlite",
    "sqlite3",
    "toml",
    "yaml",
    "yml",
}
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_CANDIDATE_RE = re.compile(r"(?<![0-9a-fA-F:])(?:[0-9a-fA-F]{0,4}:){1,7}[0-9a-fA-F]{0,4}(?![0-9a-fA-F:])")
_SECRET_KEY_PATTERN = (
    r"[a-z0-9_-]*(?:"
    r"token|secret|password|passwd|api[_.-]?key|private[_.-]?key|"
    r"psk|pin|pre[_.-]?shared[_.-]?key|auth|bearer|passphrase|"
    r"keyring|ca[_.-]?cert|client[_.-]?cert|cert|community|knxkeys[_.-]?file[_.-]?path"
    r")"
)
_QUOTED_SECRET_RE = re.compile(rf"(?i)(?<![a-z0-9_-])({_SECRET_KEY_PATTERN})\s*=\s*([\"'])(.*?)(\2)")
_LONG_TOKEN_RE = re.compile(rf"(?i)(?<![a-z0-9_-])({_SECRET_KEY_PATTERN})\s*=\s*([^&\s]+)")
_QUOTED_COLON_SECRET_RE = re.compile(rf"(?i)(?<![a-z0-9_-])({_SECRET_KEY_PATTERN})\s*:\s*([\"'])(.*?)(\2)")
_AUTH_HEADER_RE = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*.*?"
    rf"(?=(?:\s+\b(?:x-api-key|api-key|cookie|set-cookie|{_SECRET_KEY_PATTERN})\s*[:=])|$|[,\n\r])"
)
_COOKIE_HEADER_RE = re.compile(
    r"(?i)\b(cookie|set-cookie)\s*[:=]\s*.*?"
    rf"(?=(?:\s+\b(?:authorization|x-api-key|api-key|cookie|set-cookie|{_SECRET_KEY_PATTERN})\s*[:=])|$|[,\n\r])"
)
_HEADER_SECRET_RE = re.compile(r"(?i)\b(x-api-key|api-key)\s*:\s*([^\s,;]+)")
_COLON_SECRET_RE = re.compile(
    rf"(?i)(?<![a-z0-9_-])({_SECRET_KEY_PATTERN})\s*:\s*"
    r"([^\s,;}]+)"
)
_JSON_SECRET_RE = re.compile(
    rf"(?i)([\"']{_SECRET_KEY_PATTERN}[\"']\s*:\s*)"
    r"([\"'])(.*?)(\2)"
)
_JSON_UNQUOTED_SECRET_RE = re.compile(
    rf"(?i)([\"']{_SECRET_KEY_PATTERN}[\"']\s*:\s*)"
    r"(?![\"'])([^,}\]\s]+)"
)
_HOSTLIKE_NAME_RE = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+[a-z0-9-]+\b")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_DOMAIN_RE = re.compile(r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b")
_FILENAME_DOMAIN_RE = re.compile(r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?=\.[a-z0-9]{1,8}\b)")
_ABS_PATH_RE = re.compile(r"(?<![:/])/(?:[^\s\"'<>:]+/)+[^\s\"'<>:]+")
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:\\(?:[^\s\"'<>:]+\\)+[^\s\"'<>:]+")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\"'<>:\\]+\\[^\s\"'<>:\\]+(?:\\[^\s\"'<>:\\]+)+")
_PASSTHROUGH_BOUNDARY_CHARS = r"a-zA-Z0-9_./\\-"

_debug_restore_task: asyncio.Task[None] | None = None
_debug_restore_level: str | None = None
_debug_temporary_level: str | None = None
_debug_until: datetime | None = None


class SupportCategoryOut(BaseModel):
    key: str
    label: str
    description: str


class DebugLogRequest(BaseModel):
    duration_seconds: int = Field(300, ge=30, le=3600)
    level: str = "DEBUG"


class DebugLogStatusOut(BaseModel):
    active: bool
    level: str
    until: str | None = None


class SupportPackageOut(BaseModel):
    schema_version: int
    generated_at: str
    generated_by: str
    categories: list[str]
    privacy: dict[str, Any]
    installation: dict[str, Any]
    runtime: dict[str, Any]
    adapters: list[dict[str, Any]]
    history: dict[str, Any]
    monitor: dict[str, Any]
    health: dict[str, Any]
    warning_history: list[dict[str, Any]]
    error_history: list[dict[str, Any]]
    debug_log: list[dict[str, Any]]


@router.get("/categories", response_model=list[SupportCategoryOut])
async def support_categories(
    _admin: str = Depends(get_admin_user),
) -> list[SupportCategoryOut]:
    """Return the information categories included in a support export."""
    return _support_categories()


@router.post("/package", response_model=SupportPackageOut)
async def create_support_package(
    db: Database = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> SupportPackageOut:
    """Build a sanitized support package on explicit admin request."""
    now = datetime.now(UTC)
    log_entries = [_sanitize_log_entry(entry) for entry in get_log_buffer()]
    error_history = [entry for entry in log_entries if entry.get("level") in {"ERROR", "CRITICAL"}][-50:]
    warning_history = [entry for entry in log_entries if entry.get("level") in {"WARNING", "ERROR", "CRITICAL"}][-100:]

    return SupportPackageOut(
        schema_version=1,
        generated_at=_iso(now),
        generated_by="[REDACTED]",
        categories=[category.key for category in _support_categories()],
        privacy={
            "automatic_upload": False,
            "remote_access": False,
            "sanitizer": "central_recursive_v1",
            "generated_by_redacted": admin != "[REDACTED]",
            "path_policy": "basename_only",
            "redacted": [
                "credentials",
                "tokens",
                "secrets",
                "IP addresses",
                "domain names",
                "email addresses",
                "endpoint values",
                "filesystem path prefixes",
                "exporting username",
            ],
        },
        installation=_build_installation_info(),
        runtime=_build_runtime_info(now),
        adapters=await _build_adapter_info(db),
        history=await _build_history_info(db),
        monitor=await _build_monitor_info(),
        health=_build_health_info(now),
        warning_history=warning_history,
        error_history=error_history,
        debug_log=log_entries[-200:],
    )


@router.get("/debug-log", response_model=DebugLogStatusOut)
async def get_debug_log_status(
    _admin: str = Depends(get_admin_user),
) -> DebugLogStatusOut:
    return _debug_status()


@router.post("/debug-log", response_model=DebugLogStatusOut)
async def enable_debug_log(
    body: DebugLogRequest,
    _admin: str = Depends(get_admin_user),
) -> DebugLogStatusOut:
    """Temporarily enable verbose logging for support diagnostics."""
    global _debug_restore_level, _debug_restore_task, _debug_temporary_level, _debug_until

    level = body.level.upper()
    if level not in _VALID_LOG_LEVELS:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"level must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}",
        )

    current = logging.getLevelName(logging.getLogger().level)
    if not isinstance(current, str):
        current = "INFO"
    if _debug_restore_level is None:
        _debug_restore_level = current

    if _debug_restore_task and not _debug_restore_task.done():
        _debug_restore_task.cancel()

    _debug_until = datetime.now(UTC) + _duration(body.duration_seconds)
    _debug_temporary_level = level
    set_log_buffer_level(level)
    _debug_restore_task = asyncio.create_task(_restore_debug_later(body.duration_seconds))
    return _debug_status(level=level)


@router.delete("/debug-log", response_model=DebugLogStatusOut)
async def disable_debug_log(
    _admin: str = Depends(get_admin_user),
) -> DebugLogStatusOut:
    """Disable a temporary support debug window immediately."""
    await _restore_debug_now()
    return _debug_status()


def sanitize_support_data(value: Any, key: str | None = None) -> Any:
    """Recursively redact secrets, credentials, endpoints and IP addresses."""
    if _is_passthrough_key(key) and not isinstance(value, (dict, list, tuple)):
        if isinstance(value, str):
            return _sanitize_string(value)
        return value
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if _is_basename_only_key(key) and isinstance(value, str):
        return _sanitize_basename(value)
    if _is_endpoint_key(key):
        return "[REDACTED_ENDPOINT]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            sanitized_key = _deduplicate_dict_key(_sanitize_dict_key(raw_key), sanitized)
            sanitized[sanitized_key] = sanitize_support_data(raw_value, str(raw_key))
        return sanitized
    if isinstance(value, list):
        return [sanitize_support_data(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_support_data(item, key) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def _support_categories() -> list[SupportCategoryOut]:
    return [
        SupportCategoryOut(
            key="installation",
            label="Installation",
            description="Installation type, OBS version, operating system and runtime environment.",
        ),
        SupportCategoryOut(
            key="adapters",
            label="Adapters",
            description="Enabled adapter instances, sanitized adapter configuration, binding and object counts.",
        ),
        SupportCategoryOut(
            key="health",
            label="Health",
            description="Runtime health states, uptime and last start timestamp.",
        ),
        SupportCategoryOut(
            key="history",
            label="History",
            description="History backend configuration and sanitized storage statistics.",
        ),
        SupportCategoryOut(
            key="monitor",
            label="Monitor",
            description="RingBuffer/Monitor configuration, retention and storage statistics.",
        ),
        SupportCategoryOut(
            key="logs",
            label="Logs",
            description="Recent sanitized error history and in-memory debug log entries.",
        ),
    ]


def _build_installation_info() -> dict[str, Any]:
    settings = get_settings()
    return sanitize_support_data(
        {
            "installation_type": _detect_installation_type(),
            "obs_version": __version__,
            "config_source": _basename_only(os.environ.get("OBS_CONFIG") or "config.yaml"),
            "database": {"path": _basename_only(settings.database.path), "history_plugin": settings.database.history_plugin},
        },
    )


def _build_runtime_info(now: datetime) -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "environment": _detect_runtime_environment(),
        "started_at": _iso(_PROCESS_STARTED_AT),
        "uptime_seconds": max(0, int((now - _PROCESS_STARTED_AT).total_seconds())),
        "resources": _build_resource_snapshot(),
    }


async def _build_adapter_info(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM adapter_instances ORDER BY adapter_type, name")
    binding_counts = await _counts_by_adapter(
        db,
        "SELECT adapter_instance_id, COUNT(*) AS count FROM adapter_bindings GROUP BY adapter_instance_id",
    )
    object_counts = await _counts_by_adapter(
        db,
        """SELECT adapter_instance_id, COUNT(DISTINCT datapoint_id) AS count
           FROM adapter_bindings GROUP BY adapter_instance_id""",
    )
    transformation_counts = await _counts_by_adapter(
        db,
        """SELECT adapter_instance_id, COUNT(*) AS count
           FROM adapter_bindings
           WHERE enabled=1
             AND (
               COALESCE(NULLIF(TRIM(value_formula), ''), '') != ''
               OR COALESCE(NULLIF(TRIM(value_map), ''), '{}') NOT IN ('', '{}', '[]')
             )
           GROUP BY adapter_instance_id""",
    )
    filter_counts = await _counts_by_adapter(
        db,
        """SELECT adapter_instance_id, COUNT(*) AS count
           FROM adapter_bindings
           WHERE enabled=1
             AND (
               COALESCE(send_on_change, 0) != 0
               OR COALESCE(send_throttle_ms, 0) > 0
               OR send_min_delta IS NOT NULL
               OR send_min_delta_pct IS NOT NULL
             )
           GROUP BY adapter_instance_id""",
    )
    tps_available, tps_by_instance, tps_by_adapter_type = await _ringbuffer_tps(window_seconds=60)
    result: list[dict[str, Any]] = []
    for row in rows:
        instance = adapter_registry.get_instance_by_id(row["id"])
        adapter_type = row["adapter_type"]
        cls = adapter_registry.get_class(adapter_type)
        config = _adapter_config_or_placeholder(row["config"])
        tps = tps_by_instance.get(row["id"], 0.0)
        result.append(
            {
                "id": row["id"],
                "adapter_type": adapter_type,
                "name": _sanitize_adapter_name(row["name"]),
                "enabled": bool(row["enabled"]),
                "registered": cls is not None,
                "running": instance is not None,
                "connected": bool(instance.connected) if instance else False,
                "version": _adapter_version(cls),
                "config": sanitize_support_data(config),
                "objects": object_counts.get(row["id"], 0),
                "bindings": binding_counts.get(row["id"], 0),
                "active_transformations": transformation_counts.get(row["id"], 0),
                "active_filters": filter_counts.get(row["id"], 0),
                "transactions_per_second": tps if tps_available else None,
                "metrics_available": tps_available,
                "metrics_source": "ringbuffer_metadata_adapter_instance_60s" if tps_available else None,
                "adapter_type_transactions_per_second": (tps_by_adapter_type.get(adapter_type.upper(), 0.0) if tps_available else None),
                "health": {
                    "severity": getattr(instance, "last_severity", "ok") if instance else "ok",
                    "detail": sanitize_support_data(getattr(instance, "last_detail", "") if instance else ""),
                },
            }
        )
    return result


async def _build_history_info(db: Database) -> dict[str, Any]:
    settings = await _read_history_settings(db)
    active_plugin = settings.get("plugin", "sqlite") or "sqlite"
    try:
        table_stats = await _history_table_stats(db)
    except Exception as exc:
        table_stats = {"available": False, "reason": _support_unavailable_reason(exc)}
    runtime_plugin = None
    try:
        from obs.history.factory import get_history_plugin

        runtime_plugin = get_history_plugin().__class__.__name__
    except RuntimeError:
        runtime_plugin = None

    return sanitize_support_data(
        {
            "enabled": True,
            "active_plugin": active_plugin,
            "runtime_plugin": runtime_plugin,
            "settings": settings,
            "sqlite_storage": table_stats,
        }
    )


async def _build_monitor_info() -> dict[str, Any]:
    try:
        from obs.ringbuffer.ringbuffer import get_ringbuffer

        ringbuffer = get_ringbuffer()
        stats = await ringbuffer.stats()
        recent_entries = await ringbuffer.query(limit=200)
    except Exception as exc:
        return {"available": False, "reason": _support_unavailable_reason(exc)}

    source_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    for entry in recent_entries:
        source_counts[entry.source_adapter] = source_counts.get(entry.source_adapter, 0) + 1
        quality_counts[entry.quality] = quality_counts.get(entry.quality, 0) + 1

    return sanitize_support_data(
        {
            "available": True,
            "stats": stats,
            "recent_sample_size": len(recent_entries),
            "recent_source_adapter_counts": source_counts,
            "recent_quality_counts": quality_counts,
        }
    )


def _build_health_info(now: datetime) -> dict[str, Any]:
    all_instances = adapter_registry.get_all_instances()
    return {
        "status": "ok",
        "generated_at": _iso(now),
        "started_at": _iso(_PROCESS_STARTED_AT),
        "uptime_seconds": max(0, int((now - _PROCESS_STARTED_AT).total_seconds())),
        "adapters_registered": len(adapter_registry.all_types()),
        "adapters_running": len(all_instances),
        "adapters_connected": sum(1 for instance in all_instances.values() if instance.connected),
    }


async def _counts_by_adapter(db: Database, query: str) -> dict[str, int]:
    rows = await db.fetchall(query)
    return {row["adapter_instance_id"] or "": int(row["count"]) for row in rows}


async def _ringbuffer_tps(window_seconds: int = 60) -> tuple[bool, dict[str, float], dict[str, float]]:
    try:
        from obs.ringbuffer.ringbuffer import get_ringbuffer

        since = _iso(datetime.now(UTC) - timedelta(seconds=window_seconds))
        entries = await get_ringbuffer().query(from_ts=since or "", limit=10000)
    except Exception:
        return False, {}, {}

    instance_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for entry in entries:
        seen_instances: set[str] = set()
        bindings = entry.metadata.get("bindings") if isinstance(entry.metadata, dict) else None
        if isinstance(bindings, list):
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                instance_id = str(binding.get("adapter_instance_id") or "").strip()
                if instance_id:
                    seen_instances.add(instance_id)
        for instance_id in seen_instances:
            instance_counts[instance_id] = instance_counts.get(instance_id, 0) + 1

        key = (entry.source_adapter or "").upper()
        if key:
            type_counts[key] = type_counts.get(key, 0) + 1

    return (
        True,
        {key: round(count / window_seconds, 3) for key, count in instance_counts.items()},
        {key: round(count / window_seconds, 3) for key, count in type_counts.items()},
    )


async def _read_history_settings(db: Database) -> dict[str, str]:
    defaults = {
        "plugin": "sqlite",
        "default_window_hours": "168",
        "influx_url": "http://localhost:8086",
        "influx_version": "2",
        "influx_token": "",
        "influx_org": "",
        "influx_bucket": "obs",
        "influx_database": "obs",
        "influx_username": "",
        "influx_password": "",
        "timescale_dsn": "",
    }
    rows = await db.fetchall("SELECT key, value FROM app_settings WHERE key LIKE 'history.%'")
    settings = dict(defaults)
    for row in rows:
        short_key = row["key"][len("history.") :]
        if short_key in settings:
            settings[short_key] = row["value"] or ""
    return settings


async def _history_table_stats(db: Database) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT COUNT(*) AS total, COUNT(DISTINCT datapoint_id) AS datapoints, MIN(ts) AS oldest_ts, MAX(ts) AS newest_ts FROM history_values"
    )
    by_adapter_rows = await db.fetchall(
        """SELECT COALESCE(source_adapter, '') AS source_adapter, COUNT(*) AS count
           FROM history_values
           GROUP BY COALESCE(source_adapter, '')
           ORDER BY count DESC
           LIMIT 20"""
    )
    return {
        "total_values": int(row["total"]) if row else 0,
        "datapoints": int(row["datapoints"]) if row else 0,
        "oldest_ts": row["oldest_ts"] if row else None,
        "newest_ts": row["newest_ts"] if row else None,
        "source_adapter_counts": {(adapter_row["source_adapter"] or "unknown"): int(adapter_row["count"]) for adapter_row in by_adapter_rows},
    }


def _sanitize_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": sanitize_support_data(entry.get("ts", ""), "ts"),
        "level": sanitize_support_data(entry.get("level", ""), "level"),
        "logger": _sanitize_string(str(entry.get("logger", ""))),
        "message": sanitize_support_data(entry.get("message", ""), "message"),
    }


def _sanitize_string(value: str) -> str:
    passthrough_tokens: dict[str, str] = {}
    path_tokens: dict[str, str] = {}
    sanitized = value
    for index, literal in enumerate(sorted(_PASSTHROUGH_VALUES, key=len, reverse=True)):
        token = f"__OBS_SUPPORT_PASSTHROUGH_{index}__"
        pattern = re.compile(rf"(?<![{_PASSTHROUGH_BOUNDARY_CHARS}]){re.escape(literal)}(?![{_PASSTHROUGH_BOUNDARY_CHARS}]|:\d)")
        sanitized, count = pattern.subn(token, sanitized)
        if count:
            passthrough_tokens[token] = literal
    sanitized = _QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", sanitized)
    sanitized = _AUTH_HEADER_RE.sub("Authorization: [REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _LONG_TOKEN_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", sanitized)
    sanitized = _JSON_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}", sanitized)
    sanitized = _JSON_UNQUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", sanitized)
    sanitized = _QUOTED_COLON_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _COLON_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _HEADER_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _sanitize_urls(sanitized)
    sanitized = _sanitize_paths(sanitized, path_tokens)
    sanitized = _IPV4_RE.sub("[REDACTED_IP]", sanitized)
    sanitized = _IPV6_CANDIDATE_RE.sub(_sanitize_ipv6_candidate, sanitized)
    sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = _DOMAIN_RE.sub("[REDACTED_DOMAIN]", sanitized)
    for token, replacement in path_tokens.items():
        sanitized = sanitized.replace(token, replacement)
    for token, literal in passthrough_tokens.items():
        sanitized = sanitized.replace(token, literal)
    return sanitized


def _sanitize_adapter_name(value: str) -> str:
    sanitized = sanitize_support_data(value)
    if _HOSTLIKE_NAME_RE.search(sanitized):
        return _HOSTLIKE_NAME_RE.sub("[REDACTED_ENDPOINT]", sanitized)
    return sanitized


def _basename_only(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    return os.path.basename(normalized) or "[REDACTED_PATH]"


def _sanitize_basename(value: str) -> str:
    basename = _basename_only(value)
    sanitized = _QUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", basename)
    sanitized = _AUTH_HEADER_RE.sub("Authorization: [REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _LONG_TOKEN_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", sanitized)
    sanitized = _JSON_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}", sanitized)
    sanitized = _JSON_UNQUOTED_SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", sanitized)
    sanitized = _QUOTED_COLON_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _COLON_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _HEADER_SECRET_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", sanitized)
    sanitized = _sanitize_urls(sanitized)
    sanitized = _IPV4_RE.sub("[REDACTED_IP]", sanitized)
    sanitized = _IPV6_CANDIDATE_RE.sub(_sanitize_ipv6_candidate, sanitized)
    sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
    if _is_domain_only_basename(sanitized):
        return "[REDACTED_DOMAIN]"
    return _FILENAME_DOMAIN_RE.sub("[REDACTED_DOMAIN]", sanitized)


def _sanitize_dict_key(key: Any) -> str:
    return _sanitize_string(str(key))


def _deduplicate_dict_key(key: str, existing: dict[str, Any]) -> str:
    if key not in existing:
        return key
    index = 2
    while f"{key} ({index})" in existing:
        index += 1
    return f"{key} ({index})"


def _is_domain_only_basename(value: str) -> bool:
    if not _DOMAIN_RE.fullmatch(value):
        return False
    extension = value.rsplit(".", 1)[-1].lower()
    return extension not in _BASENAME_FILE_EXTENSIONS


def _sanitize_paths(value: str, tokens: dict[str, str] | None = None) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip("/\\")
        basename = raw.replace("\\", "/").rsplit("/", 1)[-1] or "[REDACTED_PATH]"
        replacement = f"[REDACTED_PATH]/{_sanitize_basename(basename)}"
        if tokens is None:
            return replacement
        token = f"__OBS_SUPPORT_PATH_{len(tokens)}__"
        tokens[token] = replacement
        return token

    sanitized = _WINDOWS_PATH_RE.sub(repl, value)
    sanitized = _UNC_PATH_RE.sub(repl, sanitized)
    return _ABS_PATH_RE.sub(repl, sanitized)


def _sanitize_urls(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return "[REDACTED_URL]"
        replacement = urlunsplit((parsed.scheme, "[REDACTED_ENDPOINT]", "[REDACTED_PATH]", "", ""))
        return replacement.rstrip("/")

    return re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", repl, value, flags=re.IGNORECASE)


def _adapter_config_or_placeholder(raw_config: Any) -> dict[str, Any]:
    if not raw_config:
        return {}
    try:
        config = json.loads(raw_config)
    except (TypeError, json.JSONDecodeError):
        return {"available": False, "reason": "invalid_json"}
    if isinstance(config, dict):
        return config
    return {"available": False, "reason": "invalid_config_type"}


def _support_unavailable_reason(exc: Exception) -> str:
    if isinstance(exc, RuntimeError):
        return "RingBuffer not initialized"
    return exc.__class__.__name__ or "unavailable"


def _sanitize_ipv6_candidate(match: re.Match[str]) -> str:
    candidate = match.group(0)
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return candidate
    if parsed.version == 6:
        return "[REDACTED_IP]"
    return candidate


def _is_sensitive_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.lower().replace("-", "_")
    return lowered in _SENSITIVE_KEYS or any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _is_passthrough_key(key: str | None) -> bool:
    if key is None:
        return False
    return key.lower() in _PASSTHROUGH_KEYS


def _is_basename_only_key(key: str | None) -> bool:
    if key is None:
        return False
    return key.lower() in _BASENAME_ONLY_KEYS


def _is_endpoint_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.lower()
    return any(part in lowered for part in _ENDPOINT_KEY_PARTS)


def _detect_installation_type() -> str:
    if os.path.exists("/.dockerenv") or os.environ.get("container"):
        return "docker"
    return "native"


def _detect_runtime_environment() -> dict[str, Any]:
    return {
        "docker": os.path.exists("/.dockerenv") or bool(os.environ.get("container")),
        "raspberry_pi": platform.machine().startswith(("arm", "aarch64")),
    }


def _build_resource_snapshot() -> dict[str, Any]:
    return sanitize_support_data(
        {
            "captured_at": _iso(datetime.now(UTC)),
            "system": _system_resource_snapshot(),
            "process": _process_resource_snapshot(),
            "disk": _disk_resource_snapshot(),
            "top_cpu_processes": _top_cpu_process_snapshot(),
            "top_memory_processes": _top_memory_process_snapshot(),
        }
    )


def _system_resource_snapshot() -> dict[str, Any]:
    load_avg = None
    if hasattr(os, "getloadavg"):
        try:
            one, five, fifteen = os.getloadavg()
            load_avg = {"1m": one, "5m": five, "15m": fifteen}
        except OSError:
            load_avg = None
    return {
        "cpu_count": os.cpu_count(),
        "load_average": load_avg,
        "memory": _memory_snapshot_procfs(),
    }


def _process_resource_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {"pid": os.getpid()}
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        max_rss_kib = usage.ru_maxrss
        if platform.system() == "Darwin":
            max_rss_kib = int(max_rss_kib / 1024)
        snapshot.update(
            {
                "user_cpu_seconds": usage.ru_utime,
                "system_cpu_seconds": usage.ru_stime,
                "max_rss_bytes": int(max_rss_kib) * 1024,
            }
        )
    except (ImportError, OSError, ValueError):
        snapshot.update({"user_cpu_seconds": None, "system_cpu_seconds": None, "max_rss_bytes": None})
    return snapshot


def _disk_resource_snapshot() -> dict[str, Any]:
    configured_path = Path(get_settings().database.path).expanduser()
    path = _disk_usage_target(configured_path)
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"path": _sanitize_basename(str(configured_path)), "available": False}
    return {
        "path": _sanitize_basename(str(configured_path)),
        "available": True,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _disk_usage_target(path: Path) -> Path:
    candidate = path if path.exists() else path.parent
    if str(candidate) == "":
        return Path.cwd()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else Path.cwd()


def _top_cpu_process_snapshot(limit: int = 5) -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return _top_cpu_process_snapshot_procfs(limit=limit)

    procs = list(psutil.process_iter(["pid", "name", "username", "memory_info"]))
    for proc in procs:
        try:
            proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    time.sleep(0.1)
    items: list[dict[str, Any]] = []
    for proc in procs:
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            items.append(_process_item(info.get("pid"), info.get("name"), proc.cpu_percent(interval=None), getattr(memory_info, "rss", None)))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    items.sort(key=lambda item: (item.get("cpu_percent") or 0, item.get("rss_bytes") or 0), reverse=True)
    return {"available": True, "source": "psutil", "items": items[:limit]}


def _top_memory_process_snapshot(limit: int = 5) -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return _top_memory_process_snapshot_procfs(limit=limit)

    items: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            items.append(_process_item(info.get("pid"), info.get("name"), None, getattr(memory_info, "rss", None)))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    items.sort(key=lambda item: item.get("rss_bytes") or 0, reverse=True)
    return {"available": True, "source": "psutil", "items": items[:limit]}


def _memory_snapshot_procfs() -> dict[str, Any] | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            rows = fh.readlines()
    except OSError:
        return None

    values: dict[str, int] = {}
    for row in rows:
        key, _, rest = row.partition(":")
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": total - available if total is not None and available is not None else None,
    }


def _top_cpu_process_snapshot_procfs(limit: int = 5) -> dict[str, Any]:
    proc_root = "/proc"
    if not os.path.isdir(proc_root):
        return {"available": False, "reason": "psutil_not_installed", "items": []}

    first = _read_procfs_processes()
    time.sleep(0.1)
    second = _read_procfs_processes()
    elapsed = 0.1
    cpu_count = os.cpu_count() or 1
    items: list[dict[str, Any]] = []
    for pid, current in second.items():
        prior = first.get(pid)
        if not prior:
            continue
        delta_seconds = max(0.0, current["cpu_seconds"] - prior["cpu_seconds"])
        cpu_percent = (delta_seconds / elapsed) * 100 / cpu_count
        items.append(_process_item(pid, current["name"], cpu_percent, current["rss_bytes"]))
    items.sort(key=lambda item: (item.get("cpu_percent") or 0, item.get("rss_bytes") or 0), reverse=True)
    return {"available": True, "source": "procfs", "items": items[:limit]}


def _top_memory_process_snapshot_procfs(limit: int = 5) -> dict[str, Any]:
    proc_root = "/proc"
    if not os.path.isdir(proc_root):
        return {"available": False, "reason": "psutil_not_installed", "items": []}

    items = [_process_item(pid, item["name"], None, item["rss_bytes"]) for pid, item in _read_procfs_processes().items()]
    items.sort(key=lambda item: item.get("rss_bytes") or 0, reverse=True)
    return {"available": True, "source": "procfs", "items": items[:limit]}


def _read_procfs_processes() -> dict[int, dict[str, Any]]:
    proc_root = "/proc"
    page_size = os.sysconf("SC_PAGE_SIZE")
    clock_ticks = os.sysconf("SC_CLK_TCK")
    items: dict[int, dict[str, Any]] = {}
    for pid_name in os.listdir(proc_root):
        if not pid_name.isdigit():
            continue
        pid = int(pid_name)
        proc_dir = os.path.join(proc_root, pid_name)
        try:
            with open(os.path.join(proc_dir, "comm"), encoding="utf-8") as fh:
                name = fh.read().strip()
            with open(os.path.join(proc_dir, "stat"), encoding="utf-8") as fh:
                stat_row = fh.read()
            with open(os.path.join(proc_dir, "statm"), encoding="utf-8") as fh:
                statm = fh.read().split()
        except OSError:
            continue
        cpu_seconds = _parse_procfs_stat_cpu_seconds(stat_row, clock_ticks)
        if cpu_seconds is None:
            continue
        rss_pages = int(statm[1]) if len(statm) > 1 and statm[1].isdigit() else 0
        items[pid] = {
            "name": name,
            "cpu_seconds": cpu_seconds,
            "rss_bytes": rss_pages * page_size,
        }
    return items


def _parse_procfs_stat_cpu_seconds(stat_row: str, clock_ticks: int) -> float | None:
    """Parse CPU ticks from /proc/<pid>/stat without splitting the comm field."""
    end_comm = stat_row.rfind(")")
    if end_comm == -1:
        return None
    fields = stat_row[end_comm + 1 :].strip().split()
    if len(fields) <= 12:
        return None
    try:
        utime = int(fields[11])
        stime = int(fields[12])
    except ValueError:
        return None
    return (utime + stime) / clock_ticks


def _process_item(pid: Any, name: Any, cpu_percent: Any, rss_bytes: Any) -> dict[str, Any]:
    return {
        "pid": pid,
        "name": name,
        "username": "[REDACTED]",
        "cpu_percent": cpu_percent,
        "rss_bytes": rss_bytes,
    }


def _adapter_version(cls: type | None) -> str | None:
    if cls is None:
        return None
    module = __import__(cls.__module__, fromlist=["__version__"])
    return getattr(module, "__version__", None)


def _duration(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


def _debug_status(level: str | None = None) -> DebugLogStatusOut:
    active = _debug_until is not None and _debug_until > datetime.now(UTC)
    current_level = level or logging.getLevelName(logging.getLogger().level)
    if not isinstance(current_level, str):
        current_level = "INFO"
    return DebugLogStatusOut(
        active=active,
        level=current_level,
        until=_iso(_debug_until) if active and _debug_until else None,
    )


async def _restore_debug_later(seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)
        await _restore_debug_now()
    except asyncio.CancelledError:
        return


async def _restore_debug_now() -> None:
    global _debug_restore_level, _debug_restore_task, _debug_temporary_level, _debug_until

    if _debug_restore_task and not _debug_restore_task.done():
        _debug_restore_task.cancel()
    restore_level = _debug_restore_level or "INFO"
    current_level = logging.getLevelName(logging.getLogger().level)
    if current_level == (_debug_temporary_level or "DEBUG"):
        set_log_buffer_level(restore_level)
    _debug_restore_level = None
    _debug_restore_task = None
    _debug_temporary_level = None
    _debug_until = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
