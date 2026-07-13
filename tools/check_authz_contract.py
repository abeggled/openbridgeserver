#!/usr/bin/env python3
# ruff: noqa: E402 -- repository root must be importable when run as a script
"""Fail CI when live v1 routes drift from their AuthZ contracts."""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.routing import APIRoute, APIWebSocketRoute

from obs.api.capabilities import CONFIG_CAPABILITIES
from obs.api.router import router
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.api.v1.security_contract_registry import (
    AuditEffect,
    AuditMode,
    AuthorizationMode,
    PrincipalMode,
    ROUTE_SECURITY_CONTRACTS,
)

_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_RESOLVER_PREFIXES = frozenset({"body", "constant", "declared", "derived", "global", "path"})
_SENSITIVE_DETAIL_PARTS = frozenset({"authorization", "cookie", "credential", "keyfile", "password", "pin", "secret", "token"})
_REQUIRED_DEPENDENCY = {
    PrincipalMode.ADMIN: "get_admin_user",
    PrincipalMode.PRINCIPAL: "get_current_principal",
    PrincipalMode.USER: "get_current_user",
}

# Compatibility bridges are pre-existing and may be removed, but not expanded.
# Counts make an extra synthetic Principal in an allowlisted helper fail as well.
_SYNTHETIC_ADMIN_BASELINE = Counter(
    {
        ("obs/api/v1/adapters.py", "_principal_from_dependency", "str(user) == 'admin'"): 1,
        ("obs/api/v1/bindings.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/datapoints.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/hierarchy.py", "_principal_from_dependency", "str(user) == 'admin'"): 1,
        ("obs/api/v1/knxproj.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/knxproj.py", "set_knx_device_hierarchy_links", "True"): 1,
        ("obs/api/v1/logic.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/logic.py", "_principal_from_mutation_dependency", "True"): 1,
        ("obs/api/v1/ringbuffer.py", "_principal_from_dependency", "True"): 1,
        ("obs/api/v1/ringbuffer.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/search.py", "search", "_user == 'admin'"): 1,
        ("obs/api/v1/visu.py", "_principal_from_dependency", "value == 'admin'"): 1,
        ("obs/api/v1/visu.py", "_principal_from_mutation_dependency", "True"): 1,
    }
)


def collect_live_routes() -> dict[tuple[str, str], APIRoute | APIWebSocketRoute]:
    result: dict[tuple[str, str], APIRoute | APIWebSocketRoute] = {}

    def walk(routes: list, prefix: str) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods or set():
                    if method not in {"HEAD", "OPTIONS"}:
                        result[(method, f"{prefix}{route.path}")] = route
            elif isinstance(route, APIWebSocketRoute):
                result[("WEBSOCKET", f"{prefix}{route.path}")] = route
            elif hasattr(route, "original_router") and hasattr(route, "include_context"):
                sub_prefix = getattr(route.include_context, "prefix", "") or ""
                walk(route.original_router.routes, prefix + sub_prefix)

    walk(router.routes, "/api/v1")
    return result


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            names.add(getattr(dependency.call, "__name__", type(dependency.call).__name__))
            walk(dependency)

    walk(route.dependant)
    return names


def _bound_audit_contracts(route: APIRoute) -> set[tuple[str, str]]:
    """Return route/endpoint markers installed by contract audit wrappers."""
    markers: set[tuple[str, str]] = set()
    endpoint_marker = getattr(route.endpoint, "__audit_contract__", None)
    if isinstance(endpoint_marker, tuple) and len(endpoint_marker) == 2:
        markers.add(endpoint_marker)

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            marker = getattr(dependency.call, "__audit_contract__", None)
            if isinstance(marker, tuple) and len(marker) == 2:
                markers.add(marker)
            walk(dependency)

    walk(route.dependant)
    return markers


def _synthetic_admin_principals(repo_root: Path) -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()
    api_root = repo_root / "obs" / "api" / "v1"
    for path in api_root.rglob("*.py"):
        relative = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                functions.append(node.name)
                self.generic_visit(node)
                functions.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name == "Principal":
                    for keyword in node.keywords:
                        if keyword.arg != "is_admin":
                            continue
                        expression = ast.unparse(keyword.value)
                        if expression == "True" or "'admin'" in expression or '"admin"' in expression:
                            found[(relative, functions[-1] if functions else "<module>", expression)] += 1
                self.generic_visit(node)

        Visitor().visit(tree)
    return found


def _literal_contract_audit_calls(repo_root: Path) -> dict[tuple[str, str], list[tuple[str, str, bool]]]:
    """Collect statically reviewable contract writes from API source modules."""
    found: dict[tuple[str, str], list[tuple[str, str, bool]]] = {}
    for path in (repo_root / "obs" / "api").rglob("*.py"):
        relative = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                functions.append(node.name)
                self.generic_visit(node)
                functions.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                is_contract_write = isinstance(node.func, ast.Attribute) and node.func.attr == "write_contract"
                if is_contract_write and len(node.args) >= 2:
                    method_node, path_node = node.args[:2]
                    if (
                        isinstance(method_node, ast.Constant)
                        and isinstance(method_node.value, str)
                        and isinstance(path_node, ast.Constant)
                        and isinstance(path_node.value, str)
                    ):
                        signature = (method_node.value.upper(), path_node.value)
                        commit_false = any(
                            keyword.arg == "commit" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                            for keyword in node.keywords
                        )
                        found.setdefault(signature, []).append((relative, functions[-1] if functions else "<module>", commit_false))
                self.generic_visit(node)

        Visitor().visit(tree)
    return found


def validate_contracts(repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    live = collect_live_routes()
    classified = set(ROUTE_CLASSIFICATIONS)
    mutation_routes = {signature for signature, category in ROUTE_CLASSIFICATIONS.items() if category == "config_mutation"}
    contracted = set(ROUTE_SECURITY_CONTRACTS)

    if set(live) != classified:
        errors.append(f"route classification drift: missing={sorted(set(live) - classified)!r} stale={sorted(classified - set(live))!r}")
    if mutation_routes != contracted:
        errors.append(f"security contract drift: missing={sorted(mutation_routes - contracted)!r} stale={sorted(contracted - mutation_routes)!r}")

    seen_actions: set[str] = set()
    for signature, contract in ROUTE_SECURITY_CONTRACTS.items():
        if not _ACTION_RE.fullmatch(contract.audit_action):
            errors.append(f"{signature}: invalid audit action {contract.audit_action!r}")
        if contract.audit_action in seen_actions:
            errors.append(f"{signature}: duplicate audit action {contract.audit_action!r}")
        seen_actions.add(contract.audit_action)
        if not contract.scope.strip():
            errors.append(f"{signature}: empty scope")
        if contract.authorization == AuthorizationMode.POLICY_OR_CAPABILITY and not contract.capability:
            errors.append(f"{signature}: capability authorization without capability")
        if contract.capability and (contract.capability == "*" or "*" in contract.capability):
            errors.append(f"{signature}: wildcard capability {contract.capability!r}")
        if contract.audit_mode == AuditMode.ATOMIC and contract.audit_effect != AuditEffect.DB_MUTATION:
            errors.append(f"{signature}: atomic audit must describe a DB mutation")
        if contract.audit_mode == AuditMode.SECURITY and contract.audit_effect != AuditEffect.SECURITY_EVENT:
            errors.append(f"{signature}: security delivery must describe a security event")
        if contract.principal not in {PrincipalMode.AUTH_FLOW, PrincipalMode.CREDENTIAL} and not contract.checks:
            errors.append(f"{signature}: protected mutation has no declared authorization checks")
        for check in contract.checks:
            prefix = check.target_resolver.partition(":")[0]
            if prefix not in _RESOLVER_PREFIXES:
                errors.append(f"{signature}: unknown target resolver {check.target_resolver!r}")
            if check.capability and "*" in check.capability:
                errors.append(f"{signature}: wildcard check capability {check.capability!r}")
        for field in contract.allowed_detail_fields:
            parts = set(field.lower().replace("-", "_").split("_"))
            if parts & _SENSITIVE_DETAIL_PARTS:
                errors.append(f"{signature}: sensitive audit detail field {field!r}")

        route = live.get(signature)
        required = _REQUIRED_DEPENDENCY.get(contract.principal)
        if isinstance(route, APIRoute):
            dependencies = _dependency_names(route)
            if required and required not in dependencies:
                errors.append(f"{signature}: {contract.principal.value} contract requires dependency {required}")
            if "optional_current_user" in dependencies:
                errors.append(f"{signature}: config mutation must not use optional_current_user")

    for capability in CONFIG_CAPABILITIES:
        if capability == "*" or "*" in capability:
            errors.append(f"wildcard configuration capability {capability!r}")

    audit_calls = _literal_contract_audit_calls(root)
    for signature, contract in ROUTE_SECURITY_CONTRACTS.items():
        route = live.get(signature)
        bound_contracts = _bound_audit_contracts(route) if isinstance(route, APIRoute) else set()
        if bound_contracts and signature not in bound_contracts:
            errors.append(f"{signature}: mismatched audit contract binding {sorted(bound_contracts)!r}")
        if signature in bound_contracts:
            continue
        endpoint_module = getattr(getattr(route, "endpoint", None), "__module__", "")
        endpoint_path = f"{endpoint_module.replace('.', '/')}.py"
        matching_calls = [call for call in audit_calls.get(signature, []) if call[0] == endpoint_path]
        if not matching_calls:
            errors.append(f"{signature}: endpoint module has no literal write_contract call")
        elif contract.audit_mode == AuditMode.ATOMIC and not any(call[2] for call in matching_calls):
            errors.append(f"{signature}: atomic endpoint has no write_contract call with commit=False")
    for signature in audit_calls.keys() - ROUTE_SECURITY_CONTRACTS.keys():
        errors.append(f"{signature}: stale literal write_contract call without route contract")

    synthetic = _synthetic_admin_principals(root)
    for key, count in synthetic.items():
        if count > _SYNTHETIC_ADMIN_BASELINE[key]:
            errors.append(f"new runtime admin imitation {key!r} (count {count}, baseline {_SYNTHETIC_ADMIN_BASELINE[key]})")
    return errors


def main() -> int:
    errors = validate_contracts()
    if errors:
        print("AuthZ contract check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"AuthZ contract check passed: {len(ROUTE_SECURITY_CONTRACTS)} configuration mutations covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
