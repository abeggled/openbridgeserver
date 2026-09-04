"""Node definition for the ``string_replace`` function block (String Suchen/Ersetzen)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="string_replace",
    label="String Suchen/Ersetzen",
    category="string",
    description=(
        "Ersetzt Treffer in einem Text. Mehrere Regeln werden in der angegebenen Reihenfolge "
        "nacheinander auf das Zwischenergebnis angewendet. Je Regel wählbar: Suchtext (Plain) "
        "oder regulärer Ausdruck (RegEx, Gruppenverweise wie \\1 im Ersetzen-Feld), "
        "Gross-/Kleinschreibung und alle oder nur das erste Vorkommen."
    ),
    inputs=[port("text", "Text", "string")],
    outputs=[port("result", "Ergebnis", "string")],
    config_schema={
        # One empty rule so a freshly dropped block already shows an editable
        # row. Mirrored by _defaultReplaceRules() in NodeConfigPanel.vue.
        "rules": {
            "type": "array",
            "label": "Regeln",
            "description": "Ordered search/replace rules, each applied to the previous rule's result.",
            "items": {
                "type": "object",
                "required": ["search"],
                "properties": {
                    "search": {"type": "string", "description": "Search term; a rule with no search term is skipped."},
                    "replace": {"type": "string"},
                    "mode": {"type": "string", "enum": ["plain", "regex"], "default": "plain"},
                    "case_sensitive": {"type": "boolean", "default": True},
                    "replace_all": {
                        "type": "boolean",
                        "default": True,
                        "description": "False replaces only the first occurrence.",
                    },
                },
            },
            "default": [
                {"search": "", "replace": "", "mode": "plain", "case_sensitive": True, "replace_all": True},
            ],
        },
    },
    color="#0891b2",
    help_id="logic-block-string-replace",
)
