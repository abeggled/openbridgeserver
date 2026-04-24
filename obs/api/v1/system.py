"""
System API — Phase 4 / Phase 5 (Multi-Instance)

GET    /api/v1/system/health           liveness check (no auth required)
GET    /api/v1/system/adapters         detailed adapter instances + binding stats
GET    /api/v1/system/datatypes        all registered DataTypes
GET    /api/v1/system/settings         read app settings (timezone, …)
PUT    /api/v1/system/settings         update app settings
GET    /api/v1/system/history/settings read history backend configuration
PUT    /api/v1/system/history/settings update history backend configuration
POST   /api/v1/system/history/test     test history backend connectivity
GET    /api/v1/system/nav-links        list all custom nav links (auth required)
POST   /api/v1/system/nav-links        create a custom nav link (admin only)
PATCH  /api/v1/system/nav-links/{id}   update a custom nav link (admin only)
DELETE /api/v1/system/nav-links/{id}   delete a custom nav link (admin only)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel

from obs import __version__
from obs.api.auth import get_current_user, get_admin_user
from obs.adapters import registry as adapter_registry
from obs.db.database import get_db, Database
from obs.models.types import DataTypeRegistry

router = APIRouter(tags=["system"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class HealthOut(BaseModel):
    status: str   # "ok"
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


class AppSettingsIn(BaseModel):
    timezone: str


class HistorySettingsOut(BaseModel):
    plugin: str            # sqlite | influxdb | timescaledb
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
    for instance_id, instance in all_instances.items():
        result.append(AdapterDetailOut(
            id=instance._instance_id,
            adapter_type=instance.adapter_type,
            name=instance._instance_name,
            registered=True,
            running=True,
            connected=instance.connected,
            bindings=len(instance.get_bindings()),
        ))
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
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'timezone'")
    return AppSettingsOut(timezone=row["value"] if row else "Europe/Zurich")


@router.put("/settings", response_model=AppSettingsOut)
async def update_app_settings(
    body: AppSettingsIn,
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AppSettingsOut:
    """Update application settings. Changes are applied immediately."""
    # Validate timezone using zoneinfo
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Unknown timezone: {body.timezone!r}")

    await db.execute_and_commit(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('timezone', ?)",
        (body.timezone,),
    )

    # Hot-reload LogicManager so astro_sun picks up new timezone immediately
    try:
        from obs.logic.manager import get_logic_manager
        get_logic_manager().update_app_config({"timezone": body.timezone})
    except Exception:
        pass  # Manager may not be running — non-critical

    return AppSettingsOut(timezone=body.timezone)


# ---------------------------------------------------------------------------
# History settings
# ---------------------------------------------------------------------------

_HISTORY_KEYS = [
    "plugin",
    "influx_url", "influx_version", "influx_token", "influx_org",
    "influx_bucket", "influx_database", "influx_username", "influx_password",
    "timescale_dsn",
]

_HISTORY_DEFAULTS: dict[str, str] = {
    "plugin":           "sqlite",
    "influx_url":       "http://localhost:8086",
    "influx_version":   "2",
    "influx_token":     "",
    "influx_org":       "",
    "influx_bucket":    "obs",
    "influx_database":  "obs",
    "influx_username":  "",
    "influx_password":  "",
    "timescale_dsn":    "",
}


async def _read_history_cfg(db: Database) -> dict[str, str]:
    rows = await db.fetchall(
        "SELECT key, value FROM app_settings WHERE key LIKE 'history.%'"
    )
    cfg = dict(_HISTORY_DEFAULTS)
    for r in rows:
        short_key = r["key"][len("history."):]
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
    return HistorySettingsOut(
        plugin=cfg["plugin"],
        influx_url=cfg["influx_url"],
        influx_version=int(cfg["influx_version"]),
        influx_token=cfg["influx_token"],
        influx_org=cfg["influx_org"],
        influx_bucket=cfg["influx_bucket"],
        influx_database=cfg["influx_database"],
        influx_username=cfg["influx_username"],
        influx_password=cfg["influx_password"],
        timescale_dsn=cfg["timescale_dsn"],
    )


@router.put("/history/settings", response_model=HistorySettingsOut)
async def update_history_settings(
    body: HistorySettingsIn,
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
        "plugin":           body.plugin,
        "influx_url":       body.influx_url,
        "influx_version":   str(body.influx_version),
        "influx_token":     body.influx_token,
        "influx_org":       body.influx_org,
        "influx_bucket":    body.influx_bucket,
        "influx_database":  body.influx_database,
        "influx_username":  body.influx_username,
        "influx_password":  body.influx_password,
        "timescale_dsn":    body.timescale_dsn,
    }

    for k, v in data.items():
        await db.execute_and_commit(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (f"history.{k}", v),
        )

    # Hot-reload the history plugin
    try:
        from obs.history.factory import reload_history_plugin
        await reload_history_plugin(db)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Settings saved but plugin reload failed: {exc}",
        )

    return HistorySettingsOut(
        plugin=body.plugin,
        influx_url=body.influx_url,
        influx_version=body.influx_version,
        influx_token=body.influx_token,
        influx_org=body.influx_org,
        influx_bucket=body.influx_bucket,
        influx_database=body.influx_database,
        influx_username=body.influx_username,
        influx_password=body.influx_password,
        timescale_dsn=body.timescale_dsn,
    )


@router.post("/history/test", response_model=HistoryTestResult)
async def test_history_connection(
    body: HistorySettingsIn,
    _admin: str = Depends(get_admin_user),
) -> HistoryTestResult:
    """Test connectivity for the given history backend configuration. Admin only."""
    try:
        if body.plugin == "sqlite":
            return HistoryTestResult(ok=True, message="SQLite is always available.")

        if body.plugin == "influxdb":
            from obs.history.influxdb_plugin import InfluxDBHistoryPlugin
            plugin = InfluxDBHistoryPlugin(
                url=body.influx_url,
                version=body.influx_version,
                token=body.influx_token,
                org=body.influx_org,
                bucket=body.influx_bucket,
                database=body.influx_database,
                username=body.influx_username,
                password=body.influx_password,
            )
            ok = await plugin.ping()
            if ok:
                return HistoryTestResult(ok=True, message=f"InfluxDB v{body.influx_version} reachable at {body.influx_url}")
            return HistoryTestResult(ok=False, message=f"InfluxDB v{body.influx_version} not reachable at {body.influx_url}")

        if body.plugin == "timescaledb":
            from obs.history.timescaledb_plugin import TimescaleDBHistoryPlugin
            plugin = TimescaleDBHistoryPlugin(dsn=body.timescale_dsn)
            ok = await plugin.ping()
            if ok:
                return HistoryTestResult(ok=True, message="PostgreSQL/TimescaleDB reachable.")
            return HistoryTestResult(ok=False, message="PostgreSQL/TimescaleDB not reachable.")

        return HistoryTestResult(ok=False, message=f"Unknown plugin: {body.plugin}")

    except RuntimeError as exc:
        # Missing optional dependency
        return HistoryTestResult(ok=False, message=str(exc))
    except Exception as exc:
        return HistoryTestResult(ok=False, message=str(exc))


# ---------------------------------------------------------------------------
# Nav Links
# ---------------------------------------------------------------------------

@router.get("/nav-links", response_model=list[NavLinkOut])
async def list_nav_links(
    db: Database = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[NavLinkOut]:
    """List all custom navigation links, ordered by sort_order."""
    rows = await db.fetchall(
        "SELECT id, label, url, icon, sort_order, open_new_tab FROM nav_links ORDER BY sort_order, created_at"
    )
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
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> NavLinkOut:
    """Create a new custom navigation link. Admin only."""
    link_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        "INSERT INTO nav_links (id, label, url, icon, sort_order, open_new_tab, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (link_id, body.label, body.url, body.icon, body.sort_order, int(body.open_new_tab), now),
    )
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

    new_label       = body.label       if body.label       is not None else row["label"]
    new_url         = body.url         if body.url         is not None else row["url"]
    new_icon        = body.icon        if body.icon        is not None else row["icon"]
    new_sort_order  = body.sort_order  if body.sort_order  is not None else row["sort_order"]
    new_open_new_tab = body.open_new_tab if body.open_new_tab is not None else bool(row["open_new_tab"])

    await db.execute_and_commit(
        "UPDATE nav_links SET label=?, url=?, icon=?, sort_order=?, open_new_tab=? WHERE id=?",
        (new_label, new_url, new_icon, new_sort_order, int(new_open_new_tab), link_id),
    )
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
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> None:
    """Delete a custom navigation link. Admin only."""
    row = await db.fetchone("SELECT id FROM nav_links WHERE id = ?", (link_id,))
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Link not found")
    await db.execute_and_commit("DELETE FROM nav_links WHERE id = ?", (link_id,))
