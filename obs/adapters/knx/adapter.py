"""KNX Adapter — Phase 3

Verbindet sich mit einem KNX/IP-Gateway (Tunneling, Routing oder IP Secure).
Nutzt xknx für das Protokoll, eigenen DPTRegistry für Codierung.

xknx ≥ 3.x: Device-dispatch läuft über _iter_remote_values() → GA-Map.
has_group_address() wird in xknx 3.x nicht mehr für Dispatch verwendet.
Der _TelegramSniffer wird deshalb NACH dem Laden der Bindings erstellt.

Binding-Konfiguration (pro AdapterBinding.config):
  group_address:       str   — Gruppenadresse z.B. "1/2/3"
  dpt_id:              str   — z.B. "DPT9.001"
  state_group_address: str?  — Rückmelde-GA für DEST-Bindings (optional)

Adapter-Konfiguration (adapter_configs.config in DB):
  connection_type:   "tunneling" | "tunneling_tcp" | "tunneling_secure" |
                     "routing" | "routing_secure"
  --- Tunneling UDP / TCP ---
  host:              str   (IP des KNX/IP-Interfaces)
  port:              int   (default: 3671)
  individual_address: str  (default: "1.1.255"; bei Keyfile: wählt Tunnel-Endpoint)
  local_ip:          str?  (lokale IP zum Binden, optional)
  --- Routing (multicast) ---
  multicast_group:   str   (default: "224.0.23.12"; KNX-Standard-Multicastadresse)
  multicast_port:    int   (default: 3671)
  individual_address: str  (Quelladresse des Routers)
  local_ip:          str?  (Netzwerkinterface für Multicast, optional)
  --- KNX IP Secure — Keyfile-Modus (tunneling_secure / routing_secure) ---
  knxkeys_file_path: str?  (Pfad zur gespeicherten .knxkeys Datei)
  knxkeys_password:  str?  (Passwort-Feld)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.knx.dpt_registry import DPTRegistry
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

TUNNEL_OVERLOAD_DETAIL = "KNX-Tunnel-Slot wahrscheinlich von anderem Client belegt — Gateway-Pool überlastet."

# Import APCI classes at module level so missing symbols fail loudly at startup
try:
    from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

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
    connection_type: Literal[
        "tunneling",
        "tunneling_tcp",
        "tunneling_secure",
        "routing",
        "routing_secure",
    ] = "tunneling"
    # Tunneling UDP/TCP: IP des KNX/IP-Interfaces
    host: str = "192.168.1.100"
    port: int = 3671
    individual_address: str = "1.1.255"
    local_ip: str | None = None
    # Routing: Multicast-Gruppe (KNX-Standard: 224.0.23.12)
    multicast_group: str = "224.0.23.12"
    multicast_port: int = 3671
    # KNX IP Secure — Keyfile-Modus
    user_id: int = Field(default=2, ge=1, le=127)
    knxkeys_file_path: str | None = None
    knxkeys_password: str | None = Field(
        default=None,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    # KNX IP Secure — Manueller Modus (Fallback, nicht in GUI exponiert)
    user_password: str | None = Field(
        default=None,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    device_authentication_password: str | None = Field(
        default=None,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    backbone_key: str | None = Field(
        default=None,
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    # Issue #466: tunnel-pool overload detection.
    # `threshold` disconnects within `window_s` seconds raise a visible warning
    # on the adapter card without flipping the connected flag.
    tunnel_overload_threshold: int = Field(default=3, ge=1)
    tunnel_overload_window_s: int = Field(default=300, ge=1)


class KnxBindingConfig(BaseModel):
    group_address: str  # z.B. "1/2/3"
    dpt_id: str = "DPT1.001"
    state_group_address: str | None = None  # DEST-Bindings Rückmelde-GA
    respond_to_read: bool = False  # SOURCE: antworte auf GroupValueRead mit aktuellem Wert


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
        self._reconnect_task: asyncio.Task | None = None
        self._stopped: bool = False
        # Tunnel-overload detection (issue #466)
        self._disconnect_times: deque[datetime] = deque()
        self._warning_active: bool = False

    @staticmethod
    def _now() -> datetime:
        """Monkeypatch-able clock seam for deterministic tests."""
        return datetime.now(UTC)

    def set_value_getter(self, getter: Any) -> None:
        """Set a callable that returns ValueState | None for a datapoint UUID."""
        self._value_getter = getter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._stopped = False
        await self._do_connect()
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _do_connect(self) -> None:
        """Internal connect attempt — creates a fresh xknx instance and starts it."""
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType
            from xknx.telegram.address import IndividualAddress
        except ImportError:
            logger.error("xknx not installed — KNX adapter disabled")
            await self._publish_status(False, "xknx not installed", severity="error")
            return

        # Clean up any previous xknx instance before creating a new one
        self._sniffer = None
        if self._xknx:
            try:
                await self._xknx.stop()
            except Exception:
                pass
            self._xknx = None

        cfg = KnxAdapterConfig(**self._config)

        _conn_type_map = {
            "tunneling": ConnectionType.TUNNELING,
            "tunneling_tcp": ConnectionType.TUNNELING_TCP,
            "tunneling_secure": ConnectionType.TUNNELING_TCP_SECURE,
            "routing": ConnectionType.ROUTING,
            "routing_secure": ConnectionType.ROUTING_SECURE,
        }
        conn_type = _conn_type_map.get(cfg.connection_type, ConnectionType.TUNNELING)

        # Build SecureConfig for KNX IP Secure modes
        secure_config = None
        if cfg.connection_type in ("tunneling_secure", "routing_secure"):
            try:
                from xknx.io import SecureConfig

                if cfg.knxkeys_file_path and cfg.knxkeys_password:
                    # Keyfile-Modus (bevorzugt): xknx liest alle Credentials aus dem .knxkeys File.
                    # individual_address in ConnectionConfig wählt den Tunnel aus.
                    secure_config = SecureConfig(
                        knxkeys_file_path=cfg.knxkeys_file_path,
                        knxkeys_password=cfg.knxkeys_password,
                    )
                    logger.info("KNX IP Secure: Keyfile-Modus (%s)", cfg.knxkeys_file_path)
                elif cfg.connection_type == "tunneling_secure":
                    # Manueller Modus Tunneling: Credentials einzeln angeben
                    secure_config = SecureConfig(
                        device_authentication_password=cfg.device_authentication_password or "",
                        user_id=cfg.user_id,
                        user_password=cfg.user_password or "",
                    )
                    logger.info("KNX IP Secure: Manueller Modus (Tunneling)")
                else:
                    # Manueller Modus Routing: Backbone-Key
                    if not cfg.backbone_key:
                        await self._publish_status(
                            False,
                            "routing_secure erfordert backbone_key oder knxkeys_file_path",
                            severity="error",
                        )
                        logger.error("KNX IP Secure (Routing): backbone_key und knxkeys_file_path fehlen")
                        return
                    backbone_hex = cfg.backbone_key.replace(":", "").replace(" ", "")
                    bytes.fromhex(backbone_hex)  # Früh-Validierung: ValueError bei ungültigem Hex
                    secure_config = SecureConfig(backbone_key=backbone_hex)
                    logger.info("KNX IP Secure: Manueller Modus (Routing, backbone_key)")
            except ValueError as exc:
                await self._publish_status(False, f"KNX Backbone-Key ungültig (kein Hex-String): {exc}", severity="error")
                logger.error("KNX IP Secure backbone_key Parse-Fehler: %s", exc)
                return
            except Exception as exc:
                await self._publish_status(False, f"KNX IP Secure Konfigurationsfehler: {exc}", severity="error")
                logger.error("KNX IP Secure Konfigurationsfehler: %s", exc)
                return

        is_routing = cfg.connection_type in ("routing", "routing_secure")

        if is_routing:
            conn_cfg = ConnectionConfig(
                connection_type=conn_type,
                multicast_group=cfg.multicast_group,
                multicast_port=cfg.multicast_port,
                local_ip=cfg.local_ip,
                individual_address=IndividualAddress(cfg.individual_address),
                secure_config=secure_config,
            )
        else:
            conn_cfg = ConnectionConfig(
                connection_type=conn_type,
                gateway_ip=cfg.host,
                gateway_port=cfg.port,
                local_ip=cfg.local_ip,
                individual_address=IndividualAddress(cfg.individual_address),
                secure_config=secure_config,
            )

        self._xknx = XKNX(
            connection_config=conn_cfg,
            connection_state_changed_cb=self._on_xknx_connection_state,
        )

        try:
            await self._xknx.start()
            if is_routing:
                await self._publish_status(True, f"Connected (routing {cfg.multicast_group}:{cfg.multicast_port})")
                logger.info("KNX adapter connected: routing %s:%d", cfg.multicast_group, cfg.multicast_port)
            else:
                await self._publish_status(True, f"Connected to {cfg.host}:{cfg.port}")
                logger.info(
                    "KNX adapter connected: %s:%d (%s)",
                    cfg.host,
                    cfg.port,
                    cfg.connection_type,
                )
            # Rebuild sniffer on the new xknx instance
            await self._on_bindings_reloaded()
        except Exception as exc:
            await self._publish_status(False, str(exc), severity="error")
            logger.warning("KNX connect failed: %s", exc)

    async def _reconnect_loop(self) -> None:
        """Background task: reconnect every 30 s when not connected."""
        while not self._stopped:
            await asyncio.sleep(30)
            if self._stopped:
                break
            if not self._connected:
                logger.info("KNX: not connected — attempting reconnect …")
                await self._do_connect()

    # ------------------------------------------------------------------
    # Tunnel-pool overload detection — issue #466
    # ------------------------------------------------------------------

    def _prune_disconnects(self, now: datetime) -> None:
        """Drop disconnect timestamps older than tunnel_overload_window_s."""
        try:
            window_s = int(self._config.get("tunnel_overload_window_s", 300))
        except (TypeError, ValueError):
            window_s = 300
        cutoff = now.timestamp() - window_s
        while self._disconnect_times and self._disconnect_times[0].timestamp() < cutoff:
            self._disconnect_times.popleft()

    async def _record_disconnect(self) -> None:
        """Note a disconnect event; raise warning if it exceeds the threshold."""
        try:
            threshold = int(self._config.get("tunnel_overload_threshold", 3))
        except (TypeError, ValueError):
            threshold = 3
        now = self._now()
        self._disconnect_times.append(now)
        self._prune_disconnects(now)

        if not self._warning_active and len(self._disconnect_times) >= threshold:
            self._warning_active = True
            await self._publish_status(
                connected=self._connected,
                detail=TUNNEL_OVERLOAD_DETAIL,
                severity="warning",
            )
            logger.warning(
                "KNX tunnel-pool overload suspected: %d disconnects in last %ss",
                len(self._disconnect_times),
                self._config.get("tunnel_overload_window_s", 300),
            )

    async def _record_reconnect(self) -> None:
        """Note a (re)connect event; clear warning once the window is quiet."""
        now = self._now()
        self._prune_disconnects(now)
        if self._warning_active and not self._disconnect_times:
            self._warning_active = False
            await self._publish_status(
                connected=self._connected,
                detail="Tunnel-Pool wieder stabil.",
                severity="ok",
            )
            logger.info("KNX tunnel-pool warning cleared (quiet window).")

    def _on_xknx_connection_state(self, state: Any) -> None:
        """Sync callback registered with xknx.connection_state_changed_cb.

        xknx calls this from the main loop via call_soon_threadsafe, so the
        actual async bookkeeping is scheduled via create_task.
        """
        try:
            from xknx.core.connection_state import XknxConnectionState
        except ImportError:
            return
        if state == XknxConnectionState.DISCONNECTED:
            asyncio.ensure_future(self._record_disconnect())
        elif state == XknxConnectionState.CONNECTED:
            asyncio.ensure_future(self._record_reconnect())
        # CONNECTING is a transient state — ignored.

    async def disconnect(self) -> None:
        self._stopped = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None
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
            len(self._ga_source_map),
            len(self._bindings),
            list(self._ga_source_map.keys()),
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
                devices_before,
                devices_after,
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
                    from obs.core.formula import apply_formula

                    value = apply_formula(binding.value_formula, value)
                if binding.value_map:
                    from obs.core.transformation import apply_value_map

                    value = apply_value_map(value, binding.value_map)
                logger.info("KNX value: GA=%s → dp=%s value=%s", ga, binding.datapoint_id, value)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=value,
                        quality=quality,
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
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
                        ga,
                        binding.datapoint_id,
                    )
                    continue
                from xknx.dpt import DPTArray, DPTBinary
                from xknx.telegram import Telegram
                from xknx.telegram.address import GroupAddress

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
                    ga,
                    binding.datapoint_id,
                    state.value,
                    raw.hex(),
                )
            except Exception:
                logger.exception(
                    "KNX _handle_read_request failed for GA=%s binding=%s",
                    ga,
                    binding.id,
                )

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
            from xknx.dpt import DPTArray, DPTBinary  # xknx ≥ 3.x
            from xknx.telegram import Telegram
            from xknx.telegram.address import GroupAddress
            from xknx.telegram.apci import GroupValueWrite as _GVW

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
    """Build and register a minimal xknx Device that receives all source GAs.

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
            device_name="obs_sniffer",
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
            super().__init__(xknx_instance, "obs_sniffer")

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
