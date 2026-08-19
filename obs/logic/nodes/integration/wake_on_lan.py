"""Node definition for the ``wake_on_lan`` function block (Wake on LAN)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="wake_on_lan",
    label="Wake on LAN",
    category="integration",
    description="Sendet ein Wake-on-LAN Magic-Paket an ein Gerät per UDP-Broadcast. Wird ausgelöst wenn der Trigger-Eingang true ist.",
    inputs=[port("trigger", "Trigger", "trigger")],
    outputs=[port("sent", "Gesendet", "trigger")],
    config_schema={
        "mac_address": {
            "type": "string",
            "default": "",
            "label": "MAC-Adresse (z.B. AA:BB:CC:DD:EE:FF)",
        },
        "broadcast_ip": {
            "type": "string",
            "default": "255.255.255.255",
            "label": "Broadcast-IP",
        },
        "port": {
            "type": "number",
            "default": 9,
            "label": "UDP-Port",
        },
    },
    color="#0369a1",
)
