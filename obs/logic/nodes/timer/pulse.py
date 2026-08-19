"""Node definition for the ``timer_pulse`` function block (Impuls)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="timer_pulse",
    label="Impuls",
    category="timer",
    description="Gibt einen Impuls für N Sekunden aus",
    inputs=[port("trigger", "Trigger", "trigger")],
    outputs=[port("out", "Out")],
    config_schema={"duration_s": {"type": "number", "default": 1.0, "min": 0}},
    color="#b45309",
)
