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
            "type": "string",
            "default": '[{"search":"","replace":"","mode":"plain","case_sensitive":true,"replace_all":true}]',
            "label": "Regeln",
        },
    },
    color="#0891b2",
)
