"""Node definition for the ``python_script`` function block (Python Script)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="python_script",
    label="Python Script",
    category="script",
    description="Führt ein Python-Skript aus. Verfügbar: inputs dict → return value",
    inputs=[port("in1", "IN 1"), port("in2", "IN 2"), port("in3", "IN 3")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "script": {
            "type": "string",
            "default": "# inputs['in1'], inputs['in2']\nresult = inputs.get('in1', 0)",
        },
    },
    color="#be185d",
)
