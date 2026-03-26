"""
Config Export / Import — Phase 5

GET  /api/v1/config/export   → JSON dump aller DataPoints + Bindings + AdapterConfigs
POST /api/v1/config/import   ← JSON, upsert-Semantik (existierende IDs werden aktualisiert)
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
from opentws.models.datapoint import DataPoint, DataPointCreate
from opentws.models.binding import AdapterBinding

router = APIRouter(tags=["config"])

_EXPORT_VERSION = "1"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ExportedDataPoint(BaseModel):
    id: str
    name: str
    data_type: str
    unit: str | None
    tags: list[str]
    mqtt_alias: str | None


class ExportedBinding(BaseModel):
    id: str
    datapoint_id: str
    adapter_type: str
    direction: str
    config: dict
    enabled: bool


class ExportedAdapterConfig(BaseModel):
    adapter_type: str
    config: dict
    enabled: bool


class ConfigExport(BaseModel):
    opentws_version: str
    exported_at: str
    datapoints: list[ExportedDataPoint]
    bindings: list[ExportedBinding]
    adapter_configs: list[ExportedAdapterConfig]


class ImportResult(BaseModel):
    datapoints_created: int
    datapoints_updated: int
    bindings_created: int
    bindings_updated: int
    adapter_configs_upserted: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/export", response_model=ConfigExport)
async def export_config(
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> ConfigExport:
    reg = get_registry()
    all_dps = reg.all()

    datapoints = [
        ExportedDataPoint(
            id=str(dp.id),
            name=dp.name,
            data_type=dp.data_type,
            unit=dp.unit,
            tags=dp.tags,
            mqtt_alias=dp.mqtt_alias,
        )
        for dp in all_dps
    ]

    binding_rows = await db.fetchall(
        "SELECT * FROM adapter_bindings ORDER BY created_at"
    )
    bindings = [
        ExportedBinding(
            id=r["id"],
            datapoint_id=r["datapoint_id"],
            adapter_type=r["adapter_type"],
            direction=r["direction"],
            config=json.loads(r["config"]),
            enabled=bool(r["enabled"]),
        )
        for r in binding_rows
    ]

    config_rows = await db.fetchall("SELECT * FROM adapter_configs")
    adapter_configs = [
        ExportedAdapterConfig(
            adapter_type=r["adapter_type"],
            config=json.loads(r["config"]),
            enabled=bool(r["enabled"]),
        )
        for r in config_rows
    ]

    return ConfigExport(
        opentws_version=_EXPORT_VERSION,
        exported_at=datetime.now(timezone.utc).isoformat(),
        datapoints=datapoints,
        bindings=bindings,
        adapter_configs=adapter_configs,
    )


@router.post("/import", response_model=ImportResult, status_code=status.HTTP_200_OK)
async def import_config(
    body: ConfigExport,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> ImportResult:
    result = ImportResult(
        datapoints_created=0,
        datapoints_updated=0,
        bindings_created=0,
        bindings_updated=0,
        adapter_configs_upserted=0,
        errors=[],
    )
    reg = get_registry()
    now = datetime.now(timezone.utc).isoformat()

    # --- DataPoints ---
    for dp_data in body.datapoints:
        try:
            dp_id = uuid.UUID(dp_data.id)
            existing = reg.get(dp_id)
            if existing:
                from opentws.models.datapoint import DataPointUpdate
                await reg.update(dp_id, DataPointUpdate(
                    name=dp_data.name,
                    data_type=dp_data.data_type,
                    unit=dp_data.unit,
                    tags=dp_data.tags,
                    mqtt_alias=dp_data.mqtt_alias,
                ))
                result.datapoints_updated += 1
            else:
                # Insert with explicit ID
                dp = DataPoint(
                    id=dp_id,
                    name=dp_data.name,
                    data_type=dp_data.data_type,
                    unit=dp_data.unit,
                    tags=dp_data.tags,
                    mqtt_alias=dp_data.mqtt_alias,
                )
                await db.execute_and_commit(
                    """INSERT OR IGNORE INTO datapoints
                       (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (str(dp.id), dp.name, dp.data_type, dp.unit,
                     json.dumps(dp.tags), dp.mqtt_topic, dp.mqtt_alias, now, now),
                )
                from opentws.core.registry import ValueState
                reg._points[dp_id] = dp
                reg._values[dp_id] = ValueState()
                result.datapoints_created += 1
        except Exception as exc:
            result.errors.append(f"DataPoint {dp_data.id}: {exc}")

    # --- Bindings ---
    for b_data in body.bindings:
        try:
            b_id = b_data.id
            row = await db.fetchone(
                "SELECT id FROM adapter_bindings WHERE id=?", (b_id,)
            )
            if row:
                await db.execute_and_commit(
                    """UPDATE adapter_bindings
                       SET direction=?, config=?, enabled=?, updated_at=?
                       WHERE id=?""",
                    (b_data.direction, json.dumps(b_data.config), int(b_data.enabled), now, b_id),
                )
                result.bindings_updated += 1
            else:
                await db.execute_and_commit(
                    """INSERT INTO adapter_bindings
                       (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (b_id, b_data.datapoint_id, b_data.adapter_type, b_data.direction,
                     json.dumps(b_data.config), int(b_data.enabled), now, now),
                )
                result.bindings_created += 1
        except Exception as exc:
            result.errors.append(f"Binding {b_data.id}: {exc}")

    # --- Adapter Configs ---
    for ac in body.adapter_configs:
        try:
            await db.execute_and_commit(
                """INSERT INTO adapter_configs (adapter_type, config, enabled, updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(adapter_type) DO UPDATE
                   SET config=excluded.config, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (ac.adapter_type, json.dumps(ac.config), int(ac.enabled), now),
            )
            result.adapter_configs_upserted += 1
        except Exception as exc:
            result.errors.append(f"AdapterConfig {ac.adapter_type}: {exc}")

    return result
