"""DataPoints API — Phase 4

GET    /api/v1/datapoints            paginated list
POST   /api/v1/datapoints            create
GET    /api/v1/datapoints/{id}       get one (+ current value)
PATCH  /api/v1/datapoints/{id}       update
DELETE /api/v1/datapoints/{id}       delete
GET    /api/v1/datapoints/{id}/value current value only
POST   /api/v1/datapoints/{id}/value write value (fires DataValueEvent)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_serializer

from obs.api.auth import get_current_user, optional_current_user
from obs.api.v1.sessions import validate_session
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.models.datapoint import DataPointCreate, DataPointUpdate
from obs.models.visu import PageConfig

router = APIRouter(tags=["datapoints"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class NodePathSegment(BaseModel):
    node_id: str
    node_name: str


class HierarchyNodeRef(BaseModel):
    node_id: str
    node_name: str
    tree_id: str
    tree_name: str
    node_path: list[NodePathSegment] = []
    display_depth: int = 0


class DataPointOut(BaseModel):
    id: uuid.UUID
    name: str
    data_type: str
    unit: str | None
    tags: list[str]
    mqtt_topic: str
    mqtt_alias: str | None
    persist_value: bool
    record_history: bool
    created_at: str
    updated_at: str
    # Runtime
    value: Any = None
    quality: str | None = None
    # Hierarchy (populated by search endpoint, empty elsewhere)
    hierarchy_nodes: list[HierarchyNodeRef] = []

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


class WriteValueIn(BaseModel):
    value: Any


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
        record_history=dp.record_history,
        created_at=dp.created_at.isoformat(),
        updated_at=dp.updated_at.isoformat(),
        value=state.value if state else None,
        quality=state.quality if state else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_SORT_KEYS = {
    "name": lambda dp: dp.name.lower(),
    "data_type": lambda dp: dp.data_type.lower(),
    "created_at": lambda dp: dp.created_at.isoformat(),
    "updated_at": lambda dp: dp.updated_at.isoformat(),
}


@router.get("/tags", response_model=list[str])
async def list_tags(_user: str = Depends(get_current_user)) -> list[str]:
    reg = get_registry()
    return sorted({t for dp in reg.all() for t in dp.tags})


@router.get("/", response_model=DataPointPage)
async def list_datapoints(
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=10000),
    sort: str = Query("created_at", pattern="^(name|data_type|created_at|updated_at)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    _user: str = Depends(get_current_user),
) -> DataPointPage:
    reg = get_registry()
    all_dps = sorted(reg.all(), key=_SORT_KEYS[sort], reverse=(order == "desc"))
    total = len(all_dps)
    offset = page * size
    items = [_enrich(dp) for dp in all_dps[offset : offset + size]]
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
    from obs.models.types import DataTypeRegistry

    if not DataTypeRegistry.is_registered(body.data_type):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown data_type '{body.data_type}'. Available: {DataTypeRegistry.names()}",
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
        from obs.models.types import DataTypeRegistry

        if not DataTypeRegistry.is_registered(body.data_type):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    request: Request,
    user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> ValueOut:
    reg = get_registry()
    dp = reg.get(dp_id)
    if dp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    if user is None:
        # Unauthentisiert: Seitenkontext prüfen (analog zum POST-Handler)
        page_id = request.headers.get("X-Page-Id")
        if not page_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        if not await _page_has_datapoint(db, page_id, dp_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Datapoint is not part of the page")

        from obs.api.v1.visu import _resolve_access_with_node

        access, defining_node_id = await _resolve_access_with_node(db, page_id)
        if access == "protected":
            session_token = request.headers.get("X-Session-Token")
            validate_id = defining_node_id or page_id
            if not session_token or not validate_session(session_token, validate_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Valid session token required")
        elif access == "user":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        elif access not in ("public", "readonly"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    state = reg.get_value(dp_id)
    return ValueOut(
        id=dp_id,
        value=state.value if state else None,
        unit=dp.unit,
        quality=state.quality if state else "uncertain",
        ts=state.ts.isoformat() if state else None,
    )


async def _page_has_datapoint(db: Database, page_id: str, dp_id: uuid.UUID) -> bool:
    """Return True if the page contains a widget bound to this datapoint.

    Checks both the formal datapoint_id / status_datapoint_id fields and any
    additional datapoint IDs stored inside widget.config (used by Rolladen,
    Licht, RTR and other multi-channel widgets).
    """
    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = ? AND type = 'PAGE'", (page_id,))
    if not row:
        return False

    raw = row["page_config"]
    if not raw:
        return False

    try:
        page = PageConfig.model_validate_json(raw)
    except Exception:
        return False

    dp_id_str = str(dp_id)
    for widget in page.widgets:
        if widget.datapoint_id == dp_id_str or widget.status_datapoint_id == dp_id_str:
            return True
        if any(v == dp_id_str for v in widget.config.values() if isinstance(v, str)):
            return True
    return False


@router.post("/{dp_id}/value", status_code=status.HTTP_204_NO_CONTENT)
async def write_value(
    dp_id: uuid.UUID,
    body: WriteValueIn,
    request: Request,
    user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> None:
    """Write a value to a DataPoint via the internal EventBus.

    Zugriffslogik:
    - JWT vorhanden (eingeloggter Benutzer) → immer erlaubt
    - X-Page-Id Header + Seite ist 'public' → erlaubt
    - X-Page-Id Header + Seite ist 'protected' + gültiger X-Session-Token → erlaubt
    - Seite ist 'readonly' → 403 (auch mit Page-Header)
    - Seite ist 'private' ohne JWT → 401
    - Kein Auth-Kontext → 401
    """
    from obs.core.event_bus import DataValueEvent, get_event_bus

    reg = get_registry()
    if reg.get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    if user is None:
        page_id = request.headers.get("X-Page-Id")
        if not page_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        if not await _page_has_datapoint(db, page_id, dp_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Datapoint is not part of the page")

        from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

        access, defining_node_id = await _resolve_access_with_node(db, page_id)
        if access == "readonly":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Page is read-only")
        if access == "protected":
            session_token = request.headers.get("X-Session-Token")
            validate_id = defining_node_id or page_id
            if not session_token or not validate_session(session_token, validate_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Valid session token required")
        elif access == "user":
            if not await _check_user_access(db, page_id, user):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")
        elif access not in ("public",):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=body.value,
        quality="good",
        source_adapter="api",
    )
    await get_event_bus().publish(event)
