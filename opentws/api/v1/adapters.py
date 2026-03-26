"""
Adapters API — Phase 4

GET    /api/v1/adapters                  list all + status
GET    /api/v1/adapters/{type}/schema    Pydantic JSON schema (connection config)
GET    /api/v1/adapters/{type}/binding-schema  Pydantic JSON schema (binding config)
POST   /api/v1/adapters/{type}/test      test connection with given config
PATCH  /api/v1/adapters/{type}/config    update persistent config in DB
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.adapters import registry as adapter_registry
from opentws.db.database import get_db, Database

router = APIRouter(tags=["adapters"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class AdapterStatusOut(BaseModel):
    adapter_type: str
    registered: bool
    running: bool
    connected: bool


class AdapterConfigOut(BaseModel):
    adapter_type: str
    config: dict
    enabled: bool
    updated_at: str | None


class TestRequest(BaseModel):
    config: dict


class TestResult(BaseModel):
    success: bool
    detail: str


class ConfigPatch(BaseModel):
    config: dict
    enabled: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[AdapterStatusOut])
async def list_adapters(
    _user: str = Depends(get_current_user),
) -> list[AdapterStatusOut]:
    status_map = adapter_registry.get_status()
    return [
        AdapterStatusOut(adapter_type=k, **v)
        for k, v in status_map.items()
    ]


@router.get("/{adapter_type}/schema")
async def get_adapter_schema(
    adapter_type: str,
    _user: str = Depends(get_current_user),
) -> dict:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' not registered")
    schema = cls.config_schema.model_json_schema()
    schema["title"] = f"{adapter_type} Connection Config"
    return schema


@router.get("/{adapter_type}/binding-schema")
async def get_binding_schema(
    adapter_type: str,
    _user: str = Depends(get_current_user),
) -> dict:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' not registered")
    if not hasattr(cls, "binding_config_schema"):
        return {}
    schema = cls.binding_config_schema.model_json_schema()
    schema["title"] = f"{adapter_type} Binding Config"
    return schema


@router.post("/{adapter_type}/test", response_model=TestResult)
async def test_adapter(
    adapter_type: str,
    body: TestRequest,
    _user: str = Depends(get_current_user),
) -> TestResult:
    """
    Attempt to connect with the given config. Returns result without
    persisting or affecting the running adapter.
    """
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' not registered")

    # Validate config schema
    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        return TestResult(success=False, detail=f"Config validation error: {exc}")

    # Create a temporary instance and try to connect
    from opentws.core.event_bus import EventBus
    dummy_bus = EventBus()

    test_instance = cls(event_bus=dummy_bus, config=body.config)
    try:
        await test_instance.connect()
        connected = test_instance.connected
        await test_instance.disconnect()
        if connected:
            return TestResult(success=True, detail=f"Successfully connected to {adapter_type}")
        else:
            return TestResult(success=False, detail="Connection attempt failed")
    except Exception as exc:
        return TestResult(success=False, detail=str(exc))


@router.patch("/{adapter_type}/config", response_model=AdapterConfigOut)
async def update_adapter_config(
    adapter_type: str,
    body: ConfigPatch,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterConfigOut:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' not registered")

    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Config validation error: {exc}",
        ) from exc

    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        """INSERT INTO adapter_configs (adapter_type, config, enabled, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(adapter_type) DO UPDATE
           SET config=excluded.config, enabled=excluded.enabled, updated_at=excluded.updated_at""",
        (adapter_type, json.dumps(body.config), int(body.enabled), now),
    )
    return AdapterConfigOut(
        adapter_type=adapter_type,
        config=body.config,
        enabled=body.enabled,
        updated_at=now,
    )


@router.get("/{adapter_type}/config", response_model=AdapterConfigOut)
async def get_adapter_config(
    adapter_type: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterConfigOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_configs WHERE adapter_type=?", (adapter_type,)
    )
    if row is None:
        return AdapterConfigOut(
            adapter_type=adapter_type, config={}, enabled=True, updated_at=None
        )
    return AdapterConfigOut(
        adapter_type=adapter_type,
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )
