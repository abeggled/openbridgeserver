"""Node definition for the ``operating_hours`` function block (Betriebsstunden)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="operating_hours",
    label="Betriebsstunden",
    category="timer",
    description="Zählt Betriebsstunden solange 'Aktiv' wahr ist. Reset setzt den Zähler zurück.",
    inputs=[
        port("active", "Aktiv", "trigger"),
        port("reset", "Reset", "trigger"),
    ],
    outputs=[port("hours", "Stunden")],
    config_schema={
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#b45309",
)
