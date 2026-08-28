"""Tests for Visu SPA and Help site static file routes.

These routes are only registered when frontend_dist/ / help_dist/ exist on disk
at create_app() time, so the fixtures create a minimal directory temporarily.
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
            ("obs_logo_light.svg", b'<svg xmlns="http://www.w3.org/2000/svg" id="light"/>'),
            ("obs_logo_dark.svg", b'<svg xmlns="http://www.w3.org/2000/svg" id="dark"/>'),
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


@pytest_asyncio.fixture
async def help_dist_client(tmp_path):
    """AsyncClient wired to a fresh app instance that has help_dist in place."""
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

    help_dist = _PROJECT_ROOT / "help_dist"
    de_dir = help_dist / "de"
    en_dir = help_dist / "en"
    created_dir = not help_dist.exists()
    created_de_dir = not de_dir.exists()
    created_en_dir = not en_dir.exists()
    created_files: list[Path] = []

    try:
        help_dist.mkdir(exist_ok=True)
        # No root-level index.html: every locale, including German, lives
        # under its own prefixed directory (de/, en/) — there is no
        # unprefixed "root" locale (see help/.vitepress/config.mts).
        target = help_dist / "404.html"
        if not target.exists():
            target.write_bytes(b"<html><body>Nicht gefunden</body></html>")
            created_files.append(target)

        de_dir.mkdir(exist_ok=True)
        de_index = de_dir / "index.html"
        if not de_index.exists():
            de_index.write_bytes(b"<html><body>Hilfe Start</body></html>")
            created_files.append(de_index)

        en_dir.mkdir(exist_ok=True)
        en_index = en_dir / "index.html"
        if not en_index.exists():
            en_index.write_bytes(b"<html><body>Help Start</body></html>")
            created_files.append(en_index)

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    finally:
        for f in created_files:
            f.unlink(missing_ok=True)
        if created_en_dir:
            try:
                en_dir.rmdir()
            except OSError:
                pass
        if created_de_dir:
            try:
                de_dir.rmdir()
            except OSError:
                pass
        if created_dir:
            try:
                help_dist.rmdir()
            except OSError:
                pass
        override_settings(saved_settings)


@pytest_asyncio.fixture
async def no_help_dist_client(tmp_path):
    """AsyncClient wired to a fresh app instance where help_dist does NOT exist."""
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

    help_dist = _PROJECT_ROOT / "help_dist"
    assert not help_dist.exists(), "help_dist/ must not exist for this fixture to be meaningful"

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    override_settings(saved_settings)


@pytest_asyncio.fixture
async def partial_help_dist_client(tmp_path):
    """AsyncClient wired to a fresh app instance where help_dist/ exists but is
    only partially built — has an index.html but no 404.html yet, the state a
    VitePress build can be in mid-run (or an incomplete copy)."""
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

    help_dist = _PROJECT_ROOT / "help_dist"
    de_dir = help_dist / "de"
    created_dir = not help_dist.exists()
    created_de_dir = not de_dir.exists()
    created_files: list[Path] = []

    try:
        help_dist.mkdir(exist_ok=True)
        de_dir.mkdir(exist_ok=True)
        index = de_dir / "index.html"
        if not index.exists():
            index.write_bytes(b"<html><body>Hilfe Start</body></html>")
            created_files.append(index)

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    finally:
        for f in created_files:
            f.unlink(missing_ok=True)
        if created_de_dir:
            try:
                de_dir.rmdir()
            except OSError:
                pass
        if created_dir:
            try:
                help_dist.rmdir()
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


@pytest.mark.asyncio
async def test_obs_logo_light_returns_svg(gui_dist_client):
    resp = await gui_dist_client.get("/obs_logo_light.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_obs_logo_dark_returns_svg(gui_dist_client):
    resp = await gui_dist_client.get("/obs_logo_dark.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_help_bare_root_redirects_to_german(help_dist_client):
    """The help site has no unprefixed "root" locale — every locale, including
    German, lives under its own /help/<lang>/ prefix. The bare /help/ (with
    trailing slash) redirects to German explicitly instead of 404ing."""
    resp = await help_dist_client.get("/help/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/help/de/"


@pytest.mark.asyncio
async def test_help_de_locale_index_served(help_dist_client):
    resp = await help_dist_client.get("/help/de/")
    assert resp.status_code == 200
    assert "Hilfe Start" in resp.text


@pytest.mark.asyncio
async def test_help_en_locale_index_served(help_dist_client):
    resp = await help_dist_client.get("/help/en/")
    assert resp.status_code == 200
    assert "Help Start" in resp.text


@pytest.mark.asyncio
async def test_help_unknown_path_returns_help_404_page(help_dist_client):
    resp = await help_dist_client.get("/help/does-not-exist")
    assert resp.status_code == 404
    assert "Nicht gefunden" in resp.text


@pytest.mark.asyncio
async def test_help_unknown_path_does_not_fall_back_to_admin_gui(help_dist_client):
    """A missing /help/... path must not silently render the Admin-GUI shell."""
    resp = await help_dist_client.get("/help/de/does-not-exist")
    assert "Hilfe Start" not in resp.text


@pytest.mark.asyncio
async def test_help_path_returns_json_404_when_dist_missing(no_help_dist_client):
    """Without a built help_dist/, /help/... must not fall back to the Admin-GUI shell either."""
    resp = await no_help_dist_client.get("/help/anything")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


@pytest.mark.asyncio
async def test_bare_help_path_returns_json_404_when_dist_missing(no_help_dist_client):
    """The bare "/help" (no trailing slash/path) is a distinct request path from
    "/help/..." — without this exact-match guard it fell through to the Admin-GUI
    SPA fallback instead of the same JSON 404 every other /help/... path gets."""
    resp = await no_help_dist_client.get("/help")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


@pytest.mark.asyncio
async def test_help_root_path_returns_json_404_when_dist_missing(no_help_dist_client):
    """The bare "/help/" (with trailing slash, no locale) must not redirect to
    a locale that doesn't exist — same JSON 404 contract as every other
    /help/... path when help_dist/ itself is missing."""
    resp = await no_help_dist_client.get("/help/", follow_redirects=False)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


@pytest.mark.asyncio
async def test_help_starts_working_without_a_restart_once_help_dist_appears(tmp_path):
    """help_dist/ is a separate npm build with no guaranteed ordering against
    backend startup (issue #1179 — a PyCharm "before launch" step for this
    turned out not to be reliably awaited). /help must recover on its own, on
    the very next request, the moment the directory appears — not require a
    fresh `create_app()` / process restart."""
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

    help_dist = _PROJECT_ROOT / "help_dist"
    assert not help_dist.exists(), "help_dist/ must not exist for this test to be meaningful"

    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            before = await client.get("/help/index.html")
            assert before.status_code == 404
            assert before.json() == {"detail": "Not found"}

            help_dist.mkdir()
            (help_dist / "index.html").write_bytes(b"<html><body>Hilfe Start</body></html>")
            (help_dist / "404.html").write_bytes(b"<html><body>Nicht gefunden</body></html>")

            after = await client.get("/help/index.html")
            assert after.status_code == 200
            assert "Hilfe Start" in after.text
    finally:
        for name in ("index.html", "404.html"):
            (help_dist / name).unlink(missing_ok=True)
        if help_dist.exists():
            help_dist.rmdir()
        override_settings(saved_settings)


@pytest.mark.asyncio
async def test_bare_help_path_redirects_when_dist_exists(help_dist_client):
    """Mirrors the missing-dist bare-path test above, for the existing-dist
    case: the bare "/help" still redirects to "/help/" (unchanged from before
    the lazy-mount rewrite), it just no longer masks a missing help_dist/."""
    resp = await help_dist_client.get("/help", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/help/"


@pytest.mark.asyncio
async def test_bare_help_path_head_request_redirects_like_get(help_dist_client):
    """A HEAD request must be answered the same way as GET (a 307 to
    "/help/"), not the bare 405 an @app.get()-only route would give — an
    availability probe using HEAD (Codex review on PR #1180)."""
    resp = await help_dist_client.head("/help", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/help/"


@pytest.mark.asyncio
async def test_help_root_path_head_request_redirects_to_german(help_dist_client):
    """Same HEAD-must-match-GET reasoning as the bare "/help" path, for the
    "/help/" -> "/help/de/" locale redirect."""
    resp = await help_dist_client.head("/help/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/help/de/"


@pytest.mark.asyncio
async def test_help_static_mount_is_reused_across_requests(help_dist_client):
    """_LazyHelpStatic caches its inner StaticFiles instance after the first
    request through it (self._static). A second request through the same
    app/client must be served from that cached instance, not just the
    construct-it-for-the-first-time path (Codex review on PR #1180)."""
    first = await help_dist_client.get("/help/de/")
    assert first.status_code == 200
    assert "Hilfe Start" in first.text

    second = await help_dist_client.get("/help/de/")
    assert second.status_code == 200
    assert "Hilfe Start" in second.text


@pytest.mark.asyncio
async def test_help_unresolvable_path_returns_json_404_even_with_a_partial_dist(partial_help_dist_client):
    """A help_dist/ that exists but has no local 404.html yet (e.g. mid-build)
    must still get the same JSON 404 contract as a missing dist — not silently
    fall through to the Admin-GUI SPA shell as a misleading 200, which would
    make the help store's loadIndex() cache the wrong thing as "loaded"
    (Codex review on PR #1180 — a guard for this in the global 404 handler
    was previously removed as unreachable "dead code"; it wasn't)."""
    resp = await partial_help_dist_client.get("/help/help-index.json")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}
