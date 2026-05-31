"""Integration Tests — Autobackup API

Covers:
  GET    /api/v1/config/autobackup/config           (read defaults)
  PUT    /api/v1/config/autobackup/config           (valid, invalid hour, invalid retention)
  GET    /api/v1/config/autobackup/list
  POST   /api/v1/config/autobackup/run              (creates + returns name)
  POST   /api/v1/config/autobackup/restore/{name}  (invalid name format, not found, success)
  DELETE /api/v1/config/autobackup/{name}           (invalid name format, not found, success)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# GET /config/autobackup/config
# ---------------------------------------------------------------------------


async def test_get_autobackup_config_requires_auth(client):
    resp = await client.get("/api/v1/config/autobackup/config")
    assert resp.status_code == 401


async def test_get_autobackup_config_returns_defaults(client, auth_headers):
    resp = await client.get("/api/v1/config/autobackup/config", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert "hour" in body
    assert "retention_days" in body
    assert isinstance(body["enabled"], bool)
    assert isinstance(body["hour"], int)
    assert isinstance(body["retention_days"], int)


# ---------------------------------------------------------------------------
# PUT /config/autobackup/config
# ---------------------------------------------------------------------------


async def test_put_autobackup_config_requires_auth(client):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": 3, "retention_days": 7},
    )
    assert resp.status_code == 401


async def test_put_autobackup_config_valid(client, auth_headers):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": 2, "retention_days": 14},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["hour"] == 2
    assert body["retention_days"] == 14


async def test_put_autobackup_config_persists(client, auth_headers):
    await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": False, "hour": 5, "retention_days": 3},
        headers=auth_headers,
    )
    resp = await client.get("/api/v1/config/autobackup/config", headers=auth_headers)
    body = resp.json()
    assert body["enabled"] is False
    assert body["hour"] == 5
    assert body["retention_days"] == 3

    # Restore
    await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": False, "hour": 3, "retention_days": 7},
        headers=auth_headers,
    )


async def test_put_autobackup_config_invalid_hour_too_high(client, auth_headers):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": 24, "retention_days": 7},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_put_autobackup_config_invalid_hour_negative(client, auth_headers):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": -1, "retention_days": 7},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_put_autobackup_config_invalid_retention_too_high(client, auth_headers):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": 3, "retention_days": 31},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_put_autobackup_config_invalid_retention_zero(client, auth_headers):
    resp = await client.put(
        "/api/v1/config/autobackup/config",
        json={"enabled": True, "hour": 3, "retention_days": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /config/autobackup/list
# ---------------------------------------------------------------------------


async def test_list_autobackups_requires_auth(client):
    resp = await client.get("/api/v1/config/autobackup/list")
    assert resp.status_code == 401


async def test_list_autobackups_returns_list(client, auth_headers):
    resp = await client.get("/api/v1/config/autobackup/list", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# POST /config/autobackup/run
# ---------------------------------------------------------------------------


async def test_run_autobackup_requires_auth(client):
    resp = await client.post("/api/v1/config/autobackup/run")
    assert resp.status_code == 401


async def test_run_autobackup_creates_backup(client, auth_headers):
    resp = await client.post("/api/v1/config/autobackup/run", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "name" in body
    assert isinstance(body["old_backups_deleted"], int)


async def test_run_autobackup_appears_in_list(client, auth_headers):
    run_resp = await client.post("/api/v1/config/autobackup/run", headers=auth_headers)
    backup_name = run_resp.json()["name"]

    list_resp = await client.get("/api/v1/config/autobackup/list", headers=auth_headers)
    names = [e["name"] for e in list_resp.json()]
    assert backup_name in names


# ---------------------------------------------------------------------------
# DELETE /config/autobackup/{name}
# ---------------------------------------------------------------------------


async def test_delete_autobackup_requires_auth(client):
    resp = await client.delete("/api/v1/config/autobackup/20240101-0300")
    assert resp.status_code == 401


async def test_delete_autobackup_invalid_name_format(client, auth_headers):
    resp = await client.delete("/api/v1/config/autobackup/notadateformat", headers=auth_headers)
    assert resp.status_code == 400


async def test_delete_autobackup_not_found(client, auth_headers):
    resp = await client.delete("/api/v1/config/autobackup/20000101-0000", headers=auth_headers)
    assert resp.status_code == 404


async def test_delete_autobackup_success(client, auth_headers):
    run_resp = await client.post("/api/v1/config/autobackup/run", headers=auth_headers)
    backup_name = run_resp.json()["name"]

    del_resp = await client.delete(f"/api/v1/config/autobackup/{backup_name}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
    assert del_resp.json()["name"] == backup_name

    list_resp = await client.get("/api/v1/config/autobackup/list", headers=auth_headers)
    names = [e["name"] for e in list_resp.json()]
    assert backup_name not in names


# ---------------------------------------------------------------------------
# POST /config/autobackup/restore/{name}
# ---------------------------------------------------------------------------


async def test_restore_autobackup_requires_auth(client):
    resp = await client.post("/api/v1/config/autobackup/restore/20240101-0300")
    assert resp.status_code == 401


async def test_restore_autobackup_invalid_name_format(client, auth_headers):
    resp = await client.post("/api/v1/config/autobackup/restore/notadateformat", headers=auth_headers)
    assert resp.status_code == 400


async def test_restore_autobackup_not_found(client, auth_headers):
    resp = await client.post("/api/v1/config/autobackup/restore/20000101-0000", headers=auth_headers)
    assert resp.status_code == 404


async def test_restore_autobackup_success(client, auth_headers):
    run_resp = await client.post("/api/v1/config/autobackup/run", headers=auth_headers)
    backup_name = run_resp.json()["name"]

    restore_resp = await client.post(
        f"/api/v1/config/autobackup/restore/{backup_name}",
        headers=auth_headers,
    )
    assert restore_resp.status_code == 200
    body = restore_resp.json()
    assert body["ok"] is True
    assert body["name"] == backup_name
    assert isinstance(body["datapoints"], int)
    assert isinstance(body["bindings"], int)
    assert isinstance(body["errors"], list)
