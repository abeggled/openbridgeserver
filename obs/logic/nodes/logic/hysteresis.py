"""Node definition for the ``hysteresis`` function block (Hysterese)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="hysteresis",
    label="Hysterese",
    category="logic",
    description="Schaltet bei Überschreitung ON, erst bei Unterschreitung OFF",
    inputs=[port("value", "Wert")],
    outputs=[port("out", "Out")],
    config_schema={
        "threshold_on": {"type": "number", "default": 25.0},
        "threshold_off": {"type": "number", "default": 20.0},
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
