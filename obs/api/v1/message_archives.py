"""Message archives API."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from obs.api.audit import AuditLogWriter, get_audit_log_writer
from obs.api.auth import Principal, decode_token, get_admin_user, get_current_principal, hash_api_key
from obs.config import get_settings
from obs.db.database import Database, get_db
from obs.message_archive import (
    ArchiveInput,
    ArchivePatch,
    EntryInput,
    EntryPatch,
    EntryPredicate,
    EntryQuery,
    MIGRATIONS,
    MessageArchiveStore,
    activate_message_archive_service,
    broadcast_message_archive_entry,
    get_message_archive_store,
    init_message_archive_store,
)

router = APIRouter(tags=["message-archives"])
logger = logging.getLogger(__name__)


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

    @model_validator(mode="after")
    def validate_required_nulls(self) -> "MessageArchiveUpdate":
        for field in ("name", "description", "color"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class MessageArchiveOut(MessageArchiveBase):
    id: str
    created_at: str
    updated_at: str
    entry_count: int
    oldest_entry_at: str | None
    newest_entry_at: str | None
    db_status: str | None = None
    db_path: str | None = None


class MessageEntryCreate(BaseModel):
    type: str | None = None
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


class DatabaseImportResult(BaseModel):
    ok: bool
    message: str
    size_bytes: int


@dataclass(frozen=True)
class ArchiveReadAccess:
    username: str
    predicates: list[Any] | None = None
    is_admin: bool = False
    page_access: str | None = None


async def _store() -> MessageArchiveStore:
    try:
        store = get_message_archive_store()
    except RuntimeError:
        store = await init_message_archive_store(get_settings())
    if store.status != "ok" or not store.is_connected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Meldungsarchiv-Datenbank ist nicht verfügbar.")
    return store


async def _store_for_import() -> MessageArchiveStore:
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


def _validate_archive_id(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 80:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid message archive id")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if any(ch not in allowed for ch in normalized):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid message archive id")
    return normalized


def _has_foreign_key_cascade(foreign_keys: list[Any], table: str, from_column: str, to_column: str) -> bool:
    for row in foreign_keys:
        target_table = str(row[2])
        source_column = str(row[3])
        target_column = str(row[4])
        on_delete = str(row[6]).upper()
        if target_table == table and source_column == from_column and target_column == to_column and on_delete == "CASCADE":
            return True
    return False


def _has_unique_key(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> bool:
    table_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    primary_key_columns = tuple(str(row[1]) for row in sorted((row for row in table_info if int(row[5]) > 0), key=lambda row: int(row[5])))
    if primary_key_columns == columns:
        return True

    for index_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        is_unique = bool(index_row[2])
        is_partial = bool(index_row[4]) if len(index_row) > 4 else False
        if not is_unique or is_partial:
            continue
        index_name = str(index_row[1])
        index_columns = tuple(str(row[2]) for row in conn.execute(f"PRAGMA index_info({index_name})").fetchall())
        if index_columns == columns:
            return True
    return False


def _validate_sqlite_archive_db(path: str) -> None:
    with open(path, "rb") as handle:
        header = handle.read(16)
    if header != b"SQLite format 3\x00":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die hochgeladene Datei ist keine gültige SQLite-Datenbank.")

    required_tables = {
        "schema_version",
        "message_archives",
        "message_archive_entries",
        "message_archive_read_states",
    }
    required_columns = {
        "schema_version": {"version", "applied_at"},
        "message_archives": {
            "id",
            "name",
            "description",
            "tags",
            "default_type",
            "color",
            "retention_max_entries",
            "retention_max_age_days",
            "created_at",
            "updated_at",
        },
        "message_archive_entries": {
            "id",
            "archive_id",
            "created_at",
            "updated_at",
            "type",
            "severity",
            "status",
            "source",
            "title",
            "message",
            "payload",
            "acknowledged_at",
            "acknowledged_by",
        },
        "message_archive_read_states": {"entry_id", "username", "read_at", "hidden_at"},
    }
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Meldungsarchiv-Datenbank ist beschädigt.")
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {str(row[0]) for row in rows}
            missing = sorted(required_tables - tables)
            if missing:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Datei ist keine Meldungsarchiv-Datenbank.")
            version_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            schema_version = int(version_row[0]) if version_row and version_row[0] is not None else 0
            supported_version = max(version for version, _sql in MIGRATIONS)
            if schema_version != supported_version:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Meldungsarchiv-Datenbank hat eine nicht unterstützte Schema-Version.")
            for table, columns in required_columns.items():
                table_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
                actual_columns = {str(col[1]) for col in table_info}
                if columns - actual_columns:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Datei ist keine gültige Meldungsarchiv-Datenbank.")
            entry_foreign_keys = conn.execute("PRAGMA foreign_key_list(message_archive_entries)").fetchall()
            has_entry_archive_cascade = _has_foreign_key_cascade(entry_foreign_keys, "message_archives", "archive_id", "id")
            read_state_foreign_keys = conn.execute("PRAGMA foreign_key_list(message_archive_read_states)").fetchall()
            has_read_state_entry_cascade = _has_foreign_key_cascade(read_state_foreign_keys, "message_archive_entries", "entry_id", "id")
            if not has_entry_archive_cascade or not has_read_state_entry_cascade:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Meldungsarchiv-Datenbank hat ungültige Fremdschlüssel.")
            if not _has_unique_key(conn, "message_archive_read_states", ("entry_id", "username")):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Meldungsarchiv-Datenbank hat ungültige Lesestatus-Schlüssel.")
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Meldungsarchiv-Datenbank enthält ungültige Fremdschlüssel.")
        finally:
            conn.close()
    except HTTPException:
        raise
    except sqlite3.Error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die hochgeladene Datei ist keine gültige SQLite-Datenbank.") from None


def _unlink_sqlite_sidecars(path: str) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.unlink(f"{path}{suffix}")
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove SQLite sidecar %s", f"{path}{suffix}")


async def _archive_read_access(request: Request, db: Database) -> ArchiveReadAccess:
    auth_header = request.headers.get("authorization", "")
    page_id = request.headers.get("x-page-id") or request.query_params.get("page_id")
    if auth_header.startswith("Bearer "):
        try:
            username = decode_token(auth_header[7:])
            row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (username,))
            is_admin = bool(row and row["is_admin"])
            if not page_id or is_admin:
                return ArchiveReadAccess(username=username, is_admin=is_admin)
            return await _page_scoped_archive_access(request, db, username=username)
        except HTTPException:
            if not page_id:
                raise

    api_key = request.headers.get("x-api-key")
    if api_key:
        key_hash = hash_api_key(api_key)
        row = await db.fetchone("SELECT id, owner FROM api_keys WHERE key_hash=?", (key_hash,))
        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
        await db.execute_and_commit("UPDATE api_keys SET last_used_at=? WHERE key_hash=?", (datetime.now(UTC).isoformat(), key_hash))
        owner = row["owner"] if row["owner"] else None
        username = owner or f"api_key:{row['id']}"
        if page_id:
            return await _page_scoped_archive_access(request, db, username=username)
        return ArchiveReadAccess(username=username)

    if not page_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Provide Authorization: Bearer {token} or X-API-Key: {key}")

    return await _page_scoped_archive_access(request, db, username=f"visu-page:{page_id}")


async def _page_scoped_archive_access(request: Request, db: Database, *, username: str) -> ArchiveReadAccess:
    from obs.api.v1 import sessions as sessions_api
    from obs.api.v1.visu import _check_user_access, _resolve_access_with_node
    from obs.api.v1.websocket import _page_allowed_message_archive_predicates

    page_id = request.headers.get("x-page-id") or request.query_params.get("page_id")
    if not page_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Page context required")

    page_row = await db.fetchone("SELECT type FROM visu_nodes WHERE id = ?", (page_id,))
    if not page_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    page_type = page_row.get("type") if isinstance(page_row, dict) else page_row["type"]
    if page_type != "PAGE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid visu page")

    access, defining_node_id = await _resolve_access_with_node(db, page_id)
    session_token = request.headers.get("x-session-token") or request.query_params.get("session_token")
    if access == "protected":
        validate_id = defining_node_id or page_id
        if not session_token or not sessions_api.validate_session(session_token, validate_id):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Valid session token required")
    elif access == "user":
        if username.startswith(("visu-page:", "api_key:")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        if not await _check_user_access(db, page_id, username):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Zugriff verweigert")
    elif access not in ("public", "readonly"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")

    async def _can_access_widget_ref_page(source_page_id: str) -> bool:
        source_access, source_defining_node_id = await _resolve_access_with_node(db, source_page_id)
        if source_access in ("public", "readonly"):
            return True
        if source_access == "protected":
            source_validate_id = source_defining_node_id or source_page_id
            return bool(session_token and sessions_api.validate_session(session_token, source_validate_id))
        if source_access == "user":
            return not username.startswith(("visu-page:", "api_key:")) and await _check_user_access(db, source_page_id, username)
        return False

    async def _is_readonly_widget_ref_page(source_page_id: str) -> bool:
        source_access, _source_defining_node_id = await _resolve_access_with_node(db, source_page_id)
        return source_access == "readonly"

    predicates = await _page_allowed_message_archive_predicates(
        db,
        page_id,
        widget_ref_access_check=_can_access_widget_ref_page,
        widget_ref_readonly_check=_is_readonly_widget_ref_page,
    )
    if not predicates:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Message archive access is not configured for this page")
    return ArchiveReadAccess(username=username, predicates=predicates, page_access=access)


def _entry_predicates_for_page(predicates: list[Any] | None) -> list[EntryPredicate] | None:
    if predicates is None:
        return None
    converted: list[EntryPredicate] = []
    for predicate in predicates:
        converted.append(
            EntryPredicate(
                archive_ids=sorted(predicate.archive_ids) if predicate.archive_ids is not None else None,
                types=sorted(predicate.types) if predicate.types is not None else None,
                severities=sorted(predicate.severities) if predicate.severities is not None else None,
                statuses=sorted(predicate.statuses) if predicate.statuses is not None else None,
                sources=sorted(predicate.sources) if predicate.sources is not None else None,
            )
        )
    return converted


def _entry_action_allowed_for_page(entry: dict[str, Any], predicates: list[Any] | None, action: Literal["read", "acknowledge"]) -> bool:
    if predicates is None:
        return True
    from obs.api.v1.websocket import _message_archive_entry_matches_access

    for predicate in predicates:
        if action == "read" and not getattr(predicate, "allow_read", True):
            continue
        if action == "acknowledge" and not getattr(predicate, "allow_acknowledge", True):
            continue
        if _message_archive_entry_matches_access(entry, [predicate]):
            return True
    return False


def _ensure_page_action_mutation_allowed(access: ArchiveReadAccess) -> None:
    if access.page_access == "readonly":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Readonly visu pages cannot mutate message archives")


def _strip_archive_storage_fields(archive: dict[str, Any]) -> dict[str, Any]:
    public_archive = dict(archive)
    public_archive.pop("db_path", None)
    public_archive.pop("db_status", None)
    return public_archive


@router.get("", response_model=list[MessageArchiveOut], response_model_exclude_none=True)
async def list_message_archives(
    request: Request,
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> list[dict[str, Any]]:
    access = await _archive_read_access(request, db)
    archives = await store.list_archives()
    if access.predicates is None:
        return archives if access.is_admin else [_strip_archive_storage_fields(archive) for archive in archives]
    allowed_ids: set[str] | None = set()
    for predicate in access.predicates:
        if predicate.archive_ids is None:
            allowed_ids = None
            break
        allowed_ids.update(predicate.archive_ids)
    page_archives = archives if allowed_ids is None else [archive for archive in archives if archive["id"] in allowed_ids]
    return [_strip_archive_storage_fields(archive) for archive in page_archives]


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
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid message archive id") from None
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Message archive already exists") from None
        logger.exception("Message archive creation failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Message archive could not be created") from None


@router.post("/integrity-check", response_model=IntegrityCheckResult)
async def run_message_archive_integrity_check(
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store_for_import),
) -> dict[str, Any]:
    return await store.integrity_check()


@router.get("/export/db")
async def export_message_archive_db(
    background_tasks: BackgroundTasks,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> FileResponse:
    if store.path == ":memory:":
        raise HTTPException(status.HTTP_409_CONFLICT, "Meldungsarchiv-Datenbank kann im In-Memory-Modus nicht gesichert werden.")
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    try:
        await store.sqlite_snapshot(tmp.name)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        logger.exception("Message archive database export failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Meldungsarchiv-Sicherung fehlgeschlagen.") from None
    background_tasks.add_task(os.unlink, tmp.name)
    return FileResponse(
        path=tmp.name,
        media_type="application/octet-stream",
        filename="message-archives.sqlite",
    )


@router.post("/import/db", response_model=DatabaseImportResult, status_code=status.HTTP_200_OK)
async def import_message_archive_db(
    file: UploadFile = File(...),
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store_for_import),
) -> dict[str, Any]:
    if store.path == ":memory:":
        raise HTTPException(status.HTTP_409_CONFLICT, "Meldungsarchiv-Datenbank kann im In-Memory-Modus nicht wiederhergestellt werden.")

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    backup_path = f"{store.path}.pre-import.bak"
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        _validate_sqlite_archive_db(tmp.name)

        target_path = store.path
        target_exists = os.path.exists(target_path)
        await store.disconnect()
        try:
            if target_exists:
                shutil.copy2(target_path, backup_path)
            _unlink_sqlite_sidecars(target_path)
            shutil.copy2(tmp.name, target_path)
            await store.connect()
            integrity = await store.integrity_check()
            if not integrity.get("ok"):
                raise RuntimeError("imported database failed integrity check")
            activate_message_archive_service(store)
        except Exception:
            try:
                await store.disconnect()
            except Exception:
                pass
            if target_exists and os.path.exists(backup_path):
                shutil.copy2(backup_path, target_path)
            _unlink_sqlite_sidecars(target_path)
            await store.connect()
            activate_message_archive_service(store)
            logger.exception("Message archive database import failed")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Meldungsarchiv-Wiederherstellung fehlgeschlagen.")

        return {
            "ok": True,
            "message": "Meldungsarchiv-Datenbankwiederherstellung erfolgreich.",
            "size_bytes": os.path.getsize(target_path),
        }
    finally:
        for path in (tmp.name, backup_path):
            try:
                os.unlink(path)
            except Exception:
                pass


@router.get("/entries", response_model=MessageEntryPage)
async def query_message_archive_entries(
    request: Request,
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
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    access = await _archive_read_access(request, db)
    query = EntryQuery(
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
        username=access.username,
        predicates=_entry_predicates_for_page(access.predicates),
    )
    try:
        return await store.query_entries(query)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid message archive id") from None


@router.get("/{archive_id}", response_model=MessageArchiveOut, response_model_exclude_none=True)
async def get_message_archive(
    archive_id: str,
    request: Request,
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
    access = await _archive_read_access(request, db)
    if access.predicates is not None:
        if not _entry_predicates_for_page(access.predicates):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Message archive access denied")
        probe = await store.query_entries(
            EntryQuery(archive_ids=[archive_id], limit=1, username=access.username, predicates=_entry_predicates_for_page(access.predicates))
        )
        if probe["total"] == 0:
            allowed_ids = [predicate.archive_ids for predicate in access.predicates]
            if any(ids is None for ids in allowed_ids):
                pass
            elif archive_id not in {item for ids in allowed_ids for item in (ids or set())}:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Message archive access denied")
    archive = await store.get_archive(archive_id)
    if not archive:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    return archive if access.is_admin else _strip_archive_storage_fields(archive)


@router.patch("/{archive_id}", response_model=MessageArchiveOut)
async def update_message_archive(
    archive_id: str,
    body: MessageArchiveUpdate,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
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
            fields_set=body.model_fields_set,
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
    archive_id = _validate_archive_id(archive_id)
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
    archive_id = _validate_archive_id(archive_id)
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
    archive_id = _validate_archive_id(archive_id)
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
    request: Request,
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
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
    access = await _archive_read_access(request, db)
    query = EntryQuery(
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
        username=access.username,
        predicates=_entry_predicates_for_page(access.predicates),
    )
    try:
        return await store.query_entries(query)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid message archive query") from None


@router.post("/{archive_id}/entries", response_model=MessageEntryOut, status_code=status.HTTP_201_CREATED)
async def create_message_archive_entry(
    archive_id: str,
    body: MessageEntryCreate,
    principal: Principal = Depends(get_current_principal),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
    if not await store.get_archive(archive_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive not found")
    source = body.source or _principal_name(principal)
    try:
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
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await broadcast_message_archive_entry(entry)
    return entry


@router.patch("/{archive_id}/entries/{entry_id}", response_model=MessageEntryOut)
async def update_message_archive_entry(
    archive_id: str,
    entry_id: str,
    body: MessageEntryUpdate,
    _admin: str = Depends(get_admin_user),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
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
    return await store.get_entry(archive_id, entry_id, username=_admin) or entry


@router.post("/{archive_id}/entries/{entry_id}/read", response_model=MessageEntryOut)
async def mark_message_archive_entry_read(
    archive_id: str,
    entry_id: str,
    request: Request,
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
    access = await _archive_read_access(request, db)
    _ensure_page_action_mutation_allowed(access)
    existing = await store.get_entry(archive_id, entry_id, username=access.username)
    if not existing or not _entry_action_allowed_for_page(existing, access.predicates, "read"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    entry = await store.mark_read(archive_id, entry_id, access.username)
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    return entry


@router.post("/{archive_id}/entries/{entry_id}/acknowledge", response_model=MessageEntryOut)
async def acknowledge_message_archive_entry(
    archive_id: str,
    entry_id: str,
    request: Request,
    db: Database = Depends(lambda: get_db()),
    store: MessageArchiveStore = Depends(_store),
) -> dict[str, Any]:
    archive_id = _validate_archive_id(archive_id)
    access = await _archive_read_access(request, db)
    _ensure_page_action_mutation_allowed(access)
    existing = await store.get_entry(archive_id, entry_id, username=access.username)
    if not existing or not _entry_action_allowed_for_page(existing, access.predicates, "acknowledge"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    previous_entry = await store.get_entry(archive_id, entry_id)
    entry = await store.acknowledge_entry(archive_id, entry_id, access.username)
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message archive entry not found")
    broadcast_entry = await store.get_entry(archive_id, entry_id) or entry
    await broadcast_message_archive_entry(broadcast_entry, previous_entry=previous_entry)
    return entry
