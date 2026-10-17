"""Node definition for the ``json_extractor`` function block (JSON Extractor)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="json_extractor",
    label="JSON Extractor",
    category="integration",
    description="Parst einen JSON-String und extrahiert einen oder mehrere Werte anhand von Schlüsselpfaden (Punkt-Notation, z.B. sensors.temperature). Mehrere Ausgänge konfigurierbar über + im Konfigurations-Panel.",
    inputs=[port("data", "Daten")],
    outputs=[port("value", "Wert")],  # overridden dynamically when json_paths is set
    config_schema={
        "json_path": {"type": "string", "default": "", "label": "Schlüsselpfad (Legacy)"},
        "json_paths": {"type": "string", "default": "", "label": "Ausgänge (JSON-Array)"},
        "variables": {
            "type": "array",
            "default": [],
            "label": "Variablen",
            "description": "DataPoint-backed placeholders, usable in the paths as '###OBS<slot>###' (e.g. '###OBS1###').",
            "items": {
                "type": "object",
                "required": ["slot", "datapoint_id"],
                "properties": {
                    "slot": {"type": "integer", "description": "Placeholder index, referenced as ###OBS<slot>###."},
                    "datapoint_id": {"type": "string", "format": "datapoint"},
                    "datapoint_name": {"type": "string"},
                },
            },
        },
    },
    color="#0369a1",
    help_id="logic-block-json-extractor",
)
