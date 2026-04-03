"""
openTWS entry point — startup and graceful shutdown.

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
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from opentws.config import get_settings
    from opentws.db.database import init_db, get_db
    from opentws.core.event_bus import init_event_bus, DataValueEvent
    from opentws.core.mqtt_client import init_mqtt_client
    from opentws.core.registry import init_registry
    from opentws.core.write_router import init_write_router
    from opentws.api.auth import ensure_default_user
    from opentws.api.v1.websocket import init_ws_manager
    from opentws.adapters import registry as adapter_registry
    from opentws.ringbuffer.ringbuffer import init_ringbuffer
    from opentws.history.factory import init_history_plugin

    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("openTWS v0.1.0 starting …")

    # 1. Database
    db = await init_db(settings.database.path)
    await ensure_default_user(db)

    # Rebuild Mosquitto passwd file from DB on every startup (keeps it in sync).
    # SIGHUP is sent after MQTT connects (see below) so Mosquitto reloads cleanly.
    from opentws.core.mqtt_passwd import rebuild_passwd_file, reload_mosquitto as _reload_mqtt
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

    # 5. RingBuffer
    rb_path = settings.database.path.replace(".db", "_ringbuffer.db")
    rb = await init_ringbuffer(
        storage=settings.ringbuffer.storage,
        max_entries=settings.ringbuffer.max_entries,
        disk_path=rb_path,
    )
    bus.subscribe(DataValueEvent, rb.handle_value_event)

    # 6. History plugin
    await init_history_plugin(db)
    from opentws.history.factory import handle_value_event as history_handler
    bus.subscribe(DataValueEvent, history_handler)

    # 7. WebSocket Manager
    ws_manager = init_ws_manager()
    bus.subscribe(DataValueEvent, ws_manager.handle_value_event)

    # 6. Write Router
    #    Path A: MQTT dp/{uuid}/set → adapters (external commands)
    #    Path B: DataValueEvent → DEST/BOTH bindings (cross-protocol propagation)
    write_router = init_write_router(db, registry)
    mqtt.on_write_request(write_router.handle)
    bus.subscribe(DataValueEvent, write_router.handle_value_event)

    # 7. MQTT connect
    await mqtt.start()
    # Reload Mosquitto after connecting so user accounts take effect immediately.
    await _reload_mqtt(_m.reload_command, _m.reload_pid)

    # 8. Adapters — import triggers @register, then start_all loads DB configs + bindings
    import opentws.adapters.knx.adapter        # noqa: F401
    import opentws.adapters.modbus_tcp.adapter  # noqa: F401
    import opentws.adapters.modbus_rtu.adapter  # noqa: F401
    import opentws.adapters.onewire.adapter     # noqa: F401
    import opentws.adapters.mqtt.adapter        # noqa: F401
    await adapter_registry.start_all(bus, db, value_getter=registry.get_value)

    # 9. Logic Engine
    from opentws.logic.manager import init_logic_manager
    logic_mgr = init_logic_manager(db=db, event_bus=bus, registry=registry)
    await logic_mgr.start()

    logger.info(
        "openTWS ready — %d datapoints, %d adapters registered",
        registry.count(),
        len(adapter_registry.all_types()),
    )

    yield  # ← application running

    # Shutdown (reverse order)
    await logic_mgr.stop()
    await adapter_registry.stop_all()
    await mqtt.stop()
    await rb.stop()
    await get_db().disconnect()
    logger.info("openTWS stopped.")


def create_app() -> FastAPI:
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from opentws.api.router import router
    from opentws.api.auth import limiter as auth_limiter
    from opentws.config import get_settings

    settings = get_settings()

    app = FastAPI(
        title="openTWS",
        description="Open-Source Multiprotocol Server for Building Automation",
        version="0.1.0",
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    # Rate limiter state (used by @limiter.limit decorators in auth.py)
    app.state.limiter = auth_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — configure allowed origins via config.yaml or OPENTWS_CORS__ORIGINS env var
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

    # ── Serve Visu SPA (frontend_dist → /visu) ────────────────────────────
    _visu_dist = Path(__file__).parent.parent / "frontend_dist"
    if _visu_dist.is_dir():
        _visu_assets = _visu_dist / "assets"
        if _visu_assets.is_dir():
            app.mount("/visu/assets", StaticFiles(directory=_visu_assets), name="visu_assets")

        @app.get("/visu/{path:path}", include_in_schema=False)
        async def visu_spa(path: str):  # noqa: ARG001
            """Alle /visu/... Pfade → index.html (Vue Router history mode)."""
            index = _visu_dist / "index.html"
            if index.exists():
                return FileResponse(str(index))
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
                return FileResponse(str(index))
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app


async def main() -> None:
    from opentws.config import get_settings
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
