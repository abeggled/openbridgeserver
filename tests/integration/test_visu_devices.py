"""Integration Tests — Visu device profiles

Covers:
  GET /api/v1/system/visu-devices → public list
  PUT /api/v1/system/visu-devices → replace all profiles (admin only)
  config import validation of the ``visu.devices`` app setting
"""

from __future__ import annotations

import json
import uuid

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration

URL = "/api/v1/system/visu-devices"


async def _create_page(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/visu/nodes",
        json={"name": f"Start-{uuid.uuid4().hex[:8]}", "type": "PAGE", "order": 999, "access": "public"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _non_admin_headers(client, auth_headers) -> dict:
    username = f"visu-dev-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "TestPass123!", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {create_access_token(username)}"}


@pytest.fixture
async def reset_devices(client, auth_headers):
    yield
    await client.put(URL, json={"devices": []}, headers=auth_headers)


async def test_get_is_public_and_empty_by_default(client, auth_headers, reset_devices):
    await client.put(URL, json={"devices": []}, headers=auth_headers)
    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_put_stores_profiles_and_get_returns_them_without_login(client, auth_headers, reset_devices):
    page_id = await _create_page(client, auth_headers)
    devices = [
        {"slug": "visu-pc", "name": " Visu-PC Flur ", "start_node_id": page_id, "return_after_s": 120},
        {"slug": "handy", "name": "Handy", "start_node_id": "", "return_after_s": 0},
    ]

    resp = await client.put(URL, json={"devices": devices}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    expected = [
        {"slug": "visu-pc", "name": "Visu-PC Flur", "start_node_id": page_id, "return_after_s": 120},
        {"slug": "handy", "name": "Handy", "start_node_id": None, "return_after_s": 0},
    ]
    assert resp.json() == expected
    # The public list also says whether each start page still exists.
    assert (await client.get(URL)).json() == [{**d, "start_node_missing": False} for d in expected]

    # A deleted start page stays stored (the Admin GUI marks it), but the Visu learns it is gone.
    resp = await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
    assert resp.status_code in (200, 204), resp.text
    assert [d["start_node_missing"] for d in (await client.get(URL)).json()] == [True, False]


async def test_put_requires_login(client, reset_devices):
    resp = await client.put(URL, json={"devices": []})
    assert resp.status_code == 401


async def test_put_requires_admin(client, auth_headers, reset_devices):
    headers = await _non_admin_headers(client, auth_headers)
    resp = await client.put(URL, json={"devices": [{"slug": "x", "name": "X"}]}, headers=headers)
    assert resp.status_code == 403
    assert (await client.get(URL)).json() == []


async def test_put_rejects_unknown_start_page(client, auth_headers, reset_devices):
    missing = str(uuid.uuid4())
    resp = await client.put(
        URL,
        json={"devices": [{"slug": "panel", "name": "Panel", "start_node_id": missing}]},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert missing in resp.json()["detail"]


@pytest.mark.parametrize(
    "devices",
    [
        [{"slug": "Handy", "name": "Handy"}],  # uppercase
        [{"slug": "-handy", "name": "Handy"}],  # leading hyphen
        [{"slug": "handy", "name": "   "}],  # blank name
        [{"slug": "handy", "name": "Handy", "return_after_s": -1}],
        [{"slug": "handy", "name": "Handy", "return_after_s": 86_401}],
        [{"slug": "handy", "name": "A"}, {"slug": "handy", "name": "B"}],  # duplicate slug
    ],
)
async def test_put_validates_profiles(client, auth_headers, reset_devices, devices):
    resp = await client.put(URL, json={"devices": devices}, headers=auth_headers)
    assert resp.status_code == 422


async def test_config_import_rejects_invalid_profiles_but_keeps_valid_ones(client, auth_headers, reset_devices):
    # A minimal import document: only the app setting under test, nothing else to upsert.
    document = {
        "obs_version": "test",
        "exported_at": "2026-10-18T00:00:00+00:00",
        "datapoints": [],
        "bindings": [],
        "app_settings": [{"key": "visu.devices", "value": "not json"}],
    }
    resp = await client.post("/api/v1/config/import", json=document, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert any("visu.devices" in e for e in resp.json()["errors"])
    assert (await client.get(URL)).json() == []

    good = [{"slug": "imported", "name": "Imported", "start_node_id": None, "return_after_s": 30}]
    document["app_settings"] = [{"key": "visu.devices", "value": json.dumps(good)}]
    resp = await client.post("/api/v1/config/import", json=document, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert (await client.get(URL)).json() == [{**d, "start_node_missing": False} for d in good]
