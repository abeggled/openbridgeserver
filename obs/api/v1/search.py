"""
Search API — Phase 4 / Issue #182

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
from obs.api.v1.datapoints import DataPointOut, _enrich, _SORT_KEYS
from obs.core.registry import get_registry
from obs.db.database import get_db, Database

router = APIRouter(tags=["search"])


class SearchPage(BaseModel):
    items: list[DataPointOut]
    total: int
    page: int
    size: int
    pages: int
    query: dict


@router.get("/", response_model=SearchPage)
async def search(
    q:       str = Query("",          description="Substring match on name, UUID, or binding config fields"),
    tag:     str = Query("",          description="Exact tag match"),
    type:    str = Query("",          description="data_type match"),
    adapter: str = Query("",          description="Has binding with this adapter_type"),
    quality: str = Query("",          description="Runtime quality filter: good | bad | uncertain"),
    sort:    str = Query("name",      pattern="^(name|data_type|created_at|updated_at)$"),
    order:   str = Query("asc",       pattern="^(asc|desc)$"),
    page:    int = Query(0,  ge=0),
    size:    int = Query(50, ge=1, le=500),
    _user:   str = Depends(get_current_user),
    db:      Database = Depends(lambda: get_db()),
) -> SearchPage:
    reg     = get_registry()
    results = reg.all()

    # 1. type filter (cheap, in-memory)
    if type:
        results = [dp for dp in results if dp.data_type == type]

    # 2. tag filter (cheap, in-memory)
    if tag:
        results = [dp for dp in results if tag in dp.tags]

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
        config_rows = await db.fetchall(
            "SELECT datapoint_id, config FROM adapter_bindings"
        )
        # Concatenate all config JSON strings per datapoint_id for substring search.
        binding_texts: dict[str, str] = {}
        for row in config_rows:
            dp_id_str = row["datapoint_id"]
            binding_texts[dp_id_str] = binding_texts.get(dp_id_str, "") + " " + (row["config"] or "").lower()

        def _matches(dp) -> bool:
            name_text   = dp.name.lower()
            uuid_text   = str(dp.id).lower()
            config_text = binding_texts.get(str(dp.id), "")
            return (
                all(t in name_text   for t in tokens)
                or all(t in uuid_text   for t in tokens)
                or all(t in config_text for t in tokens)
            )

        results = [dp for dp in results if _matches(dp)]

    # 5. quality filter (runtime, must come after cheaper filters)
    if quality:
        def _quality_of(dp) -> str:
            state = reg.get_value(dp.id)
            # DataPoints that have never received a value have no ValueState →
            # treat them as "uncertain", consistent with the /value endpoint.
            return state.quality if state else "uncertain"

        results = [dp for dp in results if _quality_of(dp) == quality]

    # 6. Sort
    results = sorted(results, key=_SORT_KEYS[sort], reverse=(order == "desc"))

    # 7. Paginate
    total  = len(results)
    offset = page * size
    items  = [_enrich(dp) for dp in results[offset : offset + size]]

    return SearchPage(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, (total + size - 1) // size),
        query={"q": q, "tag": tag, "type": type, "adapter": adapter, "quality": quality, "sort": sort, "order": order},
    )
