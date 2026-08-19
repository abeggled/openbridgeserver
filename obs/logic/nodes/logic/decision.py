"""Node definition for the ``decision`` function block (Entscheidung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="decision",
    label="Entscheidung",
    category="logic",
    description="Prüft einen Eingangswert gegen mehrere unabhängige Bedingungen. Jeder Ausgang liefert TRUE/FALSE.",
    inputs=[port("value", "Wert")],
    outputs=[port("out_1", "Ausgang 1", "trigger"), port("out_2", "Ausgang 2", "trigger")],
    config_schema={
        "conditions": {
            "type": "string",
            "default": ('[{"handle":"out_1","operator":"eq"},{"handle":"out_2","operator":"eq"}]'),
            "label": "Bedingungen",
        },
    },
    color="#1d4ed8",
)
