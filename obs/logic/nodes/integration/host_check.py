"""Node definition for the ``host_check`` function block (Host Check (Ping))."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="host_check",
    label="Host Check (Ping)",
    category="integration",
    description="Pingt einen Host und liefert den Erreichbarkeitsstatus sowie die Latenz. Wird ausgelöst wenn der Trigger-Eingang true ist (Flanke). Empfehlung: mit einem Timer/Cron-Knoten verbinden.",
    inputs=[port("trigger", "Trigger", "trigger")],
    outputs=[
        port("reachable", "Erreichbar", "boolean"),
        port("latency_ms", "Latenz (ms)", "number"),
    ],
    config_schema={
        "host": {
            "type": "string",
            "default": "",
            "label": "Host / IP-Adresse",
        },
        "timeout_s": {
            "type": "number",
            "default": 1,
            "label": "Timeout (Sekunden)",
        },
        "count": {
            "type": "number",
            "default": 1,
            "label": "Ping-Anzahl",
        },
    },
    color="#0369a1",
)
