"""Visu API — /api/v1/visu/...

Endpoints:
  GET    /visu/tree                      → Gesamtbaum (flach)
  GET    /visu/nodes/{id}                → Einzelner Knoten
  POST   /visu/nodes                     → Knoten erstellen
  POST   /visu/nodes/import              → Teilbaum importieren
  PATCH  /visu/nodes/{id}                → Knoten bearbeiten
  DELETE /visu/nodes/{id}                → Knoten löschen
  GET    /visu/nodes/{id}/breadcrumb     → Breadcrumb-Pfad
  GET    /visu/nodes/{id}/children       → Direkte Kinder
  POST   /visu/nodes/{id}/copy           → Knoten kopieren
  PUT    /visu/nodes/{id}/move           → Knoten verschieben
  GET    /visu/nodes/{id}/export         → Teilbaum als JSON exportieren
  POST   /visu/nodes/{id}/auth           → PIN-Authentifizierung

  GET    /visu/pages/{id}                → page_config lesen
  PUT    /visu/pages/{id}                → page_config speichern
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from obs.api.auth import Principal, get_admin_user, get_current_principal, get_current_user, limiter
from obs.api.authz import AuthzAction
from obs.api.authz_service import filter_authorized_datapoints
from obs.api.v1.datapoint_config import collect_datapoint_ids_from_config, is_uuid_str
from obs.api.v1.sessions import create_session, validate_session
from obs.db.database import Database, get_db
from obs.models.visu import (
    CopyNodeRequest,
    MoveNodeRequest,
    PageConfig,
    PinAuthRequest,
    PinAuthResponse,
    VisuImportRequest,
    VisuNode,
    VisuNodeCreate,
    VisuNodeUpdate,
    VisuNodeUsersUpdate,
    WidgetInstance,
)

router = APIRouter(tags=["visu"])
_visu_bearer = HTTPBearer(auto_error=False)
_visu_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_node(row) -> VisuNode:
    """SQLite-Row → VisuNode Pydantic-Modell"""
    pc_raw = row["page_config"]
    pc = json.loads(pc_raw) if pc_raw else None
    return VisuNode(
        id=row["id"],
        parent_id=row["parent_id"],
        name=row["name"],
        type=row["type"],
        order=row["node_order"],
        icon=row["icon"],
        access=row["access"],
        access_pin=None,  # PIN-Hash niemals in der API zurückgeben
        page_config=PageConfig(**pc) if pc else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_node_or_404(db: Database, node_id: str) -> VisuNode:
    async with db.conn.execute("SELECT * FROM visu_nodes WHERE id = ?", (node_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")
    return _row_to_node(row)


async def _resolve_access(db: Database, node_id: str) -> str:
    """Traversiert die parent_id-Kette und gibt das effektive Access-Level zurück."""
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute("SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            break
        if row["access"] is not None:
            return row["access"]
        current_id = row["parent_id"]
    return "public"  # Fallback: kein Knoten hat explizites Access → public


async def _resolve_access_with_node(db: Database, node_id: str) -> tuple[str, str | None]:
    """Gibt (access_level, defining_node_id) zurück — defining_node_id ist der Knoten,
    der das Access-Level explizit setzt (für visu_node_users-Lookup).
    """
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute("SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            break
        if row["access"] is not None:
            return row["access"], current_id
        current_id = row["parent_id"]
    return "public", None


async def _resolve_access_with_node_overrides(
    db: Database,
    node_id: str,
    *,
    access_overrides: dict[str, str | None] | None = None,
    parent_overrides: dict[str, str | None] | None = None,
) -> tuple[str, str | None]:
    current_id: str | None = node_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        async with db.conn.execute("SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            break
        access = access_overrides[current_id] if access_overrides and current_id in access_overrides else row["access"]
        if access is not None:
            return access, current_id
        current_id = parent_overrides[current_id] if parent_overrides and current_id in parent_overrides else row["parent_id"]
    return "public", None


async def _check_user_access(db: Database, node_id: str, username: str) -> bool:
    """Gibt True zurück, wenn der Benutzer für den angegebenen 'user'-Knoten
    autorisiert ist (Admin oder explizit zugewiesen).
    """
    user_row = await db.fetchone("SELECT is_admin FROM users WHERE username = ?", (username,))
    if not user_row:
        return False
    if bool(user_row["is_admin"]):
        return True
    _, defining_node_id = await _resolve_access_with_node(db, node_id)
    if not defining_node_id:
        return False
    auth_row = await db.fetchone(
        "SELECT 1 FROM visu_node_users WHERE node_id = ? AND username = ?",
        (defining_node_id, username),
    )
    return auth_row is not None


async def _optional_visu_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_visu_bearer),
    api_key: str | None = Depends(_visu_api_key_header),
    db: Database = Depends(get_db),
) -> Principal | None:
    if credentials is None and api_key is None:
        return None
    try:
        return await get_current_principal(credentials=credentials, api_key=api_key, db=db)
    except HTTPException:
        return None


def _principal_from_dependency(value: Principal | str | None) -> Principal | None:
    if value is None or isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _collect_page_datapoint_ids(config: PageConfig) -> list[str]:
    datapoint_ids: set[str] = set()
    for widget in config.widgets:
        if widget.datapoint_id and is_uuid_str(widget.datapoint_id):
            datapoint_ids.add(widget.datapoint_id)
        if widget.status_datapoint_id and is_uuid_str(widget.status_datapoint_id):
            datapoint_ids.add(widget.status_datapoint_id)
        collect_datapoint_ids_from_config(widget.config, datapoint_ids)
    return sorted(datapoint_ids)


async def _check_page_datapoint_policy(
    db: Database,
    principal: Principal | None,
    datapoint_ids: list[str],
    action: AuthzAction,
    *,
    allow_empty: bool = True,
) -> None:
    if principal is None or (principal.type == "user" and principal.is_admin):
        return
    if not datapoint_ids:
        if allow_empty:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")

    allowed_ids = set(await filter_authorized_datapoints(db, principal, datapoint_ids, action=action))
    if not set(datapoint_ids).issubset(allowed_ids):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")


async def _target_usernames_for_node(
    db: Database,
    defining_node_id: str,
    *,
    usernames: list[str] | None = None,
) -> list[str]:
    if usernames is not None:
        return sorted(set(usernames))
    rows = await db.fetchall(
        "SELECT username FROM visu_node_users WHERE node_id = ? ORDER BY username",
        (defining_node_id,),
    )
    return [row["username"] for row in rows]


async def _check_user_page_target_datapoint_policy(
    db: Database,
    defining_node_id: str,
    config: PageConfig,
    *,
    usernames: list[str] | None = None,
) -> None:
    datapoint_ids = _collect_page_datapoint_ids(config)
    if not datapoint_ids:
        return

    for username in await _target_usernames_for_node(db, defining_node_id, usernames=usernames):
        user_row = await db.fetchone("SELECT is_admin FROM users WHERE username = ?", (username,))
        principal = Principal(subject=username, type="user", is_admin=bool(user_row and user_row["is_admin"]))
        allowed_ids = set(await filter_authorized_datapoints(db, principal, datapoint_ids, action=AuthzAction.READ))
        if not set(datapoint_ids).issubset(allowed_ids):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zielgruppe darf nicht alle Datenpunkte lesen")


async def _check_user_target_pages_datapoint_policy(
    db: Database,
    defining_node_id: str,
    *,
    usernames: list[str],
) -> None:
    rows = await db.fetchall("SELECT * FROM visu_nodes WHERE type = 'PAGE'")
    for row in rows:
        page_id = row["id"]
        access, access_node_id = await _resolve_access_with_node(db, page_id)
        if access != "user" or access_node_id != defining_node_id:
            continue
        node = _row_to_node(row)
        await _check_user_page_target_datapoint_policy(
            db,
            defining_node_id,
            node.page_config or PageConfig(),
            usernames=usernames,
        )


async def _check_user_target_pages_datapoint_policy_after_access_change(
    db: Database,
    *,
    access_overrides: dict[str, str | None] | None = None,
    parent_overrides: dict[str, str | None] | None = None,
) -> None:
    rows = await db.fetchall("SELECT * FROM visu_nodes WHERE type = 'PAGE'")
    for row in rows:
        page_id = row["id"]
        current_access, current_access_node_id = await _resolve_access_with_node(db, page_id)
        access, access_node_id = await _resolve_access_with_node_overrides(
            db,
            page_id,
            access_overrides=access_overrides,
            parent_overrides=parent_overrides,
        )
        if (access, access_node_id) == (current_access, current_access_node_id):
            continue
        if access != "user" or access_node_id is None:
            continue
        node = _row_to_node(row)
        await _check_user_page_target_datapoint_policy(
            db,
            access_node_id,
            node.page_config or PageConfig(),
        )


async def _check_inherited_user_page_target_datapoint_policy(
    db: Database,
    *,
    parent_id: str | None,
    access: str | None,
    config: PageConfig,
) -> None:
    if access is not None or parent_id is None:
        return
    inherited_access, defining_node_id = await _resolve_access_with_node(db, parent_id)
    if inherited_access == "user" and defining_node_id is not None:
        await _check_user_page_target_datapoint_policy(db, defining_node_id, config)


async def _imported_user_access_defining_node(
    db: Database,
    node_id: str,
    *,
    nodes_by_id: dict[str, Any],
    id_map: dict[str, str],
    target_parent_id: str | None,
) -> str | None:
    current = nodes_by_id[node_id]
    while current is not None:
        if current.access is not None:
            return id_map[current.id] if current.access == "user" else None
        parent_id = current.parent_id
        current = nodes_by_id.get(parent_id or "")

    if target_parent_id is None:
        return None
    inherited_access, defining_node_id = await _resolve_access_with_node(db, target_parent_id)
    return defining_node_id if inherited_access == "user" else None


async def _check_page_write_access(db: Database, node_id: str, principal: Principal | None) -> None:
    if principal is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")
    if principal.type == "user" and principal.is_admin:
        return
    access, _ = await _resolve_access_with_node(db, node_id)
    if access in ("readonly", "protected"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")
    if access == "user" and (principal.type != "user" or not await _check_user_access(db, node_id, principal.subject)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")


# ── Tree ──────────────────────────────────────────────────────────────────────


@router.get("/tree", response_model=list[VisuNode])
async def get_tree(db: Database = Depends(get_db)):
    """Gesamtbaum als flache Liste (Frontend baut Baum via parent_id)."""
    async with db.conn.execute("SELECT * FROM visu_nodes ORDER BY node_order ASC") as cur:
        rows = await cur.fetchall()
    return [_row_to_node(r) for r in rows]


# ── Einzelner Knoten ──────────────────────────────────────────────────────────


@router.post("/nodes/import", response_model=VisuNode, status_code=status.HTTP_201_CREATED)
async def import_nodes(
    body: VisuImportRequest,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    """Importiert einen exportierten Visu-Teilbaum und hängt ihn an target_parent_id."""
    if body.obs_export != "visu_subtree":
        raise HTTPException(status_code=400, detail="Ungültiges Export-Format (erwartet 'visu_subtree')")
    if not body.nodes:
        raise HTTPException(status_code=400, detail="Keine Knoten im Export")

    now = _now_iso()
    # Neue IDs für alle Knoten generieren
    id_map = {n.id: str(uuid.uuid4()) for n in body.nodes}
    nodes_by_id = {n.id: n for n in body.nodes}
    root_node = body.nodes[0]
    root_new_id = id_map[root_node.id]

    await db.conn.execute("SAVEPOINT visu_import_nodes")
    try:
        for node in body.nodes:
            new_id = id_map[node.id]
            if node.id == root_node.id:
                new_parent_id = body.target_parent_id
            else:
                new_parent_id = id_map.get(node.parent_id or "") or body.target_parent_id

            # Widget-UUIDs neu generieren
            pc = node.page_config
            if pc and "widgets" in pc:
                for w in pc["widgets"]:
                    w["id"] = str(uuid.uuid4())
            pc_json = (
                json.dumps(pc)
                if pc
                else json.dumps(
                    {
                        "grid_cols": 12,
                        "grid_row_height": 80,
                        "background": None,
                        "widgets": [],
                    },
                )
            )
            if node.type == "PAGE":
                defining_node_id = await _imported_user_access_defining_node(
                    db,
                    node.id,
                    nodes_by_id=nodes_by_id,
                    id_map=id_map,
                    target_parent_id=body.target_parent_id,
                )
                if defining_node_id is not None:
                    await _check_user_page_target_datapoint_policy(db, defining_node_id, PageConfig.model_validate_json(pc_json))

            await db.conn.execute(
                """INSERT INTO visu_nodes
                       (id, parent_id, name, type, node_order, icon, access, access_pin,
                        page_config, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    new_parent_id,
                    node.name,
                    node.type,
                    node.node_order,
                    node.icon,
                    node.access,
                    None,
                    pc_json,
                    now,
                    now,
                ),
            )
    except Exception:
        await db.conn.execute("ROLLBACK TO SAVEPOINT visu_import_nodes")
        await db.conn.execute("RELEASE SAVEPOINT visu_import_nodes")
        raise
    await db.conn.execute("RELEASE SAVEPOINT visu_import_nodes")
    await db.conn.commit()
    return await _get_node_or_404(db, root_new_id)


@router.get("/nodes/{node_id}", response_model=VisuNode)
async def get_node(node_id: str, db: Database = Depends(get_db)):
    return await _get_node_or_404(db, node_id)


@router.post("/nodes", response_model=VisuNode, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: VisuNodeCreate,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    now = _now_iso()
    node_id = str(uuid.uuid4())

    pin_hash: str | None = None
    if body.access_pin:
        pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()

    default_pc = json.dumps({"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []})

    await db.conn.execute(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            body.parent_id,
            body.name,
            body.type,
            body.order,
            body.icon,
            body.access,
            pin_hash,
            default_pc,
            now,
            now,
        ),
    )
    await db.conn.commit()
    return await _get_node_or_404(db, node_id)


@router.patch("/nodes/{node_id}", response_model=VisuNode)
async def update_node(
    node_id: str,
    body: VisuNodeUpdate,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    await _get_node_or_404(db, node_id)
    updates: list[str] = []
    values: list = []

    if body.access == "user":
        await _check_user_target_pages_datapoint_policy_after_access_change(
            db,
            access_overrides={node_id: body.access},
        )

    if body.name is not None:
        updates.append("name = ?")
        values.append(body.name)
    if body.order is not None:
        updates.append("node_order = ?")
        values.append(body.order)
    if body.icon is not None:
        updates.append("icon = ?")
        values.append(body.icon)
    if body.access is not None:
        updates.append("access = ?")
        values.append(body.access)
    if body.access_pin is not None:
        pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()
        updates.append("access_pin = ?")
        values.append(pin_hash)

    if updates:
        updates.append("updated_at = ?")
        values.append(_now_iso())
        values.append(node_id)
        await db.conn.execute(f"UPDATE visu_nodes SET {', '.join(updates)} WHERE id = ?", values)
        await db.conn.commit()

    return await _get_node_or_404(db, node_id)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    await _get_node_or_404(db, node_id)
    # ON DELETE CASCADE löscht Kinder automatisch
    await db.conn.execute("DELETE FROM visu_nodes WHERE id = ?", (node_id,))
    await db.conn.commit()


# ── Breadcrumb ────────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/breadcrumb", response_model=list[VisuNode])
async def get_breadcrumb(node_id: str, db: Database = Depends(get_db)):
    crumbs: list[VisuNode] = []
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute("SELECT * FROM visu_nodes WHERE id = ?", (current_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            break
        crumbs.insert(0, _row_to_node(row))
        current_id = row["parent_id"]
    return crumbs


# ── Kinder ────────────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/children", response_model=list[VisuNode])
async def get_children(node_id: str, db: Database = Depends(get_db)):
    async with db.conn.execute(
        "SELECT * FROM visu_nodes WHERE parent_id = ? ORDER BY node_order ASC",
        (node_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_node(r) for r in rows]


# ── Kopieren ──────────────────────────────────────────────────────────────────


@router.post("/nodes/{node_id}/copy", response_model=VisuNode, status_code=201)
async def copy_node(
    node_id: str,
    body: CopyNodeRequest,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    source = await _get_node_or_404(db, node_id)
    now = _now_iso()
    new_id = str(uuid.uuid4())

    # page_config: neue Widget-UUIDs generieren
    pc = source.page_config
    if pc:
        new_widgets = [w.model_copy(update={"id": str(uuid.uuid4())}) for w in pc.widgets]
        new_pc = pc.model_copy(update={"widgets": new_widgets})
        pc_json = new_pc.model_dump_json()
    else:
        new_pc = PageConfig()
        pc_json = json.dumps({"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []})
    if source.type == "PAGE":
        await _check_inherited_user_page_target_datapoint_policy(
            db,
            parent_id=body.target_parent_id,
            access=source.access,
            config=new_pc,
        )

    await db.conn.execute(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin,
             page_config, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id,
            body.target_parent_id,
            body.new_name,
            source.type,
            source.order,
            source.icon,
            source.access,
            None,
            pc_json,
            now,
            now,
        ),
    )
    await db.conn.commit()
    return await _get_node_or_404(db, new_id)


# ── Exportieren ──────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/export")
async def export_node(
    node_id: str,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
) -> JSONResponse:
    """Exportiert den Knoten und alle Nachfolger rekursiv als JSON (ohne access_pin)."""

    async def collect(nid: str) -> list[dict]:
        async with db.conn.execute("SELECT * FROM visu_nodes WHERE id = ?", (nid,)) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        result = [
            {
                "id": row["id"],
                "parent_id": row["parent_id"],
                "name": row["name"],
                "type": row["type"],
                "node_order": row["node_order"],
                "icon": row["icon"],
                "access": row["access"],
                "page_config": json.loads(row["page_config"]) if row["page_config"] else None,
            },
        ]
        async with db.conn.execute("SELECT id FROM visu_nodes WHERE parent_id = ? ORDER BY node_order", (nid,)) as cur:
            children = await cur.fetchall()
        for child in children:
            result.extend(await collect(child["id"]))
        return result

    nodes = await collect(node_id)
    if not nodes:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")

    export_data = {
        "obs_export": "visu_subtree",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "nodes": nodes,
    }
    safe_name = nodes[0]["name"].replace(" ", "_").replace("/", "_")
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_visu.json"'},
    )


# ── Verschieben ───────────────────────────────────────────────────────────────


@router.put("/nodes/{node_id}/move", response_model=VisuNode)
async def move_node(
    node_id: str,
    body: MoveNodeRequest,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    await _get_node_or_404(db, node_id)
    await _check_user_target_pages_datapoint_policy_after_access_change(
        db,
        parent_overrides={node_id: body.new_parent_id},
    )
    await db.conn.execute(
        "UPDATE visu_nodes SET parent_id = ?, node_order = ?, updated_at = ? WHERE id = ?",
        (body.new_parent_id, body.order, _now_iso(), node_id),
    )
    await db.conn.commit()
    return await _get_node_or_404(db, node_id)


# ── PIN-Authentifizierung ─────────────────────────────────────────────────────


@router.post("/nodes/{node_id}/auth", response_model=PinAuthResponse)
@limiter.limit("10/minute")
async def pin_auth(
    node_id: str,
    body: PinAuthRequest,
    request: Request,
    db: Database = Depends(get_db),
):
    async with db.conn.execute("SELECT access_pin, access FROM visu_nodes WHERE id = ?", (node_id,)) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")

    if row["access"] != "protected":
        raise HTTPException(status_code=400, detail="Knoten ist nicht PIN-gesichert")

    if not row["access_pin"]:
        raise HTTPException(status_code=500, detail="Kein PIN konfiguriert")

    if not bcrypt.checkpw(body.pin.encode(), row["access_pin"].encode()):
        raise HTTPException(status_code=401, detail="Falscher PIN")

    token = create_session(node_id, expires_in=3600)
    return PinAuthResponse(session_token=token, expires_in=3600)


# ── Page-Config ───────────────────────────────────────────────────────────────


@router.get("/pages/{node_id}", response_model=PageConfig)
async def get_page(
    node_id: str,
    request: Request,
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    principal = _principal_from_dependency(user)
    node = await _get_node_or_404(db, node_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

    access, defining_node_id = await _resolve_access_with_node(db, node_id)
    if principal is None:
        # Unauthentisierter Zugriff: Seitentyp prüfen
        if access == "user":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Anmeldung erforderlich",
            )
        elif access == "protected":
            session_token = request.headers.get("X-Session-Token")
            validate_id = defining_node_id or node_id
            if not session_token or not validate_session(session_token, validate_id):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="PIN-Authentifizierung erforderlich",
                )
    else:
        # Authentifizierter Benutzer: bei user-Pages explizite Zuweisung prüfen
        if access == "user" and (principal.type != "user" or not await _check_user_access(db, node_id, principal.subject)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")

    config = node.page_config or PageConfig()
    if access == "user":
        await _check_page_datapoint_policy(db, principal, _collect_page_datapoint_ids(config), AuthzAction.READ)
    return config


@router.get("/widget-ref/{page_id}", response_model=list[WidgetInstance])
async def get_widget_ref(
    page_id: str,
    request: Request,
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    """Gibt alle Widget-Instanzen einer Seite zurück.
    Wird von WidgetRef-Widgets verwendet, die einzelne Widgets aus einer anderen
    Seite einbetten. Zugriff richtet sich nach dem Access-Level der Quell-Seite.
    """
    principal = _principal_from_dependency(user)
    node = await _get_node_or_404(db, page_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    if principal is None:
        if access == "user":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Anmeldung erforderlich",
            )
        elif access == "protected":
            session_token = request.headers.get("X-Session-Token")
            validate_id = defining_node_id or page_id
            if not session_token or not validate_session(session_token, validate_id):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="PIN-Authentifizierung erforderlich",
                )
    else:
        if access == "user" and (principal.type != "user" or not await _check_user_access(db, page_id, principal.subject)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")

    pc = node.page_config or PageConfig()
    if access == "user":
        await _check_page_datapoint_policy(db, principal, _collect_page_datapoint_ids(pc), AuthzAction.READ)
    return pc.widgets


@router.put("/pages/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_page(
    node_id: str,
    config: PageConfig,
    db: Database = Depends(get_db),
    _admin=Depends(get_admin_user),
):
    node = await _get_node_or_404(db, node_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

    access, defining_node_id = await _resolve_access_with_node(db, node_id)
    if access == "user" and defining_node_id is not None:
        await _check_user_page_target_datapoint_policy(db, defining_node_id, config)

    await db.conn.execute(
        "UPDATE visu_nodes SET page_config = ?, updated_at = ? WHERE id = ?",
        (config.model_dump_json(), _now_iso(), node_id),
    )
    await db.conn.commit()


# ── Benutzer-Zugang (user-Access) ─────────────────────────────────────────────


@router.get("/nodes/{node_id}/users", response_model=list[str])
async def get_node_users(
    node_id: str,
    db: Database = Depends(get_db),
    _admin=Depends(get_admin_user),
):
    """Gibt die Liste der explizit autorisierten Benutzernamen für diesen Knoten zurück.
    Admins haben immer Zugriff und tauchen hier nicht auf.
    """
    await _get_node_or_404(db, node_id)
    rows = await db.fetchall(
        "SELECT username FROM visu_node_users WHERE node_id = ? ORDER BY username",
        (node_id,),
    )
    return [r["username"] for r in rows]


@router.put("/nodes/{node_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def set_node_users(
    node_id: str,
    body: VisuNodeUsersUpdate,
    db: Database = Depends(get_db),
    _admin=Depends(get_admin_user),
):
    """Setzt die autorisierten Benutzer für diesen Knoten (ersetzt die gesamte Liste).
    Nur gültige (existierende, nicht-Admin) Benutzernamen werden gespeichert.
    """
    await _get_node_or_404(db, node_id)

    # Nur existierende, nicht-Admin Benutzer akzeptieren
    valid: list[str] = []
    for username in body.usernames:
        row = await db.fetchone("SELECT is_admin FROM users WHERE username = ?", (username,))
        if row and not bool(row["is_admin"]):
            valid.append(username)

    await _check_user_target_pages_datapoint_policy(db, node_id, usernames=valid)

    await db.conn.execute("DELETE FROM visu_node_users WHERE node_id = ?", (node_id,))
    if valid:
        await db.conn.executemany(
            "INSERT OR IGNORE INTO visu_node_users (node_id, username) VALUES (?, ?)",
            [(node_id, u) for u in valid],
        )
    await db.conn.commit()
