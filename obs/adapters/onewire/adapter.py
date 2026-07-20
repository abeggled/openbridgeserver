"""1-Wire Adapter — owserver/OWFS client (issue #6)

Connects to an external `owserver` process (OWFS project) via the `pyownet` TCP
protocol client — the same "OBS is a client of an external service" relationship
the MQTT adapter has with Mosquitto. `owserver` abstracts USB busmasters (simple
DS9490-style sticks, the ElabNET PBM's multiple channels) and the native kernel
1-Wire bus behind one uniform device tree, so this adapter never needs to know
which hardware is actually behind it.

Adapter-Konfiguration (adapter_instances.config):
  host:     str            — owserver host (default: "localhost")
  port:     int             — owserver port (default: 4304)
  poll_interval: float       — Sekunden zwischen Messungen (default: 30.0)
  request_timeout: float    — Sekunden pro owserver-Aufruf, bevor er als Fehler
                              gewertet wird (default: 10.0)
  aliases:  dict[str, str]  — persistenter ROM-ID → Klartext-Label Map, gepflegt
                              über die Binding-Scan-UI (issue #6, Punkt 2)

Binding-Konfiguration (AdapterBinding.config):
  sensor_id: str  — ROM-ID, z.B. "28.4B057F0A1C10"
  property:  str  — OWFS-Property/"Datei", z.B. "temperature", "humidity", "PIO.0"
                     (default: "temperature")

pyownet's OwnetProxy performs blocking socket I/O and is not concurrency-safe, so
all proxy calls — including disconnect()'s close_connection() — are serialized
through a single asyncio.Lock and run on a dedicated single-worker thread pool.
The single worker (not the default executor) matters: cancelling a task blocked
on run_in_executor() marks its asyncio-side future cancelled immediately without
waiting for the underlying thread, so the lock alone cannot stop a stale poll's
blocking call from still running when disconnect() closes the proxy. A lone
worker thread processes submissions strictly in order, so close always waits
its turn behind whatever was already dispatched.

OWFS yes/no properties (DS2408 PIO.x, sensed.x, latch.x, ...) are parsed as bool,
not float, so BOOLEAN datapoints don't reject them downstream as a type mismatch.
browse_sensors() also resolves OWFS aliases (root entries that aren't bare ROM-IDs)
via their "address" property, so an aliased device still shows up in a scan.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
from typing import Any

from pydantic import BaseModel

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)

_ROM_ID_RE = re.compile(r"^/([0-9A-Fa-f]{2}\.[0-9A-Fa-f]{12})/?$")
_ROM_ID_SHAPE_RE = re.compile(r"[0-9A-Fa-f]{2}\.[0-9A-Fa-f]{12}")
# owserver's "address" property on an aliased device dir is the raw 64-bit ROM
# code — 16 hex chars, family(2) + serial(12) + crc8(2), with no dot separator.
_RAW_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
# Legacy sysfs adapter (pre-owserver rewrite) stored ROM IDs as "28-000000000001";
# owserver/OWFS paths use the dotted "28.000000000001" form.
_LEGACY_SYSFS_ID_RE = re.compile(r"^([0-9A-Fa-f]{2})-([0-9A-Fa-f]{12})$")
# OWFS yes/no properties (DS2408 PIO.x/PIO.A, sensed.x, latch.x, ...) — read back
# as "0"/"1", parsed as bool rather than float so BOOLEAN datapoints don't reject
# them as a type mismatch downstream (WriteRouter only allows float values
# through for FLOAT datapoints, not BOOLEAN). Channels can be numbered (PIO.0)
# or lettered (PIO.A), hence "\w+" rather than "\d+".
_YESNO_PROPERTY_RE = re.compile(r"^(?:PIO|sensed|latch|power|present)(?:\.\w+)?$", re.IGNORECASE)
# Structural/metadata OWFS entries — not sensor readings, hidden from the browse picker.
_STRUCTURAL_PROPERTIES = frozenset(
    {"address", "alias", "crc8", "id", "locator", "r_address", "r_id", "r_locator", "type", "version", "family"},
)


def _normalize_sensor_id(sensor_id: str) -> str:
    """Convert a legacy hyphenated sysfs ROM ID to the dotted OWFS form, if needed."""
    m = _LEGACY_SYSFS_ID_RE.match(sensor_id)
    return f"{m.group(1)}.{m.group(2)}" if m else sensor_id


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class OneWireAdapterConfig(BaseModel):
    host: str = "localhost"
    port: int = 4304
    poll_interval: float = 30.0
    # pyownet's read/write/dir default to timeout=0, which owserver treats as
    # "wait indefinitely" — a wedged busmaster/sensor transaction would then
    # block that call (and, via _owlock, every other poll/browse/write) forever.
    request_timeout: float = 10.0
    aliases: dict[str, str] = {}


class OneWireBindingConfig(BaseModel):
    sensor_id: str  # ROM-ID, z.B. "28.4B057F0A1C10"
    property: str = "temperature"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class OneWireAdapter(AdapterBase):
    adapter_type = "ONEWIRE"
    config_schema = OneWireAdapterConfig
    binding_config_schema = OneWireBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._poll_tasks: list[asyncio.Task] = []
        self._cfg = OneWireAdapterConfig(**(config or {}))
        self._proxy: Any = None
        self._owlock = asyncio.Lock()
        # Single-worker pool for all proxy calls — see module docstring for why
        # this needs to be a dedicated one-thread executor rather than the loop's
        # default one.
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        # Set on successful connect() — lets _poll_loop distinguish connection-level
        # pyownet errors (ConnError/OwnetTimeout/ProtocolError) from per-property
        # OwnetError (owserver reachable, just an error for that one path).
        self._owprotocol: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._cfg = OneWireAdapterConfig(**self._config)

        try:
            import pyownet.protocol as owprotocol
        except ImportError:
            logger.error("pyownet not installed — 1-Wire adapter disabled")
            await self._publish_status(False, "pyownet not installed", code="libNotInstalled", params={"lib": "pyownet"})
            return

        if self._executor is not None:
            self._executor.shutdown(wait=False)
        self._owprotocol = owprotocol
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="onewire-owserver")

        try:
            self._proxy = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: owprotocol.proxy(host=self._cfg.host, port=self._cfg.port, persistent=True),
            )
        except owprotocol.Error as exc:
            # Covers ConnError (socket-level refusal/unreachable), OwnetTimeout,
            # and ProtocolError subclasses (MalformedHeader/ShortRead/ShortWrite —
            # host:port is reachable but isn't actually owserver).
            logger.warning("1-Wire adapter: owserver connection failed (%s:%d): %s", self._cfg.host, self._cfg.port, exc)
            await self._publish_status(
                False,
                f"Could not connect to {self._cfg.host}:{self._cfg.port}: {exc}",
                code="couldNotConnectTo",
                params={"host": self._cfg.host, "port": self._cfg.port},
            )
            self._executor.shutdown(wait=False)
            self._executor = None
            return

        await self._publish_status(
            True,
            f"Connected to {self._cfg.host}:{self._cfg.port}",
            code="connectedTo",
            params={"host": self._cfg.host, "port": self._cfg.port},
        )
        logger.info("1-Wire adapter connected: owserver %s:%d", self._cfg.host, self._cfg.port)

    async def disconnect(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()
        if self._proxy is not None and self._executor is not None:
            close = getattr(self._proxy, "close_connection", None)
            if close is not None:
                # Cancelling poll tasks above does not stop an in-flight blocking
                # read/write already handed to the executor — the single-worker
                # pool (see module docstring) still processes this close strictly
                # after that call, whether or not its owning task got cancelled.
                async with self._owlock:
                    await asyncio.get_event_loop().run_in_executor(self._executor, close)
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        self._proxy = None
        await self._publish_status(False, "Disconnected", code="disconnected")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()

        if self._proxy is None:
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
                value = await self._read_property(bc.sensor_id, bc.property)
                quality = "good" if value is not None else "bad"
                if quality == "good":
                    if not self.connected:
                        # Recovered after a connection-level failure below.
                        await self._publish_status(
                            True,
                            f"Connected to {self._cfg.host}:{self._cfg.port}",
                            code="connectedTo",
                            params={"host": self._cfg.host, "port": self._cfg.port},
                        )
                        logger.info("1-Wire adapter: connection to owserver recovered")
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
                if (
                    self._owprotocol is not None
                    and isinstance(exc, (self._owprotocol.ConnError, self._owprotocol.OwnetTimeout, self._owprotocol.ProtocolError))
                    and self.connected
                ):
                    # Connection-level failure (owserver gone/unreachable), as
                    # opposed to a per-property OwnetError (owserver is fine,
                    # just an error for this one path) — surface it on the
                    # adapter itself, not just as bad quality on this one event.
                    await self._publish_status(
                        False,
                        f"Lost connection to {self._cfg.host}:{self._cfg.port}: {exc}",
                        code="couldNotConnectTo",
                        params={"host": self._cfg.host, "port": self._cfg.port},
                    )
                    logger.warning("1-Wire adapter: connection to owserver lost: %s", exc)
                logger.warning("1-Wire poll error (sensor %s/%s): %s", bc.sensor_id, bc.property, exc)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=None,
                        quality="bad",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
            await asyncio.sleep(self._cfg.poll_interval)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        if self._proxy is None:
            return None
        try:
            bc = OneWireBindingConfig(**binding.config)
            return await self._read_property(bc.sensor_id, bc.property)
        except Exception:
            logger.exception("1-Wire read failed for binding %s", binding.id)
            return None

    async def write(self, binding: Any, value: Any) -> None:
        if self._proxy is None or self._executor is None:
            logger.debug("1-Wire write skipped — not connected (binding %s)", binding.id)
            return
        path = None
        try:
            bc = OneWireBindingConfig(**binding.config)
            path = f"/{_normalize_sensor_id(bc.sensor_id)}/{bc.property}"
            # OWFS yes/no properties expect "1"/"0" — str(True)/str(False) would
            # send the literal words "True"/"False", which owserver would reject
            # or misinterpret (mirrors the "0"/"1" parsing in _read_property()).
            data = (b"1" if value else b"0") if isinstance(value, bool) else str(value).encode()
            async with self._owlock:
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    lambda: self._proxy.write(path, data, timeout=self._cfg.request_timeout),
                )
        except Exception as exc:
            # No adapter-side writability allowlist is maintained — owserver's own
            # error is the source of truth for whether a property is writable.
            logger.warning("1-Wire write failed for binding %s (%s): %s", binding.id, path, exc)

    async def _read_property(self, sensor_id: str, property_name: str) -> float | bool | str | None:
        path = f"/{_normalize_sensor_id(sensor_id)}/{property_name}"
        async with self._owlock:
            raw = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._proxy.read(path, timeout=self._cfg.request_timeout),
            )
        text = raw.decode("utf-8", errors="replace").strip()
        if _YESNO_PROPERTY_RE.match(property_name):
            try:
                return bool(int(text))
            except ValueError:
                return text
        try:
            return float(text)
        except ValueError:
            return text

    # ------------------------------------------------------------------
    # Browse (binding-form sensor/property picker, issue #6)
    # ------------------------------------------------------------------

    async def browse_sensors(self) -> list[dict[str, Any]]:
        """Scan owserver's root directory for ROM-ID devices and their properties."""
        if self._proxy is None or self._executor is None:
            return []

        # slash=True (owserver's default) marks directory entries with a trailing "/" —
        # used both to recognize ROM-ID devices and, per-sensor, to drop nested
        # sub-directories (e.g. DS18B20's "errata/") that aren't readable leaf values.
        async with self._owlock:
            entries = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._proxy.dir("/", timeout=self._cfg.request_timeout),
            )

        sensors: list[dict[str, Any]] = []
        for entry in entries:
            match = _ROM_ID_RE.match(entry)
            if match:
                rom_id = match.group(1)
                family = rom_id.split(".", 1)[0]
            else:
                # Not a bare ROM-ID entry — could be an OWFS alias (e.g. "/boiler/",
                # configured in /etc/owfs.conf) or one of owserver's own system/meta
                # directories ("settings", "structure", "uncached", ...). The
                # "address" property exists on every real device directory (aliased
                # or not) but not on system directories, so use it to resolve real
                # devices without needing to parse OWFS's alias syntax ourselves.
                name = entry.strip("/")
                try:
                    async with self._owlock:
                        address = await asyncio.get_event_loop().run_in_executor(
                            self._executor,
                            lambda n=name: self._proxy.read(f"/{n}/address", timeout=self._cfg.request_timeout),
                        )
                    resolved = address.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                # "address" is usually the raw undotted 16-hex ROM code
                # (family+serial+crc8); some owserver builds report the dotted
                # family.serial shape instead — accept either.
                if _ROM_ID_SHAPE_RE.fullmatch(resolved):
                    family = resolved.split(".", 1)[0]
                elif _RAW_ADDRESS_RE.fullmatch(resolved):
                    family = resolved[:2]
                else:
                    continue
                rom_id = name

            async with self._owlock:
                props = await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    lambda p=rom_id: self._proxy.dir(f"/{p}", timeout=self._cfg.request_timeout),
                )
            properties = sorted(p.rsplit("/", 1)[-1] for p in props if not p.endswith("/") and p.rsplit("/", 1)[-1] not in _STRUCTURAL_PROPERTIES)
            sensors.append(
                {
                    "rom_id": rom_id,
                    "family": family,
                    "properties": properties,
                    "alias": self._cfg.aliases.get(rom_id),
                },
            )
        return sensors
