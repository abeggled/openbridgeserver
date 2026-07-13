from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import bcrypt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from obs.api.v1 import config as config_api
from obs.api.v1 import visu as visu_api
from obs.db.database import Database, _migration_v42
from obs.models.visu import PinAuthRequest, VisuNodeCreate


@pytest.mark.asyncio
async def test_clean_install_has_only_central_page_acl_storage() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        tables = {
            row["name"]
            for row in await db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%visu_page%' OR name='visu_node_users'",
            )
        }
        assert "authz_visu_page_policies" in tables
        assert "authz_visu_page_credentials" in tables
        assert "visu_node_users" not in tables
        assert await db.fetchall("SELECT * FROM authz_visu_page_policies") == []
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_v42_populated_upgrade_is_idempotent_secret_safe_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "legacy-v41.sqlite"
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    pin_hash = bcrypt.hashpw(b"2468", bcrypt.gensalt()).decode()
    await conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE visu_nodes (
            id TEXT PRIMARY KEY,
            access TEXT,
            access_pin TEXT
        );
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            is_admin INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE visu_node_users (
            node_id TEXT NOT NULL,
            username TEXT NOT NULL,
            PRIMARY KEY (node_id, username)
        );
        CREATE TABLE authz_node_roles (
            principal_type TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            node_type TEXT NOT NULL,
            node_id TEXT NOT NULL,
            role TEXT NOT NULL,
            effect TEXT NOT NULL DEFAULT 'allow',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (principal_type, principal_id, node_type, node_id)
        );
        INSERT INTO users VALUES ('alice', 0), ('bob', 0), ('admin', 1);
        INSERT INTO visu_nodes VALUES
            ('public', 'public', NULL),
            ('readonly', 'readonly', NULL),
            ('protected', 'protected', NULL),
            ('protected-ok', 'protected', 'PIN_HASH'),
            ('user-page', 'user', NULL);
        INSERT INTO visu_node_users VALUES
            ('user-page', 'alice'),
            ('user-page', 'bob'),
            ('user-page', 'admin'),
            ('user-page', 'ghost'),
            ('public', 'alice');
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'bob', 'visu_page', 'user-page', 'guest', 'deny');
        """.replace("PIN_HASH", pin_hash),
    )

    await _migration_v42(conn)
    await _migration_v42(conn)

    policies = await (
        await conn.execute(
            "SELECT node_id, access_mode FROM authz_visu_page_policies ORDER BY node_id",
        )
    ).fetchall()
    assert [(row["node_id"], row["access_mode"]) for row in policies] == [
        ("protected", "protected"),
        ("protected-ok", "protected"),
        ("public", "public"),
        ("readonly", "readonly"),
        ("user-page", "user"),
    ]
    credentials = await (await conn.execute("SELECT node_id, pin_hash FROM authz_visu_page_credentials")).fetchall()
    assert [(row["node_id"], row["pin_hash"]) for row in credentials] == [("protected-ok", pin_hash)]
    grants = await (
        await conn.execute(
            "SELECT principal_id, node_id, role, effect FROM authz_node_roles ORDER BY principal_id",
        )
    ).fetchall()
    assert [tuple(row) for row in grants] == [
        ("alice", "user-page", "guest", "allow"),
        ("bob", "user-page", "guest", "deny"),
    ]
    assert pin_hash not in json.dumps([dict(row) for row in grants])
    assert await (await conn.execute("SELECT 1 FROM sqlite_master WHERE name='visu_node_users'")).fetchone() is None
    cleared = await (await conn.execute("SELECT access, access_pin FROM visu_nodes")).fetchall()
    assert all(row["access"] is None and row["access_pin"] is None for row in cleared)
    await conn.close()


@pytest.mark.asyncio
async def test_runtime_page_grants_use_tree_inheritance_and_deny_precedence() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        now = "2026-07-13T00:00:00Z"
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('u1', 'alice', 'x', 0, ?)",
            (now,),
        )
        await db.execute_and_commit(
            """INSERT INTO visu_nodes (id, parent_id, name, type, page_config, created_at, updated_at)
               VALUES ('folder', NULL, 'Folder', 'LOCATION', '{}', ?, ?),
                      ('page', 'folder', 'Page', 'PAGE', '{}', ?, ?)""",
            (now, now, now, now),
        )
        await db.execute_and_commit(
            "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('folder', 'user')",
        )
        await db.execute_and_commit(
            """INSERT INTO authz_node_roles
                   (principal_type, principal_id, node_type, node_id, role, effect)
               VALUES ('user', 'alice', 'visu_page', 'folder', 'guest', 'allow'),
                      ('user', 'alice', 'visu_page', 'page', 'guest', 'deny')""",
        )

        assert await visu_api._check_user_access(db, "folder", "alice") is True
        assert await visu_api._check_user_access(db, "page", "alice") is False
    finally:
        await db.disconnect()


class _EmptyRegistry:
    def all(self):
        return []

    def get(self, _id):
        return None


@pytest.mark.asyncio
async def test_config_export_import_keeps_policy_and_grants_but_never_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db = Database(":memory:")
    await db.connect()
    monkeypatch.setattr(config_api, "get_registry", lambda: _EmptyRegistry())
    from obs.api.v1 import icons as icons_api

    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    monkeypatch.setattr("obs.adapters.registry.stop_all", AsyncMock())
    monkeypatch.setattr("obs.adapters.registry.start_all", AsyncMock())
    monkeypatch.setattr("obs.adapters.registry.get_all_instances", lambda: {})
    monkeypatch.setattr("obs.core.event_bus.get_event_bus", MagicMock())
    try:
        now = "2026-07-13T00:00:00Z"
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES ('u1', 'alice', 'x', 0, ?)",
            (now,),
        )
        protected = await visu_api.create_node(
            VisuNodeCreate(name="Protected", access="protected", access_pin="9876"),
            db=db,
            _user="admin",
        )
        user_page = await visu_api.create_node(VisuNodeCreate(name="User", access="user"), db=db, _user="admin")
        await db.execute_and_commit(
            """INSERT INTO authz_node_roles
                   (principal_type, principal_id, node_type, node_id, role, effect)
               VALUES ('user', 'alice', 'visu_page', ?, 'guest', 'allow')""",
            (user_page.id,),
        )
        credential = await db.fetchone("SELECT pin_hash FROM authz_visu_page_credentials WHERE node_id=?", (protected.id,))
        assert credential is not None

        exported = await config_api.export_config(_user="admin", db=db)
        payload = exported.model_dump_json()
        assert credential["pin_hash"] not in payload
        assert "access_pin" not in payload

        await db.execute_and_commit("DELETE FROM authz_node_roles WHERE node_type='visu_page'")
        result = await config_api.import_config(exported, _user="admin", db=db)
        assert result.errors == []
        policy = await db.fetchone("SELECT access_mode FROM authz_visu_page_policies WHERE node_id=?", (protected.id,))
        assert policy["access_mode"] == "protected"
        assert await db.fetchone("SELECT 1 FROM authz_visu_page_credentials WHERE node_id=?", (protected.id,)) is None
        grant = await db.fetchone(
            "SELECT role, effect FROM authz_node_roles WHERE principal_id='alice' AND node_type='visu_page' AND node_id=?",
            (user_page.id,),
        )
        assert dict(grant) == {"role": "guest", "effect": "allow"}

        with pytest.raises(HTTPException) as exc_info:
            request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})
            await visu_api.pin_auth(protected.id, PinAuthRequest(pin="9876"), request, db=db)
        assert exc_info.value.status_code == 403
    finally:
        await db.disconnect()


def test_config_import_rejects_legacy_pin_secret_field() -> None:
    with pytest.raises(ValueError):
        config_api.ExportedVisuNode(
            id="page",
            parent_id=None,
            name="Page",
            type="PAGE",
            node_order=0,
            icon=None,
            access="protected",
            access_pin="secret",
            page_config=None,
        )
