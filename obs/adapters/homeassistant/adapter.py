"""Home Assistant Adapter

Verbindet open bridge server mit einer Home Assistant Instanz.
Liest Entitätszustände über eine multiplexte WebSocket-Verbindung
(``subscribe_entities``, gefiltert auf die gebundenen Entities) und schreibt
Actions über die REST-API (#1392).

Adapter-Konfiguration (adapter_instances.config in DB):
  host:    str   — Hostname/IP der HA-Instanz (default: homeassistant.local)
  port:    int   — Port (default: 8123)
  token:   str   — Long-Lived Access Token
  ssl:     bool  — HTTPS/WSS verwenden (default: False)

Binding-Konfiguration (pro AdapterBinding.config): siehe ``profiles.HaBindingConfig``.
  entity_id, attribute                               — Lesequelle
  service_domain, service_name, service_data_key     — Legacy-Write, unverändertes Verhalten
  profile, action, write_actions, state_map,
  transitional_states, fault_states, ha_uid          — deklaratives Mapping (#1392)

Synchronisation:
  - eine authentifizierte Verbindung je Instanz, Ping alle 30 s, Reconnect mit
    begrenztem Backoff (1 s … 60 s)
  - nach jedem (Re-)Connect und jeder Binding-Änderung liefert das
    ``subscribe_entities``-Snapshot den aktuellen Wert jeder SOURCE/BOTH-Verknüpfung
  - HA ohne ``subscribe_entities`` (< 2022.4): ``state_changed`` + ``get_states``

Qualität (SOURCE):
  normaler Zustand                   → Wert, good
  unknown / Übergangszustand         → letzter stabiler Wert, uncertain
  unavailable / Fehlerzustand /
  entfernt / Verbindung verloren     → letzter stabiler Wert, bad
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, ValidationError

from obs.adapters.base import AdapterBase
from obs.adapters.homeassistant.profiles import (
    BAD,
    HaBindingConfig,
    NoActionForValueError,
    ResolvedMapping,
    direction_problem,
    entity_domain,
    identity_hash,
    normalize,
    resolve,
    select_action,
)
from obs.adapters.homeassistant.ws_client import HaAuthError, HaCommandError, HaConnection
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent
from obs.core.transformation import apply_value_map

logger = logging.getLogger(__name__)

PING_INTERVAL = 30.0
PING_TIMEOUT = 10.0
BACKOFF_MIN = 1.0
BACKOFF_MAX = 60.0
# A connection must stay up this long before the backoff starts over at BACKOFF_MIN,
# so a failure right after login (e.g. a rejected subscription) cannot loop every second.
STABLE_AFTER = 60.0
BROWSE_CACHE_SECONDS = 30.0
_sleep = asyncio.sleep  # module seam: tests observe the backoff without waiting

# Attribute keys never exposed by the entity browser (e.g. media_player
# entity_picture carries an access token in its URL). "code" only as the whole key:
# error_code or country_code are ordinary values.
_SENSITIVE_ATTRIBUTE_PARTS = ("token", "password", "secret", "entity_picture")
_SENSITIVE_ATTRIBUTE_KEYS = frozenset({"code"})


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class HaAdapterConfig(BaseModel):
    host: str = "homeassistant.local"
    port: int = 8123
    token: str = Field(default="", json_schema_extra={"format": "password"})
    ssl: bool = False


class HaNotConnectedError(Exception):
    """The instance has no live WebSocket connection."""


class HaActionError(Exception):
    """Home Assistant rejected an action call (or could not be reached)."""


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _public_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if _is_scalar(v) and not _is_sensitive(k.lower())}


def _is_sensitive(key: str) -> bool:
    return key in _SENSITIVE_ATTRIBUTE_KEYS or any(part in key for part in _SENSITIVE_ATTRIBUTE_PARTS)


# Non-scalar attributes the profile checks read (e.g. on/off-only lights); passed
# to the import planner separately, never offered as importable attributes.
_CAPABILITY_ATTRIBUTES = ("supported_color_modes",)


def _capabilities(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: attrs[k] for k in _CAPABILITY_ATTRIBUTES if k in attrs}


def _same_value(a: Any, b: Any) -> bool:
    """Equality that keeps booleans apart from numbers (``1 == True`` in Python)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _text(value: Any) -> str | None:
    return str(value)[:200] if isinstance(value, str) and value else None


def _service_fields(fields: Any) -> list[dict[str, Any]]:
    """Flatten an action's data fields, including collapsible sections."""
    out: list[dict[str, Any]] = []
    for key, spec in (fields or {}).items() if isinstance(fields, dict) else []:
        spec = spec if isinstance(spec, dict) else {}
        if isinstance(spec.get("fields"), dict):  # a section groups further fields
            out.extend(_service_fields(spec["fields"]))
            continue
        out.append({"key": key, "name": _text(spec.get("name")), "required": bool(spec.get("required", False))})
    return sorted(out, key=lambda f: f["key"])


def _ha_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return str(message)[:200] if message else ""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class HomeAssistantAdapter(AdapterBase):
    adapter_type = "HOME_ASSISTANT"
    config_schema = HaAdapterConfig
    binding_config_schema = HaBindingConfig

    @staticmethod
    def validate_binding_direction(binding_config: HaBindingConfig, direction: str) -> None:
        """Reject a profile that cannot work in the binding's direction (raises ValueError)."""
        problem = direction_problem(binding_config, direction)
        if problem:
            raise ValueError(problem)

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg: HaAdapterConfig | None = None
        self._ws_task: asyncio.Task | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._conn: HaConnection | None = None
        self._ha_version = ""
        # entity_id → list of SOURCE/BOTH bindings
        self._entity_map: dict[str, list[Any]] = {}
        self._mappings: dict[Any, ResolvedMapping] = {}
        self._entity_cache: dict[str, dict[str, Any]] = {}
        # binding id → last value published with quality good (after formula/value_map)
        self._last_good: dict[Any, Any] = {}
        self._last_published: dict[Any, tuple[Any, str]] = {}
        self._fresh_events: set[str] | None = None
        self._write_specs: dict[Any, tuple[dict[str, Any], HaBindingConfig, ResolvedMapping]] = {}
        # binding id → what its published value depends on; a change republishes it
        self._signatures: dict[Any, tuple[Any, ...]] = {}
        self._sub_lock = asyncio.Lock()
        self._sub_id: int | None = None
        self._sub_entities: tuple[str, ...] | None = None
        self._sub_mode = "entities"  # falls back to "events" on HA < 2022.4
        self._snapshot_expected: set[str] | None = None
        self._browse_cache: tuple[float, dict[str, Any]] | None = None
        self._services_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._action_failed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError:
            logger.error("websockets not installed — Home Assistant adapter disabled")
            await self._publish_status(False, "websockets not installed", code="libNotInstalled", params={"lib": "websockets"})
            return

        self._cfg = HaAdapterConfig(**self._config)
        if not self._cfg.token:
            logger.error("Home Assistant adapter: no token configured")
            await self._publish_status(False, "Kein Token konfiguriert", code="noToken")
            return

        scheme = "https" if self._cfg.ssl else "http"
        base_url = f"{scheme}://{self._cfg.host}:{self._cfg.port}"
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._cfg.token}"},
            timeout=10.0,
        )
        await self._publish_status(
            False,
            f"Verbinde mit {self._cfg.host}:{self._cfg.port}…",
            code="connecting",
            params={"host": self._cfg.host, "port": self._cfg.port},
        )
        logger.info("Home Assistant adapter started → %s:%d ssl=%s", self._cfg.host, self._cfg.port, self._cfg.ssl)
        self._ws_task = asyncio.create_task(self._run(), name="ha-adapter-ws")

    async def disconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        await self._close_connection()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._entity_map.clear()
        self._mappings.clear()
        self._signatures.clear()
        self._entity_cache.clear()
        await self._publish_status(False, "Getrennt", code="disconnected")

    async def _close_connection(self) -> None:
        conn, self._conn = self._conn, None
        self._sub_id = None
        self._sub_entities = None
        self._snapshot_expected = None
        # A new connection tries the filtered subscription again (HA may have been upgraded).
        self._sub_mode = "entities"
        self._browse_cache = None
        self._services_cache = None
        if conn is not None:
            await conn.close()

    def _ws_uri(self) -> str:
        cfg = self._cfg
        scheme = "wss" if cfg.ssl else "ws"
        return f"{scheme}://{cfg.host}:{cfg.port}/api/websocket"

    async def _run(self) -> None:
        """Connection supervisor: connect, authenticate, subscribe, heartbeat, reconnect."""
        import websockets

        uri = self._ws_uri()
        backoff = BACKOFF_MIN
        while True:
            up_since: float | None = None
            try:
                logger.info("HA adapter WebSocket connecting → %s", uri)
                conn = await HaConnection.open(uri, self._cfg.token, connect=websockets.connect)
                self._conn = conn
                self._ha_version = conn.ha_version
                logger.info("HA WebSocket authenticated (ha_version=%s)", conn.ha_version or "?")
                await self._publish_connected()
                await self._sync_subscription()
                up_since = time.monotonic()
                await self._heartbeat(conn)
                logger.warning("HA WebSocket closed by Home Assistant, reconnecting")
                await self._publish_status(False, "WebSocket Fehler — reconnecting", code="wsErrorReconnecting")
            except asyncio.CancelledError:
                raise
            except HaAuthError:
                logger.error("HA WebSocket: authentication failed — check token")
                await self._publish_status(False, "Token ungültig", code="tokenInvalid")
                backoff = BACKOFF_MAX
            except Exception as exc:  # noqa: BLE001 -- the supervisor must survive any failure and retry
                logger.warning("HA adapter WebSocket error (%s: %s), reconnecting", type(exc).__name__, exc)
                await self._publish_status(False, "WebSocket Fehler — reconnecting", code="wsErrorReconnecting")
            if up_since is not None and time.monotonic() - up_since >= STABLE_AFTER:
                backoff = BACKOFF_MIN
            await self._close_connection()
            await self._mark_connection_lost()
            await _sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _heartbeat(self, conn: HaConnection) -> None:
        """Return when the connection closes; raise when a ping goes unanswered."""
        while True:
            try:
                await asyncio.wait_for(conn.wait_closed(), PING_INTERVAL)
                return
            except TimeoutError:
                pass
            await conn.ping(PING_TIMEOUT)

    async def _publish_connected(self) -> None:
        await self._publish_status(
            True,
            f"Verbunden mit HA {self._ha_version}",
            code="connectedHa",
            params={"version": self._ha_version},
        )

    # ------------------------------------------------------------------
    # Bindings / subscription
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        if self._cfg is None:
            return

        entity_map: dict[str, list[Any]] = {}
        mappings: dict[Any, ResolvedMapping] = {}
        signatures: dict[Any, tuple[Any, ...]] = {}
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = HaBindingConfig(**binding.config)
            except (ValidationError, TypeError):
                logger.warning("Ungültige HA Binding-Konfiguration für %s — übersprungen", binding.id)
                continue
            entity_map.setdefault(bc.entity_id, []).append(binding)
            mappings[binding.id] = resolve(bc)
            signatures[binding.id] = (bc.model_dump_json(), binding.value_formula, repr(binding.value_map))

        # A binding whose read path or transformation changed must publish again;
        # every other binding keeps its deduplication state, so a reload does not
        # re-send unchanged values through the WriteRouter.
        for store in (self._last_published, self._last_good):
            for binding_id in [b for b in store if signatures.get(b) != self._signatures.get(b)]:
                del store[binding_id]
        self._entity_map, self._mappings, self._signatures = entity_map, mappings, signatures
        bound = {binding.id for binding in self._bindings}
        self._write_specs = {k: v for k, v in self._write_specs.items() if k in bound}

        logger.info("HA adapter: %d entity subscription(s): %s", len(self._entity_map), sorted(self._entity_map))
        try:
            await self._sync_subscription(close_on_error=True)
        except Exception as exc:  # noqa: BLE001 -- any failure is handled by a supervised reconnect
            # The failed connection was dropped: the supervisor reconnects and resubscribes with backoff.
            logger.warning("HA adapter: resubscribe failed (%s: %s)", type(exc).__name__, exc)
            await self._publish_status(False, "Abonnement fehlgeschlagen", code="subscribeFailed")

    async def _sync_subscription(self, *, close_on_error: bool = False) -> None:
        """Subscribe to exactly the bound entities and publish their current values.

        With *close_on_error*, a failure closes the connection this call used
        (never a newer one the supervisor may have opened meanwhile).
        """
        async with self._sub_lock:
            conn = self._conn
            if conn is None or conn.closed:
                return  # the supervisor subscribes after (re)connecting
            try:
                await self._subscribe(conn)
            except Exception:
                if close_on_error:
                    await conn.close()
                raise

    async def _subscribe(self, conn: HaConnection) -> None:
        wanted = tuple(sorted(self._entity_map))
        if wanted == self._sub_entities and self._sub_id is not None:
            if self._snapshot_expected is not None:
                return  # the pending snapshot publishes every current binding
            # Same entity set: new or changed bindings get their value from the cache;
            # unchanged ones are deduplicated.
            for entity_id in wanted:
                await self._publish_entity(entity_id)
            return
        # The unfiltered state_changed subscription of the fallback already covers
        # every entity: keep it and only take a new snapshot.
        if self._sub_id is not None and (self._sub_mode == "entities" or not wanted):
            await conn.unsubscribe(self._sub_id)
            self._sub_id = None
        self._sub_entities = wanted
        self._entity_cache = {k: v for k, v in self._entity_cache.items() if k in wanted}
        if not wanted:
            return
        self._snapshot_expected = set(wanted)
        if self._sub_mode == "entities":
            try:
                self._sub_id = await conn.subscribe(
                    {"type": "subscribe_entities", "entity_ids": list(wanted)},
                    self._on_entities_event,
                )
                logger.info("HA WebSocket subscribed to %d entities", len(wanted))
                return
            except HaCommandError as exc:
                # unknown_command: HA < 2022.4; invalid_format: HA without the entity_ids filter
                if exc.code not in ("unknown_command", "invalid_format"):
                    raise
                logger.info("HA WebSocket: filtered subscribe_entities unsupported (%s), using state_changed events", exc.code)
                self._sub_mode = "events"
        if self._sub_id is None:
            self._sub_id = await conn.subscribe({"type": "subscribe_events", "event_type": "state_changed"}, self._on_state_changed_event)
        # state_changed events applied while get_states was pending are newer than its answer.
        self._fresh_events = fresh = set()
        try:
            states = await conn.command({"type": "get_states"})
        finally:
            self._fresh_events = None
        snapshot = {
            s["entity_id"]: s
            for s in states or []
            if isinstance(s, dict) and s.get("entity_id") in self._entity_map and s.get("entity_id") not in fresh
        }
        self._entity_cache.update(snapshot)
        await self._complete_snapshot(set(snapshot))

    async def _complete_snapshot(self, received: set[str]) -> None:
        expected, self._snapshot_expected = self._snapshot_expected or set(), None
        for entity_id in sorted(expected | received):
            await self._publish_entity(entity_id)

    async def _on_entities_event(self, event: dict[str, Any]) -> None:
        """Apply a compressed ``subscribe_entities`` event (a=add, c=change, r=remove)."""
        touched: set[str] = set()
        for entity_id, comp in (event.get("a") or {}).items():
            if isinstance(comp, dict):
                self._entity_cache[entity_id] = {"state": comp.get("s"), "attributes": dict(comp.get("a") or {})}
                touched.add(entity_id)
        for entity_id, diff in (event.get("c") or {}).items():
            current = self._entity_cache.get(entity_id)
            if current is None or not isinstance(diff, dict):
                continue
            plus = diff.get("+") or {}
            if "s" in plus:
                current["state"] = plus["s"]
            current["attributes"].update(plus.get("a") or {})
            for key in (diff.get("-") or {}).get("a") or []:
                current["attributes"].pop(key, None)
            touched.add(entity_id)
        for entity_id in event.get("r") or []:
            self._entity_cache.pop(entity_id, None)
            touched.add(entity_id)

        if self._snapshot_expected is not None and "a" in event:
            await self._complete_snapshot(touched)
            return
        for entity_id in sorted(touched):
            await self._publish_entity(entity_id)

    async def _on_state_changed_event(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        entity_id = data.get("entity_id")
        if entity_id not in self._entity_map:
            return
        if self._fresh_events is not None:
            self._fresh_events.add(entity_id)
        new_state = data.get("new_state")
        if new_state:
            self._entity_cache[entity_id] = new_state
        else:
            self._entity_cache.pop(entity_id, None)
        await self._publish_entity(entity_id)

    async def _publish_entity(self, entity_id: str) -> None:
        state_obj = self._entity_cache.get(entity_id)
        for binding in list(self._entity_map.get(entity_id, [])):
            await self._publish_binding(binding, state_obj)

    async def _mark_connection_lost(self) -> None:
        self._entity_cache.clear()
        for bindings in list(self._entity_map.values()):
            for binding in list(bindings):
                await self._publish_binding(binding, None)

    def _last_known(self, binding: Any) -> Any:
        """The DataPoint's current value (other bindings may have changed it since HA did).

        Falls back to the last good HA value when the registry has no state for it.
        """
        try:
            from obs.core.registry import get_registry

            state = get_registry().get_value(binding.datapoint_id)
        except RuntimeError:  # registry not initialised (tests, shutdown)
            state = None
        return state.value if state is not None else self._last_good.get(binding.id)

    async def _publish_binding(self, binding: Any, state_obj: dict[str, Any] | None) -> None:
        mapping = self._mappings.get(binding.id)
        if mapping is None:
            return  # removed by a concurrent reload
        try:
            value, quality, stable = normalize(mapping, state_obj)
            if stable:
                pub_value = value
                # formula first (numeric scale), then value_map (text substitution)
                if binding.value_formula and pub_value is not None:
                    from obs.core.formula import apply_formula

                    pub_value = apply_formula(binding.value_formula, pub_value)
                pub_value = apply_value_map(pub_value, binding.value_map)
            else:
                # Keep the last good value and only degrade the quality.
                pub_value = self._last_known(binding)
        except Exception:
            logger.exception("HA adapter: error processing binding %s", binding.id)
            return

        last = self._last_published.get(binding.id)
        if last is not None and last[1] == quality and _same_value(last[0], pub_value):
            return
        # The value HA reports again after a reconnect, an OBS start or a degraded
        # state is no new value: update the DataPoint (quality), but never re-send it
        # to other bindings or re-trigger logic — that could re-fire actuators.
        # A degraded state always carries the DataPoint's current value.
        unchanged = not stable or _same_value(pub_value, self._last_known(binding))
        if stable:
            self._last_good[binding.id] = pub_value
        self._last_published[binding.id] = (pub_value, quality)
        if quality == BAD and state_obj is None:
            logger.debug("HA adapter: binding %s has no entity state (removed or disconnected)", binding.id)
        logger.debug("HA adapter: dp=%s value=%r quality=%s", binding.datapoint_id, pub_value, quality)
        await self._bus.publish(
            DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=pub_value,
                quality=quality,
                source_adapter=self.adapter_type,
                binding_id=binding.id,
                suppress_write_propagation=unchanged,
                suppress_action_triggers=unchanged,
            ),
        )

    # ------------------------------------------------------------------
    # Entity browser
    # ------------------------------------------------------------------

    async def browse_entities(self) -> dict[str, Any]:
        """All HA entities with display metadata (scalar, non-sensitive attributes only)."""
        conn = self._conn
        if conn is None or conn.closed:
            raise HaNotConnectedError
        cached = self._browse_cache
        if cached is not None and time.monotonic() - cached[0] < BROWSE_CACHE_SECONDS:
            return cached[1]

        # One multiplexed connection: the four lists are fetched concurrently.
        states, entities_reg, devices_reg, areas_reg = await asyncio.gather(
            conn.command({"type": "get_states"}),
            self._optional_command(conn, "config/entity_registry/list"),
            self._optional_command(conn, "config/device_registry/list"),
            self._optional_command(conn, "config/area_registry/list"),
        )
        states = states or []
        entity_meta = {e.get("entity_id"): e for e in entities_reg or [] if isinstance(e, dict)}
        devices = {d.get("id"): d for d in devices_reg or [] if isinstance(d, dict)}
        areas = {a.get("area_id"): a.get("name") for a in areas_reg or [] if isinstance(a, dict)}

        items: list[dict[str, Any]] = []
        for state in states:
            if not isinstance(state, dict) or not isinstance(state.get("entity_id"), str):
                continue
            entity_id = state["entity_id"]
            attrs = state.get("attributes") or {}
            meta = entity_meta.get(entity_id) or {}
            device = devices.get(meta.get("device_id")) or {}
            area_id = meta.get("area_id") or device.get("area_id")
            items.append(
                {
                    "entity_id": entity_id,
                    "name": attrs.get("friendly_name") or meta.get("name") or meta.get("original_name") or entity_id,
                    "domain": entity_domain(entity_id),
                    "state": state.get("state"),
                    "unit": attrs.get("unit_of_measurement"),
                    "device_class": attrs.get("device_class"),
                    "available": state.get("state") != "unavailable",
                    "attributes": _public_attributes(attrs),
                    "capabilities": _capabilities(attrs),
                    "platform": meta.get("platform"),
                    "device": device.get("name_by_user") or device.get("name"),
                    "device_id": meta.get("device_id"),
                    "manufacturer": device.get("manufacturer"),
                    "model": device.get("model"),
                    "area": areas.get(area_id),
                    "ha_uid": identity_hash(meta.get("platform"), meta.get("unique_id")),
                },
            )
        items.sort(key=lambda item: item["entity_id"])
        result = {"entities": items, "metadata_available": entities_reg is not None}
        self._browse_cache = (time.monotonic(), result)
        return result

    async def browse_services(self) -> list[dict[str, Any]]:
        """HA action catalogue (domain → action → data fields) for the binding form pickers."""
        conn = self._conn
        if conn is None or conn.closed:
            raise HaNotConnectedError
        cached = self._services_cache
        if cached is not None and time.monotonic() - cached[0] < BROWSE_CACHE_SECONDS:
            return cached[1]
        raw = await conn.command({"type": "get_services"})
        domains: list[dict[str, Any]] = []
        for domain, services in sorted((raw or {}).items()) if isinstance(raw, dict) else []:
            if not isinstance(services, dict):
                continue
            entries = []
            for service, spec in sorted(services.items()):
                spec = spec if isinstance(spec, dict) else {}
                entries.append({"service": service, "name": _text(spec.get("name")), "fields": _service_fields(spec.get("fields"))})
            domains.append({"domain": domain, "services": entries})
        self._services_cache = (time.monotonic(), domains)
        return domains

    @staticmethod
    async def _optional_command(conn: HaConnection, command: str) -> list[Any] | None:
        """Registry lists need an HA admin token; without one the browser shows states only."""
        try:
            result = await conn.command({"type": command})
        except HaCommandError as exc:
            logger.info("HA WebSocket: %s unavailable (%s)", command, exc.code)
            return None
        return result if isinstance(result, list) else None

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        """Synchronous read via REST GET /api/states/{entity_id}."""
        if self._http_client is None:
            return None
        try:
            bc = HaBindingConfig(**binding.config)
            resp = await self._http_client.get(f"/api/states/{quote(bc.entity_id, safe='')}")
            resp.raise_for_status()
            value, _quality, stable = normalize(resolve(bc), resp.json())
            return value if stable else None
        except Exception:
            logger.exception("HA adapter read failed for binding %s", binding.id)
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(self, binding: Any, value: Any) -> None:
        await self._write(binding, value, logical_value=value)

    async def write_with_context(self, binding: Any, value: Any, *, logical_value: Any, **_kwargs: Any) -> bool:
        await self._write(binding, value, logical_value=logical_value)
        return True

    async def _write(self, binding: Any, value: Any, *, logical_value: Any) -> None:
        """Call the resolved HA action via REST POST /api/services/{domain}/{service}.

        *value* is already transformed by the WriteRouter (formula + value_map);
        *logical_value* is the typed DataPoint value that selects the action.
        Failures raise so they surface as failed writes, never as successes.
        """
        if self._http_client is None:
            raise HaActionError("Home Assistant adapter is not connected")
        bc, mapping = self._write_spec(binding)
        try:
            call = select_action(bc, mapping, value, logical_value)
        except NoActionForValueError as exc:
            raise HaActionError(f"{bc.entity_id}: {exc}") from exc
        if call is None:
            logger.info("HA adapter: no action configured for value %r of binding %s", logical_value, binding.id)
            return
        action = f"{call.domain}.{call.service}"
        try:
            resp = await self._http_client.post(f"/api/services/{call.domain}/{call.service}", json=call.body)
        except httpx.HTTPError as exc:
            await self._report_action_failure(action)
            raise HaActionError(f"{action} for {bc.entity_id} failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            await self._report_action_failure(action)
            message = _ha_error_message(resp)
            raise HaActionError(f"{action} for {bc.entity_id} failed: HTTP {resp.status_code} {message}".rstrip())
        logger.info("HA adapter write: %s entity=%s value=%r", action, bc.entity_id, value)
        if self._action_failed:
            self._action_failed = False
            if self._connected:
                await self._publish_connected()

    def _write_spec(self, binding: Any) -> tuple[HaBindingConfig, ResolvedMapping]:
        """Validated config + resolved mapping of a binding, cached until its config changes."""
        cached = self._write_specs.get(binding.id)
        if cached is not None and cached[0] == binding.config:
            return cached[1], cached[2]
        bc = HaBindingConfig(**binding.config)
        mapping = resolve(bc)
        self._write_specs[binding.id] = (copy.deepcopy(binding.config), bc, mapping)
        return bc, mapping

    async def _report_action_failure(self, action: str) -> None:
        self._action_failed = True
        await self._publish_status(
            self._connected,
            f"Home Assistant hat {action} abgelehnt",
            severity="warning",
            code="haActionFailed",
            params={"action": action},
        )
