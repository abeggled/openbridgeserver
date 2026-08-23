"""Node definition for the ``change_filter`` function block (Änderungsfilter)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="change_filter",
    label="Änderungsfilter",
    category="logic",
    description=(
        "Gibt den Eingangswert aus und setzt den changed-Trigger nur dann, wenn er sich vom zuletzt "
        "empfangenen Wert unterscheidet. Wiederholt gleiche Werte werden unterdrückt (Edomi-artiges SendByChange)."
    ),
    inputs=[port("in", "Eingang")],
    outputs=[port("out", "Ausgang"), port("changed", "Geändert", "trigger")],
    config_schema={
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#1d4ed8",
)
