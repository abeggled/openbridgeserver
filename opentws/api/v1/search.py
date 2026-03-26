"""
Search API — Phase 4

GET /api/v1/search?q=&tag=&type=&adapter=&page=0&size=50

Server-side filtered search over DataPoints.
  q       — substring match on name (case-insensitive)
  tag     — exact tag match
  type    — data_type match (e.g. FLOAT)
  adapter — at least one binding with this adapter_type
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.api.v1.datapoints import DataPointOut, _enrich
from opentws.core.registry import get_registry
from opentws.db.database import get_db, Database

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
    q: str = Query("", description="Substring match on name"),
    tag: str = Query("", description="Exact tag match"),
    type: str = Query("", description="data_type match"),
    adapter: str = Query("", description="Has binding with this adapter_type"),
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> SearchPage:
    reg = get_registry()
    results = reg.all()

    if q:
        ql = q.lower()
        results = [dp for dp in results if ql in dp.name.lower()]
    if tag:
        results = [dp for dp in results if tag in dp.tags]
    if type:
        results = [dp for dp in results if dp.data_type == type]
    if adapter:
        # Filter: DataPoints that have at least one binding with this adapter_type
        rows = await db.fetchall(
            "SELECT DISTINCT datapoint_id FROM adapter_bindings WHERE adapter_type=?",
            (adapter,),
        )
        matched_ids = {r["datapoint_id"] for r in rows}
        results = [dp for dp in results if str(dp.id) in matched_ids]

    total = len(results)
    offset = page * size
    items = [_enrich(dp) for dp in results[offset : offset + size]]

    return SearchPage(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, (total + size - 1) // size),
        query={"q": q, "tag": tag, "type": type, "adapter": adapter},
    )
