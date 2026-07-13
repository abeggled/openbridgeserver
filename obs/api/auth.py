"""Authentication — Phase 4

Dual-Auth:
  JWT Bearer   → Authorization: Bearer {token}   (Web GUI, interactive)
  API Key      → X-API-Key: {key}                (automation, scripts)

JWT:
  Algorithm: HS256
  Access token:  configurable expiry (default 24 h)
  Refresh token: 30 days

API Keys:
  Format: obs_<64 hex chars>
  Stored: SHA-256 hash in api_keys table

First startup: an owner must be created offline with ``obs-admin``.
"""

from __future__ import annotations
from obs.config import get_settings
from obs.db.database import Database, get_db

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter — mounted on app in main.py via app.state.limiter
limiter = Limiter(key_func=get_remote_address)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------
# Password hashing: PBKDF2-HMAC-SHA256 (stdlib, no external dependency).
# Format: "pbkdf2$<iterations>$<salt_hex>$<hash_hex>"


_ITERATIONS = 260_000
_HASH_NAME = "sha256"


def hash_password(plain: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_HASH_NAME, plain.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            _HASH_NAME,
            plain.encode(),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def hash_api_key(key: str) -> str:
    # SHA-256 is appropriate for API key tokens: they are 32-byte random values
    # (256 bits of entropy), so speed-based brute-force attacks are infeasible.
    # This is intentionally NOT a password hash — do not replace with bcrypt/PBKDF2.
    return hashlib.sha256(key.encode()).hexdigest()  # nosec B324


def generate_api_key() -> str:
    return "obs_" + os.urandom(32).hex()


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"
_REFRESH_DAYS = 30


def _secret() -> str:
    return get_settings().security.jwt_secret


def create_access_token(sub: str) -> str:
    minutes = get_settings().security.jwt_expire_minutes
    exp = datetime.now(UTC) + timedelta(minutes=minutes)
    return jwt.encode({"sub": sub, "exp": exp, "type": "access"}, _secret(), algorithm=_ALGORITHM)


def create_refresh_token(sub: str) -> str:
    exp = datetime.now(UTC) + timedelta(days=_REFRESH_DAYS)
    return jwt.encode({"sub": sub, "exp": exp, "type": "refresh"}, _secret(), algorithm=_ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> str:
    """Return subject (username) or raise HTTPException 401."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
        if payload.get("type") != expected_type:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        return sub
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# FastAPI security schemes
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class Principal(BaseModel):
    subject: str
    type: Literal["user", "api_key"]
    is_admin: bool
    owner: str | None = None


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
    db: Database = Depends(lambda: get_db()),
) -> Principal:
    """FastAPI dependency — returns authenticated principal or raises 401."""
    if credentials:
        subject = decode_token(credentials.credentials)
        row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (subject,))
        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        return Principal(subject=subject, type="user", is_admin=bool(row["is_admin"]))

    if api_key:
        key_hash = hash_api_key(api_key)
        row = await db.fetchone(
            "SELECT id, owner FROM api_keys WHERE key_hash=?",
            (key_hash,),
        )
        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
        # Update last_used_at
        now = datetime.now(UTC).isoformat()
        await db.execute_and_commit("UPDATE api_keys SET last_used_at=? WHERE key_hash=?", (now, key_hash))
        try:
            api_key_id = row["id"]
        except (IndexError, KeyError):
            api_key_id = None
        try:
            api_key_owner = row["owner"] or None
        except (IndexError, KeyError):
            api_key_owner = None
        if api_key_id is not None:
            return Principal(subject=f"api_key:{api_key_id}", type="api_key", is_admin=False, owner=api_key_owner)

        return Principal(subject=str(row["subject"]), type="api_key", is_admin=False, owner=api_key_owner)

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Provide Authorization: Bearer {token} or X-API-Key: {key}",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
    db: Database = Depends(lambda: get_db()),
) -> str:
    """FastAPI compatibility dependency — returns principal subject."""
    principal = await get_current_principal(credentials, api_key, db)
    return principal.subject


async def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
    db: Database = Depends(lambda: get_db()),
) -> str | None:
    """FastAPI dependency — returns username if authenticated, None otherwise."""
    try:
        return await get_current_user(credentials, api_key, db)
    except HTTPException:
        return None


async def get_admin_user(
    principal: Principal = Depends(get_current_principal),
    current_user: str | None = None,
    db: Database = Depends(lambda: get_db()),
    request: Request = None,  # type: ignore[assignment]
) -> str:
    """FastAPI dependency — returns username or raises 403 if not admin."""
    if isinstance(principal, Principal):
        if principal.type != "user" or not principal.is_admin:
            await _audit_admin_denial(request, db, principal)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
        return principal.subject

    if current_user is not None:
        row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
        if row and row["is_admin"]:
            return current_user

        await _audit_admin_denial(request, db, current_user)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")

    await _audit_admin_denial(request, db, None)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")


async def _audit_admin_denial(request: Request | None, db: Database, principal: Principal | str | None) -> None:
    """Persist denied admin mutations without making audit availability an auth bypass."""
    if request is None:
        return
    try:
        from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context

        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        writer = AuditLogWriter(db, build_audit_context(request, principal))
        await writer.write_contract(request.method, path, outcome=AuditOutcome.DENIED)
    except LookupError:
        # Read-only admin routes have no mutation contract and need no event here.
        return
    except Exception:
        logger.exception("Could not persist denied admin mutation audit event")


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------


async def require_configured_owner(db: Database) -> None:
    """Fail closed until an administrator has been created offline."""
    row = await db.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_admin=1")
    if not row or row["c"] == 0:
        raise RuntimeError(
            "No OBS owner is configured. Stop the service and run 'obs-admin auth first-owner <username> --password-stdin' locally, then restart OBS."
        )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # Only returned on creation
    created_at: str


class ApiKeyListItem(BaseModel):
    id: str
    name: str
    created_at: str | None
    last_used_at: str | None


class ApiKeyCapabilitiesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    capabilities: list[str]

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        from obs.api.capabilities import CONFIG_CAPABILITIES

        if len(values) != len(set(values)) or any(value not in CONFIG_CAPABILITIES for value in values):
            raise ValueError("Capabilities must be unique values from the closed registry")
        return sorted(values)


class ApiKeyCapabilitiesResponse(BaseModel):
    key_id: str
    key_name: str
    revision: int
    capabilities: list[str]
    available_capabilities: list[str]


class UserResponse(BaseModel):
    id: str
    username: str
    is_admin: bool
    mqtt_enabled: bool
    mqtt_password_set: bool  # True = MQTT password is configured; hash is never exposed
    created_at: str


class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    mqtt_enabled: bool = False
    mqtt_password: str | None = None  # set MQTT password in one step (optional)


class UserUpdate(BaseModel):
    username: str | None = None
    is_admin: bool | None = None
    mqtt_enabled: bool | None = None  # False → clears mqtt_password_hash


class UserDeletionRequest(BaseModel):
    revision: str
    successor_username: str | None = None


class UserDeletionInventory(BaseModel):
    revision: str
    username: str
    visu_page_ids: list[str]
    logic_graph_ids: list[str]
    filterset_ids: list[str]
    api_key_ids: list[str]
    grant_count: int
    visu_acl_count: int
    filterset_state_count: int


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SetMqttPasswordRequest(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Mosquitto sync helper
# ---------------------------------------------------------------------------


async def _sync_mqtt(db: Database) -> None:
    """Rebuild Mosquitto passwd file and send reload signal."""
    from obs.config import get_settings
    from obs.core.mqtt_passwd import rebuild_passwd_file, reload_mosquitto

    m = get_settings().mosquitto
    await rebuild_passwd_file(db, m.passwd_file, m.service_username, m.service_password)
    await reload_mosquitto(m.reload_command, m.reload_pid)


def _user_row(r) -> UserResponse:
    return UserResponse(
        id=r["id"],
        username=r["username"],
        is_admin=bool(r["is_admin"]),
        mqtt_enabled=bool(r["mqtt_enabled"]),
        mqtt_password_set=r["mqtt_password_hash"] is not None,
        created_at=r["created_at"],
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: Database = Depends(lambda: get_db()),
) -> TokenResponse:
    from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context

    writer = AuditLogWriter(db, build_audit_context(request, body.username))
    row = await db.fetchone("SELECT password_hash FROM users WHERE username=?", (body.username,))
    if not row or not verify_password(body.password, row["password_hash"]):
        await writer.write_contract("POST", "/api/v1/auth/login", outcome=AuditOutcome.DENIED)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    await writer.write_contract("POST", "/api/v1/auth/login")
    return TokenResponse(
        access_token=create_access_token(body.username),
        refresh_token=create_refresh_token(body.username),
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: Database = Depends(lambda: get_db()),
) -> TokenResponse:
    from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context

    try:
        sub = decode_token(body.refresh_token, expected_type="refresh")
    except HTTPException:
        writer = AuditLogWriter(db, build_audit_context(request, None))
        await writer.write_contract("POST", "/api/v1/auth/refresh", outcome=AuditOutcome.DENIED)
        raise
    writer = AuditLogWriter(db, build_audit_context(request, sub))
    if not await db.fetchone("SELECT 1 FROM users WHERE username=?", (sub,)):
        await writer.write_contract("POST", "/api/v1/auth/refresh", outcome=AuditOutcome.DENIED)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    await writer.write_contract("POST", "/api/v1/auth/refresh")
    return TokenResponse(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),
    )


@router.get("/apikeys", response_model=list[ApiKeyListItem])
async def list_api_keys(
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[ApiKeyListItem]:
    user_row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
    is_admin = user_row is not None and bool(user_row["is_admin"])
    if is_admin:
        rows = await db.fetchall("SELECT id, name, created_at, last_used_at FROM api_keys ORDER BY created_at")
    else:
        rows = await db.fetchall(
            "SELECT id, name, created_at, last_used_at FROM api_keys WHERE owner=? ORDER BY created_at",
            (current_user,),
        )
    return [
        ApiKeyListItem(
            id=r["id"],
            name=r["name"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
        )
        for r in rows
    ]


@router.post("/apikeys", response_model=ApiKeyResponse, status_code=201)
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    body: ApiKeyCreate,
    principal: Principal = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> ApiKeyResponse:
    owner = principal.subject if principal.type == "user" else principal.owner
    if not owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API key owner is required")
    key = generate_api_key()
    key_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    from obs.api.audit import AuditLogWriter, build_audit_context

    async with db.transaction():
        await db.execute(
            "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?,?,?,?,?)",
            (key_id, body.name, hash_api_key(key), owner, now),
        )
        writer = AuditLogWriter(db, build_audit_context(request, principal))
        await writer.write_contract("POST", "/api/v1/auth/apikeys", resource_id=key_id, commit=False)
    return ApiKeyResponse(id=key_id, name=body.name, key=key, created_at=now)


@router.delete("/apikeys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: str,
    request: Request = None,  # type: ignore[assignment]
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    async with db.transaction():
        key_row = await db.fetchone("SELECT owner FROM api_keys WHERE id=?", (key_id,))
        if not key_row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
        user_row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
        is_admin = user_row is not None and bool(user_row["is_admin"])
        if not is_admin and key_row["owner"] != current_user:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete another user's API key")
        await db.execute(
            "DELETE FROM authz_node_roles WHERE principal_type='api_key' AND principal_id IN (?, ?)",
            (key_id, f"api_key:{key_id}"),
        )
        await db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        from obs.api.audit import AuditLogWriter, build_audit_context

        writer = AuditLogWriter(db, build_audit_context(request, current_user))
        await writer.write_contract("DELETE", "/api/v1/auth/apikeys/{key_id}", resource_id=key_id, commit=False)


async def _api_key_capabilities_response(db: Database, key_id: str) -> ApiKeyCapabilitiesResponse:
    from obs.api.capabilities import CONFIG_CAPABILITIES

    key = await db.fetchone("SELECT name FROM api_keys WHERE id=?", (key_id,))
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    state = await db.fetchone("SELECT revision FROM api_key_capability_sets WHERE key_id=?", (key_id,))
    rows = await db.fetchall("SELECT capability FROM api_key_capabilities WHERE key_id=? ORDER BY capability", (key_id,))
    return ApiKeyCapabilitiesResponse(
        key_id=key_id,
        key_name=key["name"],
        revision=int(state["revision"]) if state else 0,
        capabilities=[row["capability"] for row in rows],
        available_capabilities=list(CONFIG_CAPABILITIES),
    )


@router.get("/apikeys/{key_id}/capabilities", response_model=ApiKeyCapabilitiesResponse)
async def get_api_key_capabilities(
    key_id: str,
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ApiKeyCapabilitiesResponse:
    """Return one key's complete configuration-capability set and revision."""
    return await _api_key_capabilities_response(db, key_id)


@router.put("/apikeys/{key_id}/capabilities", response_model=ApiKeyCapabilitiesResponse)
async def replace_api_key_capabilities(
    key_id: str,
    body: ApiKeyCapabilitiesReplace,
    request: Request,
    admin_user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> ApiKeyCapabilitiesResponse:
    """Atomically replace one key's complete configuration-capability set."""
    from obs.api.audit import AuditLogWriter, build_audit_context

    async with db.transaction():
        current = await _api_key_capabilities_response(db, key_id)
        if current.revision != body.expected_revision:
            raise HTTPException(status.HTTP_409_CONFLICT, "API key capabilities changed; reload before saving")

        previous = set(current.capabilities)
        replacement = set(body.capabilities)
        revision = current.revision + 1
        await db.execute("DELETE FROM api_key_capabilities WHERE key_id=?", (key_id,))
        await db.executemany(
            "INSERT INTO api_key_capabilities (key_id, capability) VALUES (?, ?)",
            [(key_id, capability) for capability in sorted(replacement)],
        )
        await db.execute(
            """
            INSERT INTO api_key_capability_sets (key_id, revision, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key_id) DO UPDATE SET revision=excluded.revision, updated_at=excluded.updated_at
            """,
            (key_id, revision),
        )

        writer = AuditLogWriter(db, build_audit_context(request, admin_user))
        added = sorted(replacement - previous)
        revoked = sorted(previous - replacement)
        for capability in added:
            await writer.write(
                "api_key.capability.grant",
                resource_type="api_key",
                resource_id=key_id,
                details={"api_key_id": key_id, "capability": capability, "revision": revision},
                commit=False,
            )
        for capability in revoked:
            await writer.write(
                "api_key.capability.revoke",
                resource_type="api_key",
                resource_id=key_id,
                details={"api_key_id": key_id, "capability": capability, "revision": revision},
                commit=False,
            )
        await writer.write_contract(
            "PUT",
            "/api/v1/auth/apikeys/{key_id}/capabilities",
            resource_id=key_id,
            commit=False,
        )

        response = ApiKeyCapabilitiesResponse(
            key_id=key_id,
            key_name=current.key_name,
            revision=revision,
            capabilities=sorted(replacement),
            available_capabilities=current.available_capabilities,
        )

    return response


# ---------------------------------------------------------------------------
# User management  (admin-only, except /me endpoints)
# ---------------------------------------------------------------------------

_USER_COLS = "id, username, is_admin, mqtt_enabled, mqtt_password_hash, created_at"


async def _principal_name_is_referenced(db: Database, username: str) -> bool:
    checks = (
        ("SELECT 1 FROM api_keys WHERE owner=? LIMIT 1", username),
        ("SELECT 1 FROM logic_graphs WHERE created_by=? LIMIT 1", username),
        ("SELECT 1 FROM visu_nodes WHERE created_by=? LIMIT 1", username),
        ("SELECT 1 FROM authz_node_roles WHERE principal_type='user' AND principal_id=? LIMIT 1", username),
        ("SELECT 1 FROM ringbuffer_filterset_user_state WHERE username=? LIMIT 1", username),
    )
    for sql, value in checks:
        if await db.fetchone(sql, (value,)) is not None:
            return True
    return False


async def _deletion_inventory(db: Database, username: str) -> UserDeletionInventory:
    target = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")

    async def ids(sql: str) -> list[str]:
        return [str(row["id"]) for row in await db.fetchall(sql, (username,))]

    visu_page_ids = await ids("SELECT id FROM visu_nodes WHERE type='PAGE' AND created_by=? ORDER BY id")
    logic_graph_ids = await ids("SELECT id FROM logic_graphs WHERE created_by=? ORDER BY id")
    filterset_ids = await ids(
        """SELECT node_id AS id FROM authz_node_roles
           WHERE principal_type='user' AND principal_id=?
             AND node_type='ringbuffer_filterset' AND role='owner' AND effect='allow'
           ORDER BY node_id"""
    )
    api_key_ids = await ids("SELECT id FROM api_keys WHERE owner=? ORDER BY id")
    grants = [
        [row["node_type"], row["node_id"], row["role"], row["effect"], bool(row["central_control"])]
        for row in await db.fetchall(
            """SELECT node_type, node_id, role, effect, central_control FROM authz_node_roles
               WHERE principal_type='user' AND principal_id=?
               ORDER BY node_type, node_id, role, effect""",
            (username,),
        )
    ]
    visu_acl_node_ids = [
        row["node_id"]
        for row in await db.fetchall(
            """SELECT node_id FROM authz_node_roles
               WHERE principal_type='user' AND principal_id=? AND node_type='visu_page'
               ORDER BY node_id""",
            (username,),
        )
    ]
    filterset_state_ids = [
        row["filterset_id"]
        for row in await db.fetchall(
            "SELECT filterset_id FROM ringbuffer_filterset_user_state WHERE username=? ORDER BY filterset_id",
            (username,),
        )
    ]
    revision_payload = {
        "api_key_ids": api_key_ids,
        "filterset_ids": filterset_ids,
        "filterset_state_ids": filterset_state_ids,
        "grants": grants,
        "is_admin": bool(target["is_admin"]),
        "logic_graph_ids": logic_graph_ids,
        "user_id": target["id"],
        "visu_acl_node_ids": visu_acl_node_ids,
        "visu_page_ids": visu_page_ids,
    }
    revision = hashlib.sha256(json.dumps(revision_payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return UserDeletionInventory(
        revision=revision,
        username=username,
        visu_page_ids=visu_page_ids,
        logic_graph_ids=logic_graph_ids,
        filterset_ids=filterset_ids,
        api_key_ids=api_key_ids,
        grant_count=len(grants),
        visu_acl_count=len(visu_acl_node_ids),
        filterset_state_count=len(filterset_state_ids),
    )


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> list[UserResponse]:
    rows = await db.fetchall(f"SELECT {_USER_COLS} FROM users ORDER BY created_at")
    return [_user_row(r) for r in rows]


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    existing = await db.fetchone("SELECT id FROM users WHERE username=?", (body.username,))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Username '{body.username}' already exists")
    if await _principal_name_is_referenced(db, body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Username '{body.username}' has stale principal references")

    from obs.core.mqtt_passwd import mosquitto_hash

    uid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    mqtt_enabled = body.mqtt_enabled and body.mqtt_password is not None
    mqtt_hash = mosquitto_hash(body.mqtt_password) if mqtt_enabled else None
    password_hash = hash_password(body.password)

    from obs.api.audit import AuditLogWriter, build_audit_context

    audit_writer = AuditLogWriter(db=db, context=build_audit_context(request=request, current_user=_admin))
    async with db.transaction():
        await db.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                uid,
                body.username,
                password_hash,
                int(body.is_admin),
                int(mqtt_enabled),
                mqtt_hash,
                now,
            ),
        )
        await audit_writer.write_contract(
            "POST",
            "/api/v1/auth/users",
            resource_id=uid,
            details={
                "is_admin": body.is_admin,
                "mqtt_enabled": mqtt_enabled,
                "username": body.username,
            },
            commit=False,
        )
    if mqtt_enabled:
        await _sync_mqtt(db)
    row = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE id=?", (uid,))
    return _user_row(row)


@router.get("/users/{username}", response_model=UserResponse)
async def get_user(
    username: str,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if not row["is_admin"] and current_user != username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    target = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")
    return _user_row(target)


@router.patch("/users/{username}", response_model=UserResponse)
async def update_user(
    username: str,
    body: UserUpdate,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    async with db.transaction():
        target = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,))
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")

        new_username = body.username if body.username is not None else target["username"]
        new_is_admin = int(body.is_admin) if body.is_admin is not None else target["is_admin"]
        if target["is_admin"] and not new_is_admin:
            if username == _admin:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot demote your own account")
            admin_count = int((await db.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_admin=1"))["c"])
            if admin_count <= 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last administrator")

        if body.username and body.username != username:
            conflict = await db.fetchone("SELECT id FROM users WHERE username=?", (body.username,))
            if conflict or await _principal_name_is_referenced(db, body.username):
                raise HTTPException(status.HTTP_409_CONFLICT, f"Username '{body.username}' already exists or is reserved")

        mqtt_changed = body.mqtt_enabled is not None and bool(body.mqtt_enabled) != bool(target["mqtt_enabled"])
        new_mqtt_enabled = int(body.mqtt_enabled) if body.mqtt_enabled is not None else target["mqtt_enabled"]
        new_mqtt_hash = None if body.mqtt_enabled is False else target["mqtt_password_hash"]

        update_cursor = await db.execute(
            """UPDATE users
               SET username=?, is_admin=?, mqtt_enabled=?, mqtt_password_hash=?
               WHERE id=?
                 AND (is_admin=0 OR ?=1 OR (SELECT COUNT(*) FROM users WHERE is_admin=1) > 1)""",
            (new_username, new_is_admin, new_mqtt_enabled, new_mqtt_hash, target["id"], new_is_admin),
        )
        if getattr(update_cursor, "rowcount", 1) == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last administrator")
        if new_username != username:
            for sql in (
                "UPDATE api_keys SET owner=? WHERE owner=?",
                "UPDATE logic_graphs SET created_by=? WHERE created_by=?",
                "UPDATE visu_nodes SET created_by=? WHERE created_by=?",
                "UPDATE ringbuffer_filtersets SET created_by=? WHERE created_by=?",
                "UPDATE authz_node_roles SET principal_id=? WHERE principal_type='user' AND principal_id=?",
                "UPDATE ringbuffer_filterset_user_state SET username=? WHERE username=?",
            ):
                await db.execute(sql, (new_username, username))

        from obs.api.audit import AuditLogWriter, build_audit_context

        before = {"is_admin": bool(target["is_admin"]), "mqtt_enabled": bool(target["mqtt_enabled"]), "username": target["username"]}
        after = {"is_admin": bool(new_is_admin), "mqtt_enabled": bool(new_mqtt_enabled), "username": new_username}
        audit_writer = AuditLogWriter(db=db, context=build_audit_context(request=request, current_user=_admin))
        await audit_writer.write_contract(
            "PATCH",
            "/api/v1/auth/users/{username}",
            resource_id=target["id"],
            details={"after": after, "before": before, "changed_fields": sorted(field for field in before if before[field] != after[field])},
            commit=False,
        )
    if mqtt_changed:
        await _sync_mqtt(db)
    row = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE id=?", (target["id"],))
    return _user_row(row)


@router.get("/users/{username}/deletion-preflight", response_model=UserDeletionInventory)
async def get_user_deletion_preflight(
    username: str,
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> UserDeletionInventory:
    return await _deletion_inventory(db, username)


@router.delete("/users/{username}", status_code=204)
async def delete_user(
    username: str,
    body: UserDeletionRequest | None = None,
    request: Request = None,  # type: ignore[assignment]
    admin_user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    if username == admin_user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete your own account")
    async with db.transaction():
        target = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,))
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")
        if target["is_admin"]:
            admin_count = int((await db.fetchone("SELECT COUNT(*) AS n FROM users WHERE is_admin=1"))["n"])
            if admin_count <= 1:
                raise HTTPException(status.HTTP_409_CONFLICT, "Cannot delete the last recoverable admin")

        inventory = await _deletion_inventory(db, username)
        if body is None:
            raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, "A deletion preflight revision is required")
        if not hmac.compare_digest(inventory.revision, body.revision):
            raise HTTPException(status.HTTP_409_CONFLICT, "Deletion preflight is stale")
        transfer_required = bool(inventory.visu_page_ids or inventory.logic_graph_ids or inventory.filterset_ids)
        successor = body.successor_username
        if transfer_required and not successor:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A successor is required for owned artifacts")
        if successor:
            if successor == username or not await db.fetchone("SELECT 1 FROM users WHERE username=?", (successor,)):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Successor must be an existing different user")
            source_artifact_grants = await db.fetchall(
                """SELECT node_type, node_id, central_control
                   FROM authz_node_roles
                   WHERE principal_type='user' AND principal_id=?
                     AND ((node_type='visu_page' AND node_id IN ({visu_placeholders}))
                       OR (node_type='logic_graph' AND node_id IN ({logic_placeholders})))""".format(
                    visu_placeholders=",".join("?" for _ in inventory.visu_page_ids) or "NULL",
                    logic_placeholders=",".join("?" for _ in inventory.logic_graph_ids) or "NULL",
                ),
                (username, *inventory.visu_page_ids, *inventory.logic_graph_ids),
            )
            central_control_by_artifact = {(row["node_type"], row["node_id"]): bool(row["central_control"]) for row in source_artifact_grants}
            transferred_grants = [
                (
                    "user",
                    successor,
                    node_type,
                    node_id,
                    "owner",
                    "allow",
                    int(central_control_by_artifact.get((node_type, node_id), False)),
                )
                for node_type, node_ids in (
                    ("visu_page", inventory.visu_page_ids),
                    ("logic_graph", inventory.logic_graph_ids),
                )
                for node_id in node_ids
            ]
            if transferred_grants:
                await db.executemany(
                    """INSERT INTO authz_node_roles
                           (principal_type, principal_id, node_type, node_id, role, effect, central_control)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(principal_type, principal_id, node_type, node_id) DO UPDATE SET
                           role='owner', effect='allow',
                           central_control=MAX(authz_node_roles.central_control, excluded.central_control),
                           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
                    transferred_grants,
                )
            await db.execute("UPDATE visu_nodes SET created_by=? WHERE type='PAGE' AND created_by=?", (successor, username))
            await db.execute("UPDATE logic_graphs SET created_by=? WHERE created_by=?", (successor, username))
            if inventory.filterset_ids:
                placeholders = ",".join("?" for _ in inventory.filterset_ids)
                await db.execute(
                    f"""INSERT INTO authz_node_roles
                            (principal_type, principal_id, node_type, node_id, role, effect, central_control)
                        SELECT 'user', ?, 'ringbuffer_filterset', node_id, 'owner', 'allow', central_control
                        FROM authz_node_roles
                        WHERE principal_type='user' AND principal_id=?
                          AND node_type='ringbuffer_filterset' AND role='owner' AND effect='allow'
                          AND node_id IN ({placeholders})
                        ON CONFLICT(principal_type, principal_id, node_type, node_id) DO UPDATE SET
                            role='owner', effect='allow',
                            central_control=MAX(authz_node_roles.central_control, excluded.central_control),
                            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
                    (successor, username, *inventory.filterset_ids),
                )

        # ``created_by`` is retained as provenance only. Clear a deleted name;
        # ownership transfer above is exclusively represented by central grants.
        await db.execute("UPDATE ringbuffer_filtersets SET created_by=NULL WHERE created_by=?", (username,))

        if inventory.api_key_ids:
            api_key_principal_ids = [principal_id for key_id in inventory.api_key_ids for principal_id in (key_id, f"api_key:{key_id}")]
            placeholders = ",".join("?" for _ in api_key_principal_ids)
            await db.execute(
                f"DELETE FROM authz_node_roles WHERE principal_type='api_key' AND principal_id IN ({placeholders})",
                api_key_principal_ids,
            )
        await db.execute("DELETE FROM api_keys WHERE owner=?", (username,))
        await db.execute("DELETE FROM authz_node_roles WHERE principal_type='user' AND principal_id=?", (username,))
        await db.execute("DELETE FROM ringbuffer_filterset_user_state WHERE username=?", (username,))
        await db.execute("DELETE FROM users WHERE username=?", (username,))

        from obs.api.audit import AuditLogWriter, build_audit_context

        audit_writer = AuditLogWriter(db=db, context=build_audit_context(request=request, current_user=admin_user))
        await audit_writer.write_contract(
            "DELETE",
            "/api/v1/auth/users/{username}",
            resource_id=target["id"],
            details={
                "api_keys_revoked": len(inventory.api_key_ids),
                "artifacts_transferred": len(inventory.visu_page_ids) + len(inventory.logic_graph_ids) + len(inventory.filterset_ids),
                "is_admin": bool(target["is_admin"]),
                "mqtt_enabled": bool(target["mqtt_enabled"]),
                "successor_username": successor,
                "username": target["username"],
            },
            commit=False,
        )
    if target["mqtt_enabled"]:
        await _sync_mqtt(db)


# ---------------------------------------------------------------------------
# MQTT password management  (admin or self)
# ---------------------------------------------------------------------------


@router.post("/users/{username}/mqtt-password", status_code=204)
async def set_mqtt_password(
    username: str,
    body: SetMqttPasswordRequest,
    request: Request = None,  # type: ignore[assignment]
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    """Set (or rotate) the MQTT password for a user. Enables MQTT access automatically."""
    caller = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
    if not caller:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not found")
    if not caller["is_admin"] and current_user != username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")

    target = await db.fetchone("SELECT id FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")

    from obs.core.mqtt_passwd import mosquitto_hash

    from obs.api.audit import AuditLogWriter, build_audit_context

    async with db.transaction():
        await db.execute(
            "UPDATE users SET mqtt_enabled=1, mqtt_password_hash=? WHERE username=?",
            (mosquitto_hash(body.password), username),
        )
        writer = AuditLogWriter(db, build_audit_context(request, current_user))
        await writer.write_contract("POST", "/api/v1/auth/users/{username}/mqtt-password", resource_id=target["id"], commit=False)
    await _sync_mqtt(db)


@router.delete("/users/{username}/mqtt-password", status_code=204)
async def delete_mqtt_password(
    username: str,
    request: Request = None,  # type: ignore[assignment]
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    """Revoke MQTT access for a user (clears password and disables flag)."""
    target = await db.fetchone("SELECT id FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")
    from obs.api.audit import AuditLogWriter, build_audit_context

    async with db.transaction():
        await db.execute(
            "UPDATE users SET mqtt_enabled=0, mqtt_password_hash=NULL WHERE username=?",
            (username,),
        )
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("DELETE", "/api/v1/auth/users/{username}/mqtt-password", resource_id=target["id"], commit=False)
    await _sync_mqtt(db)


# ---------------------------------------------------------------------------
# /me endpoints  (any authenticated user)
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    row = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (current_user,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _user_row(row)


@router.post("/me/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    request: Request = None,  # type: ignore[assignment]
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone("SELECT password_hash FROM users WHERE username=?", (current_user,))
    if not row or not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    from obs.api.audit import AuditLogWriter, build_audit_context

    async with db.transaction():
        await db.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (hash_password(body.new_password), current_user),
        )
        writer = AuditLogWriter(db, build_audit_context(request, current_user))
        await writer.write_contract("POST", "/api/v1/auth/me/change-password", resource_id=current_user, commit=False)
