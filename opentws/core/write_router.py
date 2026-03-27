"""
Write Router — Phase 4

Routes inbound MQTT dp/{uuid}/set messages to the correct adapter.

Flow:
  MQTT dp/{uuid}/set payload
    → WriteRouter.handle(dp_id, raw_payload)
      → DataPoint lookup → DataTypeRegistry.deserialize
        → DB: get all DEST/BOTH bindings for this DataPoint
          → AdapterRegistry.get_instance(adapter_type).write(binding, value)
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class WriteRouter:
    def __init__(self, db: Any, registry: Any) -> None:
        from opentws.db.database import Database
        from opentws.core.registry import DataPointRegistry
        self._db: Database = db
        self._registry: DataPointRegistry = registry

    async def handle(self, dp_id: uuid.UUID, raw_payload: str) -> None:
        """Deserialize payload and write to all DEST/BOTH bindings."""
        from opentws.models.types import DataTypeRegistry
        from opentws.adapters import registry as adapter_registry
        from opentws.adapters.registry import _row_to_binding

        logger.info("WriteRouter.handle: dp_id=%s payload=%r", dp_id, raw_payload)
        dp = self._registry.get(dp_id)
        if dp is None:
            logger.warning("Write request for unknown DataPoint %s — ignored", dp_id)
            return

        # Deserialize value
        dt = DataTypeRegistry.get(dp.data_type)
        try:
            value = dt.mqtt_deserializer(raw_payload)
        except Exception:
            # Fallback: try raw JSON, then plain string
            try:
                value = json.loads(raw_payload)
            except Exception:
                value = raw_payload
        logger.info("WriteRouter: dp=%s value=%r (type=%s)", dp.name, value, dp.data_type)

        # Get all active DEST/BOTH bindings
        rows = await self._db.fetchall(
            """SELECT * FROM adapter_bindings
               WHERE datapoint_id=? AND direction IN ('DEST','BOTH') AND enabled=1""",
            (str(dp_id),),
        )
        if not rows:
            logger.warning("No writable bindings for DataPoint %s", dp_id)
            return
        logger.info("WriteRouter: %d writable binding(s) found", len(rows))

        for row in rows:
            binding = _row_to_binding(row)
            instance = adapter_registry.get_instance(binding.adapter_type)
            if instance is None:
                logger.warning(
                    "Adapter %s not running — write for binding %s skipped",
                    binding.adapter_type, binding.id,
                )
                continue
            try:
                await instance.write(binding, value)
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
