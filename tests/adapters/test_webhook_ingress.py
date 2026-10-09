"""Unit tests for the WEBHOOK adapter's ingress address checks (issue #1256)."""

from __future__ import annotations

import ipaddress

import pytest

from obs.adapters.webhook.ingress import (
    address_allowed,
    parse_networks,
    resolve_client_ip,
    split_entries,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        ([], []),
        (["10.0.0.0/8", " 192.168.1.5 ", "", "  "], ["10.0.0.0/8", "192.168.1.5"]),
        (("10.0.0.0/8",), ["10.0.0.0/8"]),
        ("10.0.0.0/8", ["10.0.0.0/8"]),
        ("10.0.0.0/8,192.168.1.0/24", ["10.0.0.0/8", "192.168.1.0/24"]),
        ("10.0.0.0/8; 192.168.1.0/24", ["10.0.0.0/8", "192.168.1.0/24"]),
        ("10.0.0.0/8\n192.168.1.0/24", ["10.0.0.0/8", "192.168.1.0/24"]),
    ],
)
def test_split_entries(raw, expected):
    assert split_entries(raw) == expected


def test_normalise_entries_canonicalises_and_deduplicates():
    from obs.adapters.webhook.ingress import normalise_entries

    assert normalise_entries(["10.38.111.21/16", "10.38.0.0/16", "192.168.1.5"]) == ["10.38.0.0/16", "192.168.1.5/32"]
    assert normalise_entries("") == []


def test_parse_networks_accepts_bare_addresses_and_host_bits():
    networks = parse_networks("192.168.1.5, 10.1.2.3/8, fd00::/8")
    assert networks == [
        ipaddress.ip_network("192.168.1.5/32"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("fd00::/8"),
    ]


@pytest.mark.parametrize("raw", ["192.168.1.0/33", "not-an-ip", "10.0.0.1/-1"])
def test_parse_networks_rejects_malformed_entries(raw):
    with pytest.raises(ValueError):
        parse_networks(raw)


def test_empty_allowlist_allows_everything():
    assert address_allowed("203.0.113.7", []) is True
    assert address_allowed(None, []) is True


@pytest.mark.parametrize(
    ("client_ip", "expected"),
    [
        ("192.168.1.7", True),
        ("192.168.2.7", False),
        ("::ffff:192.168.1.7", True),  # IPv4-mapped IPv6 from a dual-stack listener
        ("fd00::1", False),
        (None, False),
        ("garbage", False),
    ],
)
def test_address_allowed_against_a_non_empty_allowlist(client_ip, expected):
    assert address_allowed(client_ip, parse_networks("192.168.1.0/24")) is expected


@pytest.mark.parametrize(
    ("client_ip", "expected"),
    [
        ("::ffff:192.168.1.5", True),  # the mapped form is matched as written …
        ("192.168.1.5", False),  # … but a plain IPv4 caller is not inside an IPv6 entry
        ("::ffff:192.168.2.5", False),
    ],
)
def test_address_allowed_matches_ipv4_mapped_ipv6_networks(client_ip, expected):
    assert address_allowed(client_ip, parse_networks("::ffff:192.168.1.0/120")) is expected


def test_address_allowed_matches_ipv6_networks():
    assert address_allowed("fd00::5", parse_networks("fd00::/8")) is True


@pytest.mark.parametrize(
    ("peer", "forwarded", "trust", "expected"),
    [
        ("10.0.0.1", None, False, "10.0.0.1"),
        ("10.0.0.1", "192.168.1.7", False, "10.0.0.1"),
        ("10.0.0.1", "192.168.1.7", True, "192.168.1.7"),
        ("10.0.0.1", "192.168.1.7, 10.0.0.1", True, "192.168.1.7"),
        ("10.0.0.1", "192.168.1.7:51234", True, "192.168.1.7"),
        ("10.0.0.1", "[fd00::1]:51234", True, "fd00::1"),
        ("10.0.0.1", "[fd00::1", True, "[fd00::1"),
        ("10.0.0.1", "fd00::1", True, "fd00::1"),
        ("10.0.0.1", "", True, "10.0.0.1"),
        ("10.0.0.1", "  ,  ", True, "10.0.0.1"),
        (None, None, False, None),
    ],
)
def test_resolve_client_ip(peer, forwarded, trust, expected):
    assert resolve_client_ip(peer, forwarded, trust_forwarded_for=trust) == expected
