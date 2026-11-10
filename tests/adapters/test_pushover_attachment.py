"""Security and transport tests for Pushover image attachments (issue #1328).

The image download runs through a real ``httpx.AsyncClient`` backed by a
``MockTransport``; DNS is faked at ``socket.getaddrinfo`` so the real URL Target
Allowlist policy decides every hop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from obs.adapters.message.adapter import MessageAdapter
from obs.adapters.message.providers import pushover_attachment
from obs.adapters.message.providers.pushover import PushoverProvider
from obs.adapters.message.providers.pushover_attachment import (
    PUSHOVER_ATTACHMENT_MAX_BYTES,
    PushoverAttachmentError,
    fetch_pushover_image,
)
from obs.config import SecuritySettings, Settings, override_settings
from obs.core.event_bus import DataValueEvent
from obs.security.url_targets import add_allowed_url_target, is_non_public_address
from tests.adapters.test_message_adapter import _message_binding

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
GIF = b"GIF89a" + b"\x00" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _allowlist(tmp_path):
    override_settings(
        Settings(security=SecuritySettings(jwt_secret="unit-test-secret-32-chars-xxx", url_target_allowlist_path=str(tmp_path / "allow.yaml")))
    )


def _dns(mapping: dict[str, list[str]]):
    def _getaddrinfo(host, port, *args, **kwargs):
        if host in mapping:
            return [(None, None, None, None, (ip, port or 0)) for ip in mapping[host]]
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise OSError("Name or service not known") from exc
        return [(None, None, None, None, (host, port or 0))]

    return patch("obs.security.url_targets.socket.getaddrinfo", side_effect=_getaddrinfo)


class _Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _install(monkeypatch, handler) -> _Recorder:
    recorder = _Recorder(handler)

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder)
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return recorder


def _image(content: bytes = JPEG, content_type: str = "image/jpeg", **headers) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type, **headers}, content=content)


async def _fetch_error(url: str) -> PushoverAttachmentError:
    with pytest.raises(PushoverAttachmentError) as exc_info:
        await fetch_pushover_image(url)
    return exc_info.value


# ── Target policy ───────────────────────────────────────────────────────────


async def test_external_https_image_is_pinned_to_the_checked_ip(monkeypatch):
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({"camera.example": ["93.184.216.34"]}):
        image = await fetch_pushover_image("https://camera.example/snap/front.jpg?size=large")

    assert image.content == JPEG
    assert image.content_type == "image/jpeg"
    assert image.filename == "front.jpg"
    request = recorder.requests[0]
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/snap/front.jpg"
    assert request.headers["Host"] == "camera.example"
    assert request.extensions["sni_hostname"] == "camera.example"


async def test_allowlisted_internal_http_camera_is_fetched(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({}):
        image = await fetch_pushover_image("http://192.168.1.50/snapshot.jpg")

    assert image.content == JPEG
    assert str(recorder.requests[0].url) == "http://192.168.1.50/snapshot.jpg"
    assert "sni_hostname" not in recorder.requests[0].extensions


async def test_allowlisted_internal_network_covers_hostname_camera(monkeypatch):
    add_allowed_url_target("192.168.1.0/24")
    recorder = _install(monkeypatch, lambda request: _image(PNG, "image/png"))

    with _dns({"cam.local.example": ["192.168.1.77"]}):
        image = await fetch_pushover_image("http://cam.local.example:8080/cgi/snapshot")

    assert image.content_type == "image/png"
    assert image.filename == "snapshot.png"
    assert str(recorder.requests[0].url) == "http://192.168.1.77:8080/cgi/snapshot"
    assert recorder.requests[0].headers["Host"] == "cam.local.example:8080"


async def test_internal_camera_without_allowlist_entry_is_rejected(monkeypatch):
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({}):
        error = await _fetch_error("http://192.168.1.50/snapshot.jpg")

    assert error.code == "not_allowlisted"
    assert "URL Target Allowlist" in error.message
    assert "192.168.1.50/32" in error.message
    assert recorder.requests == []


async def test_hostname_allowlist_entry_does_not_release_its_private_ip(monkeypatch):
    with _dns({"cam.example": ["192.168.1.50"]}):
        add_allowed_url_target("cam.example")
        _install(monkeypatch, lambda request: _image())
        error = await _fetch_error("http://cam.example/snapshot.jpg")

    assert error.code == "not_allowlisted"


async def test_external_http_image_is_rejected(monkeypatch):
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("http://camera.example/snapshot.jpg")

    assert error.code == "insecure_http"
    assert "HTTPS" in error.message
    assert recorder.requests == []


async def test_allowlisted_public_ip_still_requires_https(monkeypatch):
    add_allowed_url_target("93.184.216.34")
    _install(monkeypatch, lambda request: _image())

    with _dns({}):
        error = await _fetch_error("http://93.184.216.34/snapshot.jpg")

    assert error.code == "insecure_http"


async def test_dual_stack_http_camera_uses_only_its_internal_address(monkeypatch):
    add_allowed_url_target("192.168.1.0/24")
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({"cam.example": ["2a02:1210::50", "192.168.1.5"]}):
        image = await fetch_pushover_image("http://cam.example/snapshot.jpg")

    assert image.content == JPEG
    assert [request.url.host for request in recorder.requests] == ["192.168.1.5"]


@pytest.mark.parametrize("url", ["ftp://camera.example/a.jpg", "file:///etc/passwd", "https:///missing-host.jpg", "http://[::1/a.jpg"])
async def test_non_http_or_malformed_urls_are_rejected(monkeypatch, url):
    _install(monkeypatch, lambda request: _image())

    error = await _fetch_error(url)

    assert error.code == "invalid_url"


async def test_unresolvable_host_reports_dns_error(monkeypatch):
    _install(monkeypatch, lambda request: _image())

    with _dns({}):
        error = await _fetch_error("https://does-not-exist.example/a.jpg")

    assert error.code == "dns_error"
    assert "does-not-exist.example" in error.message


def _decision(*, allowed: bool, host: str = "", resolved_ips: list[str] | None = None, blocked_ips: list[str] | None = None):
    return SimpleNamespace(
        allowed=allowed, host=host, resolved_ips=resolved_ips or [], blocked_ips=blocked_ips or [], suggested_target=None, reason="blocked"
    )


async def test_policy_rejection_without_host_or_blocked_ip_is_invalid_url():
    with patch("obs.security.url_targets.evaluate_url_target", return_value=_decision(allowed=False)):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "invalid_url"


async def test_target_without_usable_address_is_a_dns_error():
    with patch.object(pushover_attachment, "build_pinned_url_targets", side_effect=ValueError("Blocked unresolved URL target")):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "dns_error"


async def test_idna_failure_is_invalid_url():
    with patch.object(pushover_attachment, "build_pinned_url_targets", side_effect=UnicodeError("label too long")):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "invalid_url"


async def test_url_that_httpx_rejects_is_invalid_url(monkeypatch):
    _install(monkeypatch, lambda request: _image())

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a\x01b.jpg")

    assert error.code == "invalid_url"


async def test_tls_certificate_failure_is_reported_as_such(monkeypatch):
    import ssl

    def _handler(request):
        try:
            raise ssl.SSLCertVerificationError("self-signed certificate")
        except ssl.SSLError as cert_error:
            raise httpx.ConnectError("certificate verify failed", request=request) from cert_error

    _install(monkeypatch, _handler)
    add_allowed_url_target("192.168.1.50")
    with _dns({}):
        error = await _fetch_error("https://192.168.1.50/snap.jpg")

    assert error.code == "tls_error"
    assert "certificate" in error.message


# ── DNS rebinding / pinning ─────────────────────────────────────────────────


async def test_dns_answer_change_after_the_check_does_not_move_the_connection(monkeypatch):
    answers = iter([["93.184.216.34"], ["10.0.0.5"]])

    def _rebinding(host, port, *args, **kwargs):
        return [(None, None, None, None, (ip, 0)) for ip in next(answers)]

    recorder = _install(monkeypatch, lambda request: _image())
    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=_rebinding):
        await fetch_pushover_image("https://camera.example/a.jpg")

    # One resolution for the policy check; the request goes to that exact IP.
    assert [request.url.host for request in recorder.requests] == ["93.184.216.34"]


class _Stream:
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def get_extra_info(self, name):
        return (self._ip, 443) if name == "server_addr" else None


async def test_unexpected_peer_ip_is_rejected(monkeypatch):
    _install(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG, extensions={"network_stream": _Stream("10.0.0.5")}),
    )

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "ip_mismatch"


async def test_matching_peer_ip_and_missing_server_addr_are_accepted(monkeypatch):
    class _NoAddr:
        def get_extra_info(self, name):
            return None

    streams = iter([_Stream("93.184.216.34"), _NoAddr()])
    _install(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG, extensions={"network_stream": next(streams)}),
    )

    with _dns({"camera.example": ["93.184.216.34"]}):
        assert (await fetch_pushover_image("https://camera.example/a.jpg")).content == JPEG
        assert (await fetch_pushover_image("https://camera.example/a.jpg")).content == JPEG


async def test_unparsable_peer_address_is_rejected(monkeypatch):
    _install(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=JPEG, extensions={"network_stream": _Stream("not-an-ip")}
        ),
    )

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "ip_mismatch"


async def test_connect_error_falls_back_to_the_next_checked_ip(monkeypatch):
    def _handler(request):
        if request.url.host == "2001:db8::1":
            raise httpx.ConnectError("unreachable", request=request)
        return _image()

    recorder = _install(monkeypatch, _handler)
    decision = _decision(allowed=True, host="camera.example", resolved_ips=["2001:db8::1", "93.184.216.34"])
    with patch("obs.security.url_targets.evaluate_url_target", return_value=decision):
        image = await fetch_pushover_image("https://camera.example/a.jpg")

    assert image.content == JPEG
    assert [request.url.host for request in recorder.requests] == ["2001:db8::1", "93.184.216.34"]


async def test_connect_timeout_falls_back_to_the_next_checked_ip(monkeypatch):
    def _handler(request):
        if request.url.host == "2001:db8::1":
            raise httpx.ConnectTimeout("slow", request=request)
        return _image()

    recorder = _install(monkeypatch, _handler)
    decision = _decision(allowed=True, host="camera.example", resolved_ips=["2001:db8::1", "93.184.216.34"])
    with patch("obs.security.url_targets.evaluate_url_target", return_value=decision):
        image = await fetch_pushover_image("https://camera.example/a.jpg")

    assert image.content == JPEG
    assert len(recorder.requests) == 2


async def test_connect_error_on_every_ip_is_reported(monkeypatch):
    def _handler(request):
        raise httpx.ConnectError("unreachable", request=request)

    _install(monkeypatch, _handler)
    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "connect_error"
    assert "camera.example" in error.message


# ── Redirects ───────────────────────────────────────────────────────────────


async def test_redirect_to_allowed_target_is_followed_and_rechecked(monkeypatch):
    def _handler(request):
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "https://cdn.example/final.png"})
        return _image(PNG, "image/png")

    recorder = _install(monkeypatch, _handler)
    with _dns({"camera.example": ["93.184.216.34"], "cdn.example": ["93.184.216.35"]}):
        image = await fetch_pushover_image("https://camera.example/a.jpg")

    assert image.filename == "final.png"
    assert [request.url.host for request in recorder.requests] == ["93.184.216.34", "93.184.216.35"]
    assert recorder.requests[1].headers["Host"] == "cdn.example"


async def test_redirect_to_non_allowlisted_internal_target_is_rejected(monkeypatch):
    recorder = _install(monkeypatch, lambda request: httpx.Response(302, headers={"location": "http://10.0.0.5/admin"}))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "redirect_blocked"
    assert "URL Target Allowlist" in error.message
    assert len(recorder.requests) == 1


async def test_redirect_to_external_http_target_is_rejected(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(301, headers={"location": "http://camera.example/a.jpg"}))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "redirect_blocked"
    assert "HTTPS" in error.message


async def test_redirect_loop_stops_after_the_limit(monkeypatch):
    recorder = _install(monkeypatch, lambda request: httpx.Response(307, headers={"location": "/again"}))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "redirect_blocked"
    assert len(recorder.requests) == pushover_attachment.MAX_REDIRECTS + 1


async def test_redirect_without_location_is_an_http_error(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(302))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "http_error"


async def test_credentials_are_kept_for_same_origin_and_dropped_cross_origin(monkeypatch):
    add_allowed_url_target("192.168.1.0/24")
    seen: list[tuple[str, str | None]] = []

    def _handler(request):
        authorization = request.headers.get("authorization")
        seen.append((f"{request.url.host}{request.url.path}", authorization))
        if request.url.host == "192.168.1.50" and authorization is None:
            return httpx.Response(401, headers={"www-authenticate": 'Basic realm="cam"'})
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/same"})
        if request.url.path == "/same":
            return httpx.Response(302, headers={"location": "http://192.168.1.51/other"})
        return _image()

    recorder = _install(monkeypatch, _handler)
    with _dns({}):
        await fetch_pushover_image("http://user:s3cret@192.168.1.50/start")

    # Credentials only answer a challenge, only on the original origin.
    assert [(target, authorization is not None) for target, authorization in seen] == [
        ("192.168.1.50/start", False),
        ("192.168.1.50/start", True),
        ("192.168.1.50/same", False),
        ("192.168.1.50/same", True),
        ("192.168.1.51/other", False),
    ]
    assert seen[1][1].startswith("Basic ")
    assert all("s3cret" not in str(request.url) for request in recorder.requests)


async def test_unchallenged_401_is_not_retried_with_credentials(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, lambda request: httpx.Response(401))

    with _dns({}):
        error = await _fetch_error("http://user:pw@192.168.1.50/snap.jpg")

    assert error.code == "http_error"
    assert len(recorder.requests) == 1


def _digest_camera(request):
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Digest "):
        return _image()
    return httpx.Response(401, headers={"www-authenticate": 'Digest realm="cam", nonce="abc", qop="auth", algorithm=MD5'})


async def test_digest_camera_never_receives_the_password_in_clear(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    authorizations = []

    def _handler(request):
        authorizations.append(request.headers.get("authorization", ""))
        return _digest_camera(request)

    _install(monkeypatch, _handler)

    with _dns({}):
        image = await fetch_pushover_image("http://admin:pw@192.168.1.50/ISAPI/picture")

    assert image.content == JPEG
    # No password goes out before the challenge; Digest answers it directly.
    assert len(authorizations) == 2
    assert authorizations[0] == ""
    assert authorizations[1].startswith('Digest username="admin"')


async def test_basic_only_rejection_is_reported_as_http_error(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, lambda request: httpx.Response(401, headers={"www-authenticate": 'Basic realm="cam"'}))

    with _dns({}):
        error = await _fetch_error("http://admin:wrong@192.168.1.50/snap.jpg")

    assert error.code == "http_error"
    assert "HTTP 401" in error.message
    assert "wrong" not in error.message
    assert len(recorder.requests) == 2


async def test_failed_digest_login_stops_after_one_retry(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, lambda request: httpx.Response(401, headers={"www-authenticate": 'Digest realm="cam", nonce="abc", qop="auth"'}))

    with _dns({}):
        error = await _fetch_error("http://admin:wrong@192.168.1.50/snap.jpg")

    assert error.code == "http_error"
    assert len(recorder.requests) == 2


# ── Content checks ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content", "content_type", "expected_type", "extension"),
    [
        (JPEG, "image/jpeg", "image/jpeg", "jpg"),
        (JPEG, "image/jpg", "image/jpeg", "jpg"),
        (PNG, "image/png; charset=binary", "image/png", "png"),
        (GIF, "image/gif", "image/gif", "gif"),
        (WEBP, "image/webp", "image/webp", "webp"),
    ],
)
async def test_supported_image_types(monkeypatch, content, content_type, expected_type, extension):
    _install(monkeypatch, lambda request: _image(content, content_type))

    with _dns({"camera.example": ["93.184.216.34"]}):
        image = await fetch_pushover_image("https://camera.example/")

    assert image.content_type == expected_type
    assert image.filename == f"image.{extension}"


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml", ""])
async def test_unsupported_content_type_is_rejected(monkeypatch, content_type):
    _install(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": content_type}, content=JPEG))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "invalid_content_type"


async def test_content_not_matching_declared_type_is_rejected(monkeypatch):
    _install(monkeypatch, lambda request: _image(b"<html>not an image</html>", "image/jpeg"))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "invalid_content_type"


@pytest.mark.parametrize("content", [b"", b"RIFF"])
async def test_empty_or_truncated_body_is_rejected(monkeypatch, content):
    _install(monkeypatch, lambda request: _image(content, "image/webp"))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.webp")

    assert error.code == "invalid_content_type"


async def test_short_valid_body_is_accepted(monkeypatch):
    _install(monkeypatch, lambda request: _image(b"GIF89a", "image/gif"))

    with _dns({"camera.example": ["93.184.216.34"]}):
        image = await fetch_pushover_image("https://camera.example/a.gif")

    assert image.content == b"GIF89a"


async def test_declared_oversize_image_is_rejected_before_download(monkeypatch):
    _install(monkeypatch, lambda request: _image(JPEG, "image/jpeg", **{"content-length": str(PUSHOVER_ATTACHMENT_MAX_BYTES + 1)}))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "too_large"
    assert "5 MB" in error.message


async def test_streamed_oversize_image_is_rejected(monkeypatch):
    monkeypatch.setattr(pushover_attachment, "PUSHOVER_ATTACHMENT_MAX_BYTES", 16)

    async def _chunks():
        yield JPEG[:12]
        yield b"\x00" * 12

    _install(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_chunks()))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "too_large"


def test_declared_length_check_ignores_unparsable_values():
    response = httpx.Response(200, headers={"content-length": "bogus"})
    pushover_attachment._check_declared_length(response)


@pytest.mark.parametrize("status_code", [404, 500, 204])
async def test_non_success_status_is_reported(monkeypatch, status_code):
    _install(monkeypatch, lambda request: httpx.Response(status_code))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    if status_code == 204:
        assert error.code == "invalid_content_type"
    else:
        assert error.code == "http_error"
        assert f"HTTP {status_code}" in error.message


# ── Timeouts and transport errors ───────────────────────────────────────────


async def test_transport_timeout_is_reported(monkeypatch):
    def _handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    _install(monkeypatch, _handler)
    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "timeout"


async def test_overall_deadline_bounds_a_slow_download(monkeypatch):
    monkeypatch.setattr(pushover_attachment, "DOWNLOAD_DEADLINE_SECONDS", 0.05)

    async def _slow_chunks():
        yield JPEG[:12]
        await asyncio.sleep(1)
        yield b"\x00"

    _install(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_slow_chunks()))

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "timeout"


async def test_other_transport_errors_are_reported_without_details(monkeypatch):
    def _handler(request):
        raise httpx.RemoteProtocolError("peer closed http://user:pw@camera.example/?key=abc", request=request)

    _install(monkeypatch, _handler)
    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://user:pw@camera.example/a.jpg?key=abc")

    assert error.code == "connect_error"
    assert error.message == "image download from camera.example failed (RemoteProtocolError)"


def test_host_for_message_handles_malformed_urls():
    assert pushover_attachment._host_for_message("http://[::1") == "?"
    assert pushover_attachment._host_for_message("relative/path") == "?"


def test_origin_handles_invalid_port():
    assert pushover_attachment._origin("http://camera.example:99999/") is None


def test_ipv6_target_uses_bracketed_pinned_host_and_host_header():
    decision = _decision(allowed=True, host="2001:db8::5", resolved_ips=["2001:db8::5"])
    with patch("obs.security.url_targets.evaluate_url_target", return_value=decision):
        plan = pushover_attachment._plan_request("https://[2001:db8::5]:8443/a.jpg")

    assert plan.candidates == [("https://[2001:db8::5]:8443/a.jpg", "2001:db8::5")]
    assert plan.headers == {"Host": "[2001:db8::5]:8443", "Accept-Encoding": "identity"}


def test_is_non_public_address():
    assert is_non_public_address("192.168.1.50") is True
    assert is_non_public_address("127.0.0.1") is True
    assert is_non_public_address("93.184.216.34") is False
    assert is_non_public_address("not-an-ip") is False


# ── Provider integration: attachment, failure reporting, secrets ────────────


def _pushover_handler(image_response):
    def _handler(request):
        if request.url.host == "api.pushover.net":
            return httpx.Response(200, json={"status": 1})
        return image_response(request)

    return _handler


async def _send(context):
    return await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app-token-secret", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user-key-secret"},
        title="Alarm",
        message="Door",
        context=context,
    )


async def test_provider_uploads_the_image_as_multipart_attachment(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, _pushover_handler(lambda request: _image()))

    with _dns({}):
        result = await _send({"pushover": {"image_url": "http://192.168.1.50/snapshot.jpg", "url": "https://obs.example", "url_title": "Open"}})

    assert result.ok is True
    post = recorder.requests[-1]
    assert post.url.host == "api.pushover.net"
    body = post.content
    assert post.headers["content-type"].startswith("multipart/form-data")
    assert b'name="attachment"; filename="snapshot.jpg"' in body
    assert b"Content-Type: image/jpeg" in body
    assert JPEG in body
    assert b'name="url"\r\n\r\nhttps://obs.example' in body
    assert b'name="url_title"\r\n\r\nOpen' in body


async def test_provider_without_image_posts_form_data(monkeypatch):
    recorder = _install(monkeypatch, _pushover_handler(lambda request: _image()))

    result = await _send({})

    assert result.ok is True
    assert recorder.requests[-1].headers["content-type"] == "application/x-www-form-urlencoded"


async def test_provider_fails_the_send_when_the_image_cannot_be_loaded(monkeypatch, caplog):
    recorder = _install(monkeypatch, _pushover_handler(lambda request: _image()))

    with _dns({}):
        result = await _send({"pushover": {"image_url": "http://user:camera-pass@192.168.1.50/snap.jpg?apikey=cam-key"}})

    assert result.ok is False
    assert result.detail.startswith("image attachment failed (not_allowlisted):")
    assert "URL Target Allowlist" in result.detail
    assert all(request.url.host != "api.pushover.net" for request in recorder.requests)
    for secret in ("camera-pass", "cam-key", "app-token-secret", "user-key-secret"):
        assert secret not in result.detail
        assert secret not in caplog.text


async def test_provider_reports_pushover_timeout_and_transport_errors(monkeypatch):
    def _timeout(request):
        raise httpx.ConnectTimeout("slow", request=request)

    _install(monkeypatch, _timeout)
    result = await _send({})
    assert (result.ok, result.detail) == (False, "Pushover request timed out")

    def _broken(request):
        raise httpx.ConnectError("token=app-token-secret", request=request)

    _install(monkeypatch, _broken)
    result = await _send({})
    assert (result.ok, result.detail) == (False, "Pushover request failed (ConnectError)")


async def test_provider_reports_pushover_api_rejection_with_errors(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(400, json={"status": 0, "errors": ["retry is invalid"]}))

    result = await _send({})

    assert result.ok is False
    assert result.detail == "HTTP 400: retry is invalid"


async def test_provider_end_to_end_logs_failure_without_secrets(monkeypatch, caplog):
    """Through the adapter: a failed attachment is logged and secrets stay out of it."""
    caplog.set_level(logging.WARNING)
    _install(monkeypatch, _pushover_handler(lambda request: _image()))

    adapter = MessageAdapter(
        event_bus=None,
        config={"providers": {"pushover": {"enabled": True, "api_token": "app-token-secret", "targets": {"phone": {"user_key": "user-key-secret"}}}}},
    )
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id,
        providers=[{"provider": "pushover", "target": "phone"}],
        pushover={"image_url": "http://admin:camera-pass@192.168.1.50/snap.jpg?token=cam-key"},
    )
    event = DataValueEvent(datapoint_id=dp_id, value=1, quality="good", source_adapter="test")

    with _dns({}):
        results = await adapter._send_to_targets(adapter.binding_config_schema(**binding.config), binding, event, "body")

    assert results[0].ok is False
    assert "image attachment failed (not_allowlisted)" in caplog.text
    for secret in ("camera-pass", "cam-key", "app-token-secret", "user-key-secret"):
        assert secret not in caplog.text
        assert secret not in results[0].detail


async def test_open_camera_with_credentials_needs_no_auth_round_trip(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    recorder = _install(monkeypatch, lambda request: _image())

    with _dns({}):
        image = await fetch_pushover_image("http://user:pw@192.168.1.50/snap.jpg")

    assert image.content == JPEG
    assert len(recorder.requests) == 1
    assert "authorization" not in recorder.requests[0].headers


async def test_compressed_image_bodies_are_rejected_and_not_requested(monkeypatch):
    async def _gzip_body():
        yield b"\x1f\x8b" + b"\x00" * 64  # never inflated: rejected before reading

    recorder = _install(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "image/jpeg", "content-encoding": "gzip"}, content=_gzip_body()),
    )

    with _dns({"camera.example": ["93.184.216.34"]}):
        error = await _fetch_error("https://camera.example/a.jpg")

    assert error.code == "invalid_content_type"
    assert "compressed" in error.message
    assert recorder.requests[0].headers["accept-encoding"] == "identity"


async def test_challenge_body_is_never_read(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    read_chunks = []

    async def _endless_body():
        while True:
            read_chunks.append(1)
            yield b"x" * 65536

    def _handler(request):
        if request.headers.get("authorization", "").startswith("Basic "):
            return _image()
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="cam"'}, content=_endless_body())

    _install(monkeypatch, _handler)
    with _dns({}):
        image = await fetch_pushover_image("http://user:pw@192.168.1.50/snap.jpg")

    assert image.content == JPEG
    assert read_chunks == []


async def test_basic_challenge_with_digest_in_the_realm_uses_basic(monkeypatch):
    add_allowed_url_target("192.168.1.50")

    def _handler(request):
        if request.headers.get("authorization", "").startswith("Basic "):
            return _image()
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="Digest-Cam"'})

    _install(monkeypatch, _handler)
    with _dns({}):
        image = await fetch_pushover_image("http://user:pw@192.168.1.50/snap.jpg")

    assert image.content == JPEG


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (['Basic realm="Digest-Cam"'], {"basic"}),
        (['Digest realm="cam", nonce="a", qop="auth"'], {"digest"}),
        (['Basic realm="x", Digest realm="y", nonce="n"'], {"basic", "digest"}),
        (['Basic realm="x"', 'Digest realm="y"'], {"basic", "digest"}),
        (["Negotiate abc=="], {"negotiate"}),
        (["Basic"], {"basic"}),
        ([], set()),
    ],
)
def test_challenge_schemes(headers, expected):
    assert {scheme for scheme, _challenge in pushover_attachment._challenges(headers)} == expected


async def test_combined_basic_and_digest_challenge_line_uses_digest(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    authorizations = []

    def _handler(request):
        authorization = request.headers.get("authorization", "")
        authorizations.append(authorization)
        if authorization.startswith("Digest "):
            return _image()
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="cam", Digest realm="cam", nonce="abc", qop="auth"'})

    _install(monkeypatch, _handler)
    with _dns({}):
        image = await fetch_pushover_image("http://user:pw@192.168.1.50/snap.jpg")

    assert image.content == JPEG
    assert not any(authorization.startswith("Basic ") for authorization in authorizations)


async def test_peer_is_verified_before_credentials_answer_a_challenge(monkeypatch):
    add_allowed_url_target("192.168.1.50")
    authorizations = []

    def _handler(request):
        authorizations.append(request.headers.get("authorization"))
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="cam"'}, extensions={"network_stream": _Stream("192.168.1.99")})

    _install(monkeypatch, _handler)
    with _dns({}):
        error = await _fetch_error("http://user:pw@192.168.1.50/snap.jpg")

    assert error.code == "ip_mismatch"
    assert authorizations == [None]


def test_challenges_are_split_per_scheme():
    assert pushover_attachment._challenges(['Basic realm="a, b", Digest realm="Basic x", nonce="n"']) == [
        ("basic", 'Basic realm="a, b"'),
        ("digest", 'Digest realm="Basic x", nonce="n"'),
    ]


async def test_tls_failure_on_one_address_falls_back_to_the_next(monkeypatch):
    import ssl

    def _handler(request):
        if request.url.host == "2001:db8::1":
            try:
                raise ssl.SSLCertVerificationError("bad chain")
            except ssl.SSLError as cert_error:
                raise httpx.ConnectError("certificate verify failed", request=request) from cert_error
        return _image()

    _install(monkeypatch, _handler)
    decision = _decision(allowed=True, host="camera.example", resolved_ips=["2001:db8::1", "93.184.216.34"])
    with patch("obs.security.url_targets.evaluate_url_target", return_value=decision):
        image = await fetch_pushover_image("https://camera.example/a.jpg")

    assert image.content == JPEG
