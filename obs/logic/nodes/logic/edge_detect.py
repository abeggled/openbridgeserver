"""Node definition for the ``edge_detect`` function block (Flankenerkennung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="edge_detect",
    label="Flankenerkennung",
    category="logic",
    description=(
        "Wertet den Eingang boolesch aus und reagiert auf den Wechsel: bei steigender Flanke (falsch → wahr) "
        "wird der Wert für steigende Flanken ausgegeben und der Steigend-Trigger gesetzt, bei fallender Flanke "
        "(wahr → falsch) entsprechend der Wert für fallende Flanken und der Fallend-Trigger. Ohne Flanke wird "
        "nichts auf den Ausgang gesendet, der letzte Wert wird also nicht wiederholt."
    ),
    inputs=[port("in", "Eingang"), port("reset", "Reset", "trigger")],
    outputs=[
        port("out", "Ausgang"),
        port("rising", "Steigende Flanke", "trigger"),
        port("falling", "Fallende Flanke", "trigger"),
    ],
    config_schema={
        "mode": {
            "type": "string",
            "enum": ["both", "rising", "falling"],
            "default": "both",
            "label": "Flanke",
        },
        # ``value_type_field`` points the editor at the field that decides how
        # these two are entered: a true/false dropdown, a number input or free
        # text. Keeping it in the schema means NodeConfigPanel stays generic.
        "value_rising": {
            "type": "string",
            "default": "true",
            "label": "Wert bei steigender Flanke",
            "value_type_field": "data_type",
        },
        "value_falling": {
            "type": "string",
            "default": "false",
            "label": "Wert bei fallender Flanke",
            "value_type_field": "data_type",
        },
        "data_type": {
            "type": "string",
            "enum": ["bool", "number", "string"],
            "default": "bool",
            "label": "Datentyp",
        },
        "send_on_rising": {"type": "boolean", "default": True, "label": "Bei steigender Flanke senden"},
        "send_on_falling": {"type": "boolean", "default": True, "label": "Bei fallender Flanke senden"},
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
