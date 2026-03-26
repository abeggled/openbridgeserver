"""
Bindings API — Phase 4

GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}

On create/update/delete the relevant adapter is notified to reload bindings.
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
    direction: str
    config: dict
    enabled: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_bindings_for_dp(db: Database, dp_id: uuid.UUID) -> list[BindingOut]:
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE datapoint_id=? ORDER BY created_at",
        (str(dp_id),),
    )
    return [_row_out(r) for r in rows]


async def _reload_adapter(adapter_type: str, db: Database) -> None:
    """Tell a running adapter to reload its bindings from DB."""
    from opentws.adapters.registry import get_instance, _row_to_binding
    instance = get_instance(adapter_type)
    if instance is None:
        return
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_type=? AND enabled=1",
        (adapter_type,),
    )
    bindings = [_row_to_binding(r) for r in rows]
    await instance.reload_bindings(bindings)


def _row_out(row: Any) -> BindingOut:
    return BindingOut(
        id=uuid.UUID(row["id"]),
        datapoint_id=uuid.UUID(row["datapoint_id"]),
        adapter_type=row["adapter_type"],
        direction=row["direction"],
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    # Validate binding config against adapter's binding_config_schema
    from opentws.adapters.registry import get_class
    cls = get_class(body.adapter_type)
    if cls is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown adapter_type '{body.adapter_type}'",
        )
    if hasattr(cls, "binding_config_schema"):
        try:
            cls.binding_config_schema(**body.config)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Invalid binding config: {exc}",
            ) from exc

    binding = AdapterBinding(datapoint_id=dp_id, **body.model_dump())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            str(binding.id), str(dp_id), binding.adapter_type, binding.direction,
            json.dumps(binding.config), int(binding.enabled), now, now,
        ),
    )
    await _reload_adapter(binding.adapter_type, db)

    row = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (str(binding.id),))
    return _row_out(row)


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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding not found")

    updates = body.model_dump(exclude_none=True)
    now = datetime.now(timezone.utc).isoformat()

    direction = updates.get("direction", row["direction"])
    config_val = json.dumps(updates.get("config", json.loads(row["config"])))
    enabled = int(updates.get("enabled", bool(row["enabled"])))

    await db.execute_and_commit(
        """UPDATE adapter_bindings
           SET direction=?, config=?, enabled=?, updated_at=?
           WHERE id=?""",
        (direction, config_val, enabled, now, str(binding_id)),
    )
    await _reload_adapter(row["adapter_type"], db)

    updated = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (str(binding_id),))
    return _row_out(updated)


@router.delete("/{dp_id}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(
    dp_id: uuid.UUID,
    binding_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone(
        "SELECT adapter_type FROM adapter_bindings WHERE id=? AND datapoint_id=?",
        (str(binding_id), str(dp_id)),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding not found")

    adapter_type = row["adapter_type"]
    await db.execute_and_commit(
        "DELETE FROM adapter_bindings WHERE id=?", (str(binding_id),)
    )
    await _reload_adapter(adapter_type, db)
