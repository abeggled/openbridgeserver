"""First-run setup mode (#1229) — state, guard rules and the claim endpoint."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from obs.api import setup as setup_api
from obs.api.auth import verify_password
from obs.api.setup import (
    MIN_PASSWORD_LENGTH,
    FirstOwnerRequest,
    announce_setup_mode,
    detect_setup_required,
    path_is_allowed_during_setup,
    set_setup_required,
    setup_required,
)


@pytest.fixture(autouse=True)
def _restore_setup_state():
    previous = setup_required()
    yield
    set_setup_required(previous)


class _DbStub:
    def __init__(self, row):
        self.row = row

    async def fetchone(self, _sql, _params=()):
        return self.row


# ---------------------------------------------------------------------------
# Setup-mode detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"c": 0}, True),
        (None, True),  # unreadable user table → towards the setup page, never past it
        ({"c": 1}, False),
        ({"c": 7}, False),
    ],
)
async def test_detect_setup_required(row, expected):
    assert await detect_setup_required(_DbStub(row)) is expected
    assert setup_required() is expected


async def test_announce_setup_mode_points_at_the_setup_page(caplog):
    with caplog.at_level("WARNING"):
        entered = await announce_setup_mode(_DbStub({"c": 0}), 8080)

    assert entered is True
    assert "SETUP MODE" in caplog.text
    assert "8080" in caplog.text


async def test_announce_setup_mode_stays_quiet_for_a_configured_installation(caplog):
    with caplog.at_level("WARNING"):
        entered = await announce_setup_mode(_DbStub({"c": 1}), 8080)

    assert entered is False
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Which paths survive setup mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/setup",
        "/api/v1/setup/status",
        "/api/v1/setup/owner",
        "/api/v1/system/health",
        "/assets/index-abc123.js",
        "/favicon.svg",
        "/manifest.webmanifest",
        "/apple-touch-icon.png",
        "/obs_logo_light.svg",
        "/obs_logo_dark.svg",
        "/help/de/",
    ],
)
def test_paths_reachable_during_setup(path):
    assert path_is_allowed_during_setup(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/login",
        "/datapoints",
        "/api/v1/datapoints",
        "/api/v1/auth/login",
        "/api/v1/auth/users",
        "/api/v1/system/info",
        "/visu/",
        "/openapi.json",
        "/setup-something-else",
        "/api/v1/system/healthcheck-lookalike",
        "/helpdesk",
    ],
)
def test_paths_blocked_during_setup(path):
    assert path_is_allowed_during_setup(path) is False


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_username_is_trimmed():
    assert FirstOwnerRequest(username="  owner  ", password="a-good-password").username == "owner"


@pytest.mark.parametrize("username", ["", "   ", "two words", "colon:name", "line\nbreak", "x" * 65])
def test_invalid_usernames_are_rejected(username):
    with pytest.raises(ValueError):
        FirstOwnerRequest(username=username, password="a-good-password")


def test_short_passwords_are_rejected():
    with pytest.raises(ValueError):
        FirstOwnerRequest(username="owner", password="x" * (MIN_PASSWORD_LENGTH - 1))


# ---------------------------------------------------------------------------
# Claiming the installation
# ---------------------------------------------------------------------------


@pytest.fixture
async def empty_db():
    """A migrated database without any user — a fresh installation."""
    from obs.db.database import Database

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="obs_setup_test_") as handle:
        db_path = handle.name
    db = Database(db_path)
    await db.connect()
    try:
        yield db
    finally:
        await db.disconnect()
        for suffix in ("", "-wal", "-shm"):
            Path(db_path + suffix).unlink(missing_ok=True)


@pytest.fixture
async def setup_client(empty_db, monkeypatch):
    """The setup router alone, wired to a fresh installation's database."""
    monkeypatch.setattr(setup_api, "get_db", lambda: empty_db)
    app = FastAPI()
    app.include_router(setup_api.router, prefix="/api/v1/setup")
    set_setup_required(True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def test_status_reports_setup_mode(setup_client):
    assert (await setup_client.get("/api/v1/setup/status")).json() == {"setup_required": True}

    set_setup_required(False)

    assert (await setup_client.get("/api/v1/setup/status")).json() == {"setup_required": False}


async def test_first_owner_is_created_and_setup_mode_ends(setup_client, empty_db):
    resp = await setup_client.post("/api/v1/setup/owner", json={"username": "owner", "password": "a-good-password"})

    assert resp.status_code == 201
    assert resp.json() == {"username": "owner"}
    row = await empty_db.fetchone("SELECT username, password_hash, is_admin, mqtt_enabled FROM users")
    assert row["username"] == "owner"
    assert row["is_admin"] == 1
    assert row["mqtt_enabled"] == 0
    assert verify_password("a-good-password", row["password_hash"])
    assert setup_required() is False


async def test_owner_gets_the_root_owner_role_when_a_hierarchy_exists(setup_client, empty_db):
    await empty_db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, created_at, updated_at) VALUES ('tree-1', 'Gebäude', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    await empty_db.execute_and_commit(
        "INSERT INTO hierarchy_nodes (id, tree_id, parent_id, name, created_at, updated_at)"
        " VALUES ('root-1', 'tree-1', NULL, 'Root', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )

    await setup_client.post("/api/v1/setup/owner", json={"username": "owner", "password": "a-good-password"})

    grant = await empty_db.fetchone("SELECT principal_id, node_id, role, effect FROM authz_node_roles")
    assert (grant["principal_id"], grant["node_id"], grant["role"], grant["effect"]) == ("owner", "root-1", "owner", "allow")


async def test_owner_without_a_hierarchy_is_created_without_a_grant(setup_client, empty_db):
    resp = await setup_client.post("/api/v1/setup/owner", json={"username": "owner", "password": "a-good-password"})

    assert resp.status_code == 201
    assert await empty_db.fetchone("SELECT 1 FROM authz_node_roles") is None


async def test_second_claim_is_refused(setup_client):
    await setup_client.post("/api/v1/setup/owner", json={"username": "owner", "password": "a-good-password"})

    resp = await setup_client.post("/api/v1/setup/owner", json={"username": "intruder", "password": "another-password"})

    assert resp.status_code == 409


async def test_claim_is_refused_when_a_user_appeared_behind_the_flag(setup_client, empty_db):
    """The process flag can be stale — the user table decides, in the transaction."""
    await empty_db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, created_at)"
        " VALUES ('u1', 'someone', 'pbkdf2$1$00$00', 0, 0, '2026-01-01T00:00:00Z')"
    )
    set_setup_required(True)

    resp = await setup_client.post("/api/v1/setup/owner", json={"username": "intruder", "password": "another-password"})

    assert resp.status_code == 409
    assert setup_required() is False


async def test_websocket_handshake_is_refused_during_setup(monkeypatch):
    """HTTP middleware cannot see websocket handshakes — the endpoint checks itself."""
    from obs.api.v1 import websocket as ws_module

    set_setup_required(True)

    class _Ws:
        def __init__(self):
            self.headers = {}

    ok, reason = await ws_module._authenticate_ws_request(_Ws())

    assert ok is False
    assert reason == "Setup required"


async def test_claim_endpoint_reports_conflict_before_touching_the_database(empty_db, monkeypatch):
    """Calling the endpoint outside setup mode must not write anything."""
    monkeypatch.setattr(setup_api, "get_db", lambda: empty_db)
    set_setup_required(False)

    with pytest.raises(HTTPException) as exc:
        await setup_api.create_first_owner(
            request=None,  # never reached
            body=FirstOwnerRequest(username="owner", password="a-good-password"),
            db=empty_db,
        )

    assert exc.value.status_code == 409
    assert await empty_db.fetchone("SELECT 1 FROM users") is None
