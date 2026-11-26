"""GO2RTC- und FRIGATE-Adapter (#1479): Endpunkte, Streamliste, Anmeldung, Status."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

import obs.adapters.go2rtc_base as base_module
from obs.adapters.frigate.adapter import FrigateAdapter
from obs.adapters.go2rtc.adapter import Go2rtcAdapter
from obs.adapters.go2rtc_base import CameraInfo, StreamInfo, StreamSourceError

FRIGATE_CONFIG = {
    "cameras": {
        "hof": {"enabled": True, "live": {"streams": {"Main": "cam_hof", "Sub": "cam_hof_sub"}}},
        "alt": {"live": {"streams": {}}},  # ohne live.streams, aber Restream unter dem Kameranamen
        "aus": {"enabled": False, "live": {"streams": {"Main": "cam_aus"}}},
        "leer": {"live": {"streams": {"Main": ""}}},  # kein brauchbarer Stream → fällt weg
        "kaputt": "nicht-dict",
    },
    "go2rtc": {"streams": {"cam_hof": [], "cam_hof_sub": [], "alt": []}},
}


class Bus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _use_transport(adapter: Any, handler: Any) -> list[httpx.Request]:
    """Ersetzt den HTTP-Client des Adapters durch einen MockTransport; liefert das Anfrage-Log."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    adapter._http_client = lambda: httpx.AsyncClient(base_url=adapter.http_base, transport=httpx.MockTransport(_record))
    return seen


# ── Endpunkte ──────────────────────────────────────────────────────────────────


def test_go2rtc_endpoints_and_defaults() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": " cams.lan "})
    assert adapter.cfg.port == 1984
    assert adapter.http_base == "http://cams.lan:1984"
    assert adapter.ws_url("webrtc", "a b") == "ws://cams.lan:1984/api/ws?src=a+b"
    assert adapter.ws_url("mse", "x") == "ws://cams.lan:1984/api/ws?src=x"
    assert adapter.mse_direct_url("x") == "ws://cams.lan:1984/api/ws?src=x"
    assert adapter.ssl_context() is None
    assert adapter.snapshot_request("x", "x", 360) == ("/api/frame.jpeg", {"src": "x", "h": 360})


def test_frigate_endpoints_ssl_and_ipv6() -> None:
    adapter = FrigateAdapter(Bus(), {"host": "fd00::5", "port": 8971, "ssl": True})
    assert adapter.http_base == "https://[fd00::5]:8971"
    assert adapter.ws_url("webrtc", "c") == "wss://[fd00::5]:8971/live/webrtc/api/ws?src=c"
    assert adapter.ws_url("mse", "c") == "wss://[fd00::5]:8971/live/mse/api/ws?src=c"
    assert adapter.snapshot_request("hof", "cam_hof", 200) == ("/api/hof/latest.jpg", {"h": 200})
    assert FrigateAdapter(Bus(), {"host": "f"}).cfg.port == 5000


def test_ssl_context_verify_toggle() -> None:
    verified = Go2rtcAdapter(Bus(), {"host": "h", "ssl": True}).ssl_context()
    assert verified is not None and verified.verify_mode == ssl.CERT_REQUIRED
    unverified = Go2rtcAdapter(Bus(), {"host": "h", "ssl": True, "verify_tls": False}).ssl_context()
    assert unverified is not None and unverified.verify_mode == ssl.CERT_NONE and not unverified.check_hostname


def test_mse_direct_url_only_in_direct_mode_without_credentials() -> None:
    assert Go2rtcAdapter(Bus(), {"host": "h", "mse_mode": "proxy"}).mse_direct_url("x") is None
    assert Go2rtcAdapter(Bus(), {"host": "h", "username": "u"}).mse_direct_url("x") is None


def test_http_client_uses_config() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "h", "port": 1, "verify_tls": False})
    client = adapter._http_client()
    assert str(client.base_url) == "http://h:1"
    assert client.follow_redirects is False


# ── Anmeldung / Anfragen ───────────────────────────────────────────────────────


async def test_go2rtc_basic_auth_header_and_none_without_user() -> None:
    assert await Go2rtcAdapter(Bus(), {"host": "h"}).auth_headers() == {}
    headers = await Go2rtcAdapter(Bus(), {"host": "h", "username": "u", "password": "p"}).auth_headers()
    assert headers == {"Authorization": "Basic dTpw"}


async def test_request_maps_errors() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "h"})
    _use_transport(adapter, lambda r: httpx.Response(401))
    with pytest.raises(StreamSourceError, match="unauthorized"):
        await adapter.request("GET", "/api")
    _use_transport(adapter, lambda r: httpx.Response(500))
    with pytest.raises(StreamSourceError, match="HTTP 500"):
        await adapter.request("GET", "/api")
    _use_transport(adapter, lambda r: httpx.Response(200, json={}))
    assert (await adapter.request("GET", "/api")).status_code == 200


async def test_frigate_login_cookie_and_relogin_on_401() -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f", "username": "u", "password": "p"})
    logins: list[dict] = []
    tokens = iter(["t1", "t2"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            logins.append(json.loads(request.content))
            return httpx.Response(200, headers={"set-cookie": f"frigate_token={next(tokens)}; Path=/"})
        if request.headers.get("cookie") == "frigate_token=t1":
            return httpx.Response(401)  # Token abgelaufen
        return httpx.Response(200, json={"ok": True})

    seen = _use_transport(adapter, handler)
    resp = await adapter.request("GET", "/api/version")
    assert resp.json() == {"ok": True}
    assert logins == [{"user": "u", "password": "p"}, {"user": "u", "password": "p"}]
    assert [r.headers.get("cookie") for r in seen if r.url.path == "/api/version"] == ["frigate_token=t1", "frigate_token=t2"]
    # Gültiges Token wird wiederverwendet.
    await adapter.request("GET", "/api/version")
    assert len(logins) == 2


async def test_frigate_without_user_does_not_login_or_retry() -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f"})
    seen = _use_transport(adapter, lambda r: httpx.Response(401))
    assert await adapter.auth_headers() == {}
    with pytest.raises(StreamSourceError):
        await adapter.request("GET", "/api/version")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [httpx.Response(401), httpx.Response(200)],  # abgelehnt bzw. ohne Cookie
)
async def test_frigate_login_failure(response: httpx.Response) -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f", "username": "u"})
    _use_transport(adapter, lambda r: response)
    with pytest.raises(StreamSourceError, match="login failed"):
        await adapter.auth_headers()


# ── Kameras / Streams ──────────────────────────────────────────────────────────


async def test_frigate_cameras_from_config() -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f"})
    _use_transport(adapter, lambda r: httpx.Response(200, json=FRIGATE_CONFIG))
    cameras = await adapter.list_cameras()
    assert cameras == [
        CameraInfo("hof", (StreamInfo("Main", "cam_hof"), StreamInfo("Sub", "cam_hof_sub"))),
        CameraInfo("alt", (StreamInfo("alt", "alt"),)),
    ]
    assert await adapter.camera_of("cam_hof_sub") == "hof"
    assert await adapter.camera_of("nope") is None


@pytest.mark.parametrize(
    "payload",
    [
        [],  # kein Objekt
        {"cameras": []},  # cameras kein Objekt
        {"cameras": {"x": {"live": "kaputt"}}, "go2rtc": "kaputt"},  # weder Mapping noch Restream
    ],
)
async def test_frigate_cameras_tolerate_odd_config(payload: Any) -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f"})
    _use_transport(adapter, lambda r: httpx.Response(200, json=payload))
    assert await adapter.list_cameras() == []


async def test_go2rtc_streams_sorted_and_filtered() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "g"})
    _use_transport(adapter, lambda r: httpx.Response(200, json={"b": {}, "a": {}, "": {}}))
    assert [c.name for c in await adapter.list_cameras()] == ["a", "b"]
    _use_transport(adapter, lambda r: httpx.Response(200, json=["a"]))
    assert await adapter.list_cameras(force=True) == []


async def test_camera_cache_and_force(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "g"})
    seen = _use_transport(adapter, lambda r: httpx.Response(200, json={"a": {}}))
    now = {"t": 100.0}
    monkeypatch.setattr(base_module, "time", type("T", (), {"monotonic": staticmethod(lambda: now["t"])}))
    await adapter.list_cameras()  # holt (t=100)
    now["t"] = 110.0
    await adapter.list_cameras()  # Cache
    now["t"] = 200.0
    await adapter.list_cameras()  # abgelaufen → holt
    assert len(seen) == 2
    assert await adapter.owns_src("a") is True
    assert await adapter.owns_src("b") is False
    await adapter.list_cameras(force=True)
    assert len(seen) == 3


async def test_fetch_snapshot() -> None:
    adapter = FrigateAdapter(Bus(), {"host": "f"})
    adapter._cameras = [CameraInfo("hof", (StreamInfo("Sub", "cam_hof_sub"),))]
    adapter._cameras_at = 1e12  # Cache gilt

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/hof/latest.jpg"
        assert request.url.params["h"] == "240"
        return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg; charset=binary"})

    _use_transport(adapter, handler)
    assert await adapter.fetch_snapshot("cam_hof_sub", 240) == (b"JPEG", "image/jpeg")

    with pytest.raises(StreamSourceError, match="unknown stream"):
        await adapter.fetch_snapshot("nope", 240)

    _use_transport(adapter, lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))
    with pytest.raises(StreamSourceError, match="not an image"):
        await adapter.fetch_snapshot("cam_hof_sub", 240)


# ── Lebenszyklus / Status ──────────────────────────────────────────────────────


async def test_connect_without_host_reports_error() -> None:
    bus = Bus()
    adapter = Go2rtcAdapter(bus, {"host": "  "})
    await adapter.connect()
    assert adapter._health_task is None
    assert adapter.last_detail_code == "streamSourceNoHost"
    assert adapter.last_severity == "error"


async def test_health_reports_only_changes() -> None:
    bus = Bus()
    adapter = Go2rtcAdapter(bus, {"host": "g"})
    state = {"up": True}
    _use_transport(adapter, lambda r: httpx.Response(200 if state["up"] else 503))

    await adapter.check_health()
    await adapter.check_health()
    assert adapter.connected and adapter.last_detail_code == "streamSourceConnected"
    assert len(bus.events) == 1

    state["up"] = False
    await adapter.check_health()
    await adapter.check_health()
    assert not adapter.connected and adapter.last_detail_code == "streamSourceUnreachable"
    assert adapter.last_detail_params == {"error": "HTTP 503"}
    assert len(bus.events) == 2

    # Unerreichbar nach anderem Fehlerzustand (z. B. getrennt) wird erneut gemeldet.
    adapter._last_detail_code = "streamSourceDisconnected"
    await adapter.check_health()
    assert len(bus.events) == 3


async def test_health_handles_transport_errors() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "g"})

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _use_transport(adapter, boom)
    await adapter.check_health()
    assert adapter.last_detail_code == "streamSourceUnreachable"


async def test_connect_starts_loop_and_disconnect_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = Bus()
    adapter = FrigateAdapter(bus, {"host": "f"})
    _use_transport(adapter, lambda r: httpx.Response(200, json={}))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.Event().wait()  # bleibt hängen bis zum Abbruch

    monkeypatch.setattr(base_module, "_sleep", fake_sleep)
    await adapter.connect()
    for _ in range(20):
        if sleeps:
            break
        await asyncio.sleep(0)
    assert adapter.connected
    assert sleeps == [base_module.HEALTH_INTERVAL]

    await adapter.disconnect()
    assert adapter._health_task is None
    assert not adapter.connected
    assert adapter.last_detail_code == "streamSourceDisconnected"
    # Zweites disconnect ohne Task ist harmlos.
    await adapter.disconnect()


async def test_read_write_are_noops() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "g"})
    assert await adapter.read(object()) is None
    assert await adapter.write(object(), 1) is None
    assert Go2rtcAdapter.writes_values is False


async def test_on_unauthorized_default_is_false() -> None:
    adapter = Go2rtcAdapter(Bus(), {"host": "g", "username": "u"})
    assert await adapter._on_unauthorized() is False
    adapter.auth_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    _use_transport(adapter, lambda r: httpx.Response(401))
    with pytest.raises(StreamSourceError):
        await adapter.request("GET", "/api")
