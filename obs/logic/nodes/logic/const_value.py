"""Node definition for the ``const_value`` function block (Festwert)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="const_value",
    label="Festwert",
    category="logic",
    description="Gibt einen festen Wert aus — Zahl, Bool oder Text. Nützlich als Schwellwert oder Referenz.",
    inputs=[],
    outputs=[port("value", "Wert")],
    config_schema={
        "value": {"type": "string", "default": "0", "label": "Wert"},
        "data_type": {
            "type": "string",
            "enum": ["number", "bool", "string"],
            "default": "number",
            "label": "Datentyp",
        },
    },
    color="#475569",
)
