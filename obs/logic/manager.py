"""LogicManager — manages all logic graphs and integrates with the EventBus.

- Subscribes to DataValueEvents
- Triggers graphs whose datapoint_read nodes watch the changed DataPoint
- Executes the graph and writes outputs back via the registry
- Schedules timer_cron nodes via asyncio tasks (requires croniter)
"""

from __future__ import annotations

import asyncio
import base64
import email.utils
import http.cookies
import ipaddress
import json
import logging
import os
import stat
import uuid
from datetime import UTC, datetime
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

_ICAL_MAX_BYTES = 1_048_576
_ICAL_MAX_REDIRECTS = 5
_ICAL_ALLOWED_CONTENT_TYPES = ("text/calendar", "application/ics", "application/octet-stream", "text/plain")
_PUSHOVER_ATTACHMENT_MAX_BYTES = 5_000_000
_SECRET_FILE_MAX_BYTES = 8192
_SECRET_FILE_DEFAULT_ROOT = "/run/secrets"
_API_CLIENT_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}


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
        # cron tasks: (graph_id, node_id) → asyncio.Task
        self._cron_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
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

    async def reload(self) -> None:
        """Reload graph cache from DB and restart cron schedulers."""
        for task in list(self._cron_tasks.values()):
            task.cancel()
        self._cron_tasks.clear()
        await self._load_graphs()
        self._start_cron_tasks()

    # ── App Config ────────────────────────────────────────────────────────

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
            outputs = executor.execute(aug_overrides)
        except Exception as exc:
            logger.error("Graph %s (%s) execution error: %s", graph_id, name, exc)
            return {}

        # ── Update operating_hours state ─────────────────────────────────
        for node in flow.nodes:
            if node.type != "operating_hours":
                continue
            out = outputs.get(node.id, {})
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

        # ── Handle api_client ─────────────────────────────────────────────
        # Track which api_client nodes completed an HTTP call so we can
        # re-propagate their real outputs to downstream nodes afterwards.
        triggered_api_clients: set[str] = set()
        import json as _json  # noqa: PLC0415

        for node in flow.nodes:
            if node.type != "api_client":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            url = (node.data.get("url") or "").strip()
            if not url:
                continue
            try:
                request_urls, pinned_headers, request_extensions = _build_api_client_fetch_targets(url)
            except ValueError as exc:
                logger.warning("Graph %s: blocked api_client target %s: %s", graph_id[:8], url, exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
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
            body = out.get("_body")
            # ── Authentication ──────────────────────────────────────────
            auth_type = (node.data.get("auth_type") or "none").lower()
            auth: Any = None
            if auth_type in ("basic", "digest"):
                username = (node.data.get("auth_username") or "").strip()
                password = (node.data.get("auth_password") or "").strip()
                if username:
                    auth = httpx.BasicAuth(username, password) if auth_type == "basic" else httpx.DigestAuth(username, password)
            elif auth_type == "bearer":
                token = (node.data.get("auth_token") or "").strip()
                if not token:
                    token = _read_secret_file(node.data.get("auth_token_file") or "")
                if token:
                    extra_headers = {
                        **extra_headers,
                        "Authorization": f"Bearer {token}",
                    }
            try:
                req_kwargs: dict[str, Any] = {
                    "headers": extra_headers,
                    "timeout": timeout_s,
                }
                if method in ("POST", "PUT", "PATCH"):
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
                triggered_api_clients.add(node.id)
            except Exception as exc:
                logger.warning("Graph %s: api_client failed: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})

        # ── Re-propagate api_client outputs to downstream nodes ───────────
        # The first executor pass computed downstream nodes with the placeholder
        # success=False. Now that we have the real HTTP results, we re-run the
        # executor for those downstream nodes using input overrides so their
        # outputs (and downstream datapoint writes, etc.) reflect the real values.
        if triggered_api_clients:
            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in triggered_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if downstream_overrides:
                second_executor = GraphExecutor(flow, hyst, self._app_config)
                second_outputs = second_executor.execute(downstream_overrides)
                api_client_ids = {n.id for n in flow.nodes if n.type == "api_client"}
                for nid, vals in second_outputs.items():
                    if nid not in api_client_ids:
                        outputs[nid] = vals

        # ── Handle notify_pushover ────────────────────────────────────────
        # Runs AFTER api_client second-pass so that graphs with api_client →
        # json_extractor → notify see the real HTTP response, not placeholders.
        for node in flow.nodes:
            if node.type != "notify_pushover":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            app_token = (node.data.get("app_token") or "").strip()
            user_key = (node.data.get("user_key") or "").strip()
            if not app_token or not user_key:
                logger.warning("Pushover: app_token or user_key missing on node %s", node.id[:8])
                continue
            _raw_msg = out.get("_message")
            msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
            title = node.data.get("title", "open bridge server")
            prio = int(node.data.get("priority", 0))
            # Input port value takes precedence over static config
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
                        # Stream attachment bytes and enforce max size while downloading.
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
                    logger.info("Graph %s: Pushover sent (msg=%r)", graph_id[:8], msg[:40])
            except Exception as exc:
                logger.warning(
                    "Graph %s: Pushover failed (msg=%r): %s",
                    graph_id[:8],
                    msg[:40],
                    exc,
                )

        # ── Handle notify_sms ─────────────────────────────────────────────
        for node in flow.nodes:
            if node.type != "notify_sms":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            api_key = (node.data.get("api_key") or "").strip()
            to = (node.data.get("to") or "").strip()
            if not api_key or not to:
                logger.warning("seven.io SMS: api_key or to missing on node %s", node.id[:8])
                continue
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
                    # seven.io returns the number of sent messages as body (e.g. "1").
                    # A value of "0" means failure (no credits, invalid number, etc.)
                    # even though the HTTP status is 200.
                    body = r.text.strip()
                    logger.info(
                        "Graph %s: seven.io response status=%d body=%r",
                        graph_id[:8],
                        r.status_code,
                        body[:80],
                    )
                    # seven.io returns the number of sent messages on success (e.g. "1"),
                    # or a numeric error code on failure. Known error codes:
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
                        raise  # re-raise error code or zero-count errors
                    except TypeError:
                        pass  # non-numeric body → assume success (future API changes)
                    outputs[node.id]["sent"] = True
                    logger.info(
                        "Graph %s: seven.io SMS sent to %s (msg=%r)",
                        graph_id[:8],
                        to,
                        msg[:40],
                    )
            except Exception as exc:
                logger.warning(
                    "Graph %s: seven.io SMS failed (msg=%r): %s",
                    graph_id[:8],
                    msg[:40],
                    exc,
                )

        # ── Process datapoint_write outputs — apply trigger gating + write-side filters,
        # then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get notified.
        from obs.core.event_bus import DataValueEvent

        write_now = execute_now

        # Build set of node+handle pairs that have an incoming edge (= are wired)
        wired_inputs: set[tuple[str, str]] = {(e.target, e.targetHandle or "in") for e in flow.edges}

        for node in flow.nodes:
            if node.type != "datapoint_write":
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

            d = node.data
            ns = graph_state.setdefault(node.id, {})
            last_wr = ns.get("last_write_val")
            last_ts = ns.get("last_write_ts")

            # ── Filter: only_on_change ───────────────────────────────────
            ooc = d.get("only_on_change")
            if ooc is True or ooc == "true":
                if write_val == last_wr:
                    continue

            # ── Filter: min_delta (write side) ───────────────────────────
            raw_delta = d.get("min_delta")
            if raw_delta not in (None, "", 0) and last_wr is not None:
                try:
                    if abs(float(write_val) - float(last_wr)) < float(raw_delta):
                        continue
                except (TypeError, ValueError):
                    pass

            # ── Filter: throttle (value + unit, write side) ───────────────
            tv = d.get("throttle_value")
            if tv not in (None, "", 0) and last_ts is not None:
                try:
                    unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                    throttle_ms = float(tv) * unit_ms
                    elapsed_ms = (write_now - last_ts).total_seconds() * 1000
                    if elapsed_ms < throttle_ms:
                        continue
                except (TypeError, ValueError):
                    pass

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
                )
                await self._event_bus.publish(event)
                logger.debug("Graph %s: wrote dp %s = %s", graph_id, dp_id_str, write_val)
            except Exception as exc:
                logger.warning("Graph %s: failed to write dp %s: %s", graph_id, dp_id_str, exc)

        # ── Persist node state (statistics / hysteresis) to DB ───────────
        # Nodes with persist_state=False are excluded from the saved snapshot
        # so their accumulators reset on server restart (opt-out behaviour).
        hyst = self._hysteresis.get(graph_id)
        if hyst:
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
        # Cancel cron tasks for this specific graph
        to_remove = [k for k in list(self._cron_tasks) if k[0] == graph_id]
        for k in to_remove:
            self._cron_tasks[k].cancel()
            del self._cron_tasks[k]
