"""
RingBuffer API — Phase 5

GET  /api/v1/ringbuffer?q=&adapter=&from=&limit=    gefilterte Einträge
GET  /api/v1/ringbuffer/stats                        Statistik
POST /api/v1/ringbuffer/config                       Speicher umschalten
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.ringbuffer.ringbuffer import get_ringbuffer

router = APIRouter(tags=["ringbuffer"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RingBufferEntryOut(BaseModel):
    id: int
    ts: str
    datapoint_id: str
    topic: str
    old_value: Any
    new_value: Any
    source_adapter: str
    quality: str


class RingBufferStats(BaseModel):
    total: int
    oldest_ts: str | None
    newest_ts: str | None
    storage: str
    max_entries: int


class RingBufferConfig(BaseModel):
    storage: str = "memory"       # "memory" | "disk"
    max_entries: int = 10000


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[RingBufferEntryOut])
async def query_ringbuffer(
    q: str = Query("", description="Substring in datapoint_id or source_adapter"),
    adapter: str = Query("", description="Exact source_adapter match"),
    from_ts: str = Query("", alias="from", description="ISO-8601 timestamp (exclusive lower bound)"),
    limit: int = Query(100, ge=1, le=10000),
    _user: str = Depends(get_current_user),
) -> list[RingBufferEntryOut]:
    rb = get_ringbuffer()
    entries = await rb.query(q=q, adapter=adapter, from_ts=from_ts, limit=limit)
    return [
        RingBufferEntryOut(
            id=e.id, ts=e.ts, datapoint_id=e.datapoint_id, topic=e.topic,
            old_value=e.old_value, new_value=e.new_value,
            source_adapter=e.source_adapter, quality=e.quality,
        )
        for e in entries
    ]


@router.get("/stats", response_model=RingBufferStats)
async def ringbuffer_stats(
    _user: str = Depends(get_current_user),
) -> RingBufferStats:
    stats = await get_ringbuffer().stats()
    return RingBufferStats(**stats)


@router.post("/config", response_model=RingBufferStats)
async def configure_ringbuffer(
    body: RingBufferConfig,
    _user: str = Depends(get_current_user),
) -> RingBufferStats:
    """Switch storage mode and/or max_entries at runtime."""
    if body.storage not in ("memory", "disk"):
        from fastapi import HTTPException, status
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "storage must be 'memory' or 'disk'"
        )
    rb = get_ringbuffer()
    await rb.reconfigure(body.storage, body.max_entries)
    stats = await rb.stats()
    return RingBufferStats(**stats)
