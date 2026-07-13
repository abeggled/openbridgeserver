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
            "principal_type",
            "principal_id",
            "outcome",
            "http_method",
            "route_template",
        } <= column_names

        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log_entries'")
        index_names = {row["name"] for row in indexes}
        assert "idx_audit_log_entries_created_at" in index_names
        assert "idx_audit_log_entries_action" in index_names
        assert "idx_audit_log_entries_principal" in index_names
        assert "idx_audit_log_entries_outcome" in index_names
        assert "idx_audit_log_entries_route" in index_names
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
        assert row["principal_type"] == "anonymous"
        assert row["outcome"] == "success"
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


@pytest.mark.asyncio
async def test_principal_outcome_and_canonical_route_are_queryable():
    from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context
    from obs.api.auth import Principal

    db = Database(":memory:")
    await db.connect()
    try:
        request = Request(
            {
                "type": "http",
                "method": "PATCH",
                "path": "/api/v1/datapoints/dp-1",
                "query_string": b"",
                "headers": [(b"x-request-id", b"principal-route")],
                "client": ("127.0.0.1", 1),
            }
        )
        writer = AuditLogWriter(db, build_audit_context(request, Principal(subject="api_key:key-1", type="api_key", is_admin=False)))
        event_id = await writer.write_contract(
            "PATCH",
            "/api/v1/datapoints/{dp_id}",
            resource_id="dp-1",
            details={"capability": "datapoint.metadata.write"},
            outcome=AuditOutcome.DENIED,
        )
        row = await db.fetchone("SELECT * FROM audit_log_entries WHERE id=?", (event_id,))
        assert row is not None
        assert (row["actor"], row["principal_type"], row["principal_id"]) == ("api_key:key-1", "api_key", "key-1")
        assert (row["outcome"], row["http_method"], row["route_template"]) == (
            "denied",
            "PATCH",
            "/api/v1/datapoints/{dp_id}",
        )
        assert row["action"] == "datapoint.updated"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_atomic_success_requires_surrounding_transaction_and_secrets_are_rejected():
    from obs.api.audit import AuditContext, AuditLogWriter

    db = Database(":memory:")
    await db.connect()
    try:
        writer = AuditLogWriter(db, AuditContext("admin", None, None, None, principal_type="user", principal_id="admin"))
        with pytest.raises(ValueError, match="surrounding transaction"):
            await writer.write_contract("PUT", "/api/v1/system/settings")
        with pytest.raises(ValueError, match="sensitive audit detail"):
            await writer.write("config.updated", details={"password": "audit-secret-sentinel"})
        presence_event_id = await writer.write("config.updated", details={"has_influx_token": True})
        presence_row = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE id=?", (presence_event_id,))
        assert presence_row is not None
        assert json.loads(presence_row["details_json"]) == {"has_influx_token": True}
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE details_json LIKE '%audit-secret-sentinel%'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_atomic_contract_event_commits_and_rolls_back_with_mutation(monkeypatch):
    from obs.api.audit import AuditContext, AuditLogWriter

    db = Database(":memory:")
    await db.connect()
    try:
        writer = AuditLogWriter(db, AuditContext("admin", None, None, None, principal_type="user", principal_id="admin"))

        async with db.transaction():
            await db.execute("INSERT INTO app_settings (key, value) VALUES ('wave14.atomic', 'committed')")
            event_id = await writer.write_contract(
                "PUT",
                "/api/v1/system/settings",
                resource_id="global",
                commit=False,
            )

        row = await db.fetchone("SELECT action, resource_id FROM audit_log_entries WHERE id=?", (event_id,))
        assert row is not None
        assert (row["action"], row["resource_id"]) == ("system.settings.updated", "global")

        with pytest.raises(RuntimeError, match="force rollback"):
            async with db.transaction():
                await db.execute_and_commit("UPDATE app_settings SET value='rolled-back' WHERE key='wave14.atomic'")
                await writer.write_contract(
                    "PUT",
                    "/api/v1/system/settings",
                    resource_id="rolled-back",
                    commit=False,
                )
                raise RuntimeError("force rollback")

        settings = await db.fetchone("SELECT value FROM app_settings WHERE key='wave14.atomic'")
        assert settings is not None
        assert settings["value"] == "committed"
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE resource_id='rolled-back'") is None

        async def fail_audit(*args, **kwargs):
            raise RuntimeError("audit insert failed")

        monkeypatch.setattr(writer, "write_contract", fail_audit)
        with pytest.raises(RuntimeError, match="audit insert failed"):
            async with db.transaction():
                await db.execute("INSERT INTO app_settings (key, value) VALUES ('wave14.direct-commit', 'partial')")
                await db.commit()
                await writer.write_contract("PUT", "/api/v1/system/settings", resource_id="direct-commit", commit=False)

        assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='wave14.direct-commit'") is None
    finally:
        await db.disconnect()


def test_bulk_payload_digest_is_deterministic_and_order_sensitive():
    from obs.api.audit import audit_payload_sha256

    assert audit_payload_sha256({"ids": ["a", "b"]}) == audit_payload_sha256({"ids": ["a", "b"]})
    assert audit_payload_sha256({"ids": ["a", "b"]}) != audit_payload_sha256({"ids": ["b", "a"]})
