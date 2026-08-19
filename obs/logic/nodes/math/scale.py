"""Node definition for the ``math_map`` function block (Skalieren)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="math_map",
    label="Skalieren",
    category="math",
    description="Skaliert einen Wert von einem Bereich in einen anderen",
    inputs=[port("value", "Wert")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "in_min": {"type": "number", "default": 0},
        "in_max": {"type": "number", "default": 100},
        "out_min": {"type": "number", "default": 0},
        "out_max": {"type": "number", "default": 1},
    },
    color="#7c3aed",
)
