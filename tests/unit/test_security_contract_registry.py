from __future__ import annotations

from pathlib import Path

from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import AuditMode, ROUTE_SECURITY_CONTRACTS, get_route_security_contract
from tools.check_authz_contract import validate_contracts


def test_every_config_mutation_has_exactly_one_security_and_audit_contract() -> None:
    expected = {signature for signature, category in ROUTE_CLASSIFICATIONS.items() if category == "config_mutation"}
    assert set(ROUTE_SECURITY_CONTRACTS) == expected
    assert len(ROUTE_SECURITY_CONTRACTS) == 99


def test_contract_checker_accepts_the_current_router_and_policy() -> None:
    assert validate_contracts(Path(__file__).resolve().parents[2]) == []


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
