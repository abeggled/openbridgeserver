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
import math
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from obs.adapters.base import (
    AdapterBase,
    AdapterDelegationCapability,
    ConfirmationActionContext,
    ConfirmationActionToken,
    ConfirmationWriteOrder,
)
from obs.adapters.knx.dpt_registry import DPTRegistry
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

TUNNEL_OVERLOAD_DETAIL = "KNX-Tunnel-Slot wahrscheinlich von anderem Client belegt — Gateway-Pool überlastet."
ECHO_SUPPRESSION_WINDOW_S = 2.0
STATE_CONFIRMATION_WINDOW_S = 30.0

# Import APCI classes at module level so missing symbols fail loudly at startup
try:
    from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

    _APCI_IMPORTED = True
except ImportError:
    GroupValueWrite = None  # type: ignore[assignment,misc]
    GroupValueResponse = None  # type: ignore[assignment,misc]
    GroupValueRead = None  # type: ignore[assignment,misc]
    _APCI_IMPORTED = False

# Module-level keyring import — makes sync_load_keyring patchable in tests
try:
    from xknx.secure.keyring import sync_load_keyring
except ImportError:
    sync_load_keyring = None  # type: ignore[assignment]

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
    delegation_capabilities: frozenset[AdapterDelegationCapability] = frozenset()
    config_schema = KnxAdapterConfig
    binding_config_schema = KnxBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._xknx: Any = None
        self._sniffer: Any = None
        self._ga_source_map: dict[str, list[tuple[Any, Any]]] = {}
        self._ga_respond_map: dict[str, list[tuple[Any, Any]]] = {}
        self._recent_writes: dict[
            tuple[str, str],
            deque[
                tuple[
                    float,
                    bytes,
                    Any,
                    ConfirmationActionContext | bool,
                    tuple[Any, ...],
                    ConfirmationWriteOrder | None,
                ]
            ],
        ] = {}
        self._pending_transmissions: dict[
            int,
            tuple[Any, str, str | None, bytes, Any, ConfirmationActionContext | bool, tuple[Any, ...]],
        ] = {}
        self._pending_telegram_refs: dict[int, Any] = {}
        self._outgoing_confirmation_owners: dict[int, tuple[str, float, Any, tuple[Any, ...]]] = {}
        self._local_read_responses: dict[
            int,
            tuple[Any, str, str, tuple[Any, ...], float | None],
        ] = {}
        self._invalidated_transmissions: dict[int, tuple[Any, str, str, float | None]] = {}
        self._invalidated_state_confirmations: dict[
            tuple[str, bytes],
            deque[tuple[float, str, str]],
        ] = {}
        self._latest_confirmation_at: dict[str, float] = {}
        self._value_getter: Any = None
        self._reconnect_task: asyncio.Task | None = None
        self._echo_cleanup_task: asyncio.Task | None = None
        self._stopped: bool = False
        # Tunnel-overload detection (issue #466)
        self._disconnect_times: deque[datetime] = deque()
        self._warning_active: bool = False

    @staticmethod
    def _now() -> datetime:
        """Monkeypatch-able clock seam for deterministic tests."""
        return datetime.now(UTC)

    @staticmethod
    def _monotonic() -> float:
        """Monkeypatch-able monotonic clock seam for echo suppression tests."""
        return time.monotonic()

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
        if self._echo_cleanup_task is None or self._echo_cleanup_task.done():
            self._echo_cleanup_task = asyncio.ensure_future(self._echo_cleanup_loop())

    async def _do_connect(self) -> None:
        """Internal connect attempt — creates a fresh xknx instance and starts it."""
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType
            from xknx.telegram.address import IndividualAddress
        except ImportError:
            logger.error("xknx not installed — KNX adapter disabled")
            await self._publish_status(False, "xknx not installed", severity="error", code="libNotInstalled", params={"lib": "xknx"})
            return

        # Clean up any previous xknx instance before creating a new one
        self._sniffer = None
        if self._xknx:
            try:
                await self._xknx.stop()
            except Exception:
                logger.exception("KNX cleanup of previous instance failed")
            self._pending_transmissions.clear()
            self._pending_telegram_refs.clear()
            self._local_read_responses = {
                telegram_id: response for telegram_id, response in self._local_read_responses.items() if response[4] is not None
            }
            self._invalidated_transmissions = {
                telegram_id: transmission for telegram_id, transmission in self._invalidated_transmissions.items() if transmission[3] is not None
            }
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
        resolved_individual_address = cfg.individual_address
        if cfg.connection_type in ("tunneling_secure", "routing_secure"):
            try:
                from xknx.io import SecureConfig

                if cfg.knxkeys_file_path and cfg.knxkeys_password:
                    # Keyfile-Modus: OBS extrahiert Credentials selbst aus dem .knxkeys File
                    # und übergibt sie explizit an SecureConfig.  Dadurch entfällt der interne
                    # UDP-DescriptionRequest von xknx, der in Docker-Bridge-Netzwerken scheitert,
                    # weil keine Route zurück zum Container besteht (Issue #393).
                    keyfile_result = _secure_config_from_keyfile(
                        cfg.knxkeys_file_path,
                        cfg.knxkeys_password,
                        cfg.connection_type,
                        cfg.individual_address,
                    )
                    if keyfile_result is None:
                        if cfg.connection_type == "routing_secure":
                            detail = "Kein Backbone-Key im Keyfile — das Keyfile enthält keinen Backbone-Eintrag für Routing Secure."
                            keyfile_code = "knxNoBackboneKeyInKeyfile"
                        else:
                            detail = "Keine Tunneling-Interfaces im Keyfile gefunden — bitte Individual Address im Keyfile prüfen."
                            keyfile_code = "knxNoTunnelingInterfaces"
                        await self._publish_status(False, detail, severity="error", code=keyfile_code)
                        return
                    secure_config, resolved_individual_address = keyfile_result
                    logger.info("KNX IP Secure: Keyfile-Modus (%s), Credentials direkt extrahiert", cfg.knxkeys_file_path)
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
                            code="knxRoutingSecureRequiresKey",
                        )
                        logger.error("KNX IP Secure (Routing): backbone_key und knxkeys_file_path fehlen")
                        return
                    backbone_hex = cfg.backbone_key.replace(":", "").replace(" ", "")
                    bytes.fromhex(backbone_hex)  # Früh-Validierung: ValueError bei ungültigem Hex
                    secure_config = SecureConfig(backbone_key=backbone_hex)
                    logger.info("KNX IP Secure: Manueller Modus (Routing, backbone_key)")
            except ValueError as exc:
                await self._publish_status(
                    False,
                    f"KNX Backbone-Key ungültig (kein Hex-String): {exc}",
                    severity="error",
                    code="knxBackboneKeyInvalid",
                    params={"error": str(exc)},
                )
                logger.error("KNX IP Secure backbone_key Parse-Fehler: %s", exc)
                return
            except Exception as exc:
                await self._publish_status(
                    False,
                    f"KNX IP Secure Konfigurationsfehler: {exc}",
                    severity="error",
                    code="knxIpSecureConfigError",
                    params={"error": str(exc)},
                )
                logger.exception("KNX IP Secure Konfigurationsfehler")
                return

        is_routing = cfg.connection_type in ("routing", "routing_secure")

        if is_routing:
            conn_cfg = ConnectionConfig(
                connection_type=conn_type,
                multicast_group=cfg.multicast_group,
                multicast_port=cfg.multicast_port,
                local_ip=cfg.local_ip,
                individual_address=IndividualAddress(resolved_individual_address),
                secure_config=secure_config,
            )
        else:
            conn_cfg = ConnectionConfig(
                connection_type=conn_type,
                gateway_ip=cfg.host,
                gateway_port=cfg.port,
                local_ip=cfg.local_ip,
                individual_address=IndividualAddress(resolved_individual_address),
                secure_config=secure_config,
            )

        self._xknx = XKNX(
            connection_config=conn_cfg,
            connection_state_changed_cb=self._on_xknx_connection_state,
        )
        self._xknx.telegram_queue.register_telegram_received_cb(
            self._on_telegram_transmitted,
            match_for_outgoing=True,
        )

        try:
            await self._xknx.start()
            if is_routing:
                await self._publish_status(
                    True,
                    f"Connected (routing {cfg.multicast_group}:{cfg.multicast_port})",
                    code="knxConnectedRouting",
                    params={"group": cfg.multicast_group, "port": cfg.multicast_port},
                )
                logger.info("KNX adapter connected: routing %s:%d", cfg.multicast_group, cfg.multicast_port)
            else:
                await self._publish_status(
                    True,
                    f"Connected to {cfg.host}:{cfg.port}",
                    code="connectedTo",
                    params={"host": cfg.host, "port": cfg.port},
                )
                logger.info(
                    "KNX adapter connected: %s:%d (%s)",
                    cfg.host,
                    cfg.port,
                    cfg.connection_type,
                )
            # Rebuild sniffer on the new xknx instance
            await self._on_bindings_reloaded()
        except Exception as exc:
            detail = _knx_connect_error_detail(exc, cfg.connection_type)
            await self._publish_status(False, detail, severity="error")
            logger.exception("KNX connect failed")

    async def _reconnect_loop(self) -> None:
        """Background task: reconnect every 30 s when not connected."""
        while not self._stopped:
            await asyncio.sleep(30)
            if self._stopped:
                break
            if not self._connected:
                logger.info("KNX: not connected — attempting reconnect …")
                await self._do_connect()

    async def _echo_cleanup_loop(self) -> None:
        """Remove expired sent confirmation expectations."""
        while not self._stopped:
            await asyncio.sleep(ECHO_SUPPRESSION_WINDOW_S)
            self._prune_recent_writes()
            self._prune_local_read_responses()

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
                code="knxTunnelOverload",
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
                code="knxTunnelStable",
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
        if self._echo_cleanup_task and not self._echo_cleanup_task.done():
            self._echo_cleanup_task.cancel()
            try:
                await self._echo_cleanup_task
            except asyncio.CancelledError:
                pass
        self._echo_cleanup_task = None
        if self._xknx:
            try:
                await self._xknx.stop()
            except Exception:
                logger.exception("KNX disconnect error")
        self._sniffer = None
        self._recent_writes.clear()
        self._pending_transmissions.clear()
        self._pending_telegram_refs.clear()
        self._outgoing_confirmation_owners.clear()
        self._local_read_responses.clear()
        self._invalidated_transmissions.clear()
        self._invalidated_state_confirmations.clear()
        self._latest_confirmation_at.clear()
        await self._publish_status(False, "Disconnected", code="disconnected")
        self._xknx = None

    # ------------------------------------------------------------------
    # Bindings — sniffer is created/recreated here so _iter_remote_values
    # already knows the registered GAs at Device.__init__ time.
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        """Rebuild GA→binding map and re-register the sniffer Device."""
        bindings_by_id = {str(binding.id): binding for binding in self._bindings}
        invalidated_confirmation_datapoints: dict[str, str] = {}
        for key, recent_writes in list(self._recent_writes.items()):
            replacement = bindings_by_id.get(key[0])
            if replacement is None:
                self._recent_writes.pop(key, None)
                invalidated_confirmation_datapoints[key[0]] = str(recent_writes[0][4][0])
                for recent_write in recent_writes:
                    self._remember_invalidated_state_confirmation(
                        key[0],
                        key[1],
                        recent_write,
                    )
                continue
            signature = self._confirmation_binding_signature(replacement)
            invalidated = [recent_write for recent_write in recent_writes if recent_write[4] != signature]
            retained = deque(recent_write for recent_write in recent_writes if recent_write[4] == signature)
            if invalidated:
                invalidated_confirmation_datapoints[key[0]] = str(invalidated[0][4][0])
                for recent_write in invalidated:
                    self._remember_invalidated_state_confirmation(
                        key[0],
                        key[1],
                        recent_write,
                    )
            if retained:
                self._recent_writes[key] = retained
            else:
                self._recent_writes.pop(key, None)
        for telegram_id, (binding_id, _, telegram, _) in list(self._outgoing_confirmation_owners.items()):
            datapoint_id = invalidated_confirmation_datapoints.get(binding_id)
            if datapoint_id is None:
                continue
            self._outgoing_confirmation_owners.pop(telegram_id, None)
            self._invalidated_transmissions[telegram_id] = (
                telegram,
                binding_id,
                datapoint_id,
                self._monotonic(),
            )
        for telegram_id, pending in list(self._pending_transmissions.items()):
            binding, command_ga, state_ga, raw, logical_value, suppress_actions, signature = pending
            replacement = bindings_by_id.get(str(binding.id))
            if replacement is None or self._confirmation_binding_signature(replacement) != signature:
                self._pending_transmissions.pop(telegram_id, None)
                telegram = self._pending_telegram_refs.pop(telegram_id, None)
                self._invalidated_transmissions[telegram_id] = (
                    telegram,
                    str(binding.id),
                    str(signature[0]),
                    None,
                )
                continue
            self._pending_transmissions[telegram_id] = (
                replacement,
                command_ga,
                state_ga,
                raw,
                logical_value,
                suppress_actions,
                signature,
            )
        for telegram_id, response in list(self._local_read_responses.items()):
            telegram, binding_id, datapoint_id, signature, tracked_at = response
            replacement = bindings_by_id.get(binding_id)
            if replacement is not None and self._confirmation_binding_signature(replacement) == signature:
                continue
            self._local_read_responses.pop(telegram_id, None)
            self._invalidated_transmissions[telegram_id] = (
                telegram,
                binding_id,
                datapoint_id,
                tracked_at,
            )
        self._ga_source_map.clear()
        self._ga_respond_map.clear()
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = KnxBindingConfig(**binding.config)
            except (ValidationError, TypeError):
                logger.warning("Invalid KNX binding config for %s — skipped", binding.id)
                continue

            dpt = DPTRegistry.get(bc.dpt_id)
            entry = (binding, dpt)
            self._ga_source_map.setdefault(bc.group_address, []).append(entry)
            if bc.state_group_address and bc.state_group_address != bc.group_address:
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
            except Exception:
                logger.exception("KNX: sniffer remove failed")
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
            is_outgoing = getattr(getattr(telegram, "direction", None), "name", None) == "OUTGOING"

            # Handle incoming read requests: respond with current persisted value
            if isinstance(telegram.payload, GroupValueRead):
                if not is_outgoing:
                    await self._handle_read_request(ga)
                return

            if not isinstance(telegram.payload, (GroupValueWrite, GroupValueResponse)):
                return

            suppressed_local_binding_id = None
            suppressed_local_datapoint_id = None
            if is_outgoing and isinstance(telegram.payload, GroupValueResponse):
                local_response = self._local_read_responses.get(id(telegram))
                if local_response is not None and local_response[0] is telegram:
                    self._local_read_responses.pop(id(telegram), None)
                    suppressed_local_binding_id = local_response[1]
                    suppressed_local_datapoint_id = local_response[2]
                    logger.debug(
                        "KNX local read response owner ignored: GA=%s binding=%s dp=%s",
                        ga,
                        suppressed_local_binding_id,
                        suppressed_local_datapoint_id,
                    )

            if is_outgoing:
                invalidated_transmission = self._invalidated_transmissions.get(id(telegram))
                if invalidated_transmission is not None and invalidated_transmission[0] is telegram:
                    self._invalidated_transmissions.pop(id(telegram), None)
                    suppressed_local_binding_id = invalidated_transmission[1]
                    suppressed_local_datapoint_id = invalidated_transmission[2]
                    logger.debug(
                        "KNX invalidated write owner ignored: GA=%s binding=%s dp=%s",
                        ga,
                        suppressed_local_binding_id,
                        suppressed_local_datapoint_id,
                    )

            entries = self._ga_source_map.get(ga)
            if not entries:
                return

            raw = _telegram_to_bytes(telegram)
            if not is_outgoing:
                invalidated_state_owner = self._consume_invalidated_state_confirmation(ga, raw)
                if invalidated_state_owner is not None:
                    suppressed_local_binding_id, suppressed_local_datapoint_id = invalidated_state_owner
                    logger.debug(
                        "KNX invalidated state owner ignored: GA=%s binding=%s dp=%s",
                        ga,
                        suppressed_local_binding_id,
                        suppressed_local_datapoint_id,
                    )
            outgoing_owner = self._outgoing_confirmation_owners.get(id(telegram))
            confirmation_datapoint_ids = (
                self._local_confirmation_datapoint_ids(
                    ga,
                    raw,
                    is_outgoing=is_outgoing,
                )
                if not is_outgoing or outgoing_owner is not None
                else set()
            )
            consumed_confirmation_datapoints: set[str] = set()
            events: list[DataValueEvent] = []
            for binding, dpt in entries:
                datapoint_id = str(binding.datapoint_id)
                if str(binding.id) == suppressed_local_binding_id or datapoint_id == suppressed_local_datapoint_id:
                    continue
                suppress_peer_confirmation = datapoint_id in confirmation_datapoint_ids
                may_consume = (
                    outgoing_owner is not None and outgoing_owner[0] == str(binding.id)
                    if is_outgoing
                    else datapoint_id not in consumed_confirmation_datapoints
                )
                confirmation_at = None
                if may_consume:
                    confirmation_at = (
                        outgoing_owner[3][0]
                        if is_outgoing and outgoing_owner is not None
                        else self._matching_confirmation_timestamp(binding, ga, raw, is_outgoing=is_outgoing)
                    )
                if may_consume:
                    is_outbound_confirmation, logical_value, action_context, write_order = self._consume_outbound_confirmation(
                        binding,
                        ga,
                        raw,
                        is_outgoing=is_outgoing,
                        owned_write=outgoing_owner[3] if is_outgoing and outgoing_owner is not None else None,
                    )
                else:
                    is_outbound_confirmation, logical_value, action_context, write_order = False, None, False, None
                if suppress_peer_confirmation and not is_outbound_confirmation:
                    logger.debug(
                        "KNX duplicate peer confirmation ignored: GA=%s binding=%s",
                        ga,
                        binding.id,
                    )
                    continue
                if is_outbound_confirmation:
                    if write_order is not None and self._has_newer_matching_confirmation(
                        datapoint_id,
                        ga,
                        raw,
                        write_order,
                    ):
                        logger.debug(
                            "KNX superseded confirmation ignored: GA=%s binding=%s",
                            ga,
                            binding.id,
                        )
                        continue
                    if write_order is not None:
                        write_order.activate()
                        if not write_order.accept_confirmation():
                            logger.debug(
                                "KNX globally stale confirmation ignored: GA=%s binding=%s",
                                ga,
                                binding.id,
                            )
                            continue
                    else:
                        latest_confirmation_at = self._latest_confirmation_at.get(datapoint_id)
                        if confirmation_at is not None and latest_confirmation_at is not None and confirmation_at < latest_confirmation_at:
                            logger.debug(
                                "KNX stale confirmation ignored: GA=%s binding=%s",
                                ga,
                                binding.id,
                            )
                            continue
                        if confirmation_at is not None:
                            self._latest_confirmation_at[datapoint_id] = confirmation_at
                    consumed_confirmation_datapoints.add(datapoint_id)
                    logger.debug(
                        "KNX outbound confirmation: GA=%s binding=%s raw=%s",
                        ga,
                        binding.id,
                        raw.hex(),
                    )
                    suppress_action_triggers = (
                        action_context if isinstance(action_context, bool) else action_context.suppress_actions_at_confirmation()
                    )
                    value = logical_value
                    quality = "good"
                else:
                    suppress_action_triggers = False
                    try:
                        value = dpt.decoder(raw)
                        quality = "good"
                    except Exception:
                        logger.exception("KNX DPT decode error for %s (%s)", ga, dpt.dpt_id)
                        value = raw.hex() if isinstance(raw, (bytes, bytearray)) else raw
                        quality = "uncertain"

                if not is_outbound_confirmation and isinstance(value, float) and not math.isfinite(value):
                    logger.warning(
                        "KNX DPT decoded non-finite float for GA=%s (%s): %s — quality=bad",
                        ga,
                        dpt.dpt_id,
                        value,
                    )
                    quality = "bad"
                    value = None

                if not is_outbound_confirmation and binding.value_formula and quality == "good":
                    from obs.core.formula import apply_formula

                    value = apply_formula(binding.value_formula, value)
                if not is_outbound_confirmation and binding.value_map and quality != "bad":
                    from obs.core.transformation import apply_value_map

                    value = apply_value_map(value, binding.value_map)
                logger.info("KNX value: GA=%s → dp=%s value=%s", ga, binding.datapoint_id, value)
                events.append(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=value,
                        quality=quality,
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                        suppress_write_propagation=is_outbound_confirmation,
                        suppress_action_triggers=suppress_action_triggers,
                    ),
                )
            if is_outgoing:
                self._outgoing_confirmation_owners.pop(id(telegram), None)
            for event in events:
                await self._bus.publish(event)
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
                self._local_read_responses[id(telegram)] = (
                    telegram,
                    str(binding.id),
                    str(binding.datapoint_id),
                    self._confirmation_binding_signature(binding),
                    None,
                )
                try:
                    await self._xknx.telegrams.put(telegram)
                except Exception:
                    self._local_read_responses.pop(id(telegram), None)
                    raise
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

    @staticmethod
    def _confirmation_binding_signature(binding: Any) -> tuple[Any, ...]:
        """Return binding fields that must stay stable for confirmation reuse."""
        return (
            str(binding.datapoint_id),
            binding.direction,
            bool(binding.enabled),
            dict(binding.config or {}),
            binding.value_formula,
            binding.value_map,
        )

    def _remember_outbound_write(
        self,
        binding: Any,
        ga: str,
        raw: bytes,
        logical_value: Any,
        *,
        written_at: float | None = None,
        suppress_action_triggers: bool = False,
        action_context: ConfirmationActionContext | None = None,
        write_order: ConfirmationWriteOrder | None = None,
    ) -> tuple[Any, ...]:
        """Remember a BOTH-binding write so its immediate confirmation can be recognized."""
        key = (str(binding.id), ga)
        now = self._monotonic() if written_at is None else written_at
        self._prune_recent_writes(now)
        recent_writes = self._recent_writes.setdefault(key, deque())
        recent_write = (
            now,
            bytes(raw),
            logical_value,
            action_context if action_context is not None else suppress_action_triggers,
            self._confirmation_binding_signature(binding),
            write_order,
        )
        recent_writes.append(recent_write)
        return recent_write

    def _activate_outbound_write(self, telegram: Any, ga: str, raw: bytes) -> None:
        """Start confirmation windows after XKNX has transmitted a queued telegram."""
        pending = self._pending_transmissions.pop(id(telegram), None)
        self._pending_telegram_refs.pop(id(telegram), None)
        if pending is None:
            return

        binding, command_ga, state_ga, written_raw, logical_value, action_context, _ = pending
        if ga != command_ga or written_raw != bytes(raw):
            self._invalidated_transmissions[id(telegram)] = (
                telegram,
                str(binding.id),
                str(binding.datapoint_id),
                self._monotonic(),
            )
            logger.warning(
                "KNX transmitted telegram differs from queued write: GA=%s expected_GA=%s",
                ga,
                command_ga,
            )
            return

        write_order = None if isinstance(action_context, bool) else action_context.write_order
        written_at = self._monotonic()
        command_write = self._remember_outbound_write(
            binding,
            command_ga,
            written_raw,
            logical_value,
            written_at=written_at,
            suppress_action_triggers=action_context if isinstance(action_context, bool) else False,
            action_context=None if isinstance(action_context, bool) else action_context,
            write_order=write_order,
        )
        self._outgoing_confirmation_owners[id(telegram)] = (str(binding.id), written_at, telegram, command_write)
        if state_ga and state_ga != command_ga:
            state_action_context = (
                action_context if isinstance(action_context, ConfirmationActionContext) and action_context.shares_action_token else True
            )
            self._remember_outbound_write(
                binding,
                state_ga,
                written_raw,
                logical_value,
                written_at=written_at,
                suppress_action_triggers=state_action_context if isinstance(state_action_context, bool) else False,
                action_context=None if isinstance(state_action_context, bool) else state_action_context,
                write_order=write_order,
            )

    def _on_telegram_transmitted(self, telegram: Any) -> None:
        """Activate confirmation expectations after XKNX successfully sends a telegram."""
        if getattr(getattr(telegram, "direction", None), "name", None) != "OUTGOING":
            return
        local_response = self._local_read_responses.get(id(telegram))
        if local_response is not None and local_response[0] is telegram:
            _, binding_id, datapoint_id, signature, _ = local_response
            self._local_read_responses[id(telegram)] = (
                telegram,
                binding_id,
                datapoint_id,
                signature,
                self._monotonic(),
            )
            return
        invalidated_transmission = self._invalidated_transmissions.get(id(telegram))
        if invalidated_transmission is not None and invalidated_transmission[0] is telegram:
            _, binding_id, datapoint_id, _ = invalidated_transmission
            self._invalidated_transmissions[id(telegram)] = (
                telegram,
                binding_id,
                datapoint_id,
                self._monotonic(),
            )
            return
        self._activate_outbound_write(
            telegram,
            str(telegram.destination_address),
            _telegram_to_bytes(telegram),
        )

    def _prune_recent_writes(self, now: float | None = None) -> None:
        """Remove expired command echoes and bounded state-feedback expectations."""
        current = self._monotonic() if now is None else now
        command_cutoff = current - ECHO_SUPPRESSION_WINDOW_S
        state_cutoff = current - STATE_CONFIRMATION_WINDOW_S
        for key, recent_writes in list(self._recent_writes.items()):
            key_ga = key[1]
            retained = deque(
                recent_write
                for recent_write in recent_writes
                if recent_write[0]
                >= (
                    state_cutoff
                    if (recent_write[4][3].get("state_group_address") == key_ga and recent_write[4][3].get("group_address") != key_ga)
                    else command_cutoff
                )
            )
            if retained:
                self._recent_writes[key] = retained
            else:
                self._recent_writes.pop(key, None)
        owner_cutoff = current - ECHO_SUPPRESSION_WINDOW_S
        for telegram_id, (_, transmitted_at, _, _) in list(self._outgoing_confirmation_owners.items()):
            if transmitted_at < owner_cutoff:
                self._outgoing_confirmation_owners.pop(telegram_id, None)
        self._prune_invalidated_state_confirmations(current)

    def _remember_invalidated_state_confirmation(
        self,
        binding_id: str,
        ga: str,
        recent_write: tuple[Any, ...],
    ) -> None:
        """Retain bounded identity-independent suppression for invalidated state feedback."""
        written_at, raw, _, _, signature, *_ = recent_write
        config = signature[3]
        if config.get("state_group_address") != ga or config.get("group_address") == ga:
            return
        tombstones = self._invalidated_state_confirmations.setdefault((ga, bytes(raw)), deque())
        tombstones.append((written_at, binding_id, str(signature[0])))

    def _prune_invalidated_state_confirmations(self, now: float | None = None) -> None:
        """Remove invalidated state-feedback markers after the feedback window."""
        cutoff = (self._monotonic() if now is None else now) - STATE_CONFIRMATION_WINDOW_S
        for key, tombstones in list(self._invalidated_state_confirmations.items()):
            retained = deque(tombstone for tombstone in tombstones if tombstone[0] >= cutoff)
            if retained:
                self._invalidated_state_confirmations[key] = retained
            else:
                self._invalidated_state_confirmations.pop(key, None)

    def _consume_invalidated_state_confirmation(
        self,
        ga: str,
        raw: bytes,
    ) -> tuple[str, str] | None:
        """Consume one invalidated state expectation matching an incoming telegram."""
        self._prune_recent_writes()
        self._prune_invalidated_state_confirmations()
        key = (ga, bytes(raw))
        tombstones = self._invalidated_state_confirmations.get(key)
        if not tombstones:
            return None
        while tombstones:
            written_at, binding_id, datapoint_id = tombstones.popleft()
            has_newer_live_expectation = any(
                key_ga == ga
                and (live_binding_id == binding_id or str(recent_write[4][0]) == datapoint_id)
                and recent_write[1] == raw
                and recent_write[0] > written_at
                for (live_binding_id, key_ga), recent_writes in self._recent_writes.items()
                for recent_write in recent_writes
            )
            if not has_newer_live_expectation:
                if not tombstones:
                    self._invalidated_state_confirmations.pop(key, None)
                return binding_id, datapoint_id
        if not tombstones:
            self._invalidated_state_confirmations.pop(key, None)
        return None

    def _prune_local_read_responses(self, now: float | None = None) -> None:
        """Remove transmitted read responses not dispatched to the sniffer."""
        cutoff = (self._monotonic() if now is None else now) - ECHO_SUPPRESSION_WINDOW_S
        for telegram_id, (_, _, _, _, tracked_at) in list(self._local_read_responses.items()):
            if tracked_at is not None and tracked_at < cutoff:
                self._local_read_responses.pop(telegram_id, None)
        for telegram_id, (_, _, _, tracked_at) in list(self._invalidated_transmissions.items()):
            if tracked_at is not None and tracked_at < cutoff:
                self._invalidated_transmissions.pop(telegram_id, None)

    def _matching_confirmation_timestamp(
        self,
        binding: Any,
        ga: str,
        raw: bytes,
        *,
        is_outgoing: bool,
    ) -> float | None:
        """Return the write timestamp represented by a matching confirmation."""
        recent_writes = self._recent_writes.get((str(binding.id), ga))
        if not recent_writes:
            return None
        state_ga = binding.config.get("state_group_address")
        is_distinct_state_ga = state_ga == ga and state_ga != binding.config.get("group_address")
        if is_distinct_state_ga:
            if is_outgoing:
                return None
            matching = [recent_write for recent_write in recent_writes if recent_write[1] == raw]
            return matching[-1][0] if matching else None
        if not is_outgoing:
            return None
        return next((written_at for written_at, written_raw, *_ in recent_writes if written_raw == raw), None)

    def _local_confirmation_datapoint_ids(
        self,
        ga: str,
        raw: bytes,
        *,
        is_outgoing: bool,
    ) -> set[str]:
        """Return datapoints with a matching local confirmation on this address."""
        self._prune_recent_writes()
        datapoint_ids: set[str] = set()
        for (_, key_ga), recent_writes in self._recent_writes.items():
            if key_ga != ga:
                continue
            for recent_write in recent_writes:
                written_raw = recent_write[1]
                signature = recent_write[4]
                if written_raw != raw:
                    continue
                config = signature[3]
                command_ga = config.get("group_address")
                state_ga = config.get("state_group_address")
                if is_outgoing and ga == command_ga:
                    datapoint_ids.add(str(signature[0]))
                if not is_outgoing and ga == state_ga and state_ga != command_ga:
                    datapoint_ids.add(str(signature[0]))
        return datapoint_ids

    def _has_newer_matching_confirmation(
        self,
        datapoint_id: str,
        ga: str,
        raw: bytes,
        write_order: ConfirmationWriteOrder,
    ) -> bool:
        """Return whether this adapter can publish a newer matching state confirmation."""
        for (_, key_ga), recent_writes in self._recent_writes.items():
            if key_ga != ga:
                continue
            for recent_write in recent_writes:
                signature = recent_write[4]
                candidate_order = recent_write[5] if len(recent_write) > 5 else None
                config = signature[3]
                if (
                    str(signature[0]) == datapoint_id
                    and recent_write[1] == raw
                    and config.get("state_group_address") == ga
                    and config.get("group_address") != ga
                    and candidate_order is not None
                    and candidate_order.is_newer_than(write_order)
                ):
                    return True
        return False

    def _consume_outbound_confirmation(
        self,
        binding: Any,
        ga: str,
        raw: bytes,
        *,
        is_outgoing: bool,
        owned_write: tuple[Any, ...] | None = None,
    ) -> tuple[bool, Any, ConfirmationActionContext | bool, ConfirmationWriteOrder | None]:
        """Consume and return the logical value for one matching recent write."""
        self._prune_recent_writes()
        key = (str(binding.id), ga)
        recent_writes = self._recent_writes.get(key)
        if recent_writes is None:
            return False, None, False, None

        state_ga = binding.config.get("state_group_address")
        is_distinct_state_ga = state_ga == ga and state_ga != binding.config.get("group_address")
        if is_distinct_state_ga:
            if is_outgoing:
                return False, None, False, None
            matching_writes = [recent_write for recent_write in recent_writes if recent_write[1] == raw]
            if not matching_writes:
                return False, None, False, None

            ordered_writes = [recent_write for recent_write in matching_writes if len(recent_write) > 5 and recent_write[5] is not None]
            selected_write = matching_writes[-1]
            removal_target = matching_writes[0]
            if ordered_writes:
                selected_write = ordered_writes[0]
                for candidate in ordered_writes[1:]:
                    if candidate[5].is_newer_than(selected_write[5]):
                        selected_write = candidate
                removal_target = selected_write

            newest_logical_value = selected_write[2]
            action_context = selected_write[3]
            write_order = selected_write[5] if len(selected_write) > 5 else None
            for index, recent_write in enumerate(recent_writes):
                if recent_write is removal_target:
                    del recent_writes[index]
                    break
            if not recent_writes:
                self._recent_writes.pop(key, None)
            return True, newest_logical_value, action_context, write_order

        if not is_outgoing:
            return False, None, False, None
        for index, recent_write in enumerate(recent_writes):
            _, written_raw, logical_value, action_context, *_ = recent_write
            if (owned_write is not None and recent_write is not owned_write) or written_raw != raw:
                continue
            write_order = recent_write[5] if len(recent_write) > 5 else None
            del recent_writes[index]
            if not recent_writes:
                self._recent_writes.pop(key, None)
            return True, logical_value, action_context, write_order

        return False, None, False, None

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
        await self.write_with_context(binding, value, logical_value=value)

    async def write_with_context(
        self,
        binding: Any,
        value: Any,
        *,
        logical_value: Any,
        suppress_confirmation_actions: bool = False,
        confirmation_action_token: ConfirmationActionToken | None = None,
        confirmation_write_order: ConfirmationWriteOrder | None = None,
    ) -> bool:
        if not self._xknx:
            return False
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
            if binding.direction == "BOTH":
                self._pending_transmissions[id(telegram)] = (
                    binding,
                    bc.group_address,
                    bc.state_group_address,
                    bytes(raw),
                    logical_value,
                    ConfirmationActionContext(
                        suppress=suppress_confirmation_actions,
                        token=confirmation_action_token,
                        write_order=confirmation_write_order,
                    ),
                    self._confirmation_binding_signature(binding),
                )
                self._pending_telegram_refs[id(telegram)] = telegram
            try:
                await self._xknx.telegrams.put(telegram)
            except Exception:
                self._pending_transmissions.pop(id(telegram), None)
                self._pending_telegram_refs.pop(id(telegram), None)
                raise
            logger.info("KNX write: GA=%s value=%s raw=%s", bc.group_address, value, raw.hex())
            return binding.direction == "BOTH"
        except Exception:
            logger.exception("KNX write failed for binding %s", binding.id)
            return False


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
# Helpers
# ---------------------------------------------------------------------------


def _secure_config_from_keyfile(
    knxkeys_file_path: str,
    knxkeys_password: str,
    connection_type: str,
    individual_address: str,
) -> tuple[Any, str] | None:
    """Extract KNX IP Secure credentials from a .knxkeys file.

    Returns ``(SecureConfig, resolved_individual_address)`` or ``None``.

    Explicit credentials (user_id + user_password + device_authentication_password)
    make xknx take the "Branch A" code path in _start_secure_tunnelling_tcp, which
    does NOT call request_description() — the UDP step that fails in Docker bridge
    networks (Issue #393).

    The keyring object is ALSO passed in SecureConfig so that xknx can initialise
    Data Secure (data_secure_init) for gateways that use KNX Data Secure on top
    of the transport layer.  Without it, group-value telegrams from data-secure GAs
    would be undecryptable even though the tunnel connects successfully.

    The resolved_individual_address is the address of the actual keyfile interface used
    (may differ from the configured address when the fallback to the first tunnel fires).
    """
    from xknx.io import SecureConfig
    from xknx.secure.keyring import InterfaceType
    from xknx.telegram.address import IndividualAddress

    keyring = sync_load_keyring(knxkeys_file_path, knxkeys_password)  # type: ignore[misc]

    if connection_type == "tunneling_secure":
        xml_iface = keyring.get_tunnel_interface_by_individual_address(IndividualAddress(individual_address))
        if xml_iface is None:
            # Fallback: nimm das erste Tunneling-Interface
            tunnel_ifaces = [i for i in keyring.interfaces if i.type is InterfaceType.TUNNELING]
            if not tunnel_ifaces:
                return None
            xml_iface = tunnel_ifaces[0]
            logger.warning(
                "KNX IP Secure: individual_address %s nicht im Keyfile — verwende erstes Interface (%s)",
                individual_address,
                xml_iface.individual_address,
            )
        logger.info(
            "KNX IP Secure: Keyfile-Tunnel IA=%s user_id=%d",
            xml_iface.individual_address,
            xml_iface.user_id,
        )
        return (
            SecureConfig(
                device_authentication_password=xml_iface.decrypted_authentication or "",
                user_id=xml_iface.user_id,
                user_password=xml_iface.decrypted_password or "",
                keyring=keyring,
            ),
            str(xml_iface.individual_address),
        )

    # routing_secure: Backbone-Key extrahieren
    if keyring.backbone is None or keyring.backbone.decrypted_key is None:
        logger.error("KNX IP Secure (Routing): kein Backbone-Key im Keyfile")
        return None
    backbone_hex = keyring.backbone.decrypted_key.hex()
    logger.info("KNX IP Secure: Keyfile-Routing, Backbone-Key extrahiert (%d bytes)", len(keyring.backbone.decrypted_key))
    return (SecureConfig(backbone_key=backbone_hex, keyring=keyring), individual_address)


_DOCKER_BRIDGE_HINT = (
    " — Mögliche Ursache: Docker-Bridge-Netzwerk. xknx wählt die Container-IP "
    "statt der Host-LAN-IP; UDP-Anfragen für den Verbindungsaufbau kommen nicht "
    "zurück. Lösung: 'network_mode: host' in docker-compose.yml setzen."
)

_NO_MORE_CONNECTIONS_HINT = (
    " — Alle Tunnel-Verbindungsplätze des Gateways sind belegt. "
    "Mögliche Ursachen: ETS, TWS oder ein anderer Client hält einen Tunnel offen; "
    "oder eine vorherige Verbindung wurde vom Gateway noch nicht freigegeben. "
    "Andere KNX-Clients trennen oder das Gateway kurz neu starten."
)

_GATEWAY_UNREACHABLE_KEYWORDS = (
    "could not fetch gateway info",
    "did not respond in time",
    "descriptionquery",
)


def _knx_connect_error_detail(exc: Exception, connection_type: str = "") -> str:
    """Convert an xknx connection exception to a user-friendly German detail string.

    Includes the underlying cause (exc.__cause__) so the GUI shows the real
    error (e.g. "ConnectRequest failed. Status code: ErrorCode.E_NO_MORE_CONNECTIONS")
    rather than only the generic wrapper "Tunnel connection could not be established".

    Also detects known failure patterns and appends actionable hints.
    """
    msg = str(exc)
    # Include the real underlying cause when available
    cause = exc.__cause__
    cause_msg = f" ({cause})" if cause and str(cause) != msg else ""
    full_msg = msg + cause_msg

    combined = full_msg.lower()
    if "e_no_more_connections" in combined:
        return full_msg + _NO_MORE_CONNECTIONS_HINT
    if any(kw in combined for kw in _GATEWAY_UNREACHABLE_KEYWORDS):
        return full_msg + _DOCKER_BRIDGE_HINT
    return full_msg


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
