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
from obs.api.v1.redaction import REDACTED
from obs.core.registry import get_registry
from obs.db.database import Database, get_db
from obs.models.binding import (
    AdapterBindingCreate,
    AdapterBindingUpdate,
)

router = APIRouter(tags=["bindings"])

WEBHOOK_ADAPTER_TYPE = "WEBHOOK"


async def _clear_stale_external_write_enabled(dp_id: uuid.UUID, *, adapter_type: str, enabled: bool) -> None:
    """A newly enabled, non-MESSAGE binding takes precedence over the
    external_write_enabled opt-in at request time (see WriteRouter.handle()
    and datapoints.py's has_write_semantic_binding), but the flag itself
    stays stored on the DataPoint. Left untouched, it would silently
    reactivate — without a fresh admin decision — if this binding is later
    deleted or disabled (Codex review, issue #1169 follow-up)."""
    if not enabled or adapter_type == "MESSAGE":
        return
    reg = get_registry()
    dp = reg.get(dp_id)
    if dp is not None and getattr(dp, "external_write_enabled", False):
        from obs.models.datapoint import DataPointUpdate

        await reg.update(dp_id, DataPointUpdate(external_write_enabled=False))


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
    # A webhook binding is managed by a human with operator rights on both the
    # DataPoint and the instance; only a non-user principal (an API key) is
    # kept out, since the empty capability set below exists for exactly that.
    if adapter_type == WEBHOOK_ADAPTER_TYPE and principal.type == "user":
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
    if adapter_type == WEBHOOK_ADAPTER_TYPE and direction != "SOURCE":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "WEBHOOK-Bindings unterstützen nur Richtung SOURCE",
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


class GroupAddressInputError(HTTPException):
    """422 for a KNX group address that cannot be stored (#1296).

    ``detail`` is structured — ``code`` (``knxGroupAddressMissing`` or
    ``knxGroupAddressInvalid``), ``field``, ``value`` and a ``message`` — so the
    GUI can explain it in the user's language with an example in the project's
    style instead of showing a validator dump.
    """

    def __init__(self, code: str, field: str, value: Any, message: str) -> None:
        self.message = message
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": code, "field": field, "value": value, "message": message})

    def __str__(self) -> str:
        return f"{self.status_code}: {self.message}"


def _normalize_knx_group_addresses(adapter_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """KNX: store group addresses only in the internal notation (#1296).

    Runs before the schema validation, so a missing or invalid address is
    reported as :class:`GroupAddressInputError`, including a broken feedback
    address, which the binding model itself tolerates as absent for stored data.
    """
    if adapter_type != "KNX":
        return config
    from obs.adapters.knx.group_address import InvalidGroupAddress, normalize_ga

    command = config.get("group_address")
    if not str(command or "").strip():
        raise GroupAddressInputError("knxGroupAddressMissing", "group_address", command, "Gruppenadresse fehlt")
    normalized = dict(config)
    for key in ("group_address", "state_group_address"):
        value = config.get(key)
        if not str(value or "").strip():
            continue
        try:
            normalized[key] = normalize_ga(value)
        except InvalidGroupAddress as exc:
            raise GroupAddressInputError("knxGroupAddressInvalid", key, value, str(exc)) from exc
    return normalized


def _ensure_webhook_target_allowed(dp_id: uuid.UUID) -> None:
    """Refuse a webhook binding on a central-plant DataPoint (issue #1256).

    The trigger call has no principal, so the only thing standing between a
    device "mounted outside the house, configured in clear text" and the
    DataPoint is its token.  ``write_value()`` draws the same line for the
    anonymous Visu path, and the reasoning is stronger here.  The adapter
    re-checks this on every call, because a DataPoint can be reclassified after
    its binding was created.
    """
    dp = get_registry().get(dp_id)
    if getattr(dp, "control_class", "room_local") == "central_plant":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "WEBHOOK-Bindings sind für Datenpunkte der Klasse 'central_plant' nicht erlaubt",
        )


async def _ensure_webhook_slug_free(
    db: Database,
    instance_id: str,
    slug: str,
    *,
    exclude_binding_id: str | None = None,
) -> None:
    """A slug is the public name of one endpoint, so it must be unique per instance."""
    rows = await db.fetchall(
        "SELECT id, config FROM adapter_bindings WHERE adapter_instance_id=? AND adapter_type=?",
        (instance_id, WEBHOOK_ADAPTER_TYPE),
    )
    for row in rows:
        if exclude_binding_id is not None and row["id"] == exclude_binding_id:
            continue
        if _json_config(row["config"]).get("slug") == slug:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Der Slug '{slug}' ist in dieser Webhook-Instanz bereits vergeben",
            )


def _webhook_config_with_token(config: dict[str, Any], *, stored_token: str | None) -> dict[str, Any]:
    """Return *config* with a server-owned token and a normalised slug.

    A client never supplies or changes the token: on create one is generated,
    on update the stored one is kept.  Rotation goes through the dedicated
    ``/rotate-token`` route so it is audited as its own operation.

    ``_validate_adapter_binding()`` has already rejected a malformed config
    against the very same schema, so this only adds the token and returns the
    normalised result.
    """
    from obs.adapters.webhook.adapter import WebhookBindingConfig, generate_token

    parsed = WebhookBindingConfig(**{**config, "token": stored_token or generate_token()})
    return parsed.model_dump()


def _validate_timer_output_value(adapter_type: str, config: dict[str, Any], dp_id: uuid.UUID) -> None:
    """Reject a Zeitschaltuhr switching value that the target DataPoint type cannot hold.

    Issue #1008: the switching value used to be parsed type-blind at fire time, so an
    incompatible value was only discovered (and silently dropped) hours later. Validating
    it on save surfaces the problem immediately as a 422.

    An *omitted* value is not "no value": both routes store the config as sent, and the
    adapter then fills in its own default when the schedule point fires. So the default
    is what has to hold for the target type — otherwise a DATE/TIME/DATETIME point would
    be accepted here and dropped at every firing, the very failure mode this guards
    against (Codex review).
    """
    if adapter_type != "ZEITSCHALTUHR":
        return
    if str(config.get("timer_type", "daily")) == "meta":
        return
    dp = get_registry().get(dp_id)
    if dp is None:
        return

    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig
    from obs.models.types import coerce_text_value_for_type

    raw = config.get("value", ZeitschaltuhrBindingConfig.model_fields["value"].default)

    try:
        coerce_text_value_for_type(str(raw), dp.data_type)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Ungültiger Schaltwert für Objekttyp {dp.data_type}: {exc}",
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


def _redacted_config(adapter_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Keep a webhook token out of the generic binding listing (issue #1256).

    The token is a bearer secret for one DataPoint, so it is served only by
    ``GET /adapters/instances/{id}/webhook/bindings``, which requires a WRITE
    grant on the adapter instance.  Redacting here is safe because create and
    update never accept a client-supplied token — they generate one or keep the
    stored one — so a GUI round-trip cannot overwrite it with the placeholder.
    """
    if adapter_type != WEBHOOK_ADAPTER_TYPE or not config.get("token"):
        return config
    return {**config, "token": REDACTED}


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
        config=_redacted_config(row["adapter_type"], _json_config(row["config"])),
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

    config = _normalize_knx_group_addresses(adapter_type, body.config)
    _validate_adapter_binding(
        adapter_type,
        body.direction,
        config,
        enabled=body.enabled,
        instance_config=_json_config(instance_row["config"]) if adapter_type == "MESSAGE" else None,
    )
    _validate_timer_output_value(adapter_type, config, dp_id)

    effective_config = config
    if adapter_type == WEBHOOK_ADAPTER_TYPE:
        _ensure_webhook_target_allowed(dp_id)
        effective_config = _webhook_config_with_token(config, stored_token=None)

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

    # Held across the insert and the opt-in cleanup below — the same lock
    # update_datapoint() holds across its own bindingless check and persist
    # — so the two operations can't race: whichever acquires it first either
    # commits before the other's check runs, or leaves a state the other
    # then observes correctly (Codex review, see datapoints.py).
    reg = get_registry()
    async with reg.external_write_lock:
        # Inside the lock, which every other binding create/update also takes,
        # so two concurrent creates cannot both pass the uniqueness check.
        if adapter_type == WEBHOOK_ADAPTER_TYPE:
            await _ensure_webhook_slug_free(db, str(body.adapter_instance_id), effective_config["slug"])
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
                json.dumps(effective_config),
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
        await _clear_stale_external_write_enabled(dp_id, adapter_type=adapter_type, enabled=body.enabled)
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
    if "config" in updates:
        config = _normalize_knx_group_addresses(row["adapter_type"], config)
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
    if "config" in updates:
        _validate_timer_output_value(row["adapter_type"], config, dp_id)
    config_val = json.dumps(config)

    if row["adapter_type"] == WEBHOOK_ADAPTER_TYPE:
        # A binding on a since-reclassified DataPoint can still be switched off;
        # only a binding that would be live has to point at an allowed target.
        if enabled:
            _ensure_webhook_target_allowed(dp_id)
        stored_token = _json_config(row["config"]).get("token") or None
        config = _webhook_config_with_token(config, stored_token=stored_token)
        config_val = json.dumps(config)

    # Formel validieren
    if formula:
        from obs.core.formula import validate_formula

        err = validate_formula(formula)
        if err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ungültige Formel: {err}")

    # Held across the update and the opt-in cleanup below — see create_binding()
    # above and datapoints.py's update_datapoint() for why (Codex review).
    reg = get_registry()
    async with reg.external_write_lock:
        # See create_binding(): the uniqueness check belongs inside the lock.
        if row["adapter_type"] == WEBHOOK_ADAPTER_TYPE:
            await _ensure_webhook_slug_free(
                db,
                row["adapter_instance_id"],
                config["slug"],
                exclude_binding_id=str(binding_id),
            )
            # The token was read before the lock was taken; a rotation that ran
            # in between must win, so take the stored one again under the lock.
            fresh = await db.fetchone("SELECT config FROM adapter_bindings WHERE id=?", (str(binding_id),))
            config = {**config, "token": _json_config(fresh["config"] if fresh is not None else None).get("token") or config.get("token")}
            config_val = json.dumps(config)
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
        await _clear_stale_external_write_enabled(dp_id, adapter_type=row["adapter_type"], enabled=bool(enabled))

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
