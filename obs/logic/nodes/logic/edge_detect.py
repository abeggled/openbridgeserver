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
        # "Trigger-" prefixed so the ports are not confused with the
        # like-named on_rising/on_falling configuration fields.
        port("rising", "Trigger-Steigend", "trigger"),
        port("falling", "Trigger-Fallend", "trigger"),
    ],
    config_schema={
        # One setting per edge direction, each answering the whole question for
        # that direction: stay silent, pulse the trigger only, or pulse it and
        # send a value. A separate "which edge" enum next to per-edge send
        # switches would overlap — "only rising" and "do not send on falling"
        # differ solely on the falling trigger, which reads as a contradiction
        # in the editor.
        "on_rising": {
            "type": "string",
            "enum": ["value", "trigger", "off"],
            "default": "value",
            "label": "Steigende Flanke",
        },
        # ``value_type_field`` points the editor at the field that decides how
        # the two edge values are entered: a true/false dropdown, a number
        # input or free text. Keeping it in the schema means NodeConfigPanel
        # stays generic.
        "value_rising": {
            "type": "string",
            "default": "true",
            "label": "Wert bei steigender Flanke",
            "value_type_field": "data_type",
            # Meaningless unless this direction sends a value. Stated the way
            # the executor decides it — anything that is not off/trigger sends
            # — so an imported or future setting keeps its field visible.
            "visible_when": {"field": "on_rising", "not_in": ["off", "trigger"]},
        },
        "on_falling": {
            "type": "string",
            "enum": ["value", "trigger", "off"],
            "default": "value",
            "label": "Fallende Flanke",
        },
        "value_falling": {
            "type": "string",
            "default": "false",
            "label": "Wert bei fallender Flanke",
            "value_type_field": "data_type",
            # Meaningless unless this direction sends a value. Stated the way
            # the executor decides it — anything that is not off/trigger sends
            # — so an imported or future setting keeps its field visible.
            "visible_when": {"field": "on_falling", "not_in": ["off", "trigger"]},
        },
        "data_type": {
            "type": "string",
            "enum": ["bool", "number", "string"],
            "default": "bool",
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
