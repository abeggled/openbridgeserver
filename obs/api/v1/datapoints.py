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

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, field_serializer

from obs.api.auth import Principal, get_admin_user, get_current_principal, optional_current_user
from obs.api.authz import AuthzAction, AuthzTarget, authorize
from obs.api.authz_service import (
    _datapoint_read_grants,
    filter_authorized_datapoints,
    load_role_grants,
    resolve_datapoint_targets,
)
from obs.api.v1.datapoint_config import collect_datapoint_ids_from_config
from obs.api.v1.sessions import validate_session
from obs.core.event_bus import DataValueEvent, get_event_bus
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
    # Upstream PR #462 introduced the object form (node_id + node_name per
    # segment) and a display_depth hint per tree; the epic's earlier flat
    # `path: list[str]` is dropped in favour of this richer schema — IDs in
    # each segment make path-elements addressable (clickable drill-down,
    # stable identity across renames).
    node_path: list[NodePathSegment] = []
    display_depth: int = 0


class DataPointDiagnostic(BaseModel):
    type: str
    expected: str | None = None
    got: str | None = None
    source_adapter: str | None = None
    count: int = 1
    last_value: Any = None
    updated_at: str | None = None


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
    diagnostics: list[DataPointDiagnostic] = []
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


def _coerce_value_for_type(value: Any, data_type: str) -> Any:
    """Coerce *value* to the Python type declared for *data_type*.

    Raises ValueError when the value is incompatible so callers can return 422.
    UNKNOWN datapoints accept any value unchanged.
    """
    from obs.models.types import DataTypeRegistry

    defn = DataTypeRegistry.get(data_type)
    if defn.name == "UNKNOWN":
        return value

    py_type = defn.python_type

    if isinstance(value, py_type) and not (py_type is int and isinstance(value, bool)):
        return value
    if py_type is int and isinstance(value, bool):
        return int(value)
    if py_type is float and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if py_type is int and isinstance(value, float) and not isinstance(value, bool) and value == int(value):
        return int(value)
    if py_type is bool and isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    if py_type is datetime.date and isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    if py_type is datetime.time and isinstance(value, str):
        try:
            return datetime.time.fromisoformat(value)
        except ValueError:
            pass
    if py_type is datetime.datetime and isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"Value {value!r} ({type(value).__name__}) is not compatible with data_type '{data_type}'")


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
        diagnostics=list(state.diagnostics.values()) if state else [],
    )


def _principal_from_dependency(value: Principal | str) -> Principal:
    if isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


async def _optional_current_principal(
    request: Request,
    db: Database = Depends(get_db),
) -> Principal | None:
    auth_header = request.headers.get("Authorization")
    api_key = request.headers.get("X-API-Key")
    credentials: HTTPAuthorizationCredentials | None = None
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            credentials = HTTPAuthorizationCredentials(scheme=scheme, credentials=token)

    if credentials is None and not api_key:
        return None

    try:
        return await get_current_principal(credentials=credentials, api_key=api_key, db=db)
    except HTTPException:
        return None


async def _readable_datapoint_ids(db: Database, principal: Principal, dps: list[Any]) -> list[str]:
    ordered_ids = [str(dp.id) for dp in dps]
    if principal.type == "user" and principal.is_admin:
        return ordered_ids
    return await filter_authorized_datapoints(db, principal, ordered_ids, action=AuthzAction.READ)


async def _can_read_datapoint(db: Database, principal: Principal, dp_id: uuid.UUID) -> bool:
    if principal.type == "user" and principal.is_admin:
        return True
    allowed = await filter_authorized_datapoints(db, principal, [str(dp_id)], action=AuthzAction.READ)
    return bool(allowed)


async def _has_explicit_datapoint_read_deny(db: Database, principal: Principal, dp_id: uuid.UUID) -> bool:
    dp_id_str = str(dp_id)
    grants = await load_role_grants(db, principal)
    targets_by_dp = await resolve_datapoint_targets(db, [dp_id_str])
    direct_grant_ids = {grant.node_id for grant in grants if grant.node_type == "datapoint" and grant.node_id == dp_id_str}
    for direct_dp_id in direct_grant_ids:
        targets = targets_by_dp.setdefault(direct_dp_id, [])
        if not any(target.node_type == "datapoint" and target.node_id == direct_dp_id for target in targets):
            targets.append(AuthzTarget(node_type="datapoint", node_id=direct_dp_id))
    targets = targets_by_dp.get(dp_id_str, [])
    decision = authorize(
        principal=principal,
        action=AuthzAction.READ,
        targets=targets,
        grants=_datapoint_read_grants(grants, targets),
    )
    return decision.reason == "explicit_deny"


async def _user_visu_page_has_datapoint(db: Database, username: str, dp_id: uuid.UUID) -> bool:
    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

    rows = await db.fetchall("SELECT id FROM visu_nodes WHERE type = 'PAGE' AND page_config IS NOT NULL")
    for row in rows:
        page_id = row["id"]
        access, _ = await _resolve_access_with_node(db, page_id)
        if access != "user":
            continue
        if not await _check_user_access(db, page_id, username):
            continue
        if await _page_has_datapoint(db, page_id, dp_id):
            return True
    return False


async def _page_context_allows_datapoint_read(
    db: Database,
    request: Request,
    dp_id: uuid.UUID,
    principal: Principal | None = None,
) -> bool:
    page_id = request.headers.get("X-Page-Id")
    if not page_id or not await _page_has_datapoint(db, page_id, dp_id):
        return False

    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    if access in ("public", "readonly"):
        return True
    if access == "protected":
        if principal is not None and principal.type == "user":
            return True
        session_token = request.headers.get("X-Session-Token")
        validate_id = defining_node_id or page_id
        return bool(session_token and validate_session(session_token, validate_id))
    if access == "user" and principal is not None and principal.type == "user":
        return await _check_user_access(db, page_id, principal.subject)
    return False


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
async def list_tags(
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> list[str]:
    reg = get_registry()
    all_dps = reg.all()
    principal = _principal_from_dependency(_user)
    allowed_ids = set(await _readable_datapoint_ids(db, principal, all_dps))
    return sorted({t for dp in all_dps if str(dp.id) in allowed_ids for t in dp.tags})


@router.get("/", response_model=DataPointPage)
async def list_datapoints(
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=10000),
    sort: str = Query("created_at", pattern="^(name|data_type|created_at|updated_at)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> DataPointPage:
    principal = _principal_from_dependency(_user)
    reg = get_registry()
    all_dps = sorted(reg.all(), key=_SORT_KEYS[sort], reverse=(order == "desc"))
    allowed_ids = set(await _readable_datapoint_ids(db, principal, all_dps))
    readable_dps = [dp for dp in all_dps if str(dp.id) in allowed_ids]
    total = len(readable_dps)
    offset = page * size
    items = [_enrich(dp) for dp in readable_dps[offset : offset + size]]
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
    _user: str = Depends(get_admin_user),
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
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> DataPointOut:
    principal = _principal_from_dependency(_user)
    dp = get_registry().get(dp_id)
    if dp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    if not await _can_read_datapoint(db, principal, dp_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    return _enrich(dp)


@router.patch("/{dp_id}", response_model=DataPointOut)
async def update_datapoint(
    dp_id: uuid.UUID,
    body: DataPointUpdate,
    _user: str = Depends(get_admin_user),
) -> DataPointOut:
    reg = get_registry()
    current_dp = reg.get(dp_id)
    if current_dp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    # --- Validation phase (no side effects) ---
    if body.data_type is not None:
        from obs.models.types import DataTypeRegistry

        if not DataTypeRegistry.is_registered(body.data_type):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown data_type '{body.data_type}'",
            )

    coerced: Any = None
    quality: str | None = None
    if "value" in body.model_fields_set:
        if body.value is not None:
            # Use the incoming data_type when it changes in the same request.
            effective_type = body.data_type if body.data_type is not None else current_dp.data_type
            try:
                coerced = _coerce_value_for_type(body.value, effective_type)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
            quality = "good"
        else:
            quality = "uncertain"

    # --- Mutation phase (all validation passed) ---
    # value=None in model_copy keeps value updates out of DataPoint metadata;
    # DataPoint has no value field.
    dp = await reg.update(dp_id, body.model_copy(update={"value": None}))

    if "value" in body.model_fields_set:
        await get_event_bus().publish(
            DataValueEvent(
                datapoint_id=dp_id,
                value=coerced,
                quality=quality,
                source_adapter="api",
            )
        )

    return _enrich(dp)


@router.delete("/{dp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datapoint(
    dp_id: uuid.UUID,
    _user: str = Depends(get_admin_user),
) -> None:
    reg = get_registry()
    if reg.get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    await reg.delete(dp_id)


@router.get("/{dp_id}/value", response_model=ValueOut)
async def get_value(
    dp_id: uuid.UUID,
    request: Request,
    user: Principal | str | None = Depends(_optional_current_principal),
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
    else:
        principal = _principal_from_dependency(user)
        if not await _can_read_datapoint(db, principal, dp_id):
            if await _has_explicit_datapoint_read_deny(db, principal, dp_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
            if not await _page_context_allows_datapoint_read(db, request, dp_id, principal) and (
                principal.type != "user" or not await _user_visu_page_has_datapoint(db, principal.subject, dp_id)
            ):
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

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
        config_dp_ids: set[str] = set()
        collect_datapoint_ids_from_config(widget.config, config_dp_ids)
        if dp_id_str in config_dp_ids:
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
    reg = get_registry()
    dp = reg.get(dp_id)
    if dp is None:
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

    try:
        value = _coerce_value_for_type(body.value, dp.data_type)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    event = DataValueEvent(
        datapoint_id=dp_id,
        value=value,
        quality="good",
        source_adapter="api",
    )
    await get_event_bus().publish(event)
