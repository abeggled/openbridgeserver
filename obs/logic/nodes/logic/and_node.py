"""Node definition for the ``and`` function block (AND)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="and",
    label="AND",
    category="logic",
    description="Ausgang ist true wenn ALLE Eingänge true sind. Eingänge (2–30) und Ausgang einzeln negierbar.",
    inputs=[port("in1", "IN 1"), port("in2", "IN 2")],
    outputs=[port("out", "Out")],
    config_schema={
        "input_count": {
            "type": "integer",
            "default": 2,
            "min": 2,
            "max": 30,
            "label": "Anzahl Eingänge",
        },
    },
    color="#1d4ed8",
)
