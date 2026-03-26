"""
KNX Adapter — Phase 3

Verbindet sich mit einem KNX/IP-Gateway (Tunneling oder Routing).
Nutzt xknx für das Protokoll, eigenen DPTRegistry für Codierung.

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

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from opentws.adapters.base import AdapterBase
from opentws.adapters.registry import register
from opentws.adapters.knx.dpt_registry import DPTRegistry
from opentws.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

class KnxAdapterConfig(BaseModel):
    connection_type: str = "tunneling"          # tunneling | routing
    host: str = "192.168.1.100"
    port: int = 3671
    individual_address: str = "1.1.255"
    local_ip: str | None = None


class KnxBindingConfig(BaseModel):
    group_address: str                          # z.B. "1/2/3"
    dpt_id: str = "DPT1.001"
    state_group_address: str | None = None      # DEST-Bindings Rückmelde-GA


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class KnxAdapter(AdapterBase):
    adapter_type = "KNX"
    config_schema = KnxAdapterConfig
    binding_config_schema = KnxBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None) -> None:
        super().__init__(event_bus, config)
        self._xknx: Any = None
        # GA → list of (binding, dpt_def) — for inbound telegram routing
        self._ga_source_map: dict[str, list[tuple[Any, Any]]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType
        except ImportError:
            logger.error("xknx not installed — KNX adapter disabled. Run: pip install xknx")
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
        self._xknx.telegram_queue.register_telegram_received_cb(self._on_telegram)

        try:
            await self._xknx.start()
            await self._publish_status(True, f"Connected to {cfg.host}:{cfg.port}")
            logger.info("KNX adapter connected: %s:%d (%s)", cfg.host, cfg.port, cfg.connection_type)
        except Exception as exc:
            await self._publish_status(False, str(exc))
            logger.exception("KNX connect failed")

    async def disconnect(self) -> None:
        if self._xknx:
            try:
                await self._xknx.stop()
            except Exception:
                logger.exception("KNX disconnect error")
        await self._publish_status(False, "Disconnected")
        self._xknx = None

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        """Build GA → binding lookup table."""
        self._ga_source_map.clear()
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

            # Also register state_group_address as source if present
            if bc.state_group_address:
                self._ga_source_map.setdefault(bc.state_group_address, []).append(entry)

        logger.debug(
            "KNX: %d source GAs registered from %d bindings",
            len(self._ga_source_map), len(self._bindings),
        )

    # ------------------------------------------------------------------
    # Inbound telegram handler
    # ------------------------------------------------------------------

    async def _on_telegram(self, telegram: Any) -> None:
        from xknx.telegram.apci import GroupValueWrite, GroupValueResponse

        # Only process write and response telegrams
        if not isinstance(telegram.payload, (GroupValueWrite, GroupValueResponse)):
            return

        ga = str(telegram.destination_address)
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
                value = raw
                quality = "uncertain"

            await self._bus.publish(DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=value,
                quality=quality,
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            ))

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        """Send a GroupValueRead telegram and wait for the response."""
        if not self._xknx:
            return None
        try:
            from xknx.telegram import Telegram, TelegramDirection
            from xknx.telegram.address import GroupAddress
            from xknx.telegram.apci import GroupValueRead

            bc = KnxBindingConfig(**binding.config)
            ga = bc.state_group_address or bc.group_address
            telegram = Telegram(
                destination_address=GroupAddress(ga),
                payload=GroupValueRead(),
            )
            await self._xknx.telegrams.put(telegram)
            # Response arrives via _on_telegram callback
        except Exception:
            logger.exception("KNX read failed for binding %s", binding.id)
        return None

    async def write(self, binding: Any, value: Any) -> None:
        """Encode value and send a GroupValueWrite telegram."""
        if not self._xknx:
            return
        try:
            from xknx.telegram import Telegram
            from xknx.telegram.address import GroupAddress
            from xknx.telegram.apci import GroupValueWrite
            from xknx.core.value_reader import DPTBinary, DPTArray

            bc = KnxBindingConfig(**binding.config)
            dpt = DPTRegistry.get(bc.dpt_id)
            raw = dpt.encoder(value)

            if len(raw) == 1 and raw[0] <= 0x3F:
                payload_value = DPTBinary(raw[0])
            else:
                payload_value = DPTArray(list(raw))

            telegram = Telegram(
                destination_address=GroupAddress(bc.group_address),
                payload=GroupValueWrite(payload_value),
            )
            await self._xknx.telegrams.put(telegram)
        except Exception:
            logger.exception("KNX write failed for binding %s", binding.id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _telegram_to_bytes(telegram: Any) -> bytes:
    """Extract raw bytes from a KNX telegram payload."""
    try:
        v = telegram.payload.value
        # DPTBinary → 1 byte
        if hasattr(v, "value"):
            return bytes([v.value & 0x3F])
        # DPTArray → byte list
        if hasattr(v, "value") and isinstance(v.value, (list, tuple)):
            return bytes(v.value)
        # xknx 2.x: payload.value may be raw int or list
        if isinstance(v, int):
            return bytes([v & 0x3F])
        if isinstance(v, (list, tuple)):
            return bytes(v)
        return bytes(v) if v else b"\x00"
    except Exception:
        return b"\x00"
