"""open bridge server entry point — startup and graceful shutdown.

Startup-Sequenz:
  1. Database (SQLite + migrations)
  2. EventBus
  3. MQTT Client
  4. DataPoint Registry (load from DB)
  5. WebSocket Manager (register with EventBus)
  6. Write Router (MQTT dp/+/set → adapter.write)
  7. Adapters (load configs + bindings, connect all)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from obs import __version__

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    from obs.adapters import registry as adapter_registry
    from obs.api.auth import ensure_default_user
    from obs.api.v1.websocket import init_ws_manager
    from obs.config import get_settings
    from obs.core.event_bus import DataValueEvent, init_event_bus
    from obs.core.mqtt_client import init_mqtt_client
    from obs.core.registry import init_registry
    from obs.core.write_router import init_write_router
    from obs.db.database import get_db, init_db
    from obs.history.factory import init_history_plugin
    from obs.ringbuffer.persisted_config import load_persisted_ringbuffer_config
    from obs.ringbuffer.ringbuffer import init_ringbuffer

    settings = get_settings()

    configured_log_level = settings.server.log_level.upper()
    log_level = getattr(logging, configured_log_level, logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    import asyncio

    from obs.log_buffer import LogBufferHandler

    LogBufferHandler.install(asyncio.get_event_loop(), level=log_level)
    logger.info(f"open bridge server v{__version__} starting …")

    # 1. Database
    db = await init_db(settings.database.path)
    persistent_log_level = await _read_persistent_log_level(db)
    if persistent_log_level and persistent_log_level != configured_log_level:
        log_level = getattr(logging, persistent_log_level, log_level)
        logging.getLogger().setLevel(log_level)
        try:
            from obs.log_buffer import set_log_buffer_level

            set_log_buffer_level(persistent_log_level)
        except Exception:
            logger.exception("Failed to apply persistent log level")
        logger.info("Applied persistent log level from app_settings: %s", persistent_log_level)
    await ensure_default_user(db)

    # Rebuild Mosquitto passwd file from DB on every startup (keeps it in sync).
    # SIGHUP is sent after MQTT connects (see below) so Mosquitto reloads cleanly.
    from obs.core.mqtt_passwd import (
        rebuild_passwd_file,
    )
    from obs.core.mqtt_passwd import (
        reload_mosquitto as _reload_mqtt,
    )

    _m = settings.mosquitto
    await rebuild_passwd_file(db, _m.passwd_file, _m.service_username, _m.service_password)

    # 2. EventBus
    bus = init_event_bus()

    # 3. MQTT Client
    mqtt = init_mqtt_client(
        host=settings.mqtt.host,
        port=settings.mqtt.port,
        username=settings.mqtt.username,
        password=settings.mqtt.password,
    )

    # 4. DataPoint Registry
    registry = await init_registry(db, mqtt, bus)
    bus.subscribe(DataValueEvent, registry.handle_value_event)

    # 5. RingBuffer — config is persisted in app_settings (see persisted_config.py).
    # Defaults apply only when nothing has been configured via the API yet.
    rb_path = settings.database.path.replace(".db", "_ringbuffer.db")
    rb_cfg = await load_persisted_ringbuffer_config(db)
    rb = await init_ringbuffer(
        storage="file",
        max_entries=rb_cfg["max_entries"],
        disk_path=rb_path,
        max_file_size_bytes=rb_cfg["max_file_size_bytes"],
        max_age=rb_cfg["max_age"],
    )
    bus.subscribe(DataValueEvent, rb.handle_value_event)

    # 6. History plugin
    await init_history_plugin(db)
    from obs.history.factory import handle_value_event as history_handler

    bus.subscribe(DataValueEvent, history_handler)

    # 7. WebSocket Manager
    ws_manager = init_ws_manager()
    bus.subscribe(DataValueEvent, ws_manager.handle_value_event)

    # 6. Write Router
    #    Path A: MQTT dp/{uuid}/set → adapters (external commands)
    #    Path B: DataValueEvent → DEST/BOTH bindings (cross-protocol propagation)
    write_router = init_write_router(db, registry, event_bus=bus)
    mqtt.on_write_request(write_router.handle)
    bus.subscribe(DataValueEvent, write_router.handle_value_event)

    # 7. MQTT connect
    await mqtt.start()
    # Reload Mosquitto after connecting so user accounts take effect immediately.
    await _reload_mqtt(_m.reload_command, _m.reload_pid)

    # 8. Adapters — import triggers @register, then start_all loads DB configs + bindings
    import obs.adapters.homeassistant.adapter
    import obs.adapters.iobroker.adapter
    import obs.adapters.knx.adapter
    import obs.adapters.modbus_rtu.adapter
    import obs.adapters.modbus_tcp.adapter
    import obs.adapters.mqtt.adapter
    import obs.adapters.onewire.adapter
    import obs.adapters.snmp.adapter  # noqa: F401
    import obs.adapters.anwesenheit.adapter  # noqa: F401
    import obs.adapters.zeitschaltuhr.adapter  # noqa: F401

    await adapter_registry.start_all(bus, db, value_getter=registry.get_value)

    # 9. Logic Engine
    from obs.logic.manager import init_logic_manager

    logic_mgr = init_logic_manager(db=db, event_bus=bus, registry=registry)
    await logic_mgr.start()

    # 10. Autobackup-Scheduler
    from obs.api.v1.autobackup import init_autobackup_scheduler

    autobackup_scheduler = init_autobackup_scheduler(db=db)

    logger.info(
        "open bridge server ready — %d datapoints, %d adapters registered",
        registry.count(),
        len(adapter_registry.all_types()),
    )

    yield  # ← application running

    # Shutdown (reverse order)
    await autobackup_scheduler.stop()
    await logic_mgr.stop()
    await adapter_registry.stop_all()
    await mqtt.stop()
    await rb.stop()
    await get_db().disconnect()
    logger.info("open bridge server stopped.")


def create_app() -> FastAPI:
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from obs.api.auth import limiter as auth_limiter
    from obs.api.router import router
    from obs.config import get_settings

    settings = get_settings()

    app = FastAPI(
        title="open bridge server",
        description="Open-Source Multiprotocol Server for Building Automation",
        version=__version__,
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    # Rate limiter state (used by @limiter.limit decorators in auth.py)
    app.state.limiter = auth_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — configure allowed origins via config.yaml or OBS_CORS__ORIGINS env var
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )

    app.include_router(router, prefix="/api/v1")

    from fastapi import Request
    from fastapi.responses import JSONResponse

    def _spa_index_response(index: Path) -> FileResponse:
        return FileResponse(
            str(index),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # ── Serve Vue Admin-GUI (gui_dist → /) ────────────────────────────────
    # NOTE: We deliberately avoid a catch-all @app.get("/{path:path}") route
    # because it causes 405 Method Not Allowed for POST/PATCH requests to API
    # endpoints — FastAPI finds a path match (the catch-all) but no method match.
    # Instead we use a 404 exception handler that only intercepts truly unknown
    # paths and serves index.html for non-API routes (Vue Router history mode).
    _gui_dist = Path(__file__).parent.parent / "gui_dist"
    if _gui_dist.is_dir():
        _assets = _gui_dist / "assets"
        if _assets.is_dir():
            app.mount("/assets", StaticFiles(directory=_assets), name="assets")

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon():
            return FileResponse(_gui_dist / "favicon.svg")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def admin_manifest():
            return FileResponse(_gui_dist / "manifest.webmanifest")

        @app.get("/apple-touch-icon.png", include_in_schema=False)
        async def admin_apple_touch_icon():
            return FileResponse(_gui_dist / "apple-touch-icon.png")

    # ── Serve Visu SPA (frontend_dist → /visu) ────────────────────────────
    _visu_dist = Path(__file__).parent.parent / "frontend_dist"
    if _visu_dist.is_dir():
        _visu_assets = _visu_dist / "assets"
        if _visu_assets.is_dir():
            app.mount("/visu/assets", StaticFiles(directory=_visu_assets), name="visu_assets")

        @app.get("/visu/favicon.svg", include_in_schema=False)
        async def visu_favicon():
            return FileResponse(_visu_dist / "favicon.svg")

        @app.get("/visu/manifest.webmanifest", include_in_schema=False)
        async def visu_manifest():
            return FileResponse(_visu_dist / "manifest.webmanifest")

        @app.get("/visu/apple-touch-icon.png", include_in_schema=False)
        async def visu_apple_touch_icon():
            return FileResponse(_visu_dist / "apple-touch-icon.png")

        @app.get("/visu/{path:path}", include_in_schema=False)
        async def visu_spa(path: str):
            """Alle /visu/... Pfade → index.html (Vue Router history mode)."""
            index = _visu_dist / "index.html"
            if index.exists():
                return _spa_index_response(index)
            return JSONResponse({"detail": "Visu nicht gebaut"}, status_code=404)

    # ── 404-Handler für alles andere ──────────────────────────────────────
    @app.exception_handler(404)
    async def spa_404_handler(request: Request, exc):
        """Return index.html for unknown non-API, non-visu paths (Admin-GUI routing).
        Return JSON 404 for unknown /api/... paths.
        """
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if request.url.path.startswith("/visu/"):
            # Bereits durch visu_spa abgedeckt — sollte nicht hier landen
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if _gui_dist.is_dir():
            index = _gui_dist / "index.html"
            if index.exists():
                return _spa_index_response(index)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app


async def _read_persistent_log_level(db: object) -> str | None:
    try:
        row = await db.fetchone("SELECT value FROM app_settings WHERE key='server.log_level'")
    except Exception:
        return None
    if row is None:
        return None
    level = str(row["value"] or "").upper()
    if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return level
    return None


async def main() -> None:
    from obs.config import get_settings

    settings = get_settings()
    app = create_app()

    config = uvicorn.Config(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level.lower(),
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()
