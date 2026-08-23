"""Node definition for the ``notify_pushover`` function block (Pushover)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="notify_pushover",
    label="Pushover",
    category="notification",
    description="Sendet eine Push-Benachrichtigung via Pushover API (api.pushover.net). Wird automatisch ausgelöst wenn eine Nachricht am Eingang ankommt.",
    inputs=[
        port("trigger", "Trigger", "trigger"),
        port("message", "Nachricht", "trigger"),
        port("url", "URL"),
        port("url_title", "URL-Titel"),
        port("image_url", "Bild-URL"),
    ],
    outputs=[port("sent", "Gesendet", "trigger")],
    config_schema={
        "app_token": {"type": "string", "default": "", "label": "App-Token"},
        "user_key": {"type": "string", "default": "", "label": "User-Key"},
        "title": {
            "type": "string",
            "default": "open bridge server",
            "label": "Titel",
        },
        "message": {
            "type": "string",
            "default": "",
            "label": "Nachricht (Fallback)",
        },
        "priority": {
            "type": "string",
            "enum": ["-1", "0", "1"],
            "default": "0",
            "label": "Priorität (-1=leise, 0=normal, 1=hoch)",
        },
        "url": {"type": "string", "default": "", "label": "URL (optional)"},
        "url_title": {
            "type": "string",
            "default": "",
            "label": "URL-Titel (optional)",
        },
        "image_url": {
            "type": "string",
            "default": "",
            "label": "Bild-URL (optional)",
        },
    },
    color="#e11d48",
    hidden_from_palette=True,
    legacy=True,
)
