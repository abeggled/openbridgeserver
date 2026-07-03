"""Shared KNX device/group-address/datapoint traceability helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel, Field

from obs.db.database import Database


class KnxTraceCommObjectOut(BaseModel):
    id: str
    number: str
    name: str
    datapoint_type: str
    ga_address: str | None = None


class KnxTraceDeviceOut(BaseModel):
    pa: str
    name: str
    manufacturer: str
    order_number: str
    app_ref: str
    comm_objects: list[KnxTraceCommObjectOut] = Field(default_factory=list)


class KnxTraceDatapointOut(BaseModel):
    id: uuid.UUID
    name: str
    data_type: str
    unit: str | None = None
    binding_id: uuid.UUID
    direction: str
    enabled: bool
    adapter_instance_id: uuid.UUID | None = None
    instance_name: str | None = None
    ga_address: str
    ga_role: str


class KnxTraceGroupAddressOut(BaseModel):
    address: str
    name: str = ""
    description: str = ""
    dpt: str | None = None
    roles: list[str] = Field(default_factory=list)
    devices: list[KnxTraceDeviceOut] = Field(default_factory=list)
    datapoints: list[KnxTraceDatapointOut] = Field(default_factory=list)


class KnxDatapointContextOut(BaseModel):
    datapoint_id: uuid.UUID
    group_addresses: list[KnxTraceGroupAddressOut] = Field(default_factory=list)


class KnxDeviceCommObjectContextOut(BaseModel):
    id: str
    number: str
    name: str
    datapoint_type: str
    group_addresses: list[KnxTraceGroupAddressOut] = Field(default_factory=list)


class KnxDeviceDatapointsContextOut(BaseModel):
    pa: str
    datapoints: list[KnxTraceDatapointOut] = Field(default_factory=list)
    comm_objects: list[KnxDeviceCommObjectContextOut] = Field(default_factory=list)


def normalize_nonempty(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


async def table_columns(db: Database, table_name: str) -> set[str]:
    rows = await db.fetchall(f"PRAGMA table_info({table_name})")
    return {str(row["name"]) for row in rows if "name" in row.keys()}


async def knx_device_schema_ready(db: Database) -> bool:
    device_cols = await table_columns(db, "knx_devices")
    co_cols = await table_columns(db, "knx_comm_objects")
    link_cols = await table_columns(db, "knx_co_ga_links")
    return bool(device_cols and co_cols and link_cols)


async def resolve_device_pas_to_group_addresses(
    device_pas: list[str],
    db: Database,
) -> list[str]:
    """Resolve KNX physical addresses to group addresses via imported KNX project data."""
    normalized_pas = normalize_nonempty(device_pas)
    if not normalized_pas:
        return []

    co_cols = await table_columns(db, "knx_comm_objects")
    link_cols = await table_columns(db, "knx_co_ga_links")
    dev_cols = await table_columns(db, "knx_devices")

    ga_col = next((c for c in ("group_address", "ga_address", "ga", "address") if c in link_cols), None)
    if not ga_col:
        return []

    placeholders = ",".join("?" * len(normalized_pas))
    co_id_col = next((c for c in ("id", "comm_object_id", "communication_object_id", "co_id") if c in co_cols), None)
    link_co_col = next((c for c in ("comm_object_id", "communication_object_id", "co_id") if c in link_cols), None)

    co_pa_col = next(
        (c for c in ("physical_address", "device_physical_address", "device_pa", "pa", "address") if c in co_cols),
        None,
    )
    if co_id_col and link_co_col and co_pa_col:
        rows = await db.fetchall(
            f"""SELECT DISTINCT l.{ga_col} AS ga
                   FROM knx_comm_objects co
                   JOIN knx_co_ga_links l ON l.{link_co_col} = co.{co_id_col}
                  WHERE co.{co_pa_col} IN ({placeholders})""",
            tuple(normalized_pas),
        )
        resolved = normalize_nonempty([str(row["ga"]) for row in rows if row["ga"] is not None])
        if resolved:
            return resolved

    dev_id_col = next((c for c in ("id", "device_id") if c in dev_cols), None)
    dev_pa_col = next((c for c in ("individual_address", "physical_address", "pa", "address") if c in dev_cols), None)
    co_dev_id_col = next((c for c in ("device_id", "knx_device_id") if c in co_cols), None)
    if dev_id_col and dev_pa_col and co_id_col and link_co_col and co_dev_id_col:
        rows = await db.fetchall(
            f"""SELECT DISTINCT l.{ga_col} AS ga
                   FROM knx_devices d
                   JOIN knx_comm_objects co ON co.{co_dev_id_col} = d.{dev_id_col}
                   JOIN knx_co_ga_links l ON l.{link_co_col} = co.{co_id_col}
                  WHERE d.{dev_pa_col} IN ({placeholders})""",
            tuple(normalized_pas),
        )
        return normalize_nonempty([str(row["ga"]) for row in rows if row["ga"] is not None])

    return []


async def _group_address_metadata(group_addresses: list[str], db: Database) -> dict[str, dict[str, Any]]:
    normalized = normalize_nonempty(group_addresses)
    if not normalized:
        return {}
    placeholders = ",".join("?" * len(normalized))
    rows = await db.fetchall(
        f"""SELECT address, name, description, dpt
              FROM knx_group_addresses
             WHERE address IN ({placeholders})""",
        normalized,
    )
    result = {
        row["address"]: {
            "address": row["address"],
            "name": row["name"] or "",
            "description": row["description"] or "",
            "dpt": row["dpt"],
        }
        for row in rows
    }
    for ga in normalized:
        result.setdefault(ga, {"address": ga, "name": "", "description": "", "dpt": None})
    return result


async def _devices_by_group_address(group_addresses: list[str], db: Database) -> dict[str, list[KnxTraceDeviceOut]]:
    normalized = normalize_nonempty(group_addresses)
    if not normalized or not await knx_device_schema_ready(db):
        return {ga: [] for ga in normalized}

    placeholders = ",".join("?" * len(normalized))
    rows = await db.fetchall(
        f"""SELECT
                  l.ga_address,
                  d.individual_address AS pa,
                  d.name,
                  d.product_name AS manufacturer,
                  d.product_refid AS order_number,
                  d.hardware2program_refid AS app_ref,
                  co.id AS comm_object_id,
                  co.number,
                  co.name AS comm_object_name,
                  co.datapoint_type
             FROM knx_devices d
             JOIN knx_comm_objects co ON co.device_id = d.id
             JOIN knx_co_ga_links l ON l.comm_object_id = co.id
            WHERE l.ga_address IN ({placeholders})
            ORDER BY l.ga_address, d.individual_address, co.number, co.id""",
        normalized,
    )

    by_ga: dict[str, dict[str, KnxTraceDeviceOut]] = {ga: {} for ga in normalized}
    seen_cos: set[tuple[str, str, str]] = set()
    for row in rows:
        ga = row["ga_address"]
        pa = row["pa"]
        device = by_ga.setdefault(ga, {}).get(pa)
        if device is None:
            device = KnxTraceDeviceOut(
                pa=pa,
                name=row["name"] or "",
                manufacturer=row["manufacturer"] or "",
                order_number=row["order_number"] or "",
                app_ref=row["app_ref"] or "",
                comm_objects=[],
            )
            by_ga[ga][pa] = device
        co_key = (ga, pa, row["comm_object_id"])
        if co_key in seen_cos:
            continue
        seen_cos.add(co_key)
        device.comm_objects.append(
            KnxTraceCommObjectOut(
                id=row["comm_object_id"],
                number=row["number"] or "",
                name=row["comm_object_name"] or "",
                datapoint_type=row["datapoint_type"] or "",
                ga_address=ga,
            )
        )

    return {ga: list(devices.values()) for ga, devices in by_ga.items()}


def _extract_knx_ga_roles(config: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = [
        ("group_address", str(config.get("group_address") or "").strip()),
        ("state_group_address", str(config.get("state_group_address") or "").strip()),
    ]
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for role, ga in pairs:
        if not ga or (role, ga) in seen:
            continue
        seen.add((role, ga))
        out.append((role, ga))
    return out


async def _datapoints_by_group_address(group_addresses: list[str], db: Database) -> dict[str, list[KnxTraceDatapointOut]]:
    normalized = normalize_nonempty(group_addresses)
    if not normalized:
        return {}

    placeholders = ",".join("?" * len(normalized))
    rows = await db.fetchall(
        f"""SELECT
                  ab.id AS binding_id,
                  ab.datapoint_id,
                  dp.name AS datapoint_name,
                  dp.data_type,
                  dp.unit,
                  ab.direction,
                  ab.enabled,
                  ab.adapter_instance_id,
                  ai.name AS instance_name,
                  ab.config
             FROM adapter_bindings ab
             JOIN datapoints dp ON dp.id = ab.datapoint_id
             LEFT JOIN adapter_instances ai ON ai.id = ab.adapter_instance_id
            WHERE UPPER(ab.adapter_type) = 'KNX'
              AND json_valid(ab.config)
              AND (
                    json_extract(ab.config, '$.group_address') IN ({placeholders})
                 OR json_extract(ab.config, '$.state_group_address') IN ({placeholders})
              )
            ORDER BY dp.name, ab.created_at, ab.id""",
        [*normalized, *normalized],
    )

    by_ga: dict[str, list[KnxTraceDatapointOut]] = {ga: [] for ga in normalized}
    seen: set[tuple[str, str, str]] = set()
    wanted = set(normalized)
    for row in rows:
        try:
            config = json.loads(row["config"] or "{}")
        except json.JSONDecodeError:
            continue
        for role, ga in _extract_knx_ga_roles(config):
            if ga not in wanted:
                continue
            key = (row["binding_id"], role, ga)
            if key in seen:
                continue
            seen.add(key)
            instance_id = row["adapter_instance_id"]
            by_ga.setdefault(ga, []).append(
                KnxTraceDatapointOut(
                    id=uuid.UUID(row["datapoint_id"]),
                    name=row["datapoint_name"],
                    data_type=row["data_type"],
                    unit=row["unit"],
                    binding_id=uuid.UUID(row["binding_id"]),
                    direction=row["direction"],
                    enabled=bool(row["enabled"]),
                    adapter_instance_id=uuid.UUID(instance_id) if instance_id else None,
                    instance_name=row["instance_name"],
                    ga_address=ga,
                    ga_role=role,
                )
            )
    return by_ga


async def build_datapoint_knx_context(dp_id: uuid.UUID, db: Database) -> KnxDatapointContextOut:
    rows = await db.fetchall(
        """SELECT config
             FROM adapter_bindings
            WHERE datapoint_id = ?
              AND UPPER(adapter_type) = 'KNX'
            ORDER BY created_at, id""",
        (str(dp_id),),
    )

    roles_by_ga: dict[str, list[str]] = {}
    for row in rows:
        try:
            config = json.loads(row["config"] or "{}")
        except json.JSONDecodeError:
            continue
        for role, ga in _extract_knx_ga_roles(config):
            roles_by_ga.setdefault(ga, [])
            if role not in roles_by_ga[ga]:
                roles_by_ga[ga].append(role)

    group_addresses = list(roles_by_ga.keys())
    metadata = await _group_address_metadata(group_addresses, db)
    devices = await _devices_by_group_address(group_addresses, db)

    return KnxDatapointContextOut(
        datapoint_id=dp_id,
        group_addresses=[
            KnxTraceGroupAddressOut(
                **metadata[ga],
                roles=roles_by_ga[ga],
                devices=devices.get(ga, []),
            )
            for ga in group_addresses
        ],
    )


async def build_device_datapoints_context(pa: str, db: Database) -> KnxDeviceDatapointsContextOut:
    if not await knx_device_schema_ready(db):
        return KnxDeviceDatapointsContextOut(pa=pa)

    device_row = await db.fetchone("SELECT id, individual_address FROM knx_devices WHERE individual_address = ?", (pa,))
    if not device_row:
        return KnxDeviceDatapointsContextOut(pa=pa)

    co_rows = await db.fetchall(
        """SELECT
               co.id,
               co.number,
               co.name,
               co.datapoint_type,
               l.ga_address
           FROM knx_comm_objects co
           LEFT JOIN knx_co_ga_links l ON l.comm_object_id = co.id
           WHERE co.device_id = ?
           ORDER BY co.number, co.id, l.ga_address""",
        (device_row["id"],),
    )

    group_addresses = normalize_nonempty([str(row["ga_address"]) for row in co_rows if row["ga_address"]])
    metadata = await _group_address_metadata(group_addresses, db)
    datapoints = await _datapoints_by_group_address(group_addresses, db)

    by_co: dict[str, KnxDeviceCommObjectContextOut] = {}
    all_datapoints: dict[tuple[uuid.UUID, uuid.UUID, str, str], KnxTraceDatapointOut] = {}
    for row in co_rows:
        co_id = row["id"]
        if co_id not in by_co:
            by_co[co_id] = KnxDeviceCommObjectContextOut(
                id=co_id,
                number=row["number"] or "",
                name=row["name"] or "",
                datapoint_type=row["datapoint_type"] or "",
                group_addresses=[],
            )
        ga = row["ga_address"]
        if not ga:
            continue
        ga_datapoints = datapoints.get(ga, [])
        for dp in ga_datapoints:
            all_datapoints[(dp.id, dp.binding_id, dp.ga_role, dp.ga_address)] = dp
        by_co[co_id].group_addresses.append(
            KnxTraceGroupAddressOut(
                **metadata[ga],
                roles=[],
                datapoints=ga_datapoints,
            )
        )

    return KnxDeviceDatapointsContextOut(
        pa=device_row["individual_address"],
        datapoints=list(all_datapoints.values()),
        comm_objects=list(by_co.values()),
    )
