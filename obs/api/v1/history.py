"""History API — Phase 5

GET /api/v1/history/{id}?from=&to=&limit=
GET /api/v1/history/{id}/aggregate?fn=avg&interval=1h&from=&to=
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from obs.api.auth import optional_current_user
from obs.api.v1.sessions import validate_session
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.history.factory import get_history_plugin

router = APIRouter(tags=["history"])
DEFAULT_HISTORY_WINDOW_HOURS = 24 * 7
MIN_HISTORY_WINDOW_HOURS = 1
MAX_HISTORY_WINDOW_HOURS = 24 * 365


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HistoryPoint(BaseModel):
    ts: str
    v: Any
    u: str | None
    q: str
    a: str | None = None  # source_adapter


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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Invalid timestamp: {s!r}",
        )


async def _get_default_history_window_hours(db: Database) -> int:
    """Read configurable default window from app_settings."""
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'history.default_window_hours'")
    if not row or row["value"] is None:
        return DEFAULT_HISTORY_WINDOW_HOURS
    try:
        hours = int(row["value"])
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_WINDOW_HOURS
    return max(MIN_HISTORY_WINDOW_HOURS, min(hours, MAX_HISTORY_WINDOW_HOURS))


async def _resolve_page_access(db: Database, node_id: str) -> str:
    """Traversiert die parent_id-Kette und gibt das effektive Access-Level zurück."""
    current_id: str | None = node_id
    while current_id:
        async with db.conn.execute("SELECT access, parent_id FROM visu_nodes WHERE id = ?", (current_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return "private"  # Unbekannter Knoten → sicher ablehnen
        if row["access"] is not None:
            return row["access"]
        current_id = row["parent_id"]
    return "public"


async def _check_history_access(
    request: Request,
    user: str | None,
    db: Database,
) -> None:
    """JWT oder gültiger Session-Token (protected/public Seite) erlaubt History-Zugriff."""
    if user is not None:
        return  # JWT vorhanden → immer erlaubt

    page_id = request.headers.get("X-Page-Id")
    if not page_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    access = await _resolve_page_access(db, page_id)

    if access in ("public", "readonly"):
        return  # Öffentliche Seite → History-Lesen erlaubt
    if access == "protected":
        session_token = request.headers.get("X-Session-Token")
        if session_token and validate_session(session_token, page_id):
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Valid session token required")
    # private oder unbekannt
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{dp_id}", response_model=list[HistoryPoint])
async def query_history(
    dp_id: uuid.UUID,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(10000, ge=1, le=100000),
    request: Request = None,
    user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> list[HistoryPoint]:
    await _check_history_access(request, user, db)
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")

    now = datetime.now(UTC)
    window_hours = await _get_default_history_window_hours(db)
    from_dt = _parse_ts(from_ts, now - timedelta(hours=window_hours))
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
    request: Request = None,
    user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> list[AggregatedPoint]:
    await _check_history_access(request, user, db)
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} not found")
    if fn not in ("avg", "min", "max", "last"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "fn must be one of: avg, min, max, last",
        )

    now = datetime.now(UTC)
    window_hours = await _get_default_history_window_hours(db)
    from_dt = _parse_ts(from_ts, now - timedelta(hours=window_hours))
    to_dt = _parse_ts(to_ts, now)

    plugin = get_history_plugin()
    rows = await plugin.aggregate(dp_id, fn, interval, from_dt, to_dt)
    return [AggregatedPoint(**r) for r in rows]
