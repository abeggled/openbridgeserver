"""Node definition for the ``consumption_counter`` function block (Verbrauchszähler)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="consumption_counter",
    label="Verbrauchszähler",
    category="math",
    description=(
        "Berechnet Verbrauchswerte (täglich, wöchentlich, monatlich, jährlich) "
        "aus einem fortlaufenden Zählerwert. "
        "Speichert zusätzlich den Verbrauch der Vorperiode für Vergleiche."
    ),
    inputs=[port("value", "Zählerwert")],
    outputs=[
        port("daily", "Täglich"),
        port("weekly", "Wöchentlich"),
        port("monthly", "Monatlich"),
        port("yearly", "Jährlich"),
        port("prev_daily", "Vorgestern"),
        port("prev_weekly", "Vorwoche"),
        port("prev_monthly", "Vormonat"),
        port("prev_yearly", "Vorjahr"),
    ],
    config_schema={
        "init_meter": {
            "type": "number",
            "default": None,
            "label": "Startwert Zählerstand",
        },
        "init_daily": {
            "type": "number",
            "default": None,
            "label": "Startwert täglich",
        },
        "init_weekly": {
            "type": "number",
            "default": None,
            "label": "Startwert wöchentlich",
        },
        "init_monthly": {
            "type": "number",
            "default": None,
            "label": "Startwert monatlich",
        },
        "init_yearly": {
            "type": "number",
            "default": None,
            "label": "Startwert jährlich",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#7c3aed",
)
