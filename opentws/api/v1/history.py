"""
History API — Phase 5

GET /api/v1/history/{id}?from=&to=&limit=
GET /api/v1/history/{id}/aggregate?fn=avg&interval=1h&from=&to=
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.core.registry import get_registry
from opentws.history.factory import get_history_plugin

router = APIRouter(tags=["history"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class HistoryPoint(BaseModel):
    ts: str
    v: Any
    u: str | None
    q: str
    a: str | None = None   # source_adapter


class AggregatedPoint(BaseModel):
    bucket: str
    v: Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(s: str | None, default: datetime) -> datetime:
    if not s:
        return default
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid timestamp: {s!r}",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{dp_id}", response_model=list[HistoryPoint])
async def query_history(
    dp_id: uuid.UUID,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(1000, ge=1, le=10000),
    _user: str = Depends(get_current_user),
) -> list[HistoryPoint]:
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    now = datetime.now(timezone.utc)
    from_dt = _parse_ts(from_ts, now - timedelta(hours=24))
    to_dt = _parse_ts(to_ts, now)

    plugin = get_history_plugin()
    rows = await plugin.query(dp_id, from_dt, to_dt, limit)
    return [HistoryPoint(**r) for r in rows]


@router.get("/{dp_id}/aggregate", response_model=list[AggregatedPoint])
async def aggregate_history(
    dp_id: uuid.UUID,
    fn: str = Query("avg", description="avg | min | max | last"),
    interval: str = Query("1h", description="1m | 5m | 15m | 30m | 1h | 6h | 12h | 1d"),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    _user: str = Depends(get_current_user),
) -> list[AggregatedPoint]:
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    if fn not in ("avg", "min", "max", "last"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "fn must be one of: avg, min, max, last",
        )

    now = datetime.now(timezone.utc)
    from_dt = _parse_ts(from_ts, now - timedelta(hours=24))
    to_dt = _parse_ts(to_ts, now)

    plugin = get_history_plugin()
    rows = await plugin.aggregate(dp_id, fn, interval, from_dt, to_dt)
    return [AggregatedPoint(**r) for r in rows]
