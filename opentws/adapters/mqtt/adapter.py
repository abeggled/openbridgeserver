"""
MQTT Adapter

Verbindet sich mit einem externen MQTT-Broker als Datenquelle oder -senke.

Adapter-Konfiguration (adapter_configs.config in DB):
  host:     str   — MQTT Broker IP/Hostname (default: localhost)
  port:     int   — (default: 1883)
  username: str?  — optional
  password: str?  — optional

Binding-Konfiguration (pro AdapterBinding.config):
  topic:         str   — Topic zum Subscriben (SOURCE/BOTH) bzw. Publishen (DEST/BOTH)
  publish_topic: str?  — Für DEST/BOTH: separates Publish-Topic (falls abweichend von topic)
  retain:        bool  — Retain-Flag beim Publishen (default: False)

Richtungs-Semantik:
  SOURCE  → Adapter subscribet auf topic, liefert Werte ins System
  DEST    → Adapter publisht auf publish_topic (oder topic) wenn write() aufgerufen wird
  BOTH    → Beides: subscriben auf topic, publishen auf publish_topic (oder topic)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from opentws.adapters.base import AdapterBase
from opentws.adapters.registry import register
from opentws.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

class MqttAdapterConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = Field(default=None, json_schema_extra={"format": "password"})


class MqttBindingConfig(BaseModel):
    topic: str                        # subscribe (SOURCE/BOTH) or publish (DEST)
    publish_topic: str | None = None  # DEST/BOTH: publish here if set, else use topic
    retain: bool = False              # retain flag when publishing


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
        self._pub_task = asyncio.create_task(self._publisher_loop(), name="mqtt-adapter-pub")
        await self._publish_status(True, f"Connected to {self._cfg.host}:{self._cfg.port}")
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
            len(self._topic_map), list(self._topic_map.keys()),
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
            self._sub_task = asyncio.create_task(
                self._subscriber_loop(), name="mqtt-adapter-sub"
            )

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
                    cfg.host, cfg.port, cfg.username,
                )
                async with aiomqtt.Client(
                    hostname=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
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
        try:
            value = json.loads(raw)
        except Exception:
            value = raw

        for binding in entries:
            logger.info("MQTT adapter received: topic=%s → dp=%s value=%r", topic, binding.datapoint_id, value)
            await self._bus.publish(DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=value,
                quality="good",
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            ))

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
                    cfg.host, cfg.port, cfg.username,
                )
                async with aiomqtt.Client(
                    hostname=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                ) as client:
                    logger.info("MQTT adapter publisher connected")
                    while True:
                        topic, payload, retain = await self._publish_queue.get()
                        await client.publish(topic, payload, retain=retain)
                        logger.info("MQTT adapter → %s retain=%s", topic, retain)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MQTT adapter publisher error, reconnecting in 5 s")
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
            payload = json.dumps(value) if not isinstance(value, str) else value
            await self._publish_queue.put((topic, payload, bc.retain))
            logger.info("MQTT adapter write queued: topic=%s value=%r retain=%s", topic, value, bc.retain)
        except Exception:
            logger.exception("MQTT adapter write failed for binding %s", binding.id)
