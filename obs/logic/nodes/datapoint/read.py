"""Node definition for the ``datapoint_read`` function block (Objekt lesen)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="datapoint_read",
    label="Objekt lesen",
    category="datapoint",
    description="Gibt den aktuellen Wert eines DataPoints aus. Triggert bei Wertänderung.",
    inputs=[],
    outputs=[port("value", "Wert"), port("changed", "Geändert", "trigger")],
    config_schema={
        "datapoint_id": {"type": "string", "format": "datapoint"},
        "datapoint_name": {"type": "string"},
        # ── Transformation ────────────────────────────────────────────
        "value_formula": {"type": "string", "default": ""},
        # ── Filter ────────────────────────────────────────────────────
        "trigger_on_change": {"type": "boolean", "default": False},
        "min_delta": {"type": "number", "default": ""},
        "min_delta_pct": {"type": "number", "default": ""},
        "throttle_value": {"type": "number", "default": ""},
        "throttle_unit": {"type": "string", "default": "s"},
    },
    color="#0f766e",
)
