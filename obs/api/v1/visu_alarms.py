"""Visu alarms API — /api/v1/system/visu-alarms...

GET       /system/visu-alarms                         alarm definitions (admin)
PUT       /system/visu-alarms                         replace all alarm definitions (admin)
GET       /system/visu-alarms/active                  active alarms (public, used by the Visu)
POST      /system/visu-alarms/{alarm_id}/acknowledge  end an active alarm (public, used by the Visu)
WEBSOCKET /system/visu-alarms/ws                      live list of active alarms (public)

The public routes carry only what the administrator wrote for display (title,
text, icon, page) — never datapoint values — because the Visu runs on
anonymous and PIN-only devices, where an alarm has to appear and be
acknowledged as well.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi import status as http_status

from obs.api.audit import AuditLogWriter, build_audit_context
from obs.api.auth import get_admin_user, limiter
from obs.db.database import Database, get_db
from obs.visu_alarms import (
    VISU_ALARMS_KEY,
    ActiveVisuAlarm,
    VisuAlarm,
    VisuAlarmsIn,
    dump_visu_alarms,
    get_visu_alarm_engine,
    load_visu_alarms,
)
from obs.visu_devices import VISU_DEVICES_KEY, load_visu_devices

router = APIRouter(tags=["visu-alarms"])

_KEEPALIVE_SECONDS = 60.0


@router.get("/visu-alarms", response_model=list[VisuAlarm])
async def get_visu_alarms(
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> list[VisuAlarm]:
    """List the alarm definitions. Admin only."""
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_ALARMS_KEY,))
    return load_visu_alarms(row["value"] if row else None)


@router.put("/visu-alarms", response_model=list[VisuAlarm])
async def update_visu_alarms(
    body: VisuAlarmsIn,
    request: Request = None,  # type: ignore[assignment]
    db: Database = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> list[VisuAlarm]:
    """Replace all alarm definitions and apply them right away. Admin only."""
    await _require_existing("datapoints", {a.datapoint_id for a in body.alarms}, db, "Unknown datapoint")
    await _require_existing("visu_nodes", {a.page_id for a in body.alarms if a.page_id}, db, "Unknown page")
    await _require_known_device_profiles({slug for a in body.alarms for slug in a.devices}, db)
    async with db.transaction():
        await db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (VISU_ALARMS_KEY, dump_visu_alarms(body.alarms)),
        )
        writer = AuditLogWriter(db, build_audit_context(request, _admin))
        await writer.write_contract("PUT", "/api/v1/system/visu-alarms", resource_id="global", commit=False)
    get_visu_alarm_engine().configure(body.alarms)
    return body.alarms


async def _require_existing(table: str, ids: set[str], db: Database, message: str) -> None:
    if not ids:
        return
    placeholders = ", ".join("?" for _ in ids)
    rows = await db.fetchall(f"SELECT id FROM {table} WHERE id IN ({placeholders})", tuple(ids))
    unknown = sorted(ids - {str(r["id"]) for r in rows})
    if unknown:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_CONTENT, f"{message}: {', '.join(unknown)}")


async def _require_known_device_profiles(slugs: set[str], db: Database) -> None:
    """An alarm restricted to a device profile that does not exist would never show anywhere."""
    if not slugs:
        return
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_DEVICES_KEY,))
    known = {device.slug for device in load_visu_devices(row["value"] if row else None)}
    unknown = sorted(slugs - known)
    if unknown:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown device profile: {', '.join(unknown)}")


def _is_cross_origin(request: Request) -> bool:
    """True when a browser sends the request from a page of another site.

    A body-less POST is a CORS "simple request": a page on any other host in the
    LAN could send it without a preflight — but always with an Origin header. A
    same-origin Visu sends the server's own origin, non-browser clients send none.
    """
    origin = request.headers.get("origin")
    if not origin:
        return False
    own_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlsplit(origin).netloc.lower() != own_host.split(",")[0].strip().lower()


@router.get("/visu-alarms/active", response_model=list[ActiveVisuAlarm])
async def get_active_visu_alarms() -> list[dict]:
    """Active alarms, newest first. Public — see the module docstring."""
    return get_visu_alarm_engine().active()


@router.post("/visu-alarms/{alarm_id}/acknowledge", status_code=http_status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def acknowledge_visu_alarm(alarm_id: str, request: Request, db: Database = Depends(get_db)) -> None:
    """End an active alarm that offers an acknowledge button — on every Visu.

    Public on purpose: the Visu runs on anonymous and PIN-only wall panels that
    must be able to acknowledge. In place of a login there are two guards — a
    request from a page of another site is rejected, and every acknowledgement
    is written to the audit log with the caller's address.
    """
    if _is_cross_origin(request):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Cross-origin acknowledge rejected")
    if not get_visu_alarm_engine().acknowledge(alarm_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "No active alarm to acknowledge")
    writer = AuditLogWriter(db, build_audit_context(request, None))
    await writer.write("system.visu_alarm.acknowledged", resource_type="visu_alarm", resource_id=alarm_id)


@router.websocket("/visu-alarms/ws")
async def visu_alarms_ws(ws: WebSocket) -> None:
    """Push the active alarm list on connect and after every change. Public."""
    await ws.accept()
    engine = get_visu_alarm_engine()
    queue = engine.listen()
    receiver = asyncio.create_task(_until_closed(ws))
    try:
        await ws.send_json({"action": "visu_alarms", "alarms": engine.active()})
        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({getter, receiver}, timeout=_KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                getter.cancel()
                break
            if getter in done:
                await ws.send_json({"action": "visu_alarms", "alarms": getter.result()})
            else:
                getter.cancel()
                await ws.send_json({"action": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        engine.unlisten(queue)
        receiver.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await receiver


async def _until_closed(ws: WebSocket) -> None:
    """Consume (and ignore) client messages until the connection closes."""
    while True:
        await ws.receive_text()
