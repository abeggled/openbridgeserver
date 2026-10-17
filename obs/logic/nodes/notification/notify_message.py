"""Node definition for the ``notify_message`` function block (Benachrichtigung)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="notify_message",
    label="Benachrichtigung",
    category="notification",
    description="Sendet eine Nachricht über konfigurierte Ziele eines Message-/Benachrichtigungsadapters.",
    inputs=[
        port("trigger", "Trigger", "trigger"),
        port("message", "Nachricht", "trigger"),
        port("url", "URL"),
        port("url_title", "URL-Titel"),
        port("image_url", "Bild-URL"),
    ],
    outputs=[port("sent", "Gesendet", "trigger")],
    config_schema={
        "adapter_instance_id": {"type": "string", "default": "", "label": "MESSAGE-Adapter"},
        "providers": {"type": "array", "default": [], "label": "Ziele"},
        "title": {"type": "string", "default": "", "label": "Titel"},
        "message": {"type": "string", "default": "", "label": "Nachricht (Fallback)"},
        "priority": {"type": "integer", "default": 0, "min": -2, "max": 2, "label": "Priorität (Pushover)"},
        # Pushover options — evaluated only for selected Pushover targets. A
        # connected url/url_title/image_url input overrides the static value.
        "url": {"type": "string", "default": "", "label": "URL (Pushover)"},
        "url_title": {"type": "string", "default": "", "label": "URL-Titel (Pushover)"},
        "image_url": {"type": "string", "default": "", "label": "Bild-URL (Pushover)"},
        "sound": {"type": "string", "default": "", "label": "Sound (Pushover)"},
        "format": {"type": "string", "enum": ["text", "html", "monospace"], "default": "text", "label": "Formatierung (Pushover)"},
        "ttl": {"type": "integer", "default": None, "min": 1, "label": "TTL in Sekunden (Pushover)"},
        "timestamp": {"type": "integer", "default": None, "min": 1, "label": "Zeitstempel, Unix-Sekunden (Pushover)"},
        "retry": {"type": "integer", "default": None, "min": 30, "label": "Retry in Sekunden (Pushover, Priorität 2)"},
        "expire": {"type": "integer", "default": None, "min": 1, "max": 10800, "label": "Expire in Sekunden (Pushover, Priorität 2)"},
        "variables": {
            "type": "array",
            "default": [],
            "label": "Variablen",
            "description": "DataPoint-backed placeholders, usable in title and fallback message as '###OBS<slot>###' (e.g. '###OBS1###').",
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
    color="#e11d48",
    help_id="logic-block-notify-message",
)
