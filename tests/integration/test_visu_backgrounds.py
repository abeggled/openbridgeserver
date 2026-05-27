"""Integration tests for VISU background image library API (/api/v1/visu/backgrounds)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0bIDATx\x9cc```\x00\x00\x00\x04\x00\x01\x0b\x0e-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_MINIMAL_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


@pytest_asyncio.fixture
async def backgrounds_tmp(tmp_path):
    """Patch _backgrounds_dir() to an isolated temporary directory."""
    backgrounds_dir = tmp_path / "visu_backgrounds"
    backgrounds_dir.mkdir()

    with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=backgrounds_dir):
        yield backgrounds_dir


@pytest.mark.asyncio
async def test_list_backgrounds_empty(client, auth_headers, backgrounds_tmp):
    resp = await client.get("/api/v1/visu/backgrounds", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["backgrounds"] == []


@pytest.mark.asyncio
async def test_list_backgrounds_requires_auth(client, backgrounds_tmp):
    resp = await client.get("/api/v1/visu/backgrounds")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_import_background_png(client, auth_headers, backgrounds_tmp):
    resp = await client.post(
        "/api/v1/visu/backgrounds/import",
        headers=auth_headers,
        files=[("files", ("floorplan.png", _MINIMAL_PNG, "image/png"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert body["names"] == ["floorplan"]
    assert (backgrounds_tmp / "floorplan.png").exists()


@pytest.mark.asyncio
async def test_import_background_svg(client, auth_headers, backgrounds_tmp):
    resp = await client.post(
        "/api/v1/visu/backgrounds/import",
        headers=auth_headers,
        files=[("files", ("blueprint.svg", _MINIMAL_SVG, "image/svg+xml"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert body["names"] == ["blueprint"]
    assert (backgrounds_tmp / "blueprint.svg").exists()


@pytest.mark.asyncio
async def test_import_background_rejects_non_image(client, auth_headers, backgrounds_tmp):
    resp = await client.post(
        "/api/v1/visu/backgrounds/import",
        headers=auth_headers,
        files=[("files", ("notes.txt", b"not an image", "text/plain"))],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_background_is_public(client, backgrounds_tmp):
    (backgrounds_tmp / "public.png").write_bytes(_MINIMAL_PNG)

    resp = await client.get("/api/v1/visu/backgrounds/public")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG")
    assert resp.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_delete_backgrounds(client, auth_headers, backgrounds_tmp):
    (backgrounds_tmp / "floorplan.png").write_bytes(_MINIMAL_PNG)

    resp = await client.request(
        "DELETE",
        "/api/v1/visu/backgrounds",
        headers=auth_headers,
        json={"names": ["floorplan"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert not (backgrounds_tmp / "floorplan.png").exists()


@pytest.mark.asyncio
async def test_get_background_rejects_invalid_name(client, backgrounds_tmp):
    resp = await client.get("/api/v1/visu/backgrounds/../evil")
    assert resp.status_code in (400, 404)
