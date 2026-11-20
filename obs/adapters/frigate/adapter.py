"""Frigate-Adapter (#1479) — Frigate-NVR als Kamera-Stream-Quelle.

Frigate bringt go2rtc mit und reicht dessen WebSocket unter ``/live/webrtc/api/ws`` und
``/live/mse/api/ws`` durch. Zusätzlich zu go2rtc liefert Frigate die Zuordnung Kamera →
Streams (``cameras.<name>.live.streams`` in ``/api/config``, typischerweise Haupt- und
Substream) und Snapshots je Kamera.

Adapter-Konfiguration: siehe ``StreamSourceConfig``, Default-Port 5000 (interne API ohne
Anmeldung). Für Port 8971 (mit Anmeldung, meist HTTPS) ``username``/``password`` setzen:
der Adapter meldet sich über ``POST /api/login`` an und schickt das Cookie ``frigate_token``
mit; bei 401 meldet er sich einmal neu an.

Später (Phase 2 von #1479) bekommt nur dieser Typ Bindings — Events, Belegung, PTZ.
"""

from __future__ import annotations

import asyncio
from typing import Any

from obs.adapters.go2rtc_base import CameraInfo, StreamInfo, StreamKind, StreamSourceAdapter, StreamSourceConfig, StreamSourceError
from obs.adapters.registry import register

TOKEN_COOKIE = "frigate_token"


class FrigateAdapterConfig(StreamSourceConfig):
    port: int = 5000


@register
class FrigateAdapter(StreamSourceAdapter):
    adapter_type = "FRIGATE"
    config_schema = FrigateAdapterConfig
    health_path = "/api/version"

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs: Any) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    def ws_path(self, kind: StreamKind) -> str:
        return f"/live/{kind}/api/ws"

    # ------------------------------------------------------------------
    # Anmeldung
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        async with self._http_client() as client:
            resp = await client.post("/api/login", json={"user": self._cfg.username, "password": self._cfg.password})
        token = resp.cookies.get(TOKEN_COOKIE)
        if resp.status_code >= 400 or not token:
            raise StreamSourceError("login failed")
        self._token = token

    async def auth_headers(self) -> dict[str, str]:
        if not self._cfg.username:
            return {}
        async with self._login_lock:
            if self._token is None:
                await self._login()
        return {"Cookie": f"{TOKEN_COOKIE}={self._token}"}

    async def _on_unauthorized(self) -> bool:
        if not self._cfg.username:
            return False
        async with self._login_lock:
            self._token = None
        return True

    # ------------------------------------------------------------------
    # Kameras
    # ------------------------------------------------------------------

    async def _fetch_cameras(self) -> list[CameraInfo]:
        config = (await self.request("GET", "/api/config")).json()
        cameras = config.get("cameras") if isinstance(config, dict) else None
        if not isinstance(cameras, dict):
            return []
        go2rtc = config.get("go2rtc")
        restreams = go2rtc.get("streams") if isinstance(go2rtc, dict) else None
        known = set(restreams) if isinstance(restreams, dict) else set()

        result: list[CameraInfo] = []
        for name, cam in cameras.items():
            if not isinstance(cam, dict) or cam.get("enabled") is False:
                continue
            live = cam.get("live")
            mapping = live.get("streams") if isinstance(live, dict) else None
            streams: list[StreamInfo] = []
            if isinstance(mapping, dict):
                streams = [StreamInfo(label=str(label), src=src) for label, src in mapping.items() if isinstance(src, str) and src]
            if not streams and name in known:
                # Frigate < 0.15 / ohne live.streams: Restream unter dem Kameranamen.
                streams = [StreamInfo(label=name, src=name)]
            if streams:
                result.append(CameraInfo(name=name, streams=tuple(streams)))
        return result

    def snapshot_request(self, camera: str, src: str, height: int) -> tuple[str, dict[str, Any]]:
        return f"/api/{camera}/latest.jpg", {"h": height}
