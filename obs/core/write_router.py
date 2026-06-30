"""Write Router — Phase 4 / Phase 5 (Multi-Instance)

Two write paths:

1. MQTT dp/{uuid}/set  →  handle(dp_id, raw_payload)
   External write command: deserialize payload, write to all DEST/BOTH bindings.

2. DataValueEvent  →  handle_value_event(event)
   Internal propagation: a SOURCE/BOTH binding received a value → write it to all
   DEST/BOTH bindings of the same DataPoint (excluding the originating binding to
   prevent loopback). This implements cross-protocol bridging, e.g.:
     KNX GA 27/6/6 (SOURCE) → DataPoint → KNX GA 6/7/15 (DEST)

Phase 5: Adapter-Lookup erfolgt per adapter_instance_id (UUID), nicht mehr per Typ-String.
Fallback auf Typ-String für Bindings ohne instance_id (Rückwärtskompatibilität).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

_MAX_CACHED_VALUE_CHARS = 8192
_CACHE_TAG_STR_DIGEST = "__str_digest__"
_CACHE_TAG_REPR_DIGEST = "__repr_digest__"

logger = logging.getLogger(__name__)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _to_cached_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _MAX_CACHED_VALUE_CHARS:
            return value
        return (_CACHE_TAG_STR_DIGEST, len(value), _sha256(value))

    rendered = repr(value)
    if len(rendered) > _MAX_CACHED_VALUE_CHARS:
        # Keep only a compact fingerprint for large objects to bound cache memory.
        return (_CACHE_TAG_REPR_DIGEST, len(rendered), _sha256(rendered))
    return value


def _cached_value_equals(current_value: Any, cached_value: Any) -> bool:
    if isinstance(cached_value, tuple) and len(cached_value) == 3 and cached_value[0] in {_CACHE_TAG_STR_DIGEST, _CACHE_TAG_REPR_DIGEST}:
        tag, expected_len, expected_hash = cached_value
        if tag == _CACHE_TAG_STR_DIGEST:
            if not isinstance(current_value, str):
                return False
            return len(current_value) == expected_len and _sha256(current_value) == expected_hash

        current_repr = repr(current_value)
        return len(current_repr) == expected_len and _sha256(current_repr) == expected_hash

    return current_value == cached_value


def _unwrap_mqtt_set_payload(raw_payload: str) -> tuple[Any, bool]:
    try:
        payload = json.loads(raw_payload)
    except Exception:
        return raw_payload, False

    if isinstance(payload, dict) and "v" in payload:
        return payload["v"], True
    return payload, True


def _coerce_mqtt_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in {0, 1}:
            return bool(value)
        raise ValueError(f"Invalid boolean numeric value: {value!r}")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _coerce_mqtt_integer(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and not isinstance(value, bool) and value == int(value):
        return int(value)
    raise ValueError(f"Invalid integer value: {value!r}")


def _coerce_mqtt_float(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"Invalid float value: {value!r}")


def _deserialize_typed_mqtt_set_value(dt: Any, raw_payload: str, payload_value: Any, payload_was_json: bool) -> Any:
    if dt.name == "BOOLEAN":
        value = payload_value if payload_was_json else raw_payload
        return _coerce_mqtt_boolean(value)
    if dt.name == "INTEGER":
        value = payload_value if payload_was_json else json.loads(raw_payload)
        return _coerce_mqtt_integer(value)
    if dt.name == "FLOAT":
        value = payload_value if payload_was_json else json.loads(raw_payload)
        return _coerce_mqtt_float(value)
    if dt.name == "STRING" and not payload_was_json:
        return raw_payload
    if dt.name in {"DATE", "TIME", "DATETIME"} and not payload_was_json:
        return dt.mqtt_deserializer(json.dumps(raw_payload))
    return dt.mqtt_deserializer(json.dumps(payload_value) if payload_was_json else raw_payload)


def _row_is_enabled(row: Any) -> bool:
    return _row_value(row, "enabled") in {1, True, "1"}


class WriteRouter:
    def __init__(self, db: Any, registry: Any, event_bus: Any | None = None) -> None:
        from obs.core.registry import DataPointRegistry
        from obs.db.database import Database

        self._db: Database = db
        self._registry: DataPointRegistry = registry
        self._bus = event_bus
        # binding_id → timestamp of last successful send (monotonic seconds)
        self._last_sent: dict[uuid.UUID, float] = {}
        # binding_id → last successfully sent value (for on-change / delta checks)
        self._last_value: dict[uuid.UUID, Any] = {}

    # ------------------------------------------------------------------
    # Path 1 — inbound MQTT dp/{uuid}/set
    # ------------------------------------------------------------------

    async def handle(self, dp_id: uuid.UUID, raw_payload: str) -> None:
        """Deserialize an inbound MQTT set payload.

        Bound datapoints keep the adapter write semantics: only DEST/BOTH
        bindings receive commands, and registry state changes only when an
        adapter later publishes a value event. Bindingless datapoints are OBS
        internal objects, so their validated set payload becomes the value event.
        """
        from obs.models.types import DataTypeRegistry

        logger.info("WriteRouter.handle: dp_id=%s payload=%r", dp_id, raw_payload)
        dp = self._registry.get(dp_id)
        if dp is None:
            logger.warning("Write request for unknown DataPoint %s — ignored", dp_id)
            return

        rows = await self._db.fetchall(
            """SELECT direction, enabled, adapter_type FROM adapter_bindings
               WHERE datapoint_id=?""",
            (str(dp_id),),
        )
        active_rows = [row for row in rows if _row_is_enabled(row)]
        write_semantic_rows = [row for row in active_rows if _row_value(row, "adapter_type") != "MESSAGE"]
        has_write_semantic_bindings = bool(write_semantic_rows)
        has_writable_bindings = any(_row_value(row, "direction") in {"DEST", "BOTH"} for row in write_semantic_rows)
        if has_write_semantic_bindings and not has_writable_bindings:
            logger.warning("Write request for non-writable DataPoint %s — ignored", dp_id)
            return

        dt = DataTypeRegistry.get(dp.data_type)
        payload_value, payload_was_json = _unwrap_mqtt_set_payload(raw_payload)
        if dt.name == "UNKNOWN":
            value = payload_value if payload_was_json else raw_payload
        else:
            try:
                value = _deserialize_typed_mqtt_set_value(dt, raw_payload, payload_value, payload_was_json)
            except Exception:
                logger.warning(
                    "WriteRouter: invalid MQTT set payload for dp=%s data_type=%s payload=%r",
                    dp_id,
                    dp.data_type,
                    raw_payload,
                )
                return
        logger.info("WriteRouter: dp=%s value=%r (type=%s)", dp.name, value, dp.data_type)

        if has_writable_bindings:
            await self._write_to_dest_bindings(dp_id, value, skip_binding_id=None)
            return

        from obs.core.event_bus import DataValueEvent, get_event_bus

        bus = getattr(self, "_bus", None) or get_event_bus()
        await bus.publish(
            DataValueEvent(
                datapoint_id=dp_id,
                value=value,
                quality="good",
                source_adapter="mqtt_set",
            )
        )

    # ------------------------------------------------------------------
    # Path 2 — internal DataValueEvent propagation
    # ------------------------------------------------------------------

    async def handle_value_event(self, event: Any) -> None:
        """Propagate a DataValueEvent to all DEST/BOTH bindings of the same DataPoint.

        Called by the EventBus whenever a SOURCE/BOTH binding delivers a new value.
        The originating binding (event.binding_id) is skipped to avoid loopback.
        """
        logger.info(
            "WriteRouter.handle_value_event: dp=%s value=%r source_binding=%s",
            event.datapoint_id,
            event.value,
            event.binding_id,
        )

        if event.quality != "good" or event.value is None:
            logger.warning(
                "WriteRouter: skip DataValueEvent propagation for dp=%s due to quality=%s value=%r",
                event.datapoint_id,
                event.quality,
                event.value,
            )
            return

        dp = self._registry.get(event.datapoint_id)
        if dp is None:
            logger.warning("DataValueEvent for unknown DataPoint %s — ignored", event.datapoint_id)
            return

        from obs.models.types import DataTypeRegistry

        dt = DataTypeRegistry.get(dp.data_type)
        value = event.value
        if dt.name != "UNKNOWN" and not isinstance(value, dt.python_type):
            allow_float_numeric = dt.name == "FLOAT" and isinstance(value, int | float) and not isinstance(value, bool)
            if not allow_float_numeric:
                logger.warning(
                    "WriteRouter: skip DataValueEvent for dp=%s due to type mismatch (expected=%s got=%s)",
                    event.datapoint_id,
                    dt.python_type.__name__,
                    type(value).__name__,
                )
                await self._report_type_mismatch(event, dt.python_type.__name__, type(value).__name__)
                return

        await self._clear_type_mismatch(event.datapoint_id)
        await self._write_to_dest_bindings(event.datapoint_id, value, skip_binding_id=event.binding_id)

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    async def _write_to_dest_bindings(
        self,
        dp_id: uuid.UUID,
        value: Any,
        skip_binding_id: uuid.UUID | None,
    ) -> None:
        from obs.adapters import registry as adapter_registry
        from obs.adapters.registry import _row_to_binding

        rows = await self._db.fetchall(
            """SELECT * FROM adapter_bindings
               WHERE datapoint_id=? AND direction IN ('DEST','BOTH') AND enabled=1 AND adapter_type <> 'MESSAGE'""",
            (str(dp_id),),
        )
        if not rows:
            logger.debug("No writable bindings for DataPoint %s", dp_id)
            return

        logger.info("WriteRouter: %d writable binding(s) for dp %s", len(rows), dp_id)
        for row in rows:
            try:
                binding = _row_to_binding(row)
            except Exception as exc:
                logger.error(
                    "WriteRouter: invalid writable binding skipped for dp=%s binding=%s: %s",
                    dp_id,
                    _row_value(row, "id") or "<unknown>",
                    exc,
                )
                continue
            if skip_binding_id and binding.id == skip_binding_id:
                logger.debug("WriteRouter: skipping originating binding %s", binding.id)
                continue
            if binding.adapter_type == "MESSAGE":
                logger.debug("WriteRouter: skipping MESSAGE observer binding %s", binding.id)
                continue

            # Phase 5: Lookup per Instance-ID (bevorzugt), Fallback auf Typ
            instance = None
            if binding.adapter_instance_id:
                instance = adapter_registry.get_instance_by_id(binding.adapter_instance_id)
            if instance is None:
                instance = adapter_registry.get_instance(binding.adapter_type)

            if instance is None:
                logger.warning(
                    "Adapter-Instanz nicht gefunden — write für binding %s übersprungen (type=%s, instance_id=%s)",
                    binding.id,
                    binding.adapter_type,
                    binding.adapter_instance_id,
                )
                continue
            # --- Filter 1: Send-Throttle ---
            if binding.send_throttle_ms:
                min_interval = binding.send_throttle_ms / 1000.0
                last_ts = self._last_sent.get(binding.id)
                if last_ts is not None and (time.monotonic() - last_ts) < min_interval:
                    logger.debug(
                        "WriteRouter: throttle — skipping binding %s (min=%.3fs elapsed=%.3fs)",
                        binding.id,
                        min_interval,
                        time.monotonic() - last_ts,
                    )
                    continue

            # --- Filter 2 & 3: Wert-basierte Filter (nur wenn Vorgänger bekannt) ---
            last_val = self._last_value.get(binding.id)
            if last_val is not None:
                # Filter 2: Nur bei Änderung
                if binding.send_on_change and _cached_value_equals(value, last_val):
                    logger.debug(
                        "WriteRouter: on-change — skipping binding %s (value unchanged: %r)",
                        binding.id,
                        value,
                    )
                    continue

                # Filter 3: Mindest-Abweichung (abs./rel.) — nur für numerische Werte
                if binding.send_min_delta is not None or binding.send_min_delta_pct is not None:
                    try:
                        v_new = float(value)
                        v_last = float(last_val)
                        diff = abs(v_new - v_last)

                        if binding.send_min_delta is not None and diff < binding.send_min_delta:
                            logger.debug(
                                "WriteRouter: min_delta — skipping binding %s (diff=%.4f < min=%.4f)",
                                binding.id,
                                diff,
                                binding.send_min_delta,
                            )
                            continue

                        if binding.send_min_delta_pct is not None:
                            base = abs(v_last) if v_last != 0 else abs(v_new)
                            pct = (diff / base * 100) if base != 0 else 0.0
                            if pct < binding.send_min_delta_pct:
                                logger.debug(
                                    "WriteRouter: min_delta_pct — skipping binding %s (%.2f%% < %.2f%%)",
                                    binding.id,
                                    pct,
                                    binding.send_min_delta_pct,
                                )
                                continue
                    except (TypeError, ValueError):
                        pass  # Nicht-numerische Werte: Delta-Filter ignorieren

            # --- DEST-Transformation: Formel dann value_map ---
            write_value = value
            if binding.value_formula:
                from obs.core.formula import apply_formula

                write_value = apply_formula(binding.value_formula, write_value)
                logger.debug(
                    "WriteRouter: DEST formula '%s' applied: %r → %r",
                    binding.value_formula,
                    value,
                    write_value,
                )
            if binding.value_map:
                from obs.core.transformation import apply_value_map

                write_value = apply_value_map(write_value, binding.value_map)
                logger.debug(
                    "WriteRouter: DEST value_map applied: %r → %r",
                    value,
                    write_value,
                )

            try:
                await instance.write(binding, write_value)
                self._last_sent[binding.id] = time.monotonic()

                needs_value_cache = binding.send_on_change or binding.send_min_delta is not None or binding.send_min_delta_pct is not None
                if needs_value_cache:
                    cache_value = _to_cached_value(value)
                    self._last_value[binding.id] = cache_value  # Original für Delta/OnChange
                else:
                    self._last_value.pop(binding.id, None)
                logger.info(
                    "WriteRouter: wrote to adapter=%s instance=%s binding=%s value=%r",
                    binding.adapter_type,
                    binding.adapter_instance_id,
                    binding.id,
                    value,
                )
            except Exception:
                logger.exception(
                    "Write failed: adapter=%s, binding=%s",
                    binding.adapter_type,
                    binding.id,
                )

    async def _report_type_mismatch(self, event: Any, expected: str, got: str) -> None:
        reporter = getattr(self._registry, "report_type_mismatch", None)
        if reporter is None:
            return
        await reporter(
            event.datapoint_id,
            expected=expected,
            got=got,
            source_adapter=getattr(event, "source_adapter", ""),
            value=event.value,
        )

    async def _clear_type_mismatch(self, dp_id: uuid.UUID) -> None:
        clear = getattr(self._registry, "clear_diagnostic", None)
        if clear is None:
            return
        await clear(dp_id, "type_mismatch")


def _row_value(row: Any, key: str) -> str | None:
    try:
        value = row[key]
    except Exception:
        return None
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_write_router: WriteRouter | None = None


def get_write_router() -> WriteRouter:
    if _write_router is None:
        raise RuntimeError("WriteRouter not initialized")
    return _write_router


def reset_write_router() -> None:
    """Reset the WriteRouter singleton. For testing only."""
    global _write_router
    _write_router = None


def init_write_router(db: Any, registry: Any, event_bus: Any | None = None) -> WriteRouter:
    global _write_router
    _write_router = WriteRouter(db, registry, event_bus=event_bus)
    return _write_router
