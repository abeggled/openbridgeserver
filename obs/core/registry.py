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

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from obs.core.json import json_dumps
from obs.models.datapoint import DataPoint, DataPointCreate, DataPointUpdate
from obs.models.types import DataTypeRegistry

logger = logging.getLogger(__name__)

_INSERT_DATAPOINT_SQL = """INSERT INTO datapoints
   (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, control_class, external_write_enabled, created_at, updated_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


# ---------------------------------------------------------------------------
# ValueState — last known value per DataPoint
# ---------------------------------------------------------------------------


class ValueState:
    __slots__ = ("diagnostics", "old_value", "quality", "ts", "value")

    def __init__(self) -> None:
        self.value: Any = None
        self.quality: str = "uncertain"
        self.ts: datetime = datetime.now(UTC)
        self.old_value: Any = None
        self.diagnostics: dict[str, dict[str, Any]] = {}

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
        # Serializes external_write_enabled enable-transitions (datapoints.py)
        # against binding creation/update's own opt-in cleanup (bindings.py):
        # without it, an enable-PATCH's "no write-semantic binding" check and
        # a concurrent create_binding() race, and neither side's check
        # observes the other's not-yet-committed change (Codex review).
        self._external_write_lock = asyncio.Lock()

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
            except (_json.JSONDecodeError, TypeError):
                value = row["value"]
            if dp.data_type in {"DATE", "TIME", "DATETIME"}:
                try:
                    value = DataTypeRegistry.get(dp.data_type).mqtt_deserializer(row["value"])
                except (_json.JSONDecodeError, ValueError, TypeError):
                    logger.debug(
                        "DataPointRegistry: persisted %s value for %s could not be deserialized",
                        dp.data_type,
                        dp.id,
                    )
            state.value = value
            state.quality = "good"
            from datetime import datetime

            try:
                state.ts = datetime.fromisoformat(row["ts"])
            except (ValueError, TypeError):
                state.ts = datetime.now(UTC)
            restored += 1
        if restored:
            logger.info("DataPointRegistry: restored %d persisted values", restored)

        return len(self._points)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def external_write_lock(self) -> asyncio.Lock:
        return self._external_write_lock

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

    @staticmethod
    def prepare_create(payload: DataPointCreate) -> DataPoint:
        """Build a datapoint without persisting or publishing it."""
        return DataPoint(**payload.model_dump())

    @staticmethod
    def _insert_params(dp: DataPoint) -> tuple[Any, ...]:
        return (
            str(dp.id),
            dp.name,
            dp.data_type,
            dp.unit,
            json.dumps(dp.tags),
            dp.mqtt_topic,
            dp.mqtt_alias,
            int(dp.persist_value),
            int(dp.record_history),
            dp.control_class,
            int(dp.external_write_enabled),
            dp.created_at.isoformat(),
            dp.updated_at.isoformat(),
        )

    async def insert(self, dp: DataPoint, *, connection: Any | None = None) -> None:
        """Insert a datapoint into the current database transaction."""
        await (connection or self._db).execute(_INSERT_DATAPOINT_SQL, self._insert_params(dp))

    def publish(self, dp: DataPoint) -> None:
        """Publish a committed datapoint to the in-memory registry."""
        self._points[dp.id] = dp
        self._values[dp.id] = ValueState()
        logger.debug("DataPoint created: %s (%s)", dp.name, dp.id)

    async def create(self, payload: DataPointCreate) -> DataPoint:
        dp = self.prepare_create(payload)
        await self._db.execute_and_commit(_INSERT_DATAPOINT_SQL, self._insert_params(dp))
        self.publish(dp)
        return dp

    async def update(self, dp_id: uuid.UUID, payload: DataPointUpdate) -> DataPoint:
        dp = self.get_or_raise(dp_id)
        updates = payload.model_dump(exclude_none=True, exclude={"value"})
        for clearable_field in ("unit", "mqtt_alias"):
            if clearable_field in payload.model_fields_set:
                updates[clearable_field] = getattr(payload, clearable_field)
        now = datetime.now(UTC)
        old_name = dp.name

        def _column_value(key: str, val: Any) -> Any:
            if key == "tags":
                return json.dumps(val)
            if key in ("persist_value", "record_history", "external_write_enabled"):
                return int(val)
            return val

        # Only the columns actually being changed — a full-row UPDATE built
        # from a snapshot of the whole object would silently lose a second,
        # concurrently-committed PATCH to an unrelated field: both requests
        # would snapshot the same pre-update row, so whichever commits last
        # overwrites the first request's already-persisted change with its
        # own now-stale value for that column (Codex review).
        set_clause = ", ".join([*(f"{key}=?" for key in updates), "updated_at=?"])
        params = [*(_column_value(key, val) for key, val in updates.items()), now.isoformat(), str(dp_id)]

        async def _persist_and_apply() -> None:
            await self._db.execute_and_commit(f"UPDATE datapoints SET {set_clause} WHERE id=?", params)
            # Only mutate the live, shared registry object — the same
            # instance WriteRouter reads directly, with no DB re-check —
            # after the write commits, so a failed write never leaves e.g.
            # external_write_enabled live in memory without it actually
            # having persisted (Codex review).
            for key, val in updates.items():
                setattr(dp, key, val)
            dp.updated_at = now
            # If persistence was just disabled, remove any stored last value
            if not dp.persist_value:
                await self._db.execute_and_commit("DELETE FROM datapoint_last_values WHERE datapoint_id=?", (str(dp_id),))
            self._points[dp_id] = dp

        # Run the whole persist-then-apply sequence as a real Task, shielded
        # from the caller's own cancellation: without this, a request
        # cancelled (e.g. client disconnect, server timeout) right after the
        # UPDATE physically commits but before these lines run would leave
        # the live registry object stale relative to what's now actually in
        # the database — e.g. WriteRouter still accepting external MQTT
        # writes after a disabling PATCH whose DB write in fact succeeded
        # (Codex review). Mirrors the asyncio.shield() pattern already used
        # for this exact class of problem in
        # obs/api/v1/datapoints.py::duplicate_datapoint().
        task = asyncio.create_task(_persist_and_apply())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

        logger.debug("DataPoint updated: %s (%s)", dp.name, dp_id)

        if dp.name != old_name:
            from obs.core.event_bus import DataPointRenamedEvent

            await self._bus.publish(DataPointRenamedEvent(dp_id=dp_id, old_name=old_name, new_name=dp.name))

        return dp

    async def delete(self, dp_id: uuid.UUID) -> None:
        self.get_or_raise(dp_id)  # raises KeyError if not found
        async with self._db.transaction():
            await self._db.execute(
                "DELETE FROM authz_node_roles WHERE node_type='datapoint' AND node_id=?",
                (str(dp_id),),
            )
            await self._db.execute("DELETE FROM datapoints WHERE id=?", (str(dp_id),))
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
                    json_dumps(event.value),
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

    async def report_type_mismatch(
        self,
        dp_id: uuid.UUID,
        *,
        expected: str,
        got: str,
        source_adapter: str,
        value: Any,
    ) -> None:
        state = self._values.get(dp_id)
        if state is None:
            return
        current = state.diagnostics.get("type_mismatch")
        count = int(current.get("count", 0)) + 1 if current else 1
        state.diagnostics["type_mismatch"] = {
            "type": "type_mismatch",
            "expected": expected,
            "got": got,
            "source_adapter": source_adapter,
            "count": count,
            "last_value": value,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    async def clear_diagnostic(self, dp_id: uuid.UUID, diagnostic_type: str) -> None:
        state = self._values.get(dp_id)
        if state is None:
            return
        state.diagnostics.pop(diagnostic_type, None)


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
        control_class=row["control_class"] if "control_class" in row.keys() else "room_local",  # noqa: SIM118 -- sqlite Row membership checks values
        external_write_enabled=bool(row["external_write_enabled"]) if "external_write_enabled" in row.keys() else False,  # noqa: SIM118
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
