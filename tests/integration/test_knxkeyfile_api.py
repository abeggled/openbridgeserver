"""Integration tests for KNX keyfile API hardening."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from obs.api.auth import create_access_token
from xknx.secure.keyring import InterfaceType

import obs.api.v1.knxkeyfile as _knxkeyfile_module

pytestmark = pytest.mark.integration


async def _create_non_admin_headers(client, auth_headers) -> tuple[dict, str]:
    username = f"knx-user-{uuid.uuid4().hex[:8]}"
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


async def test_knx_scan_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.get("/api/v1/knx/scan", headers=non_admin_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_knx_keyfile_upload_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            "/api/v1/knx/keyfile",
            files={"file": ("test.knxkeys", b"dummy", "application/octet-stream")},
            data={"password": "secret"},
            headers=non_admin_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_knx_keyfile_delete_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.delete(f"/api/v1/knx/keyfile/{uuid.uuid4()}", headers=non_admin_headers)
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_knx_keyfile_upload_returns_usable_file_path(client, auth_headers, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(_knxkeyfile_module, "_keyfiles_dir", lambda: tmp_path)

    keyring = SimpleNamespace(
        project_name="Test Project",
        interfaces=[
            SimpleNamespace(
                type=InterfaceType.TUNNELING,
                individual_address="1.1.10",
                host="192.168.1.2",
                user_id=2,
                group_addresses=["1/2/3"],
            )
        ],
        backbone=None,
    )
    monkeypatch.setattr(_knxkeyfile_module, "_parse_keyring", lambda _path, _password: keyring)

    resp = await client.post(
        "/api/v1/knx/keyfile",
        files={"file": ("test.knxkeys", b"dummy-keyfile-content", "application/octet-stream")},
        data={"password": "secret"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_id"]
    assert body["file_path"] == str(tmp_path / f"{body['file_id']}.knxkeys")
    stored = tmp_path / f"{body['file_id']}.knxkeys"
    assert stored.exists()
