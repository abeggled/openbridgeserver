"""Reusable ETS hierarchy import service."""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import BaseModel

from obs.db.database import Database


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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid_mod.uuid4())


async def create_ets_hierarchy(db: Database, request: EtsImportRequest) -> ImportResult:
    """Create a hierarchy tree from already imported ETS data."""
    if request.mode not in ("groups", "mid", "flat", "buildings", "trades"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "mode muss 'groups', 'mid', 'flat', 'buildings' oder 'trades' sein",
        )

    now = _now()
    tree_id = _new_id()
    nodes_created = 0
    links_created = 0

    # Batch all inserts; commit once at the end for performance.
    inserts: list[tuple] = []

    def _q_insert(nid: str, parent_id: str | None, name: str, desc: str, order: int) -> None:
        inserts.append((nid, tree_id, parent_id, name, desc, order, None, now, now))

    if request.mode in ("groups", "mid", "flat"):
        rows = await db.fetchall("SELECT address, name, description, dpt, main_group_name, mid_group_name FROM knx_group_addresses ORDER BY address")
        if not rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine ETS-Gruppenadressen importiert. Bitte zuerst eine .knxproj oder CSV importieren.",
            )

        if request.mode == "mid":
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

        elif request.mode == "groups":
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

    elif request.mode == "buildings":
        loc_rows = await db.fetchall("SELECT id, parent_id, name, space_type, sort_order FROM knx_locations ORDER BY sort_order")
        if not loc_rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine Gebäude-Daten importiert. Bitte zuerst eine .knxproj importieren.",
            )

        loc_to_node: dict[str, str] = {}
        for loc in loc_rows:
            nid = _new_id()
            parent_nid = loc_to_node.get(loc["parent_id"]) if loc["parent_id"] else None
            _q_insert(nid, parent_nid, loc["name"] or loc["id"], loc["space_type"] or "", loc["sort_order"])
            loc_to_node[loc["id"]] = nid
            nodes_created += 1

        if request.auto_link:
            fn_rows = await db.fetchall(
                """SELECT f.space_id, l.ga_address
                   FROM knx_functions f
                   JOIN knx_function_ga_links l ON l.function_id = f.id"""
            )
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
                    inserts.append(("__link__", node_id, dp["id"]))

    else:  # "trades"
        trade_rows = await db.fetchall("SELECT id, name, parent_id, sort_order FROM knx_trades ORDER BY sort_order")
        if not trade_rows:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Keine Gewerke-Daten importiert. Bitte zuerst eine .knxproj importieren (die Datei muss einen <Trades>-Abschnitt enthalten).",
            )

        fn_count_row = await db.fetchone("SELECT COUNT(*) AS cnt FROM knx_functions WHERE trade_id IS NOT NULL")
        has_fn_links = fn_count_row and (fn_count_row["cnt"] or 0) > 0

        trade_id_to_nid: dict[str, str] = {}
        for trade in trade_rows:
            parent_trade_id = trade["parent_id"]
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

                if not request.auto_link:
                    continue

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

    node_inserts = [t for t in inserts if t[0] != "__link__"]
    link_sentinels = [t for t in inserts if t[0] == "__link__"]

    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
        (tree_id, request.tree_name, f"ets_import:{request.mode}", now, now),
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
        tree_name=request.tree_name,
        nodes_created=nodes_created,
        links_created=links_created,
        message=f"Hierarchiebaum '{request.tree_name}' mit {nodes_created} Knoten erstellt"
        + (f", {links_created} DataPoints automatisch verknüpft" if links_created else ""),
    )
