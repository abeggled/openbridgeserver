"""
1-Wire Adapter — Phase 3

Liest Temperatursensoren vom Linux 1-Wire Bus (/sys/bus/w1/).
Funktioniert nur auf Linux (Raspberry Pi, etc.). Auf anderen Systemen
wird der Adapter deaktiviert, ohne den Start zu blockieren.

Adapter-Konfiguration (adapter_configs.config):
  poll_interval: float  (Sekunden zwischen Messungen, default: 30.0)
  w1_path:       str    (default: "/sys/bus/w1/devices")

Binding-Konfiguration (AdapterBinding.config):
  sensor_id:  str   — z.B. "28-000000000001"
  sensor_type: str  — "DS18B20" | "DS18S20" | "DS1822" (default: "DS18B20")
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from opentws.adapters.base import AdapterBase
from opentws.adapters.registry import register
from opentws.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)

_W1_BASE = Path("/sys/bus/w1/devices")


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------

class OneWireAdapterConfig(BaseModel):
    poll_interval: float = 30.0
    w1_path: str = "/sys/bus/w1/devices"


class OneWireBindingConfig(BaseModel):
    sensor_id: str              # z.B. "28-000000000001"
    sensor_type: str = "DS18B20"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class OneWireAdapter(AdapterBase):
    adapter_type = "ONEWIRE"
    hidden = True
    config_schema = OneWireAdapterConfig
    binding_config_schema = OneWireBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._poll_tasks: list[asyncio.Task] = []
        self._cfg = OneWireAdapterConfig(**(config or {}))
        self._available: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._cfg = OneWireAdapterConfig(**self._config)
        w1_path = Path(self._cfg.w1_path)

        if not os.path.exists(w1_path):
            logger.warning(
                "1-Wire path %s not found — adapter disabled (Linux only, needs w1-therm kernel module)",
                w1_path,
            )
            await self._publish_status(False, f"{w1_path} not found")
            return

        self._available = True
        await self._publish_status(True, f"1-Wire bus at {w1_path}")
        logger.info("1-Wire adapter connected: %s", w1_path)

    async def disconnect(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()
        await self._publish_status(False, "Disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()

        if not self._available:
            return

        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            t = asyncio.create_task(
                self._poll_loop(binding),
                name=f"1wire-poll-{binding.id}",
            )
            self._poll_tasks.append(t)

        logger.debug("1-Wire: %d poll tasks started", len(self._poll_tasks))

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self, binding: Any) -> None:
        try:
            bc = OneWireBindingConfig(**binding.config)
        except Exception:
            logger.warning("Invalid 1-Wire binding config %s — skipped", binding.id)
            return

        while True:
            try:
                value = await asyncio.get_event_loop().run_in_executor(
                    None, _read_sensor_file, Path(self._cfg.w1_path) / bc.sensor_id
                )
                quality = "good" if value is not None else "bad"
                await self._bus.publish(DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=value,
                    quality=quality,
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                ))
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("1-Wire poll error (sensor %s): %s", bc.sensor_id, exc)
                await self._bus.publish(DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=None,
                    quality="bad",
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                ))
            await asyncio.sleep(self._cfg.poll_interval)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        if not self._available:
            return None
        try:
            bc = OneWireBindingConfig(**binding.config)
            return await asyncio.get_event_loop().run_in_executor(
                None, _read_sensor_file, Path(self._cfg.w1_path) / bc.sensor_id
            )
        except Exception:
            logger.exception("1-Wire read failed for binding %s", binding.id)
            return None

    async def write(self, binding: Any, value: Any) -> None:
        # 1-Wire Sensoren sind read-only
        logger.debug("1-Wire write ignored — sensors are read-only (binding %s)", binding.id)


# ---------------------------------------------------------------------------
# Sensor file reader (synchronous, run in executor)
# ---------------------------------------------------------------------------

def _read_sensor_file(sensor_path: Path) -> float | None:
    """
    Liest den Temperatursensor direkt aus dem sysfs (w1_slave Datei).
    Format:
      50 05 4b 46 7f ff 0c 10 1c : crc=1c YES
      50 05 4b 46 7f ff 0c 10 1c t=21312
    """
    w1_slave = sensor_path / "w1_slave"
    if not w1_slave.exists():
        return None

    try:
        text = w1_slave.read_text(encoding="ascii", errors="ignore")
        lines = text.strip().splitlines()
        if len(lines) < 2:
            return None
        if "YES" not in lines[0]:
            return None  # CRC error
        # Extract temperature
        t_part = [p for p in lines[1].split() if p.startswith("t=")]
        if not t_part:
            return None
        raw_temp = int(t_part[0][2:])
        return round(raw_temp / 1000.0, 3)
    except Exception:
        return None


def scan_sensors(w1_path: str = "/sys/bus/w1/devices") -> list[str]:
    """Return list of available 1-Wire sensor IDs (e.g. ['28-000000000001'])."""
    base = Path(w1_path)
    if not base.exists():
        return []
    return [
        p.name for p in base.iterdir()
        if p.is_dir() and p.name != "w1_bus_master1"
    ]
