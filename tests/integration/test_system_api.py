"""Integration Tests — System API

Covers endpoints not tested by test_nav_links.py:
  GET  /api/v1/system/health           (no auth)
  GET  /api/v1/system/adapters
  GET  /api/v1/system/datatypes
  GET  /api/v1/system/settings
  PUT  /api/v1/system/settings         (valid timezone, invalid timezone)
  GET  /api/v1/system/history/settings
  PUT  /api/v1/system/history/settings (valid, invalid plugin)
  POST /api/v1/system/history/test     (sqlite, influxdb unreachable, unknown plugin)
  GET  /api/v1/system/logs             (no filter, level filter, limit)
  GET  /api/v1/system/log-level
  PUT  /api/v1/system/log-level        (valid level, invalid level)
"""

from __future__ import annotations

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration


async def _create_non_admin_headers(client, auth_headers) -> tuple[dict, str]:
    import uuid

    username = f"sys-user-{uuid.uuid4().hex[:8]}"
    password = "pw-12345678"
    resp = await client.post(
        "/api/v1/auth/users",
        json={
            "username": username,
            "password": password,
            "is_admin": False,
            "mqtt_enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {create_access_token(username)}"}, username


def _headers_for(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(username)}"}


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


async def test_health_no_auth_required(client):
    resp = await client.get("/api/v1/system/health")
    assert resp.status_code == 200


async def test_health_returns_expected_fields(client):
    resp = await client.get("/api/v1/system/health")
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert isinstance(body["datapoints"], int)
    assert isinstance(body["adapters_running"], int)


# ---------------------------------------------------------------------------
# GET /adapters
# ---------------------------------------------------------------------------


async def test_adapters_detail_requires_auth(client):
    resp = await client.get("/api/v1/system/adapters")
    assert resp.status_code == 401


async def test_adapters_detail_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/system/adapters", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_adapters_detail_entry_shape(client, auth_headers):
    resp = await client.get("/api/v1/system/adapters", headers=auth_headers)
    assert resp.status_code == 200
    for entry in resp.json():
        assert "adapter_type" in entry
        assert "name" in entry
        assert "registered" in entry
        assert "running" in entry
        assert "connected" in entry
        assert "bindings" in entry


# ---------------------------------------------------------------------------
# GET /datatypes
# ---------------------------------------------------------------------------


async def test_datatypes_requires_auth(client):
    resp = await client.get("/api/v1/system/datatypes")
    assert resp.status_code == 401


async def test_datatypes_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/system/datatypes", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


async def test_datatypes_entry_shape(client, auth_headers):
    resp = await client.get("/api/v1/system/datatypes", headers=auth_headers)
    for entry in resp.json():
        assert "name" in entry
        assert "python_type" in entry
        assert "description" in entry


async def test_datatypes_contains_boolean(client, auth_headers):
    resp = await client.get("/api/v1/system/datatypes", headers=auth_headers)
    names = [d["name"] for d in resp.json()]
    assert "BOOLEAN" in names


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------


async def test_get_settings_requires_auth(client):
    resp = await client.get("/api/v1/system/settings")
    assert resp.status_code == 401


async def test_get_settings_returns_timezone(client, auth_headers):
    resp = await client.get("/api/v1/system/settings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "timezone" in body
    assert isinstance(body["timezone"], str)


# ---------------------------------------------------------------------------
# PUT /settings
# ---------------------------------------------------------------------------


async def test_put_settings_requires_auth(client):
    resp = await client.put("/api/v1/system/settings", json={"timezone": "UTC"})
    assert resp.status_code == 401


async def test_put_settings_valid_timezone(client, auth_headers):
    resp = await client.put(
        "/api/v1/system/settings",
        json={"timezone": "Europe/Berlin"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Berlin"
    await client.put("/api/v1/system/settings", json={"timezone": "Europe/Zurich"}, headers=auth_headers)


async def test_put_settings_invalid_timezone_returns_422(client, auth_headers):
    resp = await client.put(
        "/api/v1/system/settings",
        json={"timezone": "Not/A/Valid/Timezone"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_put_settings_persists(client, auth_headers):
    await client.put("/api/v1/system/settings", json={"timezone": "America/New_York"}, headers=auth_headers)
    resp = await client.get("/api/v1/system/settings", headers=auth_headers)
    assert resp.json()["timezone"] == "America/New_York"
    await client.put("/api/v1/system/settings", json={"timezone": "Europe/Zurich"}, headers=auth_headers)


# ---------------------------------------------------------------------------
# GET /history/settings
# ---------------------------------------------------------------------------


async def test_get_history_settings_requires_auth(client):
    resp = await client.get("/api/v1/system/history/settings")
    assert resp.status_code == 401


async def test_get_history_settings_returns_expected_fields(client, auth_headers):
    resp = await client.get("/api/v1/system/history/settings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "plugin" in body
    assert "default_window_hours" in body
    assert "influx_url" in body
    assert "timescale_dsn" in body


async def test_get_history_settings_default_plugin_is_sqlite(client, auth_headers):
    resp = await client.get("/api/v1/system/history/settings", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["plugin"] == "sqlite"


# ---------------------------------------------------------------------------
# PUT /history/settings
# ---------------------------------------------------------------------------


async def test_put_history_settings_invalid_plugin(client, auth_headers):
    resp = await client.put(
        "/api/v1/system/history/settings",
        json={"plugin": "nonexistent_backend", "default_window_hours": 168},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_put_history_settings_sqlite_roundtrip(client, auth_headers):
    resp = await client.put(
        "/api/v1/system/history/settings",
        json={"plugin": "sqlite", "default_window_hours": 72},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plugin"] == "sqlite"
    assert body["default_window_hours"] == 72
    await client.put(
        "/api/v1/system/history/settings",
        json={"plugin": "sqlite", "default_window_hours": 168},
        headers=auth_headers,
    )


async def test_put_history_settings_writes_audit_log_entry(client, auth_headers):
    from obs.db.database import get_db

    resp = await client.put(
        "/api/v1/system/history/settings",
        json={"plugin": "sqlite", "default_window_hours": 96},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    row = await get_db().fetchone(
        """
        SELECT actor, action, resource_type, resource_id, details_json
        FROM audit_log_entries
        ORDER BY id DESC
        LIMIT 1
        """
    )
    assert row is not None
    assert row["actor"] == "admin"
    assert row["action"] == "system.history.settings.updated"
    assert row["resource_type"] == "history_settings"
    assert row["resource_id"] == "global"
    assert "sqlite" in row["details_json"]

    await client.put(
        "/api/v1/system/history/settings",
        json={"plugin": "sqlite", "default_window_hours": 168},
        headers=auth_headers,
    )


async def test_history_settings_redacts_sensitive_fields_in_responses(client, auth_headers):
    secret_token = "tok_live_super_secret"
    secret_password = "pass_live_super_secret"

    try:
        put_resp = await client.put(
            "/api/v1/system/history/settings",
            json={
                "plugin": "sqlite",
                "default_window_hours": 48,
                "influx_token": secret_token,
                "influx_password": secret_password,
            },
            headers=auth_headers,
        )
        assert put_resp.status_code == 200
        put_body = put_resp.json()
        assert put_body["influx_token"] == "[redacted]"
        assert put_body["influx_password"] == "[redacted]"

        get_resp = await client.get("/api/v1/system/history/settings", headers=auth_headers)
        assert get_resp.status_code == 200
        get_body = get_resp.json()
        assert get_body["influx_token"] == "[redacted]"
        assert get_body["influx_password"] == "[redacted]"
    finally:
        await client.put(
            "/api/v1/system/history/settings",
            json={
                "plugin": "sqlite",
                "default_window_hours": 168,
                "influx_token": "",
                "influx_password": "",
            },
            headers=auth_headers,
        )


async def test_put_history_settings_preserves_existing_secrets_on_redacted_marker(client, auth_headers):
    from obs.db.database import get_db

    secret_token = "tok_keep_me_123"
    secret_password = "pass_keep_me_123"
    secret_dsn = "postgresql://obs:secret@db.local/obs"

    try:
        seed = await client.put(
            "/api/v1/system/history/settings",
            json={
                "plugin": "sqlite",
                "default_window_hours": 48,
                "influx_token": secret_token,
                "influx_password": secret_password,
                "timescale_dsn": secret_dsn,
            },
            headers=auth_headers,
        )
        assert seed.status_code == 200, seed.text

        preserve = await client.put(
            "/api/v1/system/history/settings",
            json={
                "plugin": "sqlite",
                "default_window_hours": 49,
                "influx_token": "[redacted]",
                "influx_password": "[redacted]",
                "timescale_dsn": "[redacted]",
            },
            headers=auth_headers,
        )
        assert preserve.status_code == 200, preserve.text

        db = get_db()
        token_row = await db.fetchone("SELECT value FROM app_settings WHERE key='history.influx_token'")
        password_row = await db.fetchone("SELECT value FROM app_settings WHERE key='history.influx_password'")
        dsn_row = await db.fetchone("SELECT value FROM app_settings WHERE key='history.timescale_dsn'")
        assert token_row["value"] == secret_token
        assert password_row["value"] == secret_password
        assert dsn_row["value"] == secret_dsn
    finally:
        await client.put(
            "/api/v1/system/history/settings",
            json={
                "plugin": "sqlite",
                "default_window_hours": 168,
                "influx_token": "",
                "influx_password": "",
                "timescale_dsn": "",
            },
            headers=auth_headers,
        )


# ---------------------------------------------------------------------------
# POST /history/test
# ---------------------------------------------------------------------------


async def test_history_test_sqlite_always_ok(client, auth_headers):
    resp = await client.post("/api/v1/system/history/test", json={"plugin": "sqlite"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "SQLite" in body["message"]


async def test_history_test_influxdb_unreachable(client, auth_headers):
    resp = await client.post(
        "/api/v1/system/history/test",
        json={
            "plugin": "influxdb",
            "influx_url": "http://127.0.0.1:19999",
            "influx_version": 2,
            "influx_token": "test",
            "influx_org": "obs",
            "influx_bucket": "obs",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "ok" in resp.json()


async def test_history_test_unknown_plugin_returns_false(client, auth_headers):
    resp = await client.post(
        "/api/v1/system/history/test",
        json={"plugin": "unknownplugin"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "Unknown plugin" in body["message"]


async def test_history_test_requires_auth(client):
    resp = await client.post("/api/v1/system/history/test", json={"plugin": "sqlite"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /logs
# ---------------------------------------------------------------------------


async def test_get_logs_requires_auth(client):
    resp = await client.get("/api/v1/system/logs")
    assert resp.status_code == 401


async def test_get_logs_allows_authenticated_users(client, auth_headers):
    username = "system-logs-non-admin"
    created = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "pw-12345678", "is_admin": False},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    try:
        resp = await client.get("/api/v1/system/logs", headers=_headers_for(username))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_get_logs_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/system/logs", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_logs_entry_shape(client, auth_headers):
    resp = await client.get("/api/v1/system/logs", headers=auth_headers)
    for entry in resp.json():
        assert "ts" in entry
        assert "level" in entry
        assert "logger" in entry
        assert "message" in entry


async def test_get_logs_level_filter(client, auth_headers):
    resp = await client.get("/api/v1/system/logs", params={"level": "INFO"}, headers=auth_headers)
    assert resp.status_code == 200
    for entry in resp.json():
        assert entry["level"] == "INFO"


async def test_get_logs_limit_respected(client, auth_headers):
    resp = await client.get("/api/v1/system/logs", params={"limit": 5}, headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) <= 5


async def test_get_logs_unknown_level_returns_empty(client, auth_headers):
    resp = await client.get("/api/v1/system/logs", params={"level": "NONEXISTENT_LEVEL"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /log-level
# ---------------------------------------------------------------------------


async def test_get_log_level_requires_auth(client):
    resp = await client.get("/api/v1/system/log-level")
    assert resp.status_code == 401


async def test_get_log_level_non_admin_forbidden(client, auth_headers):
    user_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.get("/api/v1/system/log-level", headers=user_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_get_log_level_returns_level(client, auth_headers):
    resp = await client.get("/api/v1/system/log-level", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "level" in body
    assert isinstance(body["level"], str)


# ---------------------------------------------------------------------------
# PUT /log-level
# ---------------------------------------------------------------------------


async def test_put_log_level_requires_auth(client):
    resp = await client.put("/api/v1/system/log-level", json={"level": "INFO"})
    assert resp.status_code == 401


async def test_put_log_level_non_admin_forbidden(client, auth_headers):
    user_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.put("/api/v1/system/log-level", json={"level": "INFO"}, headers=user_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_put_log_level_valid(client, auth_headers):
    resp = await client.put("/api/v1/system/log-level", json={"level": "WARNING"}, headers=auth_headers)
    assert resp.status_code == 204
    await client.put("/api/v1/system/log-level", json={"level": "INFO"}, headers=auth_headers)


async def test_put_log_level_invalid_returns_422(client, auth_headers):
    resp = await client.put("/api/v1/system/log-level", json={"level": "VERBOSE"}, headers=auth_headers)
    assert resp.status_code == 422


async def test_put_log_level_case_insensitive(client, auth_headers):
    resp = await client.put("/api/v1/system/log-level", json={"level": "error"}, headers=auth_headers)
    assert resp.status_code == 204
    await client.put("/api/v1/system/log-level", json={"level": "INFO"}, headers=auth_headers)
