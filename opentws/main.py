"""
OpenTWS entry point — startup and graceful shutdown.

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
    from opentws.history.sqlite_plugin import init_history_plugin

    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("OpenTWS v0.1.0 starting …")

    # 1. Database
    db = await init_db(settings.database.path)
    await ensure_default_user(db)

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
    init_history_plugin(db)

    # 7. WebSocket Manager
    ws_manager = init_ws_manager()
    bus.subscribe(DataValueEvent, ws_manager.handle_value_event)

    # 6. Write Router (MQTT dp/{uuid}/set → adapters)
    write_router = init_write_router(db, registry)
    mqtt.on_write_request(write_router.handle)

    # 7. MQTT connect
    await mqtt.start()

    # 8. Adapters — import triggers @register, then start_all loads DB configs + bindings
    import opentws.adapters.knx.adapter        # noqa: F401
    import opentws.adapters.modbus_tcp.adapter  # noqa: F401
    import opentws.adapters.modbus_rtu.adapter  # noqa: F401
    import opentws.adapters.onewire.adapter     # noqa: F401
    await adapter_registry.start_all(bus, db)

    logger.info(
        "OpenTWS ready — %d datapoints, %d adapters registered",
        registry.count(),
        len(adapter_registry.all_types()),
    )

    yield  # ← application running

    # Shutdown (reverse order)
    await adapter_registry.stop_all()
    await mqtt.stop()
    await rb.stop()
    await get_db().disconnect()
    logger.info("OpenTWS stopped.")


def create_app() -> FastAPI:
    from opentws.api.router import router

    app = FastAPI(
        title="OpenTWS",
        description="Open-Source Multiprotocol Server for Building Automation",
        version="0.1.0",
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )
    app.include_router(router, prefix="/api/v1")
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
