"""
Write Router — Phase 4 / Phase 5 (Multi-Instance)

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

import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class WriteRouter:
    def __init__(self, db: Any, registry: Any) -> None:
        from opentws.db.database import Database
        from opentws.core.registry import DataPointRegistry
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
        from opentws.models.types import DataTypeRegistry

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
        """
        Propagate a DataValueEvent to all DEST/BOTH bindings of the same DataPoint.

        Called by the EventBus whenever a SOURCE/BOTH binding delivers a new value.
        The originating binding (event.binding_id) is skipped to avoid loopback.
        """
        logger.info(
            "WriteRouter.handle_value_event: dp=%s value=%r source_binding=%s",
            event.datapoint_id, event.value, event.binding_id,
        )
        await self._write_to_dest_bindings(
            event.datapoint_id, event.value, skip_binding_id=event.binding_id
        )

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    async def _write_to_dest_bindings(
        self,
        dp_id: uuid.UUID,
        value: Any,
        skip_binding_id: uuid.UUID | None,
    ) -> None:
        from opentws.adapters import registry as adapter_registry
        from opentws.adapters.registry import _row_to_binding

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
                    "Adapter-Instanz nicht gefunden — write für binding %s übersprungen "
                    "(type=%s, instance_id=%s)",
                    binding.id, binding.adapter_type, binding.adapter_instance_id,
                )
                continue
            # --- Filter 1: Send-Throttle ---
            if binding.send_throttle_ms:
                min_interval = binding.send_throttle_ms / 1000.0
                last_ts = self._last_sent.get(binding.id)
                if last_ts is not None and (time.monotonic() - last_ts) < min_interval:
                    logger.debug(
                        "WriteRouter: throttle — skipping binding %s "
                        "(min=%.3fs elapsed=%.3fs)",
                        binding.id, min_interval, time.monotonic() - last_ts,
                    )
                    continue

            # --- Filter 2 & 3: Wert-basierte Filter (nur wenn Vorgänger bekannt) ---
            last_val = self._last_value.get(binding.id)
            if last_val is not None:

                # Filter 2: Nur bei Änderung
                if binding.send_on_change and value == last_val:
                    logger.debug(
                        "WriteRouter: on-change — skipping binding %s (value unchanged: %r)",
                        binding.id, value,
                    )
                    continue

                # Filter 3: Mindest-Abweichung (abs./rel.) — nur für numerische Werte
                if binding.send_min_delta is not None or binding.send_min_delta_pct is not None:
                    try:
                        v_new  = float(value)
                        v_last = float(last_val)
                        diff   = abs(v_new - v_last)

                        if binding.send_min_delta is not None and diff < binding.send_min_delta:
                            logger.debug(
                                "WriteRouter: min_delta — skipping binding %s "
                                "(diff=%.4f < min=%.4f)",
                                binding.id, diff, binding.send_min_delta,
                            )
                            continue

                        if binding.send_min_delta_pct is not None:
                            base = abs(v_last) if v_last != 0 else abs(v_new)
                            pct  = (diff / base * 100) if base != 0 else 0.0
                            if pct < binding.send_min_delta_pct:
                                logger.debug(
                                    "WriteRouter: min_delta_pct — skipping binding %s "
                                    "(%.2f%% < %.2f%%)",
                                    binding.id, pct, binding.send_min_delta_pct,
                                )
                                continue
                    except (TypeError, ValueError):
                        pass  # Nicht-numerische Werte: Delta-Filter ignorieren

            # --- DEST-Formel: Wert vor dem Schreiben transformieren ---
            write_value = value
            if binding.value_formula:
                from opentws.core.formula import apply_formula
                write_value = apply_formula(binding.value_formula, value)
                logger.debug(
                    "WriteRouter: DEST formula '%s' applied: %r → %r",
                    binding.value_formula, value, write_value,
                )

            try:
                await instance.write(binding, write_value)
                self._last_sent[binding.id]  = time.monotonic()
                self._last_value[binding.id] = value  # Original für Delta/OnChange
                logger.info(
                    "WriteRouter: wrote to adapter=%s instance=%s binding=%s value=%r",
                    binding.adapter_type, binding.adapter_instance_id, binding.id, value,
                )
            except Exception:
                logger.exception(
                    "Write failed: adapter=%s, binding=%s", binding.adapter_type, binding.id
                )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_write_router: WriteRouter | None = None


def get_write_router() -> WriteRouter:
    if _write_router is None:
        raise RuntimeError("WriteRouter not initialized")
    return _write_router


def init_write_router(db: Any, registry: Any) -> WriteRouter:
    global _write_router
    _write_router = WriteRouter(db, registry)
    return _write_router
