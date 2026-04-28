"""
Visu API — /api/v1/visu/...

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
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from obs.api.auth import get_admin_user, get_current_user, optional_current_user
from obs.api.v1.sessions import create_session
from obs.db.database import get_db, Database
from obs.models.visu import (
    VisuNode,
    VisuNodeCreate,
    VisuNodeUpdate,
    VisuNodeUsersUpdate,
    VisuImportRequest,
    PageConfig,
    WidgetInstance,
    PinAuthRequest,
    PinAuthResponse,
    CopyNodeRequest,
    MoveNodeRequest,
)

router = APIRouter(tags=["visu"])

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        access_pin=None,           # PIN-Hash niemals in der API zurückgeben
        page_config=PageConfig(**pc) if pc else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_node_or_404(db: Database, node_id: str) -> VisuNode:
    async with db.conn.execute(
        "SELECT * FROM visu_nodes WHERE id = ?", (node_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")
    return _row_to_node(row)


async def _resolve_access(db: Database, node_id: str) -> str:
    """Traversiert die parent_id-Kette und gibt das effektive Access-Level zurück."""
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute(
            "SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        if row["access"] is not None:
            return row["access"]
        current_id = row["parent_id"]
    return "public"   # Fallback: kein Knoten hat explizites Access → public


async def _resolve_access_with_node(db: Database, node_id: str) -> tuple[str, str | None]:
    """Gibt (access_level, defining_node_id) zurück — defining_node_id ist der Knoten,
    der das Access-Level explizit setzt (für visu_node_users-Lookup)."""
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute(
            "SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        if row["access"] is not None:
            return row["access"], current_id
        current_id = row["parent_id"]
    return "public", None


async def _check_user_access(db: Database, node_id: str, username: str) -> bool:
    """Gibt True zurück, wenn der Benutzer für den angegebenen 'user'-Knoten
    autorisiert ist (Admin oder explizit zugewiesen)."""
    user_row = await db.fetchone(
        "SELECT is_admin FROM users WHERE username = ?", (username,)
    )
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


# ── Tree ──────────────────────────────────────────────────────────────────────

@router.get("/tree", response_model=list[VisuNode])
async def get_tree(db: Database = Depends(get_db)):
    """Gesamtbaum als flache Liste (Frontend baut Baum via parent_id)."""
    async with db.conn.execute(
        "SELECT * FROM visu_nodes ORDER BY node_order ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_node(r) for r in rows]


# ── Einzelner Knoten ──────────────────────────────────────────────────────────

@router.post("/nodes/import", response_model=VisuNode, status_code=status.HTTP_201_CREATED)
async def import_nodes(
    body: VisuImportRequest,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Importiert einen exportierten Visu-Teilbaum und hängt ihn an target_parent_id."""
    if body.obs_export != "visu_subtree":
        raise HTTPException(status_code=400, detail="Ungültiges Export-Format (erwartet 'visu_subtree')")
    if not body.nodes:
        raise HTTPException(status_code=400, detail="Keine Knoten im Export")

    now = _now_iso()
    # Neue IDs für alle Knoten generieren
    id_map = {n.id: str(uuid.uuid4()) for n in body.nodes}
    root_node = body.nodes[0]
    root_new_id = id_map[root_node.id]

    for node in body.nodes:
        new_id = id_map[node.id]
        if node.id == root_node.id:
            new_parent_id = body.target_parent_id
        else:
            new_parent_id = id_map.get(node.parent_id or "", None) or body.target_parent_id

        # Widget-UUIDs neu generieren
        pc = node.page_config
        if pc and "widgets" in pc:
            for w in pc["widgets"]:
                w["id"] = str(uuid.uuid4())
        pc_json = json.dumps(pc) if pc else json.dumps(
            {"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []}
        )

        await db.conn.execute(
            """INSERT INTO visu_nodes
                   (id, parent_id, name, type, node_order, icon, access, access_pin,
                    page_config, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id, new_parent_id, node.name, node.type, node.node_order,
             node.icon, node.access, None, pc_json, now, now),
        )
    await db.conn.commit()
    return await _get_node_or_404(db, root_new_id)


@router.get("/nodes/{node_id}", response_model=VisuNode)
async def get_node(node_id: str, db: Database = Depends(get_db)):
    return await _get_node_or_404(db, node_id)


@router.post("/nodes", response_model=VisuNode, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: VisuNodeCreate,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    now = _now_iso()
    node_id = str(uuid.uuid4())

    pin_hash: str | None = None
    if body.access_pin:
        pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()

    default_pc = json.dumps({
        "grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []
    })

    await db.conn.execute(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id, body.parent_id, body.name, body.type, body.order,
            body.icon, body.access, pin_hash, default_pc, now, now,
        ),
    )
    await db.conn.commit()
    return await _get_node_or_404(db, node_id)


@router.patch("/nodes/{node_id}", response_model=VisuNode)
async def update_node(
    node_id: str,
    body: VisuNodeUpdate,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    await _get_node_or_404(db, node_id)
    updates: list[str] = []
    values: list = []

    if body.name is not None:
        updates.append("name = ?"); values.append(body.name)
    if body.order is not None:
        updates.append("node_order = ?"); values.append(body.order)
    if body.icon is not None:
        updates.append("icon = ?"); values.append(body.icon)
    if body.access is not None:
        updates.append("access = ?"); values.append(body.access)
    if body.access_pin is not None:
        pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()
        updates.append("access_pin = ?"); values.append(pin_hash)

    if updates:
        updates.append("updated_at = ?"); values.append(_now_iso())
        values.append(node_id)
        await db.conn.execute(
            f"UPDATE visu_nodes SET {', '.join(updates)} WHERE id = ?", values
        )
        await db.conn.commit()

    return await _get_node_or_404(db, node_id)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
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
        async with db.conn.execute(
            "SELECT * FROM visu_nodes WHERE id = ?", (current_id,)
        ) as cur:
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
    _user=Depends(get_current_user),
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
        pc_json = json.dumps({
            "grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []
        })

    await db.conn.execute(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin,
             page_config, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id, body.target_parent_id, body.new_name, source.type,
            source.order, source.icon, source.access, None,
            pc_json, now, now,
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
        result = [{
            "id": row["id"],
            "parent_id": row["parent_id"],
            "name": row["name"],
            "type": row["type"],
            "node_order": row["node_order"],
            "icon": row["icon"],
            "access": row["access"],
            "page_config": json.loads(row["page_config"]) if row["page_config"] else None,
        }]
        async with db.conn.execute(
            "SELECT id FROM visu_nodes WHERE parent_id = ? ORDER BY node_order", (nid,)
        ) as cur:
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
        "exported_at": datetime.now(timezone.utc).isoformat(),
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
    _user=Depends(get_current_user),
):
    await _get_node_or_404(db, node_id)
    await db.conn.execute(
        "UPDATE visu_nodes SET parent_id = ?, node_order = ?, updated_at = ? WHERE id = ?",
        (body.new_parent_id, body.order, _now_iso(), node_id),
    )
    await db.conn.commit()
    return await _get_node_or_404(db, node_id)


# ── PIN-Authentifizierung ─────────────────────────────────────────────────────

@router.post("/nodes/{node_id}/auth", response_model=PinAuthResponse)
async def pin_auth(
    node_id: str,
    body: PinAuthRequest,
    db: Database = Depends(get_db),
):
    async with db.conn.execute(
        "SELECT access_pin, access FROM visu_nodes WHERE id = ?", (node_id,)
    ) as cur:
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
    db: Database = Depends(get_db),
    user: str | None = Depends(optional_current_user),
):
    node = await _get_node_or_404(db, node_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

    access = await _resolve_access(db, node_id)
    if access == "user":
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Anmeldung erforderlich")
        if not await _check_user_access(db, node_id, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")

    return node.page_config or PageConfig()


@router.get("/widget-ref/{page_id}", response_model=list[WidgetInstance])
async def get_widget_ref(page_id: str, db: Database = Depends(get_db)):
    """Gibt alle Widget-Instanzen einer Seite zurück — ohne Zugriffsprüfung.
    Wird ausschließlich von WidgetRef-Widgets verwendet, die einzelne Widgets
    aus einer anderen Seite einbetten. Die Zugriffskontrolle erfolgt auf Ebene
    der einbettenden Seite."""
    node = await _get_node_or_404(db, page_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")
    pc = node.page_config or PageConfig()
    return pc.widgets


@router.put("/pages/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_page(
    node_id: str,
    config: PageConfig,
    db: Database = Depends(get_db),
    _user=Depends(get_current_user),
):
    node = await _get_node_or_404(db, node_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

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
    Admins haben immer Zugriff und tauchen hier nicht auf."""
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
    Nur gültige (existierende, nicht-Admin) Benutzernamen werden gespeichert."""
    await _get_node_or_404(db, node_id)

    # Nur existierende, nicht-Admin Benutzer akzeptieren
    valid: list[str] = []
    for username in body.usernames:
        row = await db.fetchone(
            "SELECT is_admin FROM users WHERE username = ?", (username,)
        )
        if row and not bool(row["is_admin"]):
            valid.append(username)

    await db.conn.execute(
        "DELETE FROM visu_node_users WHERE node_id = ?", (node_id,)
    )
    if valid:
        await db.conn.executemany(
            "INSERT OR IGNORE INTO visu_node_users (node_id, username) VALUES (?, ?)",
            [(node_id, u) for u in valid],
        )
    await db.conn.commit()
