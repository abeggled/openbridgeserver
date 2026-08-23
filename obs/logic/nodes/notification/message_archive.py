"""Node definition for the ``message_archive`` function block (Meldungsarchiv)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="message_archive",
    label="Meldungsarchiv",
    category="notification",
    description="Schreibt eine Meldung in ein Meldungsarchiv. Wird automatisch ausgelöst wenn eine Nachricht am Eingang ankommt oder der Trigger wahr ist.",
    inputs=[
        port("trigger", "Trigger", "trigger"),
        port("message", "Nachricht", "trigger"),
        port("title", "Titel"),
    ],
    outputs=[port("stored", "Gespeichert", "trigger")],
    config_schema={
        "archive_id": {"type": "string", "default": "", "label": "Meldungsarchiv"},
        "title": {"type": "string", "default": "", "label": "Titel (Fallback)"},
        "message": {"type": "string", "default": "", "label": "Nachricht (Fallback)"},
        "type": {
            "type": "string",
            "enum": ["automation", "notification", "system", "security", "adapter", "diagnostic"],
            "default": "automation",
            "label": "Meldungstyp",
        },
        "severity": {
            "type": "string",
            "enum": ["info", "success", "warning", "error", "critical"],
            "default": "info",
            "label": "Schweregrad",
        },
    },
    color="#2563eb",
)
