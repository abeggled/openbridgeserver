"""Logic Engine API

GET    /api/v1/logic/node-types               list all node type definitions
GET    /api/v1/logic/graphs                   list all logic graphs
POST   /api/v1/logic/graphs                   create a new graph
POST   /api/v1/logic/graphs/import            import graph from JSON
GET    /api/v1/logic/graphs/{id}              get graph (with flow_data)
PUT    /api/v1/logic/graphs/{id}              full update (save canvas)
PATCH  /api/v1/logic/graphs/{id}             partial update (name/enabled)
DELETE /api/v1/logic/graphs/{id}              delete graph
POST   /api/v1/logic/graphs/{id}/run          manually trigger execution
POST   /api/v1/logic/graphs/{id}/duplicate    duplicate graph with new node IDs
GET    /api/v1/logic/graphs/{id}/export       export graph as JSON download
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from obs.api.auth import Principal, get_admin_user, get_current_principal, get_current_user
from obs.api.authz import AuthzAction
from obs.api.authz_service import filter_authorized_datapoints
from obs.db.database import Database, get_db
from obs.logic.models import (
    FlowData,
    LogicEdge,
    LogicGraphCreate,
    LogicGraphImport,
    LogicGraphOut,
    LogicGraphUpdate,
    LogicNode,
    LogicUsageOut,
    NodeTypeDef,
)
from obs.logic.node_types import list_node_types

router = APIRouter(tags=["logic"])


def _row_to_out(row: dict) -> LogicGraphOut:
    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    return LogicGraphOut(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        enabled=bool(row["enabled"]),
        flow_data=FlowData.model_validate(raw),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _principal_from_dependency(value: Principal | str) -> Principal:
    if isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _flow_from_row(row: dict) -> FlowData:
    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    return FlowData.model_validate(raw)


def _logic_datapoint_ids(flow: FlowData) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for node in flow.nodes:
        if node.type not in {"datapoint_read", "datapoint_write"}:
            continue
        dp_id = node.data.get("datapoint_id")
        if not isinstance(dp_id, str) or not dp_id or dp_id in seen:
            continue
        seen.add(dp_id)
        ids.append(dp_id)
    return ids


async def _authorized_logic_datapoint_ids(
    db: Database,
    principal: Principal,
    row: dict,
    *,
    action: AuthzAction,
) -> tuple[list[str], list[str]]:
    all_ids = _logic_datapoint_ids(_flow_from_row(row))
    if principal.type == "user" and principal.is_admin:
        return all_ids, all_ids
    if not all_ids:
        return all_ids, []
    allowed_ids = await filter_authorized_datapoints(db, principal, all_ids, action=action)
    return all_ids, allowed_ids


async def _can_read_logic_graph(db: Database, principal: Principal, row: dict) -> bool:
    all_ids, allowed_ids = await _authorized_logic_datapoint_ids(db, principal, row, action=AuthzAction.READ)
    return len(allowed_ids) == len(all_ids) if all_ids else principal.type == "user" and principal.is_admin


async def _require_logic_graph_read(db: Database, principal: Principal, row: dict) -> None:
    if not await _can_read_logic_graph(db, principal, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")


async def _require_logic_graph_activation(db: Database, principal: Principal, row: dict) -> None:
    if principal.type == "user" and principal.is_admin:
        return
    all_ids, allowed_ids = await _authorized_logic_datapoint_ids(db, principal, row, action=AuthzAction.ACTIVATE)
    if not all_ids or len(allowed_ids) != len(all_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Zugriff verweigert")


@router.get("/node-types", response_model=list[NodeTypeDef])
async def get_node_types(_user: str = Depends(get_current_user)) -> list[NodeTypeDef]:
    return list_node_types()


@router.get("/graphs", response_model=list[LogicGraphOut])
async def list_graphs(
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> list[LogicGraphOut]:
    principal = _principal_from_dependency(_user)
    rows = await db.fetchall("SELECT * FROM logic_graphs ORDER BY name")
    readable_rows = [row for row in rows if await _can_read_logic_graph(db, principal, row)]
    return [_row_to_out(r) for r in readable_rows]


@router.post("/graphs", response_model=LogicGraphOut, status_code=status.HTTP_201_CREATED)
async def create_graph(
    body: LogicGraphCreate,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(UTC).isoformat()
    gid = str(uuid.uuid4())
    await db.execute_and_commit(
        """INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            gid,
            body.name,
            body.description,
            int(body.enabled),
            body.flow_data.model_dump_json(),
            now,
            now,
        ),
    )
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (gid,))
    # Load into executor cache so the graph is immediately runnable
    try:
        from obs.logic.manager import get_logic_manager

        await get_logic_manager().reload()
    except Exception:
        pass
    return _row_to_out(row)


@router.get("/graphs/{graph_id}", response_model=LogicGraphOut)
async def get_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)
    return _row_to_out(row)


@router.put("/graphs/{graph_id}", response_model=LogicGraphOut)
async def update_graph_full(
    graph_id: str,
    body: LogicGraphCreate,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(UTC).isoformat()
    row = await db.fetchone("SELECT id FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await db.execute_and_commit(
        """UPDATE logic_graphs
           SET name=?, description=?, enabled=?, flow_data=?, updated_at=?
           WHERE id=?""",
        (
            body.name,
            body.description,
            int(body.enabled),
            body.flow_data.model_dump_json(),
            now,
            graph_id,
        ),
    )
    # Invalidate executor cache
    try:
        from obs.logic.manager import get_logic_manager

        get_logic_manager().invalidate_cache(graph_id)
        await get_logic_manager().reload()
    except Exception:
        pass
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    return _row_to_out(row)


@router.patch("/graphs/{graph_id}", response_model=LogicGraphOut)
async def update_graph_partial(
    graph_id: str,
    body: LogicGraphUpdate,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(UTC).isoformat()
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    name = body.name if body.name is not None else row["name"]
    description = body.description if body.description is not None else row["description"]
    enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    if body.flow_data is not None:
        flow_json = body.flow_data.model_dump_json()
    else:
        flow_json = row["flow_data"]
    await db.execute_and_commit(
        """UPDATE logic_graphs
           SET name=?, description=?, enabled=?, flow_data=?, updated_at=?
           WHERE id=?""",
        (name, description, int(enabled), flow_json, now, graph_id),
    )
    try:
        from obs.logic.manager import get_logic_manager

        get_logic_manager().invalidate_cache(graph_id)
        await get_logic_manager().reload()
    except Exception:
        pass
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    return _row_to_out(row)


@router.delete("/graphs/{graph_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_graph(
    graph_id: str,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone("SELECT id FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await db.execute_and_commit("DELETE FROM logic_graphs WHERE id=?", (graph_id,))
    try:
        from obs.logic.manager import get_logic_manager

        get_logic_manager().invalidate_cache(graph_id)
    except Exception:
        pass


@router.post("/graphs/import", response_model=LogicGraphOut, status_code=status.HTTP_201_CREATED)
async def import_graph(
    body: LogicGraphImport,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    if body.obs_export != "logic_graph":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ungültiges Export-Format (erwartet 'logic_graph')",
        )

    known_types = {nt.type for nt in list_node_types()}

    # Unbekannte Node-Typen → missing_node Platzhalter
    # Bekannte Nodes: datapoint_name aus aktuellem Objektsystem holen
    try:
        from obs.core.registry import get_registry

        _registry = get_registry()
    except Exception:
        _registry = None

    processed_nodes: list[LogicNode] = []
    for node in body.flow_data.nodes:
        if node.type not in known_types and node.type != "missing_node":
            processed_nodes.append(
                LogicNode(
                    id=node.id,
                    type="missing_node",
                    position=node.position,
                    data={
                        "original_type": node.type,
                        "label": f"[Fehlend: {node.type}]",
                    },
                ),
            )
        else:
            if _registry is not None and "datapoint_id" in node.data:
                try:
                    dp = _registry.get(uuid.UUID(node.data["datapoint_id"]))
                    if dp is not None:
                        node.data["datapoint_name"] = dp.name
                except Exception:
                    pass
            processed_nodes.append(node)

    processed_flow = FlowData(nodes=processed_nodes, edges=body.flow_data.edges)

    now = datetime.now(UTC).isoformat()
    gid = str(uuid.uuid4())
    await db.execute_and_commit(
        """INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            gid,
            body.name,
            body.description,
            int(body.enabled),
            processed_flow.model_dump_json(),
            now,
            now,
        ),
    )
    try:
        from obs.logic.manager import get_logic_manager

        await get_logic_manager().reload()
    except Exception:
        pass
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (gid,))
    return _row_to_out(row)


@router.post("/graphs/{graph_id}/run", status_code=status.HTTP_200_OK)
async def run_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_activation(db, principal, row)
    if not bool(row["enabled"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logikblatt ist deaktiviert")
    try:
        from obs.logic.manager import get_logic_manager

        outputs = await get_logic_manager().execute_graph(graph_id)
        return {"status": "ok", "outputs": outputs}
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.post(
    "/graphs/{graph_id}/duplicate",
    response_model=LogicGraphOut,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_graph(
    graph_id: str,
    _user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")

    raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
    flow = FlowData.model_validate(raw)

    # Neue IDs für alle Nodes; Edges auf neue IDs umleiten
    id_map = {n.id: str(uuid.uuid4()) for n in flow.nodes}
    new_nodes = [n.model_copy(update={"id": id_map[n.id]}) for n in flow.nodes]
    new_edges = [
        LogicEdge(
            id=str(uuid.uuid4()),
            source=id_map.get(e.source, e.source),
            target=id_map.get(e.target, e.target),
            sourceHandle=e.sourceHandle,
            targetHandle=e.targetHandle,
        )
        for e in flow.edges
    ]
    new_flow = FlowData(nodes=new_nodes, edges=new_edges)

    now = datetime.now(UTC).isoformat()
    new_id = str(uuid.uuid4())
    new_name = f"Kopie von {row['name']}"
    await db.execute_and_commit(
        """INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            new_id,
            new_name,
            row["description"] or "",
            int(row["enabled"]),
            new_flow.model_dump_json(),
            now,
            now,
        ),
    )
    try:
        from obs.logic.manager import get_logic_manager

        await get_logic_manager().reload()
    except Exception:
        pass
    result = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (new_id,))
    return _row_to_out(result)


@router.get("/datapoint/{dp_id}/usages", response_model=list[LogicUsageOut])
async def get_datapoint_logic_usages(
    dp_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> list[LogicUsageOut]:
    """Return all logic graphs that reference a given DataPoint, with direction from the DP's perspective.

    - datapoint_read node  → logic reads the DP   → direction SOURCE
    - datapoint_write node → logic writes to the DP → direction DEST
    """
    principal = _principal_from_dependency(_user)
    if principal.type != "user" or not principal.is_admin:
        allowed = await filter_authorized_datapoints(db, principal, [dp_id], action=AuthzAction.READ)
        if not allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "DataPoint nicht gefunden")

    rows = await db.fetchall("SELECT id, name, enabled, flow_data FROM logic_graphs")
    usages: list[LogicUsageOut] = []
    for row in rows:
        raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
        flow = FlowData.model_validate(raw)
        for node in flow.nodes:
            if node.data.get("datapoint_id") != dp_id:
                continue
            if node.type == "datapoint_read":
                direction = "SOURCE"
            elif node.type == "datapoint_write":
                direction = "DEST"
            else:
                continue
            usages.append(
                LogicUsageOut(
                    graph_id=row["id"],
                    graph_name=row["name"],
                    graph_enabled=bool(row["enabled"]),
                    node_id=node.id,
                    node_type=node.type,
                    direction=direction,
                )
            )
    return usages


@router.get("/graphs/{graph_id}/export")
async def export_graph(
    graph_id: str,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> JSONResponse:
    principal = _principal_from_dependency(_user)
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await _require_logic_graph_read(db, principal, row)

    export_data = {
        "obs_export": "logic_graph",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "name": row["name"],
        "description": row["description"] or "",
        "enabled": bool(row["enabled"]),
        "flow_data": json.loads(row["flow_data"]) if row["flow_data"] else {"nodes": [], "edges": []},
    }
    safe_name = row["name"].replace(" ", "_").replace("/", "_")
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )
