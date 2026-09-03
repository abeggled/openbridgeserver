"""Node definition for the ``decision`` function block (Entscheidung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import CONDITION_OPERATORS, port

NODE_TYPE = NodeTypeDef(
    type="decision",
    label="Entscheidung",
    category="logic",
    description="Prüft einen Eingangswert gegen mehrere unabhängige Bedingungen. Jeder Ausgang liefert TRUE/FALSE.",
    inputs=[port("value", "Wert")],
    outputs=[port("out_1", "Ausgang 1", "trigger"), port("out_2", "Ausgang 2", "trigger")],
    config_schema={
        "conditions": {
            "type": "array",
            "label": "Bedingungen",
            "description": (
                "Ordered list of independent conditions, each tested against the 'value' "
                "input. The first field that matches its rule fires its own trigger output "
                "— unlike value_mapping there is no 'first match wins', every matching "
                "condition fires."
            ),
            "items": {
                "type": "object",
                "required": ["handle", "operator"],
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "Output port id this condition drives, e.g. 'out_1'.",
                    },
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
                },
            },
            "default": [
                {"handle": "out_1", "operator": "eq"},
                {"handle": "out_2", "operator": "eq"},
            ],
        },
    },
    color="#1d4ed8",
    help_id="logic-block-decision",
)
