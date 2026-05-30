from __future__ import annotations

import asyncio
import base64
import socket

import pytest

from obs.logic.manager import (
    _build_cookie_header,
    _build_ical_fetch_target,
    _build_ical_fetch_targets,
    _is_public_http_url,
    _preserve_same_origin_credentials,
    _read_limited_response_body,
    _store_response_cookies,
)


def test_is_public_http_url_blocks_non_http_scheme() -> None:
    assert _is_public_http_url("file:///etc/passwd") is False


def test_is_public_http_url_blocks_loopback(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("http://localhost/calendar.ics") is False


def test_is_public_http_url_allows_public_ip(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("https://example.com/calendar.ics") is True


def test_is_public_http_url_rejects_malformed_port() -> None:
    assert _is_public_http_url("https://example.com:abc/calendar.ics") is False


def test_is_public_http_url_rejects_malformed_ipv6_url() -> None:
    assert _is_public_http_url("http://[::1") is False


def test_is_public_http_url_blocks_shared_address_space(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("http://shared-space.example/calendar.ics") is False


def test_is_public_http_url_blocks_nat64_embedded_private_ipv4(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::0a00:0001", 80, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _is_public_http_url("http://nat64.example/calendar.ics") is False


def test_build_ical_fetch_target_pins_ip_and_sets_host_and_sni(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    fetch_url, headers, extensions = _build_ical_fetch_target("https://example.com/calendar.ics")
    assert fetch_url == "https://93.184.216.34/calendar.ics"
    assert headers == {"Host": "example.com"}
    assert extensions == {"sni_hostname": "example.com"}


def test_build_ical_fetch_target_brackets_ipv6_literal_host_header(monkeypatch) -> None:
    ipv6 = "2606:2800:220:1:248:1893:25c8:1946"

    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ipv6, 443, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    fetch_url, headers, _extensions = _build_ical_fetch_target(f"https://[{ipv6}]/calendar.ics")
    assert fetch_url == f"https://[{ipv6}]/calendar.ics"
    assert headers["Host"] == f"[{ipv6}]"


def test_build_ical_fetch_targets_tries_all_validated_addresses(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    fetch_urls, headers, extensions = _build_ical_fetch_targets("https://example.com/calendar.ics")
    assert fetch_urls == [
        "https://[2001:4860:4860::8888]/calendar.ics",
        "https://93.184.216.34/calendar.ics",
    ]
    assert headers == {"Host": "example.com"}
    assert extensions == {"sni_hostname": "example.com"}


def test_build_ical_fetch_target_preserves_embedded_basic_auth(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    fetch_url, headers, extensions = _build_ical_fetch_target("https://user:pass@example.com/calendar.ics")
    assert fetch_url == "https://93.184.216.34/calendar.ics"
    assert headers["Host"] == "example.com"
    assert headers["Authorization"] == f"Basic {base64.b64encode(b'user:pass').decode('ascii')}"
    assert extensions == {"sni_hostname": "example.com"}


def test_build_ical_fetch_target_decodes_urlencoded_credentials(monkeypatch) -> None:
    def _fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    _fetch_url, headers, _extensions = _build_ical_fetch_target("https://user:p%40ss%3Aword@example.com/calendar.ics")
    assert headers["Authorization"] == f"Basic {base64.b64encode(b'user:p@ss:word').decode('ascii')}"


def test_build_ical_fetch_target_encodes_idn_host_for_dns_and_sni(monkeypatch) -> None:
    captured_hostnames: list[str] = []

    def _fake_getaddrinfo(hostname, *_args, **_kwargs):
        captured_hostnames.append(hostname)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    _fetch_url, headers, extensions = _build_ical_fetch_target("https://münich.example/calendar.ics")
    assert captured_hostnames == ["xn--mnich-kva.example"]
    assert headers["Host"] == "xn--mnich-kva.example"
    assert extensions == {"sni_hostname": "xn--mnich-kva.example"}


def test_preserve_same_origin_credentials_on_absolute_redirect() -> None:
    preserved = _preserve_same_origin_credentials("https://user:p%40ss@example.com/a", "https://example.com/b")
    assert preserved == "https://user:p%40ss@example.com/b"


def test_preserve_same_origin_credentials_not_applied_cross_origin() -> None:
    preserved = _preserve_same_origin_credentials("https://user:pass@example.com/a", "https://other.example/b")
    assert preserved == "https://other.example/b"


def test_logical_cookie_store_carries_domain_cookie_across_subdomain_redirect() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc; Domain=.example.com; Path=/"], "https://auth.example.com/login")
    assert _build_cookie_header(store, "https://calendar.example.com/feed.ics") == "sid=abc"


def test_logical_cookie_store_keeps_host_only_cookie_scoped() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc; Path=/"], "https://auth.example.com/login")
    assert _build_cookie_header(store, "https://auth.example.com/feed.ics") == "sid=abc"
    assert _build_cookie_header(store, "https://calendar.example.com/feed.ics") == ""


def test_logical_cookie_default_path_uses_response_directory() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc"], "https://example.com/login")
    assert _build_cookie_header(store, "https://example.com/feed.ics") == "sid=abc"


def test_logical_cookie_path_matching_uses_segment_boundary() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc; Path=/auth"], "https://example.com/auth/login")
    assert _build_cookie_header(store, "https://example.com/auth/feed.ics") == "sid=abc"
    assert _build_cookie_header(store, "https://example.com/authz/feed.ics") == ""


def test_logical_cookie_secure_not_sent_over_http() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc; Secure; Path=/"], "https://example.com/login")
    assert _build_cookie_header(store, "https://example.com/feed.ics") == "sid=abc"
    assert _build_cookie_header(store, "http://example.com/feed.ics") == ""


def test_logical_cookie_deletion_via_max_age_removes_cookie() -> None:
    store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
    _store_response_cookies(store, ["sid=abc; Path=/"], "https://example.com/login")
    _store_response_cookies(store, ["sid=; Max-Age=0; Path=/"], "https://example.com/login")
    assert _build_cookie_header(store, "https://example.com/feed.ics") == ""


def test_read_limited_response_body_raises_on_large_response() -> None:
    class _FakeResponse:
        async def aiter_bytes(self):
            yield b"a" * 8
            yield b"b" * 8

    with pytest.raises(ValueError, match="iCal response too large"):
        asyncio.run(_read_limited_response_body(_FakeResponse(), 10))
