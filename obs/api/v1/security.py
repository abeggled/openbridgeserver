"""Security administration API."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from obs.api.auth import get_admin_user, get_current_user
from obs.security.url_targets import (
    add_allowed_url_target,
    allowlist_path,
    evaluate_url_target,
    list_allowed_url_targets,
    remove_allowed_url_target,
)

router = APIRouter(tags=["security"])


class UrlTargetAllowlistEntryOut(BaseModel):
    id: str
    target: str
    reason: str = ""
    created_by: str = ""
    created_at: str = ""


class UrlTargetAllowlistOut(BaseModel):
    path: str
    entries: list[UrlTargetAllowlistEntryOut]


class UrlTargetAllowlistCreate(BaseModel):
    target: str
    reason: str = ""


class UrlTargetCheckIn(BaseModel):
    url: str
    require_https: bool = False
    allow_loopback: bool = False


class UrlTargetDecisionOut(BaseModel):
    allowed: bool
    url: str
    host: str
    resolved_ips: list[str]
    blocked_ips: list[str]
    reason: str
    allowlisted_by: str | None = None
    suggested_target: str | None = None


def _entry_out(entry) -> UrlTargetAllowlistEntryOut:
    return UrlTargetAllowlistEntryOut(
        id=entry.id,
        target=entry.target,
        reason=entry.reason,
        created_by=entry.created_by,
        created_at=entry.created_at,
    )


@router.get("/url-target-allowlist", response_model=UrlTargetAllowlistOut)
async def get_url_target_allowlist(_admin: str = Depends(get_admin_user)) -> UrlTargetAllowlistOut:
    return UrlTargetAllowlistOut(path=str(allowlist_path()), entries=[_entry_out(entry) for entry in list_allowed_url_targets()])


@router.post("/url-target-allowlist", response_model=UrlTargetAllowlistEntryOut)
async def create_url_target_allowlist_entry(
    body: UrlTargetAllowlistCreate,
    admin: str = Depends(get_admin_user),
) -> UrlTargetAllowlistEntryOut:
    try:
        entry = await asyncio.to_thread(add_allowed_url_target, body.target, reason=body.reason, created_by=admin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Could not write URL target allowlist: {exc}") from exc
    return _entry_out(entry)


@router.delete("/url-target-allowlist")
async def delete_url_target_allowlist_entry(
    target: str = Query(..., description="Exact allowlist target, for example 10.38.113.23/32"),
    _admin: str = Depends(get_admin_user),
) -> dict[str, bool]:
    try:
        deleted = remove_allowed_url_target(target)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Could not write URL target allowlist: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist target not found")
    return {"deleted": True}


@router.post("/url-target-check", response_model=UrlTargetDecisionOut)
async def check_url_target(body: UrlTargetCheckIn, _user: str = Depends(get_current_user)) -> UrlTargetDecisionOut:
    decision = await asyncio.to_thread(
        evaluate_url_target,
        body.url,
        require_https=body.require_https,
        allow_loopback=body.allow_loopback,
    )
    return UrlTargetDecisionOut(
        allowed=decision.allowed,
        url=decision.url,
        host=decision.host,
        resolved_ips=decision.resolved_ips,
        blocked_ips=decision.blocked_ips,
        reason=decision.reason,
        allowlisted_by=decision.allowlisted_by,
        suggested_target=decision.suggested_target,
    )
