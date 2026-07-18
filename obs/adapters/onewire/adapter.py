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
  aliases:  dict[str, str]  — persistenter ROM-ID → Klartext-Label Map, gepflegt
                              über die Binding-Scan-UI (issue #6, Punkt 2)

Binding-Konfiguration (AdapterBinding.config):
  sensor_id: str  — ROM-ID, z.B. "28.4B057F0A1C10"
  property:  str  — OWFS-Property/"Datei", z.B. "temperature", "humidity", "PIO.0"
                     (default: "temperature")

pyownet's OwnetProxy performs blocking socket I/O and is not concurrency-safe, so
all proxy calls are serialized through a single asyncio.Lock and run in an
executor thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)

_ROM_ID_RE = re.compile(r"^/([0-9A-Fa-f]{2}\.[0-9A-Fa-f]{12})/?$")
# Structural/metadata OWFS entries — not sensor readings, hidden from the browse picker.
_STRUCTURAL_PROPERTIES = frozenset(
    {"address", "alias", "crc8", "id", "locator", "r_address", "r_id", "r_locator", "type", "version", "family"},
)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class OneWireAdapterConfig(BaseModel):
    host: str = "localhost"
    port: int = 4304
    poll_interval: float = 30.0
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

        try:
            self._proxy = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: owprotocol.proxy(host=self._cfg.host, port=self._cfg.port, persistent=True),
            )
        except owprotocol.ConnError as exc:
            logger.warning("1-Wire adapter: owserver connection failed (%s:%d): %s", self._cfg.host, self._cfg.port, exc)
            await self._publish_status(
                False,
                f"Could not connect to {self._cfg.host}:{self._cfg.port}: {exc}",
                code="couldNotConnectTo",
                params={"host": self._cfg.host, "port": self._cfg.port},
            )
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
        if self._proxy is not None:
            close = getattr(self._proxy, "close_connection", None)
            if close is not None:
                await asyncio.get_event_loop().run_in_executor(None, close)
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
        if self._proxy is None:
            logger.debug("1-Wire write skipped — not connected (binding %s)", binding.id)
            return
        path = None
        try:
            bc = OneWireBindingConfig(**binding.config)
            path = f"/{bc.sensor_id}/{bc.property}"
            data = str(value).encode()
            async with self._owlock:
                await asyncio.get_event_loop().run_in_executor(None, self._proxy.write, path, data)
        except Exception as exc:
            # No adapter-side writability allowlist is maintained — owserver's own
            # error is the source of truth for whether a property is writable.
            logger.warning("1-Wire write failed for binding %s (%s): %s", binding.id, path, exc)

    async def _read_property(self, sensor_id: str, property_name: str) -> float | str | None:
        path = f"/{sensor_id}/{property_name}"
        async with self._owlock:
            raw = await asyncio.get_event_loop().run_in_executor(None, self._proxy.read, path)
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            return float(text)
        except ValueError:
            return text

    # ------------------------------------------------------------------
    # Browse (binding-form sensor/property picker, issue #6)
    # ------------------------------------------------------------------

    async def browse_sensors(self) -> list[dict[str, Any]]:
        """Scan owserver's root directory for ROM-ID devices and their properties."""
        if self._proxy is None:
            return []

        # slash=True (owserver's default) marks directory entries with a trailing "/" —
        # used both to recognize ROM-ID devices and, per-sensor, to drop nested
        # sub-directories (e.g. DS18B20's "errata/") that aren't readable leaf values.
        async with self._owlock:
            entries = await asyncio.get_event_loop().run_in_executor(None, self._proxy.dir, "/")

        sensors: list[dict[str, Any]] = []
        for entry in entries:
            match = _ROM_ID_RE.match(entry)
            if not match:
                continue
            rom_id = match.group(1)
            async with self._owlock:
                props = await asyncio.get_event_loop().run_in_executor(None, lambda p=rom_id: self._proxy.dir(f"/{p}"))
            properties = sorted(p.rsplit("/", 1)[-1] for p in props if not p.endswith("/") and p.rsplit("/", 1)[-1] not in _STRUCTURAL_PROPERTIES)
            sensors.append(
                {
                    "rom_id": rom_id,
                    "family": rom_id.split(".", 1)[0],
                    "properties": properties,
                    "alias": self._cfg.aliases.get(rom_id),
                },
            )
        return sensors
