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
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

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


# Shared I/O semaphores keyed by (host, port). Instances polling the same
# Modbus endpoint (several devices behind one RS485/Modbus-TCP gateway) share a
# lock so their requests never overlap on the single serial bus.
_SHARED_BUS_SEMS: dict[tuple[str, int], asyncio.Semaphore] = {}
# Endpoint-wide inter-read delay (ms): the largest delay any instance sharing a
# host:port configured, so the promised turnaround also holds when a zero-delay
# sibling completes a transaction on the shared bus.
_SHARED_BUS_DELAYS: dict[tuple[str, int], float] = {}


def _shared_bus_sem(host: str, port: int) -> asyncio.Semaphore:
    key = (host, port)
    sem = _SHARED_BUS_SEMS.get(key)
    if sem is None:
        sem = asyncio.Semaphore(1)
        _SHARED_BUS_SEMS[key] = sem
    return sem


class ModbusTcpAdapterConfig(BaseModel):
    host: str = "192.168.1.1"
    port: int = 502
    timeout: float = 3.0
    serialize_reads: bool = Field(
        default=True,
        title="Reads serialisieren",
        description="Sendet Modbus-Requests nacheinander statt gleichzeitig. Empfohlen fuer einfache Geraete (Heizungsregler, Wechselrichter, Zaehler), die nur einen Request gleichzeitig verarbeiten koennen. Deaktivieren bei leistungsstarken PLCs mit Multi-Request-Unterstuetzung.",
    )
    shared_bus: bool = Field(
        default=False,
        title="Bus mit Instanzen gleicher IP:Port teilen",
        description=(
            "Serialisiert I/O ueber ALLE Instanzen, die denselben Host:Port pollen "
            "(z.B. mehrere Geraete hinter einem RS485/Modbus-TCP-Gateway). "
            "Verhindert gleichzeitige Requests konkurrierender Instanzen am selben Bus."
        ),
    )
    inter_read_delay_ms: float = Field(
        default=0.0,
        ge=0.0,
        le=5000.0,
        title="Wartefrist pro Read (ms)",
        description="Pause nach jeder Modbus-Transaktion auf dem (geteilten) Bus, bevor die naechste startet. Gibt langsamen RS485/Modbus-TCP-Gateways Umschaltzeit und entzerrt mehrere Geraete an einem Bus. 0 = deaktiviert.",
    )
    shared_bus_acquire_timeout_s: float = Field(
        default=30.0,
        ge=0.0,
        le=300.0,
        title="Shared-Bus Acquire-Timeout (s)",
        description="Nur bei shared_bus: Obergrenze fuers Warten auf die geteilte Bus-Semaphore. Laeuft die Sperre laenger nicht frei (verklemmte Semaphore ueber Instanzen), wird die Transaktion mit WARNING uebersprungen statt unbegrenzt zu blockieren. 0 = kein Timeout.",
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
        self._client_factory: Any = None
        self._poll_tasks: list[asyncio.Task] = []
        # I/O semaphore — serializes reads AND writes on the shared TCP socket.
        # None when serialize_reads=False (no-op via nullcontext).
        # Reconfigured in connect() based on serialize_reads option.
        self._io_sem: asyncio.Semaphore | None = asyncio.Semaphore(1)
        # Serializes bus transactions purely to make inter_read_delay_ms
        # meaningful when serialize_reads=False and shared_bus=False (_io_sem
        # is None). Without this, concurrent poll tasks each sleep after their
        # own transaction, but the sleeps overlap and never produce a minimum
        # interval between transactions on the bus. Only ever acquired when a
        # positive delay is configured, so the no-delay fast path is unaffected.
        self._space_lock: asyncio.Lock = asyncio.Lock()
        # host:port of the shared bus this instance joins (set in connect()),
        # or None when shared_bus is off.
        self._shared_bus_key: tuple[str, int] | None = None
        # Prevents concurrent _on_bindings_reloaded() calls from interleaving.
        # Without this, two simultaneous REST binding changes can create orphan
        # poll tasks that use the same TCP client alongside the tracked tasks.
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        # Serializes reconnect attempts from concurrent poll tasks (double-checked locking).
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()
        # Monotonic timestamp after which reconnect attempts are permitted again.
        # Prevents N bindings from each firing a separate connect() timeout when
        # the device is offline (e.g. 130 bindings × 3s timeout = 6+ min blocked).
        self._reconnect_ok_after: float = 0.0
        # Lifecycle guard: request serialization can be disabled, but close/connect
        # must still wait for in-flight Modbus calls before touching the socket.
        self._lifecycle_cond: asyncio.Condition = asyncio.Condition()
        self._lifecycle_busy: bool = False
        self._inflight_io: int = 0
        self._stopping: bool = False
        # Parsed adapter config — populated in connect(), used in _poll_loop.
        self._adp_cfg: ModbusTcpAdapterConfig = ModbusTcpAdapterConfig()
        # True after the first _on_bindings_reloaded() call; jitter is only applied
        # on initial start, not on subsequent binding changes (delete/recreate).
        self._initial_load_done: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._stopping = False
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError:
            logger.error("pymodbus not installed — Modbus TCP disabled. Run: pip install pymodbus")
            await self._publish_status(False, "pymodbus not installed", code="libNotInstalled", params={"lib": "pymodbus"})
            return
        self._client_factory = AsyncModbusTcpClient

        cfg = ModbusTcpAdapterConfig(**self._config)
        self._adp_cfg = cfg

        # Configure I/O serialization: Semaphore(1) = one operation at a time (safe
        # for embedded devices); None = no-op via nullcontext (for capable PLCs).
        if cfg.shared_bus:
            self._io_sem = _shared_bus_sem(cfg.host, cfg.port)
            self._shared_bus_key = (cfg.host, cfg.port)
            # Enforce one delay policy per shared endpoint: keep the largest
            # delay configured by any sibling so the interval holds bus-wide.
            _SHARED_BUS_DELAYS[self._shared_bus_key] = max(
                _SHARED_BUS_DELAYS.get(self._shared_bus_key, 0.0),
                cfg.inter_read_delay_ms,
            )
        else:
            self._io_sem = asyncio.Semaphore(1) if cfg.serialize_reads else None
            self._shared_bus_key = None
        logger.debug(
            "Modbus TCP: serialize_reads=%s startup_jitter_s=%.1f",
            cfg.serialize_reads,
            cfg.startup_jitter_s,
        )

        self._client = self._new_client()
        try:
            await self._client.connect()
            if self._client.connected:
                await self._publish_status(
                    True,
                    f"{cfg.host}:{cfg.port}",
                    code="connectedTo",
                    params={"host": cfg.host, "port": cfg.port},
                )
                logger.info("Modbus TCP connected: %s:%d", cfg.host, cfg.port)
            else:
                await self._publish_status(
                    False,
                    f"Could not connect to {cfg.host}:{cfg.port}",
                    code="couldNotConnectTo",
                    params={"host": cfg.host, "port": cfg.port},
                )
        except Exception as exc:
            await self._publish_status(False, str(exc))
            logger.exception("Modbus TCP connect failed")

    async def disconnect(self) -> None:
        async with self._reload_lock:
            self._stopping = True
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
                async with self._client_lifecycle():
                    self._client.close()
            await self._publish_status(False, "Disconnected", code="disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        # Serialize concurrent reload calls so two simultaneous REST binding changes
        # cannot interleave their cancel/create sequences and produce orphan tasks.
        async with self._reload_lock:
            if self._stopping:
                return

            needs_clean_session = self._initial_load_done or bool(self._poll_tasks) or self._inflight_io > 0

            # Cancel existing pollers and wait for them to actually finish.
            # Without awaiting gather(), old tasks may still be executing a Modbus read
            # concurrently with the new tasks, which corrupts the shared TCP connection.
            for t in self._poll_tasks:
                t.cancel()
            if self._poll_tasks:
                await asyncio.gather(*self._poll_tasks, return_exceptions=True)
            self._poll_tasks.clear()

            # Close and reconnect only for real reloads; the initial binding load
            # follows connect() and should not create a second TCP session.
            if needs_clean_session and self._client:
                async with self._client_lifecycle():
                    try:
                        self._client.close()
                    except Exception:
                        logger.exception("Modbus TCP: closing previous client failed")
                    try:
                        self._client = self._new_client()
                        await self._client.connect()
                        if self._client.connected:
                            self._reconnect_ok_after = 0.0
                            await self._publish_status(
                                True,
                                f"{self._adp_cfg.host}:{self._adp_cfg.port}",
                                code="connectedTo",
                                params={"host": self._adp_cfg.host, "port": self._adp_cfg.port},
                            )
                            logger.info("Modbus TCP: reconnected after binding reload")
                        else:
                            await self._publish_status(
                                False,
                                f"Could not reconnect to {self._adp_cfg.host}:{self._adp_cfg.port}",
                                code="couldNotReconnectTo",
                                params={"host": self._adp_cfg.host, "port": self._adp_cfg.port},
                            )
                            logger.warning("Modbus TCP: reconnect after reload left client disconnected")
                    except Exception as exc:
                        await self._publish_status(False, str(exc))
                        logger.exception("Modbus TCP: reconnect after reload failed")

            # Jitter is only useful on the very first load (spreading out the initial
            # burst after an adapter restart). Subsequent reloads triggered by binding
            # changes affect only a few tasks and should not add unnecessary delay.
            apply_jitter = not self._initial_load_done
            self._initial_load_done = True

            # Start a poller task per SOURCE/BOTH binding
            source_bindings = [b for b in self._bindings if b.direction in ("SOURCE", "BOTH")]
            for binding in source_bindings:
                if self._stopping:
                    break
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
        except (ValidationError, TypeError):
            logger.warning("Invalid Modbus TCP binding config %s — skipped", binding.id)
            return

        # Stagger the initial poll with a random delay (startup_jitter_s adapter option).
        # Only applied on the first load after connect(), not on binding changes.
        if apply_jitter:
            _jitter_max = self._adp_cfg.startup_jitter_s
            if _jitter_max > 0:
                await asyncio.sleep(random.uniform(0, _jitter_max))

        while True:
            # Auto-reconnect if the client became disconnected.
            # _reconnect_lock serializes attempts; _reconnect_ok_after provides a
            # shared backoff so N bindings do not each fire a separate connect()
            # timeout when the device is offline (avoids N × timeout seconds blocked).
            if self._client and not self._client.connected:
                reconnect_failed = False
                async with self._reconnect_lock:
                    if self._client and not self._client.connected:
                        if time.monotonic() < self._reconnect_ok_after:
                            # Backoff active — skip this attempt, publish bad quality.
                            await self._publish_disconnected_if_needed("Modbus TCP reconnect backoff active", code="modbusReconnectBackoff")
                            reconnect_failed = True
                        else:
                            try:
                                async with self._client_lifecycle():
                                    await self._client.connect()
                                if self._client.connected:
                                    self._reconnect_ok_after = 0.0  # clear backoff on success
                                    host = self._adp_cfg.host
                                    port = self._adp_cfg.port
                                    await self._publish_status(True, f"{host}:{port}", code="connectedTo", params={"host": host, "port": port})
                                    logger.info(
                                        "Modbus TCP: reconnected in poll loop (binding %s)",
                                        binding.id,
                                    )
                                else:
                                    # connect() returned without error but socket still down.
                                    self._reconnect_ok_after = time.monotonic() + self._reconnect_backoff_delay(
                                        bc.poll_interval,
                                    )
                                    await self._publish_disconnected_if_needed(
                                        f"Could not reconnect to {self._adp_cfg.host}:{self._adp_cfg.port}",
                                        code="couldNotReconnectTo",
                                        params={"host": self._adp_cfg.host, "port": self._adp_cfg.port},
                                    )
                                    logger.warning(
                                        "Modbus TCP: connect() succeeded but client still disconnected (binding %s)",
                                        binding.id,
                                    )
                                    reconnect_failed = True
                            except Exception as exc:
                                self._reconnect_ok_after = time.monotonic() + self._reconnect_backoff_delay(
                                    bc.poll_interval,
                                )
                                await self._publish_disconnected_if_needed(str(exc))
                                logger.exception("Modbus TCP: reconnect failed (binding %s)", binding.id)
                                reconnect_failed = True

                if reconnect_failed:
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
            except Exception:
                logger.exception("Modbus TCP poll error (binding %s)", binding.id)
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

    def _new_client(self) -> Any:
        if self._client_factory is None:
            from pymodbus.client import AsyncModbusTcpClient

            self._client_factory = AsyncModbusTcpClient
        return self._client_factory(
            host=self._adp_cfg.host,
            port=self._adp_cfg.port,
            timeout=self._adp_cfg.timeout,
        )

    @contextlib.asynccontextmanager
    async def _client_lifecycle(self):
        """Exclusive client lifecycle section; waits for in-flight I/O to drain."""
        async with self._lifecycle_cond:
            while self._lifecycle_busy:
                await self._lifecycle_cond.wait()
            self._lifecycle_busy = True

        try:
            async with self._lifecycle_cond:
                while self._inflight_io:
                    await self._lifecycle_cond.wait()
            yield
        finally:
            async with self._lifecycle_cond:
                self._lifecycle_busy = False
                self._lifecycle_cond.notify_all()

    @contextlib.asynccontextmanager
    async def _inflight_modbus_call(self):
        """Register one Modbus call so lifecycle close/connect can wait for it."""
        async with self._lifecycle_cond:
            while self._lifecycle_busy:
                await self._lifecycle_cond.wait()
            self._inflight_io += 1
        try:
            yield
        finally:
            async with self._lifecycle_cond:
                self._inflight_io -= 1
                if self._inflight_io == 0:
                    self._lifecycle_cond.notify_all()

    @contextlib.asynccontextmanager
    async def _modbus_io_context(self):
        if self._io_sem is None:
            # Only serialize via _space_lock when a delay is actually configured —
            # otherwise concurrent poll tasks keep their full existing concurrency.
            spacer = self._space_lock if self._adp_cfg.inter_read_delay_ms > 0 else contextlib.nullcontext()
            async with spacer, self._inflight_modbus_call():
                client = self._client if self._client_ready() else None
                try:
                    yield client
                finally:
                    await self._space_out_bus(client)
            return

        # Shared bus: bound the acquire so a wedged cross-instance semaphore surfaces
        # as a logged, skipped transaction instead of an unbounded silent hang. The
        # per-instance serialize lock (not shared) keeps its plain blocking acquire.
        _sbt = self._adp_cfg.shared_bus_acquire_timeout_s
        if self._shared_bus_key is not None and _sbt > 0:
            try:
                await asyncio.wait_for(self._io_sem.acquire(), timeout=_sbt)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "Modbus shared bus %s: I/O semaphore acquire timed out after %.0fs "
                    "(possible cross-instance wedge) — skipping this transaction",
                    self._shared_bus_key,
                    _sbt,
                )
                yield None
                return
            try:
                async with self._inflight_modbus_call():
                    client = self._client if self._client_ready() else None
                    try:
                        yield client
                    finally:
                        await self._space_out_bus(client)
            finally:
                self._io_sem.release()
            return

        async with self._io_sem, self._inflight_modbus_call():
            client = self._client if self._client_ready() else None
            try:
                yield client
            finally:
                await self._space_out_bus(client)

    async def _space_out_bus(self, client) -> None:
        """Pause after a Modbus transaction so a slow shared RS485 gateway can
        settle before the next one.

        Runs whether the transaction succeeded or raised (timeout/socket error),
        is skipped when no transaction was performed (no ready client), and
        applies regardless of whether an I/O semaphore is in use. Skipped while
        the poller task is being cancelled (disconnect / binding reload) so
        lifecycle operations are not stalled by the turnaround sleep.
        """
        if client is None:
            return
        # A cancellation (disconnect / reload) must not wait out the delay.
        task = asyncio.current_task()
        if task is not None and getattr(task, "cancelling", lambda: 0)():
            return
        _delay = self._effective_delay()
        if _delay > 0:
            await asyncio.sleep(_delay / 1000.0)

    def _effective_delay(self) -> float:
        """Inter-read delay to enforce (ms). For a shared endpoint this is the
        largest delay configured by any sibling instance, so the minimum interval
        holds bus-wide even right after a zero-delay sibling released the shared
        semaphore."""
        if self._shared_bus_key is not None:
            return _SHARED_BUS_DELAYS.get(
                self._shared_bus_key, self._adp_cfg.inter_read_delay_ms
            )
        return self._adp_cfg.inter_read_delay_ms

    def _client_ready(self) -> bool:
        return bool(not self._stopping and self._client and self._client.connected)

    def _reconnect_backoff_delay(self, current_poll_interval: float) -> float:
        intervals = [current_poll_interval]
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                intervals.append(ModbusBindingConfig(**binding.config).poll_interval)
            except (ValidationError, TypeError):
                continue
        return max(0.0, min(intervals))

    async def _publish_disconnected_if_needed(self, detail: str, *, code: str | None = None, params: dict | None = None) -> None:
        if self.connected:
            await self._publish_status(False, detail, code=code, params=params)

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

        # Use _io_sem to serialize I/O when requested; lifecycle tracking remains
        # active even when serialize_reads=False.
        async with self._modbus_io_context() as client:
            if client is None:
                return None

            if bc.register_type == "holding":
                r = await self._modbus_call(
                    client.read_holding_registers,
                    bc.address,
                    count,
                    unit_id=bc.unit_id,
                )
            elif bc.register_type == "input":
                r = await self._modbus_call(client.read_input_registers, bc.address, count, unit_id=bc.unit_id)
            elif bc.register_type == "coil":
                r = await self._modbus_call(client.read_coils, bc.address, count, unit_id=bc.unit_id)
            elif bc.register_type == "discrete_input":
                r = await self._modbus_call(client.read_discrete_inputs, bc.address, count, unit_id=bc.unit_id)
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
        async with self._modbus_io_context() as client:
            if client is None:
                return

            if bc.register_type == "coil":
                await self._modbus_call(client.write_coil, bc.address, bool(value), unit_id=bc.unit_id)
            elif bc.register_type == "holding":
                registers = encode_value(value, bc.data_format, bc.byte_order, bc.word_order, bc.scale_factor)
                if len(registers) == 1:
                    await self._modbus_call(
                        client.write_register,
                        bc.address,
                        registers[0],
                        unit_id=bc.unit_id,
                    )
                else:
                    await self._modbus_call(
                        client.write_registers,
                        bc.address,
                        registers,
                        unit_id=bc.unit_id,
                    )
