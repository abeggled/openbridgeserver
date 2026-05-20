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


class WriteRouter:
    def __init__(self, db: Any, registry: Any) -> None:
        from obs.core.registry import DataPointRegistry
        from obs.db.database import Database

        self._db: Database = db
        self._registry: DataPointRegistry = registry
        # binding_id → timestamp of last successful send (monotonic seconds)
        self._last_sent: dict[uuid.UUID, float] = {}
        # binding_id → last successfully sent value (for on-change / delta checks)
        self._last_value: dict[uuid.UUID, Any] = {}

    # ------------------------------------------------------------------
    # Path 1 — inbound MQTT dp/{uuid}/set
    # ------------------------------------------------------------------

    async def handle(self, dp_id: uuid.UUID, raw_payload: str) -> None:
        """Deserialize payload and write to all DEST/BOTH bindings."""
        from obs.models.types import DataTypeRegistry

        logger.info("WriteRouter.handle: dp_id=%s payload=%r", dp_id, raw_payload)
        dp = self._registry.get(dp_id)
        if dp is None:
            logger.warning("Write request for unknown DataPoint %s — ignored", dp_id)
            return

        dt = DataTypeRegistry.get(dp.data_type)
        try:
            value = dt.mqtt_deserializer(raw_payload)
        except Exception:
            try:
                value = json.loads(raw_payload)
            except Exception:
                value = raw_payload
        logger.info("WriteRouter: dp=%s value=%r (type=%s)", dp.name, value, dp.data_type)

        await self._write_to_dest_bindings(dp_id, value, skip_binding_id=None)

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
                return

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
               WHERE datapoint_id=? AND direction IN ('DEST','BOTH') AND enabled=1""",
            (str(dp_id),),
        )
        if not rows:
            logger.debug("No writable bindings for DataPoint %s", dp_id)
            return

        logger.info("WriteRouter: %d writable binding(s) for dp %s", len(rows), dp_id)
        for row in rows:
            binding = _row_to_binding(row)
            if skip_binding_id and binding.id == skip_binding_id:
                logger.debug("WriteRouter: skipping originating binding %s", binding.id)
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


def init_write_router(db: Any, registry: Any) -> WriteRouter:
    global _write_router
    _write_router = WriteRouter(db, registry)
    return _write_router
