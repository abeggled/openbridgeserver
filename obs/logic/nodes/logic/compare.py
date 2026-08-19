"""Node definition for the ``compare`` function block (Vergleich)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="compare",
    label="Vergleich",
    category="logic",
    description="Vergleicht zwei Werte (>, <, =, >=, <=, !=)",
    inputs=[port("in1", "IN 1"), port("in2", "IN 2")],
    outputs=[port("out", "Ergebnis")],
    config_schema={
        "operator": {
            "type": "string",
            "enum": [">", "<", "=", ">=", "<=", "!="],
            "default": ">",
        },
        "operand": {"type": "number", "default": "", "label": "Operand"},
    },
    color="#1d4ed8",
)
