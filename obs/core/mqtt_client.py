"""MQTT Client Wrapper — Phase 2

Wraps aiomqtt. Implements the MQTT topic strategy from ARCHITECTURE.md §6:

  dp/{uuid}/value       — Full JSON payload {v, u, t, q}
  dp/{uuid}/value/raw   — Bare value as string
  dp/{uuid}/set         — Inbound write requests (DEST / BOTH bindings)
  dp/{uuid}/status      — Adapter connection status for this DataPoint
  alias/{tag}/{name}/value  — Human-browsable alias (published only on value change)

Subscribes to dp/+/set and routes write requests back via the EventBus.

Architecture note (aiomqtt ≥ 2.0):
  aiomqtt's Client context manager is reusable but NOT reentrant — you cannot
  enter it twice on the same object. We therefore use two separate Client
  instances: one persistent subscriber loop and one persistent publisher loop
  fed by an asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MQTT Payload helpers
# ---------------------------------------------------------------------------


def build_payload(value: Any, unit: str | None, quality: str, ts: datetime | None = None) -> str:
    """Serialize a DataPoint value to the standard MQTT JSON payload."""
    return json.dumps(
        {
            "v": value,
            "u": unit,
            "t": (ts or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "q": quality,
        },
    )


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
    """Async MQTT publish/subscribe wrapper.

    Uses two separate aiomqtt.Client connections:
      - subscriber loop: receives dp/+/set messages
      - publisher loop:  drains an asyncio.Queue of outgoing messages

    Lifecycle:
        client = MqttClient(host, port, username, password)
        await client.start()          # launches both loops as background tasks
        await client.publish_value(dp, value, unit, quality)
        await client.stop()           # cancels both tasks
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
        self._pub_task: asyncio.Task | None = None
        self._sub_task: asyncio.Task | None = None
        self._publish_queue: asyncio.Queue = asyncio.Queue()
        self._write_handlers: list[Any] = []  # callbacks for dp/+/set messages
        # Stable, non-empty client IDs derived from connection target so that brokers
        # with allow_zero_length_clientid false accept the internal OBS connections.
        _short = hashlib.sha256(f"{host}:{port}".encode()).hexdigest()[:8]
        self._pub_id = f"obs-int-pub-{_short}"
        self._sub_id = f"obs-int-sub-{_short}"

    def on_write_request(self, handler) -> None:
        """Register a callback for inbound dp/{id}/set messages.

        handler signature: async def handler(datapoint_id: UUID, raw_payload: str)
        """
        self._write_handlers.append(handler)

    async def start(self) -> None:
        """Launch publisher and subscriber background tasks."""
        try:
            import aiomqtt  # noqa: F401
        except ImportError:
            logger.warning("aiomqtt not installed — MQTT disabled")
            return

        self._pub_task = asyncio.create_task(self._publisher_loop(), name="mqtt-publisher")
        self._sub_task = asyncio.create_task(self._subscriber_loop(), name="mqtt-subscriber")
        logger.info("MQTT client started → %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Cancel both loops."""
        for task in (self._pub_task, self._sub_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._pub_task = None
        self._sub_task = None
        logger.info("MQTT client stopped")

    # ------------------------------------------------------------------
    # Publish (enqueue — processed by _publisher_loop)
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
        """Enqueue a value publish. Non-blocking; delivered by publisher loop."""
        payload = build_payload(value, unit, quality, ts)
        raw = str(value)
        await self._publish_queue.put((topic_value(datapoint_id), payload, True))
        await self._publish_queue.put((topic_value_raw(datapoint_id), raw, True))
        if mqtt_alias_topic:
            await self._publish_queue.put((mqtt_alias_topic, payload, True))

    async def publish_status(self, datapoint_id: uuid.UUID, status: str) -> None:
        await self._publish_queue.put((topic_status(datapoint_id), status, True))

    # ------------------------------------------------------------------
    # Publisher loop — own connection, drains the publish queue
    # ------------------------------------------------------------------

    async def _publisher_loop(self) -> None:
        import aiomqtt

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password,
                    identifier=self._pub_id,
                ) as client:
                    logger.info("MQTT publisher connected to %s:%d", self._host, self._port)
                    while True:
                        topic, payload, retain = await self._publish_queue.get()
                        await client.publish(topic, payload, retain=retain)
                        logger.debug("MQTT → %s (retain=%s)", topic, retain)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MQTT publisher error, reconnecting in 5 s")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Subscriber loop — own connection, listens for dp/+/set
    # ------------------------------------------------------------------

    async def _subscriber_loop(self) -> None:
        import aiomqtt

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password,
                    identifier=self._sub_id,
                ) as client:
                    logger.info("MQTT subscriber connected, listening dp/+/set")
                    await client.subscribe("dp/+/set")
                    async for message in client.messages:
                        logger.info(
                            "MQTT set received: topic=%s payload=%r",
                            message.topic,
                            message.payload,
                        )
                        await self._handle_set_message(str(message.topic), message.payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MQTT subscriber error, reconnecting in 5 s")
                await asyncio.sleep(5)

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


def reset_mqtt_client() -> None:
    """Reset the MqttClient singleton. For testing only."""
    global _mqtt
    _mqtt = None


def init_mqtt_client(host: str, port: int, username: str | None, password: str | None) -> MqttClient:
    global _mqtt
    _mqtt = MqttClient(host, port, username, password)
    return _mqtt
