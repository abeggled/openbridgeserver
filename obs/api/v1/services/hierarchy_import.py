"""Reusable ETS hierarchy import service."""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import BaseModel

from obs.db.database import Database

_GA_SCOPE_CHUNK_SIZE = 500


class EtsImportRequest(BaseModel):
    tree_name: str
    mode: str  # "groups" | "mid" | "flat" | "buildings" | "trades"
    auto_link: bool = True  # automatically link DataPoints via GA addresses
    replace_existing: bool = False  # replace existing auto-created ETS trees for this mode
    group_addresses: list[str] | None = None  # optional scope for current .knxproj import


class ImportResult(BaseModel):
    tree_id: str
    tree_name: str
    nodes_created: int
    links_created: int = 0
    trees_replaced: int = 0
    message: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid_mod.uuid4())


def _ets_import_description(mode: str) -> str:
    return f"ets_import:{mode}"


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


async def _replace_existing_ets_trees(db: Database, mode: str) -> int:
    """Delete auto-created ETS hierarchy trees for one mode, leaving manual trees untouched."""
    source = _ets_import_description(mode)
    rows = await db.fetchall(
        "SELECT id FROM hierarchy_trees WHERE source=?",
        (source,),
    )
    tree_ids = [row["id"] for row in rows]
    if not tree_ids:
        return 0

    placeholders = ",".join("?" * len(tree_ids))
    await db.execute_and_commit(
        f"DELETE FROM hierarchy_trees WHERE id IN ({placeholders})",
        tree_ids,
    )
    return len(tree_ids)


async def replace_existing_ets_trees(db: Database, mode: str) -> int:
    return await _replace_existing_ets_trees(db, mode)


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
    device_link_sentinels: set[tuple[str, str]] = set()

    def _q_insert(nid: str, parent_id: str | None, name: str, desc: str, order: int) -> None:
        inserts.append((nid, tree_id, parent_id, name, desc, order, None, now, now))

    if request.mode in ("groups", "mid", "flat"):
        if request.group_addresses is not None:
            scoped_addresses = list(dict.fromkeys(request.group_addresses))
            rows = []
            for chunk in _chunks(scoped_addresses, _GA_SCOPE_CHUNK_SIZE):
                placeholders = ",".join("?" * len(chunk))
                rows.extend(
                    await db.fetchall(
                        f"""SELECT address, name, description, dpt, main_group_name, mid_group_name
                            FROM knx_group_addresses
                            WHERE address IN ({placeholders})
                            ORDER BY address""",
                        chunk,
                    )
                )
        else:
            rows = await db.fetchall(
                "SELECT address, name, description, dpt, main_group_name, mid_group_name FROM knx_group_addresses ORDER BY address"
            )
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

        device_rows = await db.fetchall("SELECT space_id, device_id FROM knx_space_device_links")
        for row in device_rows:
            node_id = loc_to_node.get(row["space_id"])
            if not node_id:
                continue
            device_link_sentinels.add((node_id, row["device_id"]))

        inferred_device_rows = await db.fetchall(
            """SELECT co.device_id, MIN(f.space_id) AS space_id
               FROM knx_comm_objects co
               JOIN knx_co_ga_links cgl ON cgl.comm_object_id = co.id
               JOIN knx_function_ga_links fgl ON fgl.ga_address = cgl.ga_address
               JOIN knx_functions f ON f.id = fgl.function_id
               WHERE f.space_id IS NOT NULL AND f.space_id != ''
               GROUP BY co.device_id
               HAVING COUNT(DISTINCT f.space_id) = 1"""
        )
        for row in inferred_device_rows:
            node_id = loc_to_node.get(row["space_id"])
            if not node_id:
                continue
            device_link_sentinels.add((node_id, row["device_id"]))

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

    trees_replaced = 0
    if request.replace_existing:
        trees_replaced = await _replace_existing_ets_trees(db, request.mode)

    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (tree_id, request.tree_name, _ets_import_description(request.mode), _ets_import_description(request.mode), now, now),
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

    if device_link_sentinels:
        await db.executemany(
            "INSERT OR IGNORE INTO hierarchy_device_links (id, node_id, device_id, created_at) VALUES (?,?,?,?)",
            [(_new_id(), node_id, device_id, now) for node_id, device_id in device_link_sentinels],
        )

    await db.commit()

    return ImportResult(
        tree_id=tree_id,
        tree_name=request.tree_name,
        nodes_created=nodes_created,
        links_created=links_created,
        trees_replaced=trees_replaced,
        message=f"Hierarchiebaum '{request.tree_name}' mit {nodes_created} Knoten erstellt"
        + (f" ({trees_replaced} bestehende ETS-Hierarchien ersetzt)" if trees_replaced else "")
        + (f", {links_created} DataPoints automatisch verknüpft" if links_created else ""),
    )
