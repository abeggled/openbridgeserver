"""
AdapterBase ABC — Phase 2 / erweitert Phase 3

Alle Protokoll-Adapter erben von dieser Klasse.
Phase-3-Erweiterungen:
  - config: dict   – aus DB geladen (Verbindungsparameter)
  - binding_config_schema – Pydantic-Schema für Binding-Konfiguration
  - reload_bindings() – wird vom Registry aufgerufen nach connect()
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class AdapterBase(ABC):
    """
    Abstract base class for all protocol adapters.

    Concrete subclasses must:
      1. Set adapter_type = "KNX"
      2. Set config_schema = MyAdapterConfig         (Pydantic, connection params)
      3. Set binding_config_schema = MyBindingConfig (Pydantic, per-binding params)
      4. Implement connect / disconnect / read / write
      5. Decorate with @register from adapters.registry
    """

    adapter_type: str                              # e.g. "KNX"
    config_schema: type[BaseModel]                 # API: /adapters/{type}/schema
    binding_config_schema: type[BaseModel]         # API: /adapters/{type}/binding-schema

    def __init__(self, event_bus: Any, config: dict | None = None) -> None:
        from opentws.core.event_bus import EventBus
        self._bus: EventBus = event_bus
        self._config: dict = config or {}
        self._connected: bool = False
        self._bindings: list[Any] = []  # list[AdapterBinding]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the protocol endpoint."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect gracefully."""
        ...

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def reload_bindings(self, bindings: list[Any]) -> None:
        """Replace the active binding list and reconfigure listeners/pollers."""
        self._bindings = bindings
        await self._on_bindings_reloaded()

    async def _on_bindings_reloaded(self) -> None:
        """Hook: called after reload_bindings(). Override to reconfigure."""
        pass

    def get_bindings(self) -> list[Any]:
        return list(self._bindings)

    # ------------------------------------------------------------------
    # Data exchange (called by write-routing in main)
    # ------------------------------------------------------------------

    @abstractmethod
    async def read(self, binding: Any) -> Any:
        """Read current value for *binding*. Returns raw Python value."""
        ...

    @abstractmethod
    async def write(self, binding: Any, value: Any) -> None:
        """Write *value* to the protocol endpoint for *binding*."""
        ...

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    async def _publish_status(self, connected: bool, detail: str = "") -> None:
        from opentws.core.event_bus import AdapterStatusEvent
        self._connected = connected
        await self._bus.publish(AdapterStatusEvent(
            adapter_type=self.adapter_type,
            connected=connected,
            detail=detail,
        ))
