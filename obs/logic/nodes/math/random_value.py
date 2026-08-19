"""Node definition for the ``random_value`` function block (Zufallswert)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="random_value",
    label="Zufallswert",
    category="math",
    description=(
        "Gibt bei jedem Trigger-Signal einen zufälligen Wert zwischen Min und Max aus. "
        "Typ 'int' liefert eine Ganzzahl (random.randint), "
        "Typ 'float' liefert eine Gleitkommazahl mit konfigurierbaren Nachkommastellen."
    ),
    inputs=[port("trigger", "Trigger", "trigger")],
    outputs=[port("value", "Wert")],
    config_schema={
        "data_type": {
            "type": "string",
            "enum": ["int", "float"],
            "default": "int",
            "label": "Datentyp",
        },
        "min": {"type": "number", "default": 0, "label": "Minimum"},
        "max": {"type": "number", "default": 100, "label": "Maximum"},
        "decimal_places": {
            "type": "integer",
            "default": 2,
            "minimum": 0,
            "maximum": 10,
            "label": "Nachkommastellen (nur float)",
        },
    },
    color="#7c3aed",
)
