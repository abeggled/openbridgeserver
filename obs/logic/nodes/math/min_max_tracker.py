"""Node definition for the ``min_max_tracker`` function block (Min/Max Tracker)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="min_max_tracker",
    label="Min/Max Tracker",
    category="math",
    description=(
        "Verfolgt Minimum und Maximum über Zeitperioden "
        "(täglich, wöchentlich, monatlich, jährlich, absolut). "
        "Periodenwerte werden automatisch am Tages-/Wochen-/Monats-/Jahreswechsel zurückgesetzt."
    ),
    inputs=[port("value", "Wert")],
    outputs=[
        port("min_daily", "Min täglich"),
        port("max_daily", "Max täglich"),
        port("min_weekly", "Min wöchentlich"),
        port("max_weekly", "Max wöchentlich"),
        port("min_monthly", "Min monatlich"),
        port("max_monthly", "Max monatlich"),
        port("min_yearly", "Min jährlich"),
        port("max_yearly", "Max jährlich"),
        port("min_abs", "Min absolut"),
        port("max_abs", "Max absolut"),
    ],
    config_schema={
        "init_abs_min": {
            "type": "number",
            "default": None,
            "label": "Startwert Min absolut",
        },
        "init_abs_max": {
            "type": "number",
            "default": None,
            "label": "Startwert Max absolut",
        },
        "init_day_min": {
            "type": "number",
            "default": None,
            "label": "Startwert Min täglich",
        },
        "init_day_max": {
            "type": "number",
            "default": None,
            "label": "Startwert Max täglich",
        },
        "init_month_min": {
            "type": "number",
            "default": None,
            "label": "Startwert Min monatlich",
        },
        "init_month_max": {
            "type": "number",
            "default": None,
            "label": "Startwert Max monatlich",
        },
        "init_year_min": {
            "type": "number",
            "default": None,
            "label": "Startwert Min jährlich",
        },
        "init_year_max": {
            "type": "number",
            "default": None,
            "label": "Startwert Max jährlich",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#7c3aed",
)
