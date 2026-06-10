"""Audit logging helpers for config-mutating API endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request

from obs.api.auth import optional_current_user
from obs.db.database import Database, get_db


@dataclass(frozen=True)
class AuditContext:
    actor: str
    request_id: str | None
    remote_addr: str | None
    user_agent: str | None


def build_audit_context(request: Request | None, current_user: str | None) -> AuditContext:
    if request is None:
        return AuditContext(
            actor=current_user or "anonymous",
            request_id=None,
            remote_addr=None,
            user_agent=None,
        )
    client_host = request.client.host if request.client else None
    return AuditContext(
        actor=current_user or "anonymous",
        request_id=request.headers.get("x-request-id"),
        remote_addr=client_host,
        user_agent=request.headers.get("user-agent"),
    )


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
    ) -> int:
        if not action.strip():
            raise ValueError("action must not be empty")

        payload = json.dumps(details or {}, separators=(",", ":"), sort_keys=True)
        cur = await self._db.execute_and_commit(
            """
            INSERT INTO audit_log_entries
                (actor, action, resource_type, resource_id, details_json, request_id, remote_addr, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        if cur is None:
            return 0
        return int(cur.lastrowid)


async def get_audit_log_writer(
    request: Request,
    current_user: str | None = Depends(optional_current_user),
    db: Database = Depends(get_db),
) -> AuditLogWriter:
    return AuditLogWriter(db=db, context=build_audit_context(request, current_user))
