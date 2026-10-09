"""WEBHOOK Adapter — incoming HTTP calls as a SOURCE binding (issue #1256).

Many devices can only call a URL when something happens — no custom headers, no
request body, no MQTT.  A door station (the issue's example: Wantec Monolith B)
calls a configured URL when the bell is pressed.  This adapter turns such a call
into a ``DataValueEvent`` for one DataPoint, which then reaches KNX, the Visu and
the Logic engine through that DataPoint's other (DEST) bindings — exactly like
any other SOURCE binding.

Two separate concerns, deliberately kept apart:

* **Managing** a binding (creating it, rotating its token) is an ordinary
  ``config_mutation`` performed by a user principal through ``/api/v1/...`` and
  authorized by the regular RBAC rules.  ``WEBHOOK`` declares **no**
  ``AdapterDelegationCapability``, so an API-key principal can never create or
  rotate a webhook binding — the reasoning in the issue ("a compromised device
  can only ring the bell") must not accidentally cover the management side too.
* **Triggering** a binding is done by a device that has no principal at all.
  The template for that is the anonymous Visu write path in
  ``obs/api/v1/datapoints.py``: not a principal, but a narrow secret bound to
  one resource.  The per-binding token authorizes exactly the one DataPoint its
  binding was created for, with exactly the configured value mapping.

Because the trigger call carries no principal it is intentionally invisible to
``tools/check_authz_contract.py``, which walks ``/api/v1`` routes and checks
principal-based authorization.  Its defences are instead: 256-bit tokens,
constant-time comparison, an optional ingress CIDR allowlist, a per-IP rate
limit, and a 404 that does not distinguish an unknown slug from a wrong token.
A webhook binding may only point at a ``room_local`` DataPoint — the same
boundary the anonymous Visu path draws for ``central_plant`` — and that is
re-checked on every call, not just when the binding is created.

The ingress allowlist lives on the **binding**, not on the instance: which
addresses may call is a property of the one device that owns a slug, not of the
endpoint as a whole. An empty list means no restriction for that binding. The
instance keeps only what is genuinely endpoint-wide — where it listens, how the
client address is determined, and how often any one address may call.

Adapter configuration (``adapter_instances.config``):
  path_prefix:          str    URL prefix the instance claims  (default: "/hook")
  trust_forwarded_for:  bool   Read the client IP from X-Forwarded-For (default: False)
  rate_limit_per_minute: int   Accepted calls per client IP per minute (default: 60)

Binding configuration (``adapter_bindings.config``):
  slug:             str    Path segment, e.g. "haustuer-klingel"
  token:            str    Server-generated secret; never accepted from a client
  methods:          list   Any of ["GET", "POST"]                  (default: ["GET"])
  allowed_networks: list   CIDRs/IPs for this binding; empty = any (default: [])
  value_source:     str    "fixed" | "request"                     (default: "fixed")
  fixed_value:      str    Value for value_source == "fixed"       (default: "true")
  value_param:      str    Query parameter / JSON field name       (default: "value")
  debounce_ms:      int    Ignore repeat calls within this window  (default: 0)
  autoreset:        bool   Publish a reset value after the trigger (default: False)
  autoreset_value:  str    The value to publish back               (default: "false")
  autoreset_delay_ms: int  How long the triggered value stands     (default: 1000)

**Auto-reset** turns a webhook into a trigger: the call publishes the configured
value, and after the delay the adapter publishes the reset value by itself, so a
doorbell produces 1 then 0 on the bus and the next press is a fresh edge again.
Both values take the same path — type coercion, formula, value map — so a
downstream consumer sees one value domain, and both reach that DataPoint's DEST
bindings through the WriteRouter like any other SOURCE value.

That keeps the binding a **SOURCE**: the adapter still only ever feeds values
*into* OBS and never writes out to a protocol endpoint. A reset is one more
value this source produces, this time on its own clock rather than on a call —
no different in kind from a poll-driven adapter emitting a second reading.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.adapters.webhook.ingress import address_allowed, normalise_entries, parse_networks, resolve_client_ip
from obs.core.event_bus import DataValueEvent
from obs.models.types import DataTypeRegistry, coerce_text_value_for_type

logger = logging.getLogger(__name__)

ADAPTER_TYPE = "WEBHOOK"
DEFAULT_PATH_PREFIX = "/hook"
TOKEN_BYTES = 32  # secrets.token_urlsafe(32) → ~256 bit

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PREFIX_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_PREFIX_SEGMENTS = 3

# First path segments the application itself serves.  Claiming one of them would
# shadow the API, the SPAs or the help site.
# That includes FastAPI's own documentation routes, the Admin-GUI's root-level
# static files and its history-mode routes (`gui/src/router/index.js`, guarded
# by a test): the webhook middleware runs ahead of every route, so a prefix on
# one of these would silently take that surface offline.
# Kept as a deliberate copy rather than a shared, generated source (issue #1305):
# the entries come from three owners — FastAPI routes and mounts, gui/public and
# the Vue router — and the router source does not ship with a deployment, so a
# common source would need an extra build step for little gain.  The contract
# test against gui/src/router/index.js fails as soon as a GUI route is missing.
_RESERVED_PREFIX_SEGMENTS = frozenset(
    {
        "adapters",
        "api",
        "apple-touch-icon.png",
        "assets",
        "datapoints",
        "docs",
        "favicon.svg",
        "help",
        "history",
        "knx-devices",
        "login",
        "logic",
        "logs",
        "manifest.webmanifest",
        "message-archives",
        "obs_logo_dark.svg",
        "obs_logo_light.svg",
        "openapi.json",
        "redoc",
        "ringbuffer",
        "settings",
        "setup",
        "visu",
    }
)

_TRUE_TOKENS = frozenset({"1", "true", "on", "yes"})
_FALSE_TOKENS = frozenset({"0", "false", "off", "no"})

MAX_BODY_BYTES = 64 * 1024


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def generate_token() -> str:
    """Return a fresh per-binding secret."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_matches(expected: str, provided: str | None) -> bool:
    """Compare tokens without leaking their length or content through timing."""
    if not expected or not provided:
        return False
    # Compare bytes: `compare_digest` raises TypeError for a non-ASCII str, which
    # would turn a wrong token into a 500 that only a real slug produces.
    return secrets.compare_digest(expected.encode("utf-8"), provided.encode("utf-8", "replace"))


def normalise_path_prefix(raw: str) -> str:
    """Validate and normalise an instance path prefix to ``/a/b`` form."""
    candidate = (raw or "").strip()
    if not candidate:
        return DEFAULT_PATH_PREFIX
    segments = [segment for segment in candidate.split("/") if segment]
    if not segments:
        raise ValueError("path_prefix must contain at least one path segment")
    if len(segments) > _MAX_PREFIX_SEGMENTS:
        raise ValueError(f"path_prefix must not have more than {_MAX_PREFIX_SEGMENTS} segments")
    for segment in segments:
        if not _PREFIX_SEGMENT_PATTERN.match(segment):
            raise ValueError(f"invalid path_prefix segment: {segment!r}")
    if segments[0].lower() in _RESERVED_PREFIX_SEGMENTS:
        raise ValueError(f"path_prefix must not start with the reserved segment {segments[0]!r}")
    return "/" + "/".join(segments)


# ---------------------------------------------------------------------------
# Config Schemas
# ---------------------------------------------------------------------------


class WebhookAdapterConfig(BaseModel):
    path_prefix: str = Field(default=DEFAULT_PATH_PREFIX, title="Pfad-Präfix")
    trust_forwarded_for: bool = Field(default=False, title="X-Forwarded-For vertrauen")
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000, title="Ratenlimit (Aufrufe/Minute je IP)")

    @field_validator("path_prefix")
    @classmethod
    def _check_path_prefix(cls, value: str) -> str:
        return normalise_path_prefix(value)


class WebhookBindingConfig(BaseModel):
    slug: str = Field(default="", title="Slug (Pfadsegment)")
    token: str = Field(default="", title="Token")
    methods: list[Literal["GET", "POST"]] = Field(default_factory=lambda: ["GET"], title="HTTP-Methoden")
    allowed_networks: list[str] = Field(default_factory=list, title="Erlaubte Netze (CIDR)")
    value_source: Literal["fixed", "request"] = Field(default="fixed", title="Wertquelle")
    fixed_value: str = Field(default="true", title="Fester Wert")
    value_param: str = Field(default="value", title="Parameter-/Feldname")
    debounce_ms: int = Field(default=0, ge=0, le=3_600_000, title="Entprellung (ms)")
    autoreset: bool = Field(default=False, title="Auto-Reset")
    autoreset_value: str = Field(default="false", title="Reset-Wert")
    autoreset_delay_ms: int = Field(default=1000, ge=0, le=3_600_000, title="Reset-Verzögerung (ms)")

    @field_validator("allowed_networks", mode="before")
    @classmethod
    def _check_allowed_networks(cls, value: Any) -> list[str]:
        return normalise_entries(value)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        candidate = (value or "").strip().lower()
        if not SLUG_PATTERN.match(candidate):
            raise ValueError("slug must match ^[a-z0-9][a-z0-9_-]{0,63}$")
        return candidate

    @field_validator("methods")
    @classmethod
    def _check_methods(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("at least one HTTP method is required")
        return unique

    @field_validator("value_param")
    @classmethod
    def _check_value_param(cls, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            raise ValueError("value_param must not be empty")
        if candidate.lower() == "token":
            # `token` carries the credential in the query string; reading it as the
            # payload would publish the bearer secret onto the DataPoint.
            raise ValueError("value_param must not be 'token' — that parameter carries the credential")
        return candidate


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def coerce_webhook_value(raw: Any, data_type: str) -> Any:
    """Coerce an HTTP-supplied value to the DataPoint's declared type.

    Unlike ``apply_source_type`` (which logs and passes a bad value through,
    the right behaviour for a subscribed MQTT topic) this raises ``ValueError``
    so the caller can answer 400 instead of putting a wrong value on the bus.
    """
    name = DataTypeRegistry.get(data_type).name
    if name == "UNKNOWN":
        return raw
    if name == "BOOLEAN":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            # Same domain as the text form ("0"/"1"): Python truthiness would turn
            # 2, -1 or 0.5 into True and publish a value nobody asked for.
            if raw == 0 or raw == 1:
                return bool(raw)
            raise ValueError(f"{raw!r} is not a boolean value")
        token = str(raw).strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        raise ValueError(f"{raw!r} is not a boolean value")
    if name in ("INTEGER", "FLOAT"):
        if isinstance(raw, bool):
            return int(raw) if name == "INTEGER" else float(raw)
        if name == "INTEGER" and isinstance(raw, int):
            return raw
        try:
            # Everything else goes through the shared typed-text parser instead
            # of `int(float(raw))` / `float(raw)`: that keeps integers beyond
            # 2**53 exact and refuses what must never reach the bus — a
            # fractional value for an INTEGER ("42.7", 42.7) and non-finite
            # FLOATs ("NaN", "inf"), which `float()` happily accepts.
            return coerce_text_value_for_type(str(raw), data_type)
        except ValueError as exc:
            raise ValueError(f"{raw!r} is not a valid {name.lower()} value") from exc
    if name == "STRING":
        return str(raw)
    parsers = {
        "DATE": datetime.date.fromisoformat,
        "TIME": datetime.time.fromisoformat,
        "DATETIME": datetime.datetime.fromisoformat,
    }
    parser = parsers[name]
    try:
        return parser(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{raw!r} is not a valid {name} value") from exc


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class FixedWindowRateLimiter:
    """Per-key fixed-window counter.

    ``slowapi`` is wired into the app for the login routes, but its public API
    is a route decorator — the webhook trigger is dispatched by a middleware
    (the path prefix is per-instance configuration, not a static route), so
    there is no route to decorate.  Rather than reaching into slowapi's private
    internals this keeps a small deterministic counter that the tests can drive
    with an injected clock.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}
        # Window in which a denial was last announced per key, so a flood from one
        # address can be reported once per window instead of once per request.
        self._announced: dict[str, float] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        return self.check(key, now=now)[0]

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, bool]:
        """Return ``(allowed, first_denial_in_window)`` for one call by *key*."""
        current = time.monotonic() if now is None else now
        window_start = current - (current % self._window)
        start, count = self._windows.get(key, (window_start, 0))
        if start != window_start:
            start, count = window_start, 0
        if count >= self._limit:
            self._windows[key] = (start, count)
            first_denial = self._announced.get(key) != start
            self._announced[key] = start
            return False, first_denial
        self._windows[key] = (start, count + 1)
        self._prune(window_start)
        return True, False

    def _prune(self, window_start: float) -> None:
        """Drop keys from older windows so the map cannot grow without bound."""
        if len(self._windows) <= 1024:
            return
        for key in [key for key, (start, _) in self._windows.items() if start != window_start]:
            del self._windows[key]
        self._announced = {key: start for key, start in self._announced.items() if key in self._windows}


# ---------------------------------------------------------------------------
# Trigger result + per-binding statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerOutcome:
    status: int
    detail: str


class RejectionReason(StrEnum):
    """Why a call was turned away, for the Admin GUI's diagnostics.

    A rejected call answers an indistinguishable 404 by design, which makes a
    misconfigured allowlist look exactly like a broken adapter. Counting the
    reasons — and remembering the address of the last one — is what turns that
    silence back into something an operator can act on, without telling the
    caller anything it did not already know.
    """

    RATE_LIMITED = "rate_limited"
    UNKNOWN_SLUG = "unknown_slug"
    INVALID_BINDING = "invalid_binding"
    INVALID_TOKEN = "invalid_token"
    ADDRESS_BLOCKED = "address_blocked"
    METHOD_NOT_ALLOWED = "method_not_allowed"


@dataclass
class LastRejection:
    reason: RejectionReason
    client_ip: str | None
    at: datetime.datetime
    slug: str | None = None


@dataclass
class RejectionCounters:
    """Per-reason counters; the keys are the ``RejectionReason`` values."""

    counts: dict[str, int] = field(default_factory=dict)
    last: LastRejection | None = None

    def record(self, reason: RejectionReason, *, client_ip: str | None, slug: str | None = None) -> None:
        self.counts[reason.value] = self.counts.get(reason.value, 0) + 1
        self.last = LastRejection(reason=reason, client_ip=client_ip, at=datetime.datetime.now(datetime.UTC), slug=slug)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class BindingStats:
    """In-memory call statistics shown next to a binding in the Admin GUI.

    Not persisted on purpose: a doorbell writes here on every press, and the
    value itself is already visible in the RingBuffer and the history with
    ``source_adapter="WEBHOOK"``.

    ``call_count`` counts the calls that got past the token, the method and the
    ingress checks, including those the debounce window then suppressed;
    ``publish_count`` counts the ones that actually put a value on the bus.
    The difference is what the debounce and the value checks rejected.
    """

    call_count: int = 0
    publish_count: int = 0
    last_called: datetime.datetime | None = None
    last_status: int | None = None
    rejections: RejectionCounters = field(default_factory=RejectionCounters)


_NOT_FOUND = TriggerOutcome(404, "Not found")


# ---------------------------------------------------------------------------
# Dispatch registry (prefix → running instance)
# ---------------------------------------------------------------------------

_instances_by_prefix: dict[str, WebhookAdapter] = {}


def _prefixes_overlap(first: str, second: str) -> bool:
    """Whether one prefix equals or lies below the other (``/hook`` vs ``/hook/inner``)."""
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def _register_prefix(prefix: str, instance: WebhookAdapter) -> bool:
    # Equality is not enough: dispatch picks the longest matching prefix, so an
    # instance at `/hook/inner` would take over every `/hook/inner…` URL of the
    # instance at `/hook` (whose slug may well be `inner`) and answer 404 for it.
    for claimed, owner in _instances_by_prefix.items():
        if owner is not instance and _prefixes_overlap(prefix, claimed):
            return False
    _instances_by_prefix[prefix] = instance
    return True


def _unregister_instance(instance: WebhookAdapter) -> None:
    for prefix in [prefix for prefix, candidate in _instances_by_prefix.items() if candidate is instance]:
        del _instances_by_prefix[prefix]


def active_prefixes() -> list[str]:
    """Prefixes currently claimed by a running instance (longest first)."""
    return sorted(_instances_by_prefix, key=len, reverse=True)


def resolve_webhook_target(path: str) -> tuple[WebhookAdapter, str] | None:
    """Resolve an incoming request path to an instance and its path remainder.

    Returns ``None`` when no running instance claims the path, which lets the
    normal request stack handle it unchanged.
    """
    if not _instances_by_prefix:
        return None
    normalised = "/" + path.strip("/")
    for prefix in active_prefixes():
        if normalised == prefix:
            return _instances_by_prefix[prefix], ""
        if normalised.startswith(prefix + "/"):
            return _instances_by_prefix[prefix], normalised[len(prefix) + 1 :]
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class WebhookAdapter(AdapterBase):
    """Turns incoming HTTP calls into DataValueEvents for SOURCE bindings."""

    adapter_type = "WEBHOOK"
    config_schema = WebhookAdapterConfig
    binding_config_schema = WebhookBindingConfig
    # Deliberately empty: no API key may create or rotate a webhook binding.
    # A *user* with operator grants may — ``_ensure_adapter_delegates_binding``
    # lets user principals through for this type — see the module docstring.
    delegation_capabilities = frozenset()

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._path_prefix: str = DEFAULT_PATH_PREFIX
        self._trust_forwarded_for: bool = False
        self._limiter = FixedWindowRateLimiter(60)
        self._by_slug: dict[str, Any] = {}
        self._stats: dict[str, BindingStats] = {}
        self._last_trigger: dict[str, float] = {}
        self._autoreset_tasks: dict[str, asyncio.Task] = {}
        self._autoreset_armed: dict[str, str] = {}
        self._rejections = RejectionCounters()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            cfg = WebhookAdapterConfig(**self._config)
        except ValidationError as exc:
            logger.error("WEBHOOK: invalid instance configuration — %s", exc)
            await self._publish_status(False, f"Invalid configuration: {exc}", severity="error", code="webhookInvalidConfig")
            return

        self._path_prefix = cfg.path_prefix
        self._trust_forwarded_for = cfg.trust_forwarded_for
        self._limiter = FixedWindowRateLimiter(cfg.rate_limit_per_minute)

        if not _register_prefix(cfg.path_prefix, self):
            logger.error("WEBHOOK: path prefix %s overlaps a prefix claimed by another instance", cfg.path_prefix)
            await self._publish_status(
                False,
                f"Path prefix {cfg.path_prefix} is already in use",
                severity="error",
                code="webhookPrefixConflict",
                params={"prefix": cfg.path_prefix},
            )
            return

        await self._publish_status(True, f"Listening on {cfg.path_prefix}/<slug>", code="webhookListening", params={"prefix": cfg.path_prefix})
        logger.info("WEBHOOK adapter ready: %s/<slug>", cfg.path_prefix)

    async def disconnect(self) -> None:
        _unregister_instance(self)
        self._cancel_all_autoresets()
        self._by_slug.clear()
        self._last_trigger.clear()
        await self._publish_status(False, "Disconnected", code="disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        # A pending reset belongs to the configuration it was armed under; the
        # reloaded binding may have a different reset value or none at all. A
        # reload that changes nothing relevant — a token rotation on this or
        # another binding of the instance — must leave it alone, or the
        # DataPoint would stay stuck at the trigger value.
        self._cancel_stale_autoresets()
        by_slug: dict[str, Any] = {}
        duplicates: list[str] = []
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                slug = WebhookBindingConfig(**binding.config).slug
            except ValidationError:
                logger.warning("WEBHOOK: invalid binding configuration %s — skipped", binding.id)
                continue
            if slug in by_slug:
                duplicates.append(slug)
                continue
            by_slug[slug] = binding
        self._by_slug = by_slug
        # Keep statistics only for bindings that still exist.
        for binding_id in [key for key in self._stats if key not in {str(b.id) for b in by_slug.values()}]:
            del self._stats[binding_id]

        if duplicates:
            joined = ", ".join(sorted(set(duplicates)))
            logger.warning("WEBHOOK: duplicate slug(s) ignored: %s", joined)
            await self._publish_status(
                self._connected,
                f"Duplicate slug(s) ignored: {joined}",
                severity="warning",
                code="webhookDuplicateSlug",
                params={"slugs": joined},
            )
        logger.debug("WEBHOOK: %d slug(s) active on %s", len(by_slug), self._path_prefix)

    # ------------------------------------------------------------------
    # Data exchange — SOURCE only
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        return None

    async def write(self, binding: Any, value: Any) -> None:
        """A webhook is an inbound entry point; there is nothing to write to."""

    # ------------------------------------------------------------------
    # Introspection for the Admin GUI
    # ------------------------------------------------------------------

    @property
    def path_prefix(self) -> str:
        return self._path_prefix

    @property
    def rejections(self) -> RejectionCounters:
        """Instance-wide rejections — the ones no binding can be blamed for."""
        return self._rejections

    def stats_for(self, binding_id: uuid.UUID | str) -> BindingStats:
        return self._stats.get(str(binding_id), BindingStats())

    def _reject(
        self,
        reason: RejectionReason,
        *,
        client_ip: str | None,
        slug: str | None = None,
        binding: Any = None,
    ) -> None:
        """Count a turned-away call, on the instance and on its binding if known."""
        self._rejections.record(reason, client_ip=client_ip, slug=slug)
        if binding is not None:
            stats = self._stats.setdefault(str(binding.id), BindingStats())
            stats.rejections.record(reason, client_ip=client_ip, slug=slug)

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    def admit(self, *, peer_ip: str | None, forwarded_for: str | None = None) -> TriggerOutcome | None:
        """Charge one call against the per-address rate limit.

        Returns the 429 outcome when the address is over its limit, else ``None``.
        This runs before anything else — in particular before a POST body is read —
        so a caller cannot repeat oversized or invalid requests without spending
        budget. A blocked address is logged once per window; the counters still
        see every call, so a flood cannot become a flood of log lines (and
        WebSocket broadcasts) of its own.
        """
        client_ip = resolve_client_ip(peer_ip, forwarded_for, trust_forwarded_for=self._trust_forwarded_for)
        allowed, first_denial = self._limiter.check(client_ip or "unknown")
        if allowed:
            return None
        if first_denial:
            logger.warning("WEBHOOK: rate limit exceeded for %s (further calls in this window are not logged)", client_ip)
        self._reject(RejectionReason.RATE_LIMITED, client_ip=client_ip)
        return TriggerOutcome(429, "Too many requests")

    async def handle_trigger(
        self,
        *,
        method: str,
        remainder: str,
        query_params: dict[str, str],
        body: bytes,
        peer_ip: str | None,
        forwarded_for: str | None = None,
        admitted: bool = False,
    ) -> TriggerOutcome:
        """Handle one incoming call below this instance's path prefix.

        ``admitted`` says the caller already went through :meth:`admit` (the
        middleware does that before reading a POST body); the call is then not
        charged against the rate limit a second time.

        Returns 404 for an unknown slug, a wrong token, a method the binding
        does not allow and a disabled instance alike: a device that guesses
        must not be able to tell which part of its guess was wrong.
        """
        if not admitted:
            denial = self.admit(peer_ip=peer_ip, forwarded_for=forwarded_for)
            if denial is not None:
                return denial
        client_ip = resolve_client_ip(peer_ip, forwarded_for, trust_forwarded_for=self._trust_forwarded_for)

        slug, path_token = self._split_remainder(remainder)
        binding = self._by_slug.get(slug) if slug else None
        if binding is None:
            logger.warning("WEBHOOK: unknown slug %r from %s", slug, client_ip)
            self._reject(RejectionReason.UNKNOWN_SLUG, client_ip=client_ip, slug=slug)
            return _NOT_FOUND

        try:
            config = WebhookBindingConfig(**binding.config)
        except ValidationError:
            logger.warning("WEBHOOK: binding %s has an invalid configuration", binding.id)
            self._reject(RejectionReason.INVALID_BINDING, client_ip=client_ip, slug=slug)
            return _NOT_FOUND

        provided = path_token if path_token else query_params.get("token")
        if not token_matches(config.token, provided):
            logger.warning("WEBHOOK: invalid token for slug %r from %s", slug, client_ip)
            self._reject(RejectionReason.INVALID_TOKEN, client_ip=client_ip, slug=slug)
            return _NOT_FOUND

        # Checked after the token so the rejection can be attributed to this
        # binding in the GUI. A caller that fails here already holds a valid
        # token, so the later check leaks nothing a 404 did not already say.
        if not address_allowed(client_ip, parse_networks(config.allowed_networks)):
            logger.warning("WEBHOOK: rejected call from %s — not in the allowed networks of slug %r", client_ip, slug)
            self._reject(RejectionReason.ADDRESS_BLOCKED, client_ip=client_ip, slug=slug, binding=binding)
            return self._record(binding, _NOT_FOUND)

        if method not in config.methods:
            logger.warning("WEBHOOK: method %s not allowed for slug %r", method, slug)
            self._reject(RejectionReason.METHOD_NOT_ALLOWED, client_ip=client_ip, slug=slug, binding=binding)
            return self._record(binding, _NOT_FOUND)

        stats = self._stats.setdefault(str(binding.id), BindingStats())
        stats.call_count += 1
        stats.last_called = datetime.datetime.now(datetime.UTC)

        previous_trigger = self._last_trigger.get(str(binding.id))
        if self._is_debounced(binding, config):
            logger.debug("WEBHOOK: slug %r debounced (%d ms)", slug, config.debounce_ms)
            return self._record(binding, TriggerOutcome(204, ""))

        outcome = await self._publish(binding, config, method, query_params, body)
        if outcome.status != 204:
            # The window is claimed before publishing so concurrent duplicates
            # cannot both pass, but a call that put nothing on the bus must not
            # swallow the corrected retry that follows it.
            self._release_debounce(binding, previous_trigger)
        return self._record(binding, outcome, published=outcome.status == 204)

    # ------------------------------------------------------------------
    # Trigger internals
    # ------------------------------------------------------------------

    @staticmethod
    def _split_remainder(remainder: str) -> tuple[str, str | None]:
        """Split ``<slug>`` or ``<slug>/<token>`` into its parts.

        Supporting the token as a path segment matters for devices whose
        configuration field strips query strings.
        """
        parts = [part for part in remainder.split("/") if part]
        if not parts:
            return "", None
        if len(parts) == 1:
            return parts[0].lower(), None
        if len(parts) == 2:
            return parts[0].lower(), parts[1]
        return "", None

    def _is_debounced(self, binding: Any, config: WebhookBindingConfig) -> bool:
        if config.debounce_ms <= 0:
            return False
        key = str(binding.id)
        now = time.monotonic()
        previous = self._last_trigger.get(key)
        if previous is not None and (now - previous) * 1000.0 < config.debounce_ms:
            return True
        self._last_trigger[key] = now
        return False

    def _release_debounce(self, binding: Any, previous: float | None) -> None:
        key = str(binding.id)
        if previous is None:
            self._last_trigger.pop(key, None)
        else:
            self._last_trigger[key] = previous

    def _record(self, binding: Any, outcome: TriggerOutcome, *, published: bool = False) -> TriggerOutcome:
        stats = self._stats.setdefault(str(binding.id), BindingStats())
        stats.last_status = outcome.status
        if published:
            stats.publish_count += 1
        return outcome

    async def _publish(
        self,
        binding: Any,
        config: WebhookBindingConfig,
        method: str,
        query_params: dict[str, str],
        body: bytes,
    ) -> TriggerOutcome:
        from obs.core.registry import get_registry

        datapoint = get_registry().get(binding.datapoint_id)
        if datapoint is None:
            logger.warning("WEBHOOK: binding %s points at a missing DataPoint", binding.id)
            return _NOT_FOUND
        # Re-checked on every call, not only when the binding was created: a
        # DataPoint can be reclassified as central_plant afterwards, and a
        # device "mounted outside the house, configured in clear text" must
        # never reach a central plant (see the anonymous Visu write path).
        if getattr(datapoint, "control_class", "room_local") == "central_plant":
            logger.warning("WEBHOOK: binding %s targets a central_plant DataPoint — refused", binding.id)
            return TriggerOutcome(403, "Webhook bindings may not write central plant datapoints")

        try:
            raw = self._raw_value(config, method, query_params, body)
        except ValueError as exc:
            return TriggerOutcome(400, str(exc))

        try:
            value = coerce_webhook_value(raw, datapoint.data_type)
        except ValueError as exc:
            logger.warning("WEBHOOK: binding %s received an incompatible value — %s", binding.id, exc)
            return TriggerOutcome(400, str(exc))

        await self._emit(binding, value)
        logger.info("WEBHOOK: dp=%s value=%r (binding %s)", binding.datapoint_id, value, binding.id)
        self._schedule_autoreset(binding, config)
        return TriggerOutcome(204, "")

    async def _emit(self, binding: Any, value: Any) -> None:
        """Put one value on the bus for *binding*, transformations applied.

        Shared by the triggered value and the auto-reset so both land in the
        same value domain: a value map turning True into "on" must turn the
        reset False into "off" as well, or a downstream consumer would see two
        different vocabularies from one binding.
        """
        if binding.value_formula:
            from obs.core.formula import apply_formula

            value = apply_formula(binding.value_formula, value)
        if binding.value_map:
            from obs.core.transformation import apply_value_map

            value = apply_value_map(value, binding.value_map)

        await self._bus.publish(
            DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=value,
                quality="good",
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            )
        )

    # ------------------------------------------------------------------
    # Auto-reset
    # ------------------------------------------------------------------

    def _schedule_autoreset(self, binding: Any, config: WebhookBindingConfig) -> None:
        """Arm the timer that publishes the reset value.

        Retriggerable on purpose: a second call while one is pending restarts
        the delay instead of adding a timer, so a bell pressed twice stays on
        for the configured time after the *last* press rather than resetting in
        the middle of it.
        """
        if not config.autoreset:
            return
        key = str(binding.id)
        self._cancel_autoreset(key)
        self._autoreset_armed[key] = self._reset_signature(binding)
        self._autoreset_tasks[key] = asyncio.create_task(
            self._autoreset(binding, config),
            name=f"webhook-autoreset-{key}",
        )

    async def _autoreset(self, binding: Any, config: WebhookBindingConfig) -> None:
        from obs.core.registry import get_registry

        # CancelledError is a BaseException and passes straight through the
        # ValueError handler, so a cancelled timer needs no clause of its own.
        try:
            if config.autoreset_delay_ms:
                await asyncio.sleep(config.autoreset_delay_ms / 1000.0)
            # Resolved after the delay, not when the reset was armed: the DataPoint
            # can be reclassified as central_plant or change its type meanwhile,
            # and this is an anonymous write just like the trigger itself.
            datapoint = get_registry().get(binding.datapoint_id)
            if datapoint is None or getattr(datapoint, "control_class", "room_local") == "central_plant":
                logger.warning("WEBHOOK: auto-reset for binding %s skipped — its DataPoint is gone or now central_plant", binding.id)
                return
            value = coerce_webhook_value(config.autoreset_value, datapoint.data_type)
        except ValueError as exc:
            logger.warning("WEBHOOK: binding %s has an unusable auto-reset value — %s", binding.id, exc)
            return
        await self._emit(binding, value)
        # The reset is a value placed on the bus like the trigger, so it counts.
        self._stats.setdefault(str(binding.id), BindingStats()).publish_count += 1
        logger.info("WEBHOOK: auto-reset dp=%s value=%r (binding %s)", binding.datapoint_id, value, binding.id)

    @staticmethod
    def _reset_signature(binding: Any) -> str:
        """Everything a pending reset depends on — the token is deliberately not part of it."""
        config = {name: value for name, value in binding.config.items() if name != "token"}
        return json.dumps([config, binding.value_formula, binding.value_map, str(binding.datapoint_id)], sort_keys=True, default=str)

    def _cancel_stale_autoresets(self) -> None:
        current = {str(binding.id): self._reset_signature(binding) for binding in self._bindings}
        for key in list(self._autoreset_tasks):
            if self._autoreset_armed.get(key) != current.get(key):
                self._cancel_autoreset(key)

    def _cancel_autoreset(self, key: str) -> None:
        self._autoreset_armed.pop(key, None)
        task = self._autoreset_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_autoresets(self) -> None:
        for key in list(self._autoreset_tasks):
            self._cancel_autoreset(key)

    @staticmethod
    def _raw_value(
        config: WebhookBindingConfig,
        method: str,
        query_params: dict[str, str],
        body: bytes,
    ) -> Any:
        if config.value_source == "fixed":
            return config.fixed_value

        if method == "POST" and body:
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("Request body is too large")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and config.value_param in payload:
                return payload[config.value_param]

        if config.value_param in query_params:
            return query_params[config.value_param]

        raise ValueError(f"Missing value: no {config.value_param!r} parameter in the request")
