"""Helpers for datapoint references embedded in widget configuration."""

from __future__ import annotations

import uuid
from typing import Any


_DATAPOINT_KEYS_EXACT = {
    "datapoint_id",
    "status_datapoint_id",
    "dp_id",
    "house_dp",
    "secondary_dp_id",
    "actual_temp_dp_id",
    "mode_dp_id",
    "datapointId",
    "statusDatapointId",
}


def is_uuid_str(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (TypeError, ValueError):
        return False


def _is_datapoint_config_key(key: str, parent_key: str | None) -> bool:
    if key in _DATAPOINT_KEYS_EXACT:
        return True
    if key.startswith("dp_"):
        return True
    if key.endswith(("_dp", "_dp_id", "_datapoint_id")):
        return True
    # Widgets with array items that store datapoint IDs as `id`.
    # - Info: extra_datapoints[].id
    # - Energiefluss: entities[].id
    if key == "id" and parent_key in {"extra_datapoints", "entities"}:
        return True
    return False


def collect_datapoint_ids_from_config(
    value: Any,
    out: set[str],
    *,
    parent_key: str | None = None,
) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(nested, str) and _is_datapoint_config_key(key, parent_key) and is_uuid_str(nested):
                out.add(nested)
            collect_datapoint_ids_from_config(nested, out, parent_key=key)
        return
    if isinstance(value, list):
        for nested in value:
            collect_datapoint_ids_from_config(nested, out, parent_key=parent_key)
