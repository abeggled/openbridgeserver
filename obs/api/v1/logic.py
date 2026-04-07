"""
Logic Engine API

GET    /api/v1/logic/node-types           list all node type definitions
GET    /api/v1/logic/graphs               list all logic graphs
POST   /api/v1/logic/graphs               create a new graph
GET    /api/v1/logic/graphs/{id}          get graph (with flow_data)
PUT    /api/v1/logic/graphs/{id}          full update (save canvas)
PATCH  /api/v1/logic/graphs/{id}          partial update (name/enabled)
DELETE /api/v1/logic/graphs/{id}          delete graph
POST   /api/v1/logic/graphs/{id}/run      manually trigger execution
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from obs.api.auth import get_current_user
from obs.db.database import get_db, Database
from obs.logic.models import (
    FlowData, LogicGraphCreate, LogicGraphOut, LogicGraphUpdate, NodeTypeDef
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


@router.get("/node-types", response_model=list[NodeTypeDef])
async def get_node_types(_user: str = Depends(get_current_user)) -> list[NodeTypeDef]:
    return list_node_types()


@router.get("/graphs", response_model=list[LogicGraphOut])
async def list_graphs(
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[LogicGraphOut]:
    rows = await db.fetchall("SELECT * FROM logic_graphs ORDER BY name")
    return [_row_to_out(r) for r in rows]


@router.post("/graphs", response_model=LogicGraphOut, status_code=status.HTTP_201_CREATED)
async def create_graph(
    body: LogicGraphCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(timezone.utc).isoformat()
    gid = str(uuid.uuid4())
    await db.execute_and_commit(
        """INSERT INTO logic_graphs (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (gid, body.name, body.description, int(body.enabled),
         body.flow_data.model_dump_json(), now, now),
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
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    return _row_to_out(row)


@router.put("/graphs/{graph_id}", response_model=LogicGraphOut)
async def update_graph_full(
    graph_id: str,
    body: LogicGraphCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(timezone.utc).isoformat()
    row = await db.fetchone("SELECT id FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    await db.execute_and_commit(
        """UPDATE logic_graphs
           SET name=?, description=?, enabled=?, flow_data=?, updated_at=?
           WHERE id=?""",
        (body.name, body.description, int(body.enabled),
         body.flow_data.model_dump_json(), now, graph_id),
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
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> LogicGraphOut:
    now = datetime.now(timezone.utc).isoformat()
    row = await db.fetchone("SELECT * FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    name        = body.name        if body.name        is not None else row["name"]
    description = body.description if body.description is not None else row["description"]
    enabled     = body.enabled     if body.enabled     is not None else bool(row["enabled"])
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
    _user: str = Depends(get_current_user),
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


@router.post("/graphs/{graph_id}/run", status_code=status.HTTP_200_OK)
async def run_graph(
    graph_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    row = await db.fetchone("SELECT id FROM logic_graphs WHERE id=?", (graph_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Graph nicht gefunden")
    try:
        from obs.logic.manager import get_logic_manager
        outputs = await get_logic_manager().execute_graph(graph_id)
        return {"status": "ok", "outputs": outputs}
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
