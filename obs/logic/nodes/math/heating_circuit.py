"""Node definition for the ``heating_circuit`` function block (Sommer/Winter (DIN))."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="heating_circuit",
    label="Sommer/Winter (DIN)",
    category="math",
    description=(
        "Sommer/Winter-Umschaltung nach DIN (Mannheimer Methode). Eingang: Aussentemperatur. "
        "Messzeitpunkte (Erste-Kreuzung): T1 = anliegender Wert ab 07:00, T2 = ab 14:00, T3 = ab 21:00. "
        "Funktioniert auch wenn der Sensor die Messstunden nicht exakt trifft. "
        "Jeder Slot wird pro Tag nur einmal erfasst. "
        "Tagesmittel: T_avg = (T1 + T2 + 2×T3) / 4. "
        "Monatsmittel: gleitender Mittelwert der letzten 31 Tagesmittel. "
        "Heizmodus EIN wenn T_avg < Grenztemperatur, AUS wenn T_avg ≥ Grenztemperatur + Hysterese. "
        "Fehlende Slots werden beim Start aus der Historie ergänzt. "
        "Zustand bleibt über Neustarts erhalten."
    ),
    inputs=[
        port("value", "Temp °C"),
    ],
    outputs=[
        port("heating_mode", "Heizmodus"),
        port("daily_avg", "Tagesmittel"),
        port("monthly_avg", "Monatsmittel"),
        port("t1", "T1 07:00 (debug)"),
        port("t2", "T2 14:00 (debug)"),
        port("t3", "T3 21:00 (debug)"),
    ],
    config_schema={
        "threshold_temp": {
            "type": "number",
            "default": 14.0,
            "label": "Grenztemperatur °C (Heizen EIN unterhalb)",
        },
        "hysteresis": {
            "type": "number",
            "default": 2.0,
            "label": "Hysterese °C (Heizen AUS ab Grenztemperatur + Hysterese)",
        },
        "persist_state": {
            "type": "boolean",
            "default": True,
            "label": "Zustand nach Neustart wiederherstellen",
        },
    },
    color="#7c3aed",
)
