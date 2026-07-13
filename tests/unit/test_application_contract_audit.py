from __future__ import annotations

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1.application_audit import audit_application_contract, write_application_success
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import ROUTE_SECURITY_CONTRACTS
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
