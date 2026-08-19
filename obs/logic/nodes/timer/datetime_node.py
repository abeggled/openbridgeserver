"""Node definition for the ``datetime`` function block (Datum/Zeit)."""

from __future__ import annotations

from obs.datetime_format import DEFAULT_CUSTOM_FORMAT
from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="datetime",
    label="Datum/Zeit",
    category="timer",
    description="Gibt das aktuelle Datum und die aktuelle Zeit in der Anwendungs-Zeitzone aus.",
    inputs=[],
    outputs=[port("date", "Datum"), port("time", "Zeit"), port("custom", "Benutzerdefiniert")],
    config_schema={
        "custom_format": {"type": "string", "default": DEFAULT_CUSTOM_FORMAT, "label": "Benutzerdefiniertes Format"},
    },
    color="#b45309",
)
