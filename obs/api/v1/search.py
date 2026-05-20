"""Search API — Phase 4 / Issue #182

GET /api/v1/search?q=&tag=&type=&adapter=&quality=&sort=name&order=asc&page=0&size=50

Server-side filtered search over DataPoints.
  q       — substring match on name OR UUID OR any binding config field (case-insensitive)
  tag     — exact tag match
  type    — data_type match (e.g. FLOAT)
  adapter — at least one binding with this adapter_type
  quality — runtime quality filter: good | bad | uncertain
  sort    — sort column: name | data_type | created_at | updated_at  (default: name)
  order   — sort direction: asc | desc                               (default: asc)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from obs.api.auth import get_current_user
from obs.api.v1.datapoints import _SORT_KEYS, DataPointOut, HierarchyNodeRef, NodePathSegment, _enrich
from obs.core.registry import get_registry
from obs.db.database import Database, get_db

router = APIRouter(tags=["search"])


class SearchPage(BaseModel):
    items: list[DataPointOut]
    total: int
    page: int
    size: int
    pages: int
    query: dict


async def _add_hierarchy(items: list[DataPointOut], db: Database) -> None:
    """Batch-query hierarchy node links and inject into items in-place.

    Also computes each node's ancestor path (root → leaf, excluding tree name)
    so the frontend can disambiguate same-named leaves under different parents
    (e.g. "Gebäude › EG › Küche" vs "Gebäude › OG › Küche") — see #433.
    """
    if not items:
        return
    dp_ids = [str(item.id) for item in items]
    placeholders = ",".join("?" * len(dp_ids))
    rows = await db.fetchall(
        f"""SELECT hdl.datapoint_id, hn.id AS node_id, hn.name AS node_name,
                   ht.id AS tree_id, ht.name AS tree_name, ht.display_depth
            FROM hierarchy_datapoint_links hdl
            JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
            JOIN hierarchy_trees ht ON ht.id = hn.tree_id
            WHERE hdl.datapoint_id IN ({placeholders})
            ORDER BY ht.name, hn.name""",
        dp_ids,
    )
    # Build ancestor paths for all linked nodes via recursive CTE (upstream
    # PR #462) — produces the richer node_path schema (objects with stable
    # node_id + node_name per segment) that the epic switched to during the
    # merge. The epic's earlier in-memory walker over a full hierarchy_nodes
    # SELECT is dropped: the CTE scales with the actual matched node set
    # instead of the whole tree.
    node_ids = list({r["node_id"] for r in rows})
    node_paths: dict[str, list[NodePathSegment]] = {}
    if node_ids:
        ph2 = ",".join("?" * len(node_ids))
        path_rows = await db.fetchall(
            f"""WITH RECURSIVE anc(leaf_id, cur_id, cur_name, cur_parent, depth) AS (
                SELECT id, id, name, parent_id, 0 FROM hierarchy_nodes WHERE id IN ({ph2})
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

    by_dp: dict[str, list[HierarchyNodeRef]] = {}
    for r in rows:
        by_dp.setdefault(r["datapoint_id"], []).append(
            HierarchyNodeRef(
                node_id=r["node_id"],
                node_name=r["node_name"],
                tree_id=r["tree_id"],
                tree_name=r["tree_name"],
                node_path=node_paths.get(r["node_id"], []),
                display_depth=r["display_depth"] if r["display_depth"] is not None else 0,
            )
        )
    for item in items:
        item.hierarchy_nodes = by_dp.get(str(item.id), [])


@router.get("/", response_model=SearchPage)
async def search(
    q: str = Query("", description="Substring match on name, UUID, or binding config fields"),
    tag: str = Query("", description="Comma-separated tag list — OR logic (e.g. 'heating,lighting')"),
    type: str = Query("", description="data_type match"),
    adapter: str = Query("", description="Has binding with this adapter_type"),
    quality: str = Query("", description="Runtime quality filter: good | bad | uncertain"),
    node_id: str = Query("", description="Comma-separated node IDs — OR logic"),
    tree_id: str = Query("", description="Comma-separated tree IDs — matches any node in these trees"),
    sort: str = Query("name", pattern="^(name|data_type|created_at|updated_at)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> SearchPage:
    reg = get_registry()
    results = reg.all()

    # 1. type filter (cheap, in-memory)
    if type:
        results = [dp for dp in results if dp.data_type == type]

    # 2. tag filter (cheap, in-memory) — comma-separated, OR logic
    if tag:
        tag_list = [t.strip() for t in tag.split(",") if t.strip()]
        results = [dp for dp in results if any(t in dp.tags for t in tag_list)]

    # 3. adapter filter (one DB query)
    if adapter:
        rows = await db.fetchall(
            "SELECT DISTINCT datapoint_id FROM adapter_bindings WHERE adapter_type=?",
            (adapter,),
        )
        matched_ids = {r["datapoint_id"] for r in rows}
        results = [dp for dp in results if str(dp.id) in matched_ids]

    # 4. q filter: all-token match on name, UUID, or binding config text (one DB query)
    #
    # The query is split into whitespace-separated tokens.  A DataPoint matches
    # if every token appears in the name  — OR  every token appears in the UUID
    # — OR every token appears in the concatenated binding config text.
    # This lets "u04 temperatur" find "U04 Präsenzmelder 01 Temperatur" even
    # though the words are not adjacent.
    if q:
        tokens = q.lower().split()

        # Pre-fetch all binding configs in one query to avoid N+1 DB hits.
        config_rows = await db.fetchall("SELECT datapoint_id, config FROM adapter_bindings")
        # Concatenate all config JSON strings per datapoint_id for substring search.
        binding_texts: dict[str, str] = {}
        for row in config_rows:
            dp_id_str = row["datapoint_id"]
            binding_texts[dp_id_str] = binding_texts.get(dp_id_str, "") + " " + (row["config"] or "").lower()

        def _matches(dp) -> bool:
            name_text = dp.name.lower()
            uuid_text = str(dp.id).lower()
            config_text = binding_texts.get(str(dp.id), "")
            return all(t in name_text for t in tokens) or all(t in uuid_text for t in tokens) or all(t in config_text for t in tokens)

        results = [dp for dp in results if _matches(dp)]

    # 5a. node_id filter — includes selected nodes AND all their descendants
    if node_id:
        node_id_list = [n.strip() for n in node_id.split(",") if n.strip()]
        if node_id_list:
            placeholders = ",".join("?" * len(node_id_list))
            rows = await db.fetchall(
                f"""WITH RECURSIVE desc(id) AS (
                    SELECT id FROM hierarchy_nodes WHERE id IN ({placeholders})
                    UNION ALL
                    SELECT hn.id FROM hierarchy_nodes hn JOIN desc d ON hn.parent_id = d.id
                )
                SELECT DISTINCT hdl.datapoint_id
                FROM hierarchy_datapoint_links hdl
                JOIN desc d ON hdl.node_id = d.id""",
                node_id_list,
            )
            matched_ids = {r["datapoint_id"] for r in rows}
            results = [dp for dp in results if str(dp.id) in matched_ids]

    # 5b. tree_id filter (all nodes in these trees — OR logic)
    if tree_id:
        tree_id_list = [t.strip() for t in tree_id.split(",") if t.strip()]
        if tree_id_list:
            placeholders = ",".join("?" * len(tree_id_list))
            rows = await db.fetchall(
                f"""SELECT DISTINCT hdl.datapoint_id
                    FROM hierarchy_datapoint_links hdl
                    JOIN hierarchy_nodes hn ON hn.id = hdl.node_id
                    WHERE hn.tree_id IN ({placeholders})""",
                tree_id_list,
            )
            matched_ids = {r["datapoint_id"] for r in rows}
            results = [dp for dp in results if str(dp.id) in matched_ids]

    # 6. quality filter (runtime, must come after cheaper filters)
    if quality:

        def _quality_of(dp) -> str:
            state = reg.get_value(dp.id)
            # DataPoints that have never received a value have no ValueState →
            # treat them as "uncertain", consistent with the /value endpoint.
            return state.quality if state else "uncertain"

        results = [dp for dp in results if _quality_of(dp) == quality]

    # 7. Sort
    results = sorted(results, key=_SORT_KEYS[sort], reverse=(order == "desc"))

    # 8. Paginate
    total = len(results)
    offset = page * size
    items = [_enrich(dp) for dp in results[offset : offset + size]]

    # 9. Enrich with hierarchy node assignments (single batch query)
    await _add_hierarchy(items, db)

    return SearchPage(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, (total + size - 1) // size),
        query={
            "q": q,
            "tag": tag,
            "type": type,
            "adapter": adapter,
            "quality": quality,
            "node_id": node_id,
            "tree_id": tree_id,
            "sort": sort,
            "order": order,
        },
    )
