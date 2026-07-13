"""AdapterBase ABC — Phase 2 / erweitert Phase 3 / Phase 5 (Multi-Instance)

Alle Protokoll-Adapter erben von dieser Klasse.
Phase-5-Erweiterungen:
  - instance_id: uuid.UUID  – eindeutige Instanz-ID (aus DB)
  - name: str               – benutzerfreundlicher Name (z.B. "KNX EG")
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class AdapterDelegationCapability(StrEnum):
    """Adapter-owned operations that a scoped non-admin may perform."""

    CREATE_DEVICE = "create_device"
    CREATE_DATAPOINT = "create_datapoint"
    LINK_BINDING = "link_binding"
    CONFIGURE_INSTANCE = "configure_instance"


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
    delegation_capabilities: frozenset[AdapterDelegationCapability] = frozenset()

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
        # i18n (issue #779): status details are emitted as a stable key suffix
        # (`detail_code`, under `adapters.statusDetail.*` in the locale files)
        # plus interpolation `detail_params`. The frontend translates the code;
        # `_last_detail` stays as a human-readable fallback for codeless/dynamic
        # messages (e.g. raw exception text).
        self._last_detail_code: str | None = None
        self._last_detail_params: dict[str, Any] = {}

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

    @property
    def last_detail_code(self) -> str | None:
        return self._last_detail_code

    @property
    def last_detail_params(self) -> dict[str, Any]:
        return self._last_detail_params

    async def _publish_status(
        self,
        connected: bool,
        detail: str = "",
        severity: str = "ok",
        *,
        code: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Publish an adapter status change.

        For i18n (issue #779) prefer a stable `code` (key suffix under
        `adapters.statusDetail.*`) plus `params` for interpolation; the frontend
        translates it. `detail` is the human-readable fallback shown when no code
        is given or the locale key is missing (e.g. raw exception text).
        """
        from obs.core.event_bus import AdapterStatusEvent

        # severity="warning" signals degraded operation without changing the
        # connected flag — issue #466. All other severities track `connected`.
        if severity != "warning":
            self._connected = connected
        self._last_severity = severity
        self._last_detail = detail
        self._last_detail_code = code
        self._last_detail_params = params or {}
        await self._bus.publish(
            AdapterStatusEvent(
                adapter_type=self.adapter_type,
                instance_id=self._instance_id,
                instance_name=self._instance_name,
                connected=self._connected,
                detail=detail,
                severity=severity,
                detail_code=code,
                detail_params=params or {},
            ),
        )
