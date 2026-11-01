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
import socket
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

# Bounded poll-task drain (see ModbusTcpAdapter._drain_poll_tasks): how long to wait
# for cancelled pollers before force-closing the client, and how long after that.
_DRAIN_TIMEOUT_S = 5.0
_FORCE_CLOSE_GRACE_S = 3.0

# Kernel dead-peer detection on the gateway socket (see _apply_socket_keepalive).
# Options missing on the platform (e.g. TCP_USER_TIMEOUT outside Linux) are skipped.
_TCP_KEEPALIVE_OPTS = (
    ("TCP_KEEPIDLE", 10),
    ("TCP_KEEPINTVL", 5),
    ("TCP_KEEPCNT", 3),
    ("TCP_USER_TIMEOUT", 15000),
)


# ---------------------------------------------------------------------------
# Adapter Config
# ---------------------------------------------------------------------------


# Shared I/O semaphores keyed by (host, port). Instances polling the same
# Modbus endpoint (several devices behind one RS485/Modbus-TCP gateway) share a
# lock so their requests never overlap on the single serial bus.
_SHARED_BUS_SEMS: dict[tuple[str, int], asyncio.Semaphore] = {}


def _cancel_requested() -> bool:
    """True when the running task has a pending cancel() that something swallowed."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


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
        # Socket that _apply_socket_keepalive() last configured; a new one (own
        # reconnect or pymodbus' automatic one) gets the options again.
        self._keepalive_sock: Any = None

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
        else:
            self._io_sem = asyncio.Semaphore(1) if cfg.serialize_reads else None
        logger.debug(
            "Modbus TCP: serialize_reads=%s startup_jitter_s=%.1f",
            cfg.serialize_reads,
            cfg.startup_jitter_s,
        )

        self._client = self._new_client()
        try:
            await self._client.connect()
            self._apply_socket_keepalive()
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

    async def _drain_poll_tasks(self) -> None:
        """Cancel poll tasks and wait for them — but bounded.

        A task may be stuck in a read that ignores cancellation (only closing the
        socket frees it). A plain ``await gather()`` then deadlocks disconnect()/reload,
        which holds ``_reload_lock`` forever and pins the adapter at enabled=False. So:
        bound the drain with ``asyncio.wait`` (NOT ``wait_for`` — that would try to
        cancel the un-cancellable gather and hang itself), and if the tasks do not
        finish, force-close the client to break the stuck read. Force-close happens
        only in that stuck case, so normal teardown (tasks cancel promptly) is unchanged.

        ``asyncio.wait`` on the tasks themselves never raises their outcome, so a
        cancelled poll task cannot leak its ``CancelledError`` into the caller. If the
        caller itself is cancelled, the poll tasks are cancelled again before the
        cancellation propagates, so no poller outlives its lifecycle call.
        """
        tasks = list(self._poll_tasks)
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        try:
            _, pending = await asyncio.wait(tasks, timeout=_DRAIN_TIMEOUT_S)
            if pending:
                logger.warning(
                    "Modbus TCP: %d poll task(s) did not cancel within %.0fs — force-closing client to break a stuck read (%s:%s)",
                    len(pending),
                    _DRAIN_TIMEOUT_S,
                    self._adp_cfg.host,
                    self._adp_cfg.port,
                )
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    logger.debug("Modbus TCP: force-close during drain failed", exc_info=True)
                _, pending = await asyncio.wait(pending, timeout=_FORCE_CLOSE_GRACE_S)
                if pending:
                    logger.error(
                        "Modbus TCP: %d poll task(s) still running after force-close (%s:%s)",
                        len(pending),
                        self._adp_cfg.host,
                        self._adp_cfg.port,
                    )
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
        finally:
            for t in tasks:
                if t.done() and not t.cancelled():
                    t.exception()  # mark retrieved, as gather(return_exceptions=True) did
            self._poll_tasks.clear()

    async def disconnect(self) -> None:
        async with self._reload_lock:
            self._stopping = True
            await self._drain_poll_tasks()
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

            # Cancel existing pollers and wait for them to actually finish (bounded —
            # force-closes a wedged client so a stuck read cannot deadlock the reload).
            await self._drain_poll_tasks()

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
                        self._apply_socket_keepalive()
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
                                    self._apply_socket_keepalive()
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
                # Defence in depth: with pymodbus < 3.16 a CancelledError raised
                # while awaiting a response surfaced here as
                # ModbusIOException("Request cancelled outside library"). main now
                # pins pymodbus >= 3.16.1, which propagates CancelledError cleanly,
                # so this no longer fires for that case. The task may still be
                # marked as cancelling, so honour the cancel instead of polling on.
                if _cancel_requested():
                    return
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

    def _apply_socket_keepalive(self) -> None:
        """Force fast dead-peer detection on the freshly connected TCP socket.

        Modbus gateways behind an RS485 bridge can go half-open: a request stays
        stuck unacknowledged in the socket Send-Q while TCP still reports
        ESTABLISHED, so the read await hangs for minutes (default TCP retransmit)
        and pymodbus' own timeout never fires (it waits for a response that never
        comes). SO_KEEPALIVE + TCP_USER_TIMEOUT make the kernel drop such a socket
        within ~15 s, turning the silent hang into a normal socket error that the
        existing reconnect handling recovers from. Best-effort: never raises.

        Idempotent per socket: pymodbus reconnects on its own after the peer closes
        the connection, so this also runs before every Modbus call and configures a
        socket the first time it shows up there.
        """
        # pymodbus 3.x keeps the asyncio transport on the client's TransactionManager.
        transport = getattr(getattr(self._client, "ctx", None), "transport", None)
        try:
            sock = transport.get_extra_info("socket") if transport is not None else None
        except Exception:
            logger.debug("Modbus TCP: socket lookup for keepalive failed", exc_info=True)
            sock = None
        if sock is None:
            logger.debug("Modbus TCP: no socket to set keepalive on (%s:%s)", self._adp_cfg.host, self._adp_cfg.port)
            return
        if sock is self._keepalive_sock:
            return
        self._keepalive_sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            applied = ["SO_KEEPALIVE"]
            for name, val in _TCP_KEEPALIVE_OPTS:
                opt = getattr(socket, name, None)
                if opt is not None:
                    sock.setsockopt(socket.IPPROTO_TCP, opt, val)
                    applied.append(f"{name}={val}")
            logger.info("Modbus TCP keepalive set on %s:%s (%s)", self._adp_cfg.host, self._adp_cfg.port, ",".join(applied))
        except Exception:
            logger.debug("Modbus TCP: keepalive setsockopt failed (%s:%s)", self._adp_cfg.host, self._adp_cfg.port, exc_info=True)

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
            async with self._inflight_modbus_call():
                yield self._client if self._client_ready() else None
            return

        async with self._io_sem, self._inflight_modbus_call():
            yield self._client if self._client_ready() else None

    def _client_ready(self) -> bool:
        if self._stopping or not self._client or not self._client.connected:
            return False
        # Covers sockets pymodbus opened by its own auto-reconnect, which bypass
        # every connect() call site; a no-op once the current socket is configured.
        self._apply_socket_keepalive()
        return True

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
