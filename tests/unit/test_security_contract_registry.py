from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import Depends
from fastapi.routing import APIRoute

from obs.api.audit import AuditOutcome, contract_audit
from obs.api.auth import get_admin_user, get_current_principal
from obs.api.router import router as api_router
from obs.api.v1.application_audit import audit_application_contract
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import AuditMode, ROUTE_SECURITY_CONTRACTS, get_route_security_contract
from obs.db.database import get_db
from tests.unit._authz_checker_helpers import write_shared_helper_audit
from tools.check_authz_contract import validate_contracts


@contextmanager
def _registered_test_route(signature, contract, endpoint, *, complete_audit: bool = False):
    ROUTE_CLASSIFICATIONS[signature] = "config_mutation"
    ROUTE_SECURITY_CONTRACTS[signature] = contract
    dependencies = [Depends(contract_audit(*signature))] if complete_audit else None
    route = APIRoute(signature[1].removeprefix("/api/v1"), endpoint, methods={signature[0]}, dependencies=dependencies)
    api_router.routes.append(route)
    try:
        yield route
    finally:
        api_router.routes.remove(route)
        ROUTE_SECURITY_CONTRACTS.pop(signature)
        ROUTE_CLASSIFICATIONS.pop(signature)


async def _helper_audited_endpoint(_admin=Depends(get_admin_user)) -> None:
    writer = None
    await write_shared_helper_audit(writer)


def test_every_config_mutation_has_exactly_one_security_and_audit_contract() -> None:
    expected = {signature for signature, category in ROUTE_CLASSIFICATIONS.items() if category == "config_mutation"}
    assert set(ROUTE_SECURITY_CONTRACTS) == expected
    assert len(ROUTE_SECURITY_CONTRACTS) == 99


def test_contract_checker_accepts_the_current_router_and_policy() -> None:
    assert validate_contracts(Path(__file__).resolve().parents[2]) == []


def test_contract_checker_is_deterministic_across_repeated_ast_walks() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for _ in range(5):
        assert validate_contracts(repo_root) == []


def test_unknown_mutation_contract_fails_closed() -> None:
    try:
        get_route_security_contract("POST", "/api/v1/unknown")
    except LookupError as exc:
        assert "No security contract" in str(exc)
    else:
        raise AssertionError("unknown mutation contract did not fail closed")


def test_atomic_and_result_events_are_both_declared() -> None:
    modes = {contract.audit_mode for contract in ROUTE_SECURITY_CONTRACTS.values()}
    assert {AuditMode.ATOMIC, AuditMode.RESULT, AuditMode.SECURITY} <= modes


def test_multi_scope_creation_and_execution_contracts_are_explicit() -> None:
    assert len(ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/logic/graphs/{graph_id}/run")].checks) >= 3
    assert len(ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/visu/nodes/{node_id}/copy")].checks) >= 3
    assert len(ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/datapoints/{dp_id}/bindings")].checks) >= 2


def test_logic_run_contract_datapoint_check_requires_activate_not_read() -> None:
    from obs.api.v1.security_contract_registry import CheckKind

    contract = ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/logic/graphs/{graph_id}/run")]
    dp_checks = [c for c in contract.checks if c.kind == CheckKind.ROLE and c.target_type == "datapoint"]
    assert dp_checks, "logic-run contract must have a datapoint ROLE check"
    for check in dp_checks:
        assert check.action == "activate", (
            f"logic-run datapoint check uses {check.action!r} but runtime authorizes with ACTIVATE; "
            "approving a 'read'-only grant while the endpoint requires operator+ causes silent access denial"
        )


def test_generate_mutation_contracts_match_runtime_datapoint_checks() -> None:
    from obs.api.v1.security_contract_registry import CheckKind

    signatures = (
        ("PUT", "/api/v1/logic/graphs/{graph_id}"),
        ("PATCH", "/api/v1/logic/graphs/{graph_id}"),
        ("DELETE", "/api/v1/logic/graphs/{graph_id}"),
        ("PUT", "/api/v1/visu/pages/{node_id}"),
    )
    for signature in signatures:
        contract = ROUTE_SECURITY_CONTRACTS[signature]
        assert contract.action == "generate"
        datapoint_checks = [check for check in contract.checks if check.kind == CheckKind.ROLE and check.target_type == "datapoint"]
        assert datapoint_checks
        assert all(check.action == "generate" for check in datapoint_checks)

    assert ROUTE_SECURITY_CONTRACTS[("PUT", "/api/v1/visu/pages/{node_id}")].capability == "visu.page_config.write"


def test_duplicate_live_route_signature_fails_closed() -> None:
    async def unaudited_duplicate() -> None:
        return None

    route = APIRoute("/system/nav-links", unaudited_duplicate, methods={"POST"})
    api_router.routes.insert(0, route)
    try:
        errors = validate_contracts(Path(__file__).resolve().parents[2])
    finally:
        api_router.routes.remove(route)

    assert any("duplicate live route signature" in error for error in errors)


def test_declared_policy_without_reachable_enforcement_fails_closed() -> None:
    signature = ("POST", "/api/v1/test/declared-policy-only")
    contract = replace(
        ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/logic/graphs")],
        audit_action="test.declared_policy_only",
    )

    async def declared_only(principal=Depends(get_current_principal)) -> None:
        return None

    with _registered_test_route(signature, contract, declared_only, complete_audit=True):
        errors = validate_contracts(Path(__file__).resolve().parents[2])

    assert any("declared policy checks have no reachable authorization enforcement call" in error for error in errors)


def test_principal_dependency_must_be_the_real_callable() -> None:
    signature = ("POST", "/api/v1/test/fake-admin-dependency")
    contract = replace(
        ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/system/nav-links")],
        audit_action="test.fake_admin_dependency",
    )

    async def impostor() -> str:
        return "admin"

    impostor.__name__ = "get_admin_user"

    async def fake_admin_route(_admin=Depends(impostor)) -> None:
        return None

    with _registered_test_route(signature, contract, fake_admin_route, complete_audit=True):
        errors = validate_contracts(Path(__file__).resolve().parents[2])

    assert any("admin contract requires dependency get_admin_user" in error for error in errors)


def test_application_failure_wrapper_without_success_audit_fails_closed() -> None:
    signature = ("POST", "/api/v1/test/missing-success-audit")
    contract = replace(
        ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/system/nav-links")],
        audit_action="test.missing_success_audit",
    )

    @audit_application_contract(*signature, principal_param="_admin")
    async def missing_success(_admin=Depends(get_admin_user), db=Depends(get_db)) -> None:
        return None

    with _registered_test_route(signature, contract, missing_success):
        errors = validate_contracts(Path(__file__).resolve().parents[2])

    assert any("no awaited success-capable contract audit call" in error for error in errors)


def test_failure_only_literal_audit_does_not_satisfy_result_contract() -> None:
    signature = ("POST", "/api/v1/test/failure-only-audit")
    contract = replace(
        ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/system/history/test")],
        audit_action="test.failure_only_audit",
    )

    async def failure_only(_admin=Depends(get_admin_user)) -> None:
        writer = None
        await writer.write_contract("POST", "/api/v1/test/failure-only-audit", outcome=AuditOutcome.FAILED)

    with _registered_test_route(signature, contract, failure_only):
        errors = validate_contracts(Path(__file__).resolve().parents[2])

    assert any("no awaited success-capable contract audit call" in error for error in errors)


def test_reachable_shared_helper_audit_is_accepted() -> None:
    signature = ("POST", "/api/v1/test/helper-audit")
    contract = replace(
        ROUTE_SECURITY_CONTRACTS[("POST", "/api/v1/system/history/test")],
        audit_action="test.helper_audit",
    )

    with _registered_test_route(signature, contract, _helper_audited_endpoint):
        assert validate_contracts(Path(__file__).resolve().parents[2]) == []
