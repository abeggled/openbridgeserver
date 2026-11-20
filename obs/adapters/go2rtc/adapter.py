"""go2rtc-Adapter (#1479) — eigenständige go2rtc-Instanz als Kamera-Stream-Quelle.

Adapter-Konfiguration: siehe ``StreamSourceConfig`` (Default-Port 1984, optional Basic Auth
aus go2rtcs ``api.username``/``api.password``).

Jeder Stream aus ``/api/streams`` erscheint als eigene „Kamera“ mit genau diesem einen Stream;
Kachel- und Vollbild-Stream wählt man im Kamera-Widget frei aus der Liste.
"""

from __future__ import annotations

from typing import Any

from obs.adapters.go2rtc_base import CameraInfo, StreamInfo, StreamKind, StreamSourceAdapter, StreamSourceConfig
from obs.adapters.registry import register


class Go2rtcAdapterConfig(StreamSourceConfig):
    port: int = 1984


@register
class Go2rtcAdapter(StreamSourceAdapter):
    adapter_type = "GO2RTC"
    config_schema = Go2rtcAdapterConfig
    health_path = "/api"

    def ws_path(self, kind: StreamKind) -> str:
        # go2rtc spricht WebRTC und MSE über denselben WebSocket.
        return "/api/ws"

    async def _fetch_cameras(self) -> list[CameraInfo]:
        data = (await self.request("GET", "/api/streams")).json()
        if not isinstance(data, dict):
            return []
        return [CameraInfo(name=name, streams=(StreamInfo(label=name, src=name),)) for name in sorted(data) if isinstance(name, str) and name]

    def snapshot_request(self, camera: str, src: str, height: int) -> tuple[str, dict[str, Any]]:
        return "/api/frame.jpeg", {"src": src, "h": height}
