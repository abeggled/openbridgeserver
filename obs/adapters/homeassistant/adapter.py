"""
Home Assistant Adapter

Verbindet open bridge server mit einer Home Assistant Instanz.
Liest Entitätszustände über die WebSocket-API (Echtzeit state_changed Events)
und schreibt via REST-API (Service Calls).

Adapter-Konfiguration (adapter_instances.config in DB):
  host:    str   — Hostname/IP der HA-Instanz (default: homeassistant.local)
  port:    int   — Port (default: 8123)
  token:   str   — Long-Lived Access Token
  ssl:     bool  — HTTPS/WSS verwenden (default: False)

Binding-Konfiguration (pro AdapterBinding.config):
  entity_id:        str        — HA-Entity-ID (z.B. "light.living_room")
  attribute:        str?       — Attribut-Schlüssel (None = state-Feld)
  service_domain:   str?       — Domain für Service-Call (default: aus entity_id ableiten)
  service_name:     str?       — Service-Name beim Write (default: turn_on/turn_off bei bool)
  service_data_key: str?       — Schlüssel im Service-Data für numerische/string Werte
                                  (z.B. "value" für input_number, "brightness" für Dimmer)

Richtungs-Semantik:
  SOURCE  → WebSocket: subscribe auf state_changed events, liefert Werte ins System
  DEST    → REST: Service Call bei write()
  BOTH    → Beides

Write-Logik:
  bool-Wert  → turn_on / turn_off (domain aus entity_id, unless service_domain/service_name set)
  sonstige   → {domain}.{service_name or "set_value"} mit {service_data_key: value}
               Kein service_data_key → entity_id-only Service Call

Attribut-Auflösung (SOURCE/read):
  attribute=None → new_state.state (z.B. "on", "22.5")
  attribute="brightness" → new_state.attributes["brightness"]
  Numerische Strings werden automatisch nach int/float konvertiert.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent
from obs.core.transformation import apply_value_map

logger = logging.getLogger(__name__)

# WebSocket message ID counter (process-global, simple monotone increment)
_ws_id_counter = 0


def _next_ws_id() -> int:
    global _ws_id_counter
    _ws_id_counter += 1
    return _ws_id_counter


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

class HaAdapterConfig(BaseModel):
    host: str = "homeassistant.local"
    port: int = 8123
    token: str = Field(default="", json_schema_extra={"format": "password"})
    ssl: bool = False


class HaBindingConfig(BaseModel):
    entity_id: str                                  # e.g. "light.living_room"
    attribute: str | None = None                    # None → state field; "brightness" → attribute
    service_domain: str | None = None              # override: e.g. "homeassistant"
    service_name: str | None = None                # override: e.g. "toggle", "set_value"
    service_data_key: str | None = None            # numeric/string write key, e.g. "value", "brightness"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_state(raw: str) -> Any:
    """Auto-convert HA state string to Python type."""
    if raw in ("on", "true"):
        return True
    if raw in ("off", "false"):
        return False
    try:
        int_val = int(raw)
        return int_val
    except (ValueError, TypeError):
        pass
    try:
        float_val = float(raw)
        return float_val
    except (ValueError, TypeError):
        pass
    return raw


def _domain_from_entity(entity_id: str) -> str:
    """Extract domain from entity_id (part before first '.')."""
    return entity_id.split(".")[0]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class HomeAssistantAdapter(AdapterBase):
    adapter_type = "HOME_ASSISTANT"
    config_schema = HaAdapterConfig
    binding_config_schema = HaBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg: HaAdapterConfig | None = None
        self._ws_task: asyncio.Task | None = None
        self._http_client: httpx.AsyncClient | None = None
        # entity_id → list of SOURCE/BOTH bindings
        self._entity_map: dict[str, list[Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError:
            logger.error("websockets not installed — Home Assistant adapter disabled")
            await self._publish_status(False, "websockets not installed")
            return

        self._cfg = HaAdapterConfig(**self._config)
        if not self._cfg.token:
            logger.error("Home Assistant adapter: no token configured")
            await self._publish_status(False, "Kein Token konfiguriert")
            return

        scheme = "https" if self._cfg.ssl else "http"
        base_url = f"{scheme}://{self._cfg.host}:{self._cfg.port}"
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._cfg.token}"},
            timeout=10.0,
        )

        await self._publish_status(True, f"Verbunden mit {self._cfg.host}:{self._cfg.port}")
        logger.info("Home Assistant adapter started → %s:%d ssl=%s", self._cfg.host, self._cfg.port, self._cfg.ssl)

    async def disconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._entity_map.clear()
        await self._publish_status(False, "Getrennt")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        if self._cfg is None:
            return

        self._entity_map.clear()
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = HaBindingConfig(**binding.config)
            except Exception:
                logger.warning("Ungültige HA Binding-Konfiguration für %s — übersprungen", binding.id)
                continue
            self._entity_map.setdefault(bc.entity_id, []).append(binding)

        logger.info(
            "HA adapter: %d entity subscription(s): %s",
            len(self._entity_map), list(self._entity_map.keys()),
        )

        # Restart WebSocket subscriber with updated entity list
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._entity_map:
            # Initial read: publish current state for all SOURCE/BOTH bindings immediately
            asyncio.create_task(self._initial_read_all(), name="ha-adapter-init-read")
            self._ws_task = asyncio.create_task(
                self._ws_loop(), name="ha-adapter-ws"
            )

    async def _initial_read_all(self) -> None:
        """Read and publish current state for all SOURCE/BOTH bindings via REST on startup."""
        if self._http_client is None:
            return
        for bindings in self._entity_map.values():
            for binding in bindings:
                try:
                    bc = HaBindingConfig(**binding.config)
                    resp = await self._http_client.get(f"/api/states/{bc.entity_id}")
                    resp.raise_for_status()
                    state_obj: dict = resp.json()

                    if bc.attribute:
                        attrs = state_obj.get("attributes") or {}
                        raw_val = attrs.get(bc.attribute)
                    else:
                        raw_str = state_obj.get("state", "unavailable")
                        raw_val = _coerce_state(raw_str) if raw_str not in ("unavailable", "unknown") else None

                    pub_value = apply_value_map(raw_val, binding.value_map)
                    if binding.value_formula and pub_value is not None:
                        from obs.core.formula import apply_formula
                        pub_value = apply_formula(binding.value_formula, pub_value)

                    logger.info(
                        "HA adapter initial read: entity=%s attr=%s → dp=%s value=%r",
                        bc.entity_id, bc.attribute or "state", binding.datapoint_id, pub_value,
                    )
                    await self._bus.publish(DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=pub_value,
                        quality="good",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ))
                except Exception:
                    logger.exception("HA adapter initial read failed for binding %s", binding.id)

    # ------------------------------------------------------------------
    # WebSocket loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        import websockets

        cfg = self._cfg
        ws_scheme = "wss" if cfg.ssl else "ws"
        uri = f"{ws_scheme}://{cfg.host}:{cfg.port}/api/websocket"

        while True:
            try:
                logger.info("HA adapter WebSocket connecting → %s", uri)
                async with websockets.connect(uri) as ws:
                    # Step 1: receive auth_required
                    msg = json.loads(await ws.recv())
                    if msg.get("type") != "auth_required":
                        logger.error("HA WebSocket: unexpected first message: %s", msg)
                        await asyncio.sleep(10)
                        continue

                    # Step 2: authenticate
                    await ws.send(json.dumps({"type": "auth", "access_token": cfg.token}))
                    msg = json.loads(await ws.recv())
                    if msg.get("type") == "auth_invalid":
                        logger.error("HA WebSocket: authentication failed — check token")
                        await self._publish_status(False, "Token ungültig")
                        await asyncio.sleep(60)
                        continue
                    if msg.get("type") != "auth_ok":
                        logger.error("HA WebSocket: unexpected auth response: %s", msg)
                        await asyncio.sleep(10)
                        continue

                    logger.info("HA WebSocket authenticated (ha_version=%s)", msg.get("ha_version", "?"))
                    await self._publish_status(True, f"Verbunden mit HA {msg.get('ha_version', '')}")

                    # Step 3: subscribe to state_changed events
                    sub_id = _next_ws_id()
                    await ws.send(json.dumps({
                        "id": sub_id,
                        "type": "subscribe_events",
                        "event_type": "state_changed",
                    }))
                    msg = json.loads(await ws.recv())
                    if not msg.get("success", False):
                        logger.error("HA WebSocket: subscribe failed: %s", msg)
                        await asyncio.sleep(10)
                        continue

                    logger.info("HA WebSocket subscribed to state_changed events")

                    # Step 4: receive events
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "event" and msg.get("id") == sub_id:
                            event = msg.get("event", {})
                            if event.get("event_type") == "state_changed":
                                data = event.get("data", {})
                                eid = data.get("entity_id", "?")
                                logger.debug("HA WS event: entity=%s", eid)
                                if eid in self._entity_map:
                                    logger.info("HA WS event (subscribed): entity=%s new_state=%s",
                                                eid,
                                                (data.get("new_state") or {}).get("state", "?"))
                                await self._on_state_changed(data)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HA adapter WebSocket error, reconnecting in 10 s")
                await self._publish_status(False, "WebSocket Fehler — reconnecting")
                await asyncio.sleep(10)

    async def _on_state_changed(self, data: dict) -> None:
        """Process a state_changed event from Home Assistant."""
        entity_id: str = data.get("entity_id", "")
        entries = self._entity_map.get(entity_id)
        if not entries:
            return

        new_state: dict = data.get("new_state") or {}
        if not new_state:
            return  # entity removed

        for binding in entries:
            try:
                bc = HaBindingConfig(**binding.config)

                # Extract raw value from state or attribute
                if bc.attribute:
                    attrs = new_state.get("attributes") or {}
                    raw_val = attrs.get(bc.attribute)
                else:
                    raw_str = new_state.get("state", "unavailable")
                    raw_val = _coerce_state(raw_str) if raw_str not in ("unavailable", "unknown") else None

                # Apply value_map
                pub_value = apply_value_map(raw_val, binding.value_map)

                if binding.value_formula and pub_value is not None:
                    from obs.core.formula import apply_formula
                    pub_value = apply_formula(binding.value_formula, pub_value)

            except Exception:
                logger.exception("HA adapter: error processing binding %s", binding.id)
                continue

            logger.info(
                "HA adapter state_changed: entity=%s attr=%s → dp=%s value=%r",
                entity_id, bc.attribute or "state", binding.datapoint_id, pub_value,
            )
            await self._bus.publish(DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=pub_value,
                quality="good",
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            ))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        """Synchronous read via REST GET /api/states/{entity_id}."""
        if self._http_client is None:
            return None
        try:
            bc = HaBindingConfig(**binding.config)
            resp = await self._http_client.get(f"/api/states/{bc.entity_id}")
            resp.raise_for_status()
            state_obj: dict = resp.json()

            if bc.attribute:
                attrs = state_obj.get("attributes") or {}
                return attrs.get(bc.attribute)
            else:
                raw = state_obj.get("state", "unavailable")
                return _coerce_state(raw) if raw not in ("unavailable", "unknown") else None
        except Exception:
            logger.exception("HA adapter read failed for binding %s", binding.id)
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(self, binding: Any, value: Any) -> None:
        """Write via REST POST /api/services/{domain}/{service}."""
        if self._http_client is None:
            logger.warning("HA adapter write called but HTTP client not initialized")
            return
        try:
            bc = HaBindingConfig(**binding.config)

            # Apply value_map (general binding-level field)
            mapped = apply_value_map(value, binding.value_map)

            domain = bc.service_domain or _domain_from_entity(bc.entity_id)

            # Determine service name
            if bc.service_name:
                service = bc.service_name
            elif isinstance(mapped, bool):
                service = "turn_on" if mapped else "turn_off"
            else:
                service = "set_value"

            # Build service data
            service_data: dict = {"entity_id": bc.entity_id}
            if bc.service_data_key and not isinstance(mapped, bool):
                service_data[bc.service_data_key] = mapped

            resp = await self._http_client.post(
                f"/api/services/{domain}/{service}",
                json=service_data,
            )
            resp.raise_for_status()
            logger.info(
                "HA adapter write: %s.%s entity=%s value=%r",
                domain, service, bc.entity_id, mapped,
            )
        except Exception:
            logger.exception("HA adapter write failed for binding %s", binding.id)
