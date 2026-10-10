"""Binding delegation matrix over every registered adapter type (issue #1303).

Contract (``docs/authz-creation-authority.md``): creating, changing or deleting
an adapter binding is allowed exactly when the principal is an admin, or when it
is a *user* principal and the adapter type declares
``AdapterDelegationCapability.LINK_BINDING``.  API keys never get binding
delegation, whatever their grants.

The adapter types are taken from the registry after importing the adapter block
of ``obs/main.py`` — the authoritative list of enabled adapters — so a newly
added adapter is part of the matrix automatically, and the explicit expectation
table below makes the test fail until its delegation is decided on purpose.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from obs.adapters import registry as adapter_registry
from obs.adapters.base import AdapterDelegationCapability
from obs.api.auth import Principal
from obs.api.v1 import bindings as bindings_api
from obs.db.database import Database
from obs.models.binding import AdapterBindingCreate, AdapterBindingUpdate

NOW = "2026-10-08T00:00:00+00:00"
MAIN_PY = Path(__file__).resolve().parents[2] / "obs" / "main.py"

# Whether a non-admin *user* may manage bindings of this adapter type.  Adding
# an adapter to obs/main.py without deciding this fails the test below.
EXPECTED_USER_BINDING_DELEGATION = {
    "ANWESENHEITSSIMULATION": True,
    "HOME_ASSISTANT": False,
    "IOBROKER": True,
    "KNX": False,
    "MESSAGE": False,
    "MODBUS_RTU": False,
    "MODBUS_TCP": False,
    "MQTT": True,
    "ONEWIRE": False,
    "SNMP": False,
    "WEBHOOK": True,
    "ZEITSCHALTUHR": False,
}


def _main_adapter_modules() -> list[str]:
    """Module names of the adapter import block in ``obs/main.py``."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    modules = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("obs.adapters.") and alias.name.endswith(".adapter")
    ]
    assert modules, "adapter import block not found in obs/main.py"
    return modules


def _registered_adapters() -> dict[str, type]:
    for module in _main_adapter_modules():
        importlib.import_module(module)
    return {cls.adapter_type: cls for cls in adapter_registry.all_classes().values() if cls.__module__ in _main_adapter_modules()}


REGISTERED = _registered_adapters()
PRINCIPALS = {
    "admin": Principal(subject="admin", type="user", is_admin=True),
    "user": Principal(subject="alice", type="user", is_admin=False),
    "api_key": Principal(subject="api_key:device", type="api_key", is_admin=False),
}


def _expected_allowed(principal_kind: str, adapter_type: str) -> bool:
    if principal_kind == "admin":
        return True
    declared = AdapterDelegationCapability.LINK_BINDING in REGISTERED[adapter_type].delegation_capabilities
    return principal_kind == "user" and declared


class _PassedAuthz(Exception):
    """Raised by the first post-authorization step to mark an allowed request."""


class _RegistryStub:
    def __init__(self, dp_id: uuid.UUID):
        self._dp = SimpleNamespace(id=dp_id)
        self.external_write_lock = asyncio.Lock()

    def get(self, dp_id: uuid.UUID):
        return self._dp if dp_id == self._dp.id else None


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _grant(db: Database, principal_type: str, principal_id: str, node_type: str, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES (?, ?, ?, ?, 'operator', 'allow')
        """,
        (principal_type, principal_id, node_type, node_id),
    )


async def _setup(db: Database, adapter_type: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    dp_id, instance_id, binding_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, control_class, created_at, updated_at)
        VALUES (?, 'Matrix', 'BOOLEAN', NULL, '[]', ?, NULL, 1, 1, 'room_local', ?, ?)
        """,
        (str(dp_id), f"dp/{dp_id}/value", NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'Matrix', '{}', 0, ?, ?)
        """,
        (str(instance_id), adapter_type, NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'SOURCE', '{}', 1, ?, ?)
        """,
        (str(binding_id), str(dp_id), adapter_type, str(instance_id), NOW, NOW),
    )
    for principal_type, principal_id in (("user", "alice"), ("api_key", "device")):
        await _grant(db, principal_type, principal_id, "datapoint", str(dp_id))
        await _grant(db, principal_type, principal_id, "adapter_instance", str(instance_id))
    return dp_id, instance_id, binding_id


def _raise_passed(*_args, **_kwargs) -> None:
    raise _PassedAuthz


async def _attempt(db: Database, operation: str, principal: Principal, dp_id, instance_id, binding_id) -> bool:
    try:
        if operation == "create":
            await bindings_api.create_binding(
                dp_id=dp_id,
                body=AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE"),
                _user=principal,
                db=db,
            )
        elif operation == "update":
            await bindings_api.update_binding(
                dp_id=dp_id,
                binding_id=binding_id,
                body=AdapterBindingUpdate(enabled=False),
                _user=principal,
                db=db,
            )
        else:
            await bindings_api.delete_binding(dp_id=dp_id, binding_id=binding_id, _user=principal, db=db)
            return await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (str(binding_id),)) is None
    except _PassedAuthz:
        return True
    except HTTPException as exc:
        assert exc.status_code == 403, exc.detail
        return False
    raise AssertionError("create/update must stop at the validation sentinel")


def test_every_registered_adapter_has_a_deliberate_binding_delegation_decision() -> None:
    assert set(REGISTERED) == set(EXPECTED_USER_BINDING_DELEGATION)
    for adapter_type, expected in EXPECTED_USER_BINDING_DELEGATION.items():
        assert _expected_allowed("user", adapter_type) is expected, adapter_type


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize("principal_kind", list(PRINCIPALS))
@pytest.mark.parametrize("adapter_type", sorted(REGISTERED))
async def test_binding_delegation_matrix(monkeypatch, db: Database, adapter_type: str, principal_kind: str, operation: str) -> None:
    monkeypatch.setattr(adapter_registry, "_adapters", dict(REGISTERED))
    dp_id, instance_id, binding_id = await _setup(db, adapter_type)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))
    monkeypatch.setattr(bindings_api, "_validate_adapter_binding", _raise_passed)
    monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())

    allowed = await _attempt(db, operation, PRINCIPALS[principal_kind], dp_id, instance_id, binding_id)

    assert allowed is _expected_allowed(principal_kind, adapter_type)
