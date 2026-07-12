"""Regression tests for audited user and central-settings mutations (#630)."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from obs.api.auth import UserCreate, UserUpdate, create_user, delete_user, update_user
from obs.api.v1.system import AppSettingsIn, update_app_settings
from obs.db.database import Database


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": path,
            "query_string": b"",
            "headers": [(b"user-agent", b"audit-test"), (b"x-request-id", b"wave-8")],
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.mark.asyncio
async def test_user_lifecycle_writes_deterministic_non_sensitive_audit(monkeypatch):
    db = Database(":memory:")
    await db.connect()
    monkeypatch.setattr("obs.api.auth.hash_password", lambda _password: "password-hash")
    try:
        created = await create_user(
            body=UserCreate(username="alice", password="create-secret"),
            request=_request("/api/v1/auth/users"),
            _admin="admin",
            db=db,
        )
        updated = await update_user(
            username="alice",
            body=UserUpdate(username="alice-renamed", is_admin=True),
            request=_request("/api/v1/auth/users/alice"),
            _admin="admin",
            db=db,
        )
        await delete_user(
            username="alice-renamed",
            request=_request("/api/v1/auth/users/alice-renamed"),
            admin_user="admin",
            db=db,
        )

        assert updated.id == created.id
        rows = await db.fetchall("SELECT actor, action, resource_type, resource_id, details_json, request_id FROM audit_log_entries ORDER BY id")
        assert [(row["actor"], row["action"], row["resource_type"], row["resource_id"], row["request_id"]) for row in rows] == [
            ("admin", "auth.user.created", "user", created.id, "wave-8"),
            ("admin", "auth.user.updated", "user", created.id, "wave-8"),
            ("admin", "auth.user.deleted", "user", created.id, "wave-8"),
        ]
        assert [json.loads(row["details_json"]) for row in rows] == [
            {"is_admin": False, "mqtt_enabled": False, "username": "alice"},
            {
                "after": {"is_admin": True, "mqtt_enabled": False, "username": "alice-renamed"},
                "before": {"is_admin": False, "mqtt_enabled": False, "username": "alice"},
                "changed_fields": ["is_admin", "username"],
            },
            {"is_admin": True, "mqtt_enabled": False, "username": "alice-renamed"},
        ]
        assert "create-secret" not in "".join(row["details_json"] for row in rows)
        assert "password-hash" not in "".join(row["details_json"] for row in rows)
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_timezone_update_writes_before_after_audit():
    db = Database(":memory:")
    await db.connect()
    try:
        result = await update_app_settings(
            body=AppSettingsIn(timezone="Europe/Berlin"),
            request=_request("/api/v1/system/settings"),
            db=db,
            _user="operator",
        )

        assert result.timezone == "Europe/Berlin"
        row = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='system.settings.updated'")
        assert row is not None
        assert row["actor"] == "operator"
        assert row["resource_type"] == "app_settings"
        assert row["resource_id"] == "global"
        assert json.loads(row["details_json"]) == {
            "after": {"timezone": "Europe/Berlin"},
            "before": {"timezone": "Europe/Zurich"},
            "changed_fields": ["timezone"],
        }
    finally:
        await db.disconnect()
