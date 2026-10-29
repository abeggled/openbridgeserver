"""System API — Phase 4 / Phase 5 (Multi-Instance)

GET    /api/v1/system/health           liveness check (no auth required)
GET    /api/v1/system/adapters         detailed adapter instances + binding stats
GET    /api/v1/system/datatypes        all registered DataTypes
GET    /api/v1/system/settings         read app settings (timezone, …)
GET    /api/v1/system/display-settings display formatting settings (public, used by the Visu)
PUT    /api/v1/system/settings         update app settings
GET    /api/v1/system/visu-devices     Visu device profiles (public, used by the Visu)
PUT    /api/v1/system/visu-devices     replace the Visu device profiles (admin only)
GET    /api/v1/system/history/settings read history backend configuration
PUT    /api/v1/system/history/settings update history backend configuration
POST   /api/v1/system/history/test     test history backend connectivity
GET    /api/v1/system/nav-links        list all custom nav links (auth required)
POST   /api/v1/system/nav-links        create a custom nav link (admin only)
PATCH  /api/v1/system/nav-links/{id}   update a custom nav link (admin only)
DELETE /api/v1/system/nav-links/{id}   delete a custom nav link (admin only)
GET    /api/v1/system/logs             recent log entries from in-memory buffer
GET    /api/v1/system/log-level        read current root log level (admin only)
PUT    /api/v1/system/log-level        change log level at runtime (admin only)
GET    /api/v1/system/update-status    update check status of the active channel
GET    /api/v1/system/update-status/release-notes  release notes up to the latest version
POST   /api/v1/system/update-status/check          check for updates now (admin only)
PUT    /api/v1/system/update-channel   change the update channel (admin only)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from pydantic import BaseModel, Field

from obs import __version__
from obs.adapters import registry as adapter_registry
from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context
from obs.api.auth import get_admin_user, get_current_user
from obs.api.v1.redaction import REDACTED, redact_sensitive_fields
from obs.datetime_format import DEFAULT_DATE_FORMAT, DEFAULT_TIME_FORMAT, validate_datetime_setting
from obs.db.database import Database, get_db
from obs.models.types import DataTypeRegistry
from obs.regional_format import (
    DEFAULT_CURRENCY,
    DEFAULT_REGION_FORMAT,
    REGIONAL_SETTING_KEYS,
    SUPPORTED_CURRENCIES,
    SUPPORTED_REGION_FORMATS,
    resolve_currency,
    resolve_region_format,
    validate_regional_setting,
)
from obs.update.versions import Channel
from obs.visu_devices import VISU_DEVICES_KEY, VisuDevice, VisuDeviceOut, VisuDevicesIn, dump_visu_devices, load_visu_devices

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str  # "ok"
    version: str
    datapoints: int
    adapters_running: int


class AdapterDetailOut(BaseModel):
    id: uuid.UUID | None
    adapter_type: str
    name: str
    registered: bool
    running: bool
    connected: bool
    bindings: int


class DataTypeOut(BaseModel):
    name: str
    python_type: str
    description: str


class AppSettingsOut(BaseModel):
    timezone: str
    date_format: str
    time_format: str
    language: str
    region_format: str = DEFAULT_REGION_FORMAT
    currency: str = DEFAULT_CURRENCY


class AppSettingsIn(BaseModel):
    timezone: str | None = None
    date_format: str | None = None
    time_format: str | None = None
    language: str | None = None
    region_format: str | None = None
    currency: str | None = None


class DisplaySettingsOut(BaseModel):
    """Public, read-only display formatting settings.

    Consumed by the Visu SPA, which is reachable without an admin login, and by
    the Admin GUI to build the regional-format/currency option lists.
    """

    language: str
    timezone: str
    date_format: str
    time_format: str
    region_format: str
    currency: str
    resolved_region_format: str
    resolved_currency: str
    supported_region_formats: list[str]
    supported_currencies: list[str]


class HistorySettingsOut(BaseModel):
    plugin: str  # sqlite | influxdb | timescaledb
    default_window_hours: int
    influx_url: str
    influx_version: int
    influx_token: str
    influx_org: str
    influx_bucket: str
    influx_database: str
    influx_username: str
    influx_password: str
    timescale_dsn: str


class HistorySettingsIn(BaseModel):
    plugin: str
    default_window_hours: int = Field(168, ge=1, le=24 * 365)
    influx_url: str = "http://localhost:8086"
    influx_version: int = 2
    influx_token: str = ""
    influx_org: str = ""
    influx_bucket: str = "obs"
    influx_database: str = "obs"
    influx_username: str = ""
    influx_password: str = ""
    timescale_dsn: str = ""


class HistoryTestResult(BaseModel):
    ok: bool
    message: str


class NavLinkOut(BaseModel):
    id: str
    label: str
    url: str
    icon: str
    sort_order: int
    open_new_tab: bool


class NavLinkIn(BaseModel):
    label: str
    url: str
    icon: str = ""
    sort_order: int = 0
    open_new_tab: bool = True


class NavLinkPatch(BaseModel):
    label: str | None = None
    url: str | None = None
    icon: str | None = None
    sort_order: int | None = None
    open_new_tab: bool | None = None


class LogEntryOut(BaseModel):
    ts: str
    level: str
    logger: str
    message: str


class LogLevelOut(BaseModel):
    level: str


class LogLevelIn(BaseModel):
    level: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness probe — no auth required."""
    try:
        from obs.core.registry import get_registry

        dp_count = get_registry().count()
    except RuntimeError:
        dp_count = 0

    all_instances = adapter_registry.get_all_instances()
    running = sum(1 for inst in all_instances.values() if inst.connected)

    return HealthOut(
        status="ok",
        version=__version__,
        datapoints=dp_count,
        adapters_running=running,
    )


@router.get("/adapters", response_model=list[AdapterDetailOut])
async def adapters_detail(
    _user: str = Depends(get_current_user),
) -> list[AdapterDetailOut]:
    """Alle laufenden Adapter-Instanzen mit Status."""
    all_instances = adapter_registry.get_all_instances()
    result = []
    for instance in all_instances.values():
        result.append(
            AdapterDetailOut(
                id=instance._instance_id,
                adapter_type=instance.adapter_type,
                name=instance._instance_name,
                registered=True,
                running=True,
                connected=instance.connected,
                bindings=len(instance.get_bindings()),
            ),
        )
    return result


@router.get("/datatypes", response_model=list[DataTypeOut])
async def datatypes(
    _user: str = Depends(get_current_user),
) -> list[DataTypeOut]:
    return [
        DataTypeOut(
            name=name,
            python_type=d.python_type.__name__,
            description=d.description,
        )
        for name, d in DataTypeRegistry.all().items()
    ]


@router.get("/settings", response_model=AppSettingsOut)
async def get_app_settings(
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AppSettingsOut:
    """Read current application settings."""
    timezone_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'timezone'")
    date_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'date_format'")
    time_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'time_format'")
    language_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'language'")
    region_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'region_format'")
    currency_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'currency'")
    return AppSettingsOut(
        timezone=timezone_row["value"] if timezone_row else "Europe/Zurich",
        date_format=date_row["value"] if date_row else DEFAULT_DATE_FORMAT,
        time_format=time_row["value"] if time_row else DEFAULT_TIME_FORMAT,
        language=language_row["value"] if language_row else "de",
        region_format=region_row["value"] if region_row else DEFAULT_REGION_FORMAT,
        currency=currency_row["value"] if currency_row else DEFAULT_CURRENCY,
    )


@router.get("/display-settings", response_model=DisplaySettingsOut)
async def get_display_settings(db: Database = Depends(get_db)) -> DisplaySettingsOut:
    """Read the display formatting settings.

    Deliberately unauthenticated: the Visu is served to anonymous and PIN-only
    users, who still have to see numbers and dates in the configured regional
    format. The response carries no sensitive data and is read-only.
    """
    settings = await get_app_settings(db=db, _user="")
    region = resolve_region_format(settings.region_format, settings.language)
    return DisplaySettingsOut(
        language=settings.language,
        timezone=settings.timezone,
        date_format=settings.date_format,
        time_format=settings.time_format,
        region_format=settings.region_format,
        currency=settings.currency,
        resolved_region_format=region,
        resolved_currency=resolve_currency(settings.currency, region),
        supported_region_formats=list(SUPPORTED_REGION_FORMATS),
        supported_currencies=list(SUPPORTED_CURRENCIES),
    )


async def _read_visu_devices(db: Database) -> list[VisuDevice]:
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_DEVICES_KEY,))
    return load_visu_devices(row["value"] if row else None)


async def _existing_visu_node_ids(db: Database, node_ids: set[str]) -> set[str]:
    if not node_ids:
        return set()
    placeholders = ", ".join("?" for _ in node_ids)
    rows = await db.fetchall(f"SELECT id FROM visu_nodes WHERE id IN ({placeholders})", tuple(node_ids))
    return {r["id"] for r in rows}


@router.get("/visu-devices", response_model=list[VisuDeviceOut])
async def get_visu_devices(db: Database = Depends(get_db)) -> list[VisuDeviceOut]:
    """List the Visu device profiles (start page and return-to-start timeout).

    Deliberately unauthenticated, like ``/display-settings``: the Visu runs on
    anonymous and PIN-only devices that still need to know their start page.
    The profiles carry no secrets — a protected start page still asks for its
    PIN or login when opened. ``start_node_missing`` reports a start page that
    no longer exists at all (not one the caller merely cannot see), so the Visu
    can open the overview instead.
    """
    devices = await _read_visu_devices(db)
    existing = await _existing_visu_node_ids(db, {d.start_node_id for d in devices if d.start_node_id})
    return [VisuDeviceOut(**d.model_dump(), start_node_missing=d.start_node_id is not None and d.start_node_id not in existing) for d in devices]


@router.put("/visu-devices", response_model=list[VisuDevice])
async def update_visu_devices(
    body: VisuDevicesIn,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> list[VisuDevice]:
    """Replace all Visu device profiles. Admin only."""
    start_ids = {d.start_node_id for d in body.devices if d.start_node_id}
    unknown = sorted(start_ids - await _existing_visu_node_ids(db, start_ids))
    if unknown:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown start page: {', '.join(unknown)}",
        )
    async with db.transaction():
        await db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (VISU_DEVICES_KEY, dump_visu_devices(body.devices)),
        )
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("PUT", "/api/v1/system/visu-devices", resource_id="global", commit=False)
    return body.devices


@router.put("/settings", response_model=AppSettingsOut)
async def update_app_settings(
    body: AppSettingsIn,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AppSettingsOut:
    """Update application settings. Changes are applied immediately."""
    supplied_fields = body.model_fields_set
    if not supplied_fields:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="At least one setting must be supplied")
    format_fields = {"date_format", "time_format"} & supplied_fields
    if len(format_fields) == 1:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Date and time formats must be supplied together",
        )
    try:
        for field in supplied_fields:
            if field in REGIONAL_SETTING_KEYS:
                validate_regional_setting(field, getattr(body, field))
            else:
                validate_datetime_setting(field, getattr(body, field))
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    current_settings = await get_app_settings(db=db, _user=_user)
    updated_config = {
        field: value
        for field in ("timezone", "date_format", "time_format", "language", "region_format", "currency")
        if field in supplied_fields and (value := getattr(body, field)) is not None
    }
    before_config = {field: getattr(current_settings, field) for field in updated_config}
    changed_fields = [field for field, value in updated_config.items() if before_config[field] != value]
    placeholders = ", ".join("(?, ?)" for _ in updated_config)
    parameters = tuple(item for pair in updated_config.items() for item in pair)
    async with db.transaction():
        await db.execute(f"INSERT OR REPLACE INTO app_settings (key, value) VALUES {placeholders}", parameters)
        audit_writer = AuditLogWriter(db=db, context=build_audit_context(request=request, current_user=_user))
        await audit_writer.write_contract(
            "PUT",
            "/api/v1/system/settings",
            resource_id="global",
            details={
                "after": updated_config,
                "before": before_config,
                "changed_fields": changed_fields,
            },
            commit=False,
        )
    # Kept for Database-compatible test doubles; real transactions are already committed.
    await db.commit()

    # Hot-reload LogicManager so astro_sun picks up new timezone immediately
    try:
        from obs.logic.manager import get_logic_manager

        get_logic_manager().update_app_config(updated_config)
    except RuntimeError:
        pass  # Manager may not be running — non-critical

    return AppSettingsOut(
        timezone=updated_config.get("timezone", current_settings.timezone),
        date_format=updated_config.get("date_format", current_settings.date_format),
        time_format=updated_config.get("time_format", current_settings.time_format),
        language=updated_config.get("language", current_settings.language),
        region_format=updated_config.get("region_format", current_settings.region_format),
        currency=updated_config.get("currency", current_settings.currency),
    )


# ---------------------------------------------------------------------------
# History settings
# ---------------------------------------------------------------------------

_HISTORY_KEYS = [
    "plugin",
    "default_window_hours",
    "influx_url",
    "influx_version",
    "influx_token",
    "influx_org",
    "influx_bucket",
    "influx_database",
    "influx_username",
    "influx_password",
    "timescale_dsn",
]

_HISTORY_DEFAULTS: dict[str, str] = {
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

_HISTORY_SENSITIVE_FIELDS = ("influx_token", "influx_password", "timescale_dsn")


async def _read_history_cfg(db: Database) -> dict[str, str]:
    rows = await db.fetchall("SELECT key, value FROM app_settings WHERE key LIKE 'history.%'")
    cfg = dict(_HISTORY_DEFAULTS)
    for r in rows:
        short_key = r["key"][len("history.") :]
        if short_key in cfg:
            cfg[short_key] = r["value"] or ""
    return cfg


@router.get("/history/settings", response_model=HistorySettingsOut)
async def get_history_settings(
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> HistorySettingsOut:
    """Read current history backend configuration."""
    cfg = await _read_history_cfg(db)
    payload = redact_sensitive_fields(
        {
            "plugin": cfg["plugin"],
            "default_window_hours": int(cfg["default_window_hours"]),
            "influx_url": cfg["influx_url"],
            "influx_version": int(cfg["influx_version"]),
            "influx_token": cfg["influx_token"],
            "influx_org": cfg["influx_org"],
            "influx_bucket": cfg["influx_bucket"],
            "influx_database": cfg["influx_database"],
            "influx_username": cfg["influx_username"],
            "influx_password": cfg["influx_password"],
            "timescale_dsn": cfg["timescale_dsn"],
        },
        _HISTORY_SENSITIVE_FIELDS,
    )
    return HistorySettingsOut(**payload)


@router.put("/history/settings", response_model=HistorySettingsOut)
async def update_history_settings(
    body: HistorySettingsIn,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> HistorySettingsOut:
    """Update history backend configuration and hot-reload the plugin. Admin only."""
    if body.plugin not in ("sqlite", "influxdb", "timescaledb"):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="plugin must be one of: sqlite, influxdb, timescaledb",
        )

    data: dict[str, str] = {
        "plugin": body.plugin,
        "default_window_hours": str(body.default_window_hours),
        "influx_url": body.influx_url,
        "influx_version": str(body.influx_version),
        "influx_token": body.influx_token,
        "influx_org": body.influx_org,
        "influx_bucket": body.influx_bucket,
        "influx_database": body.influx_database,
        "influx_username": body.influx_username,
        "influx_password": body.influx_password,
        "timescale_dsn": body.timescale_dsn,
    }

    # UI submits redacted marker unchanged when a secret is already configured.
    # Preserve persisted values instead of storing the marker literal.
    if any(data[field] == REDACTED for field in _HISTORY_SENSITIVE_FIELDS):
        existing_cfg = await _read_history_cfg(db)
        for field in _HISTORY_SENSITIVE_FIELDS:
            if data[field] == REDACTED:
                data[field] = existing_cfg[field]

    async with db.transaction():
        for k, v in data.items():
            await db.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (f"history.{k}", v),
            )

    audit_writer = AuditLogWriter(
        db=db,
        context=build_audit_context(request=request, current_user=_admin),
    )
    audit_details = {
        "plugin": body.plugin,
        "default_window_hours": body.default_window_hours,
        "influx_version": body.influx_version,
    }
    try:
        from obs.history.factory import reload_history_plugin

        await reload_history_plugin(db)
    except Exception as exc:
        logger.exception("History plugin reload failed")
        await audit_writer.write_contract(
            "PUT",
            "/api/v1/system/history/settings",
            resource_id="global",
            details=audit_details,
            outcome=AuditOutcome.FAILED,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Settings saved but plugin reload failed: {exc}",
        ) from exc
    await audit_writer.write_contract(
        "PUT",
        "/api/v1/system/history/settings",
        resource_id="global",
        details=audit_details,
    )

    payload = redact_sensitive_fields(
        {
            "plugin": body.plugin,
            "default_window_hours": body.default_window_hours,
            "influx_url": body.influx_url,
            "influx_version": body.influx_version,
            "influx_token": body.influx_token,
            "influx_org": body.influx_org,
            "influx_bucket": body.influx_bucket,
            "influx_database": body.influx_database,
            "influx_username": body.influx_username,
            "influx_password": body.influx_password,
            "timescale_dsn": body.timescale_dsn,
        },
        _HISTORY_SENSITIVE_FIELDS,
    )
    return HistorySettingsOut(**payload)


@router.post("/history/test", response_model=HistoryTestResult)
async def test_history_connection(
    body: HistorySettingsIn,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> HistoryTestResult:
    """Test connectivity for the given history backend configuration. Admin only."""
    try:
        if body.plugin == "sqlite":
            result = HistoryTestResult(ok=True, message="SQLite is always available.")
        else:
            data: dict[str, str] = {
                "influx_url": body.influx_url,
                "influx_version": str(body.influx_version),
                "influx_token": body.influx_token,
                "influx_org": body.influx_org,
                "influx_bucket": body.influx_bucket,
                "influx_database": body.influx_database,
                "influx_username": body.influx_username,
                "influx_password": body.influx_password,
                "timescale_dsn": body.timescale_dsn,
            }
            if any(data[field] == REDACTED for field in _HISTORY_SENSITIVE_FIELDS):
                existing_cfg = await _read_history_cfg(db)
                for field in _HISTORY_SENSITIVE_FIELDS:
                    if data[field] == REDACTED:
                        data[field] = existing_cfg[field]

            if body.plugin == "influxdb":
                from obs.history.influxdb_plugin import InfluxDBHistoryPlugin

                plugin = InfluxDBHistoryPlugin(
                    url=data["influx_url"],
                    version=int(data["influx_version"]),
                    token=data["influx_token"],
                    org=data["influx_org"],
                    bucket=data["influx_bucket"],
                    database=data["influx_database"],
                    username=data["influx_username"],
                    password=data["influx_password"],
                )
                ok = await plugin.ping()
                message = f"InfluxDB v{body.influx_version} {'reachable' if ok else 'not reachable'} at {body.influx_url}"
                if not ok and plugin.last_ping_error:
                    message = f"InfluxDB v{body.influx_version} at {body.influx_url}: {plugin.last_ping_error}"
                result = HistoryTestResult(ok=ok, message=message)
            elif body.plugin == "timescaledb":
                from obs.history.timescaledb_plugin import TimescaleDBHistoryPlugin

                plugin = TimescaleDBHistoryPlugin(dsn=data["timescale_dsn"])
                ok = await plugin.ping()
                result = HistoryTestResult(
                    ok=ok,
                    message=f"PostgreSQL/TimescaleDB {'reachable' if ok else 'not reachable'}.",
                )
            else:
                result = HistoryTestResult(ok=False, message=f"Unknown plugin: {body.plugin}")
    except Exception as exc:
        logger.exception("History connection test failed")
        result = HistoryTestResult(ok=False, message=str(exc))

    writer = AuditLogWriter(db, build_audit_context(request, _admin))
    outcome = AuditOutcome.SUCCESS if result.ok else AuditOutcome.FAILED
    await writer.write_contract("POST", "/api/v1/system/history/test", resource_id="global", outcome=outcome)
    return result


# ---------------------------------------------------------------------------
# Nav Links
# ---------------------------------------------------------------------------


@router.get("/nav-links", response_model=list[NavLinkOut])
async def list_nav_links(
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[NavLinkOut]:
    """List all custom navigation links, ordered by sort_order."""
    rows = await db.fetchall("SELECT id, label, url, icon, sort_order, open_new_tab FROM nav_links ORDER BY sort_order, created_at")
    return [
        NavLinkOut(
            id=r["id"],
            label=r["label"],
            url=r["url"],
            icon=r["icon"] or "",
            sort_order=r["sort_order"],
            open_new_tab=bool(r["open_new_tab"]),
        )
        for r in rows
    ]


@router.post("/nav-links", response_model=NavLinkOut, status_code=201)
async def create_nav_link(
    body: NavLinkIn,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> NavLinkOut:
    """Create a new custom navigation link. Admin only."""
    link_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with db.transaction():
        await db.execute_and_commit(
            "INSERT INTO nav_links (id, label, url, icon, sort_order, open_new_tab, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                link_id,
                body.label,
                body.url,
                body.icon,
                body.sort_order,
                int(body.open_new_tab),
                now,
            ),
        )
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("POST", "/api/v1/system/nav-links", resource_id=link_id, commit=False)
    return NavLinkOut(
        id=link_id,
        label=body.label,
        url=body.url,
        icon=body.icon,
        sort_order=body.sort_order,
        open_new_tab=body.open_new_tab,
    )


@router.patch("/nav-links/{link_id}", response_model=NavLinkOut)
async def update_nav_link(
    link_id: str,
    body: NavLinkPatch,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> NavLinkOut:
    """Update an existing custom navigation link. Admin only."""
    row = await db.fetchone(
        "SELECT id, label, url, icon, sort_order, open_new_tab FROM nav_links WHERE id = ?",
        (link_id,),
    )
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Link not found")

    new_label = body.label if body.label is not None else row["label"]
    new_url = body.url if body.url is not None else row["url"]
    new_icon = body.icon if body.icon is not None else row["icon"]
    new_sort_order = body.sort_order if body.sort_order is not None else row["sort_order"]
    new_open_new_tab = body.open_new_tab if body.open_new_tab is not None else bool(row["open_new_tab"])

    async with db.transaction():
        await db.execute_and_commit(
            "UPDATE nav_links SET label=?, url=?, icon=?, sort_order=?, open_new_tab=? WHERE id=?",
            (new_label, new_url, new_icon, new_sort_order, int(new_open_new_tab), link_id),
        )
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("PATCH", "/api/v1/system/nav-links/{link_id}", resource_id=link_id, commit=False)
    return NavLinkOut(
        id=link_id,
        label=new_label,
        url=new_url,
        icon=new_icon,
        sort_order=new_sort_order,
        open_new_tab=new_open_new_tab,
    )


@router.delete("/nav-links/{link_id}", status_code=204)
async def delete_nav_link(
    link_id: str,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> None:
    """Delete a custom navigation link. Admin only."""
    row = await db.fetchone("SELECT id FROM nav_links WHERE id = ?", (link_id,))
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Link not found")
    async with db.transaction():
        await db.execute_and_commit("DELETE FROM nav_links WHERE id = ?", (link_id,))
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("DELETE", "/api/v1/system/nav-links/{link_id}", resource_id=link_id, commit=False)


# ---------------------------------------------------------------------------
# Log buffer
# ---------------------------------------------------------------------------

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.get("/logs", response_model=list[LogEntryOut])
async def get_logs(
    level: str | None = None,
    limit: int = 200,
    _user: str = Depends(get_current_user),
) -> list[LogEntryOut]:
    """Recent log entries from the in-memory buffer, newest first.

    Optional query params:
    - level: filter by exact level name (INFO, WARNING, ERROR, CRITICAL)
    - limit: max entries to return (default 200, max 500)
    """
    from obs.log_buffer import get_log_buffer

    entries = get_log_buffer()
    if level:
        lvl = level.upper()
        entries = [e for e in entries if e["level"] == lvl]
    limit = max(1, min(limit, 500))
    entries = entries[-limit:]
    entries.reverse()
    return [LogEntryOut(**e) for e in entries]


@router.get("/log-level", response_model=LogLevelOut)
async def get_log_level(
    _admin: str = Depends(get_admin_user),
) -> LogLevelOut:
    """Return the current root log level. Admin only."""
    import logging as _logging

    lvl = _logging.getLevelName(_logging.getLogger().level)
    return LogLevelOut(level=lvl)


@router.put("/log-level", status_code=204)
async def set_log_level(
    body: LogLevelIn,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> None:
    """Change the root log level at runtime without restarting. Admin only."""
    from obs.log_buffer import set_log_buffer_level

    lvl = body.level.upper()
    if lvl not in _VALID_LOG_LEVELS:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"level must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}",
        )
    set_log_buffer_level(lvl)
    writer = AuditLogWriter(db, build_audit_context(request, _admin))
    await writer.write_contract("PUT", "/api/v1/system/log-level", resource_id="global")


# ---------------------------------------------------------------------------
# Update check (issue #1370)
# ---------------------------------------------------------------------------


class UpdateStatusOut(BaseModel):
    enabled: bool
    channel: str
    channels: list[str]
    installed_version: str
    latest_version: str | None
    latest_url: str | None
    update_available: bool | None
    last_check_at: datetime | None
    last_success_at: datetime | None
    next_check_at: datetime | None
    # Stable key under ``updates.errors.*`` in the Admin GUI locales; None after a successful check.
    last_error: str | None
    last_error_detail: str | None
    # The last check failed, so the shown result may be out of date.
    stale: bool
    checking: bool
    release_notes_count: int


class ReleaseNoteOut(BaseModel):
    version: str
    name: str
    published_at: str | None
    url: str
    body: str


class UpdateChannelIn(BaseModel):
    channel: Channel


def _update_service():
    from obs.update.service import get_optional_update_service

    service = get_optional_update_service()
    if service is None:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="Update check not initialized")
    return service


def _update_status_out(service) -> UpdateStatusOut:
    from obs.update.versions import CHANNELS

    state = service.state
    return UpdateStatusOut(
        enabled=service.enabled,
        channel=state.channel,
        channels=list(CHANNELS),
        installed_version=state.installed_version,
        latest_version=state.latest_version,
        latest_url=state.latest_url,
        update_available=state.update_available,
        last_check_at=state.last_check_at,
        last_success_at=state.last_success_at,
        next_check_at=service.next_check_at,
        last_error=state.last_error,
        last_error_detail=state.last_error_detail,
        stale=state.last_error is not None and state.last_success_at is not None,
        checking=service.checking,
        release_notes_count=len(state.release_notes),
    )


@router.get("/update-status", response_model=UpdateStatusOut)
async def get_update_status(_user: str = Depends(get_current_user)) -> UpdateStatusOut:
    """Cached result of the last update check — never asks GitHub itself."""
    return _update_status_out(_update_service())


@router.get("/update-status/release-notes", response_model=list[ReleaseNoteOut])
async def get_update_release_notes(_user: str = Depends(get_current_user)) -> list[ReleaseNoteOut]:
    """Release notes of the active channel's versions newer than the installed one, oldest first."""
    return [ReleaseNoteOut(**note.__dict__) for note in _update_service().state.release_notes]


@router.post("/update-status/check", response_model=UpdateStatusOut)
async def check_for_updates(
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> UpdateStatusOut:
    """Check GitHub for a newer version now. Admin only."""
    service = _update_service()
    if not service.enabled:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Update check is disabled (update_check.enabled = false)")
    state, ran = await service.check_now()
    if ran:
        # Only an actual request to GitHub is audited — not a call answered from
        # the cool-down or by joining a check that was already running.
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        outcome = AuditOutcome.FAILED if state.last_error else AuditOutcome.SUCCESS
        await writer.write_contract("POST", "/api/v1/system/update-status/check", resource_id="global", outcome=outcome)
    return _update_status_out(service)


@router.put("/update-channel", response_model=UpdateStatusOut)
async def set_update_channel(
    body: UpdateChannelIn,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> UpdateStatusOut:
    """Switch the update channel. Admin only."""
    service = _update_service()
    before = service.state.channel
    state, checked = await service.set_channel(body.channel)
    writer = AuditLogWriter(db, build_audit_context(request, _admin))
    await writer.write_contract(
        "PUT",
        "/api/v1/system/update-channel",
        resource_id="global",
        details={"before": before, "after": body.channel},
    )
    if checked:
        # The switch asked GitHub: audited like a manual check.
        outcome = AuditOutcome.FAILED if state.last_error else AuditOutcome.SUCCESS
        await writer.write_contract("POST", "/api/v1/system/update-status/check", resource_id="global", outcome=outcome)
    return _update_status_out(service)
