"""Experimental HomeKit/Yahka migration API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from obs.api.auth import get_current_user
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.homekit.yahka import (
    HomeKitApplyRequest,
    HomeKitApplyResult,
    HomeKitPreview,
    HomeKitPreviewOptions,
    apply_mapping,
    build_preview,
)

router = APIRouter(tags=["homekit"])


@router.post("/preview", response_model=HomeKitPreview)
async def preview_homekit_mapping(
    body: HomeKitPreviewOptions,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> HomeKitPreview:
    """Generate a reviewable Yahka/HomeKit mapping from the VISU tree.

    This experimental endpoint is intentionally read-only. It does not create
    ioBroker states, OBS bindings, or Yahka configuration.
    """
    try:
        return await build_preview(db, body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/apply", response_model=HomeKitApplyResult)
async def apply_homekit_mapping(
    body: HomeKitApplyRequest,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> HomeKitApplyResult:
    """Create OBS datapoints and ioBroker bindings from the mapping preview.

    This is an experimental migration helper. The default is ``dry_run=true``.
    In dry-run mode the endpoint returns the exact create/skip plan without
    writing to OBS or ioBroker.
    """
    try:
        return await apply_mapping(db, get_registry(), body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
