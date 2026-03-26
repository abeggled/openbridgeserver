"""
System API — Phase 4

GET /api/v1/system/health      liveness check (no auth required)
GET /api/v1/system/adapters    detailed adapter + binding stats
GET /api/v1/system/datatypes   all registered DataTypes
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.adapters import registry as adapter_registry
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
    adapter_type: str
    registered: bool
    running: bool
    connected: bool
    bindings: int


class DataTypeOut(BaseModel):
    name: str
    python_type: str
    description: str


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

    status_map = adapter_registry.get_status()
    running = sum(1 for v in status_map.values() if v["running"])

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
    from opentws.adapters.registry import get_instance, all_types

    result = []
    for adapter_type in all_types():
        instance = get_instance(adapter_type)
        binding_count = len(instance.get_bindings()) if instance else 0
        result.append(AdapterDetailOut(
            adapter_type=adapter_type,
            registered=True,
            running=instance is not None,
            connected=instance.connected if instance else False,
            bindings=binding_count,
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
