"""Contract-audit scope shared by application configuration endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import wraps
import inspect
import logging
from typing import Any, Callable

from fastapi import HTTPException, Request

from obs.api.audit import AuditLogWriter, AuditOutcome, build_audit_context
from obs.api.auth import Principal
from obs.db.database import Database

logger = logging.getLogger(__name__)


@asynccontextmanager
async def audit_application_route(
    db: Database,
    request: Request | None,
    principal: Principal | str | None,
    method: str,
    path: str,
    *,
    resource_id: str | None = None,
    audit_not_found: bool = True,
) -> AsyncIterator[AuditLogWriter]:
    """Audit denials/failures while leaving success timing to the endpoint."""
    actual_request = request if isinstance(request, Request) else None
    writer = AuditLogWriter(db=db, context=build_audit_context(actual_request, principal))
    try:
        yield writer
    except HTTPException as exc:
        if getattr(exc, "__audit_contract_written__", False):
            raise
        if exc.status_code == 404 and not audit_not_found:
            raise
        outcome = AuditOutcome.DENIED if 400 <= exc.status_code < 500 else AuditOutcome.FAILED
        try:
            await writer.write_contract(method, path, resource_id=resource_id, outcome=outcome)
        except Exception:
            logger.exception("Could not persist application contract audit event for %s %s", method, path)
        raise
    except Exception:
        try:
            await writer.write_contract(method, path, resource_id=resource_id, outcome=AuditOutcome.FAILED)
        except Exception:
            logger.exception("Could not persist failed application contract audit event for %s %s", method, path)
        raise


def audit_application_contract(
    method: str,
    path: str,
    *,
    principal_param: str | None,
    resource_param: str | None = None,
    audit_not_found: bool = True,
) -> Callable:
    """Wrap a FastAPI endpoint with canonical denial/failure auditing."""

    def decorate(endpoint: Callable) -> Callable:
        signature = inspect.signature(endpoint)

        @wraps(endpoint)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            arguments = signature.bind_partial(*args, **kwargs).arguments
            db = arguments.get("db")
            # Preserve direct unit-call compatibility; FastAPI always injects
            # a concrete Database for live requests.
            if not isinstance(db, Database):
                return await endpoint(*args, **kwargs)
            principal = arguments.get(principal_param) if principal_param else None
            resource_id = str(arguments[resource_param]) if resource_param and arguments.get(resource_param) is not None else None
            async with audit_application_route(
                db,
                arguments.get("request"),
                principal,
                method,
                path,
                resource_id=resource_id,
                audit_not_found=audit_not_found,
            ):
                return await endpoint(*args, **kwargs)

        setattr(wrapped, "__audit_contract__", (method.upper(), path))
        return wrapped

    return decorate


def mark_contract_audited(exc: HTTPException) -> HTTPException:
    """Mark an HTTP error whose route already wrote a richer canonical event."""
    setattr(exc, "__audit_contract_written__", True)
    return exc


async def write_application_success(
    db: Database,
    request: Request | None,
    principal: Principal | str | None,
    method: str,
    path: str,
    *,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool,
) -> int:
    """Write the route's canonical success event."""
    actual_request = request if isinstance(request, Request) else None
    writer = AuditLogWriter(db=db, context=build_audit_context(actual_request, principal))
    return await writer.write_contract(method, path, resource_id=resource_id, details=details, commit=commit)
