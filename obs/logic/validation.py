"""Validation shared by logic graph persistence paths."""

from __future__ import annotations

from obs.logic.models import FlowData

_TIMER_DURATION_FIELDS = {
    "timer_delay": "delay_s",
    "timer_pulse": "duration_s",
}


def validate_timer_durations(flow_data: FlowData) -> None:
    """Reject negative timer durations before they are persisted."""
    for node in flow_data.nodes:
        field = _TIMER_DURATION_FIELDS.get(node.type)
        if field is None or (value := node.data.get(field)) is None:
            continue

        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue

        if duration < 0:
            raise ValueError(f"{field} must be greater than or equal to 0")
