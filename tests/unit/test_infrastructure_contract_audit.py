from __future__ import annotations

import json
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from obs.api.audit import (
    AuditLogWriter,
    AuditOutcome,
    contract_audit,
    set_contract_audit_details,
    set_contract_audit_outcome,
    set_contract_audit_resource_id,
    set_contract_audit_summary,
)
from obs.api.auth import Principal, get_admin_user
from obs.api.v1.adapters import _bulk_mutation_result
from obs.api.v1 import adapters as adapters_api
from obs.api.v1 import bindings as bindings_api
from obs.api.v1 import datapoints as datapoints_api
from obs.api.v1 import hierarchy as hierarchy_api
from obs.api.v1 import knxkeyfile as knxkeyfile_api
from obs.api.v1.datapoints import _audit_metadata_snapshot
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import AuditEffect, AuditMode, ROUTE_SECURITY_CONTRACTS
from obs.db.database import Database
from obs.models.datapoint import DataPoint
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
async def test_result_mode_concealed_denial_keeps_completed_side_effect_and_is_durable(db: Database) -> None:
    dp_id = "00000000-0000-0000-0000-000000000014"
    path = "/api/v1/datapoints/{dp_id}"
    request = _request("PATCH", path, path_params={"dp_id": dp_id})
    dependency = contract_audit("PATCH", path)
    audit_scope = dependency(request, Principal(subject="api_key:key-14", type="api_key", is_admin=False), db)

    await anext(audit_scope)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('infra.denied', 'rolled-back')")
    with pytest.raises(HTTPException, match="concealed"):
        await audit_scope.athrow(HTTPException(status_code=404, detail="concealed"))

    assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='infra.denied'") is not None
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
    path = "/api/v1/adapters/instances/{instance_id}/iobroker/import"
    instance_id = "00000000-0000-0000-0000-000000000099"
    request = _request("POST", path, path_params={"instance_id": instance_id})
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, Principal(subject="alice", type="user", is_admin=False), db)

    await anext(audit_scope)
    set_contract_audit_details(
        request,
        {
            "resource_count": 3,
            "payload_sha256": "0" * 64,
            "error_count": 1,
        },
    )
    set_contract_audit_outcome(request, AuditOutcome.FAILED)
    with pytest.raises(StopAsyncIteration):
        await anext(audit_scope)

    event = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='adapter.iobroker.imported'")
    details = json.loads(event["details_json"])
    assert event["outcome"] == "failed"
    assert details["resource_count"] == 3
    assert details["error_count"] == 1
    assert len(details["payload_sha256"]) == 64
    assert "states" not in event["details_json"]


def test_runtime_side_effect_contracts_are_result_external_mutations() -> None:
    signatures = {
        ("POST", "/api/v1/datapoints/"),
        ("PATCH", "/api/v1/datapoints/{dp_id}"),
        ("DELETE", "/api/v1/datapoints/{dp_id}"),
        ("POST", "/api/v1/datapoints/{dp_id}/bindings"),
        ("PATCH", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"),
        ("DELETE", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"),
        ("POST", "/api/v1/adapters/instances"),
        ("PATCH", "/api/v1/adapters/instances/{instance_id}"),
        ("DELETE", "/api/v1/adapters/instances/{instance_id}"),
        ("POST", "/api/v1/adapters/instances/{source_instance_id}/bindings/migrate"),
        ("POST", "/api/v1/adapters/instances/{instance_id}/iobroker/import"),
        ("POST", "/api/v1/adapters/instances/{instance_id}/anwesenheit/sync-bindings"),
    }

    for signature in signatures:
        contract = ROUTE_SECURITY_CONTRACTS[signature]
        assert (contract.audit_mode, contract.audit_effect) == (AuditMode.RESULT, AuditEffect.EXTERNAL_MUTATION)


@pytest.mark.asyncio
async def test_generated_resource_id_is_queryable_from_canonical_create_event(db: Database) -> None:
    path = "/api/v1/datapoints/"
    generated_id = "00000000-0000-0000-0000-000000000583"
    request = _request("POST", path)
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, Principal(subject="admin", type="user", is_admin=True), db)

    await anext(audit_scope)
    set_contract_audit_resource_id(request, generated_id)
    with pytest.raises(StopAsyncIteration):
        await anext(audit_scope)

    event = await db.fetchone("SELECT action, resource_id FROM audit_log_entries WHERE resource_id=?", (generated_id,))
    assert event is not None
    assert event["action"] == "datapoint.created"


def test_all_single_resource_infrastructure_creates_publish_generated_id() -> None:
    endpoints = (
        datapoints_api.create_datapoint,
        bindings_api.create_binding,
        adapters_api.create_instance,
        hierarchy_api.create_tree,
        hierarchy_api.create_node,
        hierarchy_api.create_link,
        hierarchy_api.import_from_ets,
        knxkeyfile_api.upload_keyfile,
    )

    for endpoint in endpoints:
        assert "set_contract_audit_resource_id(" in inspect.getsource(endpoint)


@pytest.mark.asyncio
async def test_admin_denial_is_not_duplicated_by_active_route_wrapper(db: Database) -> None:
    path = "/api/v1/hierarchy/trees"
    request = _request("POST", path)
    principal = Principal(subject="operator", type="user", is_admin=False)
    dependency = contract_audit("POST", path)
    audit_scope = dependency(request, principal, db)

    await anext(audit_scope)
    with pytest.raises(HTTPException) as exc_info:
        await get_admin_user(principal=principal, db=db, request=request)
    with pytest.raises(HTTPException):
        await audit_scope.athrow(exc_info.value)

    events = await db.fetchall("SELECT action, outcome FROM audit_log_entries")
    assert [(event["action"], event["outcome"]) for event in events] == [("hierarchy.tree.created", "denied")]


def test_datapoint_metadata_snapshot_excludes_runtime_value_and_secrets() -> None:
    datapoint = DataPoint(
        name="Living room",
        data_type="FLOAT",
        unit="°C",
        tags=["climate"],
        mqtt_alias="room/temperature",
    )

    snapshot = _audit_metadata_snapshot(datapoint)

    assert set(snapshot) == {
        "name",
        "data_type",
        "unit",
        "tags",
        "mqtt_alias",
        "persist_value",
        "record_history",
        "control_class",
    }
    assert "value" not in snapshot
    assert "mqtt_topic" not in snapshot


@pytest.mark.asyncio
async def test_datapoint_patch_persists_safe_before_after_and_changed_fields(db: Database, monkeypatch) -> None:
    datapoint = DataPoint(
        id="00000000-0000-0000-0000-000000000584",
        name="Before",
        data_type="UNKNOWN",
        tags=["old"],
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        updated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    class RegistryStub:
        def get(self, dp_id):
            return datapoint if dp_id == datapoint.id else None

        def get_value(self, _dp_id):
            return None

        async def update(self, _dp_id, payload):
            for field, value in payload.model_dump(exclude_none=True, exclude={"value"}).items():
                setattr(datapoint, field, value)
            datapoint.updated_at = datetime(2026, 7, 13, 1, tzinfo=UTC)
            return datapoint

    event_bus = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: RegistryStub())
    monkeypatch.setattr(datapoints_api, "get_event_bus", lambda: event_bus)
    path = "/api/v1/datapoints/{dp_id}"
    request = _request("PATCH", path, path_params={"dp_id": str(datapoint.id)})
    principal = Principal(subject="admin", type="user", is_admin=True)
    audit_scope = contract_audit("PATCH", path)(request, principal, db)

    await anext(audit_scope)
    await datapoints_api.update_datapoint(
        dp_id=datapoint.id,
        body=datapoints_api.DataPointUpdate(name="After", value="audit-secret-sentinel"),
        request=request,
        _user=principal,
        db=db,
    )
    with pytest.raises(StopAsyncIteration):
        await anext(audit_scope)

    event = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE action='datapoint.updated'")
    details = json.loads(event["details_json"])
    assert details["before"]["name"] == "Before"
    assert details["after"]["name"] == "After"
    assert details["changed_fields"] == ["name"]
    assert "audit-secret-sentinel" not in event["details_json"]


def test_bulk_mutation_errors_set_failed_outcome_without_persisting_messages() -> None:
    request = _request("POST", "/api/v1/adapters/instances/{instance_id}/iobroker/import")

    _bulk_mutation_result(
        request,
        count=2,
        payload={"states": ["safe-structural-id"]},
        errors=["state.0: audit-secret-sentinel"],
    )

    assert request.state.contract_audit_outcome == AuditOutcome.FAILED
    assert request.state.contract_audit_details["error_count"] == 1
    assert "audit-secret-sentinel" not in json.dumps(request.state.contract_audit_details)


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
