"""Node definition for the ``notify_sms`` function block (SMS (seven.io))."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="notify_sms",
    label="SMS (seven.io)",
    category="notification",
    description="Sendet eine SMS via seven.io Gateway (gateway.seven.io). Wird automatisch ausgelöst wenn eine Nachricht am Eingang ankommt.",
    inputs=[port("trigger", "Trigger", "trigger"), port("message", "Nachricht", "trigger")],
    outputs=[port("sent", "Gesendet", "trigger")],
    config_schema={
        "api_key": {"type": "string", "default": "", "label": "API-Key"},
        "to": {"type": "string", "default": "", "label": "Empfänger (+41…)"},
        "sender": {
            "type": "string",
            "default": "obs",
            "label": "Absender (max 11 Zeichen)",
        },
        "message": {
            "type": "string",
            "default": "",
            "label": "Nachricht (Fallback)",
        },
    },
    color="#e11d48",
    hidden_from_palette=True,
    legacy=True,
)
