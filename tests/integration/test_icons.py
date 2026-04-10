"""
Integration tests for the Icons Library API (/api/v1/icons/).

Requires a running FastAPI app with lifespan (session fixture from conftest.py).
Each test uses its own temporary icons directory to avoid cross-test pollution.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

_MINIMAL_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M1 1"/></svg>'
_MINIMAL_SVG2 = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4"/></svg>'


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_zip(*files: tuple[str, bytes]) -> bytes:
    """Build an in-memory ZIP from (filename, content) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files:
            zf.writestr(name, content)
    return buf.getvalue()


@pytest_asyncio.fixture
async def icons_tmp(tmp_path, monkeypatch):
    """
    Patch _icons_dir() to use a fresh temporary directory for each test.
    Returns the Path to that directory.
    """
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir()

    with patch("obs.api.v1.icons._icons_dir", return_value=icons_dir):
        yield icons_dir


# ---------------------------------------------------------------------------
# GET /icons/ — list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_icons_empty(client, auth_headers, icons_tmp):
    resp = await client.get("/api/v1/icons/", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["icons"] == []


@pytest.mark.asyncio
async def test_list_icons_with_files(client, auth_headers, icons_tmp):
    (icons_tmp / "home.svg").write_bytes(_MINIMAL_SVG)
    (icons_tmp / "star.svg").write_bytes(_MINIMAL_SVG2)

    resp = await client.get("/api/v1/icons/", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {i["name"] for i in body["icons"]}
    assert names == {"home", "star"}


@pytest.mark.asyncio
async def test_list_icons_requires_auth(client, icons_tmp):
    resp = await client.get("/api/v1/icons/")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /icons/import — upload SVG
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_single_svg(client, auth_headers, icons_tmp):
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[("files", ("home.svg", _MINIMAL_SVG, "image/svg+xml"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert "home" in body["names"]
    assert (icons_tmp / "home.svg").exists()


@pytest.mark.asyncio
async def test_import_multiple_svgs(client, auth_headers, icons_tmp):
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[
            ("files", ("home.svg",  _MINIMAL_SVG,  "image/svg+xml")),
            ("files", ("star.svg",  _MINIMAL_SVG2, "image/svg+xml")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 2


@pytest.mark.asyncio
async def test_import_zip_with_svgs(client, auth_headers, icons_tmp):
    zip_bytes = _make_zip(
        ("home.svg", _MINIMAL_SVG),
        ("star.svg", _MINIMAL_SVG2),
    )
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[("files", ("icons.zip", zip_bytes, "application/zip"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 2
    assert (icons_tmp / "home.svg").exists()
    assert (icons_tmp / "star.svg").exists()


@pytest.mark.asyncio
async def test_import_rejects_non_svg(client, auth_headers, icons_tmp):
    fake_svg = b'{"not": "an svg"}'
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[("files", ("fake.svg", fake_svg, "image/svg+xml"))],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_import_zip_skips_non_svg_members(client, auth_headers, icons_tmp):
    zip_bytes = _make_zip(
        ("home.svg", _MINIMAL_SVG),
        ("readme.txt", b"just a readme"),
        ("image.png", b"\x89PNG\r\n"),
    )
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[("files", ("mixed.zip", zip_bytes, "application/zip"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert body["skipped"] >= 1


@pytest.mark.asyncio
async def test_import_invalid_zip(client, auth_headers, icons_tmp):
    resp = await client.post(
        "/api/v1/icons/import",
        headers=auth_headers,
        files=[("files", ("bad.zip", b"not a real zip", "application/zip"))],
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /icons/{name} — get single icon
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_icon(client, auth_headers, icons_tmp):
    (icons_tmp / "home.svg").write_bytes(_MINIMAL_SVG)

    resp = await client.get("/api/v1/icons/home", headers=auth_headers)
    assert resp.status_code == 200
    assert b"<svg" in resp.content
    assert resp.headers["content-type"].startswith("image/svg+xml")


@pytest.mark.asyncio
async def test_get_icon_not_found(client, auth_headers, icons_tmp):
    resp = await client.get("/api/v1/icons/nonexistent", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /icons/ — delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_icons(client, auth_headers, icons_tmp):
    (icons_tmp / "home.svg").write_bytes(_MINIMAL_SVG)
    (icons_tmp / "star.svg").write_bytes(_MINIMAL_SVG2)

    # httpx.AsyncClient.delete() hat kein json=-Argument → request() verwenden
    resp = await client.request(
        "DELETE",
        "/api/v1/icons/",
        headers=auth_headers,
        json={"names": ["home"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert not (icons_tmp / "home.svg").exists()
    assert (icons_tmp / "star.svg").exists()


@pytest.mark.asyncio
async def test_delete_icons_not_found(client, auth_headers, icons_tmp):
    resp = await client.request(
        "DELETE",
        "/api/v1/icons/",
        headers=auth_headers,
        json={"names": ["ghost"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 0
    assert "ghost" in body["not_found"]


# ---------------------------------------------------------------------------
# POST /icons/export — export ZIP (JSON-Body, kein URL-Limit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_all_icons(client, auth_headers, icons_tmp):
    (icons_tmp / "home.svg").write_bytes(_MINIMAL_SVG)
    (icons_tmp / "star.svg").write_bytes(_MINIMAL_SVG2)

    resp = await client.post(
        "/api/v1/icons/export",
        headers=auth_headers,
        json={"names": []},   # leer = alle
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
    assert "home.svg" in names
    assert "star.svg" in names


@pytest.mark.asyncio
async def test_export_selected_icons(client, auth_headers, icons_tmp):
    (icons_tmp / "home.svg").write_bytes(_MINIMAL_SVG)
    (icons_tmp / "star.svg").write_bytes(_MINIMAL_SVG2)

    resp = await client.post(
        "/api/v1/icons/export",
        headers=auth_headers,
        json={"names": ["home"]},
    )
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
    assert "home.svg" in names
    assert "star.svg" not in names


@pytest.mark.asyncio
async def test_export_empty_returns_404(client, auth_headers, icons_tmp):
    # Keine Icons im Verzeichnis → 404
    resp = await client.post(
        "/api/v1/icons/export",
        headers=auth_headers,
        json={"names": []},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_large_selection(client, auth_headers, icons_tmp):
    """Grosse Selektion via POST-Body — kein URL-Längenlimit."""
    # 100 Icons anlegen
    for i in range(100):
        (icons_tmp / f"icon_{i:03d}.svg").write_bytes(_MINIMAL_SVG)

    names = [f"icon_{i:03d}" for i in range(100)]
    resp = await client.post(
        "/api/v1/icons/export",
        headers=auth_headers,
        json={"names": names},
    )
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 100


# ---------------------------------------------------------------------------
# POST /icons/fontawesome — FontAwesome import (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fontawesome_import_free(client, auth_headers, icons_tmp):
    """FontAwesome free CDN path — mock the HTTP response."""
    from unittest.mock import AsyncMock, MagicMock, patch as _patch

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _MINIMAL_SVG

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with _patch("obs.api.v1.icons.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            "/api/v1/icons/fontawesome",
            headers=auth_headers,
            json={"icons": ["home"], "style": "solid"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert "home-solid" in body["names"]  # Dateiname enthält Style seit #89


@pytest.mark.asyncio
async def test_fontawesome_import_skips_failed(client, auth_headers, icons_tmp):
    """If CDN returns 404 for an icon, it should be counted as skipped."""
    from unittest.mock import AsyncMock, MagicMock, patch as _patch

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.content = b""

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with _patch("obs.api.v1.icons.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            "/api/v1/icons/fontawesome",
            headers=auth_headers,
            json={"icons": ["nonexistent-icon-xyz"], "style": "solid"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_fontawesome_import_empty_list(client, auth_headers, icons_tmp):
    resp = await client.post(
        "/api/v1/icons/fontawesome",
        headers=auth_headers,
        json={"icons": [], "style": "solid"},
    )
    assert resp.status_code == 400
