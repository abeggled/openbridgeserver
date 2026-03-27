"""
Bindings API — Phase 4 / Phase 5 (Multi-Instance)

GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}

Phase 5: Bindings referenzieren adapter_instance_id (UUID), nicht mehr adapter_type.
adapter_type wird aus der Instanz abgeleitet und denormalisiert gespeichert.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.core.registry import get_registry
from opentws.db.database import get_db, Database
from opentws.models.binding import AdapterBindingCreate, AdapterBindingUpdate, AdapterBinding

router = APIRouter(tags=["bindings"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class BindingOut(BaseModel):
    id: uuid.UUID
    datapoint_id: uuid.UUID
    adapter_type: str
    adapter_instance_id: uuid.UUID | None
    instance_name: str | None
    direction: str
    config: dict
    enabled: bool
    send_throttle_ms: int | None = None
    send_on_change: bool = False
    send_min_delta: float | None = None
    send_min_delta_pct: float | None = None
    value_formula: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_instance_name_map(db: Database) -> dict[str, str]:
    """instance_id → name Mapping aus DB."""
    rows = await db.fetchall("SELECT id, name FROM adapter_instances")
    return {row["id"]: row["name"] for row in rows}


async def _get_bindings_for_dp(db: Database, dp_id: uuid.UUID) -> list[BindingOut]:
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE datapoint_id=? ORDER BY created_at",
        (str(dp_id),),
    )
    name_map = await _get_instance_name_map(db)
    return [_row_out(r, name_map) for r in rows]


async def _reload_adapter_instance(instance_id: str, db: Database) -> None:
    """Laufende Adapter-Instanz über ihre Bindings aus DB informieren."""
    from opentws.adapters.registry import get_instance_by_id, _row_to_binding
    instance = get_instance_by_id(instance_id)
    if instance is None:
        return
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
        (instance_id,),
    )
    bindings = [_row_to_binding(r) for r in rows]
    await instance.reload_bindings(bindings)


def _row_out(row: Any, name_map: dict[str, str] | None = None) -> BindingOut:
    instance_id = row["adapter_instance_id"]
    throttle    = row["send_throttle_ms"]
    min_delta   = row["send_min_delta"]
    min_delta_p = row["send_min_delta_pct"]
    return BindingOut(
        id=uuid.UUID(row["id"]),
        datapoint_id=uuid.UUID(row["datapoint_id"]),
        adapter_type=row["adapter_type"],
        adapter_instance_id=uuid.UUID(instance_id) if instance_id else None,
        instance_name=name_map.get(instance_id) if name_map and instance_id else None,
        direction=row["direction"],
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        send_throttle_ms=int(throttle) if throttle is not None else None,
        send_on_change=bool(row["send_on_change"]),
        send_min_delta=float(min_delta) if min_delta is not None else None,
        send_min_delta_pct=float(min_delta_p) if min_delta_p is not None else None,
        value_formula=row["value_formula"] or None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{dp_id}/bindings", response_model=list[BindingOut])
async def list_bindings(
    dp_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[BindingOut]:
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} nicht gefunden")
    return await _get_bindings_for_dp(db, dp_id)


@router.post(
    "/{dp_id}/bindings",
    response_model=BindingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_binding(
    dp_id: uuid.UUID,
    body: AdapterBindingCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> BindingOut:
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} nicht gefunden")

    # Instanz aus DB laden → adapter_type ableiten
    instance_row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(body.adapter_instance_id),)
    )
    if instance_row is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Adapter-Instanz '{body.adapter_instance_id}' nicht gefunden",
        )
    adapter_type = instance_row["adapter_type"]

    # Binding-Config gegen Schema validieren
    from opentws.adapters.registry import get_class
    cls = get_class(adapter_type)
    if cls and hasattr(cls, "binding_config_schema"):
        try:
            cls.binding_config_schema(**body.config)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Ungültige Binding-Config: {exc}",
            ) from exc

    # Formel validieren
    if body.value_formula:
        from opentws.core.formula import validate_formula
        err = validate_formula(body.value_formula)
        if err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültige Formel: {err}")

    binding_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled,
            send_throttle_ms, send_on_change, send_min_delta, send_min_delta_pct,
            value_formula, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            binding_id, str(dp_id), adapter_type,
            str(body.adapter_instance_id), body.direction,
            json.dumps(body.config), int(body.enabled),
            body.send_throttle_ms, int(body.send_on_change),
            body.send_min_delta, body.send_min_delta_pct,
            body.value_formula or None,
            now, now,
        ),
    )
    await _reload_adapter_instance(str(body.adapter_instance_id), db)

    row = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (binding_id,))
    name_map = await _get_instance_name_map(db)
    return _row_out(row, name_map)


@router.patch("/{dp_id}/bindings/{binding_id}", response_model=BindingOut)
async def update_binding(
    dp_id: uuid.UUID,
    binding_id: uuid.UUID,
    body: AdapterBindingUpdate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> BindingOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_bindings WHERE id=? AND datapoint_id=?",
        (str(binding_id), str(dp_id)),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding nicht gefunden")

    updates = body.model_dump(exclude_none=True)
    now = datetime.now(timezone.utc).isoformat()

    direction       = updates.get("direction", row["direction"])
    config_val      = json.dumps(updates.get("config", json.loads(row["config"])))
    enabled         = int(updates.get("enabled", bool(row["enabled"])))
    throttle_ms     = updates.get("send_throttle_ms", row["send_throttle_ms"])
    on_change       = int(updates.get("send_on_change", bool(row["send_on_change"])))
    min_delta       = updates.get("send_min_delta", row["send_min_delta"])
    min_delta_pct   = updates.get("send_min_delta_pct", row["send_min_delta_pct"])
    formula         = updates.get("value_formula", row["value_formula"]) or None

    # Formel validieren
    if formula:
        from opentws.core.formula import validate_formula
        err = validate_formula(formula)
        if err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültige Formel: {err}")

    await db.execute_and_commit(
        """UPDATE adapter_bindings
           SET direction=?, config=?, enabled=?,
               send_throttle_ms=?, send_on_change=?, send_min_delta=?, send_min_delta_pct=?,
               value_formula=?, updated_at=?
           WHERE id=?""",
        (direction, config_val, enabled,
         throttle_ms, on_change, min_delta, min_delta_pct,
         formula, now, str(binding_id)),
    )

    instance_id = row["adapter_instance_id"]
    if instance_id:
        await _reload_adapter_instance(instance_id, db)

    updated = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (str(binding_id),))
    name_map = await _get_instance_name_map(db)
    return _row_out(updated, name_map)


@router.delete("/{dp_id}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(
    dp_id: uuid.UUID,
    binding_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone(
        "SELECT adapter_instance_id FROM adapter_bindings WHERE id=? AND datapoint_id=?",
        (str(binding_id), str(dp_id)),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding nicht gefunden")

    instance_id = row["adapter_instance_id"]
    await db.execute_and_commit(
        "DELETE FROM adapter_bindings WHERE id=?", (str(binding_id),)
    )
    if instance_id:
        await _reload_adapter_instance(instance_id, db)
