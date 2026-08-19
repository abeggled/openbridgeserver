"""Node definition for the ``value_mapping`` function block (Zuordnung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="value_mapping",
    label="Zuordnung",
    category="logic",
    description="Ordnet einem Eingangswert anhand einer geordneten Regelliste genau einen Ergebniswert zu.",
    inputs=[port("value", "Wert")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "output_type": {
            "type": "string",
            "enum": ["bool", "int", "float", "string"],
            "default": "string",
            "label": "Ausgangstyp",
        },
        "rules": {
            "type": "string",
            "default": ('[{"operator":"eq","result":""},{"operator":"eq","result":""}]'),
            "label": "Regeln",
        },
        "has_default": {"type": "boolean", "default": False, "label": "Sonst-Wert verwenden"},
        "default_value": {"type": "string", "default": "", "label": "Sonst-Wert"},
    },
    color="#1d4ed8",
)
