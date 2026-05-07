"""FastAPI Router Aggregator — Phase 4/5

Mounts all v1 sub-routers under /api/v1.
"""

from __future__ import annotations

from fastapi import APIRouter

from obs.api.auth import router as auth_router
from obs.api.v1.adapters import router as adapters_router
from obs.api.v1.bindings import router as bindings_router
from obs.api.v1.camera import router as camera_router
from obs.api.v1.autobackup import router as autobackup_router
from obs.api.v1.config import router as config_router
from obs.api.v1.datapoints import router as dp_router
from obs.api.v1.history import router as history_router
from obs.api.v1.icons import router as icons_router
from obs.api.v1.knxproj import router as knxproj_router
from obs.api.v1.logic import router as logic_router
from obs.api.v1.ringbuffer import router as rb_router
from obs.api.v1.search import router as search_router
from obs.api.v1.system import router as system_router
from obs.api.v1.visu import router as visu_router
from obs.api.v1.hierarchy import router as hierarchy_router
from obs.api.v1.weather import router as weather_router
from obs.api.v1.websocket import router as ws_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(dp_router, prefix="/datapoints")
router.include_router(bindings_router, prefix="/datapoints")
router.include_router(search_router, prefix="/search")
router.include_router(adapters_router, prefix="/adapters")
router.include_router(system_router, prefix="/system")
router.include_router(ws_router)
router.include_router(rb_router, prefix="/ringbuffer")
router.include_router(history_router, prefix="/history")
router.include_router(config_router, prefix="/config")
router.include_router(autobackup_router, prefix="/config")
router.include_router(knxproj_router, prefix="/knxproj")
router.include_router(logic_router, prefix="/logic")
router.include_router(visu_router, prefix="/visu")
router.include_router(icons_router, prefix="/icons")
router.include_router(camera_router, prefix="/camera")
router.include_router(weather_router, prefix="/weather")
router.include_router(hierarchy_router, prefix="/hierarchy")
