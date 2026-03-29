"""
System API — Phase 4 / Phase 5 (Multi-Instance)

GET /api/v1/system/health      liveness check (no auth required)
GET /api/v1/system/adapters    detailed adapter instances + binding stats
GET /api/v1/system/datatypes   all registered DataTypes
GET /api/v1/system/settings    read app settings (timezone, …)
PUT /api/v1/system/settings    update app settings
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.adapters import registry as adapter_registry
from opentws.db.database import get_db, Database
from opentws.models.types import DataTypeRegistry

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness probe — no auth required."""
    try:
        from opentws.core.registry import get_registry
        dp_count = get_registry().count()
    except RuntimeError:
        dp_count = 0

    all_instances = adapter_registry.get_all_instances()
    running = sum(1 for inst in all_instances.values() if inst.connected)

    return HealthOut(
        status="ok",
        version="0.1.0",
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
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unknown timezone: {body.timezone!r}")

    await db.execute_and_commit(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('timezone', ?)",
        (body.timezone,),
    )

    # Hot-reload LogicManager so astro_sun picks up new timezone immediately
    try:
        from opentws.logic.manager import get_logic_manager
        get_logic_manager().update_app_config({"timezone": body.timezone})
    except Exception:
        pass  # Manager may not be running — non-critical

    return AppSettingsOut(timezone=body.timezone)
