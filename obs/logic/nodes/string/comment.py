"""Node definition for the ``comment`` function block (Kommentar)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef

# Purely visual annotation node (issue #1043) — no ports, no executor
# case (falls through to the "unknown node type" no-op branch, same as
# ai_logic). width/height live in config_schema only so freshly dropped
# nodes get sane defaults via the generic onDrop seeding in LogicView.vue;
# they are not rendered as form fields (comment has its own config panel).
NODE_TYPE = NodeTypeDef(
    type="comment",
    label="Kommentar",
    category="string",
    description="Freier Mehrzeilen-Text zur Dokumentation direkt auf dem Graph-Canvas. Rein visuell, hat keinen Effekt auf die Ausführung.",
    inputs=[],
    outputs=[],
    config_schema={
        "text": {"type": "string", "default": "", "label": "Text"},
        "width": {"type": "number", "default": 220, "label": "Breite"},
        "height": {"type": "number", "default": 140, "label": "Höhe"},
    },
    color="#ca8a04",
)
