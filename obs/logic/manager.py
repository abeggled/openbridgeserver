"""LogicManager — manages all logic graphs and integrates with the EventBus.

- Subscribes to DataValueEvents
- Triggers graphs whose datapoint_read nodes watch the changed DataPoint
- Executes the graph and writes outputs back via the registry
- Schedules timer_cron nodes via asyncio tasks (requires croniter)
"""

from __future__ import annotations

import asyncio
import base64
import copy
import email.utils
import http.cookies
import ipaddress
import json
import logging
import os
import re
import socket
import stat
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

import httpx

from obs.logic.executor import GraphExecutor
from obs.logic.models import FlowData
from obs.security.url_targets import resolve_url_target

logger = logging.getLogger(__name__)


def _msg_to_str(v: object) -> str:
    """Convert any node output value to a message string.

    Uses explicit None-check rather than truthiness so that falsy values
    (0, False, 0.0, "") are preserved as their string representation instead
    of being silently replaced by a fallback.
    """
    import json as _j  # noqa: PLC0415

    if isinstance(v, (dict, list)):
        return _j.dumps(v, ensure_ascii=False)
    return str(v)


_THROTTLE_UNITS: dict[str, float] = {
    "ms": 1.0,
    "s": 1000.0,
    "min": 60_000.0,
    "h": 3_600_000.0,
}
_MAX_LOGIC_CASCADE_DEPTH = 10
_MAX_SEQUENCE_REPEAT_COUNT = 10_000

# Node types the side-effect-free initialization pass (initialize_graph) must
# not publish through: async/action nodes are not executed there, so their
# executor outputs are placeholders (e.g. api_client.success=False, timers
# and missing_node return {}); per-sample accumulators run on a throwaway
# state copy, so their outputs would include the seed while the persisted
# state stays untouched; random_value generates a fresh value on every
# evaluation, so a save would publish a new random actuator value; memory
# evaluates with commit_memory=False, so its output is the uncommitted
# previous/default value, not the seeded input; ical outputs come from the
# fetch cache, which may still be empty right after a save.
_INIT_EXCLUDED_NODE_TYPES = frozenset(
    {
        "api_client",
        "host_check",
        "notify_pushover",
        "notify_sms",
        "message_archive",
        "wake_on_lan",
        "value_sequence",
        "timer_delay",
        "timer_pulse",
        "timer_cron",
        "ical",
        "missing_node",
        "python_script",
        "statistics",
        "avg_multi",
        "min_max_tracker",
        "consumption_counter",
        "heating_circuit",
        "random_value",
        "memory",
    }
)

# Deterministic two-state nodes whose init-pass state IS committed when they
# sit on a clean seeded path: their output is published, so the persisted
# state must switch with it or the next real value inside the dead band would
# flip the output back to the stale pre-save state.
_INIT_COMMIT_STATE_NODE_TYPES = frozenset({"gate", "hysteresis"})

# Input handles that control WHEN a node's output fires/passes but do not
# deliver the value itself. Seeded eligibility must not propagate through
# them: a Const → Gate.in → Write.value sheet whose Read Object only drives
# Gate.enable (or Write.trigger) would otherwise publish the constant on
# save even though the written value does not descend from the seed.
_INIT_CONTROL_INPUT_HANDLES: dict[str, frozenset[str]] = {
    "datapoint_write": frozenset({"trigger"}),
    "gate": frozenset({"enable"}),
}


def _downstream_closure(start: set[str], edges: list[Any]) -> set[str]:
    """Node ids reachable from *start* (inclusive) following edges forward."""
    reached = set(start)
    grew = True
    while grew:
        grew = False
        for edge in edges:
            if edge.source in reached and edge.target not in reached:
                reached.add(edge.target)
                grew = True
    return reached


_ICAL_MAX_BYTES = 1_048_576
_ICAL_MAX_REDIRECTS = 5
_ICAL_ALLOWED_CONTENT_TYPES = ("text/calendar", "application/ics", "application/octet-stream", "text/plain")
_PUSHOVER_ATTACHMENT_MAX_BYTES = 5_000_000
_SECRET_FILE_MAX_BYTES = 8192
_SECRET_FILE_DEFAULT_ROOT = "/run/secrets"
_API_CLIENT_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
_API_CLIENT_VARIABLE_RE = re.compile(r"###OBS([1-9][0-9]*)###")
_API_CLIENT_URL_LEADING_STRIP_CHARS = "".join(chr(value) for value in range(0x21))
_API_CLIENT_URL_REMOVE_CHARS = str.maketrans("", "", "\r\n\t")
_HOST_CHECK_MIN_TIMEOUT_S = 1.0
_HOST_CHECK_MAX_TIMEOUT_S = 30.0
_HOST_CHECK_MIN_COUNT = 1
_HOST_CHECK_MAX_COUNT = 10
_HOST_CHECK_RUNTIME_TOKEN = uuid.uuid4().hex


class _ApiClientVariableError(ValueError):
    pass


def _secret_file_root() -> Path:
    return Path(os.environ.get("OBS_SECRET_FILE_DIR", _SECRET_FILE_DEFAULT_ROOT)).resolve()


def _read_secret_file(path: str) -> str:
    secret_path_raw = (path or "").strip()
    if not secret_path_raw:
        return ""

    try:
        secret_root = _secret_file_root()
        secret_path = Path(secret_path_raw).resolve(strict=True)
        if not secret_path.is_relative_to(secret_root):
            logger.warning("Refusing to read secret file outside %s: %s", secret_root, secret_path)
            return ""

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(secret_path, flags)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                logger.warning("Refusing to read non-regular secret file: %s", secret_path)
                return ""
            if file_stat.st_size > _SECRET_FILE_MAX_BYTES:
                logger.warning("Refusing to read oversized secret file: %s", secret_path)
                return ""
            data = os.read(fd, _SECRET_FILE_MAX_BYTES + 1)
        finally:
            os.close(fd)

        if len(data) > _SECRET_FILE_MAX_BYTES:
            logger.warning("Refusing to read oversized secret file: %s", secret_path)
            return ""
        return data.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("Could not read secret file %s: %s", secret_path_raw, exc)
        return ""


def _normalise_api_client_variables(raw: Any) -> dict[int, dict[str, str]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return {}

    variables: dict[int, dict[str, str]] = {}
    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        slot_raw = entry.get("slot", idx)
        try:
            slot = int(slot_raw)
        except (TypeError, ValueError):
            slot = idx
        if slot < 1:
            slot = idx
        datapoint_id = str(entry.get("datapoint_id") or "").strip()
        if not datapoint_id:
            continue
        variables[slot] = {
            "datapoint_id": datapoint_id,
            "datapoint_name": str(entry.get("datapoint_name") or datapoint_id),
        }
    return variables


def _rename_api_client_variable_datapoint_names(raw: Any, datapoint_id: str, new_name: str) -> tuple[Any, bool]:
    was_string = isinstance(raw, str)
    variables = raw
    if was_string:
        try:
            variables = json.loads(raw)
        except Exception:
            return raw, False
    if not isinstance(variables, list):
        return raw, False

    changed = False
    for variable in variables:
        if not isinstance(variable, dict):
            continue
        if variable.get("datapoint_id") == datapoint_id and variable.get("datapoint_name") != new_name:
            variable["datapoint_name"] = new_name
            changed = True
    if not changed:
        return raw, False
    if was_string:
        return json.dumps(variables, ensure_ascii=False), True
    return variables, True


def _api_client_value_to_string(value: Any) -> str:
    if value is None:
        raise _ApiClientVariableError("API client variable value is empty")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _replace_api_client_placeholders(
    value: Any,
    resolver: Any,
    transform: Any | None = None,
) -> Any:
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            replacement = resolver(int(match.group(1)))
            return transform(replacement) if transform is not None else replacement

        return _API_CLIENT_VARIABLE_RE.sub(_replace, value)
    if isinstance(value, list):
        return [_replace_api_client_placeholders(item, resolver, transform) for item in value]
    if isinstance(value, dict):
        return {
            _replace_api_client_placeholders(key, resolver, transform): _replace_api_client_placeholders(item, resolver, transform)
            for key, item in value.items()
        }
    return value


def _quote_api_client_url_value(value: str) -> str:
    return quote(value, safe="-._~")


def _normalise_api_client_url_for_parse(value: str) -> str:
    # The API-client call sites apply Python ``str.strip()`` to the resolved URL,
    # which removes Unicode whitespace (e.g. U+00A0) on top of the C0 controls and
    # ASCII space that ``urlparse`` itself trims. Mirror both here so the authority
    # bounds are computed against the same leading run that is silently removed
    # later; otherwise a leading Unicode-whitespace (or interleaved control /
    # whitespace) prefix would hide the scheme and let a variable choose the host.
    previous = None
    while value != previous:
        previous = value
        value = value.lstrip(_API_CLIENT_URL_LEADING_STRIP_CHARS).lstrip()
    return value.translate(_API_CLIENT_URL_REMOVE_CHARS)


def _replace_api_client_url_placeholders(value: str, resolver: Any) -> str:
    value = _normalise_api_client_url_for_parse(value)
    authority_bounds: tuple[int, int] | None = None
    scheme_separator = value.find("://")
    if scheme_separator != -1 and _API_CLIENT_VARIABLE_RE.search(value[:scheme_separator]):
        raise _ApiClientVariableError(
            "API client URL variables are not allowed in the scheme, host, userinfo, or port",
        )
    # Reject templates where removing placeholders would expose a :// that is hidden in the
    # raw template (e.g. "http:###OBS1###//attacker.com" collapses to "http://attacker.com"
    # when the variable resolves to an empty string).
    if scheme_separator == -1 and _API_CLIENT_VARIABLE_RE.sub("", value).find("://") != -1:
        raise _ApiClientVariableError(
            "API client URL variables are not allowed in the scheme, host, userinfo, or port",
        )
    scheme_match = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value)
    if scheme_match is not None:
        separator_scan_value = _API_CLIENT_VARIABLE_RE.sub(lambda match: "X" * (match.end() - match.start()), value)
        authority_start = scheme_match.end()
        authority_end = len(value)
        for separator in "/?#":
            separator_index = separator_scan_value.find(separator, authority_start)
            if separator_index != -1:
                authority_end = min(authority_end, separator_index)
        authority_bounds = (authority_start, authority_end)

    def _replace(match: re.Match[str]) -> str:
        if authority_bounds is not None and authority_bounds[0] <= match.start() < authority_bounds[1]:
            raise _ApiClientVariableError(
                "API client URL variables are not allowed in the scheme, host, userinfo, or port",
            )
        replacement = resolver(int(match.group(1)))
        return _quote_api_client_url_value(replacement)

    return _API_CLIENT_VARIABLE_RE.sub(_replace, value)


def _make_api_client_variable_resolver(
    registry: Any,
    raw_variables: Any,
    execution_values_by_datapoint_id: dict[str, Any] | None = None,
) -> Any:
    variables = _normalise_api_client_variables(raw_variables)
    execution_values_by_datapoint_id = execution_values_by_datapoint_id or {}
    cache: dict[int, str] = {}

    def _resolve(index: int) -> str:
        if index in cache:
            return cache[index]
        variable = variables.get(index)
        if variable is None:
            raise _ApiClientVariableError(f"API client variable OBS{index} is not configured")
        datapoint_id = variable["datapoint_id"]
        if datapoint_id in execution_values_by_datapoint_id:
            value = execution_values_by_datapoint_id[datapoint_id]
            if value is None:
                raise _ApiClientVariableError(
                    f"API client variable OBS{index} object {variable['datapoint_name']} has no value",
                )
            cache[index] = _api_client_value_to_string(value)
            return cache[index]
        try:
            state = registry.get_value(uuid.UUID(datapoint_id))
        except Exception as exc:
            raise _ApiClientVariableError(f"API client variable OBS{index} references an invalid object") from exc
        if state is None:
            raise _ApiClientVariableError(
                f"API client variable OBS{index} object {variable['datapoint_name']} is not available",
            )
        if state.value is None:
            raise _ApiClientVariableError(
                f"API client variable OBS{index} object {variable['datapoint_name']} has no value",
            )
        cache[index] = _api_client_value_to_string(state.value)
        return cache[index]

    return _resolve


def _parse_http_url(url: str) -> Any | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname:
        return None
    return parsed


async def _resolve_safe_image_url(url: str) -> tuple[str, str, str] | None:
    """Return a DNS-pinned HTTPS request tuple for safe image downloads.

    Returns:
        (pinned_url, host_header, pinned_ip) or None if the URL is unsafe.
    """
    try:
        target = await asyncio.to_thread(resolve_url_target, url, require_https=True)
    except ValueError:
        return None
    if not target.addresses:
        return None

    parsed = urlparse(url)
    port = target.port or 443
    pinned_ip = target.addresses[0]
    pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    has_explicit_port = target.port is not None
    netloc = f"{pinned_host}:{port}" if has_explicit_port else pinned_host
    pinned_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    host_header = f"{target.hostname_ascii}:{port}" if has_explicit_port else target.hostname_ascii
    return pinned_url, host_header, pinned_ip


def _origin_tuple(parsed: Any) -> tuple[str, str, int] | None:
    if not parsed or not parsed.hostname or parsed.scheme not in {"http", "https"}:
        return None
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, hostname_ascii, port


def _preserve_same_origin_credentials(current_url: str, redirected_url: str) -> str:
    current_parsed = _parse_http_url(current_url)
    redirected_parsed = _parse_http_url(redirected_url)
    if not current_parsed or not redirected_parsed:
        return redirected_url
    if redirected_parsed.username is not None:
        return redirected_url
    if _origin_tuple(current_parsed) != _origin_tuple(redirected_parsed):
        return redirected_url
    if current_parsed.username is None:
        return redirected_url

    username = quote(unquote(current_parsed.username), safe="")
    password = None if current_parsed.password is None else quote(unquote(current_parsed.password), safe="")
    hostname = redirected_parsed.hostname
    if not hostname:
        return redirected_url
    try:
        host_for_netloc = hostname.encode("idna").decode("ascii")
        ip = ipaddress.ip_address(host_for_netloc)
        if isinstance(ip, ipaddress.IPv6Address):
            host_for_netloc = f"[{host_for_netloc}]"
    except UnicodeError:
        return redirected_url
    except ValueError:
        pass
    try:
        port = redirected_parsed.port
    except ValueError:
        return redirected_url

    auth = username if password is None else f"{username}:{password}"
    netloc = f"{auth}@{host_for_netloc}"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return redirected_parsed._replace(netloc=netloc).geturl()


def _build_http_host_header(hostname_ascii: str, scheme: str, port: int | None) -> str:
    host_header = hostname_ascii
    if ":" in host_header and not host_header.startswith("["):
        host_header = f"[{host_header}]"
    if port is not None:
        default_port = 443 if scheme == "https" else 80
        if port != default_port:
            host_header = f"{host_header}:{port}"
    return host_header


def _build_api_client_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    parsed = _parse_http_url(url)
    if not parsed:
        raise ValueError("Invalid URL target")
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Invalid URL target") from None
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid URL target") from None

    try:
        target = resolve_url_target(url)
    except ValueError as exc:
        raise ValueError(f"Blocked URL target: {exc}") from exc
    addresses = target.addresses
    if not addresses:
        raise ValueError("Blocked unresolved URL target")

    auth_prefix = ""
    if parsed.username is not None:
        username = quote(unquote(parsed.username), safe="")
        password = None if parsed.password is None else quote(unquote(parsed.password), safe="")
        auth = username if password is None else f"{username}:{password}"
        auth_prefix = f"{auth}@"

    pinned_urls: list[str] = []
    for pinned_ip in dict.fromkeys(addresses):
        pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = f"{auth_prefix}{pinned_host}:{port}" if port is not None else f"{auth_prefix}{pinned_host}"
        pinned_urls.append(parsed._replace(netloc=netloc).geturl())
    headers = {"Host": _build_http_host_header(hostname_ascii, parsed.scheme, port)}
    extensions = {"sni_hostname": hostname_ascii} if parsed.scheme == "https" else {}
    return pinned_urls, headers, extensions


def _cookie_domain_matches(hostname: str, cookie_domain: str) -> bool:
    host = hostname.lower()
    domain = cookie_domain.lower().lstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _cookie_path_matches(request_path: str, cookie_path: str) -> bool:
    req = request_path or "/"
    path = cookie_path or "/"
    if not req.startswith("/"):
        req = f"/{req}"
    if not path.startswith("/"):
        path = f"/{path}"
    if req == path:
        return True
    if not req.startswith(path):
        return False
    if path.endswith("/"):
        return True
    return len(req) > len(path) and req[len(path)] == "/"


def _default_cookie_path(request_path: str) -> str:
    path = request_path or "/"
    if not path.startswith("/"):
        return "/"
    if path.count("/") <= 1:
        return "/"
    return path.rsplit("/", 1)[0] or "/"


def _store_response_cookies(
    cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]],
    set_cookie_headers: list[str],
    logical_url: str,
) -> None:
    parsed = _parse_http_url(logical_url)
    if not parsed or not parsed.hostname:
        return
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    default_path = _default_cookie_path(parsed.path or "/")
    for raw in set_cookie_headers:
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            continue
        for morsel in jar.values():
            name = morsel.key
            value = morsel.value
            raw_domain = (morsel["domain"] or "").strip().lower()
            host_only = raw_domain == ""
            domain = hostname if host_only else raw_domain.lstrip(".")
            if not _cookie_domain_matches(hostname, domain):
                continue
            path = (morsel["path"] or default_path).strip() or "/"
            if not path.startswith("/"):
                path = f"/{path}"
            max_age = (morsel["max-age"] or "").strip()
            expires = (morsel["expires"] or "").strip()
            delete_cookie = False
            if max_age:
                try:
                    delete_cookie = int(max_age) <= 0
                except ValueError:
                    pass
            if not delete_cookie and expires:
                try:
                    exp_dt = email.utils.parsedate_to_datetime(expires)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    delete_cookie = exp_dt <= datetime.now(UTC)
                except Exception:
                    pass
            key = (domain, path, name, host_only)
            if delete_cookie:
                cookie_store.pop(key, None)
                continue
            secure = bool(morsel["secure"])
            cookie_store[key] = (value, secure)


def _build_cookie_header(cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]], logical_url: str) -> str:
    parsed = _parse_http_url(logical_url)
    if not parsed or not parsed.hostname:
        return ""
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    req_path = parsed.path or "/"
    is_https_request = parsed.scheme.lower() == "https"
    matched: list[tuple[str, str]] = []
    for (domain, path, name, host_only), (value, secure) in cookie_store.items():
        if not _should_send_cookie(
            req_hostname=hostname,
            req_path=req_path,
            req_is_https=is_https_request,
            cookie_domain=domain,
            cookie_path=path,
            cookie_host_only=host_only,
            cookie_secure=secure,
        ):
            continue
        cookie_pair = (name, value)
        matched.append(cookie_pair)
    return "; ".join(f"{name}={value}" for name, value in matched)


def _should_send_cookie(
    req_hostname: str,
    req_path: str,
    req_is_https: bool,
    cookie_domain: str,
    cookie_path: str,
    cookie_host_only: bool,
    cookie_secure: bool,
) -> bool:
    if cookie_host_only and req_hostname != cookie_domain:
        return False
    if not cookie_host_only and not _cookie_domain_matches(req_hostname, cookie_domain):
        return False
    if not _cookie_path_matches(req_path, cookie_path):
        return False
    if bool(cookie_secure) and not req_is_https:
        return False
    return True


def _send_wol_packet(mac: str, broadcast: str, port: int) -> None:
    """Build and send a Wake-on-LAN magic packet via UDP broadcast."""
    clean = re.sub(r"[:\-\.]", "", mac).upper()
    if len(clean) != 12 or not re.fullmatch(r"[0-9A-F]{12}", clean):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    mac_bytes = bytes.fromhex(clean)
    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, (broadcast, port))


def _normalise_host_check_ping_config(timeout_s_raw: Any, count_raw: Any) -> tuple[float, int]:
    try:
        timeout_s = float(timeout_s_raw or _HOST_CHECK_MIN_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = _HOST_CHECK_MIN_TIMEOUT_S
    try:
        count = int(count_raw or _HOST_CHECK_MIN_COUNT)
    except (TypeError, ValueError):
        count = _HOST_CHECK_MIN_COUNT
    timeout_s = min(_HOST_CHECK_MAX_TIMEOUT_S, max(_HOST_CHECK_MIN_TIMEOUT_S, timeout_s))
    count = min(_HOST_CHECK_MAX_COUNT, max(_HOST_CHECK_MIN_COUNT, count))
    return timeout_s, count


async def _ping_host(host: str, count: int, timeout_s: float) -> tuple[bool, float | None]:
    """Ping *host* and return (reachable, latency_ms).

    Uses the system ping binary so no elevated privileges are required.
    timeout_s is passed to ping as the per-packet deadline; an additional
    2-second asyncio safety timeout is layered on top to handle hangs.
    """
    import sys  # noqa: PLC0415

    timeout_s, count = _normalise_host_check_ping_config(timeout_s, count)
    timeout_int = int(timeout_s)
    if sys.platform == "darwin":
        cmd = ["ping", "-c", str(count), "-W", str(timeout_int * 1000), "--", host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_int), "--", host]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s * count + 2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, None
        reachable = proc.returncode == 0
        latency_ms: float | None = None
        if reachable:
            m = re.search(r"time[<=](\d+(?:\.\d+)?)\s*ms", stdout.decode(errors="replace"))
            if m:
                latency_ms = float(m.group(1))
        return reachable, latency_ms
    except FileNotFoundError:
        logger.warning("ping binary not found — install iputils-ping to enable Host Check")
        return False, None
    except Exception:
        return False, None


def _build_ical_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    parsed = _parse_http_url(url)
    if not parsed:
        raise ValueError(f"Invalid iCal URL: {url}")
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"Invalid iCal URL host: {url}") from None
    try:
        port = parsed.port
    except ValueError:
        raise ValueError(f"Invalid iCal URL port: {url}") from None
    try:
        target = resolve_url_target(url)
    except ValueError as exc:
        raise ValueError(f"Blocked iCal URL target: {url}") from exc
    addresses = target.addresses
    if not addresses:
        raise ValueError(f"Blocked unresolved iCal URL target: {url}")
    headers = {"Host": _build_http_host_header(hostname_ascii, parsed.scheme, port)}
    if parsed.username is not None:
        username = unquote(parsed.username)
        password = unquote(parsed.password or "")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    extensions = {"sni_hostname": hostname_ascii} if parsed.scheme == "https" else {}
    fetch_urls: list[str] = []
    for resolved_ip in addresses:
        resolved_ip_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
        if port is not None:
            netloc = f"{resolved_ip_for_url}:{port}"
        else:
            netloc = resolved_ip_for_url
        fetch_urls.append(parsed._replace(netloc=netloc).geturl())
    return fetch_urls, headers, extensions


def _build_ical_fetch_target(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    fetch_urls, headers, extensions = _build_ical_fetch_targets(url)
    return fetch_urls[0], headers, extensions


def _is_public_http_url(url: str) -> bool:
    try:
        _build_ical_fetch_targets(url)
    except ValueError:
        return False
    return True


async def _read_limited_response_body(resp: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError(f"iCal response too large: {len(body)} bytes")
    return bytes(body)


_manager: LogicManager | None = None


def get_logic_manager() -> LogicManager:
    if _manager is None:
        raise RuntimeError("LogicManager not initialised")
    return _manager


def init_logic_manager(db: Any, event_bus: Any, registry: Any) -> LogicManager:
    global _manager
    _manager = LogicManager(db, event_bus, registry)
    return _manager


class LogicManager:
    def __init__(self, db: Any, event_bus: Any, registry: Any):
        self._db = db
        self._event_bus = event_bus
        self._registry = registry
        # persistent state per graph per node (hysteresis bool, statistics accumulators, …)
        self._hysteresis: dict[str, dict[str, Any]] = {}
        # graph cache: id → (name, enabled, FlowData)
        self._graphs: dict[str, tuple[str, bool, FlowData]] = {}
        # per-node runtime state for filter/throttle
        # {graph_id: {node_id: {last_value, last_ts, last_write_val, last_write_ts}}}
        self._node_state: dict[str, dict[str, dict[str, Any]]] = {}
        # graphs whose initialize_graph publish is in flight, mapped to the
        # DataPoint ids that pass is writing — only those self-originating
        # events must not re-enter the graph (see _on_value_event)
        self._initializing_graphs: dict[str, set[str]] = {}
        # graphs still awaiting their turn in a bulk initialization pass
        # (config restore) — cascaded logic writes must not double-run them
        self._bulk_init_pending: set[str] = set()
        # cron tasks: (graph_id, node_id) → asyncio.Task
        self._cron_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
        # Running value sequences, keyed per graph/node.  They are deliberately
        # separate from cron tasks because they are short-lived and user-triggered.
        self._sequence_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
        self._sequence_conditions: dict[tuple[str, str], bool] = {}
        self._sequence_queues: dict[tuple[str, str], int] = {}
        self._sequence_queue_depths: dict[tuple[str, str], int] = {}
        self._sequence_configs: dict[tuple[str, str], dict[str, Any]] = {}
        self._sequence_graph_signatures: dict[str, str] = {}
        self._sequence_restarts: set[tuple[str, str]] = set()
        self._sequence_restart_sources: dict[tuple[str, str], asyncio.Task] = {}
        # application-level config (e.g. timezone) — loaded from app_settings table
        self._app_config: dict[str, Any] = {"timezone": "Europe/Zurich"}

    async def start(self) -> None:
        """Subscribe to EventBus, load all graphs and start cron schedulers."""
        await self._load_app_config()
        await self._load_graphs()
        from obs.core.event_bus import DataPointRenamedEvent, DataValueEvent

        self._event_bus.subscribe(DataValueEvent, self._on_value_event)
        self._event_bus.subscribe(DataPointRenamedEvent, self._on_datapoint_renamed)
        self._start_cron_tasks()
        logger.info("LogicManager started — %d graphs loaded", len(self._graphs))

    async def stop(self) -> None:
        from obs.core.event_bus import DataPointRenamedEvent, DataValueEvent

        self._event_bus.unsubscribe(DataValueEvent, self._on_value_event)
        self._event_bus.unsubscribe(DataPointRenamedEvent, self._on_datapoint_renamed)
        for task in list(self._cron_tasks.values()):
            task.cancel()
        self._cron_tasks.clear()
        self._cancel_sequence_tasks()

    async def reload(self) -> None:
        """Reload graph cache from DB and restart cron schedulers."""
        for task in list(self._cron_tasks.values()):
            task.cancel()
        self._cron_tasks.clear()
        await self._load_graphs()
        # A config import/reset can remove graphs without first calling
        # invalidate_cache().  Cancel only sequences whose graph no longer
        # exists or is disabled; unrelated live graphs keep running.
        for graph_id, node_id in list(self._sequence_tasks):
            entry = self._graphs.get(graph_id)
            node = next((node for node in entry[2].nodes if node.id == node_id), None) if entry else None
            if (
                entry is None
                or not entry[1]
                or node is None
                or node.type != "value_sequence"
                or node.data != self._sequence_configs.get((graph_id, node_id))
                or entry[2].model_dump_json() != self._sequence_graph_signatures.get(graph_id)
            ):
                self._cancel_sequence_tasks(graph_id)
        self._start_cron_tasks()

    # ── App Config ────────────────────────────────────────────────────────

    def _cancel_sequence_tasks(self, graph_id: str | None = None) -> None:
        """Cancel active sequences, for shutdown/reload/delete semantics."""
        keys = [key for key in self._sequence_tasks if graph_id is None or key[0] == graph_id]
        for key in keys:
            self._cancel_sequence_task(key)
            self._sequence_conditions.pop(key, None)
            self._sequence_queues.pop(key, None)
            self._sequence_queue_depths.pop(key, None)
            self._sequence_configs.pop(key, None)

    def _cancel_sequence_task(self, key: tuple[str, str]) -> None:
        """Cancel a tracked task and the source it may be restarting."""
        task = self._sequence_tasks.pop(key, None)
        source = self._sequence_restart_sources.pop(key, None)
        self._sequence_restarts.discard(key)
        self._sequence_queues.pop(key, None)
        self._sequence_queue_depths.pop(key, None)
        if task:
            task.cancel()
        if source and source is not task:
            source.cancel()

    @staticmethod
    def _sequence_steps(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = []
        return [step for step in raw if isinstance(step, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _coerce_sequence_value(value: Any, data_type: str) -> Any:
        if data_type == "BOOLEAN":
            if not isinstance(value, str):
                return bool(value)
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"invalid boolean value {value!r}")
        if data_type == "INTEGER":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"fractional integer value {value!r}")
            return int(value)
        if data_type == "FLOAT":
            return float(value)
        if data_type == "DATE":
            return date.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "TIME":
            return time.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "DATETIME":
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "STRING":
            return str(value)
        return value

    async def _run_value_sequence(self, graph_id: str, node_id: str, config: dict[str, Any], logic_depth: int = 0) -> None:
        """Publish configured writes without blocking the graph executor."""
        from obs.core.event_bus import DataValueEvent

        key = (graph_id, node_id)
        steps = self._sequence_steps(config.get("steps"))
        if not steps:
            logger.warning("Value sequence graph=%s node=%s has no steps", graph_id[:8], node_id[:8])
            return
        mode = config.get("run_mode", "once")
        if mode == "while_condition" and not self._sequence_conditions.get(key, True):
            return
        try:
            raw_repeat_count = config.get("repeat_count", 2)
            repetitions = (
                min(_MAX_SEQUENCE_REPEAT_COUNT, max(1, int(2 if raw_repeat_count is None else raw_repeat_count))) if mode == "repeat_count" else 1
            )
        except (TypeError, ValueError):
            repetitions = 1
        try:
            while True:
                slept = False
                for step in steps:
                    if (mode == "while_condition" or config.get("cancel_when_condition_false")) and not self._sequence_conditions.get(key, True):
                        logger.info("Value sequence cancelled: graph=%s node=%s", graph_id[:8], node_id[:8])
                        return
                    target = str(step.get("datapoint_id") or "").strip()
                    if target:
                        try:
                            datapoint_id = uuid.UUID(target)
                            target_dp = self._registry.get(datapoint_id)
                            if target_dp is None:
                                raise ValueError("target object no longer exists")
                            publish_task = asyncio.create_task(
                                self._event_bus.publish(
                                    DataValueEvent(
                                        datapoint_id=datapoint_id,
                                        value=self._coerce_sequence_value(step.get("value"), target_dp.data_type),
                                        quality="good",
                                        source_adapter="logic_sequence",
                                        logic_depth=logic_depth + 1,
                                    )
                                )
                            )
                            try:
                                await asyncio.shield(publish_task)
                            except asyncio.CancelledError:
                                # A write can synchronously re-run this graph
                                # and cancel its own sequence.  Complete the
                                # already-emitted event before stopping so all
                                # EventBus subscribers see the write.
                                try:
                                    await asyncio.shield(publish_task)
                                except Exception:
                                    pass
                                raise
                        except Exception as exc:
                            logger.warning("Value sequence graph=%s node=%s target=%s failed: %s", graph_id[:8], node_id[:8], target, exc)
                    try:
                        delay_s = max(0.0, float(step.get("delay_ms") or 0) / 1000)
                    except (TypeError, ValueError):
                        delay_s = 0.0
                    if delay_s:
                        await asyncio.sleep(delay_s)
                        slept = True
                if mode == "while_condition":
                    if not self._sequence_conditions.get(key, True):
                        return
                    if not slept:
                        logger.warning("Value sequence graph=%s node=%s needs a positive pause in while mode", graph_id[:8], node_id[:8])
                        return
                    continue
                repetitions -= 1
                if repetitions <= 0:
                    return
                if mode == "repeat_count" and not slept:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        finally:
            if self._sequence_tasks.get(key) is asyncio.current_task():
                self._sequence_tasks.pop(key, None)
                queued = self._sequence_queues.pop(key, 0)
                queued_depth = self._sequence_queue_depths.pop(key, logic_depth)
                if queued and self._sequence_conditions.get(key, True):
                    if queued > 1:
                        self._sequence_queues[key] = queued - 1
                        self._sequence_queue_depths[key] = queued_depth
                    task = asyncio.create_task(
                        self._run_value_sequence(graph_id, node_id, config, queued_depth),
                        name=f"sequence-{graph_id[:8]}-{node_id[:8]}",
                    )
                    self._sequence_tasks[key] = task

    async def _restart_value_sequence(self, graph_id: str, node_id: str, config: dict[str, Any], logic_depth: int, active: asyncio.Task) -> None:
        """Stop a sequence completely before launching its restart replacement."""
        key = (graph_id, node_id)
        try:
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass
            if self._sequence_tasks.get(key) is not asyncio.current_task():
                return
            task = asyncio.create_task(
                self._run_value_sequence(graph_id, node_id, config, logic_depth),
                name=f"sequence-{graph_id[:8]}-{node_id[:8]}",
            )
            self._sequence_tasks[key] = task
        finally:
            self._sequence_restarts.discard(key)
            if self._sequence_restart_sources.get(key) is active:
                self._sequence_restart_sources.pop(key, None)

    def _start_value_sequence(self, graph_id: str, node: Any, condition: bool, logic_depth: int = 0, graph_signature: str = "") -> None:
        key = (graph_id, node.id)
        self._sequence_conditions[key] = condition
        self._sequence_configs[key] = dict(node.data)
        self._sequence_graph_signatures[graph_id] = graph_signature
        active = self._sequence_tasks.get(key)
        if active and not active.done():
            policy = node.data.get("restart_policy", "ignore")
            if policy == "restart":
                # The task slot holds the restart helper while it awaits the
                # original task.  Coalesce rapid retriggers so they cannot
                # cancel the helper and detach from that original publish.
                if key in self._sequence_restarts:
                    return
                self._sequence_restarts.add(key)
                self._sequence_restart_sources[key] = active
                restart = asyncio.create_task(
                    self._restart_value_sequence(graph_id, node.id, dict(node.data), logic_depth, active),
                    name=f"sequence-restart-{graph_id[:8]}-{node.id[:8]}",
                )
                self._sequence_tasks[key] = restart
                return
            elif policy == "queue":
                self._sequence_queues[key] = self._sequence_queues.get(key, 0) + 1
                self._sequence_queue_depths[key] = max(self._sequence_queue_depths.get(key, 0), logic_depth)
                return
            else:
                return
        task = asyncio.create_task(
            self._run_value_sequence(graph_id, node.id, dict(node.data), logic_depth),
            name=f"sequence-{graph_id[:8]}-{node.id[:8]}",
        )
        self._sequence_tasks[key] = task

    async def _load_app_config(self) -> None:
        """Load app-level settings (e.g. timezone) from the database."""
        try:
            rows = await self._db.fetchall("SELECT key, value FROM app_settings")
            for row in rows:
                self._app_config[row["key"]] = row["value"]
            logger.debug("LogicManager: app_config loaded: %s", self._app_config)
        except Exception as exc:
            logger.warning("LogicManager: could not load app_settings: %s", exc)

    def update_app_config(self, config: dict[str, Any]) -> None:
        """Hot-update app config (called by settings API on PUT /system/settings)."""
        self._app_config.update(config)
        logger.info("LogicManager: app_config updated: %s", config)

    # ── Cron Scheduler ────────────────────────────────────────────────────

    def _start_cron_tasks(self) -> None:
        """Start asyncio tasks for all timer_cron and ical nodes in enabled graphs."""
        _has_croniter = True
        try:
            import croniter as _croniter_check  # noqa: F401
        except ImportError:
            logger.warning("croniter not installed — timer_cron nodes will not auto-execute. Install with: pip install croniter")
            _has_croniter = False

        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            name, enabled, flow = entry
            if not enabled:
                continue
            for node in flow.nodes:
                if node.type == "timer_cron":
                    if not _has_croniter:
                        continue
                    key = (graph_id, node.id)
                    if key in self._cron_tasks and not self._cron_tasks[key].done():
                        continue  # already running
                    cron_expr = node.data.get("cron", "0 7 * * *")
                    task = asyncio.create_task(
                        self._cron_loop(graph_id, node.id, cron_expr),
                        name=f"cron-{graph_id[:8]}-{node.id[:8]}",
                    )
                    self._cron_tasks[key] = task
                    logger.info(
                        "Cron scheduled: graph=%s (%s) node=%s expr=%r",
                        graph_id[:8],
                        name,
                        node.id[:8],
                        cron_expr,
                    )
                elif node.type == "ical":
                    key = (graph_id, node.id)
                    if key in self._cron_tasks and not self._cron_tasks[key].done():
                        continue  # already running
                    refresh_min = max(1.0, float(node.data.get("refresh_interval_min") or 60))
                    task = asyncio.create_task(
                        self._ical_loop(graph_id, node.id, refresh_min),
                        name=f"ical-{graph_id[:8]}-{node.id[:8]}",
                    )
                    self._cron_tasks[key] = task
                    logger.info(
                        "iCal scheduled: graph=%s (%s) node=%s interval=%.0fmin",
                        graph_id[:8],
                        name,
                        node.id[:8],
                        refresh_min,
                    )

    async def _cron_loop(self, graph_id: str, node_id: str, cron_expr: str) -> None:
        """Fires a timer_cron graph node on its cron schedule — runs indefinitely."""
        from croniter import croniter

        while True:
            try:
                now = datetime.now(UTC)
                it = croniter(cron_expr, now)
                next_dt = it.get_next(datetime)
                wait_s = max(0.0, (next_dt - now).total_seconds())
                logger.debug(
                    "Cron graph %s: sleeping %.0fs until %s",
                    graph_id[:8],
                    wait_s,
                    next_dt.isoformat(),
                )
                await asyncio.sleep(wait_s)

                entry = self._graphs.get(graph_id)
                if entry and entry[1]:  # still exists and enabled
                    g_name, _, flow = entry
                    overrides = {node_id: {"trigger": True}}
                    await self._execute_graph(graph_id, g_name, flow, overrides)
                    logger.info(
                        "Cron graph %s (%s) fired at %s",
                        graph_id[:8],
                        g_name,
                        next_dt.isoformat(),
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Cron loop error graph=%s: %s", graph_id[:8], exc)
                await asyncio.sleep(60)  # back-off on unexpected errors

    async def _ical_loop(self, graph_id: str, node_id: str, refresh_min: float) -> None:
        """Triggers the graph containing an ical node on its refresh schedule.

        Fires once immediately (to populate outputs on startup), then every
        refresh_min minutes.  The actual HTTP fetch is throttled inside
        _execute_graph via the last_fetch_ts timestamp, so redundant calls are
        cheap.
        """
        while True:
            try:
                entry = self._graphs.get(graph_id)
                if entry and entry[1]:  # still exists and enabled
                    g_name, _, flow = entry
                    await self._execute_graph(graph_id, g_name, flow, {})
                    logger.debug("iCal graph %s (%s) node %s refreshed", graph_id[:8], g_name, node_id[:8])

                await asyncio.sleep(refresh_min * 60)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("iCal loop error graph=%s node=%s: %s", graph_id[:8], node_id[:8], exc)
                await asyncio.sleep(60)  # back-off on unexpected errors

    # ── Event Handler ─────────────────────────────────────────────────────

    async def _on_value_event(self, event: Any) -> None:
        dp_id = str(event.datapoint_id)
        now = datetime.now(UTC)
        logic_depth = int(getattr(event, "logic_depth", 0) or 0)

        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            name, enabled, flow = entry
            if not enabled:
                continue
            if getattr(event, "source_adapter", None) == "logic" and (
                dp_id in self._initializing_graphs.get(graph_id, ())
                or (graph_id in self._bulk_init_pending and getattr(event, "initialization", False) is True)
            ):
                # This graph's own initialization publish is in flight — or
                # the graph awaits its turn in a bulk config-restore pass and
                # will seed itself from the registry in a moment — so the
                # logic-sourced write must not re-enter it mid-pass (issue
                # #1031). Keep the read filters of the written DataPoint in
                # sync so a later event repeating this value is deduplicated
                # (last_value only — refreshing last_ts would start a
                # throttle window at save time and drop the next real
                # update). Unrelated live events (other DataPoints, or
                # non-logic sources of the same DataPoint) still execute
                # normally.
                sync_state = self._node_state.setdefault(graph_id, {})
                for tn in flow.nodes:
                    if tn.type == "datapoint_read" and tn.data.get("datapoint_id") == dp_id:
                        ns = sync_state.setdefault(tn.id, {})
                        ns["last_value"] = event.value
                continue
            trigger_nodes = [n for n in flow.nodes if n.type == "datapoint_read" and n.data.get("datapoint_id") == dp_id]
            if not trigger_nodes:
                continue
            if logic_depth >= _MAX_LOGIC_CASCADE_DEPTH:
                logger.warning(
                    "Logic cascade depth limit reached: suppressing graph=%s (%s) for dp=%s depth=%d",
                    graph_id[:8],
                    name,
                    dp_id,
                    logic_depth,
                )
                continue

            graph_state = self._node_state.setdefault(graph_id, {})
            overrides: dict[str, dict[str, Any]] = {}

            for tn in trigger_nodes:
                ns = graph_state.setdefault(tn.id, {})
                d = tn.data
                new_val = event.value
                last_val = ns.get("last_value")
                last_ts = ns.get("last_ts")

                # ── Filter: trigger_on_change ────────────────────────────
                toc = d.get("trigger_on_change")
                if toc is True or toc == "true":
                    if new_val == last_val:
                        continue

                # ── Filter: min_delta ────────────────────────────────────
                raw_delta = d.get("min_delta")
                if raw_delta not in (None, "", 0) and last_val is not None:
                    try:
                        if abs(float(new_val) - float(last_val)) < float(raw_delta):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: min_delta_pct ────────────────────────────────
                raw_pct = d.get("min_delta_pct")
                if raw_pct not in (None, "", 0) and last_val is not None:
                    try:
                        base = abs(float(last_val)) or 1.0
                        if abs(float(new_val) - float(last_val)) / base * 100 < float(raw_pct):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: throttle (value + unit) ──────────────────────
                tv = d.get("throttle_value")
                if tv not in (None, "", 0) and last_ts is not None:
                    try:
                        unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                        throttle_ms = float(tv) * unit_ms
                        elapsed_ms = (now - last_ts).total_seconds() * 1000
                        if elapsed_ms < throttle_ms:
                            continue
                    except (TypeError, ValueError):
                        pass

                # All filters passed — update state and add override
                ns["last_value"] = new_val
                ns["last_ts"] = now
                overrides[tn.id] = {"value": new_val, "changed": True}

            if not overrides:
                continue
            if getattr(event, "initialization", False) is True:
                # Save-time seeding cascading into another sheet stays
                # initialization: run the side-effect-free pass instead of a
                # full execution so no api_client/notify/WoL/sequence action
                # fires because a different sheet was saved. The cascade
                # depth guard above still bounds chains between sheets.
                await self.initialize_graph(graph_id, logic_depth=logic_depth, seed_overrides={dp_id: event.value})
                continue
            await self._execute_graph(graph_id, name, flow, overrides, logic_depth=logic_depth)

    async def _on_datapoint_renamed(self, event: Any) -> None:
        """Update datapoint_name in all logic nodes that reference the renamed DataPoint."""
        dp_id_str = str(event.dp_id)
        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            name, enabled, flow = entry
            changed = False
            for node in flow.nodes:
                if node.data.get("datapoint_id") == dp_id_str and node.data.get("datapoint_name") != event.new_name:
                    node.data["datapoint_name"] = event.new_name
                    changed = True
                variables, variables_changed = _rename_api_client_variable_datapoint_names(
                    node.data.get("variables"),
                    dp_id_str,
                    event.new_name,
                )
                if variables_changed:
                    node.data["variables"] = variables
                    changed = True
                if node.type == "value_sequence":
                    steps = self._sequence_steps(node.data.get("steps"))
                    for step in steps:
                        if step.get("datapoint_id") == dp_id_str and step.get("datapoint_name") != event.new_name:
                            step["datapoint_name"] = event.new_name
                            changed = True
                    node.data["steps"] = steps
            if changed:
                current = self._graphs.get(graph_id)
                if current is None or current[2] is not flow:
                    continue
                try:
                    await self._db.execute_and_commit(
                        "UPDATE logic_graphs SET flow_data=?, updated_at=? WHERE id=?",
                        (flow.model_dump_json(), datetime.now(UTC).isoformat(), graph_id),
                    )
                    logger.info(
                        "LogicManager: updated datapoint_name '%s' → '%s' in graph %s",
                        event.old_name,
                        event.new_name,
                        graph_id[:8],
                    )
                except Exception as exc:
                    logger.warning("LogicManager: failed to persist renamed datapoint in graph %s: %s", graph_id[:8], exc)

    # ── Execution ─────────────────────────────────────────────────────────

    async def execute_graph(self, graph_id: str) -> dict[str, Any]:
        """Manually trigger a graph (e.g. from API).

        Registry seeding for all datapoint_read nodes is handled inside
        _execute_graph, so no extra overrides are needed here.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            raise KeyError(f"Graph {graph_id} not in cache")
        name, enabled, flow = entry
        return await self._execute_graph(graph_id, name, flow, {})

    async def initialize_graph(self, graph_id: str, logic_depth: int = 0, seed_overrides: dict[str, Any] | None = None) -> None:
        """Seed Read Object nodes with their current registry values right
        after a graph is saved or activated (issue #1031).

        Without this, datapoint_read nodes stay unset until their DataPoint
        receives the next external update. Deliberately NOT a full
        _execute_graph run: saving a sheet is not a datapoint event, so this
        pass evaluates the graph side-effect-free — stateful nodes
        (statistics, memory, sequences) run on a throwaway state copy, no
        iCal URLs are fetched, and no trigger-driven action nodes
        (api_client, notify_*, wake_on_lan, message_archive, value_sequence)
        are started. Only datapoint_write outputs are published, and only
        for writes that descend from a seeded Read Object without passing
        through an unseeded one (whose None would be coerced to 0/False) or
        an _INIT_EXCLUDED_NODE_TYPES node (whose init output would be a
        placeholder or computed from throwaway state). Errors are logged,
        never raised, so a failed initial run cannot break the save request.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            return
        name, enabled, flow = entry
        if not enabled:
            return

        # Seed every configured Read Object from the registry; nodes whose
        # DataPoint has no current value taint their downstream subgraph.
        seeds: dict[str, dict[str, Any]] = {}
        seed_ts: dict[str, Any] = {}
        unseeded: set[str] = set()
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                # An unconfigured Read Object evaluates to None just like a
                # configured one without a value — taint it the same way.
                unseeded.add(node.id)
                continue
            if seed_overrides and dp_id_str in seed_overrides:
                # Cascaded initialization: the triggering event value takes
                # precedence — the registry handler runs concurrently and may
                # not have stored the write yet.
                seeds[node.id] = {"value": seed_overrides[dp_id_str], "changed": False}
                seed_ts[node.id] = None
                continue
            vs = None
            try:
                vs = self._registry.get_value(uuid.UUID(dp_id_str))
            except Exception:
                pass
            # The registry returns an empty ValueState for DataPoints that
            # never received a value — only a real value counts as seeded.
            if vs is not None and vs.value is not None:
                seeds[node.id] = {"value": vs.value, "changed": False}
                seed_ts[node.id] = getattr(vs, "ts", None)
            else:
                unseeded.add(node.id)
        if not seeds:
            return

        # Topology-only sets, computed once:
        # - Read.changed edges carry the synthetic changed=False seed, not
        #   the object value — branches fed via that handle must not be
        #   initialized.
        # - Seeded eligibility must follow value-carrying paths only: an edge
        #   into a write node's trigger handle controls WHEN a write fires
        #   but does not deliver the written value, so it must not make a
        #   write (e.g. Const → Write.value plus Read → Write.trigger)
        #   initializable.
        # - A write that closes a feedback loop onto a Read Object of the
        #   same DataPoint (the write is reachable from that read) would
        #   re-enter _on_value_event during publish and repeat until the
        #   cascade-depth guard — skip only those; unrelated reads of the
        #   target DataPoint (e.g. a separate status branch) keep the write
        #   eligible.
        read_node_ids = {node.id for node in flow.nodes if node.type == "datapoint_read"}
        changed_targets = {e.target for e in flow.edges if e.source in read_node_ids and e.sourceHandle == "changed"}
        excluded_ids = {node.id for node in flow.nodes if node.type in _INIT_EXCLUDED_NODE_TYPES}
        node_type_by_id = {node.id: node.type for node in flow.nodes}
        value_edges = [
            e for e in flow.edges if (e.targetHandle or "") not in _INIT_CONTROL_INPUT_HANDLES.get(node_type_by_id.get(e.target, ""), frozenset())
        ]
        wired_inputs = {(e.target, e.targetHandle or "in") for e in flow.edges}
        feedback_writes: set[str] = set()
        reach_by_read: dict[str, set[str]] = {}
        for rnode in flow.nodes:
            if rnode.type != "datapoint_read":
                continue
            r_dp = rnode.data.get("datapoint_id")
            if not r_dp:
                continue
            reach = _downstream_closure({rnode.id}, flow.edges)
            reach_by_read[rnode.id] = reach
            feedback_writes.update(
                wnode.id for wnode in flow.nodes if wnode.type == "datapoint_write" and wnode.id in reach and wnode.data.get("datapoint_id") == r_dp
            )

        # The settle pass adds implicit write-target → read dependencies, so
        # feedback can also span several DataPoints (Read A → Write B plus
        # Read B → Write A would never settle). Build the DataPoint-level
        # dependency graph and exclude every write whose target sits on a
        # cycle, exactly like the same-DataPoint feedback above. Only
        # value-carrying reachability counts: a read that merely gates a
        # write's trigger (or another control-only handle) can never deliver
        # the written value, so it forms no settle dependency.
        reach_by_read_value: dict[str, set[str]] = {
            rnode.id: _downstream_closure({rnode.id}, value_edges) for rnode in flow.nodes if rnode.type == "datapoint_read"
        }
        dp_deps: dict[str, set[str]] = {}
        for wnode in flow.nodes:
            if wnode.type != "datapoint_write" or wnode.id in feedback_writes:
                continue
            w_dp = wnode.data.get("datapoint_id")
            if not w_dp:
                continue
            for rnode in flow.nodes:
                if rnode.type == "datapoint_read" and rnode.data.get("datapoint_id") and wnode.id in reach_by_read_value.get(rnode.id, ()):
                    dp_deps.setdefault(w_dp, set()).add(rnode.data.get("datapoint_id"))
        cyclic_dps: set[str] = set()
        for start_dp in dp_deps:
            frontier = set(dp_deps.get(start_dp, ()))
            seen: set[str] = set()
            while frontier:
                dep = frontier.pop()
                if dep == start_dp:
                    cyclic_dps.add(start_dp)
                    break
                if dep in seen:
                    continue
                seen.add(dep)
                frontier.update(dp_deps.get(dep, ()))
        feedback_writes.update(wnode.id for wnode in flow.nodes if wnode.type == "datapoint_write" and wnode.data.get("datapoint_id") in cyclic_dps)

        now = datetime.now(UTC)
        graph_state = self._node_state.setdefault(graph_id, {})

        # Prime the event filters (trigger_on_change, min_delta) BEFORE
        # publishing writes: a graph that writes a DataPoint it also reads
        # re-enters _on_value_event during the publish await. last_ts keeps
        # the value's own registry timestamp — saving is not a datapoint
        # event, so it must not start a fresh throttle window.
        for node_id, seed in seeds.items():
            ns = graph_state.setdefault(node_id, {})
            ns["last_value"] = seed["value"]
            ts = seed_ts.get(node_id)
            if ts is not None:
                ns["last_ts"] = ts

        try:
            # Excluded node types never influence published writes (their
            # subgraphs are tainted) — replace them with inert placeholders
            # for the dry run so e.g. a python_script cannot burn CPU inside
            # the save request.
            init_flow = flow
            if excluded_ids:
                init_flow = flow.model_copy(deep=True)
                for node in init_flow.nodes:
                    if node.type in _INIT_EXCLUDED_NODE_TYPES:
                        node.type = "missing_node"

            # Evaluate until intermediate DataPoints settle: a write target
            # that another Read Object of the same sheet watches feeds its
            # computed value back into that read and re-evaluates — the write
            # event is suppressed for this graph, so downstream branches
            # would otherwise initialize from the stale registry value.
            # Feedback loops are excluded via feedback_writes, so the chains
            # form a DAG and each pass settles at least one handoff level —
            # the number of DataPoints both read and written bounds the pass
            # count for chains of any length.
            read_dps = {node.data.get("datapoint_id") for node in flow.nodes if node.type == "datapoint_read" and node.data.get("datapoint_id")}
            write_dps = {node.data.get("datapoint_id") for node in flow.nodes if node.type == "datapoint_write" and node.data.get("datapoint_id")}
            for _ in range(len(read_dps & write_dps) + 1):
                # A write may only fire when it carries a seeded value: it
                # must descend from a seeded Read Object (a save must not
                # actuate unrelated branches like Const → Write) and must not
                # descend from an unseeded Read Object or an excluded node
                # type (see _INIT_EXCLUDED_NODE_TYPES).
                tainted = _downstream_closure(unseeded | changed_targets | excluded_ids, flow.edges)
                seeded_paths = _downstream_closure(set(seeds), value_edges)
                skip_writes = {
                    node.id
                    for node in flow.nodes
                    if node.type == "datapoint_write" and (node.id in tainted or node.id not in seeded_paths or node.id in feedback_writes)
                }

                # operating_hours totals are injected as overrides by
                # _execute_graph's pre-pass — mirror that here (read-only) so
                # seeded paths through such nodes publish the accumulated
                # hours instead of 0.0.
                overrides = dict(seeds)
                for node in flow.nodes:
                    if node.type != "operating_hours":
                        continue
                    ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                    acc = ns["accumulated_hours"]
                    if ns.get("last_start"):
                        acc += (now - ns["last_start"]).total_seconds() / 3600
                    overrides[node.id] = {**overrides.get(node.id, {}), "_computed_hours": round(acc, 6)}

                # Fresh state copy per pass: the executor mutates gate/
                # hysteresis state during evaluation, and a later pass with
                # settled seeds must evaluate against the ORIGINAL persisted
                # state, not the state an earlier pass derived from stale
                # intermediate values.
                hyst_copy = copy.deepcopy(self._hysteresis.get(graph_id, {}))
                executor = GraphExecutor(init_flow, hyst_copy, self._app_config)
                outputs = executor.execute(overrides, commit_memory=False)

                settled = True
                for wnode in flow.nodes:
                    if wnode.type != "datapoint_write" or wnode.id in skip_writes:
                        continue
                    node_out = outputs.get(wnode.id, {})
                    if (wnode.id, "trigger") in wired_inputs and not GraphExecutor._to_bool(node_out.get("_triggered")):
                        continue  # gated writes do not deliver a value
                    w_dp = wnode.data.get("datapoint_id")
                    write_val = node_out.get("_write_value")
                    if not w_dp or write_val is None:
                        continue
                    # A value the write-side filters would suppress is never
                    # actually written — it must not seed downstream reads.
                    if not self._write_filters_allow(wnode.data, graph_state.get(wnode.id, {}), write_val, now):
                        continue
                    for rnode in flow.nodes:
                        if rnode.type != "datapoint_read" or rnode.data.get("datapoint_id") != w_dp:
                            continue
                        if seeds.get(rnode.id, {}).get("value") != write_val:
                            seeds[rnode.id] = {"value": write_val, "changed": False}
                            unseeded.discard(rnode.id)
                            settled = False
                if settled:
                    break

            # Start/stop the operating-hours accumulators exactly like
            # _execute_graph's _apply_operating_hours_state, but only for
            # nodes driven by clean seeded inputs — a placeholder-coerced
            # False must not stop a running counter. Without this, a source
            # that is already on at activation would not be counted until
            # its next datapoint event.
            for node in flow.nodes:
                if node.type != "operating_hours" or node.id not in seeded_paths or node.id in tainted:
                    continue
                out = outputs.get(node.id, {})
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                if out.get("_reset", False):
                    ns["accumulated_hours"] = 0.0
                    ns["last_start"] = now if out.get("_active", False) else None
                elif out.get("_active", False):
                    if not ns.get("last_start"):
                        ns["last_start"] = now
                elif ns.get("last_start"):
                    ns["accumulated_hours"] += (now - ns["last_start"]).total_seconds() / 3600
                    ns["last_start"] = None

            # While the publish is in flight, _on_value_event skips THIS
            # graph for the DataPoints written here: a write target read
            # elsewhere in the same sheet (e.g. Read A → Write B plus
            # Read B → Write C) would otherwise re-enter the graph mid-pass
            # and burst until the cascade guard. Live events for other
            # DataPoints keep executing the graph normally.
            init_write_dps = {
                str(node.data.get("datapoint_id"))
                for node in flow.nodes
                if node.type == "datapoint_write" and node.id not in skip_writes and node.data.get("datapoint_id")
            }
            self._initializing_graphs[graph_id] = init_write_dps
            try:
                published_writes = await self._apply_datapoint_write_outputs(
                    graph_id, flow, outputs, graph_state, wired_inputs, now, logic_depth, skip_node_ids=skip_writes, initialization=True
                )
            finally:
                self._initializing_graphs.pop(graph_id, None)

            # Commit gate/hysteresis state only for nodes whose switched
            # output was actually published (see
            # _INIT_COMMIT_STATE_NODE_TYPES) — without a published write the
            # save must not act like a datapoint event on the stored state.
            state_committed = False
            for node in flow.nodes:
                if (
                    node.type in _INIT_COMMIT_STATE_NODE_TYPES
                    and node.id in seeded_paths
                    and node.id not in tainted
                    and node.id in hyst_copy
                    and _downstream_closure({node.id}, flow.edges) & published_writes
                ):
                    self._hysteresis.setdefault(graph_id, {})[node.id] = hyst_copy[node.id]
                    state_committed = True
            if state_committed:
                # Persist like _execute_graph does — otherwise a restart
                # before the next real execution reloads the stale pre-save
                # state from the DB while the switched value was already
                # written.
                await self._persist_node_state(graph_id)
        except Exception as exc:
            logger.warning("LogicManager: initialization of graph %s (%s) failed: %s", graph_id[:8], name, exc)

    def _order_graphs_for_initialization(self, graph_ids: list[str]) -> list[str]:
        """Order restored graphs producers-first.

        A graph that writes a DataPoint another restored graph reads must
        initialize first, so the consumer seeds from the freshly written
        registry value. Dependency cycles fall back to the given order.
        """
        infos: dict[str, tuple[set[str], set[str]]] = {}
        for gid in graph_ids:
            entry = self._graphs.get(gid)
            if not entry:
                infos[gid] = (set(), set())
                continue
            _, _, flow = entry
            reads = {n.data.get("datapoint_id") for n in flow.nodes if n.type == "datapoint_read" and n.data.get("datapoint_id")}
            writes = {n.data.get("datapoint_id") for n in flow.nodes if n.type == "datapoint_write" and n.data.get("datapoint_id")}
            infos[gid] = (reads, writes)
        ordered: list[str] = []
        remaining = list(graph_ids)
        while remaining:
            progressed = False
            for gid in list(remaining):
                reads, _ = infos[gid]
                if any(other != gid and infos[other][1] & reads for other in remaining):
                    continue  # a pending producer writes what this graph reads
                ordered.append(gid)
                remaining.remove(gid)
                progressed = True
            if not progressed:
                ordered.extend(remaining)
                break
        return ordered

    async def reinitialize_graph(self, graph_id: str) -> None:
        """Save-path helper: invalidate + reload + initialize (issue #1031).

        The read/write filter state (last_value, last_write_val, …) is
        carried across the reload: invalidate_cache drops _node_state, and an
        initialization publish evaluated against empty filter state would
        re-send unchanged actuator values on every semantic save even though
        only_on_change/min_delta/throttle should suppress them.
        """
        saved_state = self._node_state.get(graph_id)
        self.invalidate_cache(graph_id)
        await self.reload()
        if saved_state:
            self._node_state[graph_id] = saved_state
        await self.initialize_graph(graph_id)

    async def initialize_graphs(self, graph_ids: list[str]) -> None:
        """Initialize several restored graphs exactly once each (issue #1031).

        The pass runs producers-first and keeps ALL restored graphs listed in
        _bulk_init_pending for its whole duration — initialization-flagged
        cascades between imported graphs are suppressed (the later graph
        seeds itself from the then-current registry state instead of
        double-executing), while real live events keep executing the graphs
        normally (see _on_value_event).
        """
        self._bulk_init_pending.update(graph_ids)
        try:
            for graph_id in self._order_graphs_for_initialization(graph_ids):
                await self.initialize_graph(graph_id)
        finally:
            self._bulk_init_pending.difference_update(graph_ids)

    async def reset_node_state(self, graph_id: str) -> None:
        """Drop in-memory and persisted node state of a graph.

        Used by the config restore: the imported sheet carries no node state,
        so accumulators and switch states of a previously existing graph with
        reused node ids must not leak into the restored one.
        """
        self._hysteresis.pop(graph_id, None)
        self._node_state.pop(graph_id, None)
        try:
            # node_state is TEXT NOT NULL DEFAULT '{}' — reset to the empty
            # object, NULL would violate the schema.
            await self._db.execute_and_commit("UPDATE logic_graphs SET node_state = '{}' WHERE id = ?", (graph_id,))
        except Exception as exc:
            logger.warning("Graph %s: failed to reset node_state: %s", graph_id[:8], exc)

    async def _execute_graph(
        self,
        graph_id: str,
        name: str,
        flow: FlowData,
        overrides: dict[str, dict[str, Any]],
        logic_depth: int = 0,
    ) -> dict[str, Any]:
        execute_now = datetime.now(UTC)
        graph_state = self._node_state.setdefault(graph_id, {})

        # ── Seed all datapoint_read nodes from registry ───────────────────
        # In event-driven execution only the triggered node(s) have overrides.
        # All other DP-LESEN nodes would receive None, which propagates as 0.0
        # through _to_num() in downstream blocks. Fix: pre-seed from registry so
        # every DP-LESEN node has the latest known value. Caller overrides
        # (event value + changed=True) are applied on top and take priority.
        aug_overrides: dict[str, dict[str, Any]] = {}
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                continue
            try:
                dp_id = uuid.UUID(dp_id_str)
                vs = self._registry.get_value(dp_id)
                if vs is not None:
                    aug_overrides[node.id] = {"value": vs.value, "changed": False}
            except Exception:
                pass
        # Event / manual overrides take priority over registry seed
        aug_overrides.update(overrides)

        api_client_ids = {node.id for node in flow.nodes if node.type == "api_client"}
        host_check_ids = {node.id for node in flow.nodes if node.type == "host_check"}
        message_archive_ids = {node.id for node in flow.nodes if node.type == "message_archive"}
        notify_ids = {node.id for node in flow.nodes if node.type in {"notify_pushover", "notify_sms"}}
        operating_hour_ids = {node.id for node in flow.nodes if node.type == "operating_hours"}
        async_replay_source_ids = api_client_ids | host_check_ids | message_archive_ids | notify_ids
        needs_async_replay_snapshot = any(edge.source in async_replay_source_ids for edge in flow.edges)

        # ── Pre-compute operating_hours values to inject as overrides ─────
        for node in flow.nodes:
            if node.type == "operating_hours":
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                acc = ns["accumulated_hours"]
                if ns.get("last_start"):
                    acc += (execute_now - ns["last_start"]).total_seconds() / 3600
                aug_overrides[node.id] = {
                    **aug_overrides.get(node.id, {}),
                    "_computed_hours": round(acc, 6),
                }

        # ── Pre-fetch iCal URLs (refresh only when cache is stale) ───────────
        hyst = self._hysteresis.setdefault(graph_id, {})
        for node in flow.nodes:
            if node.type != "ical":
                continue
            url = (node.data.get("url") or "").strip()
            if not url:
                continue
            refresh_min = float(node.data.get("refresh_interval_min") or 60)
            hyst_node = hyst.setdefault(node.id, {})
            last_fetch: float | None = hyst_node.get("last_fetch_ts")
            url_changed = hyst_node.get("fetched_url") != url
            needs_fetch = url_changed or last_fetch is None or (execute_now.timestamp() - last_fetch) >= refresh_min * 60
            if needs_fetch:
                active_client: httpx.AsyncClient | None = None
                try:
                    current_url = url
                    active_origin: tuple[str, str, int] | None = None
                    logical_cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
                    for redirect_count in range(_ICAL_MAX_REDIRECTS + 1):
                        fetch_urls, headers, extensions = await asyncio.to_thread(_build_ical_fetch_targets, current_url)
                        cookie_header = _build_cookie_header(logical_cookie_store, current_url)
                        if cookie_header:
                            headers = {**headers, "Cookie": cookie_header}
                        current_origin = _origin_tuple(_parse_http_url(current_url))
                        if current_origin != active_origin:
                            if active_client is not None:
                                await active_client.aclose()
                            # Keep one shared logical_cookie_store across all hops (including
                            # cross-origin redirects), but rotate the HTTP client per origin.
                            active_client = httpx.AsyncClient(timeout=30.0)
                            active_origin = None if current_origin is None else tuple(current_origin)
                        if active_client is None:
                            raise ValueError("Could not initialize iCal HTTP client")
                        redirected_to: str | None = None
                        _ct = ""
                        _resp_bytes = b""
                        last_transport_error: Exception | None = None
                        for fetch_url in fetch_urls:
                            try:
                                # Requests go to a pinned IP, but cookie send/store logic uses
                                # current_url (logical host) via _build/_store_response_cookies.
                                request_headers = headers
                                async with active_client.stream("GET", fetch_url, headers=request_headers, extensions=extensions) as _resp:
                                    if _resp.status_code in {301, 302, 303, 307, 308}:
                                        location = _resp.headers.get("location")
                                        if not location:
                                            raise ValueError("iCal redirect without Location header")
                                        _store_response_cookies(logical_cookie_store, _resp.headers.get_list("set-cookie"), current_url)
                                        redirected_to = urljoin(current_url, location)
                                        break
                                    _resp.raise_for_status()
                                    _store_response_cookies(logical_cookie_store, _resp.headers.get_list("set-cookie"), current_url)
                                    _ct = _resp.headers.get("content-type", "").lower()
                                    _resp_bytes = await _read_limited_response_body(_resp, _ICAL_MAX_BYTES)
                                    break
                            except httpx.RequestError as req_exc:
                                last_transport_error = req_exc
                                continue
                        if redirected_to:
                            if redirect_count >= _ICAL_MAX_REDIRECTS:
                                raise ValueError("Too many iCal redirects")
                            current_url = _preserve_same_origin_credentials(current_url, redirected_to)
                            continue
                        if last_transport_error is not None and not _resp_bytes:
                            raise last_transport_error
                        if not _resp_bytes:
                            raise ValueError(f"Could not fetch iCal URL after trying {len(fetch_urls)} address(es)")
                        if _ct and not any(t in _ct for t in _ICAL_ALLOWED_CONTENT_TYPES):
                            logger.debug(
                                "Graph %s: non-standard iCal content-type %r for %s; validating by body signature",
                                graph_id[:8],
                                _ct,
                                current_url,
                            )
                        # Decode with charset from Content-Type; many iCal servers
                        # omit the charset and serve Latin-1 (e.g. c-trace.de).
                        # Try strict UTF-8 first; fall back to Latin-1 which always
                        # succeeds and covers ISO-8859-1 / CP-1252 content.
                        _charset: str | None = None
                        for _part in _ct.split(";"):
                            _p = _part.strip()
                            if _p.lower().startswith("charset="):
                                _charset = _p[8:].strip().strip('"').strip("'")
                                break
                        if _charset:
                            _raw_text = _resp_bytes.decode(_charset, errors="replace")
                        else:
                            try:
                                _raw_text = _resp_bytes.decode("utf-8")
                            except UnicodeDecodeError:
                                _raw_text = _resp_bytes.decode("latin-1")
                        if not _raw_text.lstrip().startswith("BEGIN:VCALENDAR"):
                            raise ValueError(f"Response is not an iCal file (starts with {_raw_text[:60]!r})")
                        hyst_node["raw"] = _raw_text
                        hyst_node["fetched_url"] = url
                        hyst_node["last_fetch_ts"] = execute_now.timestamp()
                        logger.info("Graph %s: iCal fetched from %s (%d bytes)", graph_id[:8], current_url, len(_resp_bytes))
                        break
                except Exception as _exc:
                    logger.warning("Graph %s: iCal fetch failed for node %s (%s): %s", graph_id[:8], node.id[:8], url, _exc)
                finally:
                    if active_client is not None:
                        await active_client.aclose()

        # ── Pre-fill heating_circuit missing slots from history ───────────────────────
        # For each heating_circuit node: when a slot (T1/T2/T3) is missing for today
        # and the clock has already passed the slot's threshold hour, query the history
        # for the last value at or before that hour and inject it as _history_{slot}.
        # This covers restarts where the slot would otherwise stay empty all day.
        import datetime as _hc_dt  # noqa: PLC0415
        import zoneinfo as _hc_zi  # noqa: PLC0415

        _hc_tz = _hc_zi.ZoneInfo(self._app_config.get("timezone", "Europe/Zurich"))
        _hc_now = _hc_dt.datetime.now(tz=_hc_tz)
        _hc_today = _hc_now.date().isoformat()
        _HC_SLOTS = (("t1", 7), ("t2", 14), ("t3", 21))

        for node in flow.nodes:
            if node.type != "heating_circuit":
                continue
            # Find the datapoint_id and datapoint_read node via graph edges
            _hc_dp_id_str: str | None = None
            _hc_dp_read_node = None
            for edge in flow.edges:
                if edge.target != node.id:
                    continue
                _src = next((n for n in flow.nodes if n.id == edge.source), None)
                if _src and _src.type == "datapoint_read":
                    _hc_dp_id_str = _src.data.get("datapoint_id")
                    _hc_dp_read_node = _src
                    break
            if not _hc_dp_id_str:
                continue
            _hc_node_state = hyst.setdefault(node.id, {})
            _hc_node_aug = aug_overrides.setdefault(node.id, {})
            # Always inject app-timezone date so executor uses the same date as the manager;
            # without this, system clock vs. app timezone differences around midnight can
            # cause slots to be tagged with the wrong date and re-filled on every run.
            _hc_node_aug["_date"] = _hc_today
            try:
                from obs.history.factory import get_history_plugin as _get_hp  # noqa: PLC0415

                _hc_dp_id = uuid.UUID(_hc_dp_id_str)
                _hc_plugin = _get_hp()
                for _hc_slot, _hc_hour in _HC_SLOTS:
                    if _hc_node_state.get(f"{_hc_slot}_date") == _hc_today:
                        continue  # already captured today
                    if _hc_now.hour < _hc_hour:
                        continue  # not yet past slot time
                    # Query last known value at or before the slot's threshold time
                    _slot_dt = _hc_now.replace(hour=_hc_hour, minute=0, second=0, microsecond=0)
                    _from_dt = (_slot_dt - _hc_dt.timedelta(hours=24)).astimezone(UTC)
                    _to_dt = _slot_dt.astimezone(UTC)
                    _rows = await _hc_plugin.query(_hc_dp_id, _from_dt, _to_dt, limit=1)
                    if _rows:
                        # Keep raw value; float() is deferred until after transforms so that
                        # value_map can handle non-numeric stored values (e.g. "on" → 22.5).
                        _hist_val: Any = _rows[0]["v"]
                        # Apply the same transforms as live datapoint_read execution
                        if _hc_dp_read_node:
                            _hc_formula = (_hc_dp_read_node.data.get("value_formula") or "").strip()
                            if _hc_formula:
                                try:
                                    from obs.logic.executor import GraphExecutor as _GE  # noqa: PLC0415

                                    _hist_val = _GE._safe_eval(_hc_formula, {"x": float(_hist_val)})
                                except Exception:
                                    pass
                            _hc_vmap = _hc_dp_read_node.data.get("value_map")
                            if _hc_vmap:
                                try:
                                    from obs.core.transformation import apply_value_map as _avm  # noqa: PLC0415

                                    _hist_val = _avm(_hist_val, _hc_vmap)
                                except Exception:
                                    pass
                        try:
                            _hc_node_aug[f"_history_{_hc_slot}"] = float(_hist_val)
                            logger.debug(
                                "Graph %s: heating_circuit %s: %s filled from history: %.1f",
                                graph_id[:8],
                                node.id[:8],
                                _hc_slot,
                                float(_hc_node_aug[f"_history_{_hc_slot}"]),
                            )
                        except (TypeError, ValueError):
                            logger.debug(
                                "Graph %s: heating_circuit %s: %s history value not numeric after transforms, skipping",
                                graph_id[:8],
                                node.id[:8],
                                _hc_slot,
                            )
            except Exception as _hc_exc:
                logger.debug("Graph %s: heating_circuit history pre-fill failed: %s", graph_id[:8], _hc_exc)

        executor = GraphExecutor(flow, hyst, self._app_config)
        try:
            pre_execute_hyst = copy.deepcopy(hyst) if needs_async_replay_snapshot else None
            pre_execute_node_state = copy.deepcopy(graph_state) if needs_async_replay_snapshot else None
            outputs = executor.execute(aug_overrides, commit_memory=False)
        except Exception as exc:
            logger.error("Graph %s (%s) execution error: %s", graph_id, name, exc)
            return {}

        def _apply_operating_hours_state(node_ids: set[str] | None = None, base_state: dict[str, Any] | None = None) -> None:
            target_ids = operating_hour_ids if node_ids is None else operating_hour_ids & node_ids
            for node in flow.nodes:
                if node.id not in target_ids:
                    continue
                out = outputs.get(node.id, {})
                if base_state is not None:
                    graph_state[node.id] = copy.deepcopy(base_state.get(node.id, {"accumulated_hours": 0.0, "last_start": None}))
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                is_reset = out.get("_reset", False)
                is_active = out.get("_active", False)
                if is_reset:
                    ns["accumulated_hours"] = 0.0
                    ns["last_start"] = execute_now if is_active else None
                elif is_active:
                    if not ns.get("last_start"):
                        ns["last_start"] = execute_now
                elif ns.get("last_start"):
                    ns["accumulated_hours"] += (execute_now - ns["last_start"]).total_seconds() / 3600
                    ns["last_start"] = None

        # ── Update operating_hours state ─────────────────────────────────
        _apply_operating_hours_state()

        # ── Cron-reachability preamble ────────────────────────────────────
        # Shared by host_check and wake_on_lan: each cron tick is treated as a
        # fresh rising edge, so nodes that fire on sustained truthy inputs from
        # cron are not suppressed by the rising-edge deduplication below.
        cron_node_ids = {n.id for n in flow.nodes if n.type == "timer_cron"}
        # Forward-reachability from the cron nodes that actually fired this
        # execution — scopes the cron-retrigger exception to only those async
        # nodes driven by the firing cron, not every cron in the graph.
        fired_crons = overrides.keys() & cron_node_ids
        cron_reachable: set[str] = set(fired_crons)
        if fired_crons:
            _cq: list[str] = list(fired_crons)
            while _cq:
                _cn = _cq.pop()
                for _ce in flow.edges:
                    if _ce.source == _cn and _ce.target not in cron_reachable:
                        cron_reachable.add(_ce.target)
                        _cq.append(_ce.target)

        async def _run_host_check_node(node: Any, target_set: set[str], log_suffix: str = "") -> bool:
            out = outputs.get(node.id, {})
            hyst_hc = hyst.setdefault(node.id, {})
            is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
            was_triggered = hyst_hc.get("hc_prev_trigger", False)
            is_cron_triggered = node.id in cron_reachable
            if not is_triggered:
                return False
            host = (node.data.get("host") or "").strip()
            if not host:
                logger.warning("host_check: host missing on node %s", node.id[:8])
                return False
            try:
                timeout_s, count = _normalise_host_check_ping_config(node.data.get("timeout_s"), node.data.get("count"))
                config_sig = f"{host}\0{timeout_s:g}\0{count}"
            except Exception as exc:
                logger.warning("Graph %s: host_check %s failed: %s", graph_id[:8], host, exc)
                return False
            if (
                was_triggered
                and not is_cron_triggered
                and hyst_hc.get("hc_config_sig") == config_sig
                and hyst_hc.get("hc_runtime_token") == _HOST_CHECK_RUNTIME_TOKEN
            ):
                outputs[node.id]["reachable"] = hyst_hc.get("hc_last_reachable", False)
                outputs[node.id]["latency_ms"] = hyst_hc.get("hc_last_latency_ms")
                target_set.add(node.id)
                return True
            try:
                reachable, latency_ms = await _ping_host(host, count, timeout_s)
                hyst_hc["hc_prev_trigger"] = True
                hyst_hc["hc_last_reachable"] = reachable
                hyst_hc["hc_last_latency_ms"] = latency_ms
                hyst_hc["hc_config_sig"] = config_sig
                hyst_hc["hc_runtime_token"] = _HOST_CHECK_RUNTIME_TOKEN
                outputs[node.id]["reachable"] = reachable
                outputs[node.id]["latency_ms"] = latency_ms
                target_set.add(node.id)
                logger.info(
                    "Graph %s: host_check%s %s → reachable=%s latency=%s ms",
                    graph_id[:8],
                    log_suffix,
                    host,
                    reachable,
                    f"{latency_ms:.1f}" if latency_ms is not None else "—",
                )
                return True
            except Exception as exc:
                logger.warning("Graph %s: host_check %s failed: %s", graph_id[:8], host, exc)
                return False

        # ── Handle host_check ─────────────────────────────────────────────
        # Rising-edge trigger (same cron-exemption logic as wake_on_lan):
        # ping is sent only on the False→True transition of _trigger, or on
        # every cron tick if this node is reachable from a firing cron node.
        # Runs BEFORE wake_on_lan so that graphs with host_check → WoL see
        # real reachability values, not executor placeholders.

        # Accumulates edge-level input overrides from every resolved async node.
        # Injected into every replay merge so that nodes downstream of multiple
        # async sources see real values instead of first-pass placeholders.
        resolved_async_edge_overrides: dict[str, dict[str, Any]] = {}

        # Initialised here (before any replay pass) so that output-update guards
        # in the HC and WoL replay loops can safely reference this set even before
        # the api_client processing block populates it.
        triggered_api_clients: set[str] = set()

        def _add_resolved_outputs(node_ids: set[str]) -> None:
            for _re in flow.edges:
                if _re.source in node_ids:
                    resolved_async_edge_overrides.setdefault(_re.target, {})[_re.targetHandle or "in"] = GraphExecutor._get_output_value(
                        outputs.get(_re.source, {}), _re.sourceHandle or "out"
                    )

        def _replay_async_descendants(node_ids: set[str], *, skip_node_ids: set[str] | None = None) -> set[str]:
            descendants: set[str] = set()
            queue: list[str] = list(node_ids)
            while queue:
                source_id = queue.pop()
                for edge in flow.edges:
                    if edge.source == source_id and edge.target not in descendants:
                        descendants.add(edge.target)
                        queue.append(edge.target)
            if not descendants:
                return descendants

            replay_overrides: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                replay_overrides.setdefault(nid, {}).update(vals)
            for edge in flow.edges:
                if edge.source in node_ids:
                    source_handle = edge.sourceHandle or "out"
                    target_handle = edge.targetHandle or "in"
                    source_value = GraphExecutor._get_output_value(outputs.get(edge.source, {}), source_handle)
                    replay_overrides.setdefault(edge.target, {})[target_handle] = source_value
            for edge in flow.edges:
                if edge.target not in descendants or edge.source in descendants or edge.source in node_ids:
                    continue
                source_handle = edge.sourceHandle or "out"
                target_handle = edge.targetHandle or "in"
                source_value = GraphExecutor._get_output_value(outputs.get(edge.source, {}), source_handle)
                replay_overrides.setdefault(edge.target, {})[target_handle] = source_value

            replay_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            replay_executor = GraphExecutor(flow, replay_hyst, self._app_config)
            replay_outputs = replay_executor.execute(replay_overrides, commit_memory=False)
            blocked_ids = skip_node_ids or set()
            for nid, vals in replay_outputs.items():
                if nid in descendants and nid not in blocked_ids:
                    outputs[nid] = vals
                    if nid in replay_hyst:
                        hyst[nid] = replay_hyst[nid]
            _apply_operating_hours_state(descendants, pre_execute_node_state)
            return descendants

        triggered_host_check_nodes: set[str] = set()
        for node in flow.nodes:
            if node.type != "host_check":
                continue
            await _run_host_check_node(node, triggered_host_check_nodes)
        _add_resolved_outputs(triggered_host_check_nodes)

        # ── Re-propagate host_check outputs to downstream nodes ───────────
        pending_host_check_replay = set(triggered_host_check_nodes)
        processed_host_check_replay: set[str] = set()
        while pending_host_check_replay:
            replay_sources = pending_host_check_replay - processed_host_check_replay
            if not replay_sources:
                break
            processed_host_check_replay.update(replay_sources)
            hc_downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in replay_sources:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    hc_downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if not hc_downstream_overrides:
                continue
            hc_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                hc_merged.setdefault(nid, {}).update(vals)
            for nid, vals in hc_downstream_overrides.items():
                hc_merged.setdefault(nid, {}).update(vals)
            hc_hyst_snapshot = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            hc_second_executor = GraphExecutor(flow, hc_hyst_snapshot, self._app_config)
            hc_second_outputs = hc_second_executor.execute(hc_merged, commit_memory=False)
            hc_descendants: set[str] = set()
            hc_queue: list[str] = list(replay_sources)
            while hc_queue:
                nid = hc_queue.pop()
                for e in flow.edges:
                    if e.source == nid and e.target not in hc_descendants:
                        hc_descendants.add(e.target)
                        hc_queue.append(e.target)
            for nid, vals in hc_second_outputs.items():
                if nid in hc_descendants and nid not in triggered_api_clients:
                    outputs[nid] = vals
                    if nid not in host_check_ids and nid in hc_hyst_snapshot:
                        hyst[nid] = hc_hyst_snapshot[nid]
            _apply_operating_hours_state(hc_descendants, pre_execute_node_state)
            newly_triggered_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in hc_descendants and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, newly_triggered_hc, " (replay)")
            if newly_triggered_hc:
                triggered_host_check_nodes.update(newly_triggered_hc)
                _add_resolved_outputs(newly_triggered_hc)
                pending_host_check_replay.update(newly_triggered_hc)

        async def _run_wake_on_lan_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            hyst_wol = hyst.setdefault(node.id, {})
            is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
            was_triggered = hyst_wol.get("wol_prev_trigger", False)
            # Cron-retrigger exception applies only when the firing cron node
            # actually drives this specific WoL node (reachability check above).
            is_cron_triggered = node.id in cron_reachable
            if not is_triggered:
                hyst_wol["wol_prev_trigger"] = False
                return False
            if was_triggered and not is_cron_triggered:
                return False
            mac = (node.data.get("mac_address") or "").strip()
            if not mac:
                logger.warning("wake_on_lan: mac_address missing on node %s", node.id[:8])
                return False
            broadcast = (node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
            _port_raw = node.data.get("port")
            try:
                if isinstance(_port_raw, float) and not _port_raw.is_integer():
                    raise ValueError(f"fractional port {_port_raw!r} — must be a whole number")
                port = int(_port_raw) if _port_raw not in (None, "") else 9
                if not (1 <= port <= 65535):
                    raise ValueError(f"port {port!r} out of range 1–65535")
                try:
                    ipaddress.IPv4Address(broadcast)
                except ValueError:
                    raise ValueError(f"invalid broadcast IP {broadcast!r}") from None
                await asyncio.to_thread(_send_wol_packet, mac, broadcast, port)
                # Record the consumed rising edge only after a successful send so
                # that a transient failure does not silently suppress the next attempt.
                hyst_wol["wol_prev_trigger"] = True
                outputs[node.id]["sent"] = True
                target_set.add(node.id)
                logger.info("Graph %s: WoL sent by node %s", graph_id[:8], node.id[:8])
                return True
            except Exception as exc:
                logger.warning("Graph %s: WoL failed on node %s: %s", graph_id[:8], node.id[:8], type(exc).__name__)
                return False

        # ── Handle wake_on_lan ────────────────────────────────────────────
        # Runs AFTER host_check so that graphs with host_check → WoL read
        # real reachability, and BEFORE api_client/notify so that wol.sent
        # can propagate to downstream api_client or notify in the same tick.
        triggered_wol_nodes: set[str] = set()
        for node in flow.nodes:
            if node.type != "wake_on_lan":
                continue
            await _run_wake_on_lan_node(node, triggered_wol_nodes)

        _add_resolved_outputs(triggered_wol_nodes)

        # ── Re-propagate wake_on_lan sent=True to downstream nodes ───────────
        # The first executor pass computed downstream nodes with sent=False.
        # Re-run only the transitive downstream subgraph with the real sent
        # value injected as an input override.
        # Full aug_overrides (dp-read seeds + cron/event overrides from the
        # call site) are carried into the second pass so that downstream nodes
        # which also read from a cron pulse or a datapoint see correct values.
        # Only transitively downstream nodes are updated from the second pass
        # so that unrelated nodes (e.g. an api_client with its own trigger)
        # keep their first-pass results.
        if triggered_wol_nodes:
            wol_downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in triggered_wol_nodes:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    wol_downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if wol_downstream_overrides:
                wol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    wol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in wol_downstream_overrides.items():
                    wol_merged.setdefault(nid, {}).update(vals)
                # Use a deep copy of hyst so that stateful nodes (statistics,
                # avg_multi, …) don't accumulate a second sample just because
                # a WoL edge is present — we only want their *outputs*, not
                # a second mutation of their persisted state.
                wol_second_executor = GraphExecutor(flow, copy.deepcopy(hyst), self._app_config)
                wol_second_outputs = wol_second_executor.execute(wol_merged, commit_memory=False)
                # Compute transitive closure of WoL-triggered nodes so that only
                # their descendants are updated, leaving unrelated nodes intact.
                wol_descendants: set[str] = set()
                queue = list(triggered_wol_nodes)
                while queue:
                    nid = queue.pop()
                    for e in flow.edges:
                        if e.source == nid and e.target not in wol_descendants:
                            wol_descendants.add(e.target)
                            queue.append(e.target)
                wol_node_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in wol_second_outputs.items():
                    if nid not in wol_node_ids and nid in wol_descendants:
                        outputs[nid] = vals

        # ── Post-WoL host_check pass ──────────────────────────────────────
        # WoL.sent may drive host_check._trigger via downstream edges. Run
        # those checks now so the api_client loop below sees real reachability.
        if triggered_wol_nodes:
            _wol_all_desc: set[str] = set()
            _wol_desc_q: list[str] = list(triggered_wol_nodes)
            while _wol_desc_q:
                _wn = _wol_desc_q.pop()
                for _we in flow.edges:
                    if _we.source == _wn and _we.target not in _wol_all_desc:
                        _wol_all_desc.add(_we.target)
                        _wol_desc_q.append(_we.target)
            _post_wol_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in _wol_all_desc and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, _post_wol_hc, " (post-wol)")
            if _post_wol_hc:
                triggered_host_check_nodes.update(_post_wol_hc)
                _add_resolved_outputs(_post_wol_hc)
                _pending_pwol = set(_post_wol_hc)
                _processed_pwol: set[str] = set()
                while _pending_pwol:
                    _pwol_src = _pending_pwol - _processed_pwol
                    if not _pwol_src:
                        break
                    _processed_pwol.update(_pwol_src)
                    _pwol_dn_ovr: dict[str, dict[str, Any]] = {}
                    for _e in flow.edges:
                        if _e.source in _pwol_src:
                            _pwol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                outputs[_e.source], _e.sourceHandle or "out"
                            )
                    if not _pwol_dn_ovr:
                        continue
                    _pwol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                    for nid, vals in resolved_async_edge_overrides.items():
                        _pwol_merged.setdefault(nid, {}).update(vals)
                    for nid, vals in _pwol_dn_ovr.items():
                        _pwol_merged.setdefault(nid, {}).update(vals)
                    _pwol_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                    _pwol_exec = GraphExecutor(flow, _pwol_hyst, self._app_config)
                    _pwol_out = _pwol_exec.execute(_pwol_merged, commit_memory=False)
                    _pwol_desc: set[str] = set()
                    _pwol_dq: list[str] = list(_pwol_src)
                    while _pwol_dq:
                        _pn = _pwol_dq.pop()
                        for _e in flow.edges:
                            if _e.source == _pn and _e.target not in _pwol_desc:
                                _pwol_desc.add(_e.target)
                                _pwol_dq.append(_e.target)
                    for nid, vals in _pwol_out.items():
                        if nid in _pwol_desc and nid not in triggered_api_clients:
                            outputs[nid] = vals
                            if nid not in host_check_ids and nid in _pwol_hyst:
                                hyst[nid] = _pwol_hyst[nid]
                    _apply_operating_hours_state(_pwol_desc, pre_execute_node_state)
                    _chained_pwol: set[str] = set()
                    for node in flow.nodes:
                        if node.type == "host_check" and node.id in _pwol_desc and node.id not in triggered_host_check_nodes:
                            await _run_host_check_node(node, _chained_pwol, " (post-wol replay)")
                    if _chained_pwol:
                        triggered_host_check_nodes.update(_chained_pwol)
                        _add_resolved_outputs(_chained_pwol)
                        _pending_pwol.update(_chained_pwol)

        # ── Handle api_client ─────────────────────────────────────────────
        # Track api_client nodes with final manager-computed outputs so we can
        # re-propagate success responses and explicit error details downstream.
        triggered_api_clients: set[str] = set()
        execution_values_by_datapoint_id: dict[str, Any] = {}
        execution_value_priority_by_datapoint_id: dict[str, int] = {}
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = str(node.data.get("datapoint_id") or "").strip()
            if not dp_id_str or node.id not in aug_overrides or "value" not in aug_overrides[node.id]:
                continue
            node_override = aug_overrides[node.id]
            priority = 2 if node.id in overrides or GraphExecutor._to_bool(node_override.get("changed")) else 1
            if priority >= execution_value_priority_by_datapoint_id.get(dp_id_str, 0):
                execution_values_by_datapoint_id[dp_id_str] = node_override["value"]
                execution_value_priority_by_datapoint_id[dp_id_str] = priority
        import json as _json  # noqa: PLC0415

        async def _run_api_client_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False
            variable_resolver = _make_api_client_variable_resolver(
                self._registry,
                node.data.get("variables"),
                execution_values_by_datapoint_id,
            )
            try:
                url = _replace_api_client_url_placeholders(
                    node.data.get("url") or "",
                    variable_resolver,
                ).strip()
                if not url:
                    return False
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            try:
                request_urls, pinned_headers, request_extensions = _build_api_client_fetch_targets(url)
            except ValueError as exc:
                logger.warning("Graph %s: blocked api_client target %s: %s", graph_id[:8], url, exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            method = (node.data.get("method", "GET") or "GET").upper()
            content_type = node.data.get("content_type", "application/json")
            resp_type = node.data.get("response_type", "application/json")
            verify_ssl = node.data.get("verify_ssl", True)
            if isinstance(verify_ssl, str):
                verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
            timeout_s = float(node.data.get("timeout_s", 10) or 10)
            extra_headers: dict[str, str] = {}
            hdr_str = (node.data.get("headers") or "").strip()
            if hdr_str:
                try:
                    extra_headers = _json.loads(hdr_str)
                except Exception:
                    pass
            hdr_file = (node.data.get("headers_secret_file") or "").strip()
            if hdr_file:
                try:
                    extra_headers = {
                        **extra_headers,
                        **_json.loads(_read_secret_file(hdr_file)),
                    }
                except Exception:
                    pass
            try:
                extra_headers = _replace_api_client_placeholders(extra_headers, variable_resolver)
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            # ── Authentication ──────────────────────────────────────────
            auth_type = (node.data.get("auth_type") or "none").lower()
            auth: Any = None
            try:
                if auth_type in ("basic", "digest"):
                    username = _replace_api_client_placeholders(
                        node.data.get("auth_username") or "",
                        variable_resolver,
                    ).strip()
                    password = _replace_api_client_placeholders(
                        node.data.get("auth_password") or "",
                        variable_resolver,
                    )
                    if username:
                        auth = httpx.BasicAuth(username, password) if auth_type == "basic" else httpx.DigestAuth(username, password)
                elif auth_type == "bearer":
                    token = _replace_api_client_placeholders(
                        node.data.get("auth_token") or "",
                        variable_resolver,
                    ).strip()
                    if not token:
                        token = _replace_api_client_placeholders(
                            _read_secret_file(node.data.get("auth_token_file") or ""),
                            variable_resolver,
                        ).strip()
                    if token:
                        extra_headers = {
                            **extra_headers,
                            "Authorization": f"Bearer {token}",
                        }
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            try:
                req_kwargs: dict[str, Any] = {
                    "headers": extra_headers,
                    "timeout": timeout_s,
                }
                if method in ("POST", "PUT", "PATCH"):
                    body = _replace_api_client_placeholders(out.get("_body"), variable_resolver)
                    if content_type == "application/json":
                        req_kwargs["content"] = _json.dumps(body) if not isinstance(body, (str, bytes)) else body
                        req_kwargs["headers"] = {
                            **extra_headers,
                            "Content-Type": "application/json",
                        }
                    elif content_type == "application/x-www-form-urlencoded":
                        req_kwargs["data"] = body if isinstance(body, dict) else {"data": str(body)}
                    else:
                        req_kwargs["content"] = str(body or "")
                        req_kwargs["headers"] = {
                            **extra_headers,
                            "Content-Type": "text/plain",
                        }
                req_headers = {key: value for key, value in req_kwargs.get("headers", {}).items() if key.lower() != "host"}
                req_kwargs["headers"] = {**req_headers, **pinned_headers}
                if request_extensions:
                    req_kwargs["extensions"] = request_extensions
                last_transport_error: Exception = ValueError(f"Could not fetch API target after trying {len(request_urls)} address(es)")
                resp: httpx.Response | Any | None = None
                async with httpx.AsyncClient(auth=auth, verify=verify_ssl) as client:
                    for request_url in request_urls:
                        try:
                            resp = await client.request(method, request_url, **req_kwargs)
                            break
                        except httpx.RequestError as req_exc:
                            last_transport_error = req_exc
                            if method not in _API_CLIENT_RETRYABLE_METHODS:
                                break
                            continue
                if resp is None:
                    raise last_transport_error
                resp_text = resp.text
                if len(resp_text) > 1_000_000:
                    resp_text = resp_text[:1_000_000]
                if resp_type in ("json", "application/json"):
                    try:
                        resp_data: Any = resp.json()
                    except Exception:
                        resp_data = resp_text
                else:
                    resp_data = resp_text
                outputs[node.id].update(
                    {
                        "response": resp_data,
                        "status": resp.status_code,
                        "success": 200 <= resp.status_code < 300,
                    },
                )
                logger.info(
                    "Graph %s: API %s %s → %d",
                    graph_id[:8],
                    method,
                    url,
                    resp.status_code,
                )
                target_set.add(node.id)
                return True
            except Exception as exc:
                logger.warning("Graph %s: api_client failed: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True

        for node in flow.nodes:
            if node.type != "api_client":
                continue
            await _run_api_client_node(node, triggered_api_clients)

        _add_resolved_outputs(triggered_api_clients)

        # ── Re-propagate api_client outputs to downstream nodes ───────────
        # The first executor pass computed downstream nodes with the placeholder
        # success=False. Now that we have the real HTTP results, we re-run the
        # executor for those downstream nodes using input overrides so their
        # outputs (and downstream datapoint writes, etc.) reflect the real values.
        api_replay_overrides: dict[str, dict[str, Any]] | None = None
        if triggered_api_clients:
            downstream_node_ids: set[str] = set()
            pending_sources = list(triggered_api_clients)
            while pending_sources:
                source_id = pending_sources.pop()
                for e in flow.edges:
                    if e.source != source_id or e.target in downstream_node_ids:
                        continue
                    downstream_node_ids.add(e.target)
                    pending_sources.append(e.target)

            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in triggered_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if downstream_overrides:
                replay_overrides = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                for nid, vals in downstream_overrides.items():
                    replay_overrides.setdefault(nid, {}).update(vals)
                for e in flow.edges:
                    if e.target not in downstream_node_ids or e.source in downstream_node_ids or e.source in triggered_api_clients:
                        continue
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    replay_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs.get(e.source, {}), src_handle)
                api_replay_overrides = {nid: dict(vals) for nid, vals in replay_overrides.items()}
                if pre_execute_hyst is not None:
                    replay_hyst = copy.deepcopy(pre_execute_hyst)
                    second_executor = GraphExecutor(flow, replay_hyst, self._app_config)
                    second_outputs = second_executor.execute(replay_overrides, commit_memory=False)
                    # Compute transitive descendants of triggered api_clients so that
                    # only their subtree is updated. This prevents the api_client
                    # second pass from overwriting WoL-propagated outputs that were
                    # already written to outputs[] by the WoL second pass above.
                    api_descendants: set[str] = set()
                    _aq: list[str] = list(triggered_api_clients)
                    while _aq:
                        _an = _aq.pop()
                        for _ae in flow.edges:
                            if _ae.source == _an and _ae.target not in api_descendants:
                                api_descendants.add(_ae.target)
                                _aq.append(_ae.target)
                    for nid, vals in second_outputs.items():
                        if nid not in api_client_ids and nid in api_descendants:
                            outputs[nid] = vals
                            if nid in replay_hyst:
                                hyst[nid] = replay_hyst[nid]

        # ── Post-api-replay host_check pass ───────────────────────────────
        # api_client outputs (via the second executor pass above) may have
        # updated host_check trigger values. Re-run host_check for any nodes
        # not fired in the first pass whose trigger is now true.
        post_api_triggered_hc: set[str] = set()
        for node in flow.nodes:
            if node.type != "host_check" or node.id in triggered_host_check_nodes:
                continue
            if await _run_host_check_node(node, post_api_triggered_hc, " (post-api)"):
                triggered_host_check_nodes.add(node.id)
        if post_api_triggered_hc:
            _add_resolved_outputs(post_api_triggered_hc)

        post_api_hc_descendants: set[str] = set()
        pending_post_api_hc_replay = set(post_api_triggered_hc)
        processed_post_api_hc_replay: set[str] = set()
        while pending_post_api_hc_replay:
            replay_sources = pending_post_api_hc_replay - processed_post_api_hc_replay
            if not replay_sources:
                break
            processed_post_api_hc_replay.update(replay_sources)
            pat_hc_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in replay_sources:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    pat_hc_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if not pat_hc_overrides:
                continue
            pat_base_overrides = api_replay_overrides if api_replay_overrides is not None else aug_overrides
            pat_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in pat_base_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                pat_merged.setdefault(nid, {}).update(vals)
            for nid, vals in pat_hc_overrides.items():
                pat_merged.setdefault(nid, {}).update(vals)
            pat_hyst_snapshot = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            pat_executor = GraphExecutor(flow, pat_hyst_snapshot, self._app_config)
            pat_outputs = pat_executor.execute(pat_merged, commit_memory=False)
            pat_descendants: set[str] = set()
            pat_queue: list[str] = list(replay_sources)
            while pat_queue:
                nid = pat_queue.pop()
                for e in flow.edges:
                    if e.source == nid and e.target not in pat_descendants:
                        pat_descendants.add(e.target)
                        pat_queue.append(e.target)
            post_api_hc_descendants.update(pat_descendants)
            for nid, vals in pat_outputs.items():
                if nid in pat_descendants and nid not in triggered_api_clients:
                    outputs[nid] = vals
                    if nid not in host_check_ids and nid in pat_hyst_snapshot:
                        hyst[nid] = pat_hyst_snapshot[nid]
            _apply_operating_hours_state(pat_descendants, pre_execute_node_state)
            newly_triggered_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in pat_descendants and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, newly_triggered_hc, " (post-api replay)")
            if newly_triggered_hc:
                post_api_triggered_hc.update(newly_triggered_hc)
                triggered_host_check_nodes.update(newly_triggered_hc)
                _add_resolved_outputs(newly_triggered_hc)
                pending_post_api_hc_replay.update(newly_triggered_hc)

        # Post-api host_check replay can make downstream WoL nodes fire after
        # the normal WoL loop has already run. Process those affected nodes once
        # more so the side effect is not deferred to the next graph execution.
        post_api_wol_nodes: set[str] = set()
        if post_api_hc_descendants:
            for node in flow.nodes:
                if node.type != "wake_on_lan" or node.id not in post_api_hc_descendants or node.id in triggered_wol_nodes:
                    continue
                out = outputs.get(node.id, {})
                hyst_wol = hyst.setdefault(node.id, {})
                is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
                was_triggered = hyst_wol.get("wol_prev_trigger", False)
                is_cron_triggered = node.id in cron_reachable
                if not is_triggered:
                    hyst_wol["wol_prev_trigger"] = False
                    continue
                if was_triggered and not is_cron_triggered:
                    continue
                mac = (node.data.get("mac_address") or "").strip()
                if not mac:
                    logger.warning("wake_on_lan: mac_address missing on node %s", node.id[:8])
                    continue
                broadcast = (node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
                _port_raw = node.data.get("port")
                try:
                    if isinstance(_port_raw, float) and not _port_raw.is_integer():
                        raise ValueError(f"fractional port {_port_raw!r} — must be a whole number")
                    port = int(_port_raw) if _port_raw not in (None, "") else 9
                    if not (1 <= port <= 65535):
                        raise ValueError(f"port {port!r} out of range 1–65535")
                    try:
                        ipaddress.IPv4Address(broadcast)
                    except ValueError:
                        raise ValueError(f"invalid broadcast IP {broadcast!r}") from None
                    await asyncio.to_thread(_send_wol_packet, mac, broadcast, port)
                    hyst_wol["wol_prev_trigger"] = True
                    outputs[node.id]["sent"] = True
                    post_api_wol_nodes.add(node.id)
                    triggered_wol_nodes.add(node.id)
                    logger.info("Graph %s: WoL sent by node %s", graph_id[:8], node.id[:8])
                except Exception as exc:
                    logger.warning("Graph %s: WoL failed on node %s: %s", graph_id[:8], node.id[:8], type(exc).__name__)

        if post_api_wol_nodes:
            _add_resolved_outputs(post_api_wol_nodes)
            post_api_wol_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in post_api_wol_nodes:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    post_api_wol_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if post_api_wol_overrides:
                wol_base_overrides = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                post_api_wol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in wol_base_overrides.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    post_api_wol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in post_api_wol_overrides.items():
                    post_api_wol_merged.setdefault(nid, {}).update(vals)
                _pawol_hyst_snap = copy.deepcopy(hyst)
                post_api_wol_executor = GraphExecutor(flow, _pawol_hyst_snap, self._app_config)
                post_api_wol_outputs = post_api_wol_executor.execute(post_api_wol_merged, commit_memory=False)
                post_api_wol_descendants: set[str] = set()
                post_api_wol_queue = list(post_api_wol_nodes)
                while post_api_wol_queue:
                    nid = post_api_wol_queue.pop()
                    for e in flow.edges:
                        if e.source == nid and e.target not in post_api_wol_descendants:
                            post_api_wol_descendants.add(e.target)
                            post_api_wol_queue.append(e.target)
                wol_node_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in post_api_wol_outputs.items():
                    if nid not in wol_node_ids and nid in post_api_wol_descendants:
                        outputs[nid] = vals
                        if nid not in host_check_ids and nid in _pawol_hyst_snap:
                            hyst[nid] = _pawol_hyst_snap[nid]

                # HC nodes driven by post-api WoL output
                _pawol_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in post_api_wol_descendants and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, _pawol_hc, " (post-api-wol)")
                if _pawol_hc:
                    triggered_host_check_nodes.update(_pawol_hc)
                    _add_resolved_outputs(_pawol_hc)
                    _pawol_pending = set(_pawol_hc)
                    _pawol_processed: set[str] = set()
                    while _pawol_pending:
                        _pawol_replay_src = _pawol_pending - _pawol_processed
                        if not _pawol_replay_src:
                            break
                        _pawol_processed.update(_pawol_replay_src)
                        _pawol_dn_ovr: dict[str, dict[str, Any]] = {}
                        for _e in flow.edges:
                            if _e.source in _pawol_replay_src:
                                _pawol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                    outputs[_e.source], _e.sourceHandle or "out"
                                )
                        if not _pawol_dn_ovr:
                            continue
                        _pawol_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                        _pawol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _pawol_base.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            _pawol_merged.setdefault(nid, {}).update(vals)
                        for nid, vals in _pawol_dn_ovr.items():
                            _pawol_merged.setdefault(nid, {}).update(vals)
                        _pawol_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        _pawol_exec = GraphExecutor(flow, _pawol_hyst, self._app_config)
                        _pawol_out = _pawol_exec.execute(_pawol_merged, commit_memory=False)
                        _pawol_desc: set[str] = set()
                        _pawol_dq: list[str] = list(_pawol_replay_src)
                        while _pawol_dq:
                            _pn = _pawol_dq.pop()
                            for _e in flow.edges:
                                if _e.source == _pn and _e.target not in _pawol_desc:
                                    _pawol_desc.add(_e.target)
                                    _pawol_dq.append(_e.target)
                        for nid, vals in _pawol_out.items():
                            if nid in _pawol_desc and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in _pawol_hyst:
                                    hyst[nid] = _pawol_hyst[nid]
                        _apply_operating_hours_state(_pawol_desc, pre_execute_node_state)
                        _pawol_chained: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in _pawol_desc and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, _pawol_chained, " (post-api-wol replay)")
                        if _pawol_chained:
                            triggered_host_check_nodes.update(_pawol_chained)
                            _add_resolved_outputs(_pawol_chained)
                            _pawol_pending.update(_pawol_chained)

        post_api_hc_api_clients: set[str] = set()
        if post_api_hc_descendants:
            for node in flow.nodes:
                if node.type != "api_client" or node.id not in post_api_hc_descendants or node.id in triggered_api_clients:
                    continue
                out = outputs.get(node.id, {})
                if not GraphExecutor._to_bool(out.get("_trigger")):
                    continue
                variable_resolver = _make_api_client_variable_resolver(
                    self._registry,
                    node.data.get("variables"),
                    execution_values_by_datapoint_id,
                )
                try:
                    url = _replace_api_client_url_placeholders(
                        node.data.get("url") or "",
                        variable_resolver,
                    ).strip()
                    if not url:
                        continue
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                try:
                    request_urls, pinned_headers, request_extensions = _build_api_client_fetch_targets(url)
                except ValueError as exc:
                    logger.warning("Graph %s: blocked api_client target %s: %s", graph_id[:8], url, exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                method = (node.data.get("method", "GET") or "GET").upper()
                content_type = node.data.get("content_type", "application/json")
                resp_type = node.data.get("response_type", "application/json")
                verify_ssl = node.data.get("verify_ssl", True)
                if isinstance(verify_ssl, str):
                    verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
                timeout_s = float(node.data.get("timeout_s", 10) or 10)
                extra_headers: dict[str, str] = {}
                hdr_str = (node.data.get("headers") or "").strip()
                if hdr_str:
                    try:
                        extra_headers = _json.loads(hdr_str)
                    except Exception:
                        pass
                hdr_file = (node.data.get("headers_secret_file") or "").strip()
                if hdr_file:
                    try:
                        extra_headers = {
                            **extra_headers,
                            **_json.loads(_read_secret_file(hdr_file)),
                        }
                    except Exception:
                        pass
                try:
                    extra_headers = _replace_api_client_placeholders(extra_headers, variable_resolver)
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                auth_type = (node.data.get("auth_type") or "none").lower()
                auth: Any = None
                try:
                    if auth_type in ("basic", "digest"):
                        username = _replace_api_client_placeholders(
                            node.data.get("auth_username") or "",
                            variable_resolver,
                        ).strip()
                        password = _replace_api_client_placeholders(
                            node.data.get("auth_password") or "",
                            variable_resolver,
                        )
                        if username:
                            auth = httpx.BasicAuth(username, password) if auth_type == "basic" else httpx.DigestAuth(username, password)
                    elif auth_type == "bearer":
                        token = _replace_api_client_placeholders(
                            node.data.get("auth_token") or "",
                            variable_resolver,
                        ).strip()
                        if not token:
                            token = _replace_api_client_placeholders(
                                _read_secret_file(node.data.get("auth_token_file") or ""),
                                variable_resolver,
                            ).strip()
                        if token:
                            extra_headers = {
                                **extra_headers,
                                "Authorization": f"Bearer {token}",
                            }
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                try:
                    req_kwargs: dict[str, Any] = {
                        "headers": extra_headers,
                        "timeout": timeout_s,
                    }
                    if method in ("POST", "PUT", "PATCH"):
                        body = _replace_api_client_placeholders(out.get("_body"), variable_resolver)
                        if content_type == "application/json":
                            req_kwargs["content"] = _json.dumps(body) if not isinstance(body, (str, bytes)) else body
                            req_kwargs["headers"] = {
                                **extra_headers,
                                "Content-Type": "application/json",
                            }
                        elif content_type == "application/x-www-form-urlencoded":
                            req_kwargs["data"] = body if isinstance(body, dict) else {"data": str(body)}
                        else:
                            req_kwargs["content"] = str(body or "")
                            req_kwargs["headers"] = {
                                **extra_headers,
                                "Content-Type": "text/plain",
                            }
                    req_headers = {key: value for key, value in req_kwargs.get("headers", {}).items() if key.lower() != "host"}
                    req_kwargs["headers"] = {**req_headers, **pinned_headers}
                    if request_extensions:
                        req_kwargs["extensions"] = request_extensions
                    last_transport_error: Exception = ValueError(f"Could not fetch API target after trying {len(request_urls)} address(es)")
                    resp: httpx.Response | Any | None = None
                    async with httpx.AsyncClient(auth=auth, verify=verify_ssl) as client:
                        for request_url in request_urls:
                            try:
                                resp = await client.request(method, request_url, **req_kwargs)
                                break
                            except httpx.RequestError as req_exc:
                                last_transport_error = req_exc
                                if method not in _API_CLIENT_RETRYABLE_METHODS:
                                    break
                                continue
                    if resp is None:
                        raise last_transport_error
                    resp_text = resp.text
                    if len(resp_text) > 1_000_000:
                        resp_text = resp_text[:1_000_000]
                    if resp_type in ("json", "application/json"):
                        try:
                            resp_data: Any = resp.json()
                        except Exception:
                            resp_data = resp_text
                    else:
                        resp_data = resp_text
                    outputs[node.id].update(
                        {
                            "response": resp_data,
                            "status": resp.status_code,
                            "success": 200 <= resp.status_code < 300,
                        },
                    )
                    logger.info(
                        "Graph %s: API %s %s → %d",
                        graph_id[:8],
                        method,
                        url,
                        resp.status_code,
                    )
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                except Exception as exc:
                    logger.warning("Graph %s: api_client failed: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)

        if post_api_hc_api_clients:
            _add_resolved_outputs(post_api_hc_api_clients)
            api_descendants: set[str] = set()
            pending_sources = list(post_api_hc_api_clients)
            while pending_sources:
                source_id = pending_sources.pop()
                for e in flow.edges:
                    if e.source != source_id or e.target in api_descendants:
                        continue
                    api_descendants.add(e.target)
                    pending_sources.append(e.target)

            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in post_api_hc_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if downstream_overrides:
                replay_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                replay_overrides = {nid: dict(vals) for nid, vals in replay_base.items()}
                for nid, vals in downstream_overrides.items():
                    replay_overrides.setdefault(nid, {}).update(vals)
                for e in flow.edges:
                    if e.target not in api_descendants or e.source in api_descendants or e.source in post_api_hc_api_clients:
                        continue
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    replay_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs.get(e.source, {}), src_handle)
                replay_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                api_executor = GraphExecutor(flow, replay_hyst, self._app_config)
                api_outputs = api_executor.execute(replay_overrides, commit_memory=False)
                for nid, vals in api_outputs.items():
                    if nid not in api_client_ids and nid in api_descendants:
                        outputs[nid] = vals
                        if nid in replay_hyst:
                            hyst[nid] = replay_hyst[nid]
                _apply_operating_hours_state(api_descendants, pre_execute_node_state)
                final_api_triggered_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in api_descendants and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, final_api_triggered_hc, " (post-api api replay)")
                if final_api_triggered_hc:
                    triggered_host_check_nodes.update(final_api_triggered_hc)
                    _add_resolved_outputs(final_api_triggered_hc)
                    pending_final_api_hc_replay = set(final_api_triggered_hc)
                    processed_final_api_hc_replay: set[str] = set()
                    while pending_final_api_hc_replay:
                        replay_sources = pending_final_api_hc_replay - processed_final_api_hc_replay
                        if not replay_sources:
                            break
                        processed_final_api_hc_replay.update(replay_sources)
                        final_hc_descendants: set[str] = set()
                        final_hc_queue = list(replay_sources)
                        while final_hc_queue:
                            nid = final_hc_queue.pop()
                            for e in flow.edges:
                                if e.source == nid and e.target not in final_hc_descendants:
                                    final_hc_descendants.add(e.target)
                                    final_hc_queue.append(e.target)
                        final_hc_overrides: dict[str, dict[str, Any]] = {}
                        for e in flow.edges:
                            if e.source in replay_sources:
                                src_handle = e.sourceHandle or "out"
                                tgt_handle = e.targetHandle or "in"
                                final_hc_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(
                                    outputs[e.source],
                                    src_handle,
                                )
                        if not final_hc_overrides:
                            continue
                        final_hc_merged = {nid: dict(vals) for nid, vals in replay_overrides.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            final_hc_merged.setdefault(nid, {}).update(vals)
                        for nid, vals in final_hc_overrides.items():
                            final_hc_merged.setdefault(nid, {}).update(vals)
                        for e in flow.edges:
                            if e.target not in final_hc_descendants or e.source in final_hc_descendants or e.source in replay_sources:
                                continue
                            src_handle = e.sourceHandle or "out"
                            tgt_handle = e.targetHandle or "in"
                            final_hc_merged.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(
                                outputs.get(e.source, {}),
                                src_handle,
                            )
                        final_hc_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        final_hc_executor = GraphExecutor(flow, final_hc_hyst, self._app_config)
                        final_hc_outputs = final_hc_executor.execute(final_hc_merged, commit_memory=False)
                        for nid, vals in final_hc_outputs.items():
                            if nid in final_hc_descendants and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in final_hc_hyst:
                                    hyst[nid] = final_hc_hyst[nid]
                        _apply_operating_hours_state(final_hc_descendants, pre_execute_node_state)
                        chained_final_hc: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in final_hc_descendants and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, chained_final_hc, " (post-api api replay)")
                        if chained_final_hc:
                            triggered_host_check_nodes.update(chained_final_hc)
                            _add_resolved_outputs(chained_final_hc)
                            pending_final_api_hc_replay.update(chained_final_hc)

        # ── Final WoL pass ────────────────────────────────────────────────
        # The final HC replay (above) can set wake_on_lan._trigger=True for
        # WoL nodes that the earlier WoL loop never reached. Send those packets
        # so that chains like api_client→hc→api_client→wol complete in one tick.
        _final_wol_candidates: set[str] = set()
        for _fw_node in flow.nodes:
            if _fw_node.type != "wake_on_lan" or _fw_node.id in triggered_wol_nodes:
                continue
            _fw_out = outputs.get(_fw_node.id, {})
            _fw_hyst = hyst.setdefault(_fw_node.id, {})
            if not GraphExecutor._to_bool(_fw_out.get("_trigger")):
                _fw_hyst["wol_prev_trigger"] = False
                continue
            if _fw_hyst.get("wol_prev_trigger") and _fw_node.id not in cron_reachable:
                continue
            _fw_mac = (_fw_node.data.get("mac_address") or "").strip()
            if not _fw_mac:
                logger.warning("wake_on_lan: mac_address missing on node %s", _fw_node.id[:8])
                continue
            _fw_broadcast = (_fw_node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
            _fw_port_raw = _fw_node.data.get("port")
            try:
                if isinstance(_fw_port_raw, float) and not _fw_port_raw.is_integer():
                    raise ValueError(f"fractional port {_fw_port_raw!r}")
                _fw_port = int(_fw_port_raw) if _fw_port_raw not in (None, "") else 9
                if not (1 <= _fw_port <= 65535):
                    raise ValueError(f"port {_fw_port!r} out of range 1–65535")
                try:
                    ipaddress.IPv4Address(_fw_broadcast)
                except ValueError:
                    raise ValueError(f"invalid broadcast IP {_fw_broadcast!r}") from None
                await asyncio.to_thread(_send_wol_packet, _fw_mac, _fw_broadcast, _fw_port)
                _fw_hyst["wol_prev_trigger"] = True
                outputs[_fw_node.id]["sent"] = True
                _final_wol_candidates.add(_fw_node.id)
                triggered_wol_nodes.add(_fw_node.id)
                logger.info("Graph %s: WoL sent by node %s", graph_id[:8], _fw_node.id[:8])
            except Exception as exc:
                logger.warning("Graph %s: WoL failed on node %s: %s", graph_id[:8], _fw_node.id[:8], type(exc).__name__)
        if _final_wol_candidates:
            _add_resolved_outputs(_final_wol_candidates)
            _fwol_dn_ovr: dict[str, dict[str, Any]] = {}
            for _e in flow.edges:
                if _e.source in _final_wol_candidates:
                    _fwol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                        outputs[_e.source], _e.sourceHandle or "out"
                    )
            if _fwol_dn_ovr:
                _fwol_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                _fwol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _fwol_base.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    _fwol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in _fwol_dn_ovr.items():
                    _fwol_merged.setdefault(nid, {}).update(vals)
                _fwol_hyst_snap = copy.deepcopy(hyst)
                _fwol_exec = GraphExecutor(flow, _fwol_hyst_snap, self._app_config)
                _fwol_out = _fwol_exec.execute(_fwol_merged, commit_memory=False)
                _fwol_desc: set[str] = set()
                _fwol_q: list[str] = list(_final_wol_candidates)
                while _fwol_q:
                    _fn = _fwol_q.pop()
                    for _e in flow.edges:
                        if _e.source == _fn and _e.target not in _fwol_desc:
                            _fwol_desc.add(_e.target)
                            _fwol_q.append(_e.target)
                _fwol_wol_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in _fwol_out.items():
                    if nid not in _fwol_wol_ids and nid in _fwol_desc and nid not in triggered_api_clients:
                        outputs[nid] = vals
                        if nid not in host_check_ids and nid in _fwol_hyst_snap:
                            hyst[nid] = _fwol_hyst_snap[nid]
                _fwol_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in _fwol_desc and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, _fwol_hc, " (final-wol)")
                if _fwol_hc:
                    triggered_host_check_nodes.update(_fwol_hc)
                    _add_resolved_outputs(_fwol_hc)
                    _fwolhc_pending = set(_fwol_hc)
                    _fwolhc_processed: set[str] = set()
                    while _fwolhc_pending:
                        _fwolhc_srcs = _fwolhc_pending - _fwolhc_processed
                        if not _fwolhc_srcs:
                            break
                        _fwolhc_processed.update(_fwolhc_srcs)
                        _fwolhc_dn_ovr: dict[str, dict[str, Any]] = {}
                        for _e in flow.edges:
                            if _e.source in _fwolhc_srcs:
                                _fwolhc_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                    outputs[_e.source], _e.sourceHandle or "out"
                                )
                        if not _fwolhc_dn_ovr:
                            continue
                        _fwolhc_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                        _fwolhc_mrgd: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _fwolhc_base.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            _fwolhc_mrgd.setdefault(nid, {}).update(vals)
                        for nid, vals in _fwolhc_dn_ovr.items():
                            _fwolhc_mrgd.setdefault(nid, {}).update(vals)
                        _fwolhc_hyst = copy.deepcopy(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        _fwolhc_exec = GraphExecutor(flow, _fwolhc_hyst, self._app_config)
                        _fwolhc_out = _fwolhc_exec.execute(_fwolhc_mrgd, commit_memory=False)
                        _fwolhc_desc: set[str] = set()
                        _fwolhc_dq: list[str] = list(_fwolhc_srcs)
                        while _fwolhc_dq:
                            _fn = _fwolhc_dq.pop()
                            for _e in flow.edges:
                                if _e.source == _fn and _e.target not in _fwolhc_desc:
                                    _fwolhc_desc.add(_e.target)
                                    _fwolhc_dq.append(_e.target)
                        for nid, vals in _fwolhc_out.items():
                            if nid in _fwolhc_desc and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in _fwolhc_hyst:
                                    hyst[nid] = _fwolhc_hyst[nid]
                        _apply_operating_hours_state(_fwolhc_desc, pre_execute_node_state)
                        _fwolhc_chained: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in _fwolhc_desc and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, _fwolhc_chained, " (final-wol-hc)")
                        if _fwolhc_chained:
                            triggered_host_check_nodes.update(_fwolhc_chained)
                            _add_resolved_outputs(_fwolhc_chained)
                            _fwolhc_pending.update(_fwolhc_chained)

        # ── Handle message_archive ────────────────────────────────────────────
        triggered_message_archive_nodes: set[str] = set()

        async def _run_message_archive_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False

            archive_id = (node.data.get("archive_id") or "").strip().lower()
            if not archive_id:
                logger.warning("Message archive: archive_id missing on node %s", node.id[:8])
                return False

            _raw_msg = out.get("_message")
            msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
            _raw_title = out.get("_title")
            title = _msg_to_str(_raw_title) if _raw_title is not None else str(node.data.get("title") or "")
            message_type = str(node.data.get("type") or "automation")
            severity = str(node.data.get("severity") or "info")

            try:
                from obs.message_archive import get_message_archive_service

                payload = dict(graph_id=graph_id, graph_name=name, node_id=node.id, node_label=node.data.get("label") or node.data.get("name") or "")
                source = f"logic.graph.{graph_id}.node.{node.id}"
                record_kwargs = dict(type=message_type, severity=severity, source=source, title=title, message=msg, payload=payload)
                await get_message_archive_service().record(archive_id, **record_kwargs)
                outputs[node.id]["stored"] = True
                target_set.add(node.id)
                logger.info("Graph %s: message archived in %s (msg=%r)", graph_id[:8], archive_id, msg[:40])
                return True
            except Exception as exc:
                logger.warning("Graph %s: message archive write failed (node=%s): %s", graph_id[:8], node.id[:8], exc)
                return False

        triggered_notify_nodes: set[str] = set()

        async def _run_notify_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False

            if node.type == "notify_pushover":
                app_token = (node.data.get("app_token") or "").strip()
                user_key = (node.data.get("user_key") or "").strip()
                if not app_token or not user_key:
                    logger.warning("Pushover: app_token or user_key missing on node %s", node.id[:8])
                    return False
                _raw_msg = out.get("_message")
                msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
                title = node.data.get("title", "open bridge server")
                prio = int(node.data.get("priority", 0))
                _out_url = out.get("_url")
                _out_utit = out.get("_url_title")
                _out_img = out.get("_image_url")
                url = (_msg_to_str(_out_url) if _out_url is not None else (node.data.get("url") or "")).strip()
                url_title = (_msg_to_str(_out_utit) if _out_utit is not None else (node.data.get("url_title") or "")).strip()
                image_url = (_msg_to_str(_out_img) if _out_img is not None else (node.data.get("image_url") or "")).strip()
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        payload: dict[str, object] = {
                            "token": app_token,
                            "user": user_key,
                            "title": str(title),
                            "message": msg,
                            "priority": prio,
                        }
                        if url:
                            payload["url"] = url
                        if url_title:
                            payload["url_title"] = url_title

                        if image_url:
                            resolved = await _resolve_safe_image_url(image_url)
                            if resolved is None:
                                raise ValueError("Unsafe image_url: only validated HTTPS targets are allowed")
                            pinned_url, host_header, pinned_ip = resolved
                            async with client.stream(
                                "GET",
                                pinned_url,
                                timeout=10.0,
                                follow_redirects=False,
                                headers={"Host": host_header},
                                extensions={"sni_hostname": host_header.split(":", 1)[0]},
                            ) as img_r:
                                net_stream = img_r.extensions.get("network_stream")
                                if net_stream is not None:
                                    server_addr = net_stream.get_extra_info("server_addr")
                                    if server_addr and server_addr[0] != pinned_ip:
                                        raise ValueError("Pushover image_url resolved to an unexpected target IP")
                                img_r.raise_for_status()
                                content_type = img_r.headers.get("content-type", "").split(";")[0].strip().lower()
                                if not content_type.startswith("image/"):
                                    raise ValueError("Pushover image_url must return an image/* content type")

                                content_len_raw = img_r.headers.get("content-length", "0") or "0"
                                try:
                                    content_len = int(content_len_raw)
                                except ValueError:
                                    content_len = 0
                                if content_len > _PUSHOVER_ATTACHMENT_MAX_BYTES:
                                    raise ValueError("Pushover attachment too large (max 5 MB)")

                                img_content = bytearray()
                                async for chunk in img_r.aiter_bytes():
                                    img_content.extend(chunk)
                                    if len(img_content) > _PUSHOVER_ATTACHMENT_MAX_BYTES:
                                        raise ValueError("Pushover attachment too large (max 5 MB)")

                            fname = image_url.split("?")[0].split("/")[-1] or "image.jpg"
                            r = await client.post(
                                "https://api.pushover.net/1/messages.json",
                                data=payload,
                                files={"attachment": (fname, bytes(img_content), content_type or "image/jpeg")},
                            )
                        else:
                            r = await client.post(
                                "https://api.pushover.net/1/messages.json",
                                data=payload,
                            )
                        r.raise_for_status()
                        outputs[node.id]["sent"] = True
                        target_set.add(node.id)
                        logger.info("Graph %s: Pushover sent (msg=%r)", graph_id[:8], msg[:40])
                        return True
                except Exception as exc:
                    logger.warning(
                        "Graph %s: Pushover failed (msg=%r): %s",
                        graph_id[:8],
                        msg[:40],
                        exc,
                    )
                    return False

            if node.type == "notify_sms":
                api_key = (node.data.get("api_key") or "").strip()
                to = (node.data.get("to") or "").strip()
                if not api_key or not to:
                    logger.warning("seven.io SMS: api_key or to missing on node %s", node.id[:8])
                    return False
                _raw_msg = out.get("_message")
                msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
                sender = node.data.get("sender", "obs")
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        r = await client.post(
                            "https://gateway.seven.io/api/sms",
                            headers={"X-Api-Key": api_key},
                            data={"to": to, "from": str(sender), "text": msg},
                        )
                        r.raise_for_status()
                        body = r.text.strip()
                        logger.info(
                            "Graph %s: seven.io response status=%d body=%r",
                            graph_id[:8],
                            r.status_code,
                            body[:80],
                        )
                        _SEVEN_ERRORS = {
                            100: "Unbekannter Fehler / Empfänger nicht angegeben",
                            200: "Absender nicht angegeben",
                            201: "Absender zu lang (max 11 Zeichen)",
                            300: "Nachricht nicht angegeben",
                            301: "Nachricht zu lang",
                            401: "API-Key ungültig oder nicht autorisiert",
                            402: "Nicht genug Guthaben",
                            403: "Absender nicht erlaubt",
                            500: "Server-Fehler bei seven.io",
                        }
                        try:
                            body_int = int(body)
                            if body_int in _SEVEN_ERRORS:
                                raise ValueError(f"seven.io Fehlercode {body_int}: {_SEVEN_ERRORS[body_int]}")
                            if body_int <= 0:
                                raise ValueError(f"seven.io: 0 Nachrichten gesendet (body={body!r})")
                        except ValueError:
                            raise
                        except TypeError:
                            pass
                        outputs[node.id]["sent"] = True
                        target_set.add(node.id)
                        logger.info(
                            "Graph %s: seven.io SMS sent to %s (msg=%r)",
                            graph_id[:8],
                            to,
                            msg[:40],
                        )
                        return True
                except Exception as exc:
                    logger.warning(
                        "Graph %s: seven.io SMS failed (msg=%r): %s",
                        graph_id[:8],
                        msg[:40],
                        exc,
                    )
                    return False

            return False

        async def _run_replay_triggered_side_effects(candidate_ids: set[str]) -> None:
            def _triggered_side_effect_ids() -> set[str]:
                return (
                    triggered_message_archive_nodes
                    | triggered_notify_nodes
                    | triggered_api_clients
                    | triggered_wol_nodes
                    | triggered_host_check_nodes
                )

            pending_candidates = set(candidate_ids)
            while pending_candidates:
                newly_triggered: set[str] = set()
                for node in flow.nodes:
                    if node.id not in pending_candidates:
                        continue
                    if node.type == "host_check" and node.id not in triggered_host_check_nodes:
                        if await _run_host_check_node(node, newly_triggered, " (message-archive replay)"):
                            triggered_host_check_nodes.add(node.id)
                    elif node.type == "wake_on_lan" and node.id not in triggered_wol_nodes:
                        if await _run_wake_on_lan_node(node, newly_triggered):
                            triggered_wol_nodes.add(node.id)
                    elif node.type == "api_client" and node.id not in triggered_api_clients:
                        if await _run_api_client_node(node, newly_triggered):
                            triggered_api_clients.add(node.id)
                    elif node.type == "message_archive" and node.id not in triggered_message_archive_nodes:
                        if await _run_message_archive_node(node, newly_triggered):
                            triggered_message_archive_nodes.add(node.id)
                    elif node.type in {"notify_pushover", "notify_sms"} and node.id not in triggered_notify_nodes:
                        if await _run_notify_node(node, newly_triggered) or GraphExecutor._to_bool(outputs.get(node.id, {}).get("_trigger")):
                            triggered_notify_nodes.add(node.id)
                if not newly_triggered:
                    break
                _add_resolved_outputs(newly_triggered)
                pending_candidates = _replay_async_descendants(
                    newly_triggered,
                    skip_node_ids=_triggered_side_effect_ids(),
                )

        for node in flow.nodes:
            if node.type != "message_archive":
                continue
            await _run_message_archive_node(node, triggered_message_archive_nodes)
        if triggered_message_archive_nodes:
            _add_resolved_outputs(triggered_message_archive_nodes)
            message_archive_descendants = _replay_async_descendants(
                triggered_message_archive_nodes,
                skip_node_ids=triggered_message_archive_nodes
                | triggered_notify_nodes
                | triggered_api_clients
                | triggered_wol_nodes
                | triggered_host_check_nodes,
            )
            await _run_replay_triggered_side_effects(message_archive_descendants)

        # ── Handle notify_pushover ────────────────────────────────────────
        # Runs AFTER api_client second-pass so that graphs with api_client →
        # json_extractor → notify see the real HTTP response, not placeholders.
        for node in flow.nodes:
            if node.type != "notify_pushover":
                continue
            if node.id in triggered_notify_nodes:
                continue
            await _run_notify_node(node, triggered_notify_nodes)

        # ── Handle notify_sms ─────────────────────────────────────────────
        for node in flow.nodes:
            if node.type != "notify_sms":
                continue
            if node.id in triggered_notify_nodes:
                continue
            await _run_notify_node(node, triggered_notify_nodes)

        if triggered_notify_nodes:
            _add_resolved_outputs(triggered_notify_nodes)
            notify_descendants = _replay_async_descendants(
                triggered_notify_nodes,
                skip_node_ids=triggered_message_archive_nodes
                | triggered_notify_nodes
                | triggered_api_clients
                | triggered_wol_nodes
                | triggered_host_check_nodes,
            )
            await _run_replay_triggered_side_effects(notify_descendants)

        # Deferred hc_prev_trigger=False: clear only for HC nodes that did NOT
        # fire in any async pass. Clearing inside _run_host_check_node was wrong
        # for async-driven triggers (e.g. api_client.success→hc._trigger) because
        # the first executor pass uses placeholder success=False → _trigger=False,
        # but after the post-api pass the real trigger may be True. By deferring
        # to here, triggered_host_check_nodes is final.
        for node in flow.nodes:
            if node.type == "host_check" and node.id not in triggered_host_check_nodes:
                hyst.setdefault(node.id, {})["hc_prev_trigger"] = False

        # Memory is the explicit tick boundary for feedback loops. Commit it
        # after all async node re-propagation so the stored value always reflects
        # the final graph outputs, not executor placeholders from an earlier pass.
        executor.commit_memory_inputs(outputs, aug_overrides)

        # ── Start/cancel value sequences ──────────────────────────────────
        wired_inputs: set[tuple[str, str]] = {(e.target, e.targetHandle or "in") for e in flow.edges}
        node_by_id = {node.id: node for node in flow.nodes}
        pending_sequence_starts: list[tuple[Any, bool]] = []
        for node in flow.nodes:
            if node.type != "value_sequence":
                continue
            output = outputs.get(node.id, {})
            key = (graph_id, node.id)
            condition = GraphExecutor._to_bool(output.get("_condition")) if (node.id, "condition") in wired_inputs else True
            self._sequence_conditions[key] = condition
            active = self._sequence_tasks.get(key)
            if (
                node.data.get("cancel_when_condition_false")
                and not condition
                and active
                and not active.done()
                and active is not asyncio.current_task()
            ):
                self._cancel_sequence_task(key)
            state = graph_state.setdefault(node.id, {})
            triggered = GraphExecutor._to_bool(output.get("_triggered"))
            # A wired condition gates every sequence mode.  The cancellation
            # setting controls only whether an already-running task is stopped.
            blocked = not condition
            if blocked:
                state["sequence_prev_trigger"] = False
                continue
            was_triggered = state.get("sequence_prev_trigger", False)
            state["sequence_prev_trigger"] = triggered
            cron_triggered = any(
                edge.target == node.id and (edge.targetHandle or "in") == "trigger" and edge.source in cron_reachable for edge in flow.edges
            )
            pulse_sources = [node.id]
            pulse_seen: set[str] = set()
            datapoint_change_triggered = False
            while pulse_sources:
                target_id = pulse_sources.pop()
                if target_id in pulse_seen:
                    continue
                pulse_seen.add(target_id)
                for edge in flow.edges:
                    if edge.target != target_id:
                        continue
                    source = node_by_id.get(edge.source)
                    if (
                        source
                        and source.type == "datapoint_read"
                        and (edge.sourceHandle or "out") == "changed"
                        and GraphExecutor._to_bool(outputs.get(source.id, {}).get("changed"))
                    ):
                        datapoint_change_triggered = True
                        break
                    pulse_sources.append(edge.source)
                if datapoint_change_triggered:
                    break
            if triggered and (not was_triggered or cron_triggered or datapoint_change_triggered):
                # Defer creating the task until ordinary datapoint writes have
                # been published below.  A task created here can otherwise run
                # at the write loop's first await and invert graph-local order.
                pending_sequence_starts.append((node, condition))

        # ── Process datapoint_write outputs — apply trigger gating + write-side filters,
        # then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get notified.
        await self._apply_datapoint_write_outputs(graph_id, flow, outputs, graph_state, wired_inputs, execute_now, logic_depth)

        # Value sequences are intentionally started after synchronous graph
        # writes, so an execution that triggers both has deterministic order.
        for node, condition in pending_sequence_starts:
            if not graph_state.get(node.id, {}).get("sequence_prev_trigger", False):
                continue
            current_condition = self._sequence_conditions.get((graph_id, node.id), condition)
            if current_condition:
                self._start_value_sequence(graph_id, node, current_condition, logic_depth, flow.model_dump_json())

        # ── Persist node state (statistics / hysteresis) to DB ───────────
        await self._persist_node_state(graph_id)

        # ── Broadcast final execution results to all WS clients ──────────
        # Broadcast happens here — after all async ops (api_client HTTP calls,
        # second-pass re-execution, etc.) — so the debug view shows the real
        # success/response values and not the executor's initial placeholders.
        try:
            from obs.api.v1.websocket import get_ws_manager

            def _safe(v: Any) -> Any:
                if v is None or isinstance(v, (bool, int, float, str)):
                    return v
                return str(v)

            safe_outputs = {nid: {k: _safe(val) for k, val in node_out.items()} for nid, node_out in outputs.items() if isinstance(node_out, dict)}
            await get_ws_manager().broadcast(
                {
                    "action": "logic_run",
                    "graph_id": graph_id,
                    "outputs": safe_outputs,
                },
            )
        except Exception:
            pass  # WS not ready or no clients — non-critical

        return outputs

    # ── Cache ─────────────────────────────────────────────────────────────

    async def _persist_node_state(self, graph_id: str) -> None:
        """Persist node state (statistics / hysteresis) to the DB.

        Nodes with persist_state=False are excluded from the saved snapshot
        so their accumulators reset on server restart (opt-out behaviour).
        """
        hyst = self._hysteresis.get(graph_id)
        if not hyst:
            return
        try:
            graph_entry = self._graphs.get(graph_id)
            if graph_entry:
                _, _, _flow = graph_entry
                no_persist = {n.id for n in _flow.nodes if n.data.get("persist_state") is False}
                state_to_save = {nid: s for nid, s in hyst.items() if nid not in no_persist}
            else:
                state_to_save = hyst
            await self._db.execute_and_commit(
                "UPDATE logic_graphs SET node_state = ? WHERE id = ?",
                (json.dumps(state_to_save), graph_id),
            )
        except Exception as exc:
            logger.warning("Graph %s: failed to persist node_state: %s", graph_id[:8], exc)

    async def _apply_datapoint_write_outputs(
        self,
        graph_id: str,
        flow: FlowData,
        outputs: dict[str, dict[str, Any]],
        graph_state: dict[str, Any],
        wired_inputs: set[tuple[str, str]],
        write_now: datetime,
        logic_depth: int,
        *,
        skip_node_ids: set[str] | frozenset[str] = frozenset(),
        initialization: bool = False,
    ) -> set[str]:
        """Apply trigger gating + write-side filters to datapoint_write outputs,
        then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get
        notified. skip_node_ids excludes individual write nodes (used by
        initialize_graph for writes descending from unseeded Read Objects);
        initialization marks the events as save-time seeding so notification
        subscribers do not react to them. Returns the ids of the write nodes
        whose event was actually published.
        """
        from obs.core.event_bus import DataValueEvent

        published: set[str] = set()
        for node in flow.nodes:
            if node.type != "datapoint_write" or node.id in skip_node_ids:
                continue
            node_out = outputs.get(node.id, {})
            write_val = node_out.get("_write_value")

            # ── Trigger gating ───────────────────────────────────────────
            # If the trigger handle is wired, only write when trigger is truthy.
            if (node.id, "trigger") in wired_inputs:
                triggered = node_out.get("_triggered")
                if not GraphExecutor._to_bool(triggered):
                    continue

            if write_val is None:
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                continue

            ns = graph_state.setdefault(node.id, {})
            if not self._write_filters_allow(node.data, ns, write_val, write_now):
                continue

            # All filters passed — update state and publish
            ns["last_write_val"] = write_val
            ns["last_write_ts"] = write_now
            try:
                dp_id = uuid.UUID(dp_id_str)
                event = DataValueEvent(
                    datapoint_id=dp_id,
                    value=write_val,
                    quality="good",
                    source_adapter="logic",
                    logic_depth=logic_depth + 1,
                    initialization=initialization,
                )
                await self._event_bus.publish(event)
                published.add(node.id)
                logger.debug("Graph %s: wrote dp %s = %s", graph_id, dp_id_str, write_val)
            except Exception as exc:
                logger.warning("Graph %s: failed to write dp %s: %s", graph_id, dp_id_str, exc)
        return published

    @staticmethod
    def _write_filters_allow(d: dict[str, Any], ns: dict[str, Any], write_val: Any, write_now: datetime) -> bool:
        """Write-side only_on_change / min_delta / throttle filters.

        Pure predicate against the node's filter state — shared by the
        publish path and the initialization settle pass, which must not feed
        values downstream that these filters would suppress.
        """
        last_wr = ns.get("last_write_val")
        last_ts = ns.get("last_write_ts")

        ooc = d.get("only_on_change")
        if (ooc is True or ooc == "true") and write_val == last_wr:
            return False

        raw_delta = d.get("min_delta")
        if raw_delta not in (None, "", 0) and last_wr is not None:
            try:
                if abs(float(write_val) - float(last_wr)) < float(raw_delta):
                    return False
            except (TypeError, ValueError):
                pass

        tv = d.get("throttle_value")
        if tv not in (None, "", 0) and last_ts is not None:
            try:
                unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                if (write_now - last_ts).total_seconds() * 1000 < float(tv) * unit_ms:
                    return False
            except (TypeError, ValueError):
                pass
        return True

    async def _load_graphs(self) -> None:
        rows = await self._db.fetchall("SELECT id, name, enabled, flow_data, node_state FROM logic_graphs")
        self._graphs = {}
        for row in rows:
            try:
                raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
                flow = FlowData.model_validate(raw)
                self._graphs[row["id"]] = (row["name"], bool(row["enabled"]), flow)

                # Restore persisted node state (statistics, hysteresis, …) from DB,
                # but only when there is no in-memory state already — so a reload()
                # triggered by a graph save does NOT overwrite the live accumulators.
                if row["id"] not in self._hysteresis:
                    try:
                        saved = json.loads(row["node_state"] or "{}")
                        if isinstance(saved, dict) and saved:
                            self._hysteresis[row["id"]] = saved
                            logger.debug(
                                "Graph %s: restored node_state (%d nodes)",
                                row["id"][:8],
                                len(saved),
                            )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Failed to parse graph %s: %s", row["id"], exc)

    def invalidate_cache(self, graph_id: str) -> None:
        self._graphs.pop(graph_id, None)
        # NOTE: _hysteresis is intentionally NOT cleared here.
        # When a graph is saved (PUT/PATCH), invalidate_cache + reload() are called.
        # Clearing _hysteresis would reset statistics accumulators on every save.
        # The state is re-used by the next execution after reload.
        # On DELETE the graph row is gone from DB so no persistence concerns remain;
        # the in-memory entry is a no-op and will be GC'd naturally.
        self._node_state.pop(graph_id, None)
        self._cancel_sequence_tasks(graph_id)
        # Cancel cron tasks for this specific graph
        to_remove = [k for k in list(self._cron_tasks) if k[0] == graph_id]
        for k in to_remove:
            self._cron_tasks[k].cancel()
            del self._cron_tasks[k]

    def update_cached_graph_name(self, graph_id: str, name: str) -> None:
        """Refresh metadata without invalidating active graph execution."""
        graph = self._graphs.get(graph_id)
        if graph:
            _, enabled, flow = graph
            self._graphs[graph_id] = (name, enabled, flow)

    def update_cached_graph(self, graph_id: str, name: str, enabled: bool, flow: FlowData) -> None:
        """Apply a layout-only save without interrupting active sequences."""
        if graph_id in self._graphs:
            self._graphs[graph_id] = (name, enabled, flow)
            self._sequence_graph_signatures[graph_id] = flow.model_dump_json()
