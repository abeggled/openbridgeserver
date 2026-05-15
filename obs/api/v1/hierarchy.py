"""Hierarchy Manager API — /api/v1/hierarchy/...

Endpoints:
  GET    /hierarchy/trees                         → alle Äste (Trees)
  POST   /hierarchy/trees                         → Ast anlegen
  PUT    /hierarchy/trees/{tree_id}               → Ast umbenennen
  DELETE /hierarchy/trees/{tree_id}               → Ast löschen

  GET    /hierarchy/trees/{tree_id}/nodes         → Baumstruktur (nested)
  POST   /hierarchy/nodes                         → Knoten anlegen
  PUT    /hierarchy/nodes/{node_id}               → Knoten bearbeiten
  DELETE /hierarchy/nodes/{node_id}               → Knoten löschen
  PUT    /hierarchy/nodes/{node_id}/move          → Knoten verschieben

  GET    /hierarchy/nodes/{node_id}/datapoints    → verknüpfte DataPoints
  GET    /hierarchy/datapoints/{dp_id}/nodes      → Knoten eines DataPoints (alle Bäume)
  POST   /hierarchy/links                         → DataPoint-Knoten-Link anlegen
  DELETE /hierarchy/links                         → DataPoint-Knoten-Link entfernen

  POST   /hierarchy/import-from-ets               → Baum aus ETS-GA-Struktur erzeugen
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from obs.api.auth import get_current_user
from obs.api.v1.datapoints import NodePathSegment
from obs.db.database import Database, get_db

router = APIRouter(tags=["hierarchy"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid_mod.uuid4())


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class HierarchyTree(BaseModel):
    id: str
    name: str
    description: str
    display_depth: int
    created_at: str
    updated_at: str


class HierarchyTreeCreate(BaseModel):
    name: str
    description: str = ""
    display_depth: int = 0


class HierarchyTreeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    display_depth: int | None = None


class HierarchyNode(BaseModel):
    id: str
    tree_id: str
    parent_id: str | None
    name: str
    description: str
    order: int
    icon: str | None
    created_at: str
    updated_at: str
    children: list["HierarchyNode"] = []


class HierarchyNodeCreate(BaseModel):
    tree_id: str
    parent_id: str | None = None
    name: str
    description: str = ""
    order: int = 0
    icon: str | None = None


class HierarchyNodeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    order: int | None = None
    icon: str | None = None


class HierarchyNodeMove(BaseModel):
    new_parent_id: str | None = None
    new_order: int = 0


class HierarchyLinkCreate(BaseModel):
    node_id: str
    datapoint_id: str


class HierarchyLinkDelete(BaseModel):
    node_id: str
    datapoint_id: str


class DataPointRef(BaseModel):
    id: str
    name: str
    data_type: str
    unit: str | None
    link_id: str


class NodeRef(BaseModel):
    link_id: str
    node_id: str
    node_name: str
    tree_id: str
    tree_name: str
    node_path: list[NodePathSegment] = []


class NodeSearchResult(BaseModel):
    node_id: str
    node_name: str
    tree_id: str
    tree_name: str


class EtsImportRequest(BaseModel):
    tree_name: str
    mode: str  # "groups" | "mid" | "flat" | "buildings" | "trades"
    auto_link: bool = True  # automatically link DataPoints via GA addresses


class ImportResult(BaseModel):
    tree_id: str
    tree_name: str
    nodes_created: int
    links_created: int = 0
    message: str


# ---------------------------------------------------------------------------
# Row → Model helpers
# ---------------------------------------------------------------------------


def _row_to_tree(row: Any) -> HierarchyTree:
    return HierarchyTree(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        display_depth=row["display_depth"] if row["display_depth"] is not None else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_node(row: Any) -> HierarchyNode:
    return HierarchyNode(
        id=row["id"],
        tree_id=row["tree_id"],
        parent_id=row["parent_id"],
        name=row["name"],
        description=row["description"],
        order=row["node_order"],
        icon=row["icon"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_tree(nodes: list[HierarchyNode]) -> list[HierarchyNode]:
    """Flache Liste → verschachtelte Baumstruktur."""
    by_id = {n.id: n for n in nodes}
    roots: list[HierarchyNode] = []
    for node in nodes:
        if node.parent_id and node.parent_id in by_id:
            by_id[node.parent_id].children.append(node)
        else:
            roots.append(node)
    # sort children by order
    for node in nodes:
        node.children.sort(key=lambda n: n.order)
    roots.sort(key=lambda n: n.order)
    return roots


# ---------------------------------------------------------------------------
# Tree Endpoints
# ---------------------------------------------------------------------------


@router.get("/trees", response_model=list[HierarchyTree])
async def list_trees(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[HierarchyTree]:
    rows = await db.fetchall("SELECT * FROM hierarchy_trees ORDER BY name")
    return [_row_to_tree(r) for r in rows]


@router.post("/trees", response_model=HierarchyTree, status_code=status.HTTP_201_CREATED)
async def create_tree(
    body: HierarchyTreeCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> HierarchyTree:
    now = _now()
    tid = _new_id()
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, display_depth, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (tid, body.name, body.description, body.display_depth, now, now),
    )
    row = await db.fetchone("SELECT * FROM hierarchy_trees WHERE id=?", (tid,))
    return _row_to_tree(row)


@router.put("/trees/{tree_id}", response_model=HierarchyTree)
async def update_tree(
    tree_id: str,
    body: HierarchyTreeUpdate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> HierarchyTree:
    row = await db.fetchone("SELECT * FROM hierarchy_trees WHERE id=?", (tree_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hierarchiebaum nicht gefunden")
    name = body.name if body.name is not None else row["name"]
    desc = body.description if body.description is not None else row["description"]
    depth = body.display_depth if body.display_depth is not None else (row["display_depth"] or 0)
    now = _now()
    await db.execute_and_commit(
        "UPDATE hierarchy_trees SET name=?, description=?, display_depth=?, updated_at=? WHERE id=?",
        (name, desc, depth, now, tree_id),
    )
    row = await db.fetchone("SELECT * FROM hierarchy_trees WHERE id=?", (tree_id,))
    return _row_to_tree(row)


@router.delete("/trees/{tree_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tree(
    tree_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> None:
    row = await db.fetchone("SELECT id FROM hierarchy_trees WHERE id=?", (tree_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hierarchiebaum nicht gefunden")
    await db.execute_and_commit("DELETE FROM hierarchy_trees WHERE id=?", (tree_id,))


# ---------------------------------------------------------------------------
# Node Endpoints
# ---------------------------------------------------------------------------


@router.get("/trees/{tree_id}/nodes", response_model=list[HierarchyNode])
async def get_tree_nodes(
    tree_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[HierarchyNode]:
    """Gibt den Baum als verschachtelte Struktur zurück."""
    tree = await db.fetchone("SELECT id FROM hierarchy_trees WHERE id=?", (tree_id,))
    if not tree:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hierarchiebaum nicht gefunden")
    rows = await db.fetchall(
        "SELECT * FROM hierarchy_nodes WHERE tree_id=? ORDER BY node_order, name",
        (tree_id,),
    )
    flat = [_row_to_node(r) for r in rows]
    return _build_tree(flat)


@router.post("/nodes", response_model=HierarchyNode, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: HierarchyNodeCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> HierarchyNode:
    # Baum muss existieren
    tree = await db.fetchone("SELECT id FROM hierarchy_trees WHERE id=?", (body.tree_id,))
    if not tree:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hierarchiebaum nicht gefunden")
    # Parent muss im selben Baum liegen
    if body.parent_id:
        parent = await db.fetchone("SELECT tree_id FROM hierarchy_nodes WHERE id=?", (body.parent_id,))
        if not parent or parent["tree_id"] != body.tree_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Elternknoten nicht im gleichen Baum")

    now = _now()
    nid = _new_id()
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
           (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (nid, body.tree_id, body.parent_id, body.name, body.description, body.order, body.icon, now, now),
    )
    row = await db.fetchone("SELECT * FROM hierarchy_nodes WHERE id=?", (nid,))
    return _row_to_node(row)


@router.put("/nodes/{node_id}", response_model=HierarchyNode)
async def update_node(
    node_id: str,
    body: HierarchyNodeUpdate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> HierarchyNode:
    row = await db.fetchone("SELECT * FROM hierarchy_nodes WHERE id=?", (node_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knoten nicht gefunden")
    name = body.name if body.name is not None else row["name"]
    desc = body.description if body.description is not None else row["description"]
    order = body.order if body.order is not None else row["node_order"]
    icon = body.icon if body.icon is not None else row["icon"]
    now = _now()
    await db.execute_and_commit(
        "UPDATE hierarchy_nodes SET name=?, description=?, node_order=?, icon=?, updated_at=? WHERE id=?",
        (name, desc, order, icon, now, node_id),
    )
    row = await db.fetchone("SELECT * FROM hierarchy_nodes WHERE id=?", (node_id,))
    return _row_to_node(row)


@router.put("/nodes/{node_id}/move", response_model=HierarchyNode)
async def move_node(
    node_id: str,
    body: HierarchyNodeMove,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> HierarchyNode:
    row = await db.fetchone("SELECT * FROM hierarchy_nodes WHERE id=?", (node_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knoten nicht gefunden")
    tree_id = row["tree_id"]

    if body.new_parent_id:
        parent = await db.fetchone("SELECT tree_id FROM hierarchy_nodes WHERE id=?", (body.new_parent_id,))
        if not parent or parent["tree_id"] != tree_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Zielknoten nicht im gleichen Baum")
        # Zirkuläre Abhängigkeit verhindern
        if body.new_parent_id == node_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Knoten kann nicht sein eigener Elter sein")

    now = _now()
    await db.execute_and_commit(
        "UPDATE hierarchy_nodes SET parent_id=?, node_order=?, updated_at=? WHERE id=?",
        (body.new_parent_id, body.new_order, now, node_id),
    )
    row = await db.fetchone("SELECT * FROM hierarchy_nodes WHERE id=?", (node_id,))
    return _row_to_node(row)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> None:
    row = await db.fetchone("SELECT id FROM hierarchy_nodes WHERE id=?", (node_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knoten nicht gefunden")
    await db.execute_and_commit("DELETE FROM hierarchy_nodes WHERE id=?", (node_id,))


# ---------------------------------------------------------------------------
# Link Endpoints (DataPoint ↔ Node)
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}/datapoints", response_model=list[DataPointRef])
async def get_node_datapoints(
    node_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[DataPointRef]:
    node = await db.fetchone("SELECT id FROM hierarchy_nodes WHERE id=?", (node_id,))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knoten nicht gefunden")
    rows = await db.fetchall(
        """SELECT hdl.id AS link_id, dp.id, dp.name, dp.data_type, dp.unit
           FROM hierarchy_datapoint_links hdl
           JOIN datapoints dp ON dp.id = hdl.datapoint_id
           WHERE hdl.node_id=?
           ORDER BY dp.name""",
        (node_id,),
    )
    return [
        DataPointRef(
            id=r["id"],
            name=r["name"],
            data_type=r["data_type"],
            unit=r["unit"],
            link_id=r["link_id"],
        )
        for r in rows
    ]


@router.get("/datapoints/{dp_id}/nodes", response_model=list[NodeRef])
async def get_datapoint_nodes(
    dp_id: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[NodeRef]:
    rows = await db.fetchall(
        """SELECT hdl.id AS link_id, hn.id AS node_id, hn.name AS node_name,
                  ht.id AS tree_id, ht.name AS tree_name
           FROM hierarchy_datapoint_links hdl
           JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
           JOIN hierarchy_trees ht ON ht.id = hn.tree_id
           WHERE hdl.datapoint_id=?
           ORDER BY ht.name, hn.name""",
        (dp_id,),
    )
    node_ids = [r["node_id"] for r in rows]
    node_paths: dict[str, list[NodePathSegment]] = {}
    if node_ids:
        ph = ",".join("?" * len(node_ids))
        path_rows = await db.fetchall(
            f"""WITH RECURSIVE anc(leaf_id, cur_id, cur_name, cur_parent, depth) AS (
                SELECT id, id, name, parent_id, 0 FROM hierarchy_nodes WHERE id IN ({ph})
                UNION ALL
                SELECT a.leaf_id, hn2.id, hn2.name, hn2.parent_id, a.depth + 1
                FROM anc a JOIN hierarchy_nodes hn2 ON hn2.id = a.cur_parent
                WHERE a.cur_parent IS NOT NULL
            )
            SELECT leaf_id, cur_id, cur_name FROM anc WHERE depth > 0
            ORDER BY leaf_id, depth DESC""",
            node_ids,
        )
        for r in path_rows:
            node_paths.setdefault(r["leaf_id"], []).append(NodePathSegment(node_id=r["cur_id"], node_name=r["cur_name"]))
    return [
        NodeRef(
            link_id=r["link_id"],
            node_id=r["node_id"],
            node_name=r["node_name"],
            tree_id=r["tree_id"],
            tree_name=r["tree_name"],
            node_path=node_paths.get(r["node_id"], []),
        )
        for r in rows
    ]


@router.post("/links", status_code=status.HTTP_201_CREATED)
async def create_link(
    body: HierarchyLinkCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict:
    node = await db.fetchone("SELECT id FROM hierarchy_nodes WHERE id=?", (body.node_id,))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knoten nicht gefunden")
    dp = await db.fetchone("SELECT id FROM datapoints WHERE id=?", (body.datapoint_id,))
    if not dp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DataPoint nicht gefunden")

    existing = await db.fetchone(
        "SELECT id FROM hierarchy_datapoint_links WHERE node_id=? AND datapoint_id=?",
        (body.node_id, body.datapoint_id),
    )
    if existing:
        return {"id": existing["id"], "node_id": body.node_id, "datapoint_id": body.datapoint_id}

    lid = _new_id()
    now = _now()
    await db.execute_and_commit(
        "INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at) VALUES (?,?,?,?)",
        (lid, body.node_id, body.datapoint_id, now),
    )
    return {"id": lid, "node_id": body.node_id, "datapoint_id": body.datapoint_id}


@router.delete("/links", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    node_id: str = Query(...),
    datapoint_id: str = Query(...),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> None:
    await db.execute_and_commit(
        "DELETE FROM hierarchy_datapoint_links WHERE node_id=? AND datapoint_id=?",
        (node_id, datapoint_id),
    )


# ---------------------------------------------------------------------------
# Node Search
# ---------------------------------------------------------------------------


@router.get("/nodes/search", response_model=list[NodeSearchResult])
async def search_nodes(
    q: str = Query("", description="Volltext-Suche in Knoten- und Ast-Namen"),
    limit: int = Query(30, ge=1, le=200),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[NodeSearchResult]:
    """Knoten über alle Äste hinweg suchen. Gibt Knoten mit Ast-Kontext zurück."""
    if q:
        like = f"%{q}%"
        rows = await db.fetchall(
            """SELECT hn.id AS node_id, hn.name AS node_name,
                      ht.id AS tree_id, ht.name AS tree_name
               FROM hierarchy_nodes hn
               JOIN hierarchy_trees ht ON ht.id = hn.tree_id
               WHERE hn.name LIKE ? OR ht.name LIKE ?
               ORDER BY ht.name, hn.name
               LIMIT ?""",
            (like, like, limit),
        )
    else:
        rows = await db.fetchall(
            """SELECT hn.id AS node_id, hn.name AS node_name,
                      ht.id AS tree_id, ht.name AS tree_name
               FROM hierarchy_nodes hn
               JOIN hierarchy_trees ht ON ht.id = hn.tree_id
               ORDER BY ht.name, hn.name
               LIMIT ?""",
            (limit,),
        )
    return [NodeSearchResult(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# ETS Import → Hierarchy
# ---------------------------------------------------------------------------


@router.post("/import-from-ets", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
async def import_from_ets(
    body: EtsImportRequest,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    """Erzeugt einen neuen Hierarchiebaum aus importierten ETS-Gruppenadressen.

    Modes:
      groups — Hauptgruppe → Mittelgruppe → GA (3-stufig)
      flat   — Hauptgruppe → GA (2-stufig, Mittelgruppen werden übersprungen)

    Knotenbezeichnungen werden aus den ETS-Gruppenadress-Bereichen übernommen
    (main_group_name / mid_group_name). Fehlen diese (CSV-Import), wird
    "Hauptgruppe X" bzw. "Mittelgruppe X" als Fallback verwendet.
    """
    if body.mode not in ("groups", "mid", "flat", "buildings", "trades"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "mode muss 'groups', 'mid', 'flat', 'buildings' oder 'trades' sein",
        )

    now = _now()
    tree_id = _new_id()
    nodes_created = 0
    links_created = 0

    # Batch all inserts — commit once at the end for performance
    inserts: list[tuple] = []

    def _q_insert(nid: str, parent_id: str | None, name: str, desc: str, order: int) -> None:
        inserts.append((nid, tree_id, parent_id, name, desc, order, None, now, now))

    if body.mode in ("groups", "mid", "flat"):
        rows = await db.fetchall("SELECT address, name, description, dpt, main_group_name, mid_group_name FROM knx_group_addresses ORDER BY address")
        if not rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine ETS-Gruppenadressen importiert. Bitte zuerst eine .knxproj oder CSV importieren.",
            )

        if body.mode == "mid":
            main_nodes: dict[str, str] = {}
            mid_nodes: dict[str, str] = {}
            for row in rows:
                parts = str(row["address"]).split("/")
                if len(parts) < 2:
                    continue
                main_key, mid_key = parts[0], parts[1]
                mid_composite = f"{main_key}/{mid_key}"
                if main_key not in main_nodes:
                    nid = _new_id()
                    main_label = str(row["main_group_name"] or "").strip() or f"Hauptgruppe {main_key}"
                    _q_insert(nid, None, main_label, "", int(main_key))
                    main_nodes[main_key] = nid
                    nodes_created += 1
                if mid_composite not in mid_nodes:
                    nid = _new_id()
                    mid_label = str(row["mid_group_name"] or "").strip() or f"Mittelgruppe {mid_key}"
                    _q_insert(nid, main_nodes[main_key], mid_label, "", int(mid_key))
                    mid_nodes[mid_composite] = nid
                    nodes_created += 1

        elif body.mode == "groups":
            main_nodes = {}
            mid_nodes = {}
            for row in rows:
                parts = str(row["address"]).split("/")
                if len(parts) != 3:
                    continue
                main_key, mid_key, _ = parts
                mid_composite = f"{main_key}/{mid_key}"
                if main_key not in main_nodes:
                    nid = _new_id()
                    main_label = str(row["main_group_name"] or "").strip() or f"Hauptgruppe {main_key}"
                    _q_insert(nid, None, main_label, "", int(main_key))
                    main_nodes[main_key] = nid
                    nodes_created += 1
                if mid_composite not in mid_nodes:
                    nid = _new_id()
                    mid_label = str(row["mid_group_name"] or "").strip() or f"Mittelgruppe {mid_key}"
                    _q_insert(nid, main_nodes[main_key], mid_label, "", int(mid_key))
                    mid_nodes[mid_composite] = nid
                    nodes_created += 1
                ga_name = str(row["name"]).strip() or row["address"]
                nid = _new_id()
                _q_insert(nid, mid_nodes[mid_composite], ga_name, str(row["description"] or ""), 0)
                nodes_created += 1

        else:  # "flat"
            main_nodes = {}
            for row in rows:
                parts = str(row["address"]).split("/")
                main_key = parts[0]
                if main_key not in main_nodes:
                    nid = _new_id()
                    main_label = str(row["main_group_name"] or "").strip() or f"Hauptgruppe {main_key}"
                    _q_insert(nid, None, main_label, "", int(main_key))
                    main_nodes[main_key] = nid
                    nodes_created += 1
                ga_name = str(row["name"]).strip() or row["address"]
                nid = _new_id()
                _q_insert(nid, main_nodes[main_key], ga_name, str(row["description"] or ""), 0)
                nodes_created += 1

    elif body.mode == "buildings":
        # Gebäude-Hierarchie aus knx_locations
        loc_rows = await db.fetchall("SELECT id, parent_id, name, space_type, sort_order FROM knx_locations ORDER BY sort_order")
        if not loc_rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine Gebäude-Daten importiert. Bitte zuerst eine .knxproj importieren.",
            )

        # map ETS location id → hierarchy node id
        loc_to_node: dict[str, str] = {}
        for loc in loc_rows:
            nid = _new_id()
            parent_nid = loc_to_node.get(loc["parent_id"]) if loc["parent_id"] else None
            _q_insert(nid, parent_nid, loc["name"] or loc["id"], loc["space_type"] or "", loc["sort_order"])
            loc_to_node[loc["id"]] = nid
            nodes_created += 1

        # Auto-link DataPoints via GA addresses in functions linked to spaces
        if body.auto_link:
            fn_rows = await db.fetchall(
                """SELECT f.space_id, l.ga_address
                   FROM knx_functions f
                   JOIN knx_function_ga_links l ON l.function_id = f.id"""
            )
            # space_id → set of GA addresses
            space_gas: dict[str, set[str]] = {}
            for fr in fn_rows:
                space_gas.setdefault(fr["space_id"], set()).add(fr["ga_address"])

            for space_id, gas in space_gas.items():
                node_id = loc_to_node.get(space_id)
                if not node_id or not gas:
                    continue
                placeholders = ",".join("?" * len(gas))
                dp_rows = await db.fetchall(
                    f"""SELECT DISTINCT dp.id
                        FROM datapoints dp
                        JOIN adapter_bindings ab ON ab.datapoint_id = dp.id
                        WHERE UPPER(ab.adapter_type) = 'KNX'
                          AND JSON_EXTRACT(ab.config, '$.group_address') IN ({placeholders})""",
                    list(gas),
                )
                for dp in dp_rows:
                    links_created += 1
                    inserts.append(("__link__", node_id, dp["id"]))  # sentinel — handled below

    else:  # "trades" — Gewerke aus knx_trades Tabelle (ETS <Trades> XML-Sektion)
        trade_rows = await db.fetchall("SELECT id, name, parent_id, sort_order FROM knx_trades ORDER BY sort_order")
        if not trade_rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine Gewerke-Daten importiert. Bitte zuerst eine .knxproj importieren (die Datei muss einen <Trades>-Abschnitt enthalten).",
            )

        # Check whether function→trade links exist (populated during knxproj upload)
        fn_count_row = await db.fetchone("SELECT COUNT(*) AS cnt FROM knx_functions WHERE trade_id IS NOT NULL")
        has_fn_links = fn_count_row and (fn_count_row["cnt"] or 0) > 0

        # Build trade hierarchy: ETS trade_id → hierarchy node_id
        # Trades are ordered by sort_order (pre-order from recursive parse), so parents come first.
        trade_id_to_nid: dict[str, str] = {}

        for trade in trade_rows:
            parent_trade_id = trade["parent_id"]  # ETS parent trade ID or None
            parent_nid = trade_id_to_nid.get(parent_trade_id) if parent_trade_id else None
            trade_nid = _new_id()
            _q_insert(trade_nid, parent_nid, trade["name"] or trade["id"], "", trade["sort_order"])
            trade_id_to_nid[trade["id"]] = trade_nid
            nodes_created += 1

            if not has_fn_links:
                continue

            fn_rows = await db.fetchall(
                "SELECT id, name, usage_text FROM knx_functions WHERE trade_id = ? ORDER BY name",
                (trade["id"],),
            )
            for fn in fn_rows:
                fn_nid = _new_id()
                fn_label = fn["name"] or fn["id"]
                fn_desc = fn["usage_text"] or ""
                _q_insert(fn_nid, trade_nid, fn_label, fn_desc, nodes_created)
                nodes_created += 1

                if not body.auto_link:
                    continue

                # Auto-link DataPoints via GA addresses stored in knx_function_ga_links
                ga_rows = await db.fetchall(
                    "SELECT ga_address FROM knx_function_ga_links WHERE function_id = ?",
                    (fn["id"],),
                )
                gas = [r["ga_address"] for r in ga_rows if r["ga_address"]]
                if not gas:
                    continue

                placeholders = ",".join("?" * len(gas))
                dp_rows = await db.fetchall(
                    f"""SELECT DISTINCT dp.id
                        FROM datapoints dp
                        JOIN adapter_bindings ab ON ab.datapoint_id = dp.id
                        WHERE UPPER(ab.adapter_type) = 'KNX'
                          AND JSON_EXTRACT(ab.config, '$.group_address') IN ({placeholders})""",
                    gas,
                )
                for dp in dp_rows:
                    links_created += 1
                    inserts.append(("__link__", fn_nid, dp["id"]))

    # Separate node inserts from link sentinels
    node_inserts = [t for t in inserts if t[0] != "__link__"]
    link_sentinels = [t for t in inserts if t[0] == "__link__"]

    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
        (tree_id, body.tree_name, f"Importiert aus ETS ({body.mode})", now, now),
    )

    if node_inserts:
        await db.executemany(
            """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            node_inserts,
        )

    if link_sentinels:
        link_rows = [(_new_id(), node_id, dp_id, now) for (_, node_id, dp_id) in link_sentinels]
        await db.executemany(
            "INSERT OR IGNORE INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at) VALUES (?,?,?,?)",
            link_rows,
        )

    await db.commit()

    return ImportResult(
        tree_id=tree_id,
        tree_name=body.tree_name,
        nodes_created=nodes_created,
        links_created=links_created,
        message=f"Hierarchiebaum '{body.tree_name}' mit {nodes_created} Knoten erstellt"
        + (f", {links_created} DataPoints automatisch verknüpft" if links_created else ""),
    )
