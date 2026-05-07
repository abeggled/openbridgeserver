"""Config Backup / Restore — Phase 5 (Multi-Instance)

GET  /api/v1/config/export        → JSON-Sicherung: DataPoints + Bindings + AdapterInstances + KNX-GAs + Visu + NavLinks + AppSettings + Hierarchy
POST /api/v1/config/import        ← JSON, upsert-Semantik (existierende IDs werden aktualisiert)
POST /api/v1/config/import/db     ← SQLite-Datei hochladen und als neue Datenbank einspielen

Rückwärtskompatibel: Alter Export mit adapter_configs wird beim Import erkannt und migriert.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from obs.api.auth import get_admin_user, get_current_user
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.models.datapoint import DataPoint

router = APIRouter(tags=["config"])

_EXPORT_VERSION = "5"


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


class ExportedIcon(BaseModel):
    name: str  # Stem ohne .svg, z.B. "abacus-solid"
    content_b64: str  # base64-kodierter SVG-Inhalt


class ExportedVisuNode(BaseModel):
    id: str
    parent_id: str | None
    name: str
    type: str
    node_order: int
    icon: str | None
    access: str | None
    access_pin: str | None
    page_config: str | None
    users: list[str] = []


class ExportedNavLink(BaseModel):
    id: str
    label: str
    url: str
    icon: str
    sort_order: int
    open_new_tab: bool


class ExportedAppSetting(BaseModel):
    key: str
    value: str


class ExportedHierarchyTree(BaseModel):
    id: str
    name: str
    description: str


class ExportedHierarchyNode(BaseModel):
    id: str
    tree_id: str
    parent_id: str | None
    name: str
    description: str
    node_order: int
    icon: str | None


class ExportedHierarchyDpLink(BaseModel):
    id: str
    node_id: str
    datapoint_id: str


class ConfigExport(BaseModel):
    obs_version: str
    exported_at: str
    datapoints: list[ExportedDataPoint]
    bindings: list[ExportedBinding]
    adapter_instances: list[ExportedAdapterInstance] = []
    knx_group_addresses: list[ExportedKnxGroupAddress] = []
    logic_graphs: list[ExportedLogicGraph] = []
    # Legacy field (v1) — ignoriert beim Import wenn adapter_instances vorhanden
    adapter_configs: list[ExportedAdapterConfig] = []
    # Icons & FA-Key (ab Version 4)
    icons: list[ExportedIcon] = []
    fa_api_key: str | None = None
    # Visu, NavLinks, AppSettings, Hierarchy (ab Version 5)
    visu_nodes: list[ExportedVisuNode] = []
    nav_links: list[ExportedNavLink] = []
    app_settings: list[ExportedAppSetting] = []
    hierarchy_trees: list[ExportedHierarchyTree] = []
    hierarchy_nodes: list[ExportedHierarchyNode] = []
    hierarchy_dp_links: list[ExportedHierarchyDpLink] = []


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
    icons_imported: int = 0
    visu_nodes_upserted: int = 0
    nav_links_upserted: int = 0
    app_settings_upserted: int = 0
    hierarchy_upserted: int = 0
    errors: list[str]


class ResetResult(BaseModel):
    datapoints_deleted: int
    bindings_deleted: int
    adapter_instances_deleted: int
    knx_group_addresses_deleted: int
    logic_graphs_deleted: int
    icons_deleted: int = 0
    visu_nodes_deleted: int = 0
    nav_links_deleted: int = 0
    hierarchy_deleted: int = 0
    errors: list[str]


class ClearResult(BaseModel):
    deleted: int
    bindings_deleted: int = 0
    errors: list[str] = []


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

    binding_rows = await db.fetchall("SELECT * FROM adapter_bindings ORDER BY created_at")
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

    ga_rows = await db.fetchall("SELECT address, name, description, dpt FROM knx_group_addresses ORDER BY address")
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

    # Icons — alle SVG-Dateien als base64
    from obs.api.v1.icons import _icons_dir

    icons: list[ExportedIcon] = []
    try:
        for svg_file in sorted(_icons_dir().glob("*.svg")):
            try:
                icons.append(
                    ExportedIcon(
                        name=svg_file.stem,
                        content_b64=base64.b64encode(svg_file.read_bytes()).decode(),
                    ),
                )
            except OSError:
                pass
    except Exception:
        pass

    # FontAwesome API Key
    fa_key_row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'icons.fontawesome_api_key'")
    fa_api_key = fa_key_row["value"] if fa_key_row else None

    # Visu-Nodes (mit Benutzerzuordnungen)
    visu_node_rows = await db.fetchall("SELECT * FROM visu_nodes ORDER BY node_order, created_at")
    visu_node_user_rows = await db.fetchall("SELECT node_id, username FROM visu_node_users")
    node_users: dict[str, list[str]] = {}
    for r in visu_node_user_rows:
        node_users.setdefault(r["node_id"], []).append(r["username"])

    visu_nodes = [
        ExportedVisuNode(
            id=r["id"],
            parent_id=r["parent_id"],
            name=r["name"],
            type=r["type"],
            node_order=r["node_order"],
            icon=r["icon"],
            access=r["access"],
            access_pin=r["access_pin"],
            page_config=r["page_config"],
            users=node_users.get(r["id"], []),
        )
        for r in visu_node_rows
    ]

    # NavLinks
    nav_link_rows = await db.fetchall("SELECT * FROM nav_links ORDER BY sort_order, label")
    nav_links = [
        ExportedNavLink(
            id=r["id"],
            label=r["label"],
            url=r["url"],
            icon=r["icon"],
            sort_order=r["sort_order"],
            open_new_tab=bool(r["open_new_tab"]),
        )
        for r in nav_link_rows
    ]

    # App-Settings (alle außer FA-Key — der wird separat übergeben für Rückwärtskompatibilität)
    setting_rows = await db.fetchall("SELECT key, value FROM app_settings WHERE key != 'icons.fontawesome_api_key' ORDER BY key")
    app_settings = [ExportedAppSetting(key=r["key"], value=r["value"]) for r in setting_rows]

    # Hierarchy
    tree_rows = await db.fetchall("SELECT * FROM hierarchy_trees ORDER BY name")
    hierarchy_trees = [ExportedHierarchyTree(id=r["id"], name=r["name"], description=r["description"]) for r in tree_rows]

    h_node_rows = await db.fetchall("SELECT * FROM hierarchy_nodes ORDER BY node_order, created_at")
    hierarchy_nodes = [
        ExportedHierarchyNode(
            id=r["id"],
            tree_id=r["tree_id"],
            parent_id=r["parent_id"],
            name=r["name"],
            description=r["description"],
            node_order=r["node_order"],
            icon=r["icon"],
        )
        for r in h_node_rows
    ]

    dp_link_rows = await db.fetchall("SELECT * FROM hierarchy_datapoint_links")
    hierarchy_dp_links = [ExportedHierarchyDpLink(id=r["id"], node_id=r["node_id"], datapoint_id=r["datapoint_id"]) for r in dp_link_rows]

    return ConfigExport(
        obs_version=_EXPORT_VERSION,
        exported_at=datetime.now(UTC).isoformat(),
        datapoints=datapoints,
        bindings=bindings,
        adapter_instances=adapter_instances,
        knx_group_addresses=knx_group_addresses,
        logic_graphs=logic_graphs,
        icons=icons,
        fa_api_key=fa_api_key,
        visu_nodes=visu_nodes,
        nav_links=nav_links,
        app_settings=app_settings,
        hierarchy_trees=hierarchy_trees,
        hierarchy_nodes=hierarchy_nodes,
        hierarchy_dp_links=hierarchy_dp_links,
    )


@router.get("/export/db")
async def export_db(
    background_tasks: BackgroundTasks,
    _user: str = Depends(get_admin_user),
) -> FileResponse:
    """Erstellt eine konsistente SQLite-Sicherung via sqlite3.backup() und gibt sie als Datei zurück."""
    from obs.config import get_settings

    src_path = get_settings().database.path

    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="Datenbankdatei nicht gefunden.")

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(tmp.name)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception as exc:
        os.unlink(tmp.name)
        raise HTTPException(status_code=500, detail=f"Backup fehlgeschlagen: {exc}") from exc

    background_tasks.add_task(os.unlink, tmp.name)
    return FileResponse(
        path=tmp.name,
        media_type="application/octet-stream",
        filename="obs.sqlite",
    )


@router.post("/import/db", status_code=status.HTTP_200_OK)
async def import_db(
    file: UploadFile = File(...),
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    """SQLite-Datenbank aus hochgeladener Datei wiederherstellen.

    ACHTUNG: Alle aktuellen Daten werden durch den Inhalt der hochgeladenen Datei ersetzt.
    Adapter, Logik-Engine und Registry werden nach dem Restore neu gestartet.
    """
    from obs.config import get_settings

    dst_path = get_settings().database.path

    # Hochgeladene Datei in temporäre Datei speichern
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()

        # SQLite-Magic-Header prüfen (erste 16 Bytes: "SQLite format 3\000")
        if len(content) < 16 or not content.startswith(b"SQLite format 3\x00"):
            os.unlink(tmp.name)
            raise HTTPException(status_code=400, detail="Die hochgeladene Datei ist keine gültige SQLite-Datenbank.")

        # Adapter und Logic Engine stoppen
        try:
            from obs.adapters import registry as adapter_registry

            await adapter_registry.stop_all()
        except Exception:
            pass

        try:
            from obs.logic.manager import get_logic_manager

            await get_logic_manager().stop()
        except Exception:
            pass

        # Aiosqlite-Verbindung trennen
        await db.disconnect()

        # Restore via sqlite3.backup()
        try:
            src_conn = sqlite3.connect(tmp.name)
            dst_conn = sqlite3.connect(dst_path)
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Datenbankwiederherstellung fehlgeschlagen: {exc}") from exc

        # Verbindung wieder aufbauen (inkl. Migrationen)
        await db.connect()

        # Registry neu laden
        reg = get_registry()
        reg._points.clear()
        reg._values.clear()
        await reg.load_from_db()

        # Logic Engine neu starten
        try:
            from obs.logic.manager import get_logic_manager

            logic_mgr = get_logic_manager()
            await logic_mgr.start()
        except Exception:
            pass

        # Adapter neu starten
        adapters_restarted = 0
        try:
            from obs.adapters import registry as adapter_registry
            from obs.core.event_bus import get_event_bus

            event_bus = get_event_bus()
            await adapter_registry.start_all(event_bus, db)
            adapters_restarted = len(adapter_registry.get_all_instances())
        except Exception:
            pass

        return {"ok": True, "message": "Datenbankwiederherstellung erfolgreich.", "adapters_restarted": adapters_restarted}

    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


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
    now = datetime.now(UTC).isoformat()

    # --- DataPoints ---
    for dp_data in body.datapoints:
        try:
            dp_id = uuid.UUID(dp_data.id)
            existing = reg.get(dp_id)
            if existing:
                from obs.models.datapoint import DataPointUpdate

                await reg.update(
                    dp_id,
                    DataPointUpdate(
                        name=dp_data.name,
                        data_type=dp_data.data_type,
                        unit=dp_data.unit,
                        tags=dp_data.tags,
                        mqtt_alias=dp_data.mqtt_alias,
                    ),
                )
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
                    (
                        str(dp.id),
                        dp.name,
                        dp.data_type,
                        dp.unit,
                        json.dumps(dp.tags),
                        dp.mqtt_topic,
                        dp.mqtt_alias,
                        now,
                        now,
                    ),
                )
                from obs.core.registry import ValueState

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
            instances_to_upsert.append(
                ExportedAdapterInstance(
                    id=str(uuid.uuid4()),
                    adapter_type=ac.adapter_type,
                    name=ac.adapter_type,
                    config=ac.config,
                    enabled=ac.enabled,
                ),
            )

    for ai in instances_to_upsert:
        try:
            await db.execute_and_commit(
                """INSERT INTO adapter_instances
                   (id, adapter_type, name, config, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE
                   SET name=excluded.name, config=excluded.config,
                       enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (
                    ai.id,
                    ai.adapter_type,
                    ai.name,
                    json.dumps(ai.config),
                    int(ai.enabled),
                    now,
                    now,
                ),
            )
            result.adapter_instances_upserted += 1
        except Exception as exc:
            result.errors.append(f"AdapterInstance {ai.id}: {exc}")

    # --- Bindings ---
    for b_data in body.bindings:
        try:
            b_id = b_data.id
            row = await db.fetchone("SELECT id FROM adapter_bindings WHERE id=?", (b_id,))
            if row:
                await db.execute_and_commit(
                    """UPDATE adapter_bindings
                       SET direction=?, config=?, enabled=?,
                           value_formula=?, send_throttle_ms=?, send_on_change=?,
                           send_min_delta=?, send_min_delta_pct=?,
                           updated_at=?
                       WHERE id=?""",
                    (
                        b_data.direction,
                        json.dumps(b_data.config),
                        int(b_data.enabled),
                        b_data.value_formula,
                        b_data.send_throttle_ms,
                        int(b_data.send_on_change),
                        b_data.send_min_delta,
                        b_data.send_min_delta_pct,
                        now,
                        b_id,
                    ),
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
                    (
                        b_id,
                        b_data.datapoint_id,
                        b_data.adapter_type,
                        b_data.adapter_instance_id,
                        b_data.direction,
                        json.dumps(b_data.config),
                        int(b_data.enabled),
                        b_data.value_formula,
                        b_data.send_throttle_ms,
                        int(b_data.send_on_change),
                        b_data.send_min_delta,
                        b_data.send_min_delta_pct,
                        now,
                        now,
                    ),
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
                    (
                        lg.id,
                        lg.name,
                        lg.description,
                        int(lg.enabled),
                        flow_json,
                        now,
                        now,
                    ),
                )
                result.logic_graphs_created += 1
        except Exception as exc:
            result.errors.append(f"LogicGraph {lg.id}: {exc}")

    if body.logic_graphs:
        try:
            from obs.logic.manager import get_logic_manager

            await get_logic_manager().reload()
        except Exception as exc:
            result.errors.append(f"Logic manager reload: {exc}")

    # Restart all adapter instances so they pick up new configs and bindings
    try:
        from obs.adapters import registry as adapter_registry
        from obs.core.event_bus import get_event_bus

        event_bus = get_event_bus()
        await adapter_registry.stop_all()
        await adapter_registry.start_all(event_bus, db)
        result.adapters_restarted = len(adapter_registry.get_all_instances())
    except Exception as exc:
        result.errors.append(f"Adapter restart failed: {exc}")

    # --- FontAwesome API Key ---
    if body.fa_api_key:
        try:
            await db.execute_and_commit(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                ("icons.fontawesome_api_key", body.fa_api_key),
            )
        except Exception as exc:
            result.errors.append(f"FA API Key import failed: {exc}")

    # --- Icons ---
    if body.icons:
        from obs.api.v1.icons import _icons_dir, _is_svg, _safe_name

        icons_dir = _icons_dir()
        for icon in body.icons:
            try:
                raw = base64.b64decode(icon.content_b64)
                if not _is_svg(raw):
                    result.errors.append(f"Icon '{icon.name}': kein gültiges SVG, übersprungen")
                    continue
                safe = _safe_name(f"{icon.name}.svg")
                if not safe:
                    result.errors.append(f"Icon '{icon.name}': ungültiger Name, übersprungen")
                    continue
                (icons_dir / f"{safe}.svg").write_bytes(raw)
                result.icons_imported += 1
            except Exception as exc:
                result.errors.append(f"Icon '{icon.name}': {exc}")

    # --- Visu Nodes (topologisch sortiert: Eltern vor Kindern) ---
    if body.visu_nodes:
        inserted_ids: set[str] = set()
        remaining = list(body.visu_nodes)

        # Vorhandene IDs als bereits eingefügt markieren (damit parent_id-Referenzen korrekt aufgelöst werden)
        existing_rows = await db.fetchall("SELECT id FROM visu_nodes")
        for r in existing_rows:
            inserted_ids.add(r["id"])

        for _pass in range(len(remaining) + 1):
            if not remaining:
                break
            next_remaining = []
            for node in remaining:
                if node.parent_id is None or node.parent_id in inserted_ids:
                    try:
                        await db.execute_and_commit(
                            """INSERT INTO visu_nodes
                               (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(id) DO UPDATE
                               SET parent_id=excluded.parent_id, name=excluded.name, type=excluded.type,
                                   node_order=excluded.node_order, icon=excluded.icon, access=excluded.access,
                                   access_pin=excluded.access_pin, page_config=excluded.page_config, updated_at=excluded.updated_at""",
                            (
                                node.id,
                                node.parent_id,
                                node.name,
                                node.type,
                                node.node_order,
                                node.icon,
                                node.access,
                                node.access_pin,
                                node.page_config,
                                now,
                                now,
                            ),
                        )
                        inserted_ids.add(node.id)
                        result.visu_nodes_upserted += 1

                        # Benutzerzuordnungen
                        if node.users:
                            await db.execute_and_commit("DELETE FROM visu_node_users WHERE node_id=?", (node.id,))
                            for username in node.users:
                                try:
                                    await db.execute_and_commit(
                                        "INSERT OR IGNORE INTO visu_node_users (node_id, username) VALUES (?,?)",
                                        (node.id, username),
                                    )
                                except Exception:
                                    pass
                    except Exception as exc:
                        result.errors.append(f"VisuNode {node.id}: {exc}")
                        inserted_ids.add(node.id)
                else:
                    next_remaining.append(node)
            remaining = next_remaining

        for node in remaining:
            result.errors.append(f"VisuNode {node.id}: parent_id '{node.parent_id}' nicht gefunden, übersprungen")

    # --- NavLinks ---
    for nl in body.nav_links:
        try:
            await db.execute_and_commit(
                """INSERT INTO nav_links (id, label, url, icon, sort_order, open_new_tab, created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE
                   SET label=excluded.label, url=excluded.url, icon=excluded.icon,
                       sort_order=excluded.sort_order, open_new_tab=excluded.open_new_tab""",
                (nl.id, nl.label, nl.url, nl.icon, nl.sort_order, int(nl.open_new_tab), now),
            )
            result.nav_links_upserted += 1
        except Exception as exc:
            result.errors.append(f"NavLink {nl.id}: {exc}")

    # --- App Settings ---
    for s in body.app_settings:
        try:
            await db.execute_and_commit(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?,?)",
                (s.key, s.value),
            )
            result.app_settings_upserted += 1
        except Exception as exc:
            result.errors.append(f"AppSetting {s.key}: {exc}")

    # --- Hierarchy Trees ---
    for ht in body.hierarchy_trees:
        try:
            await db.execute_and_commit(
                """INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE
                   SET name=excluded.name, description=excluded.description, updated_at=excluded.updated_at""",
                (ht.id, ht.name, ht.description, now, now),
            )
            result.hierarchy_upserted += 1
        except Exception as exc:
            result.errors.append(f"HierarchyTree {ht.id}: {exc}")

    # --- Hierarchy Nodes (topologisch sortiert) ---
    if body.hierarchy_nodes:
        inserted_h_ids: set[str] = set()
        existing_h = await db.fetchall("SELECT id FROM hierarchy_nodes")
        for r in existing_h:
            inserted_h_ids.add(r["id"])

        remaining_h = list(body.hierarchy_nodes)
        for _pass in range(len(remaining_h) + 1):
            if not remaining_h:
                break
            next_remaining_h = []
            for hn in remaining_h:
                if hn.parent_id is None or hn.parent_id in inserted_h_ids:
                    try:
                        await db.execute_and_commit(
                            """INSERT INTO hierarchy_nodes
                               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(id) DO UPDATE
                               SET tree_id=excluded.tree_id, parent_id=excluded.parent_id, name=excluded.name,
                                   description=excluded.description, node_order=excluded.node_order,
                                   icon=excluded.icon, updated_at=excluded.updated_at""",
                            (hn.id, hn.tree_id, hn.parent_id, hn.name, hn.description, hn.node_order, hn.icon, now, now),
                        )
                        inserted_h_ids.add(hn.id)
                        result.hierarchy_upserted += 1
                    except Exception as exc:
                        result.errors.append(f"HierarchyNode {hn.id}: {exc}")
                        inserted_h_ids.add(hn.id)
                else:
                    next_remaining_h.append(hn)
            remaining_h = next_remaining_h

    # --- Hierarchy DataPoint Links ---
    for link in body.hierarchy_dp_links:
        try:
            await db.execute_and_commit(
                """INSERT OR IGNORE INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
                   VALUES (?,?,?,?)""",
                (link.id, link.node_id, link.datapoint_id, now),
            )
            result.hierarchy_upserted += 1
        except Exception as exc:
            result.errors.append(f"HierarchyDpLink {link.id}: {exc}")

    return result


@router.delete("/reset", response_model=ResetResult, status_code=status.HTTP_200_OK)
async def factory_reset(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ResetResult:
    """Factory reset — deletes ALL data. Admin only."""
    result = ResetResult(
        datapoints_deleted=0,
        bindings_deleted=0,
        adapter_instances_deleted=0,
        knx_group_addresses_deleted=0,
        logic_graphs_deleted=0,
        errors=[],
    )

    try:
        from obs.adapters import registry as adapter_registry

        await adapter_registry.stop_all()
    except Exception as exc:
        result.errors.append(f"Adapter stop failed: {exc}")

    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM logic_graphs")
        result.logic_graphs_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM logic_graphs")
        from obs.logic.manager import get_logic_manager

        await get_logic_manager().reload()
    except Exception as exc:
        result.errors.append(f"Logic graphs reset failed: {exc}")

    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_bindings")
        result.bindings_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_bindings")
    except Exception as exc:
        result.errors.append(f"Bindings reset failed: {exc}")

    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM datapoints")
        result.datapoints_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM datapoints")
        reg = get_registry()
        reg._points.clear()
        reg._values.clear()
    except Exception as exc:
        result.errors.append(f"DataPoints reset failed: {exc}")

    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_instances")
        result.adapter_instances_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_instances")
    except Exception as exc:
        result.errors.append(f"Adapter instances reset failed: {exc}")

    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM knx_group_addresses")
        result.knx_group_addresses_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM knx_group_addresses")
    except Exception as exc:
        result.errors.append(f"KNX group addresses reset failed: {exc}")

    # Visu-Nodes löschen (Kinder werden durch CASCADE automatisch gelöscht)
    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM visu_nodes")
        result.visu_nodes_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM visu_nodes WHERE parent_id IS NULL")
    except Exception as exc:
        result.errors.append(f"Visu nodes reset failed: {exc}")

    # NavLinks löschen
    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM nav_links")
        result.nav_links_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM nav_links")
    except Exception as exc:
        result.errors.append(f"NavLinks reset failed: {exc}")

    # Hierarchy löschen
    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM hierarchy_trees")
        result.hierarchy_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM hierarchy_trees")
    except Exception as exc:
        result.errors.append(f"Hierarchy reset failed: {exc}")

    # App-Settings zurücksetzen (Autobackup-Einstellungen behalten, Standard-Timezone wiederherstellen)
    try:
        await db.execute_and_commit("DELETE FROM app_settings WHERE key NOT LIKE 'autobackup.%'")
        await db.execute_and_commit("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('timezone', 'Europe/Zurich')")
    except Exception as exc:
        result.errors.append(f"App settings reset failed: {exc}")

    # Icons (SVG-Dateien) löschen
    try:
        from obs.api.v1.icons import _icons_dir

        icons_dir = _icons_dir()
        for svg_file in list(icons_dir.glob("*.svg")):
            svg_file.unlink()
            result.icons_deleted += 1
    except Exception as exc:
        result.errors.append(f"Icons reset failed: {exc}")

    return result


@router.delete("/reset/bindings", response_model=ClearResult, status_code=status.HTTP_200_OK)
async def clear_bindings(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ClearResult:
    """Delete all Bindings and restart adapters so they pick up empty binding list. Admin only."""
    result = ClearResult(deleted=0)
    try:
        from obs.adapters import registry as adapter_registry
        from obs.core.event_bus import get_event_bus

        await adapter_registry.stop_all()
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_bindings")
        result.deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_bindings")
        await adapter_registry.start_all(get_event_bus(), db)
    except Exception as exc:
        result.errors.append(f"Bindings clear failed: {exc}")
    return result


@router.delete("/reset/datapoints", response_model=ClearResult, status_code=status.HTTP_200_OK)
async def clear_datapoints(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ClearResult:
    """Delete all DataPoints and their Bindings. Admin only."""
    result = ClearResult(deleted=0, bindings_deleted=0)
    try:
        from obs.adapters import registry as adapter_registry
        from obs.core.event_bus import get_event_bus

        await adapter_registry.stop_all()
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_bindings")
        result.bindings_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_bindings")
        row = await db.fetchone("SELECT COUNT(*) as n FROM datapoints")
        result.deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM datapoints")
        reg = get_registry()
        reg._points.clear()
        reg._values.clear()
        await adapter_registry.start_all(get_event_bus(), db)
    except Exception as exc:
        result.errors.append(f"DataPoints clear failed: {exc}")
    return result


@router.delete("/reset/logic", response_model=ClearResult, status_code=status.HTTP_200_OK)
async def clear_logic(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ClearResult:
    """Delete all Logic Graphs. Admin only."""
    result = ClearResult(deleted=0)
    try:
        row = await db.fetchone("SELECT COUNT(*) as n FROM logic_graphs")
        result.deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM logic_graphs")
        from obs.logic.manager import get_logic_manager

        await get_logic_manager().reload()
    except Exception as exc:
        result.errors.append(f"Logic graphs clear failed: {exc}")
    return result


@router.delete("/reset/adapters", response_model=ClearResult, status_code=status.HTTP_200_OK)
async def clear_adapters(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ClearResult:
    """Stop and delete all Adapter Instances and their Bindings. Admin only."""
    result = ClearResult(deleted=0, bindings_deleted=0)
    try:
        from obs.adapters import registry as adapter_registry

        await adapter_registry.stop_all()
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_bindings")
        result.bindings_deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_bindings")
        row = await db.fetchone("SELECT COUNT(*) as n FROM adapter_instances")
        result.deleted = row["n"] if row else 0
        await db.execute_and_commit("DELETE FROM adapter_instances")
    except Exception as exc:
        result.errors.append(f"Adapters clear failed: {exc}")
    return result
