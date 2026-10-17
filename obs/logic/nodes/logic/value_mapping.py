"""Node definition for the ``value_mapping`` function block (Zuordnung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import CONDITION_OPERATORS, port

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
            "type": "array",
            "label": "Regeln",
            "description": "Ordered rule list — the first rule that matches 'value' wins and its 'result' is output.",
            "items": {
                "type": "object",
                "required": ["operator"],
                "properties": {
                    "operator": {
                        "type": "string",
                        "enum": list(CONDITION_OPERATORS),
                        "default": "eq",
                    },
                    "value": {
                        "description": ("Comparison operand — used by all operators except 'range' (which uses min/max)."),
                    },
                    "min": {"description": "Lower bound, operator 'range' only."},
                    "max": {"description": "Upper bound, operator 'range' only."},
                    "case_sensitive": {
                        "type": "boolean",
                        "default": True,
                        "description": "text_eq/contains/starts_with/ends_with only.",
                    },
                    "result": {
                        "description": "Value returned when this rule matches, coerced per 'output_type'.",
                    },
                },
            },
            "default": [
                {"operator": "eq", "result": ""},
                {"operator": "eq", "result": ""},
            ],
        },
        "has_default": {"type": "boolean", "default": False, "label": "Sonst-Wert verwenden"},
        "default_value": {"type": "string", "default": "", "label": "Sonst-Wert"},
    },
    color="#1d4ed8",
    help_id="logic-block-value-mapping",
)
