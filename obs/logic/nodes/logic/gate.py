"""Node definition for the ``gate`` function block (TOR)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="gate",
    label="TOR",
    category="logic",
    description=(
        "Signal-Tor: lässt den Eingang durch wenn Freigabe=true, sperrt sonst. "
        "Verhalten bei gesperrtem Tor: letzten Wert halten (retain) oder Standardwert ausgeben."
    ),
    inputs=[port("in", "Eingang"), port("enable", "Freigabe")],
    outputs=[port("out", "Ausgang")],
    config_schema={
        "closed_behavior": {
            "type": "string",
            "enum": ["retain", "default_value"],
            "default": "retain",
            "label": "Verhalten (gesperrt)",
        },
        "default_value": {
            "type": "string",
            "default": "0",
            "label": "Standardwert (bei gesperrt)",
        },
        "negate_enable": {
            "type": "boolean",
            "default": False,
            "label": "Freigabe invertieren",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
