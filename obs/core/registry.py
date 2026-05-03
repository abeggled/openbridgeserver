"""DataPoint Registry — Phase 2

In-memory store of all DataPoints, kept in sync with SQLite.
Acts as the single source of truth at runtime — DB is only read on startup.

Responsibilities:
  - Load all DataPoints from DB on startup
  - Provide fast O(1) access by UUID
  - Accept value updates from the EventBus and push to MQTT
  - Persist create/update/delete operations to DB
  - Maintain the last known value + quality per DataPoint
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from obs.models.datapoint import DataPoint, DataPointCreate, DataPointUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ValueState — last known value per DataPoint
# ---------------------------------------------------------------------------


class ValueState:
    __slots__ = ("old_value", "quality", "ts", "value")

    def __init__(self) -> None:
        self.value: Any = None
        self.quality: str = "uncertain"
        self.ts: datetime = datetime.now(UTC)
        self.old_value: Any = None

    def update(self, value: Any, quality: str) -> bool:
        """Update state. Returns True if value actually changed."""
        changed = value != self.value or quality != self.quality
        if changed:
            self.old_value = self.value
            self.value = value
            self.quality = quality
            self.ts = datetime.now(UTC)
        return changed


# ---------------------------------------------------------------------------
# DataPointRegistry
# ---------------------------------------------------------------------------


class DataPointRegistry:
    """In-memory registry of all DataPoints, backed by SQLite.

    Typical usage in startup:
        registry = DataPointRegistry(db, mqtt_client, event_bus)
        await registry.load_from_db()
        event_bus.subscribe(DataValueEvent, registry.handle_value_event)
    """

    def __init__(self, db: Any, mqtt_client: Any, event_bus: Any) -> None:
        from obs.core.event_bus import EventBus
        from obs.core.mqtt_client import MqttClient
        from obs.db.database import Database

        self._db: Database = db
        self._mqtt: MqttClient = mqtt_client
        self._bus: EventBus = event_bus
        self._points: dict[uuid.UUID, DataPoint] = {}
        self._values: dict[uuid.UUID, ValueState] = {}

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def load_from_db(self) -> int:
        """Load all DataPoints from DB into memory. Returns count loaded."""
        rows = await self._db.fetchall("SELECT * FROM datapoints ORDER BY created_at, id")
        for row in rows:
            dp = _row_to_datapoint(row)
            self._points[dp.id] = dp
            self._values[dp.id] = ValueState()
        logger.info("DataPointRegistry: loaded %d datapoints from DB", len(self._points))

        # Restore persisted last values (quality = "good" per spec)
        persisted = await self._db.fetchall("SELECT * FROM datapoint_last_values")
        restored = 0
        for row in persisted:
            dp_id = uuid.UUID(row["datapoint_id"])
            state = self._values.get(dp_id)
            dp = self._points.get(dp_id)
            if state is None or dp is None or not dp.persist_value:
                continue
            try:
                import json as _json

                value = _json.loads(row["value"])
            except Exception:
                value = row["value"]
            state.value = value
            state.quality = "good"
            from datetime import datetime

            try:
                state.ts = datetime.fromisoformat(row["ts"])
            except Exception:
                state.ts = datetime.now(UTC)
            restored += 1
        if restored:
            logger.info("DataPointRegistry: restored %d persisted values", restored)

        return len(self._points)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, dp_id: uuid.UUID) -> DataPoint | None:
        return self._points.get(dp_id)

    def get_or_raise(self, dp_id: uuid.UUID) -> DataPoint:
        dp = self._points.get(dp_id)
        if dp is None:
            raise KeyError(f"DataPoint {dp_id} not found")
        return dp

    def all(self) -> list[DataPoint]:
        return list(self._points.values())

    def count(self) -> int:
        return len(self._points)

    def get_value(self, dp_id: uuid.UUID) -> ValueState | None:
        return self._values.get(dp_id)

    def page(self, offset: int = 0, limit: int = 50) -> list[DataPoint]:
        items = list(self._points.values())
        return items[offset : offset + limit]

    def search(
        self,
        q: str = "",
        tag: str = "",
        data_type: str = "",
        adapter_type: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> list[DataPoint]:
        results = list(self._points.values())
        if q:
            ql = q.lower()
            results = [dp for dp in results if ql in dp.name.lower()]
        if tag:
            results = [dp for dp in results if tag in dp.tags]
        if data_type:
            results = [dp for dp in results if dp.data_type == data_type]
        # adapter_type filtering requires binding lookup — done in API layer
        return results[offset : offset + limit]

    # ------------------------------------------------------------------
    # Write (CRUD)
    # ------------------------------------------------------------------

    async def create(self, payload: DataPointCreate) -> DataPoint:
        dp = DataPoint(**payload.model_dump())
        await self._db.execute_and_commit(
            """INSERT INTO datapoints
               (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(dp.id),
                dp.name,
                dp.data_type,
                dp.unit,
                json.dumps(dp.tags),
                dp.mqtt_topic,
                dp.mqtt_alias,
                int(dp.persist_value),
                int(dp.record_history),
                dp.created_at.isoformat(),
                dp.updated_at.isoformat(),
            ),
        )
        self._points[dp.id] = dp
        self._values[dp.id] = ValueState()
        logger.debug("DataPoint created: %s (%s)", dp.name, dp.id)
        return dp

    async def update(self, dp_id: uuid.UUID, payload: DataPointUpdate) -> DataPoint:
        dp = self.get_or_raise(dp_id)
        updates = payload.model_dump(exclude_none=True)
        now = datetime.now(UTC)

        old_name = dp.name
        for key, val in updates.items():
            setattr(dp, key, val)
        dp.updated_at = now

        await self._db.execute_and_commit(
            """UPDATE datapoints
               SET name=?, data_type=?, unit=?, tags=?, mqtt_alias=?, persist_value=?, record_history=?, updated_at=?
               WHERE id=?""",
            (
                dp.name,
                dp.data_type,
                dp.unit,
                json.dumps(dp.tags),
                dp.mqtt_alias,
                int(dp.persist_value),
                int(dp.record_history),
                now.isoformat(),
                str(dp_id),
            ),
        )
        # If persistence was just disabled, remove any stored last value
        if not dp.persist_value:
            await self._db.execute_and_commit("DELETE FROM datapoint_last_values WHERE datapoint_id=?", (str(dp_id),))

        self._points[dp_id] = dp
        logger.debug("DataPoint updated: %s (%s)", dp.name, dp_id)

        if dp.name != old_name:
            from obs.core.event_bus import DataPointRenamedEvent

            await self._bus.publish(DataPointRenamedEvent(dp_id=dp_id, old_name=old_name, new_name=dp.name))

        return dp

    async def delete(self, dp_id: uuid.UUID) -> None:
        self.get_or_raise(dp_id)  # raises KeyError if not found
        await self._db.execute_and_commit("DELETE FROM datapoints WHERE id=?", (str(dp_id),))
        del self._points[dp_id]
        del self._values[dp_id]
        logger.debug("DataPoint deleted: %s", dp_id)

    # ------------------------------------------------------------------
    # Value update (called by EventBus handler)
    # ------------------------------------------------------------------

    async def handle_value_event(self, event: Any) -> None:
        """Handle a DataValueEvent: update state, publish to MQTT."""
        dp = self._points.get(event.datapoint_id)
        if dp is None:
            logger.debug("ValueEvent for unknown DataPoint %s — ignored", event.datapoint_id)
            return

        state = self._values[event.datapoint_id]
        changed = state.update(event.value, event.quality)

        # Persist last value to DB if enabled
        if dp.persist_value and event.quality == "good":
            await self._db.execute_and_commit(
                """INSERT INTO datapoint_last_values (datapoint_id, value, unit, ts)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(datapoint_id) DO UPDATE SET
                       value=excluded.value,
                       unit=excluded.unit,
                       ts=excluded.ts""",
                (
                    str(dp.id),
                    json.dumps(event.value),
                    dp.unit,
                    event.ts.isoformat(),
                ),
            )

        # Publish to MQTT on every event (alias only on value change)
        alias_topic = dp.mqtt_alias if changed else None
        await self._mqtt.publish_value(
            dp.id,
            event.value,
            dp.unit,
            event.quality,
            mqtt_alias_topic=alias_topic,
            ts=event.ts,
        )


# ---------------------------------------------------------------------------
# DB row → DataPoint
# ---------------------------------------------------------------------------


def _row_to_datapoint(row: Any) -> DataPoint:
    return DataPoint(
        id=uuid.UUID(row["id"]),
        name=row["name"],
        data_type=row["data_type"],
        unit=row["unit"],
        tags=json.loads(row["tags"]),
        mqtt_topic=row["mqtt_topic"],
        mqtt_alias=row["mqtt_alias"],
        persist_value=bool(row["persist_value"]) if row["persist_value"] is not None else True,
        record_history=bool(row["record_history"]) if row["record_history"] is not None else True,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

_registry: DataPointRegistry | None = None


def get_registry() -> DataPointRegistry:
    if _registry is None:
        raise RuntimeError("DataPointRegistry not initialized — call init_registry() at startup")
    return _registry


def reset_registry() -> None:
    """Reset the DataPointRegistry singleton. For testing only."""
    global _registry
    _registry = None


async def init_registry(db: Any, mqtt_client: Any, event_bus: Any) -> DataPointRegistry:
    global _registry
    _registry = DataPointRegistry(db, mqtt_client, event_bus)
    await _registry.load_from_db()
    return _registry
