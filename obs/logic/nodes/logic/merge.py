"""Node definition for the ``merge`` function block (Klemme)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="merge",
    label="Klemme",
    category="logic",
    description=(
        "Bündelt mehrere unabhängige Wertquellen auf einen gemeinsamen Ausgang: "
        "wer zuletzt einen neuen Wert liefert, wird durchgereicht (Edomi-Klemme)."
    ),
    inputs=[port("in1", "IN 1"), port("in2", "IN 2")],
    outputs=[port("out", "Out")],
    config_schema={
        "input_count": {
            "type": "integer",
            "default": 2,
            "min": 2,
            "max": 30,
            "label": "Anzahl Eingänge",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
