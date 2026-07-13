from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1.application_audit import audit_application_contract, write_application_success
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import AuditEffect, AuditMode, ROUTE_SECURITY_CONTRACTS
from obs.db.database import Database
from tools.check_authz_contract import collect_live_routes


_APPLICATION_PREFIXES = ("/api/v1/logic", "/api/v1/ringbuffer", "/api/v1/visu")


def test_all_26_application_mutations_are_bound_to_their_declared_contract() -> None:
    expected = {
        signature
        for signature, category in ROUTE_CLASSIFICATIONS.items()
        if category == "config_mutation" and signature[1].startswith(_APPLICATION_PREFIXES)
    }
    assert len(expected) == 26

    live = collect_live_routes()
    assert {signature for signature in expected if getattr(live[signature].endpoint, "__audit_contract__", None) == signature} == expected


def test_bulk_and_multi_scope_contracts_allow_only_stable_summary_fields() -> None:
    assert ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/logic/graphs")].allowed_detail_fields == {
        "control_class",
        "creator_grant_role",
        "delegated",
        "enabled_persisted",
        "enabled_requested",
        "operation",
        "reason",
    }
    assert ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/logic/graphs/{graph_id}/run")].allowed_detail_fields == {
        "control_class",
        "denied_checks",
        "output_count",
        "warning_count",
    }
    assert ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/visu/nodes/import")].allowed_detail_fields == {
        "node_count",
        "operation",
    }
    assert ROUTE_SECURITY_CONTRACTS[("PATCH", "/api/v1/ringbuffer/filtersets/order")].allowed_detail_fields == {
        "item_count",
        "payload_sha256",
    }
    ringbuffer_config = ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/ringbuffer/config")]
    assert (ringbuffer_config.audit_mode, ringbuffer_config.audit_effect) == (
        AuditMode.RESULT,
        AuditEffect.EXTERNAL_MUTATION,
    )


@pytest.mark.asyncio
async def test_denial_wrapper_writes_canonical_outcome_without_sensitive_request_data() -> None:
    db = Database(":memory:")
    await db.connect()
    principal = Principal(subject="api_key:key-1", type="api_key", is_admin=False)

    @audit_application_contract("PUT", "/api/v1/ringbuffer/export/settings", principal_param="principal")
    async def denied(*, principal: Principal, db: Database) -> None:
        raise HTTPException(403, "not authorized")

    try:
        with pytest.raises(HTTPException, match="not authorized"):
            await denied(principal=principal, db=db)
        row = await db.fetchone("SELECT * FROM audit_log_entries")
        assert row is not None
        assert (row["action"], row["outcome"]) == ("ringbuffer.export.settings_updated", "denied")
        assert (row["principal_type"], row["principal_id"]) == ("api_key", "key-1")
        assert row["details_json"] == "{}"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 422])
async def test_non_denial_http_errors_are_audited_as_failed(status_code: int) -> None:
    db = Database(":memory:")
    await db.connect()

    @audit_application_contract("PUT", "/api/v1/ringbuffer/export/settings", principal_param="principal")
    async def failed(*, principal: Principal, db: Database) -> None:
        raise HTTPException(status_code, "invalid operation")

    try:
        with pytest.raises(HTTPException, match="invalid operation"):
            await failed(principal=Principal(subject="alice", type="user", is_admin=False), db=db)
        row = await db.fetchone("SELECT action, outcome FROM audit_log_entries")
        assert (row["action"], row["outcome"]) == ("ringbuffer.export.settings_updated", "failed")
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_concealment_not_found_is_audited_as_denied() -> None:
    db = Database(":memory:")
    await db.connect()

    @audit_application_contract(
        "POST",
        "/api/v1/visu/nodes/{node_id}/auth",
        principal_param=None,
        resource_param="node_id",
    )
    async def concealed(*, node_id: str, db: Database) -> None:
        raise HTTPException(404, "not found")

    try:
        with pytest.raises(HTTPException, match="not found"):
            await concealed(node_id="hidden-node", db=db)
        row = await db.fetchone("SELECT action, resource_id, outcome FROM audit_log_entries")
        assert (row["action"], row["resource_id"], row["outcome"]) == (
            "visu.page.credential_checked",
            "hidden-node",
            "denied",
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_denial_audit_failure_does_not_replace_original_http_error(monkeypatch, caplog) -> None:
    db = Database(":memory:")
    await db.connect()

    @audit_application_contract("PUT", "/api/v1/ringbuffer/export/settings", principal_param="principal")
    async def denied(*, principal: Principal, db: Database) -> None:
        raise HTTPException(403, "original denial")

    monkeypatch.setattr(
        "obs.api.v1.application_audit.AuditLogWriter.write_contract",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await denied(principal=Principal(subject="alice", type="user", is_admin=False), db=db)
        assert (exc_info.value.status_code, exc_info.value.detail) == (403, "original denial")
        assert "Could not persist application contract audit event" in caplog.text
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_ringbuffer_runtime_result_is_audited_after_success_and_failure(monkeypatch) -> None:
    from obs.api.v1 import ringbuffer as ringbuffer_api

    db = Database(":memory:")
    await db.connect()
    stats = ringbuffer_api.RingBufferStats(
        enabled=True,
        total=0,
        oldest_ts=None,
        newest_ts=None,
        storage="file",
        max_entries=None,
        max_file_size_bytes=None,
        max_age=None,
        file_size_bytes=0,
    )
    runtime = AsyncMock(return_value=stats)
    monkeypatch.setattr(ringbuffer_api, "_configure_ringbuffer_locked", runtime)
    try:
        result = await ringbuffer_api.configure_ringbuffer(
            ringbuffer_api.RingBufferConfig(),
            None,
            _user="admin",
            db=db,
        )
        assert result == stats
        row = await db.fetchone("SELECT outcome FROM audit_log_entries WHERE action='ringbuffer.config.updated'")
        assert row["outcome"] == "success"

        await db.execute_and_commit("DELETE FROM audit_log_entries")
        runtime.side_effect = RuntimeError("runtime failed")
        with pytest.raises(RuntimeError, match="runtime failed"):
            await ringbuffer_api.configure_ringbuffer(
                ringbuffer_api.RingBufferConfig(),
                None,
                _user="admin",
                db=db,
            )
        row = await db.fetchone("SELECT outcome FROM audit_log_entries WHERE action='ringbuffer.config.updated'")
        assert row["outcome"] == "failed"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_application_success_event_rolls_back_with_its_mutation() -> None:
    db = Database(":memory:")
    await db.connect()
    principal = Principal(subject="alice", type="user", is_admin=False)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            async with db.transaction():
                await db.execute("INSERT INTO app_settings (key, value) VALUES ('application.audit', 'value')")
                await write_application_success(
                    db,
                    None,
                    principal,
                    "PUT",
                    "/api/v1/ringbuffer/export/settings",
                    commit=False,
                )
                raise RuntimeError("rollback")

        assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='application.audit'") is None
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='ringbuffer.export.settings_updated'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_direct_commit_waits_for_atomic_audit_transaction(tmp_path) -> None:
    db = Database(str(tmp_path / "audit-concurrency.db"))
    await db.connect()
    transaction_started = asyncio.Event()
    release_transaction = asyncio.Event()

    async def atomic_writer() -> None:
        async with db.transaction():
            await db.execute("INSERT INTO app_settings (key, value) VALUES ('atomic', 'value')")
            transaction_started.set()
            await release_transaction.wait()

    async def direct_writer() -> None:
        await transaction_started.wait()
        await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('direct', 'value')")

    try:
        atomic_task = asyncio.create_task(atomic_writer())
        direct_task = asyncio.create_task(direct_writer())
        await transaction_started.wait()
        await asyncio.sleep(0)
        assert not direct_task.done()
        release_transaction.set()
        await asyncio.gather(atomic_task, direct_task)

        rows = await db.fetchall("SELECT key FROM app_settings WHERE key IN ('atomic', 'direct') ORDER BY key")
        assert [row["key"] for row in rows] == ["atomic", "direct"]
    finally:
        release_transaction.set()
        await db.disconnect()


def test_database_write_lock_is_reusable_across_sequential_event_loops(tmp_path) -> None:
    db = Database(str(tmp_path / "cross-loop.db"))

    async def contend_for_write_lock() -> None:
        await db.connect()
        transaction_started = asyncio.Event()
        release_transaction = asyncio.Event()

        async def atomic_writer() -> None:
            async with db.transaction():
                transaction_started.set()
                await release_transaction.wait()

        async def waiting_reader() -> None:
            await transaction_started.wait()
            await db.fetchone("SELECT 1")

        atomic_task = asyncio.create_task(atomic_writer())
        reader_task = asyncio.create_task(waiting_reader())
        await transaction_started.wait()
        await asyncio.sleep(0)
        assert not reader_task.done()
        release_transaction.set()
        await asyncio.gather(atomic_task, reader_task)

    async def use_next_loop() -> None:
        assert await db.fetchone("SELECT 1") is not None
        await db.disconnect()

    asyncio.run(contend_for_write_lock())
    asyncio.run(use_next_loop())


@pytest.mark.asyncio
async def test_application_contract_rejects_nested_secret_details() -> None:
    db = Database(":memory:")
    await db.connect()
    principal = Principal(subject="admin", type="user", is_admin=True)
    try:
        async with db.transaction():
            with pytest.raises(ValueError, match="sensitive audit detail"):
                await write_application_success(
                    db,
                    None,
                    principal,
                    "POST",
                    "/api/v1/logic/graphs",
                    details={"operation": {"password": "sentinel"}},
                    commit=False,
                )
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE details_json LIKE '%sentinel%'") is None
    finally:
        await db.disconnect()
