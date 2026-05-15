"""
ioBroker Adapter.

Verbindet open bridge server mit einer ioBroker-Instanz über Socket.IO.
Der ioBroker socket.io/web Adapter stellt Methoden wie getState, setState und
subscribe bereit; abonnierte Änderungen kommen als stateChange-Ereignisse.

Adapter-Konfiguration (adapter_instances.config in DB):
  host:          str    — Hostname/IP der ioBroker-Instanz
  port:          int    — Socket.IO/Web Adapter Port (häufig 8084)
  username:      str?   — optional
  password:      str?   — optional
  ssl:           bool   — HTTPS verwenden
  path:          str    — Socket.IO Pfad
  access_token:  str?   — optionaler Bearer/OAuth Token

Binding-Konfiguration (pro AdapterBinding.config):
  state_id:         str   — ioBroker-State-ID, z.B. "0_userdata.0.foo"
  command_state_id: str?  — optional abweichender State für Schreibbefehle
  ack:              bool  — ack-Flag beim Schreiben
  source_data_type: str?  — "string"|"int"|"float"|"bool"|"json"|None(=auto)
  json_key:         str?  — Schlüssel für JSON-Werte

Richtungs-Semantik:
  SOURCE  → State abonnieren, stateChange liefert Werte ins System
  DEST    → State bei write() via setState setzen
  BOTH    → Beides
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent
from obs.core.transformation import apply_source_type, apply_value_map

logger = logging.getLogger(__name__)


class _EngineIOQueueFilter(logging.Filter):
    """Suppresses the spurious 'packet queue is empty' ERROR from python-engineio.

    When the server stops sending, the read loop times out and puts None into the
    packet queue as a disconnect signal.  The write loop receives None, clears the
    queue, but then loops back and waits again instead of exiting — after another
    timeout it logs an ERROR 'packet queue is empty'.  This is an internal
    clean-up artifact, not a real application error.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "packet queue is empty" not in record.getMessage()


class IoBrokerAdapterConfig(BaseModel):
    host: str = "iobroker.local"
    port: int = 8084
    username: str | None = None
    password: str | None = Field(default=None, json_schema_extra={"format": "password"})
    ssl: bool = False
    path: str = "/socket.io"
    access_token: str | None = Field(default=None, json_schema_extra={"format": "password"})
    resubscribe_interval_seconds: int = Field(default=60, ge=0)
    reconnect_interval_seconds: int = Field(default=5, ge=1)


class IoBrokerBindingConfig(BaseModel):
    state_id: str
    command_state_id: str | None = None
    ack: bool = False
    source_data_type: str | None = None
    json_key: str | None = None


class IoBrokerStateInfo(BaseModel):
    id: str
    name: str | None = None
    type: str | None = None
    role: str | None = None
    read: bool = True
    write: bool = False
    value: Any = None
    unit: str | None = None


class IoBrokerEnsureStateRequest(BaseModel):
    state_id: str
    data_type: str = "string"
    name: str | None = None
    role: str | None = None
    read: bool = True
    write: bool = True
    unit: str | None = None
    initial_value: Any = None


def _coerce_iobroker_value(value: Any) -> Any:
    """Convert common ioBroker/simple-api scalar strings to Python values."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    lowered = raw.lower()
    if lowered in ("true", "on", "yes"):
        return True
    if lowered in ("false", "off", "no"):
        return False
    if lowered in ("null", "none", "undefined", "nan"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        return json.loads(raw)
    except Exception:
        return value


@register
class IoBrokerAdapter(AdapterBase):
    adapter_type = "IOBROKER"
    config_schema = IoBrokerAdapterConfig
    binding_config_schema = IoBrokerBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg: IoBrokerAdapterConfig | None = None
        self._socket: Any | None = None
        self._state_map: dict[str, list[Any]] = {}
        self._last_source_values: dict[str, Any] = {}
        self._source_filter_last_sent: dict[str, float] = {}
        self._source_filter_last_values: dict[str, Any] = {}
        self._subscription_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._subscription_watchdog_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._disconnect_requested = False
        self._socketio: Any | None = None
        self._connect_url: str | None = None
        self._connect_kwargs: dict[str, Any] = {}

    def _socket_is_connected(self) -> bool:
        if self._socket is None:
            return False
        if getattr(self._socket, "connected", False):
            return True
        engineio_client = getattr(self._socket, "eio", None)
        return getattr(engineio_client, "state", None) == "connected"

    @property
    def connected(self) -> bool:
        if self._socket_is_connected():
            return True
        return self._connected

    async def connect(self) -> None:
        try:
            import socketio
        except ImportError:
            logger.error("python-socketio not installed — ioBroker adapter disabled")
            await self._publish_status(False, "python-socketio nicht installiert")
            return
        for noisy_logger in (
            "socketio",
            "socketio.client",
            "engineio",
            "engineio.client",
        ):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)
        logging.getLogger("engineio.client").addFilter(_EngineIOQueueFilter())

        self._socketio = socketio
        self._cfg = IoBrokerAdapterConfig(**self._config)
        self._disconnect_requested = False
        scheme = "https" if self._cfg.ssl else "http"
        url = f"{scheme}://{self._cfg.host}:{self._cfg.port}"
        self._connect_url = url
        headers = {}
        if self._cfg.username and self._cfg.password:
            raw = f"{self._cfg.username}:{self._cfg.password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()

        auth_payload: dict[str, Any] | None = None
        if self._cfg.access_token:
            headers["Authorization"] = f"Bearer {self._cfg.access_token}"
            auth_payload = {
                "token": self._cfg.access_token,
                "accessToken": self._cfg.access_token,
            }

        connect_kwargs = {
            "socketio_path": self._cfg.path.strip("/"),
        }
        if headers:
            connect_kwargs["headers"] = headers
        if auth_payload:
            connect_kwargs["auth"] = auth_payload
        self._connect_kwargs = connect_kwargs

        connected = await self._connect_socket()
        if not connected:
            await self._publish_status(False, "Socket.IO Verbindung fehlgeschlagen")
            self._ensure_reconnect_task()

        logger.info(
            "ioBroker adapter started → %s:%d path=%s",
            self._cfg.host,
            self._cfg.port,
            self._cfg.path,
        )
        self._start_subscription_watchdog()

    async def disconnect(self) -> None:
        self._disconnect_requested = True
        await self._stop_subscription_watchdog()
        await self._stop_reconnect_task()
        if self._socket:
            try:
                await self._socket.disconnect()
            except Exception:
                logger.exception("ioBroker Socket.IO disconnect failed")
            self._socket = None

        self._state_map.clear()
        self._last_source_values.clear()
        self._source_filter_last_sent.clear()
        self._source_filter_last_values.clear()
        await self._publish_status(False, "Getrennt")

    async def _on_bindings_reloaded(self) -> None:
        if self._cfg is None:
            return

        self._state_map.clear()
        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                bc = IoBrokerBindingConfig(**binding.config)
            except Exception:
                logger.warning(
                    "Ungültige ioBroker Binding-Konfiguration für %s — übersprungen",
                    binding.id,
                )
                continue
            self._state_map.setdefault(bc.state_id, []).append(binding)

        logger.info(
            "ioBroker adapter: %d state subscription(s): %s",
            len(self._state_map),
            list(self._state_map.keys()),
        )

        if self._state_map:
            await self._subscribe_bound_states(force_publish_initial=True)

    def _start_subscription_watchdog(self) -> None:
        if self._subscription_watchdog_task and not self._subscription_watchdog_task.done():
            return
        if self._cfg is None or self._cfg.resubscribe_interval_seconds <= 0:
            return
        self._subscription_watchdog_task = asyncio.create_task(self._subscription_watchdog())

    async def _stop_subscription_watchdog(self) -> None:
        task = self._subscription_watchdog_task
        self._subscription_watchdog_task = None
        if not task:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _ensure_reconnect_task(self) -> None:
        if self._disconnect_requested:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _stop_reconnect_task(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        if not task:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _subscription_watchdog(self) -> None:
        assert self._cfg is not None
        interval = self._cfg.resubscribe_interval_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                await self._subscribe_bound_states(force_publish_initial=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ioBroker subscription watchdog failed")

    def _build_socket(self) -> Any:
        assert self._socketio is not None
        # reconnection=False: OBS owns the full reconnect cycle via _reconnect_loop.
        # Leaving reconnection=True would create a second parallel reconnect mechanism
        # that races with _reconnect_loop and can produce double-connect attempts.
        sio = self._socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        self._register_socket_handlers(sio)
        return sio

    def _register_socket_handlers(self, sio: Any) -> None:
        @sio.event
        async def connect():  # noqa: ANN202
            if self._socket is not sio:
                return
            logger.info("ioBroker Socket.IO connected → %s", self._connect_url)
            if self._cfg is not None:
                await self._publish_status(True, f"Verbunden mit {self._cfg.host}:{self._cfg.port}")
            await self._subscribe_bound_states(force_publish_initial=True)

        @sio.event
        async def disconnect():  # noqa: ANN202
            if self._socket is not sio:
                return
            logger.info("ioBroker Socket.IO disconnected")
            await self._publish_status(False, "Socket.IO getrennt")
            self._ensure_reconnect_task()

        @sio.on("stateChange")
        async def state_change(*args):  # noqa: ANN202
            if self._socket is not sio:
                return
            await self._on_state_change_event(*args)

    async def _connect_socket(self) -> bool:
        if self._cfg is None or self._connect_url is None:
            return False
        async with self._connect_lock:
            if self._disconnect_requested:
                return False
            sio = self._build_socket()
            self._socket = sio
            try:
                await self._connect_with_kwargs(sio, self._connect_kwargs)
                return True
            except Exception as exc:
                if self._should_retry_with_websocket(exc):
                    logger.warning("ioBroker Socket.IO polling handshake failed; retrying with websocket transport")
                    fallback_kwargs = dict(self._connect_kwargs)
                    fallback_kwargs["transports"] = ["websocket"]
                    fallback_sio = self._build_socket()
                    self._socket = fallback_sio
                    try:
                        await self._connect_with_kwargs(fallback_sio, fallback_kwargs)
                        self._connect_kwargs = fallback_kwargs
                        return True
                    except Exception:
                        if self._socket is fallback_sio:
                            self._socket = None
                        logger.exception("ioBroker Socket.IO websocket fallback failed")
                        return False
                if self._socket is sio:
                    self._socket = None
                logger.exception("ioBroker Socket.IO connection failed")
                return False

    async def _connect_with_kwargs(self, sio: Any, connect_kwargs: dict[str, Any]) -> None:
        assert self._connect_url is not None
        try:
            await sio.connect(self._connect_url, wait_timeout=10, **connect_kwargs)
        except TypeError:
            v4_kwargs = dict(connect_kwargs)
            v4_kwargs.pop("auth", None)
            await sio.connect(self._connect_url, **v4_kwargs)

    @staticmethod
    def _should_retry_with_websocket(exc: Exception) -> bool:
        return "OPEN packet not returned by server" in str(exc)

    async def _reconnect_loop(self) -> None:
        assert self._cfg is not None
        while not self._disconnect_requested:
            if self._socket_is_connected():
                return
            connected = await self._connect_socket()
            if connected:
                return
            await asyncio.sleep(self._cfg.reconnect_interval_seconds)

    async def _subscribe_bound_states(self, *, force_publish_initial: bool = True) -> bool:
        if not self._socket_is_connected() or not self._state_map:
            return self._socket_is_connected()

        async with self._subscription_lock:
            states = list(self._state_map.keys())
            try:
                await self._call_socket("subscribe", states)
                logger.info("ioBroker Socket.IO subscribed: %s", states)
                if self._cfg is not None:
                    await self._publish_status(True, f"Verbunden mit {self._cfg.host}:{self._cfg.port}")
            except Exception:
                logger.exception("ioBroker Socket.IO subscribe failed")
                await self._publish_status(False, "Subscribe fehlgeschlagen")
                return False

            # subscribe only reports future changes. After connect/reconnect we
            # publish initial values; watchdog resync only publishes drift so it
            # can heal stale subscriptions without creating repeated writes.
            for bindings in list(self._state_map.values()):
                for binding in bindings:
                    value = await self.read(binding)
                    await self._publish_binding_value(
                        binding,
                        value,
                        force=force_publish_initial,
                        apply_filters=False,
                    )
        return True

    async def _call_socket(self, event: str, *args: Any, timeout: float = 10.0) -> Any:
        if not self._socket or not self._socket_is_connected():
            raise RuntimeError("ioBroker Socket.IO is not connected")
        if not args:
            data = None
        elif len(args) == 1:
            data = args[0]
        else:
            data = tuple(args)
        result = await self._socket.call(event, data, timeout=timeout)
        if isinstance(result, (list, tuple)):
            if len(result) >= 2:
                err, value = result[0], result[1]
                if err:
                    raise RuntimeError(str(err))
                return value
            if len(result) == 1:
                return result[0]
        return result

    async def browse_states(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        q = query.strip()
        max_results = min(max(limit, 1), 500)
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()

        # ioBroker object IDs are tree-like. A query such as "hue" should first
        # browse the "hue." namespace instead of returning unrelated full-text
        # hits such as "system.adapter.hue.0.*".
        prefixes: list[str] = []
        if q:
            prefixes.append(q if "." in q else f"{q}.")

        for prefix in prefixes:
            for item in await self._browse_state_rows(q, max_results, prefix=prefix):
                if item["id"] in seen:
                    continue
                matches.append(item)
                seen.add(item["id"])
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        if len(matches) < max_results:
            for item in await self._browse_state_rows(q, max_results, prefix=""):
                if item["id"] in seen:
                    continue
                matches.append(item)
                seen.add(item["id"])
                if len(matches) >= max_results:
                    break

        await self._add_current_values(matches)
        return matches

    async def ensure_state(self, request: IoBrokerEnsureStateRequest | dict[str, Any]) -> None:
        """Create/update an ioBroker state object if the socket.io adapter exposes setObject.

        This is used by the HomeKit/Yahka import path for 0_userdata.0.obs.home.*
        states. If the object already exists, setObject updates common metadata
        while preserving runtime state value in ioBroker.
        """
        req = request if isinstance(request, IoBrokerEnsureStateRequest) else IoBrokerEnsureStateRequest(**request)
        common: dict[str, Any] = {
            "name": req.name or req.state_id.split(".")[-1],
            "type": self._iobroker_common_type(req.data_type),
            "role": req.role or self._iobroker_default_role(req.data_type),
            "read": req.read,
            "write": req.write,
        }
        if req.unit:
            common["unit"] = req.unit
        obj = {
            "type": "state",
            "common": common,
            "native": {},
        }
        await self._call_socket("setObject", req.state_id, obj, timeout=8.0)
        if req.initial_value is not None:
            await self._call_socket("setState", req.state_id, {"val": req.initial_value, "ack": True})

    @staticmethod
    def _iobroker_common_type(data_type: str) -> str:
        return {
            "BOOLEAN": "boolean",
            "boolean": "boolean",
            "bool": "boolean",
            "FLOAT": "number",
            "INTEGER": "number",
            "number": "number",
            "STRING": "string",
            "string": "string",
        }.get(data_type, "string")

    @staticmethod
    def _iobroker_default_role(data_type: str) -> str:
        return {
            "BOOLEAN": "switch",
            "boolean": "switch",
            "bool": "switch",
            "FLOAT": "value",
            "INTEGER": "value",
            "number": "value",
        }.get(data_type, "state")

    async def _browse_state_rows(self, query: str, max_results: int, prefix: str = "") -> list[dict[str, Any]]:
        fetch_limit = max_results if prefix else min(max_results * 10, 500)
        options: dict[str, Any] = {
            "startkey": prefix,
            "endkey": f"{prefix}\uffff" if prefix else "\uffff",
            "limit": fetch_limit,
        }
        result = await self._call_socket("getObjectView", "system", "state", options, timeout=12.0)
        rows = result.get("rows", []) if isinstance(result, dict) else []
        matches: list[dict[str, Any]] = []
        q_lower = query.lower()
        for row in rows:
            item = self._state_row_to_info(row)
            haystack = " ".join(
                str(part or "")
                for part in (
                    item["id"],
                    item.get("name"),
                    item.get("role"),
                    item.get("type"),
                )
            ).lower()
            if q_lower and q_lower not in haystack:
                continue
            matches.append(item)
            if len(matches) >= max_results:
                break
        return matches

    async def _add_current_values(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            try:
                state = await self._call_socket("getState", item["id"], timeout=4.0)
                item["value"] = self._extract_state_value(state)
            except Exception:
                item["value"] = None

    @staticmethod
    def _state_row_to_info(row: Any) -> dict[str, Any]:
        state_id = str(row.get("id") or "")
        value = row.get("value") if isinstance(row, dict) else {}
        common = value.get("common", {}) if isinstance(value, dict) else {}
        name = common.get("name")
        if isinstance(name, dict):
            name = name.get("de") or name.get("en") or next(iter(name.values()), None)
        return IoBrokerStateInfo(
            id=state_id,
            name=str(name) if name else None,
            type=common.get("type"),
            role=common.get("role"),
            read=bool(common.get("read", True)),
            write=bool(common.get("write", False)),
            unit=common.get("unit"),
        ).model_dump()

    async def _on_state_change_event(self, *args: Any) -> None:
        if len(args) < 2:
            return
        state_id = str(args[0])
        state = args[1]
        entries = self._state_map.get(state_id)
        if not entries:
            return
        value = self._extract_state_value(state)
        for binding in entries:
            await self._publish_binding_value(binding, value, force=True, apply_filters=True)

    async def _publish_binding_value(
        self,
        binding: Any,
        value: Any,
        *,
        force: bool = True,
        apply_filters: bool = True,
    ) -> None:
        try:
            bc = IoBrokerBindingConfig(**binding.config)
            raw = value if isinstance(value, str) else json.dumps(value)
            auto_value = _coerce_iobroker_value(value)
            pub_value = apply_source_type(
                raw,
                auto_value,
                bc.source_data_type,
                bc.json_key,
                None,
                binding.id,
            )
            pub_value = apply_value_map(pub_value, binding.value_map)
            if binding.value_formula and pub_value is not None:
                from obs.core.formula import apply_formula

                pub_value = apply_formula(binding.value_formula, pub_value)
        except Exception:
            logger.exception("ioBroker adapter: error processing binding %s", binding.id)
            return

        last_key = str(binding.id)
        if not force and self._last_source_values.get(last_key) == pub_value:
            logger.debug(
                "ioBroker adapter state unchanged: state_id=%s value=%r",
                bc.state_id,
                pub_value,
            )
            return
        if apply_filters and not self._source_filters_allow(binding, pub_value, bc.state_id):
            return
        self._last_source_values[last_key] = pub_value
        self._source_filter_last_sent[last_key] = time.monotonic()
        self._source_filter_last_values[last_key] = pub_value

        logger.info(
            "ioBroker adapter state: state_id=%s → dp=%s value=%r",
            bc.state_id,
            binding.datapoint_id,
            pub_value,
        )
        await self._bus.publish(
            DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=pub_value,
                quality="good",
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            )
        )

    def _source_filters_allow(self, binding: Any, value: Any, state_id: str) -> bool:
        """Apply binding filters to incoming ioBroker source events.

        Startup reads and watchdog resyncs bypass these filters so OBS can
        restore the latest ioBroker state reliably after reconnects.
        """
        key = str(binding.id)

        if binding.send_throttle_ms:
            min_interval = binding.send_throttle_ms / 1000.0
            last_ts = self._source_filter_last_sent.get(key)
            if last_ts is not None and (time.monotonic() - last_ts) < min_interval:
                logger.debug(
                    "ioBroker adapter source throttle: state_id=%s binding=%s value=%r",
                    state_id,
                    binding.id,
                    value,
                )
                return False

        last_val = self._source_filter_last_values.get(key)
        if last_val is None:
            return True

        if binding.send_on_change and value == last_val:
            logger.debug(
                "ioBroker adapter source on-change: state_id=%s binding=%s unchanged=%r",
                state_id,
                binding.id,
                value,
            )
            return False

        if binding.send_min_delta is not None or binding.send_min_delta_pct is not None:
            try:
                v_new = float(value)
                v_last = float(last_val)
                diff = abs(v_new - v_last)

                if binding.send_min_delta is not None and diff < binding.send_min_delta:
                    logger.debug(
                        "ioBroker adapter source min_delta: state_id=%s binding=%s diff=%.4f min=%.4f",
                        state_id,
                        binding.id,
                        diff,
                        binding.send_min_delta,
                    )
                    return False

                if binding.send_min_delta_pct is not None:
                    base = abs(v_last) if v_last != 0 else abs(v_new)
                    pct = (diff / base * 100) if base != 0 else 0.0
                    if pct < binding.send_min_delta_pct:
                        logger.debug(
                            "ioBroker adapter source min_delta_pct: state_id=%s binding=%s %.2f%% < %.2f%%",
                            state_id,
                            binding.id,
                            pct,
                            binding.send_min_delta_pct,
                        )
                        return False
            except (TypeError, ValueError):
                pass

        return True

    async def read(self, binding: Any) -> Any:
        if self._socket is None:
            return None
        try:
            bc = IoBrokerBindingConfig(**binding.config)
            state = await self._call_socket("getState", bc.state_id)
            return self._extract_state_value(state)
        except Exception:
            logger.exception("ioBroker adapter read failed for binding %s", binding.id)
            return None

    @staticmethod
    def _extract_state_value(state: Any) -> Any:
        if isinstance(state, dict):
            if "val" in state:
                return state.get("val")
            if "value" in state:
                return state.get("value")
            if "state" in state:
                inner = state.get("state")
                if isinstance(inner, dict) and "val" in inner:
                    return inner.get("val")
                return _coerce_iobroker_value(inner)
        return _coerce_iobroker_value(state)

    async def write(self, binding: Any, value: Any) -> None:
        if self._socket is None:
            logger.warning("ioBroker adapter write called but Socket.IO client not initialized")
            return
        try:
            bc = IoBrokerBindingConfig(**binding.config)
            mapped = apply_value_map(value, binding.value_map)
            state_id = bc.command_state_id or bc.state_id
            await self._call_socket("setState", state_id, {"val": mapped, "ack": bc.ack})
            logger.info(
                "ioBroker adapter write: state=%s value=%r ack=%s",
                state_id,
                mapped,
                bc.ack,
            )
        except Exception:
            logger.exception("ioBroker adapter write failed for binding %s", binding.id)
