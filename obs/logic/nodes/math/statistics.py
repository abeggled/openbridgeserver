"""Node definition for the ``statistics`` function block (Statistik)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="statistics",
    label="Statistik",
    category="math",
    description="Berechnet Min/Max/Mittelwert laufend über alle empfangenen Werte. Reset-Eingang setzt zurück.",
    inputs=[port("value", "Wert"), port("reset", "Reset", "trigger")],
    outputs=[
        port("min", "Min"),
        port("max", "Max"),
        port("avg", "Mittelwert"),
        port("count", "Anzahl"),
    ],
    config_schema={
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#7c3aed",
)
