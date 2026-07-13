#!/usr/bin/env python3
# ruff: noqa: E402 -- repository root must be importable when run as a script
"""Fail CI when live v1 routes drift from their AuthZ contracts."""

from __future__ import annotations

import ast
import inspect
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.routing import APIRoute, APIWebSocketRoute

from obs.api.auth import get_admin_user, get_current_principal, get_current_user
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
    PrincipalMode.ADMIN: get_admin_user,
    PrincipalMode.PRINCIPAL: get_current_principal,
    PrincipalMode.USER: get_current_user,
}
_POLICY_ENFORCEMENT_CALLS = frozenset(
    {
        "obs.api.auth._require_api_key_creation_owner",
        "obs.api.authz.authorize",
        "obs.api.authz_service.authorize_adapter_instance",
        "obs.api.authz_service.authorize_visu_page",
        "obs.api.authz_service.filter_authorized_datapoints",
        "obs.api.capabilities.require_config_capability",
    }
)

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


def collect_live_route_occurrences() -> dict[tuple[str, str], list[APIRoute | APIWebSocketRoute]]:
    result: dict[tuple[str, str], list[APIRoute | APIWebSocketRoute]] = {}

    def walk(routes: list, prefix: str) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods or set():
                    if method not in {"HEAD", "OPTIONS"}:
                        result.setdefault((method, f"{prefix}{route.path}"), []).append(route)
            elif isinstance(route, APIWebSocketRoute):
                result.setdefault(("WEBSOCKET", f"{prefix}{route.path}"), []).append(route)
            elif hasattr(route, "original_router") and hasattr(route, "include_context"):
                sub_prefix = getattr(route.include_context, "prefix", "") or ""
                walk(route.original_router.routes, prefix + sub_prefix)

    walk(router.routes, "/api/v1")
    return result


def collect_live_routes() -> dict[tuple[str, str], APIRoute | APIWebSocketRoute]:
    """Return the first live route for each unique signature.

    Validation uses ``collect_live_route_occurrences`` so duplicates remain
    visible instead of silently replacing the runtime-first handler.
    """
    return {signature: routes[0] for signature, routes in collect_live_route_occurrences().items()}


def _dependency_calls(route: APIRoute) -> list[object]:
    calls: list[object] = []

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            calls.append(dependency.call)
            walk(dependency)

    walk(route.dependant)
    return calls


@dataclass(frozen=True)
class _AuditBinding:
    signature: tuple[str, str]
    delivery: str | None


def _bound_audit_contracts(route: APIRoute) -> list[_AuditBinding]:
    """Return every route/endpoint marker installed by audit wrappers."""
    markers: list[_AuditBinding] = []

    def add_marker(call) -> None:
        marker = getattr(call, "__audit_contract__", None)
        if isinstance(marker, tuple) and len(marker) == 2:
            markers.append(_AuditBinding(marker, getattr(call, "__audit_contract_delivery__", None)))

    add_marker(route.endpoint)

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            add_marker(dependency.call)
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


@dataclass(frozen=True)
class _AuditCall:
    signature: tuple[str, str]
    commit_false: bool
    success_capable: bool


@dataclass
class _EndpointEvidence:
    audit_calls: list[_AuditCall]
    policy_enforcement_calls: set[str]


def _positional_or_keyword(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((item.value for item in call.keywords if item.arg == keyword), None)


def _literal_string(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _audit_call(call: ast.Call) -> _AuditCall | None:
    name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
    if name == "write_application_success":
        method = _literal_string(_positional_or_keyword(call, 3, "method"))
        path = _literal_string(_positional_or_keyword(call, 4, "path"))
    elif name == "write_contract":
        method = _literal_string(_positional_or_keyword(call, 0, "method"))
        path = _literal_string(_positional_or_keyword(call, 1, "path"))
    else:
        return None
    if method is None or path is None:
        return None

    commit_node = next((item.value for item in call.keywords if item.arg == "commit"), None)
    commit_false = isinstance(commit_node, ast.Constant) and commit_node.value is False
    outcome_node = next((item.value for item in call.keywords if item.arg == "outcome"), None)
    explicit_outcome = (
        outcome_node.attr
        if isinstance(outcome_node, ast.Attribute)
        else outcome_node.value
        if isinstance(outcome_node, ast.Constant) and isinstance(outcome_node.value, str)
        else None
    )
    return _AuditCall(
        signature=(method.upper(), path),
        commit_false=commit_false,
        success_capable=str(explicit_outcome).lower() not in {"denied", "failed"},
    )


def _endpoint_evidence(endpoint) -> _EndpointEvidence:
    """Collect reachable audit writes and concrete policy-engine calls."""
    evidence = _EndpointEvidence(audit_calls=[], policy_enforcement_calls=set())
    endpoint = inspect.unwrap(endpoint)
    endpoint_module = getattr(endpoint, "__module__", "")
    seen_functions: set[tuple[str, str]] = set()
    seen_local_functions: set[int] = set()

    def scan_ast_function(node: ast.FunctionDef | ast.AsyncFunctionDef, namespace: dict[str, object]) -> None:
        if id(node) in seen_local_functions:
            return
        seen_local_functions.add(id(node))
        local_functions = {child.name: child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
                return None

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_If(self, child: ast.If) -> None:
                if isinstance(child.test, ast.Constant) and isinstance(child.test.value, bool):
                    for statement in child.body if child.test.value else child.orelse:
                        self.visit(statement)
                    return
                self.generic_visit(child)

            def visit_Await(self, child: ast.Await) -> None:
                if isinstance(child.value, ast.Call):
                    audit = _audit_call(child.value)
                    if audit is not None:
                        evidence.audit_calls.append(audit)
                self.visit(child.value)

            def visit_Call(self, child: ast.Call) -> None:
                if isinstance(child.func, ast.Name):
                    local = local_functions.get(child.func.id)
                    if local is not None:
                        scan_ast_function(local, namespace)
                    else:
                        candidate = namespace.get(child.func.id)
                        if inspect.isfunction(candidate):
                            unwrapped = inspect.unwrap(candidate)
                            qualified = f"{unwrapped.__module__}.{unwrapped.__name__}"
                            if qualified in _POLICY_ENFORCEMENT_CALLS:
                                evidence.policy_enforcement_calls.add(qualified)
                            if unwrapped.__module__.startswith(("obs.", "tests.")) or unwrapped.__module__ == endpoint_module:
                                scan_python_function(unwrapped)
                self.generic_visit(child)

        visitor = Visitor()
        for statement in node.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor.visit(statement)

    def scan_python_function(function) -> None:
        function = inspect.unwrap(function)
        identity = (getattr(function, "__module__", ""), getattr(function, "__qualname__", repr(function)))
        if identity in seen_functions:
            return
        seen_functions.add(identity)
        try:
            tree = ast.parse(dedent(inspect.getsource(function)))
        except (OSError, TypeError, IndentationError, SyntaxError):
            return
        root_function = next(
            (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )
        if root_function is None:
            return
        namespace = dict(getattr(function, "__globals__", {}))
        try:
            namespace.update(inspect.getclosurevars(function).nonlocals)
        except TypeError:
            pass
        scan_ast_function(root_function, namespace)

    scan_python_function(endpoint)
    return evidence


def validate_contracts(repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    occurrences = collect_live_route_occurrences()
    live = {signature: routes[0] for signature, routes in occurrences.items()}
    classified = set(ROUTE_CLASSIFICATIONS)
    mutation_routes = {signature for signature, category in ROUTE_CLASSIFICATIONS.items() if category == "config_mutation"}
    contracted = set(ROUTE_SECURITY_CONTRACTS)
    evidence_cache: dict[int, _EndpointEvidence] = {}

    def evidence_for(route: APIRoute) -> _EndpointEvidence:
        key = id(route.endpoint)
        if key not in evidence_cache:
            evidence_cache[key] = _endpoint_evidence(route.endpoint)
        return evidence_cache[key]

    for signature, routes in occurrences.items():
        if len(routes) > 1:
            errors.append(f"{signature}: duplicate live route signature ({len(routes)} handlers)")
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
            dependencies = _dependency_calls(route)
            if required and not any(inspect.unwrap(call) is inspect.unwrap(required) for call in dependencies):
                errors.append(f"{signature}: {contract.principal.value} contract requires dependency {required.__name__}")
            if any(getattr(call, "__name__", "") == "optional_current_user" for call in dependencies):
                errors.append(f"{signature}: config mutation must not use optional_current_user")
            if contract.authorization in {AuthorizationMode.POLICY, AuthorizationMode.POLICY_OR_CAPABILITY}:
                if not evidence_for(route).policy_enforcement_calls:
                    errors.append(f"{signature}: declared policy checks have no reachable authorization enforcement call")

    for capability in CONFIG_CAPABILITIES:
        if capability == "*" or "*" in capability:
            errors.append(f"wildcard configuration capability {capability!r}")

    audit_calls = _literal_contract_audit_calls(root)
    for signature, contract in ROUTE_SECURITY_CONTRACTS.items():
        route = live.get(signature)
        bindings = _bound_audit_contracts(route) if isinstance(route, APIRoute) else []
        binding_signatures = [binding.signature for binding in bindings]
        if bindings and any(binding.signature != signature for binding in bindings):
            errors.append(f"{signature}: mismatched audit contract binding {binding_signatures!r}")
        if len(bindings) > 1:
            errors.append(f"{signature}: expected exactly one audit contract binding, found {len(bindings)}")
        matching_bindings = [binding for binding in bindings if binding.signature == signature]
        if len(matching_bindings) == 1 and matching_bindings[0].delivery == "complete":
            continue
        if matching_bindings and matching_bindings[0].delivery not in {"complete", "failure_only"}:
            errors.append(f"{signature}: unknown audit contract delivery {matching_bindings[0].delivery!r}")
        endpoint_calls = evidence_for(route).audit_calls if isinstance(route, APIRoute) else []
        matching_calls = [call for call in endpoint_calls if call.signature == signature and call.success_capable]
        if not matching_calls:
            errors.append(f"{signature}: endpoint and reachable helpers have no awaited success-capable contract audit call")
        elif contract.audit_mode == AuditMode.ATOMIC and not any(call.commit_false for call in matching_calls):
            errors.append(f"{signature}: atomic endpoint has no reachable contract audit call with commit=False")
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
