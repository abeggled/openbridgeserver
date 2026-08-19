"""Node definition for the ``clamp`` function block (Begrenzer)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="clamp",
    label="Begrenzer",
    category="math",
    description="Begrenzt den Eingangswert auf [Min, Max]. Werte außerhalb werden auf den Grenzwert gesetzt.",
    inputs=[port("value", "Wert")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "min": {"type": "number", "default": 0, "label": "Minimum"},
        "max": {"type": "number", "default": 100, "label": "Maximum"},
    },
    color="#7c3aed",
)
