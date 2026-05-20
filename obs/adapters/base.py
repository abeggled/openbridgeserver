"""AdapterBase ABC — Phase 2 / erweitert Phase 3 / Phase 5 (Multi-Instance)

Alle Protokoll-Adapter erben von dieser Klasse.
Phase-5-Erweiterungen:
  - instance_id: uuid.UUID  – eindeutige Instanz-ID (aus DB)
  - name: str               – benutzerfreundlicher Name (z.B. "KNX EG")
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class AdapterBase(ABC):
    """Abstract base class for all protocol adapters.

    Concrete subclasses must:
      1. Set adapter_type = "KNX"
      2. Set config_schema = MyAdapterConfig         (Pydantic, connection params)
      3. Set binding_config_schema = MyBindingConfig (Pydantic, per-binding params)
      4. Implement connect / disconnect / read / write
      5. Decorate with @register from adapters.registry
    """

    adapter_type: str  # e.g. "KNX"
    config_schema: type[BaseModel]  # API: /adapters/{type}/schema
    binding_config_schema: type[BaseModel]  # API: /adapters/{type}/binding-schema
    hidden: bool = False  # True = not shown in "create instance" UI

    def __init__(
        self,
        event_bus: Any,
        config: dict | None = None,
        instance_id: uuid.UUID | None = None,
        name: str | None = None,
    ) -> None:
        from obs.core.event_bus import EventBus

        self._bus: EventBus = event_bus
        self._config: dict = config or {}
        self._connected: bool = False
        self._bindings: list[Any] = []  # list[AdapterBinding]
        self._instance_id: uuid.UUID = instance_id or uuid.uuid4()
        self._instance_name: str = name or getattr(self, "adapter_type", "unknown")
        # Cached so REST API can serve last-known severity/detail without
        # subscribing to the EventBus (GUI polls /adapters/instances).
        self._last_severity: str = "ok"
        self._last_detail: str = ""

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

    @property
    def last_severity(self) -> str:
        return self._last_severity

    @property
    def last_detail(self) -> str:
        return self._last_detail

    async def _publish_status(self, connected: bool, detail: str = "", severity: str = "ok") -> None:
        from obs.core.event_bus import AdapterStatusEvent

        # severity="warning" signals degraded operation without changing the
        # connected flag — issue #466. All other severities track `connected`.
        if severity != "warning":
            self._connected = connected
        self._last_severity = severity
        self._last_detail = detail
        await self._bus.publish(
            AdapterStatusEvent(
                adapter_type=self.adapter_type,
                instance_id=self._instance_id,
                instance_name=self._instance_name,
                connected=self._connected,
                detail=detail,
                severity=severity,
            ),
        )
