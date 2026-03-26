"""
MQTT Client Wrapper — Phase 2

Wraps aiomqtt. Implements the MQTT topic strategy from ARCHITECTURE.md §6:

  dp/{uuid}/value       — Full JSON payload {v, u, t, q}
  dp/{uuid}/value/raw   — Bare value as string
  dp/{uuid}/set         — Inbound write requests (DEST / BOTH bindings)
  dp/{uuid}/status      — Adapter connection status for this DataPoint
  alias/{tag}/{name}/value  — Human-browsable alias (published only on value change)

Subscribes to dp/+/set and routes write requests back via the EventBus.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
import uuid

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MQTT Payload helpers
# ---------------------------------------------------------------------------

def build_payload(value: Any, unit: str | None, quality: str, ts: datetime | None = None) -> str:
    """Serialize a DataPoint value to the standard MQTT JSON payload."""
    return json.dumps({
        "v": value,
        "u": unit,
        "t": (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "q": quality,
    })


def topic_value(datapoint_id: uuid.UUID) -> str:
    return f"dp/{datapoint_id}/value"


def topic_value_raw(datapoint_id: uuid.UUID) -> str:
    return f"dp/{datapoint_id}/value/raw"


def topic_set(datapoint_id: uuid.UUID) -> str:
    return f"dp/{datapoint_id}/set"


def topic_status(datapoint_id: uuid.UUID) -> str:
    return f"dp/{datapoint_id}/status"


def topic_alias(tag: str, name: str) -> str:
    return f"alias/{tag}/{name}/value"


# ---------------------------------------------------------------------------
# MqttClient
# ---------------------------------------------------------------------------

class MqttClient:
    """
    Async MQTT publish/subscribe wrapper.

    Lifecycle:
        client = MqttClient(host, port, username, password)
        await client.start()          # connects and starts listener loop
        await client.publish_value(dp, value, unit, quality)
        await client.stop()           # graceful disconnect
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client: Any = None          # aiomqtt.Client instance
        self._task: asyncio.Task | None = None
        self._write_handlers: list[Any] = []  # callbacks for dp/+/set messages

    def on_write_request(self, handler) -> None:
        """Register a callback for inbound dp/{id}/set messages.

        handler signature: async def handler(datapoint_id: UUID, raw_payload: str)
        """
        self._write_handlers.append(handler)

    async def start(self) -> None:
        """Connect to Mosquitto and start the subscriber task."""
        try:
            import aiomqtt  # noqa: PLC0415
        except ImportError:
            logger.warning("aiomqtt not installed — MQTT disabled")
            return

        self._client = aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
        )
        self._task = asyncio.create_task(self._listen_loop(), name="mqtt-listener")
        logger.info("MQTT client started → %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Cancel listener and disconnect."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._client = None
        logger.info("MQTT client stopped")

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish_value(
        self,
        datapoint_id: uuid.UUID,
        value: Any,
        unit: str | None,
        quality: str,
        mqtt_alias_topic: str | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Publish full JSON payload + raw topic. Optionally publishes alias."""
        if self._client is None:
            return

        payload = build_payload(value, unit, quality, ts)
        raw = str(value)

        async with self._client as client:
            await client.publish(topic_value(datapoint_id), payload)
            await client.publish(topic_value_raw(datapoint_id), raw)
            if mqtt_alias_topic:
                await client.publish(mqtt_alias_topic, payload)

    async def publish_status(self, datapoint_id: uuid.UUID, status: str) -> None:
        if self._client is None:
            return
        async with self._client as client:
            await client.publish(topic_status(datapoint_id), status)

    # ------------------------------------------------------------------
    # Subscribe / listener loop
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Subscribe to dp/+/set and route to registered write handlers."""
        try:
            import aiomqtt  # noqa: PLC0415
        except ImportError:
            return

        try:
            async with self._client as client:
                await client.subscribe("dp/+/set")
                logger.debug("MQTT subscribed to dp/+/set")
                async for message in client.messages:
                    await self._handle_set_message(str(message.topic), message.payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MQTT listener loop crashed")

    async def _handle_set_message(self, topic: str, payload: bytes) -> None:
        # topic format: dp/{uuid}/set
        parts = topic.split("/")
        if len(parts) != 3:
            return
        try:
            dp_id = uuid.UUID(parts[1])
        except ValueError:
            logger.debug("Ignoring set message with invalid UUID: %s", parts[1])
            return

        raw = payload.decode("utf-8", errors="replace")
        for handler in self._write_handlers:
            try:
                await handler(dp_id, raw)
            except Exception:
                logger.exception("Write handler raised for dp %s", dp_id)


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_mqtt: MqttClient | None = None


def get_mqtt_client() -> MqttClient:
    if _mqtt is None:
        raise RuntimeError("MqttClient not initialized — call init_mqtt_client() at startup")
    return _mqtt


def init_mqtt_client(host: str, port: int, username: str | None, password: str | None) -> MqttClient:
    global _mqtt
    _mqtt = MqttClient(host, port, username, password)
    return _mqtt
