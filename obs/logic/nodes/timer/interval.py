"""Node definition for the ``timer_interval`` function block (Takt)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="timer_interval",
    label="Takt",
    category="timer",
    description="Sendet automatisch alle N Sekunden einen Trigger-Impuls.",
    inputs=[],
    outputs=[port("trigger", "Trigger", "trigger")],
    config_schema={"interval_s": {"type": "number", "default": 5.0, "min": 0, "label": "Interval (s)"}},
    color="#b45309",
    help_id="logic-block-timer-interval",
)
