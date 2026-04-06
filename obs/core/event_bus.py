"""
Internal async Event Bus — Phase 2

Decouples adapters from the core engine.
Adapters publish DataValueEvent; the core engine routes them to MQTT
and the DataPoint registry.

Usage:
    bus = EventBus()
    bus.subscribe(DataValueEvent, my_handler)
    await bus.publish(DataValueEvent(datapoint_id=..., value=21.4, ...))
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Type
import uuid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class DataValueEvent:
    """Fired by an adapter when it receives or reads a new value."""
    datapoint_id: uuid.UUID
    value: Any
    quality: str                        # "good" | "bad" | "uncertain"
    source_adapter: str                 # adapter_type string
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    binding_id: uuid.UUID | None = None


@dataclass
class AdapterStatusEvent:
    """Fired when an adapter connection state changes."""
    adapter_type: str
    connected: bool
    detail: str = ""
    instance_id: uuid.UUID | None = None
    instance_name: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# All event types that the bus understands
AnyEvent = DataValueEvent | AdapterStatusEvent

Handler = Callable[[AnyEvent], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """
    Async publish/subscribe bus for internal use only.

    - Subscribers are async coroutine functions.
    - Exceptions in handlers are logged but do not crash the bus.
    - All handlers for an event run concurrently (asyncio.gather).
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = {}

    def subscribe(self, event_type: Type[AnyEvent], handler: Handler) -> None:
        """Register *handler* to be called for every event of *event_type*."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: Type[AnyEvent], handler: Handler) -> None:
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    async def publish(self, event: AnyEvent) -> None:
        """Publish *event* to all registered handlers (concurrent, fire-and-forget errors)."""
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return

        results = await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.exception(
                    "EventBus handler %s raised for %s: %s",
                    handlers[i].__qualname__,
                    type(event).__name__,
                    result,
                )


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    if _bus is None:
        raise RuntimeError("EventBus not initialized — call init_event_bus() at startup")
    return _bus


def init_event_bus() -> EventBus:
    global _bus
    _bus = EventBus()
    return _bus
