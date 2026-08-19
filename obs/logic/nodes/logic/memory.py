"""Node definition for the ``memory`` function block (Speicher)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="memory",
    label="Speicher",
    category="logic",
    description=(
        "Gibt den gespeicherten Wert aus dem vorherigen Logiklauf aus und speichert den aktuellen Eingangswert für den nächsten Lauf. "
        "Diese Node ist die explizite Tick-Grenze für kontrollierte Rückkopplungen."
    ),
    inputs=[port("in", "Eingang"), port("reset", "Reset", "trigger")],
    outputs=[port("out", "Ausgang")],
    config_schema={
        "initial_value": {"type": "string", "default": "", "label": "Initialwert"},
        "data_type": {
            "type": "string",
            "enum": ["auto", "number", "bool", "string"],
            "default": "auto",
            "label": "Datentyp",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
