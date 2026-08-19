"""Node definition for the ``datapoint_write`` function block (Objekt schreiben)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="datapoint_write",
    label="Objekt schreiben",
    category="datapoint",
    description="Schreibt einen Wert in einen DataPoint",
    inputs=[port("value", "Wert"), port("trigger", "Trigger", "trigger")],
    outputs=[],
    config_schema={
        "datapoint_id": {"type": "string", "format": "datapoint"},
        "datapoint_name": {"type": "string"},
        # ── Transformation ────────────────────────────────────────────
        "value_formula": {"type": "string", "default": ""},
        # ── Filter ────────────────────────────────────────────────────
        "only_on_change": {"type": "boolean", "default": False},
        "min_delta": {"type": "number", "default": ""},
        "throttle_value": {"type": "number", "default": ""},
        "throttle_unit": {"type": "string", "default": "s"},
    },
    color="#0f766e",
)
