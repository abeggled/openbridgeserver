"""Ingress address checks for the WEBHOOK adapter (issue #1256).

This is deliberately *not* ``obs/security/url_targets.py``.  That module is an
**egress** allowlist: it decides where OBS itself may connect to and protects
against SSRF towards private or internal targets.  The check here runs in the
opposite direction — which remote address may *reach* a webhook endpoint — so it
guards a different threat on a different code path.  Only the CIDR matching
technique (``ipaddress``) is shared, and that is cheap enough to keep separate
rather than bending the egress allowlist into serving both.
"""

from __future__ import annotations

import ipaddress

type IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_SEPARATORS = ",;"


def split_entries(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Split an allowlist value into its entries.

    Accepts both the list the schemas use today and the comma / semicolon /
    whitespace separated string they used before (issue #1256 follow-up), so a
    stored configuration keeps loading after the field became a list.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(entry).strip() for entry in raw if str(entry).strip()]
    normalised = str(raw)
    for separator in _SEPARATORS:
        normalised = normalised.replace(separator, " ")
    return [part for part in normalised.split() if part]


def normalise_entries(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Return the canonical text form of each entry, duplicates removed.

    ``10.38.111.21/16`` is stored as ``10.38.0.0/16`` and ``192.168.1.5`` as
    ``192.168.1.5/32``, so what the GUI lists back is what actually matches.
    """
    seen: dict[str, None] = {}
    for network in parse_networks(raw):
        seen.setdefault(str(network), None)
    return list(seen)


def parse_networks(raw: str | list[str] | tuple[str, ...] | None) -> list[IpNetwork]:
    """Parse an allowlist into networks.

    A bare address (``192.168.1.5``) becomes its single-host network.  Invalid
    entries raise ``ValueError`` so the config schemas reject them when the
    instance or binding is saved instead of failing open at request time.
    """
    return [ipaddress.ip_network(entry, strict=False) for entry in split_entries(raw)]


def _normalise(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped IPv6 addresses so IPv4 CIDRs match them.

    A dual-stack listener reports a client that connected over IPv4 as
    ``::ffff:192.168.1.5``; without this an allowlist of ``192.168.1.0/24``
    would never match it.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def address_allowed(client_ip: str | None, networks: list[IpNetwork]) -> bool:
    """Whether *client_ip* is covered by *networks*.

    An empty allowlist allows everything — that is the documented default, so
    an instance without an allowlist stays reachable.  A non-empty allowlist
    fails closed: an unknown or unparseable client address is rejected.
    """
    if not networks:
        return True
    if not client_ip:
        return False
    try:
        reported = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    # Try both forms: the unwrapped IPv4 address matches IPv4 CIDRs, while the
    # reported mapped form still matches an IPv6 entry such as
    # ``::ffff:192.168.1.0/120`` (an IPv4 address is never "in" an IPv6 network,
    # so the extra candidate cannot widen what a plain IPv4 entry allows).
    candidates = (reported, _normalise(reported))
    return any(candidate in network for network in networks for candidate in candidates)


def _strip_port(value: str) -> str:
    """Remove a trailing port from a ``host[:port]`` / ``[v6]:port`` token."""
    if value.startswith("["):
        closing = value.find("]")
        return value[1:closing] if closing != -1 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def resolve_client_ip(peer_ip: str | None, forwarded_for: str | None, *, trust_forwarded_for: bool) -> str | None:
    """Return the address the allowlist and the rate limiter should judge.

    ``X-Forwarded-For`` is honoured only when the instance opts in.  On a
    directly reachable port that header is attacker-controlled, so trusting it
    by default would let any caller claim an allowlisted source address.  With
    a reverse proxy in front, the left-most entry is the original client as
    written by that proxy.
    """
    if trust_forwarded_for and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return _strip_port(first)
    return peer_ip
