"""
Modbus RTU Adapter — Phase 3

Verbindet sich via serieller Schnittstelle (RS-485/RS-232).
Teilt die Polling-Logik mit dem TCP-Adapter (modbus_base.py).

Adapter-Konfiguration (adapter_configs.config):
  port:       str   (z.B. "/dev/ttyUSB0" oder "COM3")
  baudrate:   int   (default: 9600)
  parity:     str   ("N" | "E" | "O", default: "N")
  stopbits:   int   (1 | 2, default: 1)
  bytesize:   int   (7 | 8, default: 8)
  timeout:    float (default: 3.0)

Binding-Konfiguration: identisch mit Modbus TCP (ModbusBindingConfig).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.adapters.modbus_base import (
    ModbusBindingConfig, decode_registers, encode_value, register_count
)
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter Config
# ---------------------------------------------------------------------------

class ModbusRtuAdapterConfig(BaseModel):
    port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    bytesize: Literal[7, 8] = 8
    timeout: float = 3.0


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class ModbusRtuAdapter(AdapterBase):
    adapter_type = "MODBUS_RTU"
    hidden = True
    config_schema = ModbusRtuAdapterConfig
    binding_config_schema = ModbusBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._client: Any = None
        self._poll_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            from pymodbus.client import AsyncModbusSerialClient
        except ImportError:
            logger.error("pymodbus not installed — Modbus RTU disabled. Run: pip install pymodbus")
            await self._publish_status(False, "pymodbus not installed")
            return

        cfg = ModbusRtuAdapterConfig(**self._config)
        self._client = AsyncModbusSerialClient(
            port=cfg.port,
            baudrate=cfg.baudrate,
            parity=cfg.parity,
            stopbits=cfg.stopbits,
            bytesize=cfg.bytesize,
            timeout=cfg.timeout,
        )
        try:
            await self._client.connect()
            if self._client.connected:
                await self._publish_status(True, f"{cfg.port}@{cfg.baudrate}")
                logger.info("Modbus RTU connected: %s @ %d baud", cfg.port, cfg.baudrate)
            else:
                await self._publish_status(False, f"Could not open {cfg.port}")
        except Exception as exc:
            await self._publish_status(False, str(exc))
            logger.exception("Modbus RTU connect failed")

    async def disconnect(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()
        if self._client:
            self._client.close()
        await self._publish_status(False, "Disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()

        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            t = asyncio.create_task(
                self._poll_loop(binding),
                name=f"modbus-rtu-poll-{binding.id}",
            )
            self._poll_tasks.append(t)

        logger.debug("Modbus RTU: %d poll tasks started", len(self._poll_tasks))

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self, binding: Any) -> None:
        try:
            bc = ModbusBindingConfig(**binding.config)
        except Exception:
            logger.warning("Invalid Modbus RTU binding config %s — skipped", binding.id)
            return

        while True:
            try:
                value = await self._read_register(bc)
                quality = "good" if value is not None else "bad"
                if quality == "good":
                    if binding.value_formula:
                        from obs.core.formula import apply_formula
                        value = apply_formula(binding.value_formula, value)
                    if binding.value_map:
                        from obs.core.transformation import apply_value_map
                        value = apply_value_map(value, binding.value_map)
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
                logger.warning("Modbus RTU poll error (binding %s): %s", binding.id, exc)
                await self._bus.publish(DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=None,
                    quality="bad",
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                ))
            await asyncio.sleep(bc.poll_interval)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        try:
            bc = ModbusBindingConfig(**binding.config)
            return await self._read_register(bc)
        except Exception:
            logger.exception("Modbus RTU read failed for binding %s", binding.id)
            return None

    async def write(self, binding: Any, value: Any) -> None:
        if not self._client or not self._client.connected:
            logger.warning("Modbus RTU write skipped — not connected")
            return
        try:
            bc = ModbusBindingConfig(**binding.config)
            await self._write_register(bc, value)
        except Exception:
            logger.exception("Modbus RTU write failed for binding %s", binding.id)

    # ------------------------------------------------------------------
    # Low-level Modbus operations (identical to TCP, different client)
    # ------------------------------------------------------------------

    async def _modbus_call(self, fn, *pos_args, unit_id: int, **extra_kwargs) -> Any:
        """Version-safe pymodbus call across 2.x / 3.x / 3.12+."""
        slave_variants = [{"device_id": unit_id}, {"slave": unit_id}, {"unit": unit_id}, {}]

        for sk in slave_variants:
            try:
                return await fn(*pos_args, **sk, **extra_kwargs)
            except TypeError:
                continue

        if len(pos_args) >= 2:
            param_names = ["address", "count"]
            kw_fallback = dict(zip(param_names, pos_args))
            for sk in slave_variants:
                try:
                    return await fn(**kw_fallback, **sk, **extra_kwargs)
                except TypeError:
                    continue

        raise RuntimeError(
            f"pymodbus: cannot call {fn.__name__} with any known API variant"
        )

    async def _read_register(self, bc: ModbusBindingConfig) -> Any:
        if not self._client or not self._client.connected:
            return None

        count = register_count(bc.data_format)

        if bc.register_type == "holding":
            r = await self._modbus_call(self._client.read_holding_registers, bc.address, count, unit_id=bc.unit_id)
        elif bc.register_type == "input":
            r = await self._modbus_call(self._client.read_input_registers, bc.address, count, unit_id=bc.unit_id)
        elif bc.register_type == "coil":
            r = await self._modbus_call(self._client.read_coils, bc.address, count, unit_id=bc.unit_id)
        elif bc.register_type == "discrete_input":
            r = await self._modbus_call(self._client.read_discrete_inputs, bc.address, count, unit_id=bc.unit_id)
        else:
            return None

        if r.isError():
            return None

        if bc.register_type in ("coil", "discrete_input"):
            return bool(r.bits[0])

        return decode_registers(
            r.registers, bc.data_format, bc.byte_order, bc.word_order, bc.scale_factor
        )

    async def _write_register(self, bc: ModbusBindingConfig, value: Any) -> None:
        if bc.register_type == "coil":
            await self._modbus_call(self._client.write_coil, bc.address, bool(value), unit_id=bc.unit_id)
        elif bc.register_type == "holding":
            registers = encode_value(value, bc.data_format, bc.byte_order, bc.word_order, bc.scale_factor)
            if len(registers) == 1:
                await self._modbus_call(self._client.write_register, bc.address, registers[0], unit_id=bc.unit_id)
            else:
                await self._modbus_call(self._client.write_registers, bc.address, registers, unit_id=bc.unit_id)
