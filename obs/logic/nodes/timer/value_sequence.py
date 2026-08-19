"""Node definition for the ``value_sequence`` function block (Sequenz)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="value_sequence",
    label="Sequenz",
    category="timer",
    description="Schreibt eine Folge von Werten mit konfigurierbaren Pausen.",
    inputs=[port("trigger", "Trigger", "trigger"), port("condition", "Bedingung")],
    outputs=[],
    config_schema={
        "run_mode": {"type": "string", "enum": ["once", "repeat_count", "while_condition"], "default": "once"},
        "repeat_count": {"type": "number", "default": 2, "min": 1},
        "restart_policy": {"type": "string", "enum": ["ignore", "restart", "queue"], "default": "ignore"},
        "cancel_when_condition_false": {"type": "boolean", "default": False},
        "steps": {"type": "array", "default": []},
    },
    color="#b45309",
)
