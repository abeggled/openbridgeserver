"""
Config Backup / Restore — Phase 5 (Multi-Instance)

GET  /api/v1/config/export   → JSON-Sicherung: DataPoints + Bindings + AdapterInstances + KNX-GAs
POST /api/v1/config/import   ← JSON, upsert-Semantik (existierende IDs werden aktualisiert)

Rückwärtskompatibel: Alter Export mit adapter_configs wird beim Import erkannt und migriert.
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

_EXPORT_VERSION = "3"


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
    adapter_instance_id: str | None = None
    direction: str
    config: dict
    enabled: bool
    value_formula: str | None = None
    send_throttle_ms: int | None = None
    send_on_change: bool = False
    send_min_delta: float | None = None
    send_min_delta_pct: float | None = None


class ExportedAdapterInstance(BaseModel):
    id: str
    adapter_type: str
    name: str
    config: dict
    enabled: bool


class ExportedKnxGroupAddress(BaseModel):
    address: str
    name: str
    description: str
    dpt: str | None


# Legacy (v1 export format)
class ExportedAdapterConfig(BaseModel):
    adapter_type: str
    config: dict
    enabled: bool


class ExportedLogicGraph(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    flow_data: dict


class ConfigExport(BaseModel):
    opentws_version: str
    exported_at: str
    datapoints: list[ExportedDataPoint]
    bindings: list[ExportedBinding]
    adapter_instances: list[ExportedAdapterInstance] = []
    knx_group_addresses: list[ExportedKnxGroupAddress] = []
    logic_graphs: list[ExportedLogicGraph] = []
    # Legacy field (v1) — ignoriert beim Import wenn adapter_instances vorhanden
    adapter_configs: list[ExportedAdapterConfig] = []


class ImportResult(BaseModel):
    datapoints_created: int
    datapoints_updated: int
    bindings_created: int
    bindings_updated: int
    adapter_instances_upserted: int
    knx_group_addresses_upserted: int
    logic_graphs_created: int
    logic_graphs_updated: int
    adapters_restarted: int
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
            adapter_instance_id=r["adapter_instance_id"],
            direction=r["direction"],
            config=json.loads(r["config"]),
            enabled=bool(r["enabled"]),
            value_formula=r["value_formula"],
            send_throttle_ms=r["send_throttle_ms"],
            send_on_change=bool(r["send_on_change"]),
            send_min_delta=r["send_min_delta"],
            send_min_delta_pct=r["send_min_delta_pct"],
        )
        for r in binding_rows
    ]

    instance_rows = await db.fetchall("SELECT * FROM adapter_instances ORDER BY adapter_type, name")
    adapter_instances = [
        ExportedAdapterInstance(
            id=r["id"],
            adapter_type=r["adapter_type"],
            name=r["name"],
            config=json.loads(r["config"]) if r["config"] else {},
            enabled=bool(r["enabled"]),
        )
        for r in instance_rows
    ]

    ga_rows = await db.fetchall(
        "SELECT address, name, description, dpt FROM knx_group_addresses ORDER BY address"
    )
    knx_group_addresses = [
        ExportedKnxGroupAddress(
            address=r["address"],
            name=r["name"],
            description=r["description"],
            dpt=r["dpt"],
        )
        for r in ga_rows
    ]

    graph_rows = await db.fetchall("SELECT * FROM logic_graphs ORDER BY name")
    logic_graphs = [
        ExportedLogicGraph(
            id=r["id"],
            name=r["name"],
            description=r["description"] or "",
            enabled=bool(r["enabled"]),
            flow_data=json.loads(r["flow_data"]) if r["flow_data"] else {"nodes": [], "edges": []},
        )
        for r in graph_rows
    ]

    return ConfigExport(
        opentws_version=_EXPORT_VERSION,
        exported_at=datetime.now(timezone.utc).isoformat(),
        datapoints=datapoints,
        bindings=bindings,
        adapter_instances=adapter_instances,
        knx_group_addresses=knx_group_addresses,
        logic_graphs=logic_graphs,
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
        adapter_instances_upserted=0,
        knx_group_addresses_upserted=0,
        logic_graphs_created=0,
        logic_graphs_updated=0,
        adapters_restarted=0,
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

    # --- Adapter Instances ---
    # Quelle: adapter_instances (v2) oder adapter_configs (v1 legacy)
    instances_to_upsert = body.adapter_instances
    if not instances_to_upsert and body.adapter_configs:
        # Legacy v1: adapter_configs → neue Instanzen mit neuer UUID
        for ac in body.adapter_configs:
            instances_to_upsert.append(ExportedAdapterInstance(
                id=str(uuid.uuid4()),
                adapter_type=ac.adapter_type,
                name=ac.adapter_type,
                config=ac.config,
                enabled=ac.enabled,
            ))

    for ai in instances_to_upsert:
        try:
            await db.execute_and_commit(
                """INSERT INTO adapter_instances
                   (id, adapter_type, name, config, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE
                   SET name=excluded.name, config=excluded.config,
                       enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (ai.id, ai.adapter_type, ai.name,
                 json.dumps(ai.config), int(ai.enabled), now, now),
            )
            result.adapter_instances_upserted += 1
        except Exception as exc:
            result.errors.append(f"AdapterInstance {ai.id}: {exc}")

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
                       SET direction=?, config=?, enabled=?,
                           value_formula=?, send_throttle_ms=?, send_on_change=?,
                           send_min_delta=?, send_min_delta_pct=?,
                           updated_at=?
                       WHERE id=?""",
                    (b_data.direction, json.dumps(b_data.config), int(b_data.enabled),
                     b_data.value_formula, b_data.send_throttle_ms, int(b_data.send_on_change),
                     b_data.send_min_delta, b_data.send_min_delta_pct,
                     now, b_id),
                )
                result.bindings_updated += 1
            else:
                await db.execute_and_commit(
                    """INSERT INTO adapter_bindings
                       (id, datapoint_id, adapter_type, adapter_instance_id,
                        direction, config, enabled,
                        value_formula, send_throttle_ms, send_on_change,
                        send_min_delta, send_min_delta_pct,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (b_id, b_data.datapoint_id, b_data.adapter_type,
                     b_data.adapter_instance_id, b_data.direction,
                     json.dumps(b_data.config), int(b_data.enabled),
                     b_data.value_formula, b_data.send_throttle_ms, int(b_data.send_on_change),
                     b_data.send_min_delta, b_data.send_min_delta_pct,
                     now, now),
                )
                result.bindings_created += 1
        except Exception as exc:
            result.errors.append(f"Binding {b_data.id}: {exc}")

    # --- KNX Group Addresses ---
    for ga in body.knx_group_addresses:
        try:
            await db.execute_and_commit(
                """INSERT INTO knx_group_addresses (address, name, description, dpt)
                   VALUES (?,?,?,?)
                   ON CONFLICT(address) DO UPDATE
                   SET name=excluded.name, description=excluded.description, dpt=excluded.dpt""",
                (ga.address, ga.name, ga.description, ga.dpt),
            )
            result.knx_group_addresses_upserted += 1
        except Exception as exc:
            result.errors.append(f"KNX GA {ga.address}: {exc}")

    # --- Logic Graphs ---
    for lg in body.logic_graphs:
        try:
            row = await db.fetchone("SELECT id FROM logic_graphs WHERE id=?", (lg.id,))
            flow_json = json.dumps(lg.flow_data)
            if row:
                await db.execute_and_commit(
                    """UPDATE logic_graphs
                       SET name=?, description=?, enabled=?, flow_data=?, updated_at=?
                       WHERE id=?""",
                    (lg.name, lg.description, int(lg.enabled), flow_json, now, lg.id),
                )
                result.logic_graphs_updated += 1
            else:
                await db.execute_and_commit(
                    """INSERT INTO logic_graphs
                       (id, name, description, enabled, flow_data, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (lg.id, lg.name, lg.description, int(lg.enabled), flow_json, now, now),
                )
                result.logic_graphs_created += 1
        except Exception as exc:
            result.errors.append(f"LogicGraph {lg.id}: {exc}")

    if body.logic_graphs:
        try:
            from opentws.logic.manager import get_logic_manager
            await get_logic_manager().reload()
        except Exception as exc:
            result.errors.append(f"Logic manager reload: {exc}")

    # Restart all adapter instances so they pick up new configs and bindings
    try:
        from opentws.core.event_bus import get_event_bus
        from opentws.adapters import registry as adapter_registry
        event_bus = get_event_bus()
        await adapter_registry.stop_all()
        await adapter_registry.start_all(event_bus, db)
        result.adapters_restarted = len(adapter_registry.get_all_instances())
    except Exception as exc:
        result.errors.append(f"Adapter restart failed: {exc}")

    return result
