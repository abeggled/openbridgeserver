"""Bindings API — Phase 4 / Phase 5 (Multi-Instance)

GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}

Phase 5: Bindings referenzieren adapter_instance_id (UUID), nicht mehr adapter_type.
adapter_type wird aus der Instanz abgeleitet und denormalisiert gespeichert.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from obs.api.audit import contract_audit, set_contract_audit_resource_id
from obs.api.auth import Principal, get_current_principal
from obs.api.authz import AuthzAction, AuthzTarget, authorize
from obs.api.authz_service import (
    authorize_adapter_instance,
    filter_authorized_datapoints,
    load_role_grants,
    resolve_datapoint_targets,
)
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.models.binding import (
    AdapterBindingCreate,
    AdapterBindingUpdate,
)

router = APIRouter(tags=["bindings"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class BindingOut(BaseModel):
    id: uuid.UUID
    datapoint_id: uuid.UUID
    adapter_type: str
    adapter_instance_id: uuid.UUID | None
    instance_name: str | None
    direction: str
    config: dict
    enabled: bool
    send_throttle_ms: int | None = None
    send_on_change: bool = False
    send_min_delta: float | None = None
    send_min_delta_pct: float | None = None
    value_formula: str | None = None
    value_map: dict[str, str] | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _get_instance_name_map(db: Database) -> dict[str, str]:
    """instance_id → name Mapping aus DB."""
    rows = await db.fetchall("SELECT id, name FROM adapter_instances")
    return {row["id"]: row["name"] for row in rows}


async def _get_bindings_for_dp(db: Database, dp_id: uuid.UUID) -> list[BindingOut]:
    rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE datapoint_id=? ORDER BY created_at",
        (str(dp_id),),
    )
    name_map = await _get_instance_name_map(db)
    return [_row_out(r, name_map) for r in rows]


def _principal_from_dependency(value: Principal | str) -> Principal:
    if isinstance(value, Principal):
        return value
    return Principal(
        subject=value,
        type="api_key" if value.startswith("api_key:") else "user",
        is_admin=value == "admin",
    )


def _is_admin_principal(principal: Principal) -> bool:
    return principal.type == "user" and principal.is_admin


async def _ensure_datapoint_readable(db: Database, principal: Principal, dp_id: uuid.UUID) -> None:
    if _is_admin_principal(principal):
        return
    allowed = await filter_authorized_datapoints(db, principal, [str(dp_id)], action=AuthzAction.READ)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} nicht gefunden")


async def _ensure_binding_mutation_scope(db: Database, principal: Principal, dp_id: uuid.UUID) -> None:
    if _is_admin_principal(principal):
        return

    targets_by_dp = await resolve_datapoint_targets(db, [str(dp_id)])
    grants = await load_role_grants(db, principal)
    dp_targets = targets_by_dp.get(str(dp_id), [])
    dp_control_class = dp_targets[0].control_class if dp_targets else "room_local"
    direct_target = AuthzTarget(
        node_type="datapoint",
        node_id=str(dp_id),
        min_role="operator",
        control_class=dp_control_class,
    )
    direct_decision = authorize(
        principal=principal,
        action=AuthzAction.WRITE,
        targets=[direct_target],
        grants=grants,
    )
    if direct_decision.reason == "explicit_deny":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Binding-Änderung nicht erlaubt")

    targets = [
        AuthzTarget(
            node_type=target.node_type,
            node_id=target.node_id,
            ancestors=target.ancestors,
            min_role="operator",
            control_class=target.control_class,
        )
        for target in targets_by_dp.get(str(dp_id), [])
    ]
    decision = authorize(
        principal=principal,
        action=AuthzAction.WRITE,
        targets=targets,
        grants=grants,
    )
    if decision.reason == "explicit_deny":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Binding-Änderung nicht erlaubt")
    if not decision.allowed and not direct_decision.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Binding-Änderung nicht erlaubt")


def _ensure_adapter_delegates_binding(principal: Principal, adapter_type: str) -> None:
    if _is_admin_principal(principal):
        return

    from obs.adapters.base import AdapterDelegationCapability
    from obs.adapters.registry import supports_delegation

    if not supports_delegation(adapter_type, AdapterDelegationCapability.LINK_BINDING):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adapter-Typ erlaubt keine delegierte Binding-Änderung")


async def _filter_bindings_by_instance_read(
    db: Database,
    principal: Principal,
    bindings: list[BindingOut],
) -> list[BindingOut]:
    if _is_admin_principal(principal):
        return bindings
    instance_ids = [b.adapter_instance_id for b in bindings if b.adapter_instance_id is not None]
    if not instance_ids:
        return bindings
    grants = await load_role_grants(db, principal, node_type="adapter_instance")
    result = []
    for binding in bindings:
        if binding.adapter_instance_id is None:
            result.append(binding)
            continue
        decision = authorize(
            principal=principal,
            action=AuthzAction.READ,
            targets=[AuthzTarget(node_type="adapter_instance", node_id=str(binding.adapter_instance_id), min_role="guest")],
            grants=grants,
        )
        if decision.allowed:
            result.append(binding)
    return result


async def _ensure_adapter_instance_binding_scope(
    db: Database,
    principal: Principal,
    instance_id: str | None,
    adapter_type: str,
) -> None:
    if _is_admin_principal(principal):
        return
    if instance_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adapter-Instanz-Berechtigung erforderlich")
    decision = await authorize_adapter_instance(
        db,
        principal,
        instance_id,
        action=AuthzAction.WRITE,
        min_role="operator",
    )
    if not decision.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adapter-Instanz-Berechtigung erforderlich")
    _ensure_adapter_delegates_binding(principal, adapter_type)


async def _reload_adapter_instance(instance_id: str, db: Database) -> None:
    """Laufende Adapter-Instanz über ihre Bindings aus DB informieren."""
    from obs.adapters import registry as adapter_registry

    await adapter_registry.reload_instance_bindings(instance_id, db)


def _validate_adapter_binding(
    adapter_type: str,
    direction: str,
    config: dict[str, Any],
    *,
    validate_schema: bool = True,
    enabled: bool = True,
    instance_config: dict[str, Any] | None = None,
) -> None:
    if adapter_type == "MESSAGE" and direction != "SOURCE":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "MESSAGE-Bindings unterstützen nur Richtung SOURCE",
        )
    if adapter_type != "MESSAGE" and not validate_schema:
        return

    from obs.adapters.registry import get_class

    cls = get_class(adapter_type)
    if cls and hasattr(cls, "binding_config_schema"):
        try:
            schema_config = {**config, "enabled": enabled} if adapter_type == "MESSAGE" else config
            binding_config = cls.binding_config_schema(**schema_config)
            if adapter_type == "MESSAGE" and enabled and instance_config is not None:
                _validate_message_target_refs(binding_config, instance_config)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Ungültige Binding-Config: {exc}",
            ) from exc


def _json_config(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return dict(raw)


def _validate_message_target_refs(binding_config: Any, instance_config: dict[str, Any]) -> None:
    from obs.adapters.message.adapter import MessageAdapterConfig
    from obs.adapters.message.providers.registry import get_provider

    adapter_config = MessageAdapterConfig(**instance_config)
    for ref in binding_config.providers:
        provider_config = adapter_config.providers.get(ref.provider)
        if provider_config is None:
            raise ValueError(f"MESSAGE provider not configured: {ref.provider}")
        provider = get_provider(ref.provider)
        if provider is None:
            raise ValueError(f"MESSAGE provider not registered: {ref.provider}")
        parsed_provider_config = provider.config_schema(**provider_config)
        if not getattr(parsed_provider_config, "enabled", False):
            raise ValueError(f"MESSAGE provider is disabled: {ref.provider}")
        targets = getattr(parsed_provider_config, "targets", {}) or {}
        if ref.target not in targets:
            raise ValueError(f"MESSAGE target not configured: {ref.provider}/{ref.target}")


def _row_out(row: Any, name_map: dict[str, str] | None = None) -> BindingOut:
    instance_id = row["adapter_instance_id"]
    throttle = row["send_throttle_ms"]
    min_delta = row["send_min_delta"]
    min_delta_p = row["send_min_delta_pct"]
    return BindingOut(
        id=uuid.UUID(row["id"]),
        datapoint_id=uuid.UUID(row["datapoint_id"]),
        adapter_type=row["adapter_type"],
        adapter_instance_id=uuid.UUID(instance_id) if instance_id else None,
        instance_name=name_map.get(instance_id) if name_map and instance_id else None,
        direction=row["direction"],
        config=_json_config(row["config"]),
        enabled=bool(row["enabled"]),
        send_throttle_ms=int(throttle) if throttle is not None else None,
        send_on_change=bool(row["send_on_change"]),
        send_min_delta=float(min_delta) if min_delta is not None else None,
        send_min_delta_pct=float(min_delta_p) if min_delta_p is not None else None,
        value_formula=row["value_formula"] or None,
        value_map=json.loads(row["value_map"]) if row["value_map"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{dp_id}/bindings", response_model=list[BindingOut])
async def list_bindings(
    dp_id: uuid.UUID,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> list[BindingOut]:
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} nicht gefunden")
    principal = _principal_from_dependency(_user)
    await _ensure_datapoint_readable(db, principal, dp_id)
    bindings = await _get_bindings_for_dp(db, dp_id)
    return await _filter_bindings_by_instance_read(db, principal, bindings)


@router.post(
    "/{dp_id}/bindings",
    response_model=BindingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(contract_audit("POST", "/api/v1/datapoints/{dp_id}/bindings"))],
)
async def create_binding(
    dp_id: uuid.UUID,
    body: AdapterBindingCreate,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
    request: Request = None,
) -> BindingOut:
    principal = _principal_from_dependency(_user)
    await _ensure_binding_mutation_scope(db, principal, dp_id)
    if get_registry().get(dp_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"DataPoint {dp_id} nicht gefunden")

    # Instanz aus DB laden → adapter_type ableiten
    instance_row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(body.adapter_instance_id),))
    if instance_row is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Adapter-Instanz '{body.adapter_instance_id}' nicht gefunden",
        )
    adapter_type = instance_row["adapter_type"]
    await _ensure_adapter_instance_binding_scope(
        db,
        principal,
        str(body.adapter_instance_id),
        adapter_type,
    )

    _validate_adapter_binding(
        adapter_type,
        body.direction,
        body.config,
        enabled=body.enabled,
        instance_config=_json_config(instance_row["config"]) if adapter_type == "MESSAGE" else None,
    )

    # Formel validieren
    if body.value_formula:
        from obs.core.formula import validate_formula

        err = validate_formula(body.value_formula)
        if err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ungültige Formel: {err}")

    binding_id = str(uuid.uuid4())
    if request is not None:
        set_contract_audit_resource_id(request, binding_id)
    now = datetime.now(UTC).isoformat()

    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled,
            send_throttle_ms, send_on_change, send_min_delta, send_min_delta_pct,
            value_formula, value_map, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            binding_id,
            str(dp_id),
            adapter_type,
            str(body.adapter_instance_id),
            body.direction,
            json.dumps(body.config),
            int(body.enabled),
            body.send_throttle_ms,
            int(body.send_on_change),
            body.send_min_delta,
            body.send_min_delta_pct,
            body.value_formula or None,
            json.dumps(body.value_map) if body.value_map else None,
            now,
            now,
        ),
    )
    await _reload_adapter_instance(str(body.adapter_instance_id), db)

    row = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (binding_id,))
    name_map = await _get_instance_name_map(db)
    return _row_out(row, name_map)


@router.patch(
    "/{dp_id}/bindings/{binding_id}",
    response_model=BindingOut,
    dependencies=[Depends(contract_audit("PATCH", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"))],
)
async def update_binding(
    dp_id: uuid.UUID,
    binding_id: uuid.UUID,
    body: AdapterBindingUpdate,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> BindingOut:
    principal = _principal_from_dependency(_user)
    await _ensure_binding_mutation_scope(db, principal, dp_id)
    row = await db.fetchone(
        "SELECT * FROM adapter_bindings WHERE id=? AND datapoint_id=?",
        (str(binding_id), str(dp_id)),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding nicht gefunden")
    await _ensure_adapter_instance_binding_scope(
        db,
        principal,
        row["adapter_instance_id"],
        row["adapter_type"],
    )

    updates = body.model_dump(exclude_unset=True)
    now = datetime.now(UTC).isoformat()

    direction = updates.get("direction", row["direction"])
    config = updates.get("config", _json_config(row["config"]))
    config_val = json.dumps(config)
    enabled = int(updates.get("enabled", bool(row["enabled"])))
    throttle_ms = updates.get("send_throttle_ms", row["send_throttle_ms"])
    on_change = int(updates.get("send_on_change", bool(row["send_on_change"])))
    min_delta = updates.get("send_min_delta", row["send_min_delta"])
    min_delta_pct = updates.get("send_min_delta_pct", row["send_min_delta_pct"])
    formula = updates.get("value_formula", row["value_formula"]) or None
    value_map_new = updates.get("value_map", json.loads(row["value_map"]) if row["value_map"] else None)
    value_map_json = json.dumps(value_map_new) if value_map_new else None
    instance_config: dict[str, Any] | None = None
    if row["adapter_type"] == "MESSAGE" and row["adapter_instance_id"]:
        instance_row = await db.fetchone("SELECT config FROM adapter_instances WHERE id=?", (row["adapter_instance_id"],))
        if instance_row is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "MESSAGE adapter instance not found")
        instance_config = _json_config(instance_row["config"])

    _validate_adapter_binding(
        row["adapter_type"],
        direction,
        config,
        validate_schema="config" in updates or (row["adapter_type"] == "MESSAGE" and "enabled" in updates),
        enabled=bool(enabled),
        instance_config=instance_config,
    )

    # Formel validieren
    if formula:
        from obs.core.formula import validate_formula

        err = validate_formula(formula)
        if err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ungültige Formel: {err}")

    await db.execute_and_commit(
        """UPDATE adapter_bindings
           SET direction=?, config=?, enabled=?,
               send_throttle_ms=?, send_on_change=?, send_min_delta=?, send_min_delta_pct=?,
               value_formula=?, value_map=?, updated_at=?
           WHERE id=?""",
        (
            direction,
            config_val,
            enabled,
            throttle_ms,
            on_change,
            min_delta,
            min_delta_pct,
            formula,
            value_map_json,
            now,
            str(binding_id),
        ),
    )

    instance_id = row["adapter_instance_id"]
    if instance_id:
        await _reload_adapter_instance(instance_id, db)

    updated = await db.fetchone("SELECT * FROM adapter_bindings WHERE id=?", (str(binding_id),))
    name_map = await _get_instance_name_map(db)
    return _row_out(updated, name_map)


@router.delete(
    "/{dp_id}/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(contract_audit("DELETE", "/api/v1/datapoints/{dp_id}/bindings/{binding_id}"))],
)
async def delete_binding(
    dp_id: uuid.UUID,
    binding_id: uuid.UUID,
    _user: Principal | str = Depends(get_current_principal),
    db: Database = Depends(lambda: get_db()),
) -> None:
    principal = _principal_from_dependency(_user)
    await _ensure_binding_mutation_scope(db, principal, dp_id)
    row = await db.fetchone(
        "SELECT adapter_type, adapter_instance_id FROM adapter_bindings WHERE id=? AND datapoint_id=?",
        (str(binding_id), str(dp_id)),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Binding nicht gefunden")
    await _ensure_adapter_instance_binding_scope(
        db,
        principal,
        row["adapter_instance_id"],
        row["adapter_type"],
    )

    instance_id = row["adapter_instance_id"]
    await db.execute_and_commit("DELETE FROM adapter_bindings WHERE id=?", (str(binding_id),))
    if instance_id:
        await _reload_adapter_instance(instance_id, db)
