"""Backend-managed, read-only system DataPoints (issue #1370).

OBS publishes a handful of its own state values as ordinary DataPoints so the
Visu and Logic can read them like any other value. They differ from user
DataPoints in three ways:

* their IDs are deterministic constants, so a Visu or Logic configuration that
  references them stays portable between installations;
* the registry creates them on every load and refuses to rename, duplicate or
  delete them;
* only the backend may write their values. :class:`~obs.core.event_bus.EventBus`
  drops every ``DataValueEvent`` for one of these IDs that does not carry
  :data:`SYSTEM_SOURCE_ADAPTER` — which covers the API, MQTT ``set`` topics,
  adapter bindings and Logic in one place, whichever path a write takes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# ``source_adapter`` of the events the backend publishes for system DataPoints.
# Adapter types are upper case and the remaining internal sources (``api``,
# ``logic``, ``mqtt_set``, …) are different words, so no other path uses it.
SYSTEM_SOURCE_ADAPTER = "obs_system"

SYSTEM_TAG = "system"

# Namespace of the deterministic IDs below — never change it, or every Visu
# and Logic reference to a system DataPoint breaks.
SYSTEM_DATAPOINT_NAMESPACE = uuid.UUID("1a5cfe93-55ff-5f25-b124-53e456349404")


@dataclass(frozen=True)
class SystemDataPointDef:
    key: str
    name: str
    data_type: str

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(SYSTEM_DATAPOINT_NAMESPACE, self.key)


UPDATE_CHANNEL = SystemDataPointDef("update-channel", "OBS Update Channel", "STRING")
CURRENT_VERSION = SystemDataPointDef("current-version", "OBS Current Version", "STRING")
LATEST_VERSION = SystemDataPointDef("latest-version", "OBS Latest Version", "STRING")
LAST_CHECK = SystemDataPointDef("last-check", "OBS Last Check", "DATETIME")
UPDATE_AVAILABLE = SystemDataPointDef("update-available", "OBS Update Available", "BOOLEAN")

SYSTEM_DATAPOINTS: tuple[SystemDataPointDef, ...] = (
    UPDATE_CHANNEL,
    CURRENT_VERSION,
    LATEST_VERSION,
    LAST_CHECK,
    UPDATE_AVAILABLE,
)

SYSTEM_DATAPOINT_IDS: frozenset[uuid.UUID] = frozenset(d.id for d in SYSTEM_DATAPOINTS)


class SystemDataPointError(PermissionError):
    """Raised when something tries to change a backend-managed system DataPoint."""

    def __init__(self, dp_id: uuid.UUID) -> None:
        super().__init__(f"DataPoint {dp_id} is a read-only system DataPoint")
        self.dp_id = dp_id


def is_system_datapoint(dp_id: uuid.UUID | str | None) -> bool:
    if dp_id is None:
        return False
    if not isinstance(dp_id, uuid.UUID):
        try:
            dp_id = uuid.UUID(str(dp_id))
        except ValueError:
            return False
    return dp_id in SYSTEM_DATAPOINT_IDS
