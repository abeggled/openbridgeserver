"""Closed, database-backed configuration capabilities for API-key principals."""

from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException, Request, status

from obs.api.audit import AuditLogWriter, build_audit_context
from obs.api.auth import Principal
from obs.db.database import Database


class ConfigCapability(StrEnum):
    VISU_PAGE_CONFIG_WRITE = "visu.page_config.write"
    DATAPOINT_METADATA_WRITE = "datapoint.metadata.write"


CONFIG_CAPABILITIES = tuple(capability.value for capability in ConfigCapability)


def api_key_id(principal: Principal) -> str | None:
    if principal.type != "api_key" or not principal.subject.startswith("api_key:"):
        return None
    return principal.subject.removeprefix("api_key:")


async def _write_use_audit(
    db: Database,
    principal: Principal,
    capability: ConfigCapability,
    *,
    target_type: str,
    target_id: str,
    result: str,
    request: Request | None,
) -> None:
    key_id = api_key_id(principal)
    if key_id is None:
        return
    writer = AuditLogWriter(db, build_audit_context(request, principal.subject))
    await writer.write(
        "api_key.capability.use",
        resource_type=target_type,
        resource_id=target_id,
        details={
            "api_key_id": key_id,
            "capability": capability.value,
            "target_type": target_type,
            "target_id": target_id,
            "result": result,
        },
    )


async def require_config_capability(
    db: Database,
    principal: Principal,
    capability: ConfigCapability,
    *,
    target_type: str,
    target_id: str,
    request: Request | None,
) -> bool:
    """Authorize the operation gate and return whether an API key used it."""
    if principal.type == "user" and principal.is_admin:
        return False

    key_id = api_key_id(principal)
    allowed = False
    if key_id is not None:
        row = await db.fetchone(
            "SELECT 1 FROM api_key_capabilities WHERE key_id=? AND capability=?",
            (key_id, capability.value),
        )
        allowed = row is not None
    if not allowed:
        await _write_use_audit(
            db,
            principal,
            capability,
            target_type=target_type,
            target_id=target_id,
            result="denied",
            request=request,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Configuration capability required")
    return True


async def audit_config_capability_use(
    db: Database,
    principal: Principal,
    capability: ConfigCapability,
    *,
    target_type: str,
    target_id: str,
    allowed: bool,
    request: Request | None,
) -> None:
    await _write_use_audit(
        db,
        principal,
        capability,
        target_type=target_type,
        target_id=target_id,
        result="allowed" if allowed else "denied",
        request=request,
    )
