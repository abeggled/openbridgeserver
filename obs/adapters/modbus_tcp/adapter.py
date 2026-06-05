"""Modbus TCP Adapter — Phase 3

Verbindet sich mit einem Modbus TCP Server (z.B. SPS, Wechselrichter).
Pollt SOURCE-Bindings zyklisch, schreibt DEST-Bindings auf Anfrage.

Adapter-Konfiguration (adapter_configs.config):
  host:             str    (default: "192.168.1.1")
  port:             int    (default: 502)
  timeout:          float  (default: 3.0)
  serialize_reads:  bool   (default: True)  — one in-flight I/O at a time
  startup_jitter_s: float  (default: 30.0) — max random delay before first poll

Binding-Konfiguration (AdapterBinding.config):
  unit_id:        int     (Modbus Slave ID, default: 1)
  register_type:  str     (holding | input | coil | discrete_input)
  address:        int     (Registeradresse, 0-basiert)
  count:          int     (Anzahl Register)
  data_format:    str     (uint16 | int16 | uint32 | int32 | float32)
  scale_factor:   float   (Rohwert × scale_factor = Ingenieurwert)
  poll_interval:  float   (Sekunden, nur für SOURCE/BOTH)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import Any

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.modbus_base import (
    ModbusBindingConfig,
    decode_registers,
    encode_value,
    register_count,
)
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter Config
# ---------------------------------------------------------------------------


class ModbusTcpAdapterConfig(BaseModel):
    host: str = "192.168.1.1"
    port: int = 502
    timeout: float = 3.0
    serialize_reads: bool = Field(
        default=True,
        title="Reads serialisieren",
        description="Sendet Modbus-Requests nacheinander statt gleichzeitig. Empfohlen fuer einfache Geraete (Heizungsregler, Wechselrichter, Zaehler), die nur einen Request gleichzeitig verarbeiten koennen. Deaktivieren bei leistungsstarken PLCs mit Multi-Request-Unterstuetzung.",
    )
    startup_jitter_s: float = Field(
        default=30.0,
        ge=0.0,
        le=300.0,
        title="Startup-Jitter (s)",
        description="Maximale zufaellige Verzoegerung (Sekunden) vor dem ersten Poll jedes Bindings. Verhindert einen Request-Burst wenn alle Tasks gleichzeitig starten. 0 = deaktiviert.",
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class ModbusTcpAdapter(AdapterBase):
    adapter_type = "MODBUS_TCP"
    config_schema = ModbusTcpAdapterConfig
    binding_config_schema = ModbusBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._client: Any = None
        self._poll_tasks: list[asyncio.Task] = []
        # I/O semaphore — serializes both reads *and* writes so that a concurrent
        # DEST write cannot interleave with a SOURCE read and corrupt the TCP stream.
        # None when serialize_reads=False (unlimited / no-op via nullcontext).
        # Reconfigured in connect() based on serialize_reads option.
        self._io_sem: asyncio.Semaphore | None = asyncio.Semaphore(1)
        # Serializes reconnect attempts from concurrent poll tasks (double-checked locking).
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()
        # Parsed adapter config — populated in connect(), used in _poll_loop.
        self._adp_cfg: ModbusTcpAdapterConfig = ModbusTcpAdapterConfig()
        # True after the first _on_bindings_reloaded() call; jitter is only applied
        # on initial start, not on subsequent binding changes (delete/recreate).
        self._initial_load_done: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError:
            logger.error("pymodbus not installed — Modbus TCP disabled. Run: pip install pymodbus")
            await self._publish_status(False, "pymodbus not installed")
            return

        cfg = ModbusTcpAdapterConfig(**self._config)
        self._adp_cfg = cfg

        # Configure I/O serialization: Semaphore(1) = one operation at a time (safe
        # for embedded devices); None = no-op via nullcontext (for capable PLCs).
        self._io_sem = asyncio.Semaphore(1) if cfg.serialize_reads else None
        logger.debug(
            "Modbus TCP: serialize_reads=%s startup_jitter_s=%.1f",
            cfg.serialize_reads,
            cfg.startup_jitter_s,
        )

        self._client = AsyncModbusTcpClient(
            host=cfg.host,
            port=cfg.port,
            timeout=cfg.timeout,
        )
        try:
            await self._client.connect()
            if self._client.connected:
                await self._publish_status(True, f"{cfg.host}:{cfg.port}")
                logger.info("Modbus TCP connected: %s:%d", cfg.host, cfg.port)
            else:
                await self._publish_status(False, f"Could not connect to {cfg.host}:{cfg.port}")
        except Exception as exc:
            await self._publish_status(False, str(exc))
            logger.exception("Modbus TCP connect failed")

    async def disconnect(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        # Wait for tasks to finish — same as _on_bindings_reloaded to avoid the
        # race condition where a cancelled task is still mid-read when close() runs.
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks, return_exceptions=True)
        self._poll_tasks.clear()
        # Reset initial-load flag so jitter applies again on next connect().
        self._initial_load_done = False
        if self._client:
            self._client.close()
        await self._publish_status(False, "Disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        # Cancel existing pollers and wait for them to actually finish.
        # Without awaiting gather(), old tasks may still be executing a Modbus read
        # concurrently with the new tasks, which corrupts the shared TCP connection.
        for t in self._poll_tasks:
            t.cancel()
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks, return_exceptions=True)
        self._poll_tasks.clear()

        # Always close and reconnect after cancelling pollers to guarantee a clean
        # TCP session. Cancelled tasks may have left a pending Modbus request
        # mid-flight even when the transport still reports connected=True, so gating
        # the reconnect solely on `not connected` is insufficient (P2 review comment).
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            try:
                await self._client.connect()
                logger.info("Modbus TCP: reconnected after binding reload")
            except Exception as exc:
                logger.warning("Modbus TCP: reconnect after reload failed: %s", exc)

        # Jitter is only useful on the very first load (spreading out the initial
        # burst after an adapter restart). Subsequent reloads triggered by binding
        # changes affect only a few tasks and should not add unnecessary delay.
        apply_jitter = not self._initial_load_done
        self._initial_load_done = True

        # Start a poller task per SOURCE/BOTH binding
        source_bindings = [b for b in self._bindings if b.direction in ("SOURCE", "BOTH")]
        for binding in source_bindings:
            t = asyncio.create_task(
                self._poll_loop(binding, apply_jitter=apply_jitter),
                name=f"modbus-tcp-poll-{binding.id}",
            )
            self._poll_tasks.append(t)

        logger.debug("Modbus TCP: %d poll tasks started", len(self._poll_tasks))

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self, binding: Any, *, apply_jitter: bool = True) -> None:
        try:
            bc = ModbusBindingConfig(**binding.config)
        except Exception:
            logger.warning("Invalid Modbus TCP binding config %s — skipped", binding.id)
            return

        # Stagger the initial poll with a random delay (startup_jitter_s adapter option).
        # Only applied on the first load after connect(), not on binding changes.
        if apply_jitter:
            _jitter_max = self._adp_cfg.startup_jitter_s
            if _jitter_max > 0:
                await asyncio.sleep(random.uniform(0, min(bc.poll_interval * 0.5, _jitter_max)))

        while True:
            # Auto-reconnect if the client became disconnected (e.g. after a reload
            # that cancelled tasks mid-read, or a transient network failure).
            # _reconnect_lock serializes attempts: when N tasks all detect
            # connected=False simultaneously, only the first actually reconnects.
            if self._client and not self._client.connected:
                async with self._reconnect_lock:
                    if not self._client.connected:
                        try:
                            await self._client.connect()
                            # Verify connected before publishing healthy status (P2).
                            if self._client.connected:
                                host = self._adp_cfg.host
                                port = self._adp_cfg.port
                                await self._publish_status(True, f"{host}:{port}")
                                logger.info("Modbus TCP: reconnected in poll loop (binding %s)", binding.id)
                            else:
                                # connect() returned without error but socket is still down.
                                logger.warning(
                                    "Modbus TCP: connect() succeeded but client still disconnected (binding %s)",
                                    binding.id,
                                )
                                await self._bus.publish(
                                    DataValueEvent(
                                        datapoint_id=binding.datapoint_id,
                                        value=None,
                                        quality="bad",
                                        source_adapter=self.adapter_type,
                                        binding_id=binding.id,
                                    ),
                                )
                                await asyncio.sleep(bc.poll_interval)
                                continue
                        except Exception as exc:
                            logger.warning("Modbus TCP: reconnect failed (binding %s): %s", binding.id, exc)
                            await self._bus.publish(
                                DataValueEvent(
                                    datapoint_id=binding.datapoint_id,
                                    value=None,
                                    quality="bad",
                                    source_adapter=self.adapter_type,
                                    binding_id=binding.id,
                                ),
                            )
                            await asyncio.sleep(bc.poll_interval)
                            continue

            # Read → transform → publish
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
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=value,
                        quality=quality,
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Modbus TCP poll error (binding %s): %s", binding.id, exc)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=None,
                        quality="bad",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
            await asyncio.sleep(bc.poll_interval)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        try:
            bc = ModbusBindingConfig(**binding.config)
            return await self._read_register(bc)
        except Exception:
            logger.exception("Modbus TCP read failed for binding %s", binding.id)
            return None

    async def write(self, binding: Any, value: Any) -> None:
        if not self._client or not self._client.connected:
            logger.warning("Modbus TCP write skipped — not connected")
            return
        try:
            bc = ModbusBindingConfig(**binding.config)
            await self._write_register(bc, value)
        except Exception:
            logger.exception("Modbus TCP write failed for binding %s", binding.id)

    # ------------------------------------------------------------------
    # Low-level Modbus operations
    # ------------------------------------------------------------------

    async def _modbus_call(self, fn, *pos_args, unit_id: int, **extra_kwargs) -> Any:
        """Version-safe pymodbus call across 2.x / 3.x / 3.12+.

        Tries every combination of slave kwarg name and whether positional args
        need to become keyword args (pymodbus 3.12+ made count keyword-only).
        """
        slave_variants = [
            {"device_id": unit_id},
            {"slave": unit_id},
            {"unit": unit_id},
            {},
        ]

        # First: try all args positional (works for 2.x and 3.0-3.11)
        for sk in slave_variants:
            try:
                return await fn(*pos_args, **sk, **extra_kwargs)
            except TypeError:
                continue

        # Second: try last positional arg as keyword (pymodbus 3.12+ keyword-only params)
        if len(pos_args) >= 2:
            param_names = ["address", "count"]
            kw_fallback = dict(zip(param_names, pos_args))
            for sk in slave_variants:
                try:
                    return await fn(**kw_fallback, **sk, **extra_kwargs)
                except TypeError:
                    continue

        raise RuntimeError(f"pymodbus: cannot call {fn.__name__} with any known API variant")

    async def _read_register(self, bc: ModbusBindingConfig) -> Any:
        if not self._client or not self._client.connected:
            return None

        count = register_count(bc.data_format)

        # Use _io_sem to serialize I/O; None means no-op (serialize_reads=False).
        async with self._io_sem or contextlib.nullcontext():
            if bc.register_type == "holding":
                r = await self._modbus_call(
                    self._client.read_holding_registers,
                    bc.address,
                    count,
                    unit_id=bc.unit_id,
                )
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

            return decode_registers(r.registers, bc.data_format, bc.byte_order, bc.word_order, bc.scale_factor)

    async def _write_register(self, bc: ModbusBindingConfig, value: Any) -> None:
        # Use the same _io_sem as reads — a concurrent write and read on the same
        # TCP socket reproduce exactly the stream-corruption race the PR fixes.
        async with self._io_sem or contextlib.nullcontext():
            if bc.register_type == "coil":
                await self._modbus_call(self._client.write_coil, bc.address, bool(value), unit_id=bc.unit_id)
            elif bc.register_type == "holding":
                registers = encode_value(value, bc.data_format, bc.byte_order, bc.word_order, bc.scale_factor)
                if len(registers) == 1:
                    await self._modbus_call(
                        self._client.write_register,
                        bc.address,
                        registers[0],
                        unit_id=bc.unit_id,
                    )
                else:
                    await self._modbus_call(
                        self._client.write_registers,
                        bc.address,
                        registers,
                        unit_id=bc.unit_id,
                    )
