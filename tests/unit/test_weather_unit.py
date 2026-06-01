from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

import obs.api.v1.weather as weather


class _Resp:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, json_data: Any = None, json_exc: Exception | None = None):
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self._json_data = json_data
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


class _ClientStub:
    def __init__(self, response: _Resp):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _url: str):
        return self._response


@pytest.mark.asyncio
async def test_check_ssrf_blocks_loopback_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))])

    with pytest.raises(HTTPException) as exc:
        await weather._check_ssrf("http://example.test", allow_private_networks=True)

    assert exc.value.status_code == 400
    assert "nicht erlaubt" in exc.value.detail


@pytest.mark.asyncio
async def test_check_ssrf_blocks_private_network_for_public_requests(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("192.168.1.10", 0))])

    with pytest.raises(HTTPException) as exc:
        await weather._check_ssrf("http://example.test", allow_private_networks=False)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_check_ssrf_allows_private_network_for_authenticated_requests(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("192.168.1.10", 0))])

    await weather._check_ssrf("http://example.test", allow_private_networks=True)


@pytest.mark.asyncio
async def test_check_ssrf_unresolvable_host_returns_502(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise socket.gaierror("name lookup failed")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)

    with pytest.raises(HTTPException) as exc:
        await weather._check_ssrf("http://missing.example", allow_private_networks=True)

    assert exc.value.status_code == 502
    assert "nicht auflösbar" in exc.value.detail


@pytest.mark.asyncio
async def test_fetch_weather_rejects_non_http_scheme():
    with pytest.raises(HTTPException) as exc:
        await weather.fetch_weather(url="ftp://example.com/weather", current_user="alice")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_fetch_weather_rejects_redirect(monkeypatch):
    async def _ok(_url: str, *, allow_private_networks: bool):
        return None

    monkeypatch.setattr(weather, "_check_ssrf", _ok)
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **_kwargs: _ClientStub(_Resp(status_code=302)))

    with pytest.raises(HTTPException) as exc:
        await weather.fetch_weather(url="http://example.com/weather", current_user="alice")

    assert exc.value.status_code == 400
    assert "Redirects" in exc.value.detail


@pytest.mark.asyncio
async def test_fetch_weather_rejects_non_json_content_type(monkeypatch):
    async def _ok(_url: str, *, allow_private_networks: bool):
        return None

    monkeypatch.setattr(weather, "_check_ssrf", _ok)
    monkeypatch.setattr(
        weather.httpx,
        "AsyncClient",
        lambda **_kwargs: _ClientStub(_Resp(status_code=200, headers={"content-type": "text/html"}, json_data={"ignored": True})),
    )

    with pytest.raises(HTTPException) as exc:
        await weather.fetch_weather(url="http://example.com/weather", current_user="alice")

    assert exc.value.status_code == 502
    assert "kein JSON" in exc.value.detail


@pytest.mark.asyncio
async def test_fetch_weather_rejects_invalid_json(monkeypatch):
    async def _ok(_url: str, *, allow_private_networks: bool):
        return None

    monkeypatch.setattr(weather, "_check_ssrf", _ok)
    monkeypatch.setattr(
        weather.httpx,
        "AsyncClient",
        lambda **_kwargs: _ClientStub(_Resp(status_code=200, json_exc=ValueError("broken json"))),
    )

    with pytest.raises(HTTPException) as exc:
        await weather.fetch_weather(url="http://example.com/weather", current_user="alice")

    assert exc.value.status_code == 502
    assert "gültiges JSON" in exc.value.detail


@pytest.mark.asyncio
async def test_fetch_weather_success_returns_payload(monkeypatch):
    async def _ok(_url: str, *, allow_private_networks: bool):
        return None

    payload = {"ok": True, "temp": 21.0}
    monkeypatch.setattr(weather, "_check_ssrf", _ok)
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **_kwargs: _ClientStub(_Resp(status_code=200, json_data=payload)))

    response = await weather.fetch_weather(url="http://example.com/weather", current_user=None)

    assert response.status_code == 200
    assert response.body.decode("utf-8") == '{"ok":true,"temp":21.0}'


@pytest.mark.asyncio
async def test_fetch_weather_httpx_request_error_returns_502(monkeypatch):
    async def _ok(_url: str, *, allow_private_networks: bool):
        return None

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url: str):
            raise httpx.RequestError("offline")

    monkeypatch.setattr(weather, "_check_ssrf", _ok)
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **_kwargs: _FailingClient())

    with pytest.raises(HTTPException) as exc:
        await weather.fetch_weather(url="http://example.com/weather", current_user="alice")

    assert exc.value.status_code == 502
    assert "nicht erreichbar" in exc.value.detail
