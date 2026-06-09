"""Unit tests for minimal audit-log foundation (#585)."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from obs.db.database import Database


@pytest.mark.asyncio
async def test_db_migration_creates_audit_log_entries_table():
    db = Database(":memory:")
    await db.connect()
    try:
        columns = await db.fetchall("PRAGMA table_info(audit_log_entries)")
        column_names = {row["name"] for row in columns}
        assert {
            "id",
            "created_at",
            "actor",
            "action",
            "resource_type",
            "resource_id",
            "details_json",
            "request_id",
            "remote_addr",
            "user_agent",
        } <= column_names

        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log_entries'")
        index_names = {row["name"] for row in indexes}
        assert "idx_audit_log_entries_created_at" in index_names
        assert "idx_audit_log_entries_action" in index_names
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_writer_persists_audit_event_with_request_context():
    from obs.api.audit import AuditContext, AuditLogWriter

    db = Database(":memory:")
    await db.connect()
    try:
        writer = AuditLogWriter(
            db=db,
            context=AuditContext(
                actor="admin",
                request_id="req-123",
                remote_addr="127.0.0.1",
                user_agent="pytest-agent",
            ),
        )

        row_id = await writer.write(
            action="system.history.settings.updated",
            resource_type="history_settings",
            resource_id="global",
            details={"plugin": "sqlite"},
        )
        assert row_id > 0

        row = await db.fetchone("SELECT * FROM audit_log_entries WHERE id=?", (row_id,))
        assert row is not None
        assert row["actor"] == "admin"
        assert row["action"] == "system.history.settings.updated"
        assert row["resource_type"] == "history_settings"
        assert row["resource_id"] == "global"
        assert row["request_id"] == "req-123"
        assert row["remote_addr"] == "127.0.0.1"
        assert row["user_agent"] == "pytest-agent"
        assert json.loads(row["details_json"]) == {"plugin": "sqlite"}
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_dependency_builds_writer_context_from_request_and_user():
    from obs.api.audit import get_audit_log_writer

    db = Database(":memory:")
    await db.connect()
    try:
        request = Request(
            {
                "type": "http",
                "method": "PUT",
                "path": "/api/v1/system/history/settings",
                "query_string": b"",
                "headers": [
                    (b"user-agent", b"pytest-suite"),
                    (b"x-request-id", b"rid-42"),
                ],
                "client": ("10.0.0.8", 12345),
            }
        )

        writer = await get_audit_log_writer(request=request, current_user="alice", db=db)
        event_id = await writer.write("config.updated")

        row = await db.fetchone("SELECT * FROM audit_log_entries WHERE id=?", (event_id,))
        assert row is not None
        assert row["actor"] == "alice"
        assert row["request_id"] == "rid-42"
        assert row["remote_addr"] == "10.0.0.8"
        assert row["user_agent"] == "pytest-suite"

        anon_writer = await get_audit_log_writer(request=request, current_user=None, db=db)
        anon_event_id = await anon_writer.write("config.updated")
        anon_row = await db.fetchone("SELECT * FROM audit_log_entries WHERE id=?", (anon_event_id,))
        assert anon_row is not None
        assert anon_row["actor"] == "anonymous"
    finally:
        await db.disconnect()
