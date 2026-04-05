"""
KNX Adapter — Phase 3

Verbindet sich mit einem KNX/IP-Gateway (Tunneling oder Routing).
Nutzt xknx für das Protokoll, eigenen DPTRegistry für Codierung.

xknx ≥ 3.x: Device-dispatch läuft über _iter_remote_values() → GA-Map.
has_group_address() wird in xknx 3.x nicht mehr für Dispatch verwendet.
Der _TelegramSniffer wird deshalb NACH dem Laden der Bindings erstellt.

Binding-Konfiguration (pro AdapterBinding.config):
  group_address:       str   — Gruppenadresse z.B. "1/2/3"
  dpt_id:              str   — z.B. "DPT9.001"
  state_group_address: str?  — Rückmelde-GA für DEST-Bindings (optional)

Adapter-Konfiguration (adapter_configs.config in DB):
  connection_type: "tunneling" | "routing"   (default: tunneling)
  host:            str                        (KNX/IP Gateway IP)
  port:            int                        (default: 3671)
  individual_address: str                     (default: "1.1.255")
  local_ip:        str?                       (für Routing/Multicast)
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

from opentws.adapters.base import AdapterBase
from opentws.adapters.knx.dpt_registry import DPTRegistry
from opentws.adapters.registry import register
from opentws.core.event_bus import DataValueEvent

# Import APCI classes at module level so missing symbols fail loudly at startup
try:
    from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, GroupValueRead
    _APCI_IMPORTED = True
except ImportError:
    GroupValueWrite = None  # type: ignore[assignment,misc]
    GroupValueResponse = None  # type: ignore[assignment,misc]
    GroupValueRead = None  # type: ignore[assignment,misc]
    _APCI_IMPORTED = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

class KnxAdapterConfig(BaseModel):
    connection_type: Literal["tunneling", "routing"] = "tunneling"
    host: str = "192.168.1.100"
    port: int = 3671
    individual_address: str = "1.1.255"
    local_ip: str | None = None


class KnxBindingConfig(BaseModel):
    group_address: str                          # z.B. "1/2/3"
    dpt_id: str = "DPT1.001"
    state_group_address: str | None = None      # DEST-Bindings Rückmelde-GA
    respond_to_read: bool = False               # SOURCE: antworte auf GroupValueRead mit aktuellem Wert


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class KnxAdapter(AdapterBase):
    adapter_type = "KNX"
    config_schema = KnxAdapterConfig
    binding_config_schema = KnxBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._xknx: Any = None
        self._sniffer: Any = None
        self._ga_source_map: dict[str, list[tuple[Any, Any]]] = {}
        self._ga_respond_map: dict[str, list[tuple[Any, Any]]] = {}
        self._value_getter: Any = None

    def set_value_getter(self, getter: Any) -> None:
        """Set a callable that returns ValueState | None for a datapoint UUID."""
        self._value_getter = getter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType
        except ImportError:
            logger.error("xknx not installed — KNX adapter disabled")
            await self._publish_status(False, "xknx not installed")
            return

        cfg = KnxAdapterConfig(**self._config)
        conn_type = (
            ConnectionType.ROUTING
            if cfg.connection_type == "routing"
            else ConnectionType.TUNNELING
        )
        conn_cfg = ConnectionConfig(
            connection_type=conn_type,
            gateway_ip=cfg.host,
            gateway_port=cfg.port,
            local_ip=cfg.local_ip,
        )

        self._xknx = XKNX(connection_config=conn_cfg)

        try:
            await self._xknx.start()
            await self._publish_status(True, f"Connected to {cfg.host}:{cfg.port}")
            logger.info("KNX adapter connected: %s:%d (%s)", cfg.host, cfg.port, cfg.connection_type)
        except Exception as exc:
            await self._publish_status(False, str(exc))
            logger.exception("KNX connect failed")

    async def disconnect(self) -> None:
        self._sniffer = None
        if self._xknx:
            try:
                await self._xknx.stop()
            except Exception:
                logger.exception("KNX disconnect error")
        await self._publish_status(False, "Disconnected")
        self._xknx = None

    # ------------------------------------------------------------------
    # Bindings — sniffer is created/recreated here so _iter_remote_values
    # already knows the registered GAs at Device.__init__ time.
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        """Rebuild GA→binding map and re-register the sniffer Device."""
        self._ga_source_map.clear()
        self._ga_respond_map.clear()
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = KnxBindingConfig(**binding.config)
            except Exception:
                logger.warning("Invalid KNX binding config for %s — skipped", binding.id)
                continue

            dpt = DPTRegistry.get(bc.dpt_id)
            entry = (binding, dpt)
            self._ga_source_map.setdefault(bc.group_address, []).append(entry)
            if bc.state_group_address:
                self._ga_source_map.setdefault(bc.state_group_address, []).append(entry)

            if bc.respond_to_read:
                self._ga_respond_map.setdefault(bc.group_address, []).append(entry)

        logger.info(
            "KNX: %d source GAs from %d bindings: %s",
            len(self._ga_source_map), len(self._bindings), list(self._ga_source_map.keys()),
        )

        if not self._xknx:
            return

        # Remove old sniffer so it's not in xknx.devices twice
        if self._sniffer is not None:
            try:
                self._xknx.devices.async_remove(self._sniffer)
                logger.debug("KNX: old sniffer removed")
            except Exception as exc:
                logger.debug("KNX: sniffer remove: %s", exc)
            self._sniffer = None

        if not self._ga_source_map:
            return

        # Create new sniffer with current GAs baked into _iter_remote_values().
        # In xknx 3.x, Device.__init__ may or may not auto-register via
        # xknx.devices.async_add(self). We check the count and register manually
        # if needed.
        try:
            devices_before = len(list(self._xknx.devices))
            self._sniffer = _build_sniffer(self._xknx, self._ga_source_map, self)
            devices_after = len(list(self._xknx.devices))
            logger.info(
                "KNX: sniffer created, devices count: %d → %d",
                devices_before, devices_after,
            )

            if devices_after == devices_before:
                # Device.__init__ didn't auto-register → do it explicitly
                logger.info("KNX: auto-registration skipped, calling async_add explicitly")
                self._xknx.devices.async_add(self._sniffer)
                logger.info(
                    "KNX: after explicit async_add, devices count: %d",
                    len(list(self._xknx.devices)),
                )

            logger.info("KNX: sniffer registered for GAs: %s", list(self._ga_source_map.keys()))
        except Exception:
            logger.exception("KNX: failed to create/register sniffer device")

    # ------------------------------------------------------------------
    # Inbound telegram handler (called by sniffer.process)
    # ------------------------------------------------------------------

    async def _on_telegram(self, telegram: Any) -> None:
        try:
            if not _APCI_IMPORTED:
                logger.error("KNX: xknx.telegram.apci not importable")
                return

            ga = str(telegram.destination_address)

            # Handle incoming read requests: respond with current persisted value
            if isinstance(telegram.payload, GroupValueRead):
                await self._handle_read_request(ga)
                return

            if not isinstance(telegram.payload, (GroupValueWrite, GroupValueResponse)):
                return

            entries = self._ga_source_map.get(ga)
            if not entries:
                return

            raw = _telegram_to_bytes(telegram)
            for binding, dpt in entries:
                try:
                    value = dpt.decoder(raw)
                    quality = "good"
                except Exception as exc:
                    logger.warning("KNX DPT decode error for %s (%s): %s", ga, dpt.dpt_id, exc)
                    value = raw.hex() if isinstance(raw, (bytes, bytearray)) else raw
                    quality = "uncertain"

                if binding.value_formula and quality == "good":
                    from opentws.core.formula import apply_formula
                    value = apply_formula(binding.value_formula, value)
                if binding.value_map:
                    from opentws.core.transformation import apply_value_map
                    value = apply_value_map(value, binding.value_map)
                logger.info("KNX value: GA=%s → dp=%s value=%s", ga, binding.datapoint_id, value)
                await self._bus.publish(DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=value,
                    quality=quality,
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                ))
        except Exception:
            logger.exception("KNX _on_telegram unhandled exception")

    async def _handle_read_request(self, ga: str) -> None:
        """Respond to a GroupValueRead with the current datapoint value if quality is 'good'."""
        entries = self._ga_respond_map.get(ga)
        if not entries or not self._value_getter or not self._xknx:
            return
        for binding, dpt in entries:
            try:
                state = self._value_getter(binding.datapoint_id)
                if state is None or state.quality != "good" or state.value is None:
                    logger.debug(
                        "KNX read request for GA=%s: no good value for dp=%s — not responding",
                        ga, binding.datapoint_id,
                    )
                    continue
                from xknx.telegram import Telegram
                from xknx.telegram.address import GroupAddress
                from xknx.dpt import DPTArray, DPTBinary
                raw = dpt.encoder(state.value)
                # DPTBinary only for 1-bit boolean DPTs; all others need DPTArray
                if dpt.data_type == "BOOLEAN":
                    payload_value = DPTBinary(raw[0])
                else:
                    payload_value = DPTArray(list(raw))
                telegram = Telegram(
                    destination_address=GroupAddress(ga),
                    payload=GroupValueResponse(payload_value),
                )
                await self._xknx.telegrams.put(telegram)
                logger.info(
                    "KNX read response: GA=%s dp=%s value=%s raw=%s",
                    ga, binding.datapoint_id, state.value, raw.hex(),
                )
            except Exception:
                logger.exception("KNX _handle_read_request failed for GA=%s binding=%s", ga, binding.id)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        if not self._xknx:
            return None
        try:
            from xknx.telegram import Telegram
            from xknx.telegram.address import GroupAddress
            from xknx.telegram.apci import GroupValueRead

            bc = KnxBindingConfig(**binding.config)
            ga = bc.state_group_address or bc.group_address
            telegram = Telegram(
                destination_address=GroupAddress(ga),
                payload=GroupValueRead(),
            )
            await self._xknx.telegrams.put(telegram)
        except Exception:
            logger.exception("KNX read failed for binding %s", binding.id)
        return None

    async def write(self, binding: Any, value: Any) -> None:
        if not self._xknx:
            return
        try:
            from xknx.telegram import Telegram
            from xknx.telegram.address import GroupAddress
            from xknx.telegram.apci import GroupValueWrite as _GVW
            from xknx.dpt import DPTArray, DPTBinary  # xknx ≥ 3.x

            bc = KnxBindingConfig(**binding.config)
            dpt = DPTRegistry.get(bc.dpt_id)
            raw = dpt.encoder(value)

            # DPTBinary only for 1-bit boolean DPTs; all others (incl. 1-byte
            # DPT 5.x with values 0-255) need DPTArray to avoid ConversionError
            if dpt.data_type == "BOOLEAN":
                payload_value = DPTBinary(raw[0])
            else:
                payload_value = DPTArray(list(raw))
            telegram = Telegram(
                destination_address=GroupAddress(bc.group_address),
                payload=_GVW(payload_value),
            )
            await self._xknx.telegrams.put(telegram)
            logger.info("KNX write: GA=%s value=%s raw=%s", bc.group_address, value, raw.hex())
        except Exception:
            logger.exception("KNX write failed for binding %s", binding.id)


# ---------------------------------------------------------------------------
# Sniffer Device factory — defined outside class to avoid closure issues
# ---------------------------------------------------------------------------

def _build_sniffer(xknx_instance: Any, ga_source_map: dict, adapter: KnxAdapter) -> Any:
    """
    Build and register a minimal xknx Device that receives all source GAs.

    In xknx ≥ 3.x, Device.__init__ calls xknx.devices.async_add(self), which
    reads _iter_remote_values() to build the internal GA→device dispatch map.
    We must assign self._remote_values BEFORE super().__init__() is called.
    """
    from xknx.devices import Device as XknxDevice
    from xknx.remote_value import RemoteValue
    from xknx.telegram.address import GroupAddress

    # Minimal RemoteValue subclass — just registers a GA, no DPT decoding
    class _PassthroughRV(RemoteValue):  # type: ignore[type-arg]
        def from_knx(self, raw_array: Any) -> bytes:
            return bytes(raw_array) if raw_array else b""

        def to_knx(self, value: Any) -> Any:
            return []

        @property
        def unit_of_measurement(self) -> str | None:
            return None

    # One RemoteValue per source GA, using group_address_state (read-only sensor)
    remote_values = [
        _PassthroughRV(
            xknx_instance,
            group_address_state=GroupAddress(ga),
            device_name="opentws_sniffer",
            feature_name=ga,
        )
        for ga in ga_source_map
    ]

    class _TelegramSniffer(XknxDevice):
        def __init__(self) -> None:
            # Set _remote_values BEFORE super().__init__() so that
            # _iter_remote_values() returns the correct GAs when
            # Device.__init__ calls xknx.devices.async_add(self).
            self._remote_values = remote_values
            super().__init__(xknx_instance, "opentws_sniffer")

        def _iter_remote_values(self):  # type: ignore[override]
            return iter(self._remote_values)

        def process(self, telegram: Any) -> bool:
            # xknx 3.x calls device.process() WITHOUT await (devices.py:108),
            # so this must be synchronous. Schedule the async handler as a task.
            import asyncio
            ga = str(telegram.destination_address)
            logger.info("KNX sniffer.process: GA=%s", ga)
            asyncio.ensure_future(adapter._on_telegram(telegram))
            return True

    return _TelegramSniffer()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _telegram_to_bytes(telegram: Any) -> bytes:
    """Extract raw payload bytes from a KNX telegram."""
    try:
        v = telegram.payload.value
        if hasattr(v, "value"):
            inner = v.value
            if isinstance(inner, (list, tuple)):
                return bytes(inner)
            return bytes([inner & 0x3F])
        if isinstance(v, (list, tuple)):
            return bytes(v)
        if isinstance(v, int):
            return bytes([v & 0x3F])
        return bytes(v) if v else b"\x00"
    except Exception:
        logger.exception("KNX _telegram_to_bytes failed")
        return b"\x00"
