"""Message archives API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from obs.api.audit import AuditLogWriter, get_audit_log_writer
from obs.api.auth import Principal, get_admin_user, get_current_principal
from obs.config import get_settings
from obs.message_archive import (
    ArchiveInput,
    ArchivePatch,
    EntryInput,
    EntryPatch,
    EntryQuery,
    MessageArchiveStore,
    broadcast_message_archive_entry,
    get_message_archive_store,
    init_message_archive_store,
)

router = APIRouter(tags=["message-archives"])


class MessageArchiveBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    default_type: str | None = None
    color: str = "#3b82f6"
    retention_max_entries: int | None = Field(default=None, ge=1)
    retention_max_age_days: int | None = Field(default=None, ge=1)


class MessageArchiveCreate(MessageArchiveBase):
    id: str = Field(min_length=1, max_length=80)


class MessageArchiveUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    tags: list[str] | None = None
    default_type: str | None = None
    color: str | None = None
    retention_max_entries: int | None = Field(default=None, ge=1)
    retention_max_age_days: int | None = Field(default=None, ge=1)


class MessageArchiveOut(MessageArchiveBase):
    id: str
    created_at: str
    updated_at: str
    entry_count: int
    oldest_entry_at: str | None
    newest_entry_at: str | None
    db_status: str
    db_path: str


class MessageEntryCreate(BaseModel):
    type: str = "system"
    severity: str = "info"
    status: str = "new"
    source: str = ""
    title: str = ""
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class MessageEntryUpdate(BaseModel):
    type: str | None = None
    severity: str | None = None
    status: str | None = None
    source: str | None = None
    title: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None


class MessageEntryOut(BaseModel):
    id: str
    archive_id: str
    archive_name: str
    archive_color: str
    created_at: str
    updated_at: str
    type: str
    severity: str
    status: str
    source: str
    title: str
    message: str
    payload: dict[str, Any]
    acknowledged_at: str | None
    acknowledged_by: str | None
    read_at: str | None
    is_read: bool


class MessageEntryPage(BaseModel):
    items: list[MessageEntryOut]
    total: int
    limit: int
    offset: int


class DestructiveActionResult(BaseModel):
    ok: bool
    affected_entries: int


class IntegrityCheckResult(BaseModel):
    ok: bool
    result: str
    path: str
    status: str


async def _store() -> MessageArchiveStore:
    try:
        return get_message_archive_store()
    except RuntimeError:
        return await init_message_archive_store(get_settings())


def _principal_name(principal: Principal) -> str:
    return principal.owner or principal.subject


def _csv_values(values: list[str] | str | None) -> list[str] | None:
    if not values:
        return None
    if isinstance(values, str):
        values = [values]
    ids: list[str] = []
    for raw in values:
        for item in raw.split(","):
            item = item.strip()
            if item:
                ids.append(item)
    return ids or None


def _archive_ids(values: list[str] | None) -> list[str] | None:
    return _csv_values(values)


@router.get("", response_model=list[MessageArchiveOut])
async def list_message_archives(
    _principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> list[dict[str, Any]]:
    return await store.list_archives()


@router.post("", response_model=MessageArchiveOut, status_code=status.HTTP_201_CREATED)
async def create_message_archive(
    body: MessageArchiveCreate,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    try:
        return await store.create_archive(
            ArchiveInput(
                id=body.id,
                name=body.name,
                description=body.description,
                tags=body.tags,
                default_type=body.default_type,
                color=body.color,
                retention_max_entries=body.retention_max_entries,
                retention_max_age_days=body.retention_max_age_days,
            )
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Message archive already exists") from exc
        raise


@router.post("/integrity-check", response_model=IntegrityCheckResult)
async def run_message_archive_integrity_check(
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    return await store.integrity_check()


@router.get("/entries", response_model=MessageEntryPage)
async def query_message_archive_entries(
    archive_id: list[str] | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    read_state: Literal["read", "unread"] | None = None,
    type_: str | None = Query(default=None, alias="type"),
    severity: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort: Literal["asc", "desc"] = "desc",
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    return await store.query_entries(
        EntryQuery(
            archive_ids=_archive_ids(archive_id),
            from_ts=from_,
            to_ts=to,
            status=status_,
            statuses=_csv_values(status_),
            read_state=read_state,
            type=type_,
            types=_csv_values(type_),
            severity=severity,
            severities=_csv_values(severity),
            source=source,
            sources=_csv_values(source),
            q=q,
            limit=limit,
            offset=offset,
            sort=sort,
            username=_principal_name(principal),
        )
    )


@router.get("/{archive_id}", response_model=MessageArchiveOut)
async def get_message_archive(
    archive_id: str,
    _principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive = await store.get_archive(archive_id)
    if not archive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    return archive


@router.patch("/{archive_id}", response_model=MessageArchiveOut)
async def update_message_archive(
    archive_id: str,
    body: MessageArchiveUpdate,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive = await store.update_archive(
        archive_id,
        ArchivePatch(
            name=body.name,
            description=body.description,
            tags=body.tags,
            default_type=body.default_type,
            color=body.color,
            retention_max_entries=body.retention_max_entries,
            retention_max_age_days=body.retention_max_age_days,
        ),
    )
    if not archive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    return archive


@router.delete("/{archive_id}", response_model=DestructiveActionResult)
async def delete_message_archive(
    archive_id: str,
    confirm: bool = False,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
    audit: AuditLogWriter = Depends(get_audit_log_writer),
) -> dict[str, Any]:
    archive = await store.get_archive(archive_id)
    if not archive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    affected = int(archive["entry_count"])
    if not confirm:
        raise HTTPException(status.HTTP_409_CONFLICT, {"confirmation_required": True, "affected_entries": affected})
    deleted = await store.delete_archive(archive_id)
    if deleted < 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    await audit.write(
        "message_archive.delete",
        resource_type="message_archive",
        resource_id=archive_id,
        details={"affected_entries": affected},
    )
    return {"ok": True, "affected_entries": affected}


@router.post("/{archive_id}/clear", response_model=DestructiveActionResult)
async def clear_message_archive(
    archive_id: str,
    confirm: bool = False,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
    audit: AuditLogWriter = Depends(get_audit_log_writer),
) -> dict[str, Any]:
    archive = await store.get_archive(archive_id)
    if not archive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    affected = int(archive["entry_count"])
    if not confirm:
        raise HTTPException(status.HTTP_409_CONFLICT, {"confirmation_required": True, "affected_entries": affected})
    await store.clear_archive(archive_id)
    await audit.write(
        "message_archive.clear",
        resource_type="message_archive",
        resource_id=archive_id,
        details={"affected_entries": affected},
    )
    return {"ok": True, "affected_entries": affected}


@router.get("/{archive_id}/export")
async def export_message_archive(
    archive_id: str,
    format: Literal["jsonl", "csv"] = "jsonl",
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> Response:
    if not await store.get_archive(archive_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    if format == "csv":
        return Response(
            content=await store.export_csv(archive_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{archive_id}.csv"'},
        )
    return Response(
        content=await store.export_jsonl(archive_id),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{archive_id}.jsonl"'},
    )


@router.get("/{archive_id}/entries", response_model=MessageEntryPage)
async def query_single_message_archive_entries(
    archive_id: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    read_state: Literal["read", "unread"] | None = None,
    type_: str | None = Query(default=None, alias="type"),
    severity: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort: Literal["asc", "desc"] = "desc",
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    return await store.query_entries(
        EntryQuery(
            archive_ids=[archive_id],
            from_ts=from_,
            to_ts=to,
            status=status_,
            statuses=_csv_values(status_),
            read_state=read_state,
            type=type_,
            types=_csv_values(type_),
            severity=severity,
            severities=_csv_values(severity),
            source=source,
            sources=_csv_values(source),
            q=q,
            limit=limit,
            offset=offset,
            sort=sort,
            username=_principal_name(principal),
        )
    )


@router.post("/{archive_id}/entries", response_model=MessageEntryOut, status_code=status.HTTP_201_CREATED)
async def create_message_archive_entry(
    archive_id: str,
    body: MessageEntryCreate,
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    source = body.source or _principal_name(principal)
    entry = await store.create_entry(
        EntryInput(
            archive_id=archive_id,
            type=body.type,
            severity=body.severity,
            status=body.status,
            source=source,
            title=body.title,
            message=body.message,
            payload=body.payload,
            created_at=body.created_at,
        )
    )
    await broadcast_message_archive_entry(entry)
    return entry


@router.patch("/{archive_id}/entries/{entry_id}", response_model=MessageEntryOut)
async def update_message_archive_entry(
    archive_id: str,
    entry_id: str,
    body: MessageEntryUpdate,
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    entry = await store.update_entry(
        archive_id,
        entry_id,
        EntryPatch(
            type=body.type,
            severity=body.severity,
            status=body.status,
            source=body.source,
            title=body.title,
            message=body.message,
            payload=body.payload,
        ),
    )
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    return await store.get_entry(archive_id, entry_id, username=_principal_name(principal)) or entry


@router.post("/{archive_id}/entries/{entry_id}/read", response_model=MessageEntryOut)
async def mark_message_archive_entry_read(
    archive_id: str,
    entry_id: str,
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    entry = await store.mark_read(archive_id, entry_id, _principal_name(principal))
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    return entry


@router.post("/{archive_id}/entries/{entry_id}/acknowledge", response_model=MessageEntryOut)
async def acknowledge_message_archive_entry(
    archive_id: str,
    entry_id: str,
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    entry = await store.acknowledge_entry(archive_id, entry_id, _principal_name(principal))
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    return entry
