"""Tests for Visu SPA static file routes (favicon.svg, manifest.webmanifest).

These routes are only registered when frontend_dist/ exists on disk at
create_app() time, so the fixture creates a minimal directory temporarily.
No MQTT/DB startup is needed — static file routes have no lifespan dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest_asyncio.fixture
async def visu_dist_client(tmp_path):
    """AsyncClient wired to a fresh app instance that has frontend_dist in place."""
    from obs.config import (
        DatabaseSettings,
        MosquittoSettings,
        MqttSettings,
        SecuritySettings,
        Settings,
        get_settings,
        override_settings,
    )
    from obs.main import create_app

    saved_settings = get_settings()
    override_settings(
        Settings(
            database=DatabaseSettings(path=str(tmp_path / "test.db")),
            mqtt=MqttSettings(host="localhost", port=11883, username=None, password=None),
            security=SecuritySettings(
                jwt_secret="test-secret-32-chars-xxxxxxxxxxxx",
                jwt_expire_minutes=60,
                url_target_allowlist_path=str(tmp_path / "allowlist.yaml"),
            ),
            mosquitto=MosquittoSettings(
                passwd_file=str(tmp_path / "passwd"),
                reload_pid=None,
                reload_command=None,
                service_username="obs",
                service_password="test",
            ),
        )
    )

    frontend_dist = _PROJECT_ROOT / "frontend_dist"
    created_dir = not frontend_dist.exists()
    created_files: list[Path] = []

    try:
        frontend_dist.mkdir(exist_ok=True)
        for name, content in [
            ("favicon.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
            ("manifest.webmanifest", b'{"name":"OBS Visu"}'),
            ("apple-touch-icon.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
            ("index.html", b"<html/>"),
        ]:
            target = frontend_dist / name
            if not target.exists():
                target.write_bytes(content)
                created_files.append(target)

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    finally:
        for f in created_files:
            f.unlink(missing_ok=True)
        if created_dir:
            try:
                frontend_dist.rmdir()
            except OSError:
                pass
        override_settings(saved_settings)


@pytest_asyncio.fixture
async def gui_dist_client(tmp_path):
    """AsyncClient wired to a fresh app instance that has gui_dist in place."""
    from obs.config import (
        DatabaseSettings,
        MosquittoSettings,
        MqttSettings,
        SecuritySettings,
        Settings,
        get_settings,
        override_settings,
    )
    from obs.main import create_app

    saved_settings = get_settings()
    override_settings(
        Settings(
            database=DatabaseSettings(path=str(tmp_path / "test.db")),
            mqtt=MqttSettings(host="localhost", port=11883, username=None, password=None),
            security=SecuritySettings(
                jwt_secret="test-secret-32-chars-xxxxxxxxxxxx",
                jwt_expire_minutes=60,
                url_target_allowlist_path=str(tmp_path / "allowlist.yaml"),
            ),
            mosquitto=MosquittoSettings(
                passwd_file=str(tmp_path / "passwd"),
                reload_pid=None,
                reload_command=None,
                service_username="obs",
                service_password="test",
            ),
        )
    )

    gui_dist = _PROJECT_ROOT / "gui_dist"
    created_dir = not gui_dist.exists()
    created_files: list[Path] = []

    try:
        gui_dist.mkdir(exist_ok=True)
        assets_dir = gui_dist / "assets"
        assets_dir.mkdir(exist_ok=True)
        for name, content in [
            ("favicon.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
            ("manifest.webmanifest", b'{"name":"OBS Admin"}'),
            ("apple-touch-icon.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
            ("index.html", b"<html/>"),
        ]:
            target = gui_dist / name
            if not target.exists():
                target.write_bytes(content)
                created_files.append(target)

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    finally:
        for f in created_files:
            f.unlink(missing_ok=True)
        if created_dir:
            try:
                gui_dist.rmdir()
            except OSError:
                pass
        override_settings(saved_settings)


@pytest.mark.asyncio
async def test_visu_favicon_returns_svg(visu_dist_client):
    resp = await visu_dist_client.get("/visu/favicon.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_visu_manifest_returns_json(visu_dist_client):
    resp = await visu_dist_client.get("/visu/manifest.webmanifest")
    assert resp.status_code == 200
    assert "name" in resp.json()


@pytest.mark.asyncio
async def test_admin_favicon_returns_svg(gui_dist_client):
    resp = await gui_dist_client.get("/favicon.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_admin_manifest_returns_json(gui_dist_client):
    resp = await gui_dist_client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert "name" in resp.json()


@pytest.mark.asyncio
async def test_visu_apple_touch_icon_returns_png(visu_dist_client):
    resp = await visu_dist_client.get("/visu/apple-touch-icon.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")


@pytest.mark.asyncio
async def test_admin_apple_touch_icon_returns_png(gui_dist_client):
    resp = await gui_dist_client.get("/apple-touch-icon.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")
