"""Integrationstests Migrations-Assistent-API (#964).

``GET /api/v1/ringbuffer/migration`` liefert Zustand + Ist-Analyse (admin-only),
``POST /api/v1/ringbuffer/migration/decision`` setzt ``keep``/``skip``/``discard``;
terminale Zustände sind unveränderlich (409). Die Session-App läuft ohne
Legacy-DB (Fresh-Install-Charakter), daher ist ``legacy`` hier ``null`` –
die Legacy-Pfade selbst sind in
``tests/unit/test_ringbuffer_legacy_migration_assistant.py`` abgedeckt.
"""

from __future__ import annotations

import uuid

import pytest

from obs.api.auth import create_access_token
from obs.db.database import get_db
from obs.ringbuffer.persisted_config import LEGACY_MIGRATION_DECISION_KEY

pytestmark = pytest.mark.integration


async def _reset_decision():
    """Entscheidungszustand zurücksetzen, damit kein Zustand in andere Tests leakt."""
    await get_db().execute("DELETE FROM app_settings WHERE key=?", (LEGACY_MIGRATION_DECISION_KEY,))
    await get_db().commit()


async def _non_admin_headers(client, auth_headers) -> dict:
    username = f"mig-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "TestPass123!", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {create_access_token(username)}"}


async def test_migration_status_requires_admin(client, auth_headers):
    user_headers = await _non_admin_headers(client, auth_headers)
    resp = await client.get("/api/v1/ringbuffer/migration", headers=user_headers)
    assert resp.status_code == 403


async def test_migration_status_shape(client, auth_headers):
    await _reset_decision()
    try:
        resp = await client.get("/api/v1/ringbuffer/migration", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {
            "decision",
            "retention_protected",
            "legacy",
            "disk_free_bytes",
            "budget_bytes",
            "estimated_copy_bytes",
            "over_budget",
            "estimated_seconds_until_budget",
            "job",
        }
        assert body["decision"] is None
        assert body["retention_protected"] is False
        assert body["legacy"] is None
        assert body["disk_free_bytes"] is None or body["disk_free_bytes"] > 0
    finally:
        await _reset_decision()


async def test_decision_roundtrip_skip_keep(client, auth_headers):
    await _reset_decision()
    try:
        resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": "skip"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["decision"] == "skipped"
        assert body["retention_protected"] is True

        # skipped ist revidierbar → keep hebt den Schutz auf.
        resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": "keep"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["decision"] == "keep"
        assert body["retention_protected"] is False
    finally:
        await _reset_decision()


async def test_discard_is_terminal(client, auth_headers):
    await _reset_decision()
    try:
        resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": "discard"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["decision"] == "discarded"

        # Terminal: jede weitere Entscheidung wird abgelehnt.
        resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": "keep"}, headers=auth_headers)
        assert resp.status_code == 409
    finally:
        await _reset_decision()


async def test_decision_rejects_unknown_value(client, auth_headers):
    resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": "explode"}, headers=auth_headers)
    assert resp.status_code == 422


async def test_decision_rejected_while_migration_job_running(client, auth_headers, monkeypatch):
    """Läuft ein Migrationsjob (``copying``), wird jede Entscheidung mit 409 abgelehnt (#968, Codex :2047).

    Ohne den Guard könnte ein ``discard`` die Legacy-Quelle entfernen, während die
    Copy-Task noch läuft und danach ``migrated`` persistiert – die Admin-Entscheidung
    raced mit dem Job.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    rb = get_optional_ringbuffer()
    if rb is None:
        pytest.skip("kein RingBuffer in dieser Session")
    await _reset_decision()
    try:
        # Der decision-Guard nutzt seit #968 (Codex :2078) ``legacy_migration_in_progress()``
        # (deckt auch das synchrone Startfenster ab) statt der reinen Progress-Phase.
        monkeypatch.setattr(rb, "legacy_migration_in_progress", lambda: True)
        for decision in ("keep", "skip", "discard"):
            resp = await client.post("/api/v1/ringbuffer/migration/decision", json={"decision": decision}, headers=auth_headers)
            assert resp.status_code == 409, f"{decision}: {resp.text}"
            assert "running" in resp.json()["detail"]
    finally:
        monkeypatch.undo()
        await _reset_decision()
