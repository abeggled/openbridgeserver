"""Validation shared by logic graph persistence paths."""

from __future__ import annotations

import math

from obs.logic.models import FlowData

_DURATION_FIELDS = {
    "timer_delay": ("delay_s", 0),
    "timer_pulse": ("duration_s", 0),
    "api_client": ("timeout_s", 1),
}


def validate_timer_durations(flow_data: FlowData) -> None:
    """Reject invalid durations before they are persisted."""
    for node in flow_data.nodes:
        duration_field = _DURATION_FIELDS.get(node.type)
        if duration_field is None:
            continue
        field, minimum = duration_field
        if (value := node.data.get(field)) is None:
            continue

        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        except OverflowError as exc:
            raise ValueError(f"{field} must be a finite number") from exc

        if not math.isfinite(duration):
            raise ValueError(f"{field} must be a finite number")
        if duration < minimum:
            raise ValueError(f"{field} must be greater than or equal to {minimum}")
