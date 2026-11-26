"""Gemeinsamer Kern für Kamera-Stream-Quellen auf go2rtc-Basis (#1479).

go2rtc liefert Kamerastreams per WebRTC und MSE über *einen* WebSocket-Endpunkt; Frigate
reicht genau diesen Endpunkt unter ``/live/{webrtc,mse}/api/ws`` durch. Die Adapter
``GO2RTC`` und ``FRIGATE`` unterscheiden sich deshalb nur darin, wo Streamliste, WebSocket
und Snapshot liegen und wie sie sich anmelden — das legen die Unterklassen fest.

Die Adapter haben (noch) keine Bindings: sie liefern dem Kamera-Widget die Streamliste und
dem Stream-Endpunkt (``obs/api/v1/camera_streams.py``) Ziel-URL und Anmeldedaten.
Videodaten laufen nie durch den Adapter — WebRTC-Medien gehen direkt von go2rtc zum Browser,
MSE nur dann über OBS, wenn die Instanz ``mse_mode = "proxy"`` gesetzt hat.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import ssl as ssl_lib
import time
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase

logger = logging.getLogger(__name__)

StreamKind = Literal["webrtc", "mse"]

HEALTH_INTERVAL = 60.0
CAMERA_CACHE_SECONDS = 30.0
HTTP_TIMEOUT = 10.0
_sleep = asyncio.sleep  # module seam: tests run the health loop without waiting


class StreamSourceConfig(BaseModel):
    host: str = ""
    port: int = 1984
    ssl: bool = False
    verify_tls: bool = True
    username: str = ""
    password: str = Field(default="", json_schema_extra={"format": "password"})
    # direct: der Browser holt MSE selbst bei go2rtc (keine Last auf OBS).
    # proxy:  OBS reicht den MSE-Stream durch (Fernzugriff, HTTPS-Visu, Login).
    mse_mode: Literal["direct", "proxy"] = "direct"


class StreamSourceBindingConfig(BaseModel):
    """Stream-Quellen haben keine Bindings."""


@dataclass(frozen=True)
class StreamInfo:
    label: str
    src: str


@dataclass(frozen=True)
class CameraInfo:
    name: str
    streams: tuple[StreamInfo, ...]


class StreamSourceError(Exception):
    """Die Stream-Quelle ist nicht erreichbar oder lehnt die Anfrage ab."""


class StreamSourceAdapter(AdapterBase):
    """Basis für go2rtc-basierte Kamera-Quellen."""

    config_schema: type[BaseModel] = StreamSourceConfig
    binding_config_schema: type[BaseModel] = StreamSourceBindingConfig
    writes_values = False
    health_path: ClassVar[str]

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs: Any) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg = self.config_schema(**(config or {}))
        self._health_task: asyncio.Task | None = None
        self._cameras: list[CameraInfo] = []
        self._cameras_at = 0.0
        self._cameras_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Endpunkte
    # ------------------------------------------------------------------

    @property
    def cfg(self) -> Any:
        return self._cfg

    def _origin(self, scheme_secure: str, scheme_plain: str) -> str:
        host = self._cfg.host.strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # IPv6-Literal
        return f"{scheme_secure if self._cfg.ssl else scheme_plain}://{host}:{self._cfg.port}"

    @property
    def http_base(self) -> str:
        return self._origin("https", "http")

    @property
    def ws_base(self) -> str:
        return self._origin("wss", "ws")

    @abstractmethod
    def ws_path(self, kind: StreamKind) -> str:
        """Pfad des go2rtc-WebSockets für *kind* (ohne Query)."""

    def ws_url(self, kind: StreamKind, src: str) -> str:
        return f"{self.ws_base}{self.ws_path(kind)}?{urlencode({'src': src})}"

    def mse_direct_url(self, src: str) -> str | None:
        """URL, unter der der Browser MSE selbst holen darf — nur im Modus ``direct``.

        Mit Anmeldedaten gibt es keinen direkten Weg: der Browser kennt sie nicht und soll
        sie auch nicht bekommen.
        """
        if self._cfg.mse_mode != "direct" or self._cfg.username:
            return None
        return self.ws_url("mse", src)

    def ssl_context(self) -> ssl_lib.SSLContext | None:
        if not self._cfg.ssl:
            return None
        ctx = ssl_lib.create_default_context()
        if not self._cfg.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl_lib.CERT_NONE
        return ctx

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.http_base,
            timeout=HTTP_TIMEOUT,
            verify=self._cfg.verify_tls,
            follow_redirects=False,
        )

    async def auth_headers(self) -> dict[str, str]:
        """Header, mit denen HTTP- und WebSocket-Anfragen sich anmelden."""
        if not self._cfg.username:
            return {}
        token = base64.b64encode(f"{self._cfg.username}:{self._cfg.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    async def _on_unauthorized(self) -> bool:
        """Hook für eine 401-Antwort; True, wenn ein erneuter Versuch sinnvoll ist."""
        return False

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Anfrage mit Anmeldung; bei 401 genau ein erneuter Versuch, wenn die Unterklasse es will."""
        async with self._http_client() as client:
            resp = await client.request(method, path, headers=await self.auth_headers(), **kwargs)
            if resp.status_code == 401 and await self._on_unauthorized():
                resp = await client.request(method, path, headers=await self.auth_headers(), **kwargs)
        if resp.status_code == 401:
            raise StreamSourceError("unauthorized")
        if resp.status_code >= 400:
            raise StreamSourceError(f"HTTP {resp.status_code}")
        return resp

    # ------------------------------------------------------------------
    # Kameras / Streams
    # ------------------------------------------------------------------

    @abstractmethod
    async def _fetch_cameras(self) -> list[CameraInfo]:
        """Kamera- und Streamliste frisch von der Quelle holen."""

    async def list_cameras(self, *, force: bool = False) -> list[CameraInfo]:
        async with self._cameras_lock:
            if force or not self._cameras_at or time.monotonic() - self._cameras_at > CAMERA_CACHE_SECONDS:
                self._cameras = await self._fetch_cameras()
                self._cameras_at = time.monotonic()
            return list(self._cameras)

    async def owns_src(self, src: str) -> bool:
        return any(s.src == src for cam in await self.list_cameras() for s in cam.streams)

    @abstractmethod
    def snapshot_request(self, camera: str, src: str, height: int) -> tuple[str, dict[str, Any]]:
        """Pfad und Query-Parameter des Snapshots für *src* (Kamera *camera*)."""

    async def camera_of(self, src: str) -> str | None:
        for cam in await self.list_cameras():
            if any(s.src == src for s in cam.streams):
                return cam.name
        return None

    async def fetch_snapshot(self, src: str, height: int) -> tuple[bytes, str]:
        camera = await self.camera_of(src)
        if camera is None:
            raise StreamSourceError("unknown stream")
        path, params = self.snapshot_request(camera, src, height)
        resp = await self.request("GET", path, params=params)
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise StreamSourceError("snapshot is not an image")
        return resp.content, content_type

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if not self._cfg.host.strip():
            await self._publish_status(False, "Kein Host konfiguriert", "error", code="streamSourceNoHost")
            return
        self._health_task = asyncio.create_task(self._health_loop(), name=f"{self.adapter_type}-health-{self._instance_id}")

    async def disconnect(self) -> None:
        task, self._health_task = self._health_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._publish_status(False, "Getrennt", code="streamSourceDisconnected")

    async def _health_loop(self) -> None:
        while True:
            await self.check_health()
            await _sleep(HEALTH_INTERVAL)

    async def check_health(self) -> None:
        """Einmal prüfen und den Status melden — nur bei einer Änderung."""
        try:
            await self.request("GET", self.health_path)
        except (httpx.HTTPError, StreamSourceError) as exc:
            if self._connected or self._last_detail_code != "streamSourceUnreachable":
                logger.warning("%s %s nicht erreichbar: %s", self.adapter_type, self._instance_name, exc)
                await self._publish_status(
                    False,
                    f"Nicht erreichbar: {exc}",
                    "error",
                    code="streamSourceUnreachable",
                    params={"error": str(exc)},
                )
            return
        if not self._connected:
            await self._publish_status(True, "Verbunden", code="streamSourceConnected")

    # ------------------------------------------------------------------
    # Bindings (keine)
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        return None

    async def write(self, binding: Any, value: Any) -> None:
        return None
