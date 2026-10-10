"""Integration Tests — KNX Project Import API

Covers:
  POST   /api/v1/knxproj/import          wrong extension → 400, empty → 400,
                                          valid .knxproj → 200, with adapter_name,
                                          password-protected ETS6 → correct/wrong/missing password
  GET    /api/v1/knxproj/group-addresses  empty list, populated list, search filter, pagination
  DELETE /api/v1/knxproj/group-addresses  clears all GAs
"""

from __future__ import annotations

import base64
import hashlib
import io
import uuid
import zipfile
from pathlib import Path

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration

_DEMO_KNXPROJ = Path(__file__).parent.parent.parent / "tools" / "Demo-Test-Projekt-2026-04-06-17-18.knxproj"
_HAS_DEMO = _DEMO_KNXPROJ.exists()


async def _import_demo(client, auth_headers) -> None:
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo.knxproj", _DEMO_KNXPROJ.read_bytes(), "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def _make_adapter_instance(client, auth_headers, adapter_type: str = "ANWESENHEITSSIMULATION") -> dict:
    name = f"KnxTest-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": adapter_type, "name": name, "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_non_admin_headers(client, auth_headers) -> tuple[dict, str]:
    username = f"knxproj-user-{uuid.uuid4().hex[:8]}"
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


# ---------------------------------------------------------------------------
# POST /knxproj/import  — error paths (no real knxproj needed)
# ---------------------------------------------------------------------------


async def test_import_knxproj_requires_auth(client):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("test.knxproj", b"dummy", "application/octet-stream")},
    )
    assert resp.status_code == 401


async def test_import_knxproj_non_admin_forbidden(client, auth_headers):
    non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
    try:
        resp = await client.post(
            "/api/v1/knxproj/import",
            files={"file": ("test.knxproj", b"dummy", "application/octet-stream")},
            headers=non_admin_headers,
        )
        assert resp.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_import_knxproj_wrong_extension_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("test.txt", b"dummy content", "text/plain")},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_import_knxproj_empty_file_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("test.knxproj", b"", "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_import_knxproj_invalid_content_returns_400_or_500(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("test.knxproj", b"not a zip file at all", "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 500)


# ---------------------------------------------------------------------------
# POST /knxproj/import  — demo file (skipped when not present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj file not found in tools/")
async def test_import_knxproj_demo_file(client, auth_headers):
    content = _DEMO_KNXPROJ.read_bytes()
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo.knxproj", content, "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] > 0
    assert "message" in body


@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj file not found in tools/")
async def test_import_knxproj_demo_result_shape(client, auth_headers):
    content = _DEMO_KNXPROJ.read_bytes()
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo.knxproj", content, "application/octet-stream")},
        headers=auth_headers,
    )
    body = resp.json()
    for field in ("imported", "created", "updated", "locations", "trades", "message"):
        assert field in body, f"missing: {field}"


@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj file not found in tools/")
async def test_import_knxproj_with_adapter_name(client, auth_headers):
    inst = await _make_adapter_instance(client, auth_headers)
    content = _DEMO_KNXPROJ.read_bytes()
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo.knxproj", content, "application/octet-stream")},
        params={"adapter_name": inst["name"], "direction": "SOURCE"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] > 0


@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj file not found in tools/")
async def test_import_knxproj_adapter_not_found_returns_404(client, auth_headers):
    content = _DEMO_KNXPROJ.read_bytes()
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo.knxproj", content, "application/octet-stream")},
        params={"adapter_name": f"nonexistent-{uuid.uuid4().hex[:8]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /knxproj/group-addresses
# ---------------------------------------------------------------------------


async def test_list_group_addresses_requires_auth(client):
    resp = await client.get("/api/v1/knxproj/group-addresses")
    assert resp.status_code == 401


async def test_list_group_addresses_returns_page(client, auth_headers):
    resp = await client.get("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "items" in body
    assert isinstance(body["items"], list)


async def test_list_group_addresses_after_import(client, auth_headers):
    await _import_demo(client, auth_headers)
    resp = await client.get("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert resp.json()["total"] >= 3


async def test_list_group_addresses_item_shape(client, auth_headers):
    await _import_demo(client, auth_headers)
    resp = await client.get("/api/v1/knxproj/group-addresses", headers=auth_headers)
    items = resp.json()["items"]
    if items:
        for field in ("address", "name", "description", "imported_at"):
            assert field in items[0], f"missing: {field}"


async def test_list_group_addresses_search_by_name(client, auth_headers):
    unique_name = "Licht Wohnzimmer Schalten"
    await _import_demo(client, auth_headers)

    resp = await client.get(
        "/api/v1/knxproj/group-addresses",
        params={"q": unique_name},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    names = [i["name"] for i in body["items"]]
    assert any(unique_name in n for n in names)


async def test_list_group_addresses_search_by_address(client, auth_headers):
    await _import_demo(client, auth_headers)
    resp = await client.get(
        "/api/v1/knxproj/group-addresses",
        params={"q": "1/1/1"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["address"] == "1/1/1" for i in items)


async def test_list_group_addresses_search_no_match(client, auth_headers):
    resp = await client.get(
        "/api/v1/knxproj/group-addresses",
        params={"q": "ZZZNOMATCH99999"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


async def test_list_group_addresses_pagination(client, auth_headers):
    await _import_demo(client, auth_headers)
    resp = await client.get(
        "/api/v1/knxproj/group-addresses",
        params={"size": 1, "page": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 1


# ---------------------------------------------------------------------------
# DELETE /knxproj/group-addresses
# ---------------------------------------------------------------------------


async def test_delete_group_addresses_requires_auth(client):
    resp = await client.delete("/api/v1/knxproj/group-addresses")
    assert resp.status_code == 401


async def test_delete_group_addresses_clears_all(client, auth_headers):
    await _import_demo(client, auth_headers)

    del_resp = await client.delete("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert del_resp.status_code == 204

    list_resp = await client.get("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert list_resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# POST /knxproj/import — password-protected ETS6 files
# ---------------------------------------------------------------------------

_PROTECTED_PWD = "IntegrationTestPassword"
_PROTECTED_SALT = b"21.project.ets.knx.org"


def _make_protected_ets6_knxproj(password: str) -> bytes:
    """Build a minimal ETS6-style password-protected .knxproj with one group address."""
    import pyzipper  # type: ignore[import-untyped]

    aes_pwd = base64.b64encode(
        hashlib.pbkdf2_hmac(
            hash_name="sha256",
            password=password.encode("utf-16-le"),
            salt=_PROTECTED_SALT,
            iterations=65536,
            dklen=32,
        )
    )
    project_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/21">
    <Project Id="P-0001">
        <Installations>
            <Installation Name="Test">
                <GroupAddresses>
                    <GroupRanges>
                        <GroupRange Id="P-0001-0_GR-1" RangeStart="0" RangeEnd="2047" Name="Lights" Puid="1">
                            <GroupRange Id="P-0001-0_GR-2" RangeStart="0" RangeEnd="255" Name="EG" Puid="2">
                                <GroupAddress Id="P-0001-0_GA-1" Address="1" Name="Test GA"
                                              DatapointType="DPST-1-1" Puid="3" Description="" Comment=""/>
                            </GroupRange>
                        </GroupRange>
                    </GroupRanges>
                </GroupAddresses>
            </Installation>
        </Installations>
    </Project>
</KNX>"""
    inner_buf = io.BytesIO()
    with pyzipper.AESZipFile(inner_buf, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as inner_zf:
        inner_zf.setpassword(aes_pwd)
        inner_zf.writestr("0.xml", project_xml)
        inner_zf.writestr("project.xml", b'<KNX xmlns="http://knx.org/xml/project/21"><Project Id="P-0001"/></KNX>')

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as outer_zf:
        outer_zf.writestr("knx_master.xml", b'<KNX xmlns="http://knx.org/xml/project/21"><MasterData /></KNX>')
        outer_zf.writestr("P-0001.signature", b"")
        outer_zf.writestr("P-0001.zip", inner_buf.getvalue())
    return outer_buf.getvalue()


_PROTECTED_BYTES = _make_protected_ets6_knxproj(_PROTECTED_PWD)


async def test_import_password_protected_correct_password(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("protected.knxproj", _PROTECTED_BYTES, "application/octet-stream")},
        data={"password": _PROTECTED_PWD},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] > 0
    assert "message" in body


async def test_import_password_protected_no_password_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("protected.knxproj", _PROTECTED_BYTES, "application/octet-stream")},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "passwort" in detail or "verschl" in detail


async def test_import_password_protected_wrong_password_returns_400(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("protected.knxproj", _PROTECTED_BYTES, "application/octet-stream")},
        data={"password": "WrongPassword"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "passwort" in detail or "verschl" in detail


async def test_import_password_protected_result_shape(client, auth_headers):
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("protected.knxproj", _PROTECTED_BYTES, "application/octet-stream")},
        data={"password": _PROTECTED_PWD},
        headers=auth_headers,
    )
    body = resp.json()
    for field in ("imported", "created", "updated", "locations", "trades", "message"):
        assert field in body, f"missing: {field}"
