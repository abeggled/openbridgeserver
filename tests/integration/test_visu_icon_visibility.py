"""Integration Tests — custom icons in the Visu without login.

A visitor without login gets the icons of the pages they may view: public and
read-only pages directly, PIN pages once the PIN was entered (the Visu sends
its PIN session tokens in X-Session-Tokens). Icons used only on login-only
pages stay hidden; signed-in users still get every icon.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1 1h8v8H1z"/></svg>'


@pytest_asyncio.fixture
async def icons_tmp(tmp_path):
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir()
    with patch("obs.api.v1.icons._icons_dir", return_value=icons_dir):
        yield icons_dir


async def _page(client, auth_headers, access: str, icon: str, *, pin: str | None = None) -> str:
    payload: dict[str, object] = {"name": f"Icons-{uuid.uuid4().hex[:8]}", "type": "PAGE", "order": 999, "access": access}
    if pin:
        payload["access_pin"] = pin
    resp = await client.post("/api/v1/visu/nodes", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    page_id = resp.json()["id"]
    resp = await client.put(
        f"/api/v1/visu/pages/{page_id}",
        json={
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "Link",
                    "datapoint_id": None,
                    "status_datapoint_id": None,
                    "x": 0,
                    "y": 0,
                    "w": 2,
                    "h": 2,
                    "config": {"icon": f"svg:{icon}", "label": "Test"},
                },
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 204), resp.text
    return page_id


async def _names(client, headers=None) -> set[str]:
    resp = await client.get("/api/v1/icons/", headers=headers or {})
    assert resp.status_code == 200, resp.text
    return {icon["name"] for icon in resp.json()["icons"]}


async def test_icons_follow_page_access_without_login(client, auth_headers, icons_tmp):
    suffix = uuid.uuid4().hex[:8]
    public_icon, pin_icon, user_icon = f"pub-{suffix}", f"pin-{suffix}", f"usr-{suffix}"
    for name in (public_icon, pin_icon, user_icon):
        (icons_tmp / f"{name}.svg").write_bytes(SVG)

    await _page(client, auth_headers, "public", public_icon)
    pin_page = await _page(client, auth_headers, "protected", pin_icon, pin="4711")
    await _page(client, auth_headers, "user", user_icon)

    # Without login: only the public page's icon.
    anonymous = await _names(client)
    assert public_icon in anonymous
    assert pin_icon not in anonymous
    assert user_icon not in anonymous
    assert (await client.get(f"/api/v1/icons/{public_icon}")).status_code == 200
    assert (await client.get(f"/api/v1/icons/{pin_icon}")).status_code == 401
    assert (await client.get(f"/api/v1/icons/{user_icon}")).status_code == 401

    # After entering the PIN, the PIN page's icon too — still not the login-only one.
    auth = await client.post(f"/api/v1/visu/nodes/{pin_page}/auth", json={"pin": "4711"})
    assert auth.status_code == 200, auth.text
    tokens = {"X-Session-Tokens": f"unknown-token,{auth.json()['session_token']}"}
    unlocked = await _names(client, tokens)
    assert {public_icon, pin_icon} <= unlocked
    assert user_icon not in unlocked
    assert (await client.get(f"/api/v1/icons/{pin_icon}", headers=tokens)).status_code == 200

    # Signed in: everything, as before.
    assert {public_icon, pin_icon, user_icon} <= await _names(client, auth_headers)

    # Rejected credentials are a 401 on both routes, not a silent fall-back to the anonymous list.
    for rejected in ({"Authorization": "Bearer not-a-jwt"}, {"X-API-Key": "unknown-api-key"}):
        assert (await client.get("/api/v1/icons/", headers=rejected)).status_code == 401, rejected
        assert (await client.get(f"/api/v1/icons/{public_icon}", headers=rejected)).status_code == 401, rejected
