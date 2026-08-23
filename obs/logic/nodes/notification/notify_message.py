"""Node definition for the ``notify_message`` function block (Benachrichtigung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="notify_message",
    label="Benachrichtigung",
    category="notification",
    description="Sendet eine Nachricht über konfigurierte Ziele eines Message-/Benachrichtigungsadapters.",
    inputs=[port("trigger", "Trigger", "trigger"), port("message", "Nachricht", "trigger")],
    outputs=[port("sent", "Gesendet", "trigger")],
    config_schema={
        "adapter_instance_id": {"type": "string", "default": "", "label": "MESSAGE-Adapter"},
        "providers": {"type": "array", "default": [], "label": "Ziele"},
        "title": {"type": "string", "default": "", "label": "Titel"},
        "message": {"type": "string", "default": "", "label": "Nachricht (Fallback)"},
        "priority": {"type": "integer", "default": 0, "min": -2, "max": 1, "label": "Priorität"},
    },
    color="#e11d48",
)
