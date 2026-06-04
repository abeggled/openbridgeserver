"""URL target allowlist and SSRF policy helpers.

The allowlist is intentionally file-backed instead of stored in the database:
it controls where the backend may open outbound connections and should live
next to other operator-managed secrets/configuration.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import yaml

from obs.config import get_settings

_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


@dataclass(frozen=True)
class UrlTargetAllowEntry:
    id: str
    target: str
    reason: str = ""
    created_by: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class UrlTargetDecision:
    allowed: bool
    url: str
    host: str
    resolved_ips: list[str]
    blocked_ips: list[str]
    reason: str
    allowlisted_by: str | None = None
    suggested_target: str | None = None

    def api_detail(self) -> dict[str, Any]:
        return {
            "code": "url_target_blocked",
            "message": self.reason,
            "url": self.url,
            "host": self.host,
            "resolved_ips": self.resolved_ips,
            "blocked_ips": self.blocked_ips,
            "allowlisted_by": self.allowlisted_by,
            "suggested_target": self.suggested_target,
        }


@dataclass(frozen=True)
class ResolvedUrlTarget:
    original_url: str
    scheme: str
    hostname: str
    hostname_ascii: str
    port: int | None
    addresses: list[str]
    decision: UrlTargetDecision


class UrlTargetAllowlistReadError(OSError):
    pass


class UrlTargetBlockedError(ValueError):
    def __init__(self, decision: UrlTargetDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def allowlist_path() -> Path:
    return Path(get_settings().security.url_target_allowlist_path)


def _entry_id(target: str) -> str:
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[:16]


def _normalise_hostname(raw: str, *, require_fqdn: bool = False) -> str:
    host = (raw or "").strip().lower()
    if not host:
        raise ValueError("target must contain a hostname")
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("target must be an IP/CIDR, hostname, or URL with hostname") from exc
    host_ascii = host_ascii.rstrip(".")
    if not host_ascii or len(host_ascii) > 253:
        raise ValueError("target must contain a valid hostname")
    if re.fullmatch(r"\d+(?:\.\d+){3}", host_ascii):
        raise ValueError("target must contain a valid IP address")
    if require_fqdn and "." not in host_ascii:
        raise ValueError("target must be an IP/CIDR or FQDN")
    for label in host_ascii.split("."):
        if not label or len(label) > 63:
            raise ValueError("target must contain a valid hostname")
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("target must contain a valid hostname")
        if not all(char.isalnum() or char == "-" for char in label):
            raise ValueError("target must contain a valid hostname")
    return host_ascii


def _normalise_target(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        raise ValueError("target must not be empty")
    try:
        return str(ipaddress.ip_network(value, strict=True))
    except ValueError:
        pass
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    is_url = "://" in value
    if not is_url and "/" in value and re.match(r"^\d+(?:\.\d+){3}/", value):
        raise ValueError("target must be an IP/CIDR, hostname, or URL with hostname")
    parsed = urlparse(value if is_url else f"//{value}")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("target must contain a valid hostname and port") from exc
    if hostname:
        value = hostname.lower()
        try:
            return str(ipaddress.ip_network(value, strict=True))
        except ValueError:
            pass
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
        return _normalise_hostname(value, require_fqdn=True)
    if is_url:
        raise ValueError("URL target must contain a hostname")
    return _normalise_hostname(value, require_fqdn=True)


def _is_ip_or_network_target(target: str) -> bool:
    try:
        ipaddress.ip_network(target, strict=False)
    except ValueError:
        return False
    return True


def _validate_fqdn_target_resolves(target: str) -> None:
    if _is_ip_or_network_target(target):
        return
    try:
        infos = socket.getaddrinfo(target, None, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError(f"FQDN target must resolve: {exc}") from exc
    if not infos:
        raise ValueError("FQDN target must resolve")


def _read_allowlist_document(path: Path | None = None, *, strict: bool = False) -> dict[str, Any]:
    target_path = path or allowlist_path()
    if not target_path.exists():
        return {"version": 1, "allowed_targets": []}
    try:
        with open(target_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        if strict:
            raise UrlTargetAllowlistReadError(f"Could not read URL target allowlist {target_path}: {exc}") from exc
        return {"version": 1, "allowed_targets": []}
    if raw is None:
        return {"version": 1, "allowed_targets": []}
    if isinstance(raw, list):
        return {"version": 1, "allowed_targets": raw}
    if not isinstance(raw, dict):
        if strict:
            raise UrlTargetAllowlistReadError(f"Invalid URL target allowlist structure in {target_path}")
        return {"version": 1, "allowed_targets": []}
    allowed = raw.get("allowed_targets")
    if not isinstance(allowed, list):
        if strict and "allowed_targets" in raw:
            raise UrlTargetAllowlistReadError(f"Invalid URL target allowlist structure in {target_path}")
        raw["allowed_targets"] = []
    raw.setdefault("version", 1)
    return raw


def list_allowed_url_targets(*, strict: bool = False) -> list[UrlTargetAllowEntry]:
    doc = _read_allowlist_document(strict=strict)
    entries: list[UrlTargetAllowEntry] = []
    for item in doc.get("allowed_targets", []):
        if isinstance(item, str):
            raw_target = item
            reason = ""
            created_by = ""
            created_at = ""
        elif isinstance(item, dict):
            raw_target = str(item.get("target") or "")
            reason = str(item.get("reason") or "")
            created_by = str(item.get("created_by") or "")
            created_at = str(item.get("created_at") or "")
        else:
            continue
        try:
            target = _normalise_target(raw_target)
        except ValueError:
            continue
        entries.append(
            UrlTargetAllowEntry(
                id=_entry_id(target),
                target=target,
                reason=reason,
                created_by=created_by,
                created_at=created_at,
            ),
        )
    return entries


def _write_allowlist(entries: list[UrlTargetAllowEntry]) -> None:
    path = allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "allowed_targets": [
            {
                "target": entry.target,
                **({"reason": entry.reason} if entry.reason else {}),
                **({"created_by": entry.created_by} if entry.created_by else {}),
                **({"created_at": entry.created_at} if entry.created_at else {}),
            }
            for entry in sorted(entries, key=lambda item: item.target)
        ],
    }
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=False, sort_keys=False)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def add_allowed_url_target(target: str, reason: str = "", created_by: str = "") -> UrlTargetAllowEntry:
    normalised = _normalise_target(target)
    _validate_fqdn_target_resolves(normalised)
    entries = [entry for entry in list_allowed_url_targets(strict=True) if entry.target != normalised]
    entry = UrlTargetAllowEntry(
        id=_entry_id(normalised),
        target=normalised,
        reason=reason.strip(),
        created_by=created_by.strip(),
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    entries.append(entry)
    _write_allowlist(entries)
    return entry


def remove_allowed_url_target(target: str) -> bool:
    normalised = _normalise_target(target)
    entries = list_allowed_url_targets(strict=True)
    remaining = [entry for entry in entries if entry.target != normalised]
    if len(remaining) == len(entries):
        return False
    _write_allowlist(remaining)
    return True


def _is_blocked_ip(addr: ipaddress._BaseAddress, *, allow_loopback: bool) -> bool:  # type: ignore[attr-defined]
    if allow_loopback and addr.is_loopback:
        return False
    if addr.is_multicast:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr in _NAT64_WELL_KNOWN_PREFIX:
        embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if embedded.is_multicast:
            return True
        return not embedded.is_global
    return not addr.is_global


def _match_allowlist(hostname: str, addr: ipaddress._BaseAddress | None = None) -> str | None:  # type: ignore[attr-defined]
    host = (hostname or "").strip().lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    for entry in list_allowed_url_targets():
        try:
            network = ipaddress.ip_network(entry.target, strict=False)
        except ValueError:
            if addr is None and host == entry.target:
                return entry.target
            continue
        if addr is not None and addr in network:
            return entry.target
    return None


def evaluate_url_target(
    url: str,
    *,
    require_https: bool = False,
    allow_loopback: bool = False,
) -> UrlTargetDecision:
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return UrlTargetDecision(False, url, "", [], [], f"Invalid URL: {exc}")

    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        scheme_msg = "HTTPS" if require_https else "HTTP/HTTPS"
        try:
            host = parsed.hostname or ""
        except ValueError:
            host = ""
        return UrlTargetDecision(False, url, host, [], [], f"Only {scheme_msg} URLs are allowed")
    try:
        parsed_hostname = parsed.hostname
    except ValueError as exc:
        return UrlTargetDecision(False, url, "", [], [], f"Invalid URL host or port: {exc}")
    if not parsed_hostname:
        return UrlTargetDecision(False, url, "", [], [], "URL has no hostname")

    try:
        try:
            hostname_ascii = str(ipaddress.ip_address(parsed_hostname))
        except ValueError:
            hostname_ascii = _normalise_hostname(parsed_hostname)
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        return UrlTargetDecision(False, url, parsed_hostname, [], [], f"Invalid URL host or port: {exc}")

    if allow_loopback and hostname_ascii.lower() in {"localhost", "localhost.localdomain"}:
        return UrlTargetDecision(True, url, hostname_ascii, ["127.0.0.1"], [], "Loopback target is allowed")

    host_allow_entry = _match_allowlist(hostname_ascii)

    try:
        infos = socket.getaddrinfo(hostname_ascii, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        return UrlTargetDecision(False, url, hostname_ascii, [], [], f"Hostname could not be resolved: {exc}")

    resolved_ips: list[str] = []
    blocked_ips: list[str] = []
    allowlisted_by = host_allow_entry
    for info in infos:
        ip_text = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_text)
        except ValueError:
            blocked_ips.append(ip_text)
            continue
        ip_str = str(addr)
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)
        entry = _match_allowlist(hostname_ascii, addr)
        if entry:
            allowlisted_by = entry
            continue
        if _is_blocked_ip(addr, allow_loopback=allow_loopback):
            blocked_ips.append(ip_str)

    if blocked_ips:
        suggested = blocked_ips[0]
        try:
            suggested = str(ipaddress.ip_network(suggested, strict=False))
        except ValueError:
            pass
        return UrlTargetDecision(
            False,
            url,
            hostname_ascii,
            resolved_ips,
            blocked_ips,
            "URL target resolves to an internal, reserved, or otherwise non-public address. Allowlist it only if this backend is expected to reach that system.",
            suggested_target=suggested,
        )

    if not resolved_ips:
        return UrlTargetDecision(False, url, hostname_ascii, [], [], "Hostname did not resolve to any usable address")

    return UrlTargetDecision(True, url, hostname_ascii, resolved_ips, [], "URL target is allowed", allowlisted_by=allowlisted_by)


def resolve_url_target(
    url: str,
    *,
    require_https: bool = False,
    allow_loopback: bool = False,
) -> ResolvedUrlTarget:
    decision = evaluate_url_target(url, require_https=require_https, allow_loopback=allow_loopback)
    if not decision.allowed:
        raise ValueError(decision.reason)
    parsed = urlparse(url)
    parsed_hostname = parsed.hostname or ""
    try:
        hostname_ascii = str(ipaddress.ip_address(parsed_hostname))
    except ValueError:
        hostname_ascii = _normalise_hostname(parsed_hostname)
    return ResolvedUrlTarget(
        original_url=url,
        scheme=parsed.scheme.lower(),
        hostname=parsed.hostname or "",
        hostname_ascii=hostname_ascii,
        port=parsed.port,
        addresses=decision.resolved_ips,
        decision=decision,
    )


def _build_http_host_header(hostname_ascii: str, scheme: str, port: int | None) -> str:
    host_header = hostname_ascii
    if ":" in host_header and not host_header.startswith("["):
        host_header = f"[{host_header}]"
    if port is not None:
        default_port = 443 if scheme == "https" else 80
        if port != default_port:
            host_header = f"{host_header}:{port}"
    return host_header


def build_pinned_url_targets(
    url: str,
    *,
    require_https: bool = False,
    preserve_userinfo: bool = True,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    decision = evaluate_url_target(url, require_https=require_https)
    if not decision.allowed:
        raise UrlTargetBlockedError(decision)
    addresses = decision.resolved_ips
    if not addresses:
        raise ValueError("Blocked unresolved URL target")

    parsed = urlparse(url)
    hostname_ascii = (parsed.hostname or "").encode("idna").decode("ascii")
    port = parsed.port
    scheme = parsed.scheme.lower()
    auth_prefix = ""
    if preserve_userinfo and parsed.username is not None:
        username = quote(unquote(parsed.username), safe="")
        password = None if parsed.password is None else quote(unquote(parsed.password), safe="")
        auth = username if password is None else f"{username}:{password}"
        auth_prefix = f"{auth}@"

    pinned_urls: list[str] = []
    for pinned_ip in dict.fromkeys(addresses):
        pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = f"{auth_prefix}{pinned_host}:{port}" if port is not None else f"{auth_prefix}{pinned_host}"
        pinned_urls.append(parsed._replace(netloc=netloc).geturl())

    headers = {"Host": _build_http_host_header(hostname_ascii, scheme, port)}
    extensions = {"sni_hostname": hostname_ascii} if scheme == "https" else {}
    return pinned_urls, headers, extensions
