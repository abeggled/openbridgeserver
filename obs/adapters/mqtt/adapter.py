"""MQTT Adapter

Verbindet sich mit einem externen MQTT-Broker als Datenquelle oder -senke.

Adapter-Konfiguration (adapter_configs.config in DB):
  host:     str   — MQTT Broker IP/Hostname (default: localhost)
  port:     int   — (default: 1883)
  username: str?  — optional
  password: str?  — optional

Binding-Konfiguration (pro AdapterBinding.config):
  topic:            str        — Topic zum Subscriben (SOURCE/BOTH) bzw. Publishen (DEST/BOTH)
  publish_topic:    str?       — Für DEST/BOTH: separates Publish-Topic (falls abweichend von topic)
  retain:           bool       — Retain-Flag beim Publishen (default: False)
  payload_template: str?       — Für DEST/BOTH: Payload-Template mit ###DP### als Platzhalter
  value_map:        dict[str,str]? — Wertzuordnung: z.B. {"0": "off", "1": "on"}
  source_data_type: str?       — SOURCE/BOTH: "string"|"int"|"float"|"bool"|"json"|"xml"|None(=auto)
  json_key:         str?       — Schlüssel zum Extrahieren aus JSON-Payload (source_data_type=="json")
  xml_path:         str?       — Element-Pfad (ET-XPath) zum Extrahieren aus XML-Payload (source_data_type=="xml")

Richtungs-Semantik:
  SOURCE  → Adapter subscribet auf topic, liefert Werte ins System
  DEST    → Adapter publisht auf publish_topic (oder topic) wenn write() aufgerufen wird
  BOTH    → Beides: subscriben auf topic, publishen auf publish_topic (oder topic)

source_data_type:
  Steuert, wie der eingehende Payload interpretiert wird (SOURCE/BOTH):
    None/"auto": json.loads-Erkennung (Standardverhalten)
    "string":    Wert direkt als String
    "int":       Konvertierung nach int (via float für Strings wie "22.0")
    "float":     Konvertierung nach float
    "bool":      true/false, on/off, 1/0 → Python bool
    "json":      Payload als JSON-Objekt parsen, dann json_key extrahieren

json_key:
  Wenn source_data_type=="json": Schlüssel im geparsten JSON-Objekt, dessen Wert als
  DataPoint-Wert übernommen wird. Leer = gesamtes Objekt.

xml_path:
  Wenn source_data_type=="xml": ElementTree-kompatibler XPath-Ausdruck relativ zum
  Root-Element (z.B. "temperature", "data/sensors/temperature").
  Leer = text-Inhalt des Root-Elements.
  Zahlenwerte werden automatisch nach int/float konvertiert, sonst String.

value_map:
  Wird auf eingehende (SOURCE, nach source_data_type-Parsing) und ausgehende (DEST) Werte
  angewendet. Schlüssel und Werte sind Strings; der Istwert wird via str() für den Lookup.

payload_template:
  Enthält den Platzhalter ###DP###, der durch den (ggf. per value_map transformierten) Wert
  ersetzt wird. Nicht-String-Werte werden als JSON serialisiert (z.B. true statt True).
  Leer = Wert wird direkt als Payload gesendet.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent
from obs.core.transformation import apply_source_type, apply_value_map

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class MqttAdapterConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = Field(default=None, json_schema_extra={"format": "password"})
    client_id: str | None = None  # explicit base ID; auto-generated from instance UUID when None
    tls: bool = False  # enable TLS/SSL (required for e.g. HiveMQ Cloud on port 8883)
    tls_insecure: bool = False  # skip certificate verification (self-signed certs)


class MqttBindingConfig(BaseModel):
    topic: str  # subscribe (SOURCE/BOTH) or publish (DEST)
    publish_topic: str | None = None  # DEST/BOTH: publish here if set, else use topic
    retain: bool = False  # retain flag when publishing
    payload_template: str | None = None  # DEST/BOTH: template with ###DP### placeholder
    source_data_type: str | None = None  # SOURCE/BOTH: "string"|"int"|"float"|"bool"|"json"|"xml"|None
    json_key: str | None = None  # key to extract when source_data_type=="json"
    xml_path: str | None = None  # ET-XPath path when source_data_type=="xml"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class MqttAdapter(AdapterBase):
    adapter_type = "MQTT"
    config_schema = MqttAdapterConfig
    binding_config_schema = MqttBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg: MqttAdapterConfig | None = None
        self._pub_task: asyncio.Task | None = None
        self._sub_task: asyncio.Task | None = None
        self._publish_queue: asyncio.Queue = asyncio.Queue()
        # topic → list of SOURCE/BOTH bindings subscribed to it
        self._topic_map: dict[str, list[Any]] = {}
        # set by connect(); two distinct IDs so pub + sub don't clash on the broker
        self._pub_client_id: str | None = None
        self._sub_client_id: str | None = None
        self._tls_context: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            import aiomqtt  # noqa: F401
        except ImportError:
            logger.error("aiomqtt not installed — MQTT adapter disabled")
            await self._publish_status(False, "aiomqtt not installed")
            return

        self._cfg = MqttAdapterConfig(**self._config)

        # Derive stable, non-empty client IDs from the instance UUID so that brokers
        # with allow_zero_length_clientid false (e.g. Mosquitto, HiveMQ) accept us.
        base_id = self._cfg.client_id or f"obs-mqtt-{self._instance_id.hex[:8]}"
        self._pub_client_id = f"{base_id}-pub"
        self._sub_client_id = f"{base_id}-sub"

        self._tls_context = None
        if self._cfg.tls:
            import ssl

            ctx = ssl.create_default_context()
            if self._cfg.tls_insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._tls_context = ctx

        self._pub_task = asyncio.create_task(self._publisher_loop(), name="mqtt-adapter-pub")
        await self._publish_status(False, f"Connecting to {self._cfg.host}:{self._cfg.port}...")
        logger.info("MQTT adapter started → %s:%d", self._cfg.host, self._cfg.port)

    async def disconnect(self) -> None:
        for task in (self._pub_task, self._sub_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._pub_task = None
        self._sub_task = None
        self._topic_map.clear()
        await self._publish_status(False, "Disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        if self._cfg is None:
            return

        # Rebuild topic → binding map for SOURCE/BOTH
        self._topic_map.clear()
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = MqttBindingConfig(**binding.config)
            except Exception:
                logger.warning("Invalid MQTT binding config for %s — skipped", binding.id)
                continue
            self._topic_map.setdefault(bc.topic, []).append(binding)

        logger.info(
            "MQTT adapter: %d subscribe topic(s): %s",
            len(self._topic_map),
            list(self._topic_map.keys()),
        )

        # Restart subscriber with updated topics
        if self._sub_task:
            self._sub_task.cancel()
            try:
                await self._sub_task
            except asyncio.CancelledError:
                pass
            self._sub_task = None

        if self._topic_map:
            self._sub_task = asyncio.create_task(self._subscriber_loop(), name="mqtt-adapter-sub")

    # ------------------------------------------------------------------
    # Subscriber loop
    # ------------------------------------------------------------------

    async def _subscriber_loop(self) -> None:
        import aiomqtt

        cfg = self._cfg
        while True:
            try:
                logger.info(
                    "MQTT adapter subscriber connecting → %s:%d user=%s",
                    cfg.host,
                    cfg.port,
                    cfg.username,
                )
                async with aiomqtt.Client(
                    hostname=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                    identifier=self._sub_client_id,
                    tls_context=self._tls_context,
                ) as client:
                    for topic in self._topic_map:
                        await client.subscribe(topic)
                        logger.info("MQTT adapter subscribed: %s", topic)
                    async for message in client.messages:
                        await self._on_message(str(message.topic), message.payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MQTT adapter subscriber error, reconnecting in 5 s")
                await asyncio.sleep(5)

    async def _on_message(self, topic: str, payload: bytes) -> None:
        entries = self._topic_map.get(topic)
        if not entries:
            return

        raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
        # Auto-parse baseline (used as fallback and for "json" source type)
        try:
            auto_value = json.loads(raw)
        except Exception:
            auto_value = raw

        for binding in entries:
            pub_value = auto_value
            try:
                bc = MqttBindingConfig(**binding.config)

                # --- source_data_type coercion / extraction ---
                pub_value = apply_source_type(
                    raw,
                    auto_value,
                    bc.source_data_type,
                    bc.json_key,
                    bc.xml_path,
                    binding.id,
                )

                # --- formula first (numeric), then value_map (text substitution) ---
                if binding.value_formula and pub_value is not None:
                    from obs.core.formula import apply_formula

                    pub_value = apply_formula(binding.value_formula, pub_value)
                pub_value = apply_value_map(pub_value, binding.value_map)
            except Exception:
                logger.exception("MQTT: error processing binding %s", binding.id)
            logger.info(
                "MQTT adapter received: topic=%s → dp=%s value=%r",
                topic,
                binding.datapoint_id,
                pub_value,
            )
            await self._bus.publish(
                DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=pub_value,
                    quality="good",
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                ),
            )

    # ------------------------------------------------------------------
    # Publisher loop
    # ------------------------------------------------------------------

    async def _publisher_loop(self) -> None:
        import aiomqtt

        cfg = self._cfg
        while True:
            try:
                logger.info(
                    "MQTT adapter publisher connecting → %s:%d user=%s",
                    cfg.host,
                    cfg.port,
                    cfg.username,
                )
                async with aiomqtt.Client(
                    hostname=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                    identifier=self._pub_client_id,
                    tls_context=self._tls_context,
                ) as client:
                    await self._publish_status(True, f"Connected to {cfg.host}:{cfg.port}")
                    logger.info("MQTT adapter publisher connected")
                    while True:
                        topic, payload, retain = await self._publish_queue.get()
                        await client.publish(topic, payload, retain=retain)
                        logger.info("MQTT adapter → %s retain=%s", topic, retain)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MQTT adapter publisher error, reconnecting in 5 s")
                await self._publish_status(False, f"Reconnecting to {cfg.host}:{cfg.port}...")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        # MQTT ist push-only — kein synchrones Read möglich
        return None

    async def write(self, binding: Any, value: Any) -> None:
        try:
            bc = MqttBindingConfig(**binding.config)
            topic = bc.publish_topic or bc.topic

            # value already transformed by write_router (formula + value_map)
            # Build payload
            if bc.payload_template:
                val_str = value if isinstance(value, str) else json.dumps(value)
                payload = bc.payload_template.replace("###DP###", val_str)
            else:
                payload = value if isinstance(value, str) else json.dumps(value)

            await self._publish_queue.put((topic, payload, bc.retain))
            logger.info(
                "MQTT adapter write queued: topic=%s value=%r retain=%s",
                topic,
                value,
                bc.retain,
            )
        except Exception:
            logger.exception("MQTT adapter write failed for binding %s", binding.id)
