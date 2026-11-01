"""Validation shared by logic graph persistence paths."""

from __future__ import annotations

import json
import math
from typing import Any

from obs.core.system_datapoints import is_system_datapoint
from obs.logic.models import FlowData, LogicNode

_DURATION_FIELDS = {
    "timer_delay": ("delay_s", 0),
    "timer_pulse": ("duration_s", 0),
    "api_client": ("timeout_s", 1),
}

# The two timer blocks name their duration differently. A block carrying only
# its sibling's key would silently run with the 1 s default (#1377).
_SIBLING_DURATION_FIELDS = {
    "timer_delay": "duration_s",
    "timer_pulse": "delay_s",
}


def validate_timer_durations(flow_data: FlowData) -> None:
    """Reject invalid durations before they are persisted."""
    for node in flow_data.nodes:
        duration_field = _DURATION_FIELDS.get(node.type)
        if duration_field is None:
            continue
        field, minimum = duration_field
        value = node.data.get(field)
        sibling = _SIBLING_DURATION_FIELDS.get(node.type)
        if sibling is not None and sibling in node.data and value in (None, ""):
            raise ValueError(f"{node.type} reads its duration from {field}, not {sibling}")
        if value is None:
            continue

        try:
            duration = float(value)
        except (TypeError, ValueError) as exc:
            if value == "":
                continue
            raise ValueError(f"{field} must be a number") from exc
        except OverflowError as exc:
            raise ValueError(f"{field} must be a finite number") from exc

        if not math.isfinite(duration):
            raise ValueError(f"{field} must be a finite number")
        if duration < minimum:
            raise ValueError(f"{field} must be greater than or equal to {minimum}")


def _written_datapoint_ids(node: LogicNode) -> list[Any]:
    """The DataPoints a node writes to: a Write Object's target, a value sequence's steps."""
    if node.type == "datapoint_write":
        return [node.data.get("datapoint_id")]
    if node.type != "value_sequence":
        return []
    steps = node.data.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            return []
    return [step.get("datapoint_id") for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def validate_write_targets(flow_data: FlowData) -> None:
    """Reject Logic writes into a read-only system DataPoint (#1370).

    The event bus drops them, so such a graph would report writes that never
    happen. Reading a system DataPoint stays allowed.
    """
    for node in flow_data.nodes:
        for dp_id in _written_datapoint_ids(node):
            if is_system_datapoint(dp_id):
                raise ValueError(f"DataPoint {dp_id} is a read-only system DataPoint and cannot be written by Logic")
