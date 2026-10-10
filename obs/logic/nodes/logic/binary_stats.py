"""Node definition for the ``binary_stats`` function block (Binärstatistik)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="binary_stats",
    label="Binärstatistik",
    category="logic",
    description=(
        "Zählt TRUE/FALSE über 2–30 Binäreingänge (je Eingang negierbar): Anzahl, Mehrheit, Gleichstand, "
        "Prozentanteil und Schwelle (k von n). Unverbundene Eingänge werden wahlweise ignoriert."
    ),
    inputs=[port("in1", "IN 1"), port("in2", "IN 2")],
    outputs=[
        port("count_true", "Anzahl TRUE", "number"),
        port("count_false", "Anzahl FALSE", "number"),
        port("majority_true", "Mehrheit TRUE", "boolean"),
        port("total", "Gesamt", "number"),
        port("percent_true", "Anteil TRUE %", "number"),
        port("tie", "Gleichstand", "boolean"),
        port("threshold_reached", "Schwelle erreicht", "boolean"),
    ],
    config_schema={
        "input_count": {
            "type": "integer",
            "default": 2,
            "min": 2,
            "max": 30,
            "label": "Anzahl Eingänge",
        },
        "unwired_inputs": {
            "type": "string",
            "enum": ["ignore", "count_false"],
            "default": "ignore",
            "label": "Unverbundene Eingänge",
        },
        "threshold_count": {
            "type": "integer",
            "default": 0,
            "min": 0,
            "label": "Schwelle (0 = aus)",
        },
    },
    color="#1d4ed8",
    help_id="logic-block-binary-stats",
)
