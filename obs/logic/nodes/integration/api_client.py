"""Node definition for the ``api_client`` function block (API Client)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="api_client",
    label="API Client",
    category="integration",
    description="Sendet HTTP-Anfragen (GET/POST/PUT…) an externe APIs. Trigger-Eingang steuert die Ausführung.",
    inputs=[port("trigger", "Trigger", "trigger"), port("body", "Body")],
    outputs=[
        port("response", "Antwort"),
        port("status", "Status"),
        port("success", "Erfolg", "trigger"),
    ],
    config_schema={
        "url": {"type": "string", "default": "", "label": "URL"},
        "method": {
            "type": "string",
            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "default": "GET",
            "label": "Methode",
        },
        "content_type": {
            "type": "string",
            "enum": [
                "application/json",
                "text/plain",
                "application/x-www-form-urlencoded",
            ],
            "default": "application/json",
            "label": "Request Content-Type",
        },
        "response_type": {
            "type": "string",
            "enum": ["application/json", "text/plain"],
            "default": "application/json",
            "label": "Response Content-Typ",
        },
        "verify_ssl": {
            "type": "boolean",
            "default": True,
            "label": "SSL-Zertifikat prüfen",
        },
        "headers": {
            "type": "string",
            "default": "",
            "label": "Header (JSON-Objekt, optional)",
        },
        "headers_secret_file": {
            "type": "string",
            "default": "",
            "label": "Header-Datei (/run/secrets)",
        },
        "variables": {
            "type": "array",
            "default": [],
            "label": "Variablen",
        },
        "timeout_s": {"type": "number", "default": 10, "min": 1, "label": "Timeout (s)"},
        "auth_type": {
            "type": "string",
            "enum": ["none", "basic", "digest", "bearer"],
            "default": "none",
            "label": "Authentifizierung",
        },
        "auth_username": {
            "type": "string",
            "default": "",
            "label": "Benutzername (Basic/Digest)",
        },
        "auth_password": {
            "type": "string",
            "default": "",
            "label": "Passwort (Basic/Digest)",
            "subtype": "password",
        },
        "auth_token": {
            "type": "string",
            "default": "",
            "label": "Bearer Token",
            "subtype": "password",
        },
        "auth_token_file": {
            "type": "string",
            "default": "",
            "label": "Bearer-Token-Datei (/run/secrets)",
        },
    },
    color="#0e7490",
)
