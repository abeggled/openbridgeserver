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

from obs.api.auth import Principal, get_admin_user, get_current_principal, limiter
from obs.api.capabilities import ConfigCapability, audit_config_capability_use, require_config_capability
from obs.api.authz import AuthzAction, authorize
from obs.api.authz_service import (
    authorize_visu_page,
    filter_authorized_datapoints,
    load_role_grants,
    resolve_visu_page_targets,
)
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
    VisuNodeSummary,
    VisuNodeUpdate,
    VisuNodeUsersUpdate,
    WidgetInstance,
)

router = APIRouter(tags=["visu"])
_visu_bearer = HTTPBearer(auto_error=False)
_visu_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_UNSET = object()

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_node(row, *, access: str | None = None) -> VisuNode:
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
        access=access,
        access_pin=None,  # PIN-Hash niemals in der API zurückgeben
        page_config=PageConfig(**pc) if pc else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_summary(
    row,
    *,
    access: str | None = None,
    parent_id: str | None | object = _UNSET,
) -> VisuNodeSummary:
    """SQLite row to the deliberately redacted navigation DTO."""
    return VisuNodeSummary(
        id=row["id"],
        parent_id=row["parent_id"] if parent_id is _UNSET else parent_id,
        name=row["name"],
        type=row["type"],
        order=row["node_order"],
        icon=row["icon"],
        access=access,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_node_or_404(db: Database, node_id: str) -> VisuNode:
    async with db.conn.execute(
        """SELECT vn.*, avp.access_mode
           FROM visu_nodes AS vn
           LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
           WHERE vn.id = ?""",
        (node_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")
    access = row["access_mode"] if "access_mode" in row.keys() else None
    return _row_to_node(row, access=access)


async def _resolve_access(db: Database, node_id: str) -> str:
    """Traversiert die parent_id-Kette und gibt das effektive Access-Level zurück."""
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute(
            """SELECT vn.parent_id, avp.access_mode
               FROM visu_nodes AS vn
               LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
               WHERE vn.id = ?""",
            (current_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        access_mode = row["access_mode"] if "access_mode" in row.keys() else row["access"]
        if access_mode is not None:
            return access_mode
        current_id = row["parent_id"]
    return "public"  # Fallback: kein Knoten hat explizites Access → public


async def _resolve_access_with_node(db: Database, node_id: str) -> tuple[str, str | None]:
    """Gibt (access_level, defining_node_id) zurück — defining_node_id ist der Knoten,
    der das Access-Level explizit setzt (für visu_node_users-Lookup).
    """
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute(
            """SELECT vn.parent_id, avp.access_mode
               FROM visu_nodes AS vn
               LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
               WHERE vn.id = ?""",
            (current_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        access_mode = row["access_mode"] if "access_mode" in row.keys() else row["access"]
        if access_mode is not None:
            return access_mode, current_id
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
        async with db.conn.execute(
            """SELECT vn.parent_id, avp.access_mode
               FROM visu_nodes AS vn
               LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
               WHERE vn.id = ?""",
            (current_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        stored_access = row["access_mode"] if "access_mode" in row.keys() else row["access"]
        access = access_overrides[current_id] if access_overrides and current_id in access_overrides else stored_access
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
    return await authorize_visu_page(
        db,
        Principal(subject=username, type="user", is_admin=False),
        node_id,
        action=AuthzAction.READ,
    )


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
    if not isinstance(value, str):
        return None
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _principal_from_mutation_dependency(value: Principal | str | object) -> Principal:
    if isinstance(value, Principal):
        return value
    # Direct callers historically pass the return value of get_admin_user as a
    # string. Runtime requests now receive a Principal from get_current_principal.
    subject = value if isinstance(value, str) else "admin"
    return Principal(subject=subject, type="user", is_admin=True)


async def _can_discover_node(db: Database, node_id: str, principal: Principal | None) -> bool:
    access, _ = await _resolve_access_with_node(db, node_id)
    if access != "user":
        return True
    if principal is None:
        return False
    if principal.type != "user":
        return False
    if principal.is_admin:
        return True
    return await authorize_visu_page(db, principal, node_id, action=AuthzAction.READ)


async def _require_discoverable_node(db: Database, node_id: str, principal: Principal | None) -> VisuNode:
    node = await _get_node_or_404(db, node_id)
    if not await _can_discover_node(db, node_id, principal):
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")
    return node


async def _visu_subtree_ids(db: Database, node_id: str) -> list[str]:
    rows = await db.fetchall(
        """WITH RECURSIVE subtree(id) AS (
               SELECT id FROM visu_nodes WHERE id = ?
            UNION
               SELECT child.id FROM visu_nodes AS child JOIN subtree ON child.parent_id = subtree.id
           )
           SELECT id FROM subtree""",
        (node_id,),
    )
    return [row["id"] for row in rows]


async def _require_visu_generate(db: Database, principal: Principal, node_ids: list[str]) -> None:
    if principal.type == "user" and principal.is_admin:
        return
    targets = await resolve_visu_page_targets(db, node_ids)
    grants = await load_role_grants(db, principal, node_type="visu_page")
    decision = authorize(
        principal=principal,
        action=AuthzAction.GENERATE,
        targets=targets,
        grants=grants,
    )
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")


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
        """SELECT principal_id
           FROM authz_node_roles
           WHERE principal_type = 'user' AND node_type = 'visu_page'
             AND node_id = ? AND effect = 'allow'
           ORDER BY principal_id""",
        (defining_node_id,),
    )
    return [row["principal_id"] for row in rows]


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
        missing_ids = sorted(set(datapoint_ids) - allowed_ids)
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "visu_target_audience_datapoints_denied",
                    "username": username,
                    "datapoint_ids": missing_ids,
                },
            )


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
    usernames_overrides: dict[str, list[str]] | None = None,
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
        target_group_changed = access_node_id is not None and usernames_overrides is not None and access_node_id in usernames_overrides
        if (access, access_node_id) == (current_access, current_access_node_id) and not target_group_changed:
            continue
        if access != "user" or access_node_id is None:
            continue
        node = _row_to_node(row)
        await _check_user_page_target_datapoint_policy(
            db,
            access_node_id,
            node.page_config or PageConfig(),
            usernames=usernames_overrides.get(access_node_id) if usernames_overrides and access_node_id in usernames_overrides else None,
        )


async def _validate_target_usernames(db: Database, usernames: list[str]) -> list[str]:
    requested = sorted(set(usernames))
    valid: list[str] = []
    invalid: list[str] = []
    for username in requested:
        row = await db.fetchone("SELECT is_admin FROM users WHERE username = ?", (username,))
        if row is None or bool(row["is_admin"]):
            invalid.append(username)
        else:
            valid.append(username)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "visu_target_audience_invalid_users", "usernames": invalid},
        )
    return valid


async def _replace_target_users(db: Database, node_id: str, usernames: list[str]) -> None:
    """Replace only the simple target-audience grants; preserve advanced grants."""
    await db.conn.execute(
        """DELETE FROM authz_node_roles
           WHERE principal_type='user' AND node_type='visu_page' AND node_id=?
             AND role='guest' AND effect='allow'""",
        (node_id,),
    )
    if usernames:
        await db.conn.executemany(
            """INSERT INTO authz_node_roles
                   (principal_type, principal_id, node_type, node_id, role, effect)
               VALUES ('user', ?, 'visu_page', ?, 'guest', 'allow')
               ON CONFLICT(principal_type, principal_id, node_type, node_id) DO NOTHING""",
            [(username, node_id) for username in usernames],
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


@router.get("/tree", response_model=list[VisuNodeSummary])
async def get_tree(
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    """Gesamtbaum als flache Liste (Frontend baut Baum via parent_id)."""
    principal = _principal_from_dependency(user)
    async with db.conn.execute(
        """SELECT vn.*, avp.access_mode
           FROM visu_nodes AS vn
           LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
           ORDER BY vn.node_order ASC""",
    ) as cur:
        rows = await cur.fetchall()
    visible_rows = [row for row in rows if await _can_discover_node(db, row["id"], principal)]
    visible_ids = {row["id"] for row in visible_rows}
    return [
        _row_to_summary(
            row,
            access=row["access_mode"] if "access_mode" in row.keys() else None,
            parent_id=row["parent_id"] if row["parent_id"] in visible_ids else None,
        )
        for row in visible_rows
    ]


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
                       (id, parent_id, name, type, node_order, icon,
                        page_config, created_at, updated_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    new_parent_id,
                    node.name,
                    node.type,
                    node.node_order,
                    node.icon,
                    pc_json,
                    now,
                    now,
                    _user if node.type == "PAGE" else None,
                ),
            )
            if node.access is not None:
                await db.conn.execute(
                    "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)",
                    (new_id, node.access),
                )
    except Exception:
        await db.conn.execute("ROLLBACK TO SAVEPOINT visu_import_nodes")
        await db.conn.execute("RELEASE SAVEPOINT visu_import_nodes")
        raise
    await db.conn.execute("RELEASE SAVEPOINT visu_import_nodes")
    await db.conn.commit()
    return await _get_node_or_404(db, root_new_id)


@router.get("/nodes/{node_id}", response_model=VisuNodeSummary)
async def get_node(
    node_id: str,
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    principal = _principal_from_dependency(user)
    node = await _require_discoverable_node(db, node_id, principal)
    parent_id = node.parent_id
    if parent_id is not None and not await _can_discover_node(db, parent_id, principal):
        parent_id = None
    return VisuNodeSummary(
        id=node.id,
        parent_id=parent_id,
        name=node.name,
        type=node.type,
        order=node.order,
        icon=node.icon,
        access=node.access,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.post("/nodes", response_model=VisuNode, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: VisuNodeCreate,
    db: Database = Depends(get_db),
    _user=Depends(get_admin_user),
):
    now = _now_iso()
    node_id = str(uuid.uuid4())

    pin_hash: str | None = None
    if body.access == "protected" and body.access_pin:
        pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()

    default_pc = json.dumps({"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []})

    await db.conn.execute(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, page_config,
             created_at, updated_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            body.parent_id,
            body.name,
            body.type,
            body.order,
            body.icon,
            default_pc,
            now,
            now,
            _user if body.type == "PAGE" else None,
        ),
    )
    if body.access is not None:
        await db.conn.execute(
            "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)",
            (node_id, body.access),
        )
    if pin_hash is not None:
        await db.conn.execute(
            "INSERT INTO authz_visu_page_credentials (node_id, pin_hash) VALUES (?, ?)",
            (node_id, pin_hash),
        )
    await db.conn.commit()
    return await _get_node_or_404(db, node_id)


@router.patch("/nodes/{node_id}", response_model=VisuNode)
async def update_node(
    node_id: str,
    body: VisuNodeUpdate,
    db: Database = Depends(get_db),
    _user: Principal | str = Depends(get_current_principal),
):
    principal = _principal_from_mutation_dependency(_user)
    access_supplied = "access" in body.model_fields_set
    usernames_supplied = "usernames" in body.model_fields_set

    async with db.transaction():
        node = await _require_discoverable_node(db, node_id, principal)
        await _require_visu_generate(db, principal, await _visu_subtree_ids(db, node_id))
        requested_access = body.access if access_supplied else node.access
        target_usernames: list[str] | None = None
        if usernames_supplied:
            target_usernames = await _validate_target_usernames(db, body.usernames or [])
            if requested_access != "user" and target_usernames:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={"code": "visu_target_audience_requires_user_access"},
                )
        elif access_supplied and requested_access != "user":
            target_usernames = []

        access_overrides = {node_id: requested_access} if access_supplied else None
        usernames_overrides = {node_id: target_usernames} if target_usernames is not None else None
        if access_supplied or target_usernames is not None:
            await _check_user_target_pages_datapoint_policy_after_access_change(
                db,
                access_overrides=access_overrides,
                usernames_overrides=usernames_overrides,
            )

        updates: list[str] = []
        values: list = []
        if body.name is not None:
            updates.append("name = ?")
            values.append(body.name)
        if body.order is not None:
            updates.append("node_order = ?")
            values.append(body.order)
        if "icon" in body.model_fields_set:
            updates.append("icon = ?")
            values.append(body.icon)

        pin_hash: str | None = None
        if body.access_pin is not None:
            if requested_access != "protected":
                raise HTTPException(status_code=400, detail="PIN ist nur für geschützte Knoten zulässig")
            pin_hash = bcrypt.hashpw(body.access_pin.encode(), bcrypt.gensalt()).decode()

        if updates:
            updates.append("updated_at = ?")
            values.extend((_now_iso(), node_id))
            await db.conn.execute(f"UPDATE visu_nodes SET {', '.join(updates)} WHERE id = ?", values)

        if access_supplied:
            if requested_access is None:
                await db.conn.execute("DELETE FROM authz_visu_page_policies WHERE node_id = ?", (node_id,))
            else:
                await db.conn.execute(
                    """INSERT INTO authz_visu_page_policies (node_id, access_mode, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(node_id) DO UPDATE
                    SET access_mode=excluded.access_mode, updated_at=excluded.updated_at""",
                    (node_id, requested_access, _now_iso()),
                )
            if requested_access != "protected":
                await db.conn.execute("DELETE FROM authz_visu_page_credentials WHERE node_id = ?", (node_id,))

        if pin_hash is not None:
            await db.conn.execute(
                """INSERT INTO authz_visu_page_credentials (node_id, pin_hash, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET pin_hash=excluded.pin_hash, updated_at=excluded.updated_at""",
                (node_id, pin_hash, _now_iso()),
            )
        if target_usernames is not None:
            await _replace_target_users(db, node_id, target_usernames)

    return await _get_node_or_404(db, node_id)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str,
    db: Database = Depends(get_db),
    _user: Principal | str = Depends(get_current_principal),
):
    principal = _principal_from_mutation_dependency(_user)
    async with db.transaction():
        await _require_discoverable_node(db, node_id, principal)
        subtree_ids = await _visu_subtree_ids(db, node_id)
        await _require_visu_generate(db, principal, subtree_ids)
        placeholders = ",".join("?" for _ in subtree_ids)
        await db.conn.execute(
            f"DELETE FROM authz_node_roles WHERE node_type='visu_page' AND node_id IN ({placeholders})",
            subtree_ids,
        )
        # ON DELETE CASCADE removes descendants, policies and credentials.
        await db.conn.execute("DELETE FROM visu_nodes WHERE id = ?", (node_id,))


# ── Breadcrumb ────────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/breadcrumb", response_model=list[VisuNodeSummary])
async def get_breadcrumb(
    node_id: str,
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    principal = _principal_from_dependency(user)
    if not await _can_discover_node(db, node_id, principal):
        raise HTTPException(status_code=404, detail="Visu-Knoten nicht gefunden")
    rows = []
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute(
            """SELECT vn.*, avp.access_mode
               FROM visu_nodes AS vn
               LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
               WHERE vn.id = ?""",
            (current_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            break
        if await _can_discover_node(db, row["id"], principal):
            rows.insert(0, row)
        current_id = row["parent_id"]
    visible_ids = {row["id"] for row in rows}
    return [
        _row_to_summary(
            row,
            access=row["access_mode"] if "access_mode" in row.keys() else None,
            parent_id=row["parent_id"] if row["parent_id"] in visible_ids else None,
        )
        for row in rows
    ]


# ── Kinder ────────────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/children", response_model=list[VisuNodeSummary])
async def get_children(
    node_id: str,
    db: Database = Depends(get_db),
    user: Principal | str | None = Depends(_optional_visu_principal),
):
    principal = _principal_from_dependency(user)
    if not await _can_discover_node(db, node_id, principal):
        raise HTTPException(status_code=404, detail="Visu-Knoten nicht gefunden")
    async with db.conn.execute(
        """SELECT vn.*, avp.access_mode
           FROM visu_nodes AS vn
           LEFT JOIN authz_visu_page_policies AS avp ON avp.node_id = vn.id
           WHERE vn.parent_id = ?
           ORDER BY vn.node_order ASC""",
        (node_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        _row_to_summary(row, access=row["access_mode"] if "access_mode" in row.keys() else None)
        for row in rows
        if await _can_discover_node(db, row["id"], principal)
    ]


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
            (id, parent_id, name, type, node_order, icon,
             page_config, created_at, updated_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id,
            body.target_parent_id,
            body.new_name,
            source.type,
            source.order,
            source.icon,
            pc_json,
            now,
            now,
            _user if source.type == "PAGE" else None,
        ),
    )
    if source.access is not None:
        await db.conn.execute(
            "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)",
            (new_id, source.access),
        )
    await db.conn.commit()
    return await _get_node_or_404(db, new_id)


# ── Exportieren ──────────────────────────────────────────────────────────────


@router.get("/nodes/{node_id}/export")
async def export_node(
    node_id: str,
    db: Database = Depends(get_db),
    _user: Principal | str = Depends(get_current_principal),
) -> JSONResponse:
    """Exportiert den Knoten und alle Nachfolger rekursiv als JSON (ohne access_pin)."""
    principal = _principal_from_dependency(_user)

    async def collect(nid: str) -> list[dict]:
        if not await _can_discover_node(db, nid, principal):
            return []
        async with db.conn.execute("SELECT * FROM visu_nodes WHERE id = ?", (nid,)) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        access, _ = await _resolve_access_with_node(db, nid)
        if access == "user" and (principal is None or principal.type != "user"):
            return []
        page_config = json.loads(row["page_config"]) if row["page_config"] else None
        if access == "user" and row["type"] == "PAGE":
            config = PageConfig.model_validate(page_config or {})
            await _check_page_datapoint_policy(db, principal, _collect_page_datapoint_ids(config), AuthzAction.READ)
        policy = await db.fetchone("SELECT access_mode FROM authz_visu_page_policies WHERE node_id = ?", (nid,))
        result = [
            {
                "id": row["id"],
                "parent_id": row["parent_id"],
                "name": row["name"],
                "type": row["type"],
                "node_order": row["node_order"],
                "icon": row["icon"],
                "access": policy["access_mode"] if policy else None,
                "page_config": page_config,
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
    _user: Principal | str = Depends(get_current_principal),
):
    principal = _principal_from_mutation_dependency(_user)
    await _require_discoverable_node(db, node_id, principal)
    target_ids = await _visu_subtree_ids(db, node_id)
    if body.new_parent_id is not None:
        await _require_discoverable_node(db, body.new_parent_id, principal)
        target_ids.append(body.new_parent_id)
    await _require_visu_generate(db, principal, target_ids)
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
    node = await db.fetchone("SELECT 1 FROM visu_nodes WHERE id = ?", (node_id,))
    if not node:
        raise HTTPException(status_code=404, detail="Knoten nicht gefunden")
    access, defining_node_id = await _resolve_access_with_node(db, node_id)
    if access != "protected" or defining_node_id is None:
        raise HTTPException(status_code=400, detail="Knoten ist nicht PIN-gesichert")
    credential = await db.fetchone(
        "SELECT pin_hash FROM authz_visu_page_credentials WHERE node_id = ?",
        (defining_node_id,),
    )
    if not credential:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zugriff verweigert")
    if not bcrypt.checkpw(body.pin.encode(), credential["pin_hash"].encode()):
        raise HTTPException(status_code=401, detail="Falscher PIN")
    token = create_session(defining_node_id, expires_in=3600)
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
    request: Request,
    db: Database = Depends(get_db),
    _user: Principal | str = Depends(get_current_principal),
):
    principal = _principal_from_mutation_dependency(_user)
    node = await _require_discoverable_node(db, node_id, principal)
    if node.type != "PAGE":
        raise HTTPException(status_code=400, detail="Knoten ist keine Seite")

    used_capability = False
    try:
        if principal.type == "api_key":
            used_capability = await require_config_capability(
                db,
                principal,
                ConfigCapability.VISU_PAGE_CONFIG_WRITE,
                target_type="visu_page",
                target_id=node_id,
                request=request,
            )
        # An API key's exact page-config capability is the resource authority for
        # that page. Human principals still need an explicit GENERATE grant.
        if not used_capability:
            await _require_visu_generate(db, principal, [node_id])
        await _check_page_datapoint_policy(
            db,
            principal,
            sorted(set(_collect_page_datapoint_ids(node.page_config or PageConfig())) | set(_collect_page_datapoint_ids(config))),
            AuthzAction.GENERATE,
        )
        if principal.type == "api_key":
            await _check_page_write_access(db, node_id, principal)
    except HTTPException:
        if used_capability:
            await audit_config_capability_use(
                db,
                principal,
                ConfigCapability.VISU_PAGE_CONFIG_WRITE,
                target_type="visu_page",
                target_id=node_id,
                allowed=False,
                request=request,
            )
        raise

    access, defining_node_id = await _resolve_access_with_node(db, node_id)
    if access == "user" and defining_node_id is not None:
        await _check_user_page_target_datapoint_policy(db, defining_node_id, config)

    await db.conn.execute(
        "UPDATE visu_nodes SET page_config = ?, updated_at = ? WHERE id = ?",
        (config.model_dump_json(), _now_iso(), node_id),
    )
    await db.conn.commit()
    if used_capability:
        await audit_config_capability_use(
            db,
            principal,
            ConfigCapability.VISU_PAGE_CONFIG_WRITE,
            target_type="visu_page",
            target_id=node_id,
            allowed=True,
            request=request,
        )


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
        """SELECT principal_id
           FROM authz_node_roles
           WHERE principal_type='user' AND node_type='visu_page' AND node_id=?
             AND role='guest' AND effect='allow'
           ORDER BY principal_id""",
        (node_id,),
    )
    return [r["principal_id"] for r in rows]


@router.put("/nodes/{node_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def set_node_users(
    node_id: str,
    body: VisuNodeUsersUpdate,
    db: Database = Depends(get_db),
    _admin: Principal | str = Depends(get_current_principal),
):
    """Setzt die autorisierten Benutzer für diesen Knoten (ersetzt die gesamte Liste).
    Nur gültige (existierende, nicht-Admin) Benutzernamen werden gespeichert.
    """
    principal = _principal_from_mutation_dependency(_admin)
    async with db.transaction():
        await _require_discoverable_node(db, node_id, principal)
        await _require_visu_generate(db, principal, await _visu_subtree_ids(db, node_id))
        valid = await _validate_target_usernames(db, body.usernames)
        await _check_user_target_pages_datapoint_policy(db, node_id, usernames=valid)
        await _replace_target_users(db, node_id, valid)
