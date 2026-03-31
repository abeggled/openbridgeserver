"""
DataPoints API — Phase 4

GET    /api/v1/datapoints            paginated list
POST   /api/v1/datapoints            create
GET    /api/v1/datapoints/{id}       get one (+ current value)
PATCH  /api/v1/datapoints/{id}       update
DELETE /api/v1/datapoints/{id}       delete
GET    /api/v1/datapoints/{id}/value current value only
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_serializer

from opentws.api.auth import get_current_user
from opentws.core.registry import get_registry
from opentws.models.datapoint import DataPointCreate, DataPointUpdate

router = APIRouter(tags=["datapoints"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DataPointOut(BaseModel):
    id: uuid.UUID
    name: str
    data_type: str
    unit: str | None
    tags: list[str]
    mqtt_topic: str
    mqtt_alias: str | None
    persist_value: bool
    created_at: str
    updated_at: str
    # Runtime
    value: Any = None
    quality: str | None = None

    model_config = {"from_attributes": True}

    @field_serializer("value")
    def _serialize_value(self, v: Any) -> Any:
        if isinstance(v, (bytes, bytearray)):
            return v.hex()
        return v


class DataPointPage(BaseModel):
    items: list[DataPointOut]
    total: int
    page: int
    size: int
    pages: int


class ValueOut(BaseModel):
    id: uuid.UUID
    value: Any
    unit: str | None
    quality: str
    ts: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enrich(dp: Any) -> DataPointOut:
    """Add current value/quality from registry ValueState."""
    reg = get_registry()
    state = reg.get_value(dp.id)
    return DataPointOut(
        id=dp.id,
        name=dp.name,
        data_type=dp.data_type,
        unit=dp.unit,
        tags=dp.tags,
        mqtt_topic=dp.mqtt_topic,
        mqtt_alias=dp.mqtt_alias,
        persist_value=dp.persist_value,
        created_at=dp.created_at.isoformat(),
        updated_at=dp.updated_at.isoformat(),
        value=state.value if state else None,
        quality=state.quality if state else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_SORT_KEYS = {
    "name":       lambda dp: dp.name.lower(),
    "data_type":  lambda dp: dp.data_type.lower(),
    "created_at": lambda dp: dp.created_at.isoformat(),
    "updated_at": lambda dp: dp.updated_at.isoformat(),
}


@router.get("/", response_model=DataPointPage)
async def list_datapoints(
    page:  int = Query(0, ge=0),
    size:  int = Query(50, ge=1, le=500),
    sort:  str = Query("created_at", pattern="^(name|data_type|created_at|updated_at)$"),
    order: str = Query("asc",        pattern="^(asc|desc)$"),
    _user: str = Depends(get_current_user),
) -> DataPointPage:
    reg     = get_registry()
    all_dps = sorted(reg.all(), key=_SORT_KEYS[sort], reverse=(order == "desc"))
    total   = len(all_dps)
    offset  = page * size
    items   = [_enrich(dp) for dp in all_dps[offset : offset + size]]
    return DataPointPage(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, (total + size - 1) // size),
    )


@router.post("/", response_model=DataPointOut, status_code=status.HTTP_201_CREATED)
async def create_datapoint(
    body: DataPointCreate,
    _user: str = Depends(get_current_user),
) -> DataPointOut:
    from opentws.models.types import DataTypeRegistry
    if not DataTypeRegistry.is_registered(body.data_type):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown data_type '{body.data_type}'. "
            f"Available: {DataTypeRegistry.names()}",
        )
    reg = get_registry()
    dp = await reg.create(body)
    return _enrich(dp)


@router.get("/{dp_id}", response_model=DataPointOut)
async def get_datapoint(
    dp_id: uuid.UUID,
    _user: str = Depends(get_current_user),
) -> DataPointOut:
    dp = get_registry().get(dp_id)
    if dp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    return _enrich(dp)


@router.patch("/{dp_id}", response_model=DataPointOut)
async def update_datapoint(
    dp_id: uuid.UUID,
    body: DataPointUpdate,
    _user: str = Depends(get_current_user),
) -> DataPointOut:
    reg = get_registry()
    if reg.get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    if body.data_type is not None:
        from opentws.models.types import DataTypeRegistry
        if not DataTypeRegistry.is_registered(body.data_type):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown data_type '{body.data_type}'",
            )
    dp = await reg.update(dp_id, body)
    return _enrich(dp)


@router.delete("/{dp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datapoint(
    dp_id: uuid.UUID,
    _user: str = Depends(get_current_user),
) -> None:
    reg = get_registry()
    if reg.get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    await reg.delete(dp_id)


@router.get("/{dp_id}/value", response_model=ValueOut)
async def get_value(
    dp_id: uuid.UUID,
    _user: str = Depends(get_current_user),
) -> ValueOut:
    reg = get_registry()
    dp = reg.get(dp_id)
    if dp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    state = reg.get_value(dp_id)
    return ValueOut(
        id=dp_id,
        value=state.value if state else None,
        unit=dp.unit,
        quality=state.quality if state else "uncertain",
        ts=state.ts.isoformat() if state else None,
    )
