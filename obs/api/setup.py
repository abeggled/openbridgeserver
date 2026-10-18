"""First-run setup — claiming an installation that has no owner yet.

A fresh installation ships without any credentials.  Rather than refusing to
start until an administrator has been created offline with ``obs-admin`` — a
step that needs shell access to the container or the LXC guest — OBS starts in
*setup mode* and reduces its whole HTTP surface to one page and the single
endpoint that page needs (see ``_setup_gate`` in ``obs/main.py``):

* no login, because there is nothing to log in as,
* no API, because no principal could be authorized against an empty user table,
* only ``GET /api/v1/setup/status`` and ``POST /api/v1/setup/owner``.

Creating the owner ends setup mode in the running process, so the installation
becomes usable without a restart.  Whoever reaches the page first claims the
installation — the same first-run trust model other self-hosted appliances use
— which is why setup mode is announced loudly in the log and why the offline
``obs-admin auth first-owner`` path stays available for installations that must
never be claimable over the network.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from obs.db.database import Database, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])

# The first owner is also written into the Mosquitto passwd file, whose format
# is "<username>:<hash>" per line — a colon or any whitespace in the name would
# corrupt that file, so those are rejected here rather than later.
_USERNAME_PATTERN = re.compile(r"^[^\s:]{1,64}$")

MIN_PASSWORD_LENGTH = 8

# Paths that stay reachable while setup mode is active. Everything else is
# refused (API) or redirected to the setup page (browser navigation).
_ALLOWED_PREFIXES = (
    "/api/v1/setup/",
    # Admin-GUI shell: without its own assets the setup page cannot render.
    "/assets/",
    # Static help site — documentation only, no installation data.
    "/help/",
)

_ALLOWED_PATHS = frozenset(
    {
        "/setup",
        "/help",
        # The Compose/LXC health check must keep working, or the container is
        # reported unhealthy for as long as it waits to be set up.
        "/api/v1/system/health",
        "/favicon.svg",
        "/manifest.webmanifest",
        "/apple-touch-icon.png",
        "/obs_logo_light.svg",
        "/obs_logo_dark.svg",
    }
)

_setup_required = False


def setup_required() -> bool:
    """Whether the running process is currently in setup mode."""
    return _setup_required


def set_setup_required(value: bool) -> None:
    global _setup_required
    _setup_required = value


async def detect_setup_required(db: Database) -> bool:
    """Enter setup mode when the installation has no administrator.

    A database that cannot be read counts as "no owner": that fails towards the
    setup page, never towards an unprotected installation.
    """
    row = await db.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_admin=1")
    set_setup_required(not row or not row["c"])
    return setup_required()


async def announce_setup_mode(db: Database, port: int) -> bool:
    """Enter setup mode if needed and tell the operator where to continue."""
    if not await detect_setup_required(db):
        return False
    logger.warning(
        "No owner is configured — starting in SETUP MODE. Open http://<host>:%s/ in a browser and set the "
        "administrator password; every other page and API stays blocked until then.",
        port,
    )
    return True


def path_is_allowed_during_setup(path: str) -> bool:
    """Whether a request path stays reachable while setup mode is active."""
    return path in _ALLOWED_PATHS or path.startswith(_ALLOWED_PREFIXES)


class SetupStatus(BaseModel):
    setup_required: bool


class FirstOwnerRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        candidate = v.strip()
        if not _USERNAME_PATTERN.match(candidate):
            raise ValueError("Username must not be empty and must not contain whitespace or ':'")
        return candidate


class FirstOwnerResponse(BaseModel):
    username: str


@router.get("/status", response_model=SetupStatus)
async def get_setup_status() -> SetupStatus:
    """Public: tells the Admin GUI whether to show the setup page."""
    return SetupStatus(setup_required=setup_required())


@router.post("/owner", response_model=FirstOwnerResponse, status_code=status.HTTP_201_CREATED)
async def create_first_owner(
    request: Request,
    body: FirstOwnerRequest,
    db: Database = Depends(lambda: get_db()),
) -> FirstOwnerResponse:
    """Create the one owner an empty installation is missing, then leave setup mode."""
    if not setup_required():
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup was already completed")

    now = datetime.now(UTC).isoformat()
    uid = str(uuid.uuid4())
    from obs.api.auth import hash_password

    password_hash = hash_password(body.password)

    async with db.transaction():
        # Re-check inside the transaction: the flag is process state, this is the
        # authoritative one, and BEGIN IMMEDIATE keeps two concurrent claims from
        # both passing it.
        row = await db.fetchone("SELECT COUNT(*) AS c FROM users")
        if row and row["c"]:
            set_setup_required(False)
            raise HTTPException(status.HTTP_409_CONFLICT, "Setup was already completed")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, body.username, password_hash, 1, 0, None, now),
        )
        await _grant_root_owner_role(db, body.username, now)

    set_setup_required(False)
    from obs.api.audit import AuditLogWriter, build_audit_context

    writer = AuditLogWriter(db, build_audit_context(request, body.username))
    await writer.write_contract("POST", "/api/v1/setup/owner", resource_id=uid)
    logger.warning("Setup completed — owner '%s' created; OBS now requires authentication.", body.username)
    return FirstOwnerResponse(username=body.username)


async def _grant_root_owner_role(db: Database, username: str, now: str) -> None:
    """Mirror ``obs-admin auth first-owner``: the owner owns the hierarchy root.

    A fresh installation has no hierarchy yet, in which case there is nothing to
    grant and the role is created with the root node later.
    """
    root = await db.fetchone("SELECT id FROM hierarchy_nodes WHERE parent_id IS NULL ORDER BY created_at, id LIMIT 1")
    if root is None:
        return
    await db.execute(
        "INSERT INTO authz_node_roles"
        " (principal_type, principal_id, node_type, node_id, role, effect, created_at, updated_at)"
        " VALUES ('user', ?, 'hierarchy', ?, 'owner', 'allow', ?, ?)",
        (username, root["id"], now, now),
    )
