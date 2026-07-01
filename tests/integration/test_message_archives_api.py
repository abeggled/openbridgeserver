from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _archive_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def test_message_archives_require_auth(client):
    resp = await client.get("/api/v1/message-archives")
    assert resp.status_code == 401


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
