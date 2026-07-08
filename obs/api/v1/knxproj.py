"""KNX Project Import API

POST /api/v1/knxproj/import          — .knxproj hochladen, GAs importieren
POST /api/v1/knxproj/import-csv      — ETS GA-CSV hochladen (optional: DataPoints+Bindings anlegen)
GET  /api/v1/knxproj/group-addresses — importierte GAs abfragen (Suche)
DELETE /api/v1/knxproj/group-addresses — alle GAs löschen
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from obs.api.auth import Principal, get_admin_user, get_current_principal, get_current_user
from obs.api.authz import AuthzAction
from obs.api.authz_service import filter_authorized_datapoints
from obs.api.v1.services.hierarchy_import import EtsImportRequest, create_ets_hierarchy
from obs.db.database import Database, get_db
from obs.knxproj.csv_parser import parse_ga_csv
from obs.knxproj.parser import (
    parse_knxproj,
    parse_knxproj_devices,
    parse_knxproj_locations,
    parse_knxproj_trades,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knxproj"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


_HIERARCHY_MODE_NAMES = {
    "groups": "ETS Gruppenadressen",
    "mid": "ETS Haupt- und Mittelgruppen",
    "flat": "ETS Gruppenadressen flach",
    "buildings": "ETS Gebäude und Räume",
    "trades": "ETS Gewerke",
}


class HierarchyImportResult(BaseModel):
    mode: str
    status: str
    tree_id: str | None = None
    tree_name: str | None = None
    nodes_created: int = 0
    links_created: int = 0
    trees_replaced: int = 0
    message: str


class ImportResult(BaseModel):
    imported: int
    created: int = 0
    updated: int = 0
    locations: int = 0
    functions: int = 0
    trades: int = 0
    hierarchies: list[HierarchyImportResult] = []
    message: str


class GroupAddressOut(BaseModel):
    address: str
    name: str
    description: str
    dpt: str | None
    imported_at: str


class GroupAddressPage(BaseModel):
    total: int
    items: list[GroupAddressOut]


class KnxCommObjectOut(BaseModel):
    id: str
    number: str
    name: str
    datapoint_type: str
    ga_addresses: list[str]


class KnxHierarchyLinkOut(BaseModel):
    tree_id: str
    tree_name: str
    node_id: str
    node_name: str
    node_path: list[str] = Field(default_factory=list)
    display_depth: int = 0


class KnxDeviceOut(BaseModel):
    pa: str
    name: str
    manufacturer: str
    order_number: str
    app_ref: str
    imported_at: str
    hierarchy_links: list[KnxHierarchyLinkOut] = Field(default_factory=list)


class KnxDeviceDetailOut(KnxDeviceOut):
    comm_objects: list[KnxCommObjectOut]


class KnxDeviceHierarchyLinksIn(BaseModel):
    node_ids: list[str] = Field(default_factory=list)


class KnxDevicePage(BaseModel):
    items: list[KnxDeviceOut]
    total: int
    page: int
    size: int
    pages: int


# ---------------------------------------------------------------------------
# Bulk DataPoint + Binding import helper
# ---------------------------------------------------------------------------


async def _bulk_import_datapoints(
    records: list[Any],
    adapter_name: str,
    direction: str,
    db: Database,
    now: str,
) -> tuple[int, int]:
    """Erstellt DataPoints + KNX-Bindings für alle records in einer DB-Transaktion.
    Bestehende Bindings (gleiche group_address + adapter_instance) werden aktualisiert.

    Returns: (created, updated)
    """
    from obs.adapters.knx.dpt_registry import DPTRegistry
    from obs.core.registry import ValueState, _row_to_datapoint, get_registry

    # --- Adapter-Instanz ermitteln ---
    instance_row = await db.fetchone(
        "SELECT id, adapter_type FROM adapter_instances WHERE name=?",
        (adapter_name,),
    )
    if not instance_row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Adapter-Instanz '{adapter_name}' nicht gefunden",
        )
    adapter_instance_id: str = instance_row["id"]
    adapter_type: str = instance_row["adapter_type"]

    # --- Bestehende Bindings laden (group_address → {binding_id, dp_id}) ---
    existing_rows = await db.fetchall(
        "SELECT id, datapoint_id, config FROM adapter_bindings WHERE adapter_instance_id=?",
        (adapter_instance_id,),
    )
    existing_map: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        try:
            cfg = json.loads(row["config"])
            ga = cfg.get("group_address")
            if ga:
                existing_map[ga] = {
                    "binding_id": row["id"],
                    "dp_id": row["datapoint_id"],
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # --- Batch-Listen aufbauen ---
    dp_inserts: list[tuple] = []
    binding_inserts: list[tuple] = []
    dp_updates: list[tuple] = []
    binding_updates: list[tuple] = []
    new_dp_ids: list[str] = []  # für Registry-Update

    base_time = datetime.fromisoformat(now)

    for row_idx, record in enumerate(records):
        # DPT → data_type + unit aus Registry
        dpt_def = DPTRegistry.get(record.dpt) if record.dpt else None
        if dpt_def and dpt_def.dpt_id != "UNKNOWN":
            data_type = dpt_def.data_type
            unit = dpt_def.unit or None
        else:
            data_type = "UNKNOWN"
            unit = None

        config_dict = {"group_address": record.address}
        if record.dpt:
            config_dict["dpt_id"] = record.dpt
        config_json = json.dumps(config_dict)

        # Jede Zeile bekommt einen eindeutigen Timestamp → CSV-Reihenfolge bleibt erhalten
        row_ts = (base_time + timedelta(microseconds=row_idx)).isoformat()

        if record.address in existing_map:
            existing = existing_map[record.address]
            dp_updates.append((record.name, data_type, unit, row_ts, existing["dp_id"]))
            binding_updates.append((config_json, direction, row_ts, existing["binding_id"]))
        else:
            dp_id = str(uuid_mod.uuid4())
            mqtt_topic = f"dp/{dp_id}/value"
            dp_inserts.append(
                (
                    dp_id,
                    record.name,
                    data_type,
                    unit,
                    "[]",
                    mqtt_topic,
                    None,
                    1,
                    row_ts,
                    row_ts,
                ),
            )

            binding_id = str(uuid_mod.uuid4())
            binding_inserts.append(
                (
                    binding_id,
                    dp_id,
                    adapter_type,
                    adapter_instance_id,
                    direction,
                    config_json,
                    1,
                    now,
                    now,
                ),
            )
            new_dp_ids.append(dp_id)

    # --- Alle DB-Operationen in einer Transaktion ---
    if dp_inserts:
        await db.executemany(
            """INSERT INTO datapoints
               (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            dp_inserts,
        )
    if binding_inserts:
        await db.executemany(
            """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, adapter_instance_id,
                direction, config, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            binding_inserts,
        )
    if dp_updates:
        await db.executemany(
            "UPDATE datapoints SET name=?, data_type=?, unit=?, updated_at=? WHERE id=?",
            dp_updates,
        )
    if binding_updates:
        await db.executemany(
            "UPDATE adapter_bindings SET config=?, direction=?, updated_at=? WHERE id=?",
            binding_updates,
        )
    await db.commit()

    # --- In-Memory Registry aktualisieren (neue + aktualisierte DataPoints) ---
    updated_dp_ids = [t[4] for t in dp_updates]  # tuple: (name, data_type, unit, ts, id)
    all_registry_ids = new_dp_ids + updated_dp_ids
    if all_registry_ids:
        try:
            reg = get_registry()
            rows = await db.fetchall(
                f"SELECT * FROM datapoints WHERE id IN ({','.join('?' * len(all_registry_ids))})",
                all_registry_ids,
            )
            for row in rows:
                dp = _row_to_datapoint(row)
                reg._points[dp.id] = dp
                if dp.id not in reg._values:
                    reg._values[dp.id] = ValueState()
        except Exception:
            pass  # Registry nicht verfügbar (z.B. in Tests) — kein Fehler

    # --- Adapter-Instanz neu laden ---
    try:
        from obs.adapters.registry import reload_instance_bindings

        await reload_instance_bindings(adapter_instance_id, db)
    except Exception:
        pass  # Adapter nicht geladen — kein Fehler

    return len(dp_inserts), len(dp_updates)


async def _knx_device_schema_ready(db: Database) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knx_devices'",
    )
    return row is not None


def _device_out_from_row(row: Any) -> KnxDeviceOut:
    return KnxDeviceOut(
        pa=row["pa"],
        name=row["name"] or "",
        manufacturer=row["manufacturer"] or "",
        order_number=row["order_number"] or "",
        app_ref=row["app_ref"] or "",
        imported_at=row["imported_at"],
    )


def _principal_from_dependency(value: Principal | str) -> Principal:
    if isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _is_admin_principal(principal: Principal) -> bool:
    return principal.type == "user" and principal.is_admin


def _parse_binding_group_addresses(config: str | None) -> list[str]:
    if not config:
        return []
    try:
        parsed = json.loads(config)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, dict):
        return []
    addresses: list[str] = []
    for key in ("group_address", "state_group_address"):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            addresses.append(value)
    return addresses


async def _authorized_knx_group_addresses(
    db: Database,
    principal: Principal,
    group_addresses: list[str],
) -> set[str]:
    ordered_addresses = list(dict.fromkeys(group_addresses))
    if not ordered_addresses:
        return set()
    if _is_admin_principal(principal):
        return set(ordered_addresses)

    rows = await db.fetchall(
        """SELECT ab.datapoint_id, ab.config
           FROM adapter_bindings ab
           LEFT JOIN adapter_instances ai ON ai.id = ab.adapter_instance_id
           WHERE ab.adapter_type = 'KNX'
             AND ab.enabled = 1
             AND (ab.adapter_instance_id IS NULL OR ai.enabled = 1)""",
    )
    datapoints_by_ga: dict[str, list[str]] = {ga: [] for ga in ordered_addresses}
    wanted = set(ordered_addresses)
    for row in rows:
        for ga in _parse_binding_group_addresses(row["config"]):
            if ga in wanted:
                datapoints_by_ga.setdefault(ga, []).append(row["datapoint_id"])

    candidate_dp_ids = list(dict.fromkeys(dp_id for dp_ids in datapoints_by_ga.values() for dp_id in dp_ids))
    allowed_dp_ids = set(
        await filter_authorized_datapoints(
            db,
            principal,
            candidate_dp_ids,
            action=AuthzAction.READ,
        )
    )
    return {ga for ga, dp_ids in datapoints_by_ga.items() if any(dp_id in allowed_dp_ids for dp_id in dp_ids)}


async def _authorized_knx_device_scope(
    db: Database,
    principal: Principal,
) -> tuple[set[str], set[str]]:
    rows = await db.fetchall(
        """SELECT DISTINCT co.device_id, l.ga_address
           FROM knx_comm_objects co
           JOIN knx_co_ga_links l ON l.comm_object_id = co.id
           WHERE l.ga_address IS NOT NULL""",
    )
    if not rows:
        return set(), set()

    allowed_ga_addresses = await _authorized_knx_group_addresses(
        db,
        principal,
        [row["ga_address"] for row in rows],
    )
    allowed_device_ids = {row["device_id"] for row in rows if row["ga_address"] in allowed_ga_addresses}
    return allowed_device_ids, allowed_ga_addresses


def _parse_hierarchy_node_filter(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    if not value:
        return []
    node_ids: list[str] = []
    seen: set[str] = set()
    for raw in value.split(","):
        node_id = raw.strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        node_ids.append(node_id)
    return node_ids


async def _load_device_hierarchy_links(db: Database, device_ids: list[str]) -> dict[str, list[KnxHierarchyLinkOut]]:
    if not device_ids:
        return {}
    placeholders = ",".join("?" * len(device_ids))
    rows = await db.fetchall(
        f"""SELECT
               hdl.device_id,
               hn.id AS node_id,
               hn.name AS node_name,
               ht.id AS tree_id,
               ht.name AS tree_name,
               ht.display_depth
           FROM hierarchy_device_links hdl
           JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
           JOIN hierarchy_trees ht ON ht.id = hn.tree_id
           WHERE hdl.device_id IN ({placeholders})
           ORDER BY ht.name, hn.name""",
        device_ids,
    )
    node_ids = list(dict.fromkeys(row["node_id"] for row in rows))
    node_paths: dict[str, list[str]] = {}
    if node_ids:
        path_placeholders = ",".join("?" * len(node_ids))
        path_rows = await db.fetchall(
            f"""WITH RECURSIVE anc(leaf_id, cur_id, cur_name, cur_parent, depth) AS (
                   SELECT id, id, name, parent_id, 0 FROM hierarchy_nodes WHERE id IN ({path_placeholders})
                   UNION ALL
                   SELECT a.leaf_id, hn2.id, hn2.name, hn2.parent_id, a.depth + 1
                   FROM anc a JOIN hierarchy_nodes hn2 ON hn2.id = a.cur_parent
                   WHERE a.cur_parent IS NOT NULL
               )
               SELECT leaf_id, cur_name
               FROM anc
               WHERE depth > 0
               ORDER BY leaf_id, depth DESC""",
            node_ids,
        )
        for row in path_rows:
            node_paths.setdefault(row["leaf_id"], []).append(row["cur_name"])

    out: dict[str, list[KnxHierarchyLinkOut]] = {device_id: [] for device_id in device_ids}
    for row in rows:
        out.setdefault(row["device_id"], []).append(
            KnxHierarchyLinkOut(
                tree_id=row["tree_id"],
                tree_name=row["tree_name"],
                node_id=row["node_id"],
                node_name=row["node_name"],
                node_path=node_paths.get(row["node_id"], []),
                display_depth=row["display_depth"] if row["display_depth"] is not None else 0,
            )
        )
    return out


def _with_hierarchy_links(device: KnxDeviceOut, links: list[KnxHierarchyLinkOut] | None) -> KnxDeviceOut:
    payload = device.model_dump()
    payload["hierarchy_links"] = links or []
    return KnxDeviceOut(**payload)


async def _import_knx_devices_and_comm_objects(
    *,
    file_bytes: bytes,
    password: str | None,
    db: Database,
    now: str,
) -> tuple[int, int]:
    """Import devices + comm objects from .knxproj into V34 schema.

    The import is intentionally tolerant: when device parsing fails we keep the
    existing GA/location/function/trade import path functional.
    """
    if not await _knx_device_schema_ready(db):
        return 0, 0

    # Offload the blocking XKNXProj parse (writes a temp file + parses the ZIP)
    # to a thread, like the GA/location/trade parsers, so large imports don't
    # stall the event loop.
    devices, comm_objects, co_ga_links = await run_in_threadpool(parse_knxproj_devices, file_bytes, password)

    await db.execute("SAVEPOINT knx_device_snapshot")
    try:
        existing_hierarchy_links = await db.fetchall("SELECT node_id, device_id FROM hierarchy_device_links")
        # Keep the latest project snapshot deterministic: tables mirror the current
        # imported .knxproj payload.
        await db.execute("DELETE FROM knx_co_ga_links")
        await db.execute("DELETE FROM knx_comm_objects")
        await db.execute("DELETE FROM knx_space_device_links")
        await db.execute("DELETE FROM knx_devices")

        if devices:
            await db.executemany(
                """INSERT INTO knx_devices
                       (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        d.identifier,
                        d.individual_address,
                        d.name,
                        d.description,
                        d.manufacturer_name,
                        d.order_number,
                        d.application or "",
                        now,
                    )
                    for d in devices
                    if d.identifier and d.individual_address
                ],
            )

            imported_device_ids = {d.identifier for d in devices if d.identifier and d.individual_address}
            preserved_hierarchy_links = [
                (str(uuid_mod.uuid4()), row["node_id"], row["device_id"], now)
                for row in existing_hierarchy_links
                if row["device_id"] in imported_device_ids
            ]
            if preserved_hierarchy_links:
                await db.executemany(
                    "INSERT OR IGNORE INTO hierarchy_device_links (id, node_id, device_id, created_at) VALUES (?, ?, ?, ?)",
                    preserved_hierarchy_links,
                )

            device_space_links = [(d.space_id, d.identifier) for d in devices if d.space_id and d.identifier and d.identifier in imported_device_ids]
            if device_space_links:
                space_ids = list(dict.fromkeys(space_id for space_id, _ in device_space_links if space_id))
                placeholders = ",".join("?" * len(space_ids))
                existing_space_rows = await db.fetchall(
                    f"SELECT id FROM knx_locations WHERE id IN ({placeholders})",
                    space_ids,
                )
                existing_space_ids = {row["id"] for row in existing_space_rows}
                space_link_rows = [(space_id, device_id) for space_id, device_id in device_space_links if space_id in existing_space_ids]
                if space_link_rows:
                    await db.executemany(
                        "INSERT OR IGNORE INTO knx_space_device_links (space_id, device_id) VALUES (?, ?)",
                        space_link_rows,
                    )

        device_id_by_pa = {d.individual_address: d.identifier for d in devices if d.individual_address and d.identifier}
        imported_comm_ids: set[str] = set()
        comm_rows: list[tuple[str, str, str, str, str, str, str, str]] = []
        for co in comm_objects:
            if not co.identifier:
                continue
            device_id = device_id_by_pa.get(co.device_address)
            if not device_id:
                continue
            imported_comm_ids.add(co.identifier)
            comm_rows.append(
                (
                    co.identifier,
                    device_id,
                    str(co.number),
                    co.name,
                    co.text,
                    co.function_text,
                    ",".join(co.dpts),
                    now,
                )
            )

        if comm_rows:
            await db.executemany(
                """INSERT INTO knx_comm_objects
                       (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                comm_rows,
            )

        link_rows: list[tuple[str, str]] = []
        for link in co_ga_links:
            if link.comm_object_id in imported_comm_ids and link.ga_address:
                link_rows.append((link.comm_object_id, link.ga_address))

        if link_rows:
            # Ensure FK target exists even if a downstream tool inserted links first.
            await db.executemany(
                "INSERT OR IGNORE INTO knx_group_addresses (address) VALUES (?)",
                [(ga,) for _, ga in link_rows],
            )
            await db.executemany(
                "INSERT OR IGNORE INTO knx_co_ga_links (comm_object_id, ga_address) VALUES (?, ?)",
                link_rows,
            )
        await db.execute("RELEASE SAVEPOINT knx_device_snapshot")
    except Exception:
        await db.execute("ROLLBACK TO SAVEPOINT knx_device_snapshot")
        await db.execute("RELEASE SAVEPOINT knx_device_snapshot")
        raise

    await db.commit()
    return len(devices), len(comm_rows)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _normalize_hierarchy_modes(raw_modes: list[str] | None) -> list[str]:
    if not raw_modes or not isinstance(raw_modes, list):
        return []

    modes: list[str] = []
    for raw in raw_modes:
        for part in raw.split(","):
            mode = part.strip().lower()
            if mode:
                modes.append(mode)

    invalid = sorted({mode for mode in modes if mode not in _HIERARCHY_MODE_NAMES})
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "hierarchy_modes muss einen oder mehrere dieser Werte enthalten: groups, mid, flat, buildings, trades",
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for mode in modes:
        if mode in seen:
            continue
        seen.add(mode)
        deduped.append(mode)
    return deduped


async def _create_requested_hierarchies(
    db: Database,
    modes: list[str],
    *,
    auto_link: bool,
    replace_existing: bool,
    group_addresses: list[str] | None = None,
    unavailable_messages: dict[str, str] | None = None,
) -> list[HierarchyImportResult]:
    results: list[HierarchyImportResult] = []
    unavailable_messages = unavailable_messages or {}
    for mode in modes:
        tree_name = _HIERARCHY_MODE_NAMES[mode]
        if mode in unavailable_messages:
            results.append(
                HierarchyImportResult(
                    mode=mode,
                    status="failed",
                    tree_name=tree_name,
                    message=unavailable_messages[mode],
                )
            )
            continue
        try:
            created = await create_ets_hierarchy(
                db,
                EtsImportRequest(
                    tree_name=tree_name,
                    mode=mode,
                    auto_link=auto_link,
                    replace_existing=replace_existing,
                    group_addresses=group_addresses if mode in ("groups", "mid", "flat") else None,
                ),
            )
        except HTTPException as exc:
            message = str(exc.detail)
            results.append(
                HierarchyImportResult(
                    mode=mode,
                    status="failed",
                    tree_name=tree_name,
                    message=message,
                )
            )
            continue
        except Exception as exc:
            logger.exception("ETS-Hierarchieimport fuer Modus '%s' fehlgeschlagen", mode)
            results.append(
                HierarchyImportResult(
                    mode=mode,
                    status="failed",
                    tree_name=tree_name,
                    message=f"Hierarchieimport fehlgeschlagen: {exc}",
                )
            )
            continue

        results.append(
            HierarchyImportResult(
                mode=mode,
                status="created",
                tree_id=created.tree_id,
                tree_name=created.tree_name,
                nodes_created=created.nodes_created,
                links_created=created.links_created,
                trees_replaced=created.trees_replaced,
                message=created.message,
            )
        )
    return results


@router.post("/import", response_model=ImportResult)
async def import_knxproj_file(
    file: UploadFile = File(...),
    password: str | None = Form(None),
    adapter_name: str | None = Query(
        None,
        description="Adapter-Instanzname — wenn angegeben, werden DataPoints und Bindings angelegt",
    ),
    direction: str = Query("SOURCE", pattern="^(SOURCE|DEST|BOTH)$", description="Verknüpfungsrichtung"),
    hierarchy_modes: list[str] | None = Query(
        None,
        description="Optional: ETS-Hierarchien direkt miterzeugen. Mehrfach oder kommasepariert: groups, mid, flat, buildings, trades",
    ),
    hierarchy_auto_link: bool = Query(
        True,
        description="DataPoints automatisch mit ETS-Gebäude-/Gewerke-Hierarchien verknüpfen, wenn adapter_name DataPoints/Bindings erzeugt",
    ),
    hierarchy_replace_existing: bool = Query(
        True,
        description="Bestehende automatisch erzeugte ETS-Hierarchien desselben Modus vor der Neuerzeugung ersetzen",
    ),
    _user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    """.knxproj Datei hochladen und Gruppenadressen in die DB importieren.
    Bestehende Einträge werden mit UPSERT-Semantik aktualisiert.

    Mit adapter_name: zusätzlich DataPoints + KNX-Bindings anlegen.
    persist_value wird beim Anlegen auf False gesetzt und beim Reimport nicht überschrieben.
    """
    if not file.filename or not file.filename.lower().endswith(".knxproj"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nur .knxproj Dateien werden akzeptiert",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei ist leer")

    requested_hierarchy_modes = _normalize_hierarchy_modes(hierarchy_modes)
    auto_link_requested = hierarchy_auto_link if isinstance(hierarchy_auto_link, bool) else True
    replace_existing_requested = hierarchy_replace_existing if isinstance(hierarchy_replace_existing, bool) else True
    pwd = password or None

    # Parse GAs and locations in parallel — large files can take 10+ s each,
    # parallel execution halves the wall time and keeps the event loop free.
    async def _safe_parse_locations() -> tuple:
        try:
            loc_records, fn_records = await run_in_threadpool(parse_knxproj_locations, content, pwd)
            return loc_records, fn_records, True
        except Exception as exc:
            logger.warning("Gebäude/Gewerke-Import fehlgeschlagen (wird ignoriert): %s", exc)
            return [], [], False

    try:
        records, (loc_records, fn_records, locations_parse_ok) = await asyncio.gather(
            run_in_threadpool(parse_knxproj, content, pwd),
            _safe_parse_locations(),
        )
    except ValueError as e:
        msg = str(e)
        code = "INVALID_PASSWORD" if ("passwort" in msg.lower() or "verschl" in msg.lower()) else "PARSE_ERROR"
        logger.warning("Fehler beim Parsen der .knxproj-Datei", exc_info=True)
        detail = (
            "Die .knxproj-Datei konnte nicht verarbeitet werden. Bitte prüfe Datei und Passwort."
            if code == "INVALID_PASSWORD"
            else "Die .knxproj-Datei konnte nicht verarbeitet werden."
        )
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": detail, "error_code": code})
    except Exception:
        logger.exception("Unerwarteter Fehler beim Parsen der .knxproj-Datei")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Unerwarteter Fehler beim Parsen.", "error_code": "PARSE_ERROR"},
        )

    if not records:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": (
                    "Keine Gruppenadressen gefunden. "
                    "Bitte prüfe ob du das richtige ETS-Projekt exportiert hast: "
                    "In ETS unter 'Datei → Speichern unter' oder 'Projekt exportieren'. "
                    "Eine Produktdatenbank (nur M-XXXX/ Ordner) enthält keine Gruppenadressen."
                ),
                "error_code": "NO_GROUP_ADDRESSES",
            },
        )

    now = datetime.now(UTC).isoformat()

    await db.executemany(
        """INSERT INTO knx_group_addresses
               (address, name, description, dpt, main_group_name, mid_group_name, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name            = excluded.name,
               description     = excluded.description,
               dpt             = excluded.dpt,
               main_group_name = excluded.main_group_name,
               mid_group_name  = excluded.mid_group_name,
               imported_at     = excluded.imported_at""",
        [(r.address, r.name, r.description, r.dpt, r.main_group_name, r.mid_group_name, now) for r in records],
    )
    await db.commit()

    # Import Gebäude/Gewerke structure — already parsed in parallel above
    locations_count = 0
    functions_count = 0
    try:
        if locations_parse_ok:
            await db.execute_and_commit("DELETE FROM knx_function_ga_links")
            await db.execute_and_commit("DELETE FROM knx_functions")
        if loc_records:
            await db.execute_and_commit("DELETE FROM knx_locations")
            await db.executemany(
                """INSERT INTO knx_locations (id, parent_id, name, space_type, sort_order, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(r.identifier, r.parent_id, r.name, r.space_type, r.sort_order, now) for r in loc_records],
            )
            await db.commit()
            locations_count = len(loc_records)

        if fn_records:
            await db.executemany(
                """INSERT INTO knx_functions (id, space_id, name, usage_text, imported_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [(r.identifier, r.space_id, r.name, r.usage_text, now) for r in fn_records],
            )
            ga_links = [(r.identifier, addr) for r in fn_records for addr in r.ga_addresses]
            if ga_links:
                await db.executemany(
                    "INSERT OR IGNORE INTO knx_function_ga_links (function_id, ga_address) VALUES (?, ?)",
                    ga_links,
                )
            await db.commit()
            functions_count = len(fn_records)
    except Exception as e:
        # Discard any partial inserts so they can't be made durable by a later commit.
        await db.rollback()
        logger.warning("Gebäude/Gewerke-Import fehlgeschlagen (wird ignoriert): %s", e)

    # Import Trades (Gewerke) — direct ZIP/XML parsing; password forwarded for protected files
    trades_count = 0
    try:
        trade_records = await run_in_threadpool(parse_knxproj_trades, content, pwd)
        if trade_records:
            await db.execute_and_commit("DELETE FROM knx_trades")
            await db.executemany(
                "INSERT INTO knx_trades (id, name, parent_id, sort_order, imported_at) VALUES (?, ?, ?, ?, ?)",
                [(r.identifier, r.name, r.parent_id, r.sort_order, now) for r in trade_records],
            )
            await db.commit()
            trades_count = len(trade_records)

            # Link functions to their trade:
            # Primary: XML DeviceInstanceRef.Links (exact function ID match)
            # Fallback: usage_text case-insensitive match against trade name
            fn_to_trade: dict[str, str] = {}
            for tr in trade_records:
                for fn_id in tr.function_ids:
                    fn_to_trade[fn_id] = tr.identifier

            if fn_to_trade:
                await db.executemany(
                    "UPDATE knx_functions SET trade_id = ? WHERE id = ?",
                    [(trade_id, fn_id) for fn_id, trade_id in fn_to_trade.items()],
                )
                await db.commit()
            else:
                # Fallback: match usage_text to trade name (works for German projects)
                trade_name_map = {tr.name.lower().strip(): tr.identifier for tr in trade_records}
                fn_rows = await db.fetchall("SELECT id, usage_text FROM knx_functions WHERE trade_id IS NULL")
                updates = []
                for fn in fn_rows:
                    usage = (fn["usage_text"] or "").lower().strip()
                    if usage and usage in trade_name_map:
                        updates.append((trade_name_map[usage], fn["id"]))
                if updates:
                    await db.executemany(
                        "UPDATE knx_functions SET trade_id = ? WHERE id = ?",
                        updates,
                    )
                    await db.commit()
    except Exception as e:
        # Discard any partial inserts so they can't be made durable by a later commit.
        await db.rollback()
        logger.warning("Trades-Import fehlgeschlagen (wird ignoriert): %s", e)

    # Import Device Model (V34/V35) — optional and backward compatible.
    # On failure roll back the partial device snapshot so the GA/location/trade
    # import path stays intact and the subsequent adapter import can still commit.
    try:
        await _import_knx_devices_and_comm_objects(file_bytes=content, password=pwd, db=db, now=now)
    except Exception as e:
        await db.rollback()
        logger.warning("KNX-Geräteimport fehlgeschlagen (wird ignoriert): %s", e)

    created = 0
    updated = 0
    if adapter_name:
        # Mit Adapter: DataPoints + Bindings bulk anlegen
        created, updated = await _bulk_import_datapoints(records, adapter_name, direction, db, now)

    hierarchy_results = await _create_requested_hierarchies(
        db,
        requested_hierarchy_modes,
        auto_link=auto_link_requested and bool(adapter_name),
        replace_existing=replace_existing_requested,
        group_addresses=[r.address for r in records],
        unavailable_messages={
            **(
                {"buildings": ("Keine Gebäude-Daten aus dieser .knxproj importiert. Der buildings-Hierarchieimport wurde übersprungen.")}
                if locations_count == 0
                else {}
            ),
            **(
                {"trades": ("Keine Gewerke-Daten aus dieser .knxproj importiert. Der trades-Hierarchieimport wurde übersprungen.")}
                if trades_count == 0
                else {}
            ),
        },
    )

    msg = f"{len(records)} Gruppenadressen importiert"
    extra = []
    if locations_count:
        extra.append(f"{locations_count} Räume/Gebäude")
    if trades_count:
        extra.append(f"{trades_count} Gewerke")
    if adapter_name:
        extra.append(f"{created} DataPoints neu erstellt")
        extra.append(f"{updated} aktualisiert")
    created_hierarchies = [result for result in hierarchy_results if result.status == "created"]
    failed_hierarchies = [result for result in hierarchy_results if result.status != "created"]
    if created_hierarchies:
        extra.append(f"{len(created_hierarchies)} Hierarchien erstellt")
    if failed_hierarchies:
        extra.append(f"{len(failed_hierarchies)} Hierarchien nicht erstellt")
    if extra:
        msg += ", " + ", ".join(extra)

    return ImportResult(
        imported=len(records),
        created=created,
        updated=updated,
        locations=locations_count,
        functions=functions_count,
        trades=trades_count,
        hierarchies=hierarchy_results,
        message=msg,
    )


@router.post("/import-csv", response_model=ImportResult)
async def import_ga_csv_file(
    file: UploadFile = File(...),
    adapter_name: str | None = Query(
        None,
        description="Adapter-Instanzname — wenn angegeben, werden DataPoints und Bindings angelegt",
    ),
    direction: str = Query("SOURCE", pattern="^(SOURCE|DEST|BOTH)$", description="Verknüpfungsrichtung"),
    _user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    """ETS GA-CSV hochladen.

    Ohne adapter_name: nur knx_group_addresses Tabelle befüllen (schnelle Vorschau).
    Mit adapter_name:  zusätzlich DataPoints + KNX-Bindings in einer Transaktion anlegen
                       (Bulk-Import, deutlich schneller als Einzelrequests).

    Bestehende DataPoints/Bindings für dieselbe Gruppenadresse werden aktualisiert.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nur .csv Dateien werden akzeptiert",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei ist leer")

    try:
        records = parse_ga_csv(content)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Unerwarteter Fehler beim Parsen: {e}",
        )

    if not records:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Keine Gruppenadressen gefunden. Bitte prüfe ob du den ETS GA-Export als CSV verwendet hast.",
        )

    now = datetime.now(UTC).isoformat()

    # GA-Tabelle immer befüllen (für Vorschau / manuelle Bindung im GUI)
    await db.executemany(
        """INSERT INTO knx_group_addresses
               (address, name, description, dpt, main_group_name, mid_group_name, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name            = excluded.name,
               description     = excluded.description,
               dpt             = excluded.dpt,
               main_group_name = excluded.main_group_name,
               mid_group_name  = excluded.mid_group_name,
               imported_at     = excluded.imported_at""",
        [(r.address, r.name, r.description, r.dpt, r.main_group_name, r.mid_group_name, now) for r in records],
    )
    await db.commit()

    # Ohne Adapter: nur GA-Tabelle → fertig
    if not adapter_name:
        return ImportResult(
            imported=len(records),
            message=f"{len(records)} Gruppenadressen importiert (ohne DataPoints — adapter_name fehlt)",
        )

    # Mit Adapter: DataPoints + Bindings bulk anlegen
    created, updated = await _bulk_import_datapoints(records, adapter_name, direction, db, now)

    return ImportResult(
        imported=created + updated,
        created=created,
        updated=updated,
        message=f"{created} DataPoints neu erstellt, {updated} aktualisiert",
    )


@router.get("/devices", response_model=KnxDevicePage)
async def list_knx_devices(
    q: str = Query("", description="Suche in PA, Name, Hersteller, Bestellnummer und App-Ref"),
    manufacturer: str = Query("", description="Hersteller (Teilstring, case-insensitive)"),
    order_number: str = Query("", description="Bestellnummer (Teilstring, case-insensitive)"),
    hierarchy_node_id: str = Query("", description="Kommagetrennte Hierarchie-Knoten-IDs zur Gerätefilterung"),
    room: str = Query("", description="Optionaler Raumfilter (noch ohne Wirkung)"),
    trade: str = Query("", description="Optionaler Gewerkefilter (noch ohne Wirkung)"),
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> KnxDevicePage:
    # room/trade are intentionally accepted already so the API surface stays
    # stable while deeper room/trade joins are introduced in follow-up issues.
    _ = room, trade

    if not await _knx_device_schema_ready(db):
        return KnxDevicePage(items=[], total=0, page=page, size=size, pages=1)

    principal = _principal_from_dependency(_user)
    where: list[str] = []
    params: list[Any] = []
    if not _is_admin_principal(principal):
        allowed_device_ids, _ = await _authorized_knx_device_scope(db, principal)
        if not allowed_device_ids:
            return KnxDevicePage(items=[], total=0, page=page, size=size, pages=1)
        where.append(f"id IN ({','.join('?' for _ in allowed_device_ids)})")
        params.extend(sorted(allowed_device_ids))

    if q:
        like = f"%{q.lower()}%"
        where.append(
            """(
                lower(individual_address) LIKE ?
                OR lower(name) LIKE ?
                OR lower(product_name) LIKE ?
                OR lower(product_refid) LIKE ?
                OR lower(hardware2program_refid) LIKE ?
            )"""
        )
        params.extend([like, like, like, like, like])
    if manufacturer:
        where.append("lower(product_name) LIKE ?")
        params.append(f"%{manufacturer.lower()}%")
    if order_number:
        where.append("lower(product_refid) LIKE ?")
        params.append(f"%{order_number.lower()}%")

    hierarchy_node_ids = _parse_hierarchy_node_filter(hierarchy_node_id)
    if hierarchy_node_ids:
        placeholders = ",".join("?" * len(hierarchy_node_ids))
        where.append(
            f"""id IN (
                WITH RECURSIVE selected_hierarchy_nodes(id) AS (
                    SELECT id
                    FROM hierarchy_nodes
                    WHERE id IN ({placeholders})
                    UNION ALL
                    SELECT child.id
                    FROM hierarchy_nodes child
                    JOIN selected_hierarchy_nodes selected ON child.parent_id = selected.id
                )
                SELECT hdl.device_id
                FROM hierarchy_device_links hdl
                JOIN selected_hierarchy_nodes selected ON selected.id = hdl.node_id
            )"""
        )
        params.extend(hierarchy_node_ids)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    count_row = await db.fetchone(
        f"SELECT COUNT(*) AS n FROM knx_devices {where_sql}",
        tuple(params),
    )
    total = count_row["n"] if count_row else 0

    rows = await db.fetchall(
        f"""SELECT
                id,
                individual_address AS pa,
                name,
                product_name AS manufacturer,
                product_refid AS order_number,
                hardware2program_refid AS app_ref,
                imported_at
            FROM knx_devices
            {where_sql}
            ORDER BY individual_address
            LIMIT ? OFFSET ?""",
        tuple([*params, size, page * size]),
    )
    pages = max(1, (total + size - 1) // size)
    device_ids = [row["id"] for row in rows]
    links_by_device_id = await _load_device_hierarchy_links(db, device_ids)
    return KnxDevicePage(
        items=[_with_hierarchy_links(_device_out_from_row(row), links_by_device_id.get(row["id"])) for row in rows],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/devices/{pa}", response_model=KnxDeviceDetailOut)
async def get_knx_device(
    pa: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> KnxDeviceDetailOut:
    if not await _knx_device_schema_ready(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"KNX Gerät {pa} nicht gefunden")

    principal = _principal_from_dependency(_user)
    allowed_ga_addresses: set[str] | None = None
    allowed_device_ids: set[str] | None = None
    if not _is_admin_principal(principal):
        allowed_device_ids, allowed_ga_addresses = await _authorized_knx_device_scope(db, principal)

    device_row = await db.fetchone(
        """SELECT
               id,
               individual_address AS pa,
               name,
               product_name AS manufacturer,
               product_refid AS order_number,
               hardware2program_refid AS app_ref,
               imported_at
           FROM knx_devices
           WHERE individual_address = ?""",
        (pa,),
    )
    if not device_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"KNX Gerät {pa} nicht gefunden")
    if allowed_device_ids is not None and device_row["id"] not in allowed_device_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"KNX Gerät {pa} nicht gefunden")

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

    by_id: dict[str, KnxCommObjectOut] = {}
    for row in co_rows:
        ga = row["ga_address"]
        if allowed_ga_addresses is not None and ga not in allowed_ga_addresses:
            continue
        co_id = row["id"]
        if co_id not in by_id:
            by_id[co_id] = KnxCommObjectOut(
                id=co_id,
                number=row["number"] or "",
                name=row["name"] or "",
                datapoint_type=row["datapoint_type"] or "",
                ga_addresses=[],
            )
        if ga:
            by_id[co_id].ga_addresses.append(ga)

    device = _device_out_from_row(device_row)
    links_by_device_id = await _load_device_hierarchy_links(db, [device_row["id"]])
    return KnxDeviceDetailOut(
        **_with_hierarchy_links(device, links_by_device_id.get(device_row["id"])).model_dump(),
        comm_objects=list(by_id.values()),
    )


@router.put("/devices/{pa}/hierarchy-links", response_model=KnxDeviceDetailOut)
async def set_knx_device_hierarchy_links(
    pa: str,
    body: KnxDeviceHierarchyLinksIn,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> KnxDeviceDetailOut:
    if not await _knx_device_schema_ready(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"KNX Gerät {pa} nicht gefunden")

    device_row = await db.fetchone("SELECT id FROM knx_devices WHERE individual_address = ?", (pa,))
    if not device_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"KNX Gerät {pa} nicht gefunden")

    node_ids = list(dict.fromkeys(str(node_id).strip() for node_id in body.node_ids if str(node_id).strip()))
    if node_ids:
        placeholders = ",".join("?" * len(node_ids))
        rows = await db.fetchall(f"SELECT id FROM hierarchy_nodes WHERE id IN ({placeholders})", node_ids)
        existing_node_ids = {row["id"] for row in rows}
        missing = [node_id for node_id in node_ids if node_id not in existing_node_ids]
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Hierarchie-Knoten nicht gefunden: {', '.join(missing)}")

    now = datetime.now(UTC).isoformat()
    await db.execute("DELETE FROM hierarchy_device_links WHERE device_id = ?", (device_row["id"],))
    if node_ids:
        await db.executemany(
            "INSERT INTO hierarchy_device_links (id, node_id, device_id, created_at) VALUES (?, ?, ?, ?)",
            [(str(uuid_mod.uuid4()), node_id, device_row["id"], now) for node_id in node_ids],
        )
    await db.commit()
    return await get_knx_device(pa=pa, _user=_user, db=db)


@router.get("/group-addresses/{ga:path}/devices", response_model=KnxDevicePage)
async def list_knx_devices_for_group_address(
    ga: str,
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(get_db),
) -> KnxDevicePage:
    if not await _knx_device_schema_ready(db):
        return KnxDevicePage(items=[], total=0, page=page, size=size, pages=1)

    principal = _principal_from_dependency(_user)
    if not _is_admin_principal(principal):
        allowed_addresses = await _authorized_knx_group_addresses(db, principal, [ga])
        if ga not in allowed_addresses:
            return KnxDevicePage(items=[], total=0, page=page, size=size, pages=1)

    count_row = await db.fetchone(
        """SELECT COUNT(DISTINCT d.id) AS n
           FROM knx_devices d
           JOIN knx_comm_objects co ON co.device_id = d.id
           JOIN knx_co_ga_links l ON l.comm_object_id = co.id
           WHERE l.ga_address = ?""",
        (ga,),
    )
    total = count_row["n"] if count_row else 0

    rows = await db.fetchall(
        """SELECT DISTINCT
               d.individual_address AS pa,
               d.name,
               d.product_name AS manufacturer,
               d.product_refid AS order_number,
               d.hardware2program_refid AS app_ref,
               d.imported_at
           FROM knx_devices d
           JOIN knx_comm_objects co ON co.device_id = d.id
           JOIN knx_co_ga_links l ON l.comm_object_id = co.id
           WHERE l.ga_address = ?
           ORDER BY d.individual_address
           LIMIT ? OFFSET ?""",
        (ga, size, page * size),
    )
    pages = max(1, (total + size - 1) // size)
    return KnxDevicePage(
        items=[_device_out_from_row(row) for row in rows],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/group-addresses", response_model=GroupAddressPage)
async def list_group_addresses(
    q: str = Query("", description="Suche in Adresse, Name oder Beschreibung"),
    page: int = Query(0, ge=0),
    size: int = Query(100, ge=1, le=500),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> GroupAddressPage:
    """Importierte KNX Gruppenadressen abfragen. Unterstützt Volltextsuche."""
    if q:
        like = f"%{q}%"
        rows = await db.fetchall(
            """SELECT address, name, description, dpt, imported_at
               FROM knx_group_addresses
               WHERE address LIKE ? OR name LIKE ? OR description LIKE ?
               ORDER BY address
               LIMIT ? OFFSET ?""",
            (like, like, like, size, page * size),
        )
        count_row = await db.fetchone(
            """SELECT COUNT(*) AS n FROM knx_group_addresses
               WHERE address LIKE ? OR name LIKE ? OR description LIKE ?""",
            (like, like, like),
        )
    else:
        rows = await db.fetchall(
            """SELECT address, name, description, dpt, imported_at
               FROM knx_group_addresses
               ORDER BY address
               LIMIT ? OFFSET ?""",
            (size, page * size),
        )
        count_row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM knx_group_addresses",
        )

    total = count_row["n"] if count_row else 0
    return GroupAddressPage(
        total=total,
        items=[GroupAddressOut(**dict(r)) for r in rows],
    )


@router.delete("/group-addresses", status_code=status.HTTP_204_NO_CONTENT)
async def clear_group_addresses(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> None:
    """Alle importierten KNX Gruppenadressen löschen."""
    await db.execute_and_commit("DELETE FROM knx_group_addresses")
