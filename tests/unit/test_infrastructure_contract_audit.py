from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from obs.api.audit import (
    AuditLogWriter,
    AuditOutcome,
    contract_audit,
    set_contract_audit_outcome,
    set_contract_audit_summary,
)
from obs.api.auth import Principal
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import ROUTE_SECURITY_CONTRACTS
from obs.db.database import Database
from tools.check_authz_contract import collect_live_routes

_INFRASTRUCTURE_MODULES = frozenset(
    {
        "obs.api.v1.adapters",
        "obs.api.v1.bindings",
        "obs.api.v1.datapoints",
        "obs.api.v1.hierarchy",
        "obs.api.v1.icons",
        "obs.api.v1.knxkeyfile",
        "obs.api.v1.knxproj",
    }
)


def _audit_contracts(route: APIRoute) -> list[tuple[str, str]]:
    contracts: list[tuple[str, str]] = []

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            contract = getattr(dependency.call, "__audit_contract__", None)
            if contract is not None:
                contracts.append(contract)
            walk(dependency)

    walk(route.dependant)
    return contracts


def _request(method: str, path: str, *, path_params: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "path_params": path_params or {},
            "query_string": b"",
            "headers": [(b"x-request-id", b"wave14-infrastructure")],
            "client": ("127.0.0.1", 12345),
            "route": SimpleNamespace(path=path),
        }
    )


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def test_all_39_infrastructure_mutations_have_exactly_one_literal_audit_dependency() -> None:
    live = collect_live_routes()
    infrastructure = {
        signature: route
        for signature, route in live.items()
        if isinstance(route, APIRoute)
        and route.endpoint.__module__ in _INFRASTRUCTURE_MODULES
        and ROUTE_CLASSIFICATIONS[signature] == "config_mutation"
    }

    assert len(infrastructure) == 39
    assert {module: sum(route.endpoint.__module__ == module for route in infrastructure.values()) for module in _INFRASTRUCTURE_MODULES} == {
        "obs.api.v1.adapters": 11,
        "obs.api.v1.bindings": 3,
        "obs.api.v1.datapoints": 3,
        "obs.api.v1.hierarchy": 10,
        "obs.api.v1.icons": 6,
        "obs.api.v1.knxkeyfile": 2,
        "obs.api.v1.knxproj": 4,
    }
    for signature, route in infrastructure.items():
        assert _audit_contracts(route) == [signature]


@pytest.mark.asyncio
async def test_atomic_success_commits_domain_write_and_canonical_event(db: Database) -> None:
    request = _request("POST", "/api/v1/hierarchy/trees")
    dependency = contract_audit("POST", "/api/v1/hierarchy/trees")
    audit_scope = dependency(request, Principal(subject="admin", type="user", is_admin=True), db)

    await anext(audit_scope)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('infra.atomic', 'committed')")
    with pytest.raises(StopAsyncIteration):
        await anext(audit_scope)

    assert (await db.fetchone("SELECT value FROM app_settings WHERE key='infra.atomic'"))["value"] == "committed"
    event = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='hierarchy.tree.created'")
    assert event["outcome"] == "success"
    assert event["principal_id"] == "admin"


@pytest.mark.asyncio
async def test_concealed_denial_rolls_back_domain_write_and_is_durable(db: Database) -> None:
    dp_id = "00000000-0000-0000-0000-000000000014"
    path = "/api/v1/datapoints/{dp_id}"
    request = _request("PATCH", path, path_params={"dp_id": dp_id})
    dependency = contract_audit("PATCH", path)
    audit_scope = dependency(request, Principal(subject="api_key:key-14", type="api_key", is_admin=False), db)

    await anext(audit_scope)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('infra.denied', 'rolled-back')")
    with pytest.raises(HTTPException, match="concealed"):
        await audit_scope.athrow(HTTPException(status_code=404, detail="concealed"))

    assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='infra.denied'") is None
    event = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='datapoint.updated'")
    assert event["outcome"] == "denied"
    assert event["resource_id"] == dp_id
    assert event["principal_type"] == "api_key"


@pytest.mark.asyncio
async def test_validation_failure_rolls_back_and_records_failed_outcome(db: Database) -> None:
    path = "/api/v1/hierarchy/trees"
    request = _request("POST", path)
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, Principal(subject="admin", type="user", is_admin=True), db)

    await anext(audit_scope)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('infra.failed', 'rolled-back')")
    with pytest.raises(HTTPException, match="invalid"):
        await audit_scope.athrow(HTTPException(status_code=422, detail="invalid"))

    assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='infra.failed'") is None
    event = await db.fetchone("SELECT outcome FROM audit_log_entries WHERE action='hierarchy.tree.created'")
    assert event["outcome"] == "failed"


@pytest.mark.asyncio
async def test_audit_write_failure_rolls_back_inner_execute_and_commit(db: Database, monkeypatch) -> None:
    path = "/api/v1/hierarchy/trees"
    request = _request("POST", path)
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, Principal(subject="admin", type="user", is_admin=True), db)
    original = AuditLogWriter.write_contract

    async def fail_success(self, method, route, **kwargs):
        if kwargs.get("outcome", AuditOutcome.SUCCESS) == AuditOutcome.SUCCESS:
            raise RuntimeError("audit unavailable")
        return await original(self, method, route, **kwargs)

    monkeypatch.setattr(AuditLogWriter, "write_contract", fail_success)
    await anext(audit_scope)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('infra.audit-fail', 'rolled-back')")
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await anext(audit_scope)

    assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='infra.audit-fail'") is None
    assert (await db.fetchone("SELECT outcome FROM audit_log_entries"))["outcome"] == "failed"


@pytest.mark.asyncio
async def test_result_channel_records_domain_failure_and_redacted_bulk_summary(db: Database) -> None:
    path = "/api/v1/adapters/instances/{instance_id}/iobroker/import-preview"
    instance_id = "00000000-0000-0000-0000-000000000099"
    request = _request("POST", path, path_params={"instance_id": instance_id})
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, Principal(subject="alice", type="user", is_admin=False), db)

    await anext(audit_scope)
    set_contract_audit_summary(request, resource_count=3, payload={"states": ["a", "b", "c"]})
    set_contract_audit_outcome(request, AuditOutcome.FAILED)
    with pytest.raises(StopAsyncIteration):
        await anext(audit_scope)

    event = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='adapter.iobroker.import_previewed'")
    details = json.loads(event["details_json"])
    assert event["outcome"] == "failed"
    assert details["resource_count"] == 3
    assert len(details["payload_sha256"]) == 64
    assert "states" not in event["details_json"]


def test_bulk_contract_fields_are_allowlisted_without_secrets() -> None:
    bulk_contracts = {
        signature: contract
        for signature, contract in ROUTE_SECURITY_CONTRACTS.items()
        if signature[1].startswith(("/api/v1/adapters/", "/api/v1/icons", "/api/v1/knxproj", "/api/v1/hierarchy"))
        and "resource_count" in contract.allowed_detail_fields
    }
    assert len(bulk_contracts) >= 14
    for contract in bulk_contracts.values():
        assert {"resource_count", "payload_sha256"} <= contract.allowed_detail_fields


def test_bulk_digest_depends_only_on_redacted_structural_input() -> None:
    first = _request("POST", "/api/v1/knxproj/import")
    second = _request("POST", "/api/v1/knxproj/import")
    structural_input = {"group_addresses": ["1/2/3"], "hierarchy_modes": ["groups"]}

    set_contract_audit_summary(first, resource_count=1, payload=structural_input)
    # A credential-bearing source payload is deliberately never passed to the
    # digest helper; changing it therefore cannot turn the digest into an
    # offline password/key verifier.
    source_passwords = ["audit-secret-sentinel-a", "audit-secret-sentinel-b"]
    set_contract_audit_summary(second, resource_count=1, payload=structural_input)

    assert source_passwords[0] != source_passwords[1]
    assert first.state.contract_audit_details == second.state.contract_audit_details
    assert "audit-secret-sentinel" not in json.dumps(first.state.contract_audit_details)
