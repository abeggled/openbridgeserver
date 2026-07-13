"""Audit logging helpers for config-mutating API endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from hashlib import sha256
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import Depends, HTTPException, Request

from obs.api.auth import Principal, get_current_principal, optional_current_user
from obs.db.database import Database, get_db

logger = logging.getLogger(__name__)


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"


_SENSITIVE_DETAIL_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "keyfile",
        "password",
        "pin",
        "private_key",
        "secret",
        "token",
    }
)


@dataclass(frozen=True)
class AuditContext:
    actor: str
    request_id: str | None
    remote_addr: str | None
    user_agent: str | None
    principal_type: str = "anonymous"
    principal_id: str | None = None
    http_method: str | None = None
    route_template: str | None = None


def _principal_identity(principal: Principal | str | None) -> tuple[str, str | None, str]:
    if isinstance(principal, Principal):
        principal_id = principal.subject.removeprefix("api_key:") if principal.type == "api_key" else principal.subject
        return principal.type, principal_id, principal.subject
    if isinstance(principal, str):
        if principal.startswith("api_key:"):
            return "api_key", principal.removeprefix("api_key:"), principal
        return "user", principal, principal
    return "anonymous", None, "anonymous"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", request.url.path))


def build_audit_context(request: Request | None, current_user: Principal | str | None) -> AuditContext:
    principal_type, principal_id, actor = _principal_identity(current_user)
    if request is None:
        return AuditContext(
            actor=actor,
            request_id=None,
            remote_addr=None,
            user_agent=None,
            principal_type=principal_type,
            principal_id=principal_id,
        )
    client_host = request.client.host if request.client else None
    return AuditContext(
        actor=actor,
        request_id=request.headers.get("x-request-id"),
        remote_addr=client_host,
        user_agent=request.headers.get("user-agent"),
        principal_type=principal_type,
        principal_id=principal_id,
        http_method=request.method,
        route_template=_route_template(request),
    )


def _assert_safe_details(value: Any, *, path: str = "details") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            parts = set(normalized.split("_"))
            is_presence_flag = normalized.startswith("has_") and isinstance(nested, bool)
            if parts & _SENSITIVE_DETAIL_TOKENS and not is_presence_flag:
                raise ValueError(f"sensitive audit detail field at {path}.{key}")
            _assert_safe_details(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_safe_details(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, bytes):
        raise ValueError(f"binary audit detail at {path}")


def audit_payload_sha256(value: Any) -> str:
    """Return a deterministic digest for bulk mutation summaries."""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode()
    return sha256(payload).hexdigest()


def set_contract_audit_summary(request: Request, *, resource_count: int, payload: Any) -> None:
    """Attach a redacted bulk summary for the active contract dependency."""
    request.state.contract_audit_details = {
        "resource_count": resource_count,
        "payload_sha256": audit_payload_sha256(payload),
    }


def set_contract_audit_outcome(request: Request, outcome: AuditOutcome) -> None:
    """Override the automatic success outcome for a returned domain result."""
    request.state.contract_audit_outcome = outcome


class AuditLogWriter:
    def __init__(self, db: Database, context: AuditContext) -> None:
        self._db = db
        self.context = context

    async def write(
        self,
        action: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
        http_method: str | None = None,
        route_template: str | None = None,
        commit: bool = True,
    ) -> int:
        if not action.strip():
            raise ValueError("action must not be empty")

        safe_details = details or {}
        _assert_safe_details(safe_details)
        outcome_value = AuditOutcome(outcome).value
        payload = json.dumps(safe_details, separators=(",", ":"), sort_keys=True)
        execute = self._db.execute_and_commit if commit else self._db.execute
        cur = await execute(
            """
            INSERT INTO audit_log_entries
                (actor, action, resource_type, resource_id, details_json,
                 request_id, remote_addr, user_agent, principal_type,
                 principal_id, outcome, http_method, route_template)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.context.actor,
                action,
                resource_type,
                resource_id,
                payload,
                self.context.request_id,
                self.context.remote_addr,
                self.context.user_agent,
                self.context.principal_type,
                self.context.principal_id,
                outcome_value,
                http_method or self.context.http_method,
                route_template or self.context.route_template,
            ),
        )
        if cur is None:
            return 0
        return int(cur.lastrowid)

    async def write_contract(
        self,
        method: str,
        path: str,
        *,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
        commit: bool = True,
    ) -> int:
        """Write the canonical event declared for one mutating route."""
        from obs.api.v1.security_contract_registry import AuditMode, get_route_security_contract

        contract = get_route_security_contract(method, path)
        outcome_value = AuditOutcome(outcome)
        unknown_detail_fields = set(details or {}) - contract.allowed_detail_fields
        if unknown_detail_fields:
            raise ValueError(f"audit details not declared by contract: {sorted(unknown_detail_fields)}")
        if contract.audit_mode == AuditMode.ATOMIC and outcome_value == AuditOutcome.SUCCESS:
            if commit or not self._db.in_transaction:
                raise ValueError("successful atomic audit events must use the surrounding transaction")
        return await self.write(
            contract.audit_action,
            resource_type=contract.scope,
            resource_id=resource_id,
            details=details,
            outcome=outcome_value,
            http_method=method.upper(),
            route_template=path,
            commit=commit,
        )


async def get_audit_log_writer(
    request: Request,
    current_user: Principal | str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> AuditLogWriter:
    return AuditLogWriter(db=db, context=build_audit_context(request, current_user))


def _path_resource_id(request: Request) -> str | None:
    """Choose the most specific non-secret identifier from matched path params."""
    path_params = request.path_params
    for name in (
        "binding_id",
        "instance_id",
        "source_instance_id",
        "dp_id",
        "node_id",
        "tree_id",
        "file_id",
        "key_id",
        "pa",
        "adapter_type",
        "name",
    ):
        value = path_params.get(name)
        if value is not None:
            return str(value)
    return None


async def _write_failure_event(
    writer: AuditLogWriter,
    method: str,
    path: str,
    resource_id: str | None,
    outcome: AuditOutcome,
) -> None:
    """Best-effort secondary audit that never hides the endpoint exception."""
    try:
        await writer.write_contract(method, path, resource_id=resource_id, outcome=outcome)
    except Exception:
        logger.exception("Could not persist %s contract audit for %s %s", outcome, method, path)


def contract_audit(method: str, path: str) -> Callable[..., AsyncIterator[None]]:
    """Create a route dependency enforcing its declared audit delivery mode.

    Atomic contracts wrap the complete endpoint in the task-local DB transaction;
    the success event is the final write before commit.  Denied/failed events are
    emitted only after that transaction has rolled back, so they remain durable.
    Result contracts record the outcome after the external operation returns.
    """
    from obs.api.v1.security_contract_registry import AuditMode, ConcealmentMode, get_route_security_contract

    contract = get_route_security_contract(method, path)

    async def _dependency(
        request: Request,
        principal: Principal = Depends(get_current_principal),
        db: Database = Depends(get_db),
    ) -> AsyncIterator[None]:
        writer = AuditLogWriter(db, build_audit_context(request, principal))
        resource_id = _path_resource_id(request)

        def details() -> dict[str, Any]:
            return getattr(request.state, "contract_audit_details", {})

        def outcome() -> AuditOutcome:
            return getattr(request.state, "contract_audit_outcome", AuditOutcome.SUCCESS)

        try:
            if contract.audit_mode == AuditMode.ATOMIC:
                async with db.transaction():
                    yield
                    await writer.write_contract(
                        method,
                        path,
                        resource_id=resource_id,
                        details=details(),
                        outcome=outcome(),
                        commit=False,
                    )
            else:
                yield
                await writer.write_contract(method, path, resource_id=resource_id, details=details(), outcome=outcome())
        except HTTPException as exc:
            denied = exc.status_code in {401, 403} or (exc.status_code == 404 and contract.concealment == ConcealmentMode.NOT_FOUND)
            await _write_failure_event(
                writer,
                method,
                path,
                resource_id,
                AuditOutcome.DENIED if denied else AuditOutcome.FAILED,
            )
            raise
        except Exception:
            await _write_failure_event(writer, method, path, resource_id, AuditOutcome.FAILED)
            raise

    _dependency.__name__ = f"audit_{method.lower()}_{contract.audit_action.replace('.', '_')}"
    _dependency.__audit_contract__ = (method.upper(), path)  # type: ignore[attr-defined]
    return _dependency
