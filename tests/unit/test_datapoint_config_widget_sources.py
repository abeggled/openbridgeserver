"""Widget configs must expose their datapoints to the Visu page access check.

The Terminliste widget (#999) stores its calendar sources as
``sources[].datapoint_id``. Anonymous and PIN-protected pages only get values
for datapoints the backend finds in the page's widget configs, so these ids
have to be collected like every other widget's.
"""

from __future__ import annotations

from obs.api.v1.datapoint_config import collect_datapoint_ids_from_config

DP_A = "11111111-1111-4111-8111-111111111111"
DP_B = "22222222-2222-4222-8222-222222222222"


def test_terminliste_sources_are_collected() -> None:
    config = {
        "label": "Termine",
        "sources": [
            {"datapoint_id": DP_A, "label": "Privat", "color": "#22c55e"},
            {"datapoint_id": DP_B, "label": "Abfuhr", "color": "#f97316"},
            {"datapoint_id": "", "label": "leer", "color": "#000000"},
        ],
        "days": 7,
    }
    found: set[str] = set()

    collect_datapoint_ids_from_config(config, found)

    assert found == {DP_A, DP_B}
