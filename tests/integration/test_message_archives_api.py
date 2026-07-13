from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from obs.api.auth import create_access_token
from obs.message_archive import MIGRATIONS

pytestmark = pytest.mark.integration


def _archive_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _write_message_archive_schema(path, *, archive_primary_key: bool = True, entry_primary_key: bool = True) -> None:
    supported_version = max(version for version, _sql in MIGRATIONS)
    archive_id_definition = "TEXT PRIMARY KEY" if archive_primary_key else "TEXT NOT NULL"
    entry_id_definition = "TEXT PRIMARY KEY" if entry_primary_key else "TEXT NOT NULL"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, '2026-01-01T00:00:00Z')", (supported_version,))
        conn.execute(
            f"""
            CREATE TABLE message_archives (
                id {archive_id_definition},
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                default_type TEXT,
                color TEXT NOT NULL DEFAULT '#3b82f6',
                retention_max_entries INTEGER,
                retention_max_age_days INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE message_archive_entries (
                id {entry_id_definition},
                archive_id TEXT NOT NULL REFERENCES message_archives(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'system',
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT 'new',
                source TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{{}}',
                acknowledged_at TEXT,
                acknowledged_by TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_archive_read_states (
                entry_id TEXT NOT NULL REFERENCES message_archive_entries(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                read_at TEXT NOT NULL,
                hidden_at TEXT,
                PRIMARY KEY (entry_id, username)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


async def _create_non_admin_headers(client, auth_headers) -> tuple[dict[str, str], str]:
    username = f"archive-user-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/auth/users",
        json={
            "username": username,
            "password": "pw-12345678",
            "is_admin": False,
            "mqtt_enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {create_access_token(username)}"}, username


async def test_message_archives_require_auth(client):
    resp = await client.get("/api/v1/message-archives")
    assert resp.status_code == 401


async def test_message_archive_rejects_ids_that_shadow_static_routes(client, auth_headers):
    resp = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={"id": "entries", "name": "Shadowed"},
    )

    assert resp.status_code == 400


async def test_message_archive_crud_entries_read_ack_and_delete(client, auth_headers):
    archive_id = _archive_id("system")

    create = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={
            "id": archive_id,
            "name": "Systemmeldungen",
            "description": "Testarchiv",
            "tags": ["system"],
            "default_type": "system",
            "color": "#0f766e",
            "retention_max_entries": 10,
            "retention_max_age_days": 30,
        },
    )
    assert create.status_code == 201, create.text
    archive = create.json()
    assert archive["id"] == archive_id
    assert archive["entry_count"] == 0
    assert archive["db_path"].endswith("archives/messages.sqlite3")

    entry_resp = await client.post(
        f"/api/v1/message-archives/{archive_id}/entries",
        headers=auth_headers,
        json={
            "type": "system",
            "severity": "warning",
            "source": "pytest",
            "title": "Backup fehlgeschlagen",
            "message": "Auto-Backup konnte nicht erstellt werden.",
            "payload": {"job": "autobackup"},
        },
    )
    assert entry_resp.status_code == 201, entry_resp.text
    entry = entry_resp.json()
    assert entry["archive_id"] == archive_id
    assert entry["is_read"] is False

    query = await client.get(
        "/api/v1/message-archives/entries",
        headers=auth_headers,
        params={"archive_id": archive_id, "severity": "warning", "read_state": "unread"},
    )
    assert query.status_code == 200, query.text
    page = query.json()
    assert page["total"] == 1
    assert page["items"][0]["title"] == "Backup fehlgeschlagen"

    read_resp = await client.post(
        f"/api/v1/message-archives/{archive_id}/entries/{entry['id']}/read",
        headers=auth_headers,
    )
    assert read_resp.status_code == 200, read_resp.text
    assert read_resp.json()["is_read"] is True
    assert read_resp.json()["status"] == "open"

    new_status_after_read = await client.get(
        "/api/v1/message-archives/entries",
        headers=auth_headers,
        params={"archive_id": archive_id, "status": "new"},
    )
    assert new_status_after_read.status_code == 200, new_status_after_read.text
    assert new_status_after_read.json()["total"] == 0

    ack_resp = await client.post(
        f"/api/v1/message-archives/{archive_id}/entries/{entry['id']}/acknowledge",
        headers=auth_headers,
    )
    assert ack_resp.status_code == 200, ack_resp.text
    assert ack_resp.json()["status"] == "acknowledged"
    assert ack_resp.json()["is_read"] is True
    assert ack_resp.json()["read_at"] is not None

    delete_without_confirm = await client.delete(f"/api/v1/message-archives/{archive_id}", headers=auth_headers)
    assert delete_without_confirm.status_code == 409
    assert delete_without_confirm.json()["detail"]["affected_entries"] == 1

    delete = await client.delete(
        f"/api/v1/message-archives/{archive_id}",
        headers=auth_headers,
        params={"confirm": "true"},
    )
    assert delete.status_code == 200, delete.text
    assert delete.json() == {"ok": True, "affected_entries": 1}


async def test_message_archive_acknowledge_broadcasts_updated_entry(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("ack-broadcast")
    broadcasted: list[dict] = []
    previous_entries: list[dict | None] = []

    async def capture_broadcast(entry, previous_entry=None):
        broadcasted.append(entry)
        previous_entries.append(previous_entry)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_entry", capture_broadcast)

    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Ack Broadcast"},
        )
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Needs acknowledgement"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]
        broadcasted.clear()
        previous_entries.clear()

        ack_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/acknowledge",
            headers=auth_headers,
        )

        assert ack_resp.status_code == 200, ack_resp.text
        assert ack_resp.json()["status"] == "acknowledged"
        assert ack_resp.json()["is_read"] is True
        assert [item["id"] for item in broadcasted] == [entry_id]
        assert broadcasted[0]["status"] == "acknowledged"
        assert broadcasted[0]["acknowledged_by"] == "admin"
        assert broadcasted[0]["is_read"] is False
        assert previous_entries[0] is not None
        assert previous_entries[0]["status"] == "new"
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_mark_read_broadcasts_updated_entry(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("read-broadcast")
    broadcasted: list[dict] = []
    previous_entries: list[dict | None] = []

    async def capture_broadcast(entry, previous_entry=None):
        broadcasted.append(entry)
        previous_entries.append(previous_entry)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_entry", capture_broadcast)

    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Read Broadcast"},
        )
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Needs reading"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]
        broadcasted.clear()
        previous_entries.clear()

        read_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/read",
            headers=auth_headers,
        )

        assert read_resp.status_code == 200, read_resp.text
        assert read_resp.json()["is_read"] is True
        assert [item["id"] for item in broadcasted] == [entry_id]
        assert broadcasted[0]["status"] == "open"
        assert broadcasted[0]["is_read"] is False
        assert previous_entries[0] is not None
        assert previous_entries[0]["status"] == "new"
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_patch_broadcasts_updated_entry(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("patch-broadcast")
    broadcasted: list[dict] = []
    previous_entries: list[dict | None] = []

    async def capture_broadcast(entry, previous_entry=None):
        broadcasted.append(entry)
        previous_entries.append(previous_entry)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_entry", capture_broadcast)

    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Patch Broadcast"},
        )
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Before", "status": "new", "severity": "info"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]
        broadcasted.clear()
        previous_entries.clear()

        patch_resp = await client.patch(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}",
            headers=auth_headers,
            json={"title": "After", "status": "closed", "severity": "warning"},
        )

        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["title"] == "After"
        assert [item["id"] for item in broadcasted] == [entry_id]
        assert broadcasted[0]["title"] == "After"
        assert broadcasted[0]["status"] == "closed"
        assert previous_entries[0] is not None
        assert previous_entries[0]["title"] == "Before"
        assert previous_entries[0]["status"] == "new"
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_clear_broadcasts_archive_refresh(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("clear-refresh")
    refreshed: list[str | None] = []

    async def capture_refresh(target_archive_id=None):
        refreshed.append(target_archive_id)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_refresh", capture_refresh)

    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Clear Refresh"},
        )
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Before clear"},
        )
        assert entry_resp.status_code == 201, entry_resp.text

        clear_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/clear",
            headers=auth_headers,
            params={"confirm": "true"},
        )

        assert clear_resp.status_code == 200, clear_resp.text
        assert clear_resp.json() == {"ok": True, "affected_entries": 1}
        assert refreshed == [archive_id]
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_delete_broadcasts_archive_refresh(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("delete-refresh")
    refreshed: list[str | None] = []

    async def capture_refresh(target_archive_id=None):
        refreshed.append(target_archive_id)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_refresh", capture_refresh)

    create = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={"id": archive_id, "name": "Delete Refresh"},
    )
    assert create.status_code == 201, create.text
    entry_resp = await client.post(
        f"/api/v1/message-archives/{archive_id}/entries",
        headers=auth_headers,
        json={"title": "Before delete"},
    )
    assert entry_resp.status_code == 201, entry_resp.text

    delete_resp = await client.delete(
        f"/api/v1/message-archives/{archive_id}",
        headers=auth_headers,
        params={"confirm": "true"},
    )

    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json() == {"ok": True, "affected_entries": 1}
    assert refreshed == [archive_id]


async def test_message_archive_update_broadcasts_archive_refresh(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("update-refresh")
    refreshed: list[str | None] = []

    async def capture_refresh(target_archive_id=None):
        refreshed.append(target_archive_id)

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_refresh", capture_refresh)

    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Update Refresh"},
        )
        assert create.status_code == 201, create.text

        patch_resp = await client.patch(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            json={"name": "Update Refresh Changed", "retention_max_entries": 1},
        )

        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["name"] == "Update Refresh Changed"
        assert refreshed == [archive_id]
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_integrity_check_and_export(client, auth_headers):
    archive_id = _archive_id("export")
    try:
        resp = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Export"},
        )
        assert resp.status_code == 201, resp.text
        await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Exportierbar", "message": "JSONL und CSV"},
        )

        integrity = await client.post("/api/v1/message-archives/integrity-check", headers=auth_headers)
        assert integrity.status_code == 200, integrity.text
        assert integrity.json()["ok"] is True

        exported = await client.get(
            f"/api/v1/message-archives/{archive_id}/export",
            headers=auth_headers,
            params={"format": "jsonl"},
        )
        assert exported.status_code == 200, exported.text
        assert "Exportierbar" in exported.text
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_integrity_check_can_run_while_degraded(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    calls = 0

    async def integrity_check(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            self.status = "degraded"
            self.last_error = "simulated failure"
            return {"ok": False, "result": "simulated failure", "path": self.path, "status": self.status}
        self.status = "ok"
        self.last_error = None
        return {"ok": True, "result": "ok", "path": self.path, "status": self.status}

    monkeypatch.setattr(message_archives_api.MessageArchiveStore, "integrity_check", integrity_check)

    first = await client.post("/api/v1/message-archives/integrity-check", headers=auth_headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "degraded"

    second = await client.post("/api/v1/message-archives/integrity-check", headers=auth_headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ok"
    assert calls == 2


async def test_message_archive_database_export_and_import(client, auth_headers):
    archive_id = _archive_id("db-export")
    transient_archive_id = _archive_id("db-transient")
    create = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={"id": archive_id, "name": "DB Export"},
    )
    assert create.status_code == 201, create.text
    entry = await client.post(
        f"/api/v1/message-archives/{archive_id}/entries",
        headers=auth_headers,
        json={"title": "Bleibt erhalten"},
    )
    assert entry.status_code == 201, entry.text

    exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
    assert exported.status_code == 200, exported.text
    assert exported.content.startswith(b"SQLite format 3\x00")

    transient = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={"id": transient_archive_id, "name": "Transient"},
    )
    assert transient.status_code == 201, transient.text

    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["ok"] is True

    restored = await client.get(f"/api/v1/message-archives/{archive_id}", headers=auth_headers)
    assert restored.status_code == 200, restored.text
    missing = await client.get(f"/api/v1/message-archives/{transient_archive_id}", headers=auth_headers)
    assert missing.status_code == 404

    await client.delete(
        f"/api/v1/message-archives/{archive_id}",
        headers=auth_headers,
        params={"confirm": "true"},
    )


async def test_message_archive_database_import_broadcasts_refresh(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("db-import-refresh")
    refreshed: list[str | None] = []

    async def capture_refresh(target_archive_id=None):
        refreshed.append(target_archive_id)

    create = await client.post(
        "/api/v1/message-archives",
        headers=auth_headers,
        json={"id": archive_id, "name": "DB Import Refresh"},
    )
    assert create.status_code == 201, create.text
    exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
    assert exported.status_code == 200, exported.text

    monkeypatch.setattr(message_archives_api, "broadcast_message_archive_refresh", capture_refresh)
    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
    )

    assert imported.status_code == 200, imported.text
    assert refreshed == [None]
    await client.delete(
        f"/api/v1/message-archives/{archive_id}",
        headers=auth_headers,
        params={"confirm": "true"},
    )


async def test_message_archive_database_import_rejects_malformed_schema(client, auth_headers, tmp_path):
    malformed = tmp_path / "malformed-message-archives.sqlite"
    conn = sqlite3.connect(malformed)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-01-01T00:00:00Z')")
        conn.execute("CREATE TABLE message_archives (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE message_archive_entries (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE message_archive_read_states (entry_id TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()

    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", malformed.read_bytes(), "application/octet-stream")},
    )

    assert imported.status_code == 400


async def test_message_archive_database_import_rejects_missing_foreign_key_cascade(client, auth_headers, tmp_path):
    malformed = tmp_path / "malformed-message-archives-fk.sqlite"
    conn = sqlite3.connect(malformed)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-01-01T00:00:00Z')")
        conn.execute(
            """
            CREATE TABLE message_archives (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                default_type TEXT,
                color TEXT NOT NULL DEFAULT '#3b82f6',
                retention_max_entries INTEGER,
                retention_max_age_days INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_archive_entries (
                id TEXT PRIMARY KEY,
                archive_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'system',
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT 'new',
                source TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                acknowledged_at TEXT,
                acknowledged_by TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_archive_read_states (
                entry_id TEXT NOT NULL,
                username TEXT NOT NULL,
                read_at TEXT NOT NULL,
                hidden_at TEXT,
                PRIMARY KEY (entry_id, username)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", malformed.read_bytes(), "application/octet-stream")},
    )

    assert imported.status_code == 400


async def test_message_archive_database_import_rejects_missing_read_state_unique_key(client, auth_headers, tmp_path):
    malformed = tmp_path / "malformed-message-archives-read-state-key.sqlite"
    supported_version = max(version for version, _sql in MIGRATIONS)
    conn = sqlite3.connect(malformed)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, '2026-01-01T00:00:00Z')", (supported_version,))
        conn.execute(
            """
            CREATE TABLE message_archives (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                default_type TEXT,
                color TEXT NOT NULL DEFAULT '#3b82f6',
                retention_max_entries INTEGER,
                retention_max_age_days INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_archive_entries (
                id TEXT PRIMARY KEY,
                archive_id TEXT NOT NULL REFERENCES message_archives(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'system',
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT 'new',
                source TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                acknowledged_at TEXT,
                acknowledged_by TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_archive_read_states (
                entry_id TEXT NOT NULL REFERENCES message_archive_entries(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                read_at TEXT NOT NULL,
                hidden_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", malformed.read_bytes(), "application/octet-stream")},
    )

    assert imported.status_code == 400


@pytest.mark.parametrize(("archive_primary_key", "entry_primary_key"), [(False, True), (True, False)])
async def test_message_archive_database_import_rejects_missing_primary_keys(
    client,
    auth_headers,
    tmp_path,
    archive_primary_key,
    entry_primary_key,
):
    malformed = tmp_path / f"malformed-message-archives-primary-key-{archive_primary_key}-{entry_primary_key}.sqlite"
    _write_message_archive_schema(malformed, archive_primary_key=archive_primary_key, entry_primary_key=entry_primary_key)

    imported = await client.post(
        "/api/v1/message-archives/import/db",
        headers=auth_headers,
        files={"file": ("message-archives.sqlite", malformed.read_bytes(), "application/octet-stream")},
    )

    assert imported.status_code == 400


async def test_message_archive_database_import_rejects_foreign_key_violations(client, auth_headers, tmp_path):
    archive_id = _archive_id("db-fk")
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "DB FK"})
        assert create.status_code == 201, create.text

        exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
        assert exported.status_code == 200, exported.text
        malformed = tmp_path / "malformed-message-archives-fk-violation.sqlite"
        malformed.write_bytes(exported.content)

        conn = sqlite3.connect(malformed)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                INSERT INTO message_archive_entries (
                    id, archive_id, created_at, updated_at, type, severity, status, source, title, message, payload,
                    acknowledged_at, acknowledged_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "broken-entry",
                    "missing-archive",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    "system",
                    "info",
                    "new",
                    "pytest",
                    "Broken",
                    "Missing archive",
                    "{}",
                    None,
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        imported = await client.post(
            "/api/v1/message-archives/import/db",
            headers=auth_headers,
            files={"file": ("message-archives.sqlite", malformed.read_bytes(), "application/octet-stream")},
        )

        assert imported.status_code == 400
    finally:
        await client.delete(f"/api/v1/message-archives/{archive_id}", headers=auth_headers, params={"confirm": "true"})


async def test_message_archive_entries_allow_public_page_scoped_reads(client, auth_headers):
    archive_id = _archive_id("page")
    page_id = None
    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Page Archive"},
        )
        assert create.status_code == 201, create.text
        warning_entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"severity": "warning", "title": "Sichtbar"},
        )
        assert warning_entry.status_code == 201, warning_entry.text
        info_entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"severity": "info", "title": "Nicht sichtbar"},
        )
        assert info_entry.status_code == 201, info_entry.text

        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Public archive page", "type": "PAGE", "access": "public"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id], "severities": ["warning"]},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text

        query = await client.get(
            "/api/v1/message-archives/entries",
            headers={"X-Page-Id": page_id},
            params={"archive_id": archive_id},
        )
        assert query.status_code == 200, query.text
        body = query.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Sichtbar"

        stale_bearer_query = await client.get(
            "/api/v1/message-archives/entries",
            headers={"X-Page-Id": page_id, "Authorization": "Bearer stale.invalid.token"},
            params={"archive_id": archive_id},
        )
        assert stale_bearer_query.status_code == 200, stale_bearer_query.text

        archive_list = await client.get("/api/v1/message-archives", headers={"X-Page-Id": page_id})
        assert archive_list.status_code == 200, archive_list.text
        archive = archive_list.json()[0]
        assert "db_path" not in archive
        assert "db_status" not in archive
        assert archive["entry_count"] == 1
        assert archive["oldest_entry_at"] == warning_entry.json()["created_at"]
        assert archive["newest_entry_at"] == warning_entry.json()["created_at"]
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_page_scoped_reads_preserve_per_widget_or_predicates(client, auth_headers):
    archive_a = _archive_id("page-a")
    archive_b = _archive_id("page-b")
    page_id = None
    try:
        for archive_id, name in ((archive_a, "A"), (archive_b, "B")):
            resp = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": name})
            assert resp.status_code == 201, resp.text

        for archive_id, type_, title in (
            (archive_a, "security", "A security allowed"),
            (archive_a, "notification", "A notification blocked"),
            (archive_b, "notification", "B notification allowed"),
            (archive_b, "security", "B security blocked"),
        ):
            resp = await client.post(
                f"/api/v1/message-archives/{archive_id}/entries",
                headers=auth_headers,
                json={"type": type_, "title": title},
            )
            assert resp.status_code == 201, resp.text

        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Public archive OR page", "type": "PAGE", "access": "public"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget-a",
                    "type": "MessageArchive",
                    "name": "Archiv A",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_a], "types": ["security"]},
                },
                {
                    "id": "archive-widget-b",
                    "type": "MessageArchive",
                    "name": "Archiv B",
                    "x": 4,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_b], "types": ["notification"]},
                },
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text

        query = await client.get("/api/v1/message-archives/entries", headers={"X-Page-Id": page_id})
        assert query.status_code == 200, query.text
        assert {item["title"] for item in query.json()["items"]} == {"A security allowed", "B notification allowed"}
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        for archive_id in (archive_a, archive_b):
            await client.delete(
                f"/api/v1/message-archives/{archive_id}",
                headers=auth_headers,
                params={"confirm": "true"},
            )


async def test_message_archive_page_scope_limits_non_admin_bearer_reads(client, auth_headers):
    archive_id = _archive_id("page-nonadmin")
    other_archive_id = _archive_id("page-hidden")
    page_id = None
    username = None
    try:
        for current_archive_id, name in ((archive_id, "Visible"), (other_archive_id, "Hidden")):
            resp = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": current_archive_id, "name": name})
            assert resp.status_code == 201, resp.text
            entry = await client.post(
                f"/api/v1/message-archives/{current_archive_id}/entries",
                headers=auth_headers,
                json={"title": name},
            )
            assert entry.status_code == 201, entry.text

        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Public archive non-admin page", "type": "PAGE", "access": "public"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id]},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text
        non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)

        unrestricted = await client.get("/api/v1/message-archives/entries", headers=non_admin_headers)
        assert unrestricted.status_code == 200, unrestricted.text
        assert {item["title"] for item in unrestricted.json()["items"]} >= {"Visible", "Hidden"}

        unrestricted_archives = await client.get("/api/v1/message-archives", headers=non_admin_headers)
        assert unrestricted_archives.status_code == 200, unrestricted_archives.text
        assert "db_path" not in unrestricted_archives.json()[0]
        assert "db_status" not in unrestricted_archives.json()[0]

        scoped = await client.get(
            "/api/v1/message-archives/entries",
            headers={**non_admin_headers, "X-Page-Id": page_id},
        )
        assert scoped.status_code == 200, scoped.text
        assert {item["title"] for item in scoped.json()["items"]} == {"Visible"}
    finally:
        if username:
            await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        for current_archive_id in (archive_id, other_archive_id):
            await client.delete(
                f"/api/v1/message-archives/{current_archive_id}",
                headers=auth_headers,
                params={"confirm": "true"},
            )


async def test_message_archive_user_scoped_page_allows_assigned_user(client, auth_headers):
    archive_id = _archive_id("page-user")
    page_id = None
    username = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "User"})
        assert create.status_code == 201, create.text
        entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Nur fuer Benutzer"},
        )
        assert entry.status_code == 201, entry.text

        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "User archive page", "type": "PAGE", "access": "user"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id]},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text
        non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)
        assign = await client.put(f"/api/v1/visu/nodes/{page_id}/users", headers=auth_headers, json={"usernames": [username]})
        assert assign.status_code == 204, assign.text

        scoped = await client.get(
            "/api/v1/message-archives/entries",
            headers={**non_admin_headers, "X-Page-Id": page_id},
        )
        assert scoped.status_code == 200, scoped.text
        assert [item["title"] for item in scoped.json()["items"]] == ["Nur fuer Benutzer"]
    finally:
        if username:
            await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_page_scoped_read_and_ack_require_widget_permissions(client, auth_headers):
    archive_id = _archive_id("page-actions")
    page_id = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Actions"})
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Protected action"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]
        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Public archive action page", "type": "PAGE", "access": "public"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id], "allow_read": False, "allow_acknowledge": False},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text

        read_resp = await client.post(f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/read", headers={"X-Page-Id": page_id})
        assert read_resp.status_code == 404
        ack_resp = await client.post(f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/acknowledge", headers={"X-Page-Id": page_id})
        assert ack_resp.status_code == 404
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_readonly_page_denies_read_and_ack_mutations(client, auth_headers):
    archive_id = _archive_id("page-readonly")
    page_id = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Readonly"})
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Readonly action"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]
        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Readonly archive page", "type": "PAGE", "access": "readonly"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id], "allow_read": True, "allow_acknowledge": True},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text

        read_resp = await client.post(f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/read", headers={"X-Page-Id": page_id})
        assert read_resp.status_code == 403
        ack_resp = await client.post(f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/acknowledge", headers={"X-Page-Id": page_id})
        assert ack_resp.status_code == 403
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_entries_accept_multiple_filter_values(client, auth_headers):
    system_archive_id = _archive_id("system")
    adapter_archive_id = _archive_id("adapter")
    security_archive_id = _archive_id("security")
    for archive_id, name in (
        (system_archive_id, "System"),
        (adapter_archive_id, "Adapter"),
        (security_archive_id, "Security"),
    ):
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": name},
        )
        assert create.status_code == 201, create.text

    try:
        for archive_id, type_, severity, source, title in (
            (system_archive_id, "system", "info", "core", "System"),
            (adapter_archive_id, "adapter", "warning", "knx", "Adapter"),
            (security_archive_id, "security", "critical", "auth", "Security"),
        ):
            resp = await client.post(
                f"/api/v1/message-archives/{archive_id}/entries",
                headers=auth_headers,
                json={"type": type_, "severity": severity, "source": source, "title": title},
            )
            assert resp.status_code == 201, resp.text

        query = await client.get(
            "/api/v1/message-archives/entries",
            headers=auth_headers,
            params={
                "archive_id": f"{system_archive_id},{adapter_archive_id}",
                "type": "system,adapter",
                "severity": "info,warning",
                "source": "core,knx",
            },
        )
        assert query.status_code == 200, query.text
        page = query.json()
        assert page["total"] == 2
        assert {item["title"] for item in page["items"]} == {"System", "Adapter"}
    finally:
        for archive_id in (system_archive_id, adapter_archive_id, security_archive_id):
            await client.delete(
                f"/api/v1/message-archives/{archive_id}",
                headers=auth_headers,
                params={"confirm": "true"},
            )


async def test_message_archive_path_operations_reject_malformed_archive_ids(client, auth_headers):
    resp = await client.get("/api/v1/message-archives/bad%20id", headers=auth_headers)
    assert resp.status_code == 400


async def test_single_message_archive_entries_reject_malformed_time_filters(client, auth_headers):
    archive_id = _archive_id("time-filter")
    try:
        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Time Filter"},
        )
        assert create.status_code == 201, create.text

        resp = await client.get(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            params={"from": "not-a-date"},
        )

        assert resp.status_code == 400
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_entry_patch_requires_admin(client, auth_headers):
    archive_id = _archive_id("patch")
    username = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Patch"})
        assert create.status_code == 201, create.text
        entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Original"},
        )
        assert entry.status_code == 201, entry.text
        entry_id = entry.json()["id"]
        non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)

        forbidden = await client.patch(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}",
            headers=non_admin_headers,
            json={"title": "Changed"},
        )
        assert forbidden.status_code == 403

        allowed = await client.patch(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}",
            headers=auth_headers,
            json={"title": "Changed"},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["title"] == "Changed"
    finally:
        if username:
            await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_entry_create_requires_existing_archive_for_non_admin(client, auth_headers):
    archive_id = _archive_id("missing-entry")
    username = None
    try:
        non_admin_headers, username = await _create_non_admin_headers(client, auth_headers)

        resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=non_admin_headers,
            json={"title": "Should not create archive"},
        )

        assert resp.status_code == 404
        missing = await client.get(f"/api/v1/message-archives/{archive_id}", headers=auth_headers)
        assert missing.status_code == 404
    finally:
        if username:
            await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_message_archive_patch_rejects_null_required_fields(client, auth_headers):
    archive_id = _archive_id("patch-null")
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Patch Null"})
        assert create.status_code == 201, create.text

        resp = await client.patch(f"/api/v1/message-archives/{archive_id}", headers=auth_headers, json={"name": None})

        assert resp.status_code == 422
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_patch_accepts_non_null_required_fields(client, auth_headers):
    archive_id = _archive_id("patch-valid")
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Patch Valid"})
        assert create.status_code == 201, create.text

        resp = await client.patch(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            json={"name": "Updated", "description": "Neue Beschreibung", "color": "#123456"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Updated"
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_admin_paths_cover_update_clear_single_query_and_csv_export(client, auth_headers):
    archive_id = _archive_id("admin-paths")
    try:
        invalid = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": "Bad ID", "name": "Invalid"})
        assert invalid.status_code == 400

        create = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Admin Paths", "default_type": "system"},
        )
        assert create.status_code == 201, create.text

        duplicate = await client.post(
            "/api/v1/message-archives",
            headers=auth_headers,
            json={"id": archive_id, "name": "Duplicate"},
        )
        assert duplicate.status_code == 409

        entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={
                "type": "adapter",
                "severity": "error",
                "status": "new",
                "source": "knx",
                "title": "CSV Export",
                "message": "Single archive query",
            },
        )
        assert entry.status_code == 201, entry.text

        update = await client.patch(
            f"/api/v1/message-archives/{archive_id.upper()}",
            headers=auth_headers,
            json={
                "name": "Admin Paths Updated",
                "description": "Updated",
                "tags": ["ops", "csv"],
                "default_type": None,
                "color": "#654321",
                "retention_max_entries": 20,
                "retention_max_age_days": 5,
            },
        )
        assert update.status_code == 200, update.text
        assert update.json()["id"] == archive_id
        assert update.json()["default_type"] is None

        single_query = await client.get(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            params={
                "from": "2000-01-01T00:00:00Z",
                "to": "2999-01-01T00:00:00Z",
                "status": "new,open",
                "read_state": "unread",
                "type": "adapter,system",
                "severity": "error,warning",
                "source": "knx,core",
                "q": "CSV",
                "limit": 10,
                "offset": 0,
                "sort": "asc",
            },
        )
        assert single_query.status_code == 200, single_query.text
        assert single_query.json()["total"] == 1

        csv_export = await client.get(
            f"/api/v1/message-archives/{archive_id}/export",
            headers=auth_headers,
            params={"format": "csv"},
        )
        assert csv_export.status_code == 200, csv_export.text
        assert "CSV Export" in csv_export.text
        assert csv_export.headers["content-disposition"] == f'attachment; filename="{archive_id}.csv"'

        missing_export = await client.get("/api/v1/message-archives/missing-export/export", headers=auth_headers)
        assert missing_export.status_code == 404

        missing_update = await client.patch(
            "/api/v1/message-archives/missing-update",
            headers=auth_headers,
            json={"name": "Missing"},
        )
        assert missing_update.status_code == 404

        clear_without_confirm = await client.post(f"/api/v1/message-archives/{archive_id}/clear", headers=auth_headers)
        assert clear_without_confirm.status_code == 409
        assert clear_without_confirm.json()["detail"]["affected_entries"] == 1

        clear = await client.post(
            f"/api/v1/message-archives/{archive_id}/clear",
            headers=auth_headers,
            params={"confirm": "true"},
        )
        assert clear.status_code == 200, clear.text
        assert clear.json() == {"ok": True, "affected_entries": 1}

        missing_clear = await client.post("/api/v1/message-archives/missing-clear/clear", headers=auth_headers)
        assert missing_clear.status_code == 404
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_api_key_read_access_and_invalid_key(client, auth_headers):
    archive_id = _archive_id("api-key")
    key_id = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "API Key"})
        assert create.status_code == 201, create.text
        entry = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Readable with API key"},
        )
        assert entry.status_code == 201, entry.text

        key_resp = await client.post("/api/v1/auth/apikeys", headers=auth_headers, json={"name": f"archive-{uuid.uuid4().hex[:6]}"})
        assert key_resp.status_code == 201, key_resp.text
        key_id = key_resp.json()["id"]
        api_key = key_resp.json()["key"]

        invalid = await client.get("/api/v1/message-archives", headers={"X-API-Key": "obs_" + "0" * 64})
        assert invalid.status_code == 401

        archives = await client.get("/api/v1/message-archives", headers={"X-API-Key": api_key})
        assert archives.status_code == 200, archives.text
        visible = [archive for archive in archives.json() if archive["id"] == archive_id]
        assert visible
        assert "db_path" not in visible[0]
        assert "db_status" not in visible[0]

        entries = await client.get(
            "/api/v1/message-archives/entries",
            headers={"X-API-Key": api_key},
            params={"archive_id": archive_id},
        )
        assert entries.status_code == 200, entries.text
        assert entries.json()["total"] == 1

        api_keys = await client.get("/api/v1/auth/apikeys", headers=auth_headers)
        assert api_keys.status_code == 200, api_keys.text
        used_key = next(item for item in api_keys.json() if item["id"] == key_id)
        assert used_key["last_used_at"] is not None
    finally:
        if key_id:
            await client.delete(f"/api/v1/auth/apikeys/{key_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_page_scope_denies_unconfigured_and_hidden_archives(client, auth_headers):
    visible_archive_id = _archive_id("visible-page")
    hidden_archive_id = _archive_id("hidden-page")
    empty_page_id = None
    scoped_page_id = None
    try:
        for archive_id, name in ((visible_archive_id, "Visible"), (hidden_archive_id, "Hidden")):
            create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": name})
            assert create.status_code == 201, create.text

        empty_page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Archive page without widget", "type": "PAGE", "access": "public"},
        )
        assert empty_page.status_code == 201, empty_page.text
        empty_page_id = empty_page.json()["id"]
        empty_save = await client.put(
            f"/api/v1/visu/pages/{empty_page_id}",
            headers=auth_headers,
            json={"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []},
        )
        assert empty_save.status_code == 204, empty_save.text

        denied_unconfigured = await client.get("/api/v1/message-archives", headers={"X-Page-Id": empty_page_id})
        assert denied_unconfigured.status_code == 403

        scoped_page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Archive page with visible widget", "type": "PAGE", "access": "public"},
        )
        assert scoped_page.status_code == 201, scoped_page.text
        scoped_page_id = scoped_page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [visible_archive_id]},
                }
            ],
        }
        scoped_save = await client.put(f"/api/v1/visu/pages/{scoped_page_id}", headers=auth_headers, json=page_config)
        assert scoped_save.status_code == 204, scoped_save.text

        hidden = await client.get(f"/api/v1/message-archives/{hidden_archive_id}", headers={"X-Page-Id": scoped_page_id})
        assert hidden.status_code == 403
    finally:
        for page_id in (empty_page_id, scoped_page_id):
            if page_id:
                await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        for archive_id in (visible_archive_id, hidden_archive_id):
            await client.delete(
                f"/api/v1/message-archives/{archive_id}",
                headers=auth_headers,
                params={"confirm": "true"},
            )


async def test_message_archive_page_scoped_read_and_ack_allow_widget_permissions(client, auth_headers):
    archive_id = _archive_id("page-allow")
    page_id = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Allow Actions"})
        assert create.status_code == 201, create.text
        entry_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries",
            headers=auth_headers,
            json={"title": "Allowed action"},
        )
        assert entry_resp.status_code == 201, entry_resp.text
        entry_id = entry_resp.json()["id"]

        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Public archive allowed action page", "type": "PAGE", "access": "public"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        page_config = {
            "grid_cols": 12,
            "grid_row_height": 80,
            "background": None,
            "widgets": [
                {
                    "id": "archive-widget",
                    "type": "MessageArchive",
                    "name": "Archiv",
                    "x": 0,
                    "y": 0,
                    "w": 4,
                    "h": 4,
                    "config": {"archive_ids": [archive_id], "allow_read": True, "allow_acknowledge": True},
                }
            ],
        }
        save = await client.put(f"/api/v1/visu/pages/{page_id}", headers=auth_headers, json=page_config)
        assert save.status_code == 204, save.text

        read_resp = await client.post(f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/read", headers={"X-Page-Id": page_id})
        assert read_resp.status_code == 403, read_resp.text

        ack_resp = await client.post(
            f"/api/v1/message-archives/{archive_id}/entries/{entry_id}/acknowledge",
            headers={"X-Page-Id": page_id},
        )
        assert ack_resp.status_code == 200, ack_resp.text
        assert ack_resp.json()["status"] == "acknowledged"
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_protected_page_requires_session_token(client, auth_headers):
    archive_id = _archive_id("protected-page")
    page_id = None
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Protected"})
        assert create.status_code == 201, create.text
        page = await client.post(
            "/api/v1/visu/nodes",
            headers=auth_headers,
            json={"name": "Protected archive page", "type": "PAGE", "access": "protected", "access_pin": "1234"},
        )
        assert page.status_code == 201, page.text
        page_id = page.json()["id"]
        save = await client.put(
            f"/api/v1/visu/pages/{page_id}",
            headers=auth_headers,
            json={
                "grid_cols": 12,
                "grid_row_height": 80,
                "background": None,
                "widgets": [
                    {
                        "id": "archive-widget",
                        "type": "MessageArchive",
                        "name": "Archiv",
                        "x": 0,
                        "y": 0,
                        "w": 4,
                        "h": 4,
                        "config": {"archive_ids": [archive_id]},
                    }
                ],
            },
        )
        assert save.status_code == 204, save.text

        denied = await client.get("/api/v1/message-archives/entries", headers={"X-Page-Id": page_id})
        assert denied.status_code == 401

        auth_resp = await client.post(f"/api/v1/visu/nodes/{page_id}/auth", json={"pin": "1234"})
        assert auth_resp.status_code == 200, auth_resp.text
        allowed = await client.get(
            "/api/v1/message-archives/entries",
            headers={"X-Page-Id": page_id, "X-Session-Token": auth_resp.json()["session_token"]},
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        if page_id:
            await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_database_import_rolls_back_when_imported_db_fails_integrity(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("import-rollback")
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Rollback"})
        assert create.status_code == 201, create.text
        exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
        assert exported.status_code == 200, exported.text

        async def failing_integrity_check(self):
            return {"ok": False, "result": "not ok", "path": self.path, "status": "error"}

        monkeypatch.setattr(message_archives_api.MessageArchiveStore, "integrity_check", failing_integrity_check)

        imported = await client.post(
            "/api/v1/message-archives/import/db",
            headers=auth_headers,
            files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
        )
        assert imported.status_code == 500

        restored = await client.get(f"/api/v1/message-archives/{archive_id}", headers=auth_headers)
        assert restored.status_code == 200, restored.text
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_database_import_preserves_backup_when_rollback_copy_fails(client, auth_headers, monkeypatch):
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("import-rollback-copy")
    preserved_backups: list[str] = []
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Rollback Copy"})
        assert create.status_code == 201, create.text
        exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
        assert exported.status_code == 200, exported.text

        async def failing_integrity_check(self):
            return {"ok": False, "result": "not ok", "path": self.path, "status": "error"}

        original_copy2 = message_archives_api.shutil.copy2

        def fail_restore_copy(src, dst, *args, **kwargs):
            if str(src).endswith(".pre-import.bak"):
                preserved_backups.append(str(src))
                raise OSError("restore failed")
            return original_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(message_archives_api.MessageArchiveStore, "integrity_check", failing_integrity_check)
        monkeypatch.setattr(message_archives_api.shutil, "copy2", fail_restore_copy)

        imported = await client.post(
            "/api/v1/message-archives/import/db",
            headers=auth_headers,
            files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
        )

        assert imported.status_code == 500
        assert preserved_backups
        assert all(Path(path).exists() for path in preserved_backups)
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


@pytest.mark.parametrize(
    "unlink_error",
    [None, FileNotFoundError, OSError],
    ids=["clean-unlink", "already-removed", "unlink-fails"],
)
async def test_message_archive_database_import_recovers_when_no_prior_backup_exists(client, auth_headers, monkeypatch, unlink_error):
    """When the archive DB never existed before (no pre-import backup), a failed import
    must not leave the store pointed at the now-known-bad imported file — it should delete
    it and reconnect to a fresh, usable store instead of raising unhandled on reconnect.
    Covers the delete succeeding, the file already being gone (race), and delete failing
    outright (permissions) — none of these may crash the recovery path."""
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("import-no-backup")
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "No Backup"})
        assert create.status_code == 201, create.text
        exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
        assert exported.status_code == 200, exported.text

        store = message_archives_api.get_message_archive_store()
        target_path = store.path
        real_exists = os.path.exists
        real_unlink = os.unlink

        def fake_exists(path, *args, **kwargs):
            if str(path) == target_path:
                return False
            return real_exists(path, *args, **kwargs)

        def fake_unlink(path, *args, **kwargs):
            if str(path) == target_path and unlink_error is not None:
                raise unlink_error("simulated")
            return real_unlink(path, *args, **kwargs)

        async def failing_integrity_check(self):
            return {"ok": False, "result": "not ok", "path": self.path, "status": "error"}

        monkeypatch.setattr(message_archives_api.MessageArchiveStore, "integrity_check", failing_integrity_check)
        monkeypatch.setattr(message_archives_api.os.path, "exists", fake_exists)
        monkeypatch.setattr(message_archives_api.os, "unlink", fake_unlink)

        imported = await client.post(
            "/api/v1/message-archives/import/db",
            headers=auth_headers,
            files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
        )
        assert imported.status_code == 500
        assert not real_exists(f"{target_path}.pre-import.bak")

        monkeypatch.undo()

        # Store must have recovered to a connected, usable state — not left pointed at
        # the corrupted imported file or raising raw on subsequent requests.
        listed = await client.get("/api/v1/message-archives", headers=auth_headers)
        assert listed.status_code == 200, listed.text
    finally:
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )


async def test_message_archive_database_import_returns_500_when_recovery_reconnect_fails(client, auth_headers, monkeypatch):
    """If the store can't even be reconnected after a failed import (e.g. disk trouble),
    the endpoint must return a clean 500 instead of letting the reconnect exception
    propagate raw. The store fixture is session-scoped, so this test restores it in
    `finally` no matter the outcome to avoid breaking every later message-archive test."""
    from obs.api.v1 import message_archives as message_archives_api

    archive_id = _archive_id("import-recovery-fail")
    store = message_archives_api.get_message_archive_store()
    try:
        create = await client.post("/api/v1/message-archives", headers=auth_headers, json={"id": archive_id, "name": "Recovery Fail"})
        assert create.status_code == 201, create.text
        exported = await client.get("/api/v1/message-archives/export/db", headers=auth_headers)
        assert exported.status_code == 200, exported.text

        target_path = store.path
        real_exists = os.path.exists

        def fake_exists(path, *args, **kwargs):
            if str(path) == target_path:
                return False
            return real_exists(path, *args, **kwargs)

        async def failing_integrity_check(self):
            return {"ok": False, "result": "not ok", "path": self.path, "status": "error"}

        original_connect = message_archives_api.MessageArchiveStore.connect
        connect_calls = 0

        async def flaky_connect(self):
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls == 1:
                await original_connect(self)
                return
            raise RuntimeError("simulated reconnect failure")

        monkeypatch.setattr(message_archives_api.MessageArchiveStore, "integrity_check", failing_integrity_check)
        monkeypatch.setattr(message_archives_api.MessageArchiveStore, "connect", flaky_connect)
        monkeypatch.setattr(message_archives_api.os.path, "exists", fake_exists)

        imported = await client.post(
            "/api/v1/message-archives/import/db",
            headers=auth_headers,
            files={"file": ("message-archives.sqlite", exported.content, "application/octet-stream")},
        )
        assert imported.status_code == 500
    finally:
        monkeypatch.undo()
        if not store.is_connected:
            await store.connect()
        message_archives_api.activate_message_archive_service(store)
        await client.delete(
            f"/api/v1/message-archives/{archive_id}",
            headers=auth_headers,
            params={"confirm": "true"},
        )
