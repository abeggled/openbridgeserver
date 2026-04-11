"""
Authentication — Phase 4

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

First startup: if no users exist → create admin/admin (logged with warning).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from jose import JWTError, jwt
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter — mounted on app in main.py via app.state.limiter
limiter = Limiter(key_func=get_remote_address)

from obs.config import get_settings
from obs.db.database import get_db, Database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------
# Password hashing: PBKDF2-HMAC-SHA256 (stdlib, no external dependency).
# Format: "pbkdf2$<iterations>$<salt_hex>$<hash_hex>"

import binascii

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
    return hashlib.sha256(key.encode()).hexdigest()

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
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({"sub": sub, "exp": exp, "type": "access"}, _secret(), algorithm=_ALGORITHM)


def create_refresh_token(sub: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=_REFRESH_DAYS)
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
    db: Database = Depends(lambda: get_db()),
) -> str:
    """FastAPI dependency — returns username or raises 401."""
    if credentials:
        return decode_token(credentials.credentials)

    if api_key:
        key_hash = hash_api_key(api_key)
        row = await db.fetchone(
            "SELECT name FROM api_keys WHERE key_hash=?", (key_hash,)
        )
        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
        # Update last_used_at
        now = datetime.now(timezone.utc).isoformat()
        await db.execute_and_commit(
            "UPDATE api_keys SET last_used_at=? WHERE key_hash=?", (now, key_hash)
        )
        return row["name"]

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Provide Authorization: Bearer {token} or X-API-Key: {key}",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> str:
    """FastAPI dependency — returns username or raises 403 if not admin."""
    row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
    if not row or not row["is_admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return current_user

# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------

async def ensure_default_user(db: Database) -> None:
    """Create admin/admin if no users exist. Called once at startup."""
    row = await db.fetchone("SELECT COUNT(*) AS c FROM users")
    if row and row["c"] == 0:
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?,?,?,?,?)",
            (uid, "admin", hash_password("admin"), 1, now),
        )
        logger.warning(
            "⚠️  Default user created: admin / admin  "
            "— Change the password immediately! POST /api/v1/auth/login"
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
    key: str           # Only returned on creation
    created_at: str

class ApiKeyListItem(BaseModel):
    id: str
    name: str
    created_at: str | None
    last_used_at: str | None

class UserResponse(BaseModel):
    id: str
    username: str
    is_admin: bool
    mqtt_enabled: bool
    mqtt_password_set: bool   # True = MQTT password is configured; hash is never exposed
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
    from obs.core.mqtt_passwd import rebuild_passwd_file, reload_mosquitto
    from obs.config import get_settings
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
    row = await db.fetchone(
        "SELECT password_hash FROM users WHERE username=?", (body.username,)
    )
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    return TokenResponse(
        access_token=create_access_token(body.username),
        refresh_token=create_refresh_token(body.username),
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh(request: Request, body: RefreshRequest) -> TokenResponse:
    sub = decode_token(body.refresh_token, expected_type="refresh")
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
        rows = await db.fetchall(
            "SELECT id, name, created_at, last_used_at FROM api_keys ORDER BY created_at"
        )
    else:
        rows = await db.fetchall(
            "SELECT id, name, created_at, last_used_at FROM api_keys WHERE owner=? ORDER BY created_at",
            (current_user,),
        )
    return [
        ApiKeyListItem(id=r["id"], name=r["name"],
                       created_at=r["created_at"], last_used_at=r["last_used_at"])
        for r in rows
    ]


@router.post("/apikeys", response_model=ApiKeyResponse, status_code=201)
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    body: ApiKeyCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> ApiKeyResponse:
    key = generate_api_key()
    key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?,?,?,?,?)",
        (key_id, body.name, hash_api_key(key), _user, now),
    )
    return ApiKeyResponse(id=key_id, name=body.name, key=key, created_at=now)


@router.delete("/apikeys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: str,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    key_row = await db.fetchone("SELECT owner FROM api_keys WHERE id=?", (key_id,))
    if not key_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    user_row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (current_user,))
    is_admin = user_row is not None and bool(user_row["is_admin"])
    if not is_admin and key_row["owner"] != current_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete another user's API key")
    await db.execute_and_commit("DELETE FROM api_keys WHERE id=?", (key_id,))


# ---------------------------------------------------------------------------
# User management  (admin-only, except /me endpoints)
# ---------------------------------------------------------------------------

_USER_COLS = "id, username, is_admin, mqtt_enabled, mqtt_password_hash, created_at"


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
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    existing = await db.fetchone("SELECT id FROM users WHERE username=?", (body.username,))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Username '{body.username}' already exists")

    from obs.core.mqtt_passwd import mosquitto_hash
    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mqtt_enabled = body.mqtt_enabled and body.mqtt_password is not None
    mqtt_hash = mosquitto_hash(body.mqtt_password) if mqtt_enabled else None

    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (uid, body.username, hash_password(body.password), int(body.is_admin),
         int(mqtt_enabled), mqtt_hash, now),
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
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> UserResponse:
    target = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")

    new_username = body.username if body.username is not None else target["username"]
    new_is_admin = int(body.is_admin) if body.is_admin is not None else target["is_admin"]

    if body.username and body.username != username:
        conflict = await db.fetchone("SELECT id FROM users WHERE username=?", (body.username,))
        if conflict:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Username '{body.username}' already exists")

    mqtt_changed = body.mqtt_enabled is not None and bool(body.mqtt_enabled) != bool(target["mqtt_enabled"])
    new_mqtt_enabled = int(body.mqtt_enabled) if body.mqtt_enabled is not None else target["mqtt_enabled"]
    # Disabling mqtt_enabled clears the stored hash
    new_mqtt_hash = None if body.mqtt_enabled is False else target["mqtt_password_hash"]

    await db.execute_and_commit(
        "UPDATE users SET username=?, is_admin=?, mqtt_enabled=?, mqtt_password_hash=? WHERE id=?",
        (new_username, new_is_admin, new_mqtt_enabled, new_mqtt_hash, target["id"]),
    )
    if mqtt_changed:
        await _sync_mqtt(db)
    row = await db.fetchone(f"SELECT {_USER_COLS} FROM users WHERE id=?", (target["id"],))
    return _user_row(row)


@router.delete("/users/{username}", status_code=204)
async def delete_user(
    username: str,
    admin_user: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    if username == admin_user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete your own account")
    target = await db.fetchone(
        "SELECT mqtt_enabled FROM users WHERE username=?", (username,)
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")
    await db.execute_and_commit("DELETE FROM users WHERE username=?", (username,))
    if target["mqtt_enabled"]:
        await _sync_mqtt(db)


# ---------------------------------------------------------------------------
# MQTT password management  (admin or self)
# ---------------------------------------------------------------------------

@router.post("/users/{username}/mqtt-password", status_code=204)
async def set_mqtt_password(
    username: str,
    body: SetMqttPasswordRequest,
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
    await db.execute_and_commit(
        "UPDATE users SET mqtt_enabled=1, mqtt_password_hash=? WHERE username=?",
        (mosquitto_hash(body.password), username),
    )
    await _sync_mqtt(db)


@router.delete("/users/{username}/mqtt-password", status_code=204)
async def delete_mqtt_password(
    username: str,
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    """Revoke MQTT access for a user (clears password and disables flag)."""
    target = await db.fetchone("SELECT id FROM users WHERE username=?", (username,))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{username}' not found")
    await db.execute_and_commit(
        "UPDATE users SET mqtt_enabled=0, mqtt_password_hash=NULL WHERE username=?",
        (username,),
    )
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
    current_user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone("SELECT password_hash FROM users WHERE username=?", (current_user,))
    if not row or not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    await db.execute_and_commit(
        "UPDATE users SET password_hash=? WHERE username=?",
        (hash_password(body.new_password), current_user),
    )
