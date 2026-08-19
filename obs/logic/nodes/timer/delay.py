"""Node definition for the ``timer_delay`` function block (Verzögerung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="timer_delay",
    label="Verzögerung",
    category="timer",
    description="Verzögert ein Signal um N Sekunden",
    inputs=[port("trigger", "Trigger", "trigger")],
    outputs=[port("trigger", "Trigger", "trigger")],
    config_schema={"delay_s": {"type": "number", "default": 1.0, "min": 0}},
    color="#b45309",
)
