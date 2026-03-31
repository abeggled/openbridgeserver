"""
Visu API — /api/v1/visu/...

Endpoints:
  GET    /visu/tree                      → Gesamtbaum (flach)
  GET    /visu/nodes/{id}                → Einzelner Knoten
  POST   /visu/nodes                     → Knoten erstellen
  PATCH  /visu/nodes/{id}                → Knoten bearbeiten
  DELETE /visu/nodes/{id}                → Knoten löschen
  GET    /visu/nodes/{id}/breadcrumb     → Breadcrumb-Pfad
  GET    /visu/nodes/{id}/children       → Direkte Kinder
  POST   /visu/nodes/{id}/copy           → Knoten kopieren
  PUT    /visu/nodes/{id}/move           → Knoten verschieben
  POST   /visu/nodes/{id}/auth           → PIN-Authentifizierung

  GET    /visu/pages/{id}                → page_config lesen
  PUT    /visu/pages/{id}                → page_config speichern
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status

from opentws.api.auth import get_current_user, optional_current_user
from opentws.db.database import get_db, Database
from opentws.models.visu import (
    VisuNode,
    VisuNodeCreate,
    VisuNodeUpdate,
    PageConfig,
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

# Einfacher In-Memory Session-Store (reicht für Single-Instance)
# In Multi-Instance: Redis oder DB-Tabelle verwenden
_sessions: dict[str, tuple[str, float]] = {}   # token → (node_id, expires_at)


@router.post("/nodes/{node_id}/auth", response_model=PinAuthResponse)
async def pin_auth(
    node_id: str,
    body: PinAuthRequest,
    db: Database = Depends(get_db),
):
    import time
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

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + 3600
    _sessions[token] = (node_id, expires_at)

    return PinAuthResponse(session_token=token, expires_in=3600)


# ── Page-Config ───────────────────────────────────────────────────────────────

@router.get("/pages/{node_id}", response_model=PageConfig)
async def get_page(node_id: str, db: Database = Depends(get_db)):
    node = await _get_node_or_404(db, node_id)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")
    return node.page_config or PageConfig()


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
