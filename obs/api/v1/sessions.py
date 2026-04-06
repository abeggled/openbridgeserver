"""
Gemeinsamer In-Memory PIN-Session-Store.

token → (node_id, expires_at)

In Multi-Instance-Deployments: durch Redis oder DB-Tabelle ersetzen.
"""
from __future__ import annotations

import secrets
import time

_sessions: dict[str, tuple[str, float]] = {}


def create_session(node_id: str, expires_in: int = 3600) -> str:
    """Erstellt einen neuen Session-Token und speichert ihn."""
    token = secrets.token_urlsafe(32)
    _sessions[token] = (node_id, time.time() + expires_in)
    return token


def validate_session(token: str, node_id: str) -> bool:
    """Prüft ob der Token gültig und noch nicht abgelaufen ist."""
    entry = _sessions.get(token)
    if not entry:
        return False
    stored_node_id, expires_at = entry
    if stored_node_id != node_id:
        return False
    if time.time() > expires_at:
        del _sessions[token]
        return False
    return True
