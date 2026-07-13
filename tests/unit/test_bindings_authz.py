from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import bindings as bindings_api
from obs.db.database import Database
from obs.models.binding import AdapterBindingCreate, AdapterBindingUpdate


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, dp_id: uuid.UUID):
        self._dp = SimpleNamespace(id=dp_id)

    def get(self, dp_id: uuid.UUID):
        if dp_id == self._dp.id:
            return self._dp
        return None


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _principal(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


def test_admin_binding_delegation_keeps_legacy_adapter_access() -> None:
    bindings_api._ensure_adapter_delegates_binding(_principal("admin", is_admin=True), "UNKNOWN")


async def _insert_tree_and_nodes(db: Database) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (NOW, NOW),
    )
    await db.executemany(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', NULL, ?, '', 0, NULL, ?, ?)
        """,
        [
            ("allowed-room", "allowed-room", NOW, NOW),
            ("blocked-room", "blocked-room", NOW, NOW),
        ],
    )
    await db.commit()


async def _insert_datapoint(db: Database, dp_id: uuid.UUID, node_id: str, *, control_class: str = "room_local") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, control_class, created_at, updated_at)
        VALUES (?, 'Bindings AuthZ', 'FLOAT', NULL, '[]', ?, NULL, 1, 1, ?, ?, ?)
        """,
        (str(dp_id), f"dp/{dp_id}/value", control_class, NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{dp_id}", node_id, str(dp_id), NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, ?, ?, ?, ?)
        """,
        (principal_id, node_type, node_id, role, effect),
    )


async def _insert_instance(db: Database, instance_id: uuid.UUID, adapter_type: str = "ANWESENHEITSSIMULATION") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'Presence', '{}', 0, ?, ?)
        """,
        (str(instance_id), adapter_type, NOW, NOW),
    )


async def _insert_instance_grant(db: Database, instance_id: uuid.UUID, role: str = "operator") -> None:
    await _insert_grant(
        db,
        node_type="adapter_instance",
        node_id=str(instance_id),
        role=role,
    )


async def _insert_binding(
    db: Database,
    *,
    binding_id: uuid.UUID,
    dp_id: uuid.UUID,
    instance_id: uuid.UUID,
    adapter_type: str = "ANWESENHEITSSIMULATION",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'SOURCE', '{}', 1, ?, ?)
        """,
        (str(binding_id), str(dp_id), adapter_type, str(instance_id), NOW, NOW),
    )


@pytest.mark.asyncio
async def test_list_bindings_filters_bindings_by_instance_read_access(monkeypatch, db: Database):
    """Principal with datapoint READ but no instance grant must not see that binding."""
    dp_id = uuid.uuid4()
    allowed_instance_id = uuid.uuid4()
    blocked_instance_id = uuid.uuid4()
    binding_allowed = uuid.uuid4()
    binding_blocked = uuid.uuid4()

    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room")
    await _insert_instance(db, allowed_instance_id)
    await _insert_instance(db, blocked_instance_id)
    await _insert_instance_grant(db, allowed_instance_id, role="guest")
    await _insert_binding(db, binding_id=binding_allowed, dp_id=dp_id, instance_id=allowed_instance_id)
    await _insert_binding(db, binding_id=binding_blocked, dp_id=dp_id, instance_id=blocked_instance_id)

    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))
    visible = await bindings_api.list_bindings(
        dp_id=dp_id,
        _user=_principal("alice"),
        db=db,
    )

    assert len(visible) == 1
    assert visible[0].id == binding_allowed


@pytest.mark.asyncio
async def test_non_admin_list_bindings_requires_read_scope(monkeypatch, db: Database):
    allowed_dp = uuid.uuid4()
    blocked_dp = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, allowed_dp, "allowed-room")
    await _insert_datapoint(db, blocked_dp, "blocked-room")
    await _insert_grant(db, node_id="allowed-room")
    await _insert_instance(db, instance_id)
    await _insert_instance_grant(db, instance_id, role="guest")
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=allowed_dp, instance_id=instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=blocked_dp, instance_id=instance_id)

    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(allowed_dp))
    visible = await bindings_api.list_bindings(
        dp_id=allowed_dp,
        _user=_principal("alice"),
        db=db,
    )

    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(blocked_dp))
    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.list_bindings(
            dp_id=blocked_dp,
            _user=_principal("alice"),
            db=db,
        )

    assert len(visible) == 1
    assert visible[0].datapoint_id == allowed_dp
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_create_binding_requires_operator_scope(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room", role="guest")
    await _insert_instance(db, instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))

    body = AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE")
    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.create_binding(
            dp_id=dp_id,
            body=body,
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_create_binding_rejects_non_delegable_adapter_with_operator_scope(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_type="datapoint", node_id=str(dp_id), role="operator")
    await _insert_instance(db, instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))
    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.create_binding(
            dp_id=dp_id,
            body=AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE"),
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_create_binding_allows_delegable_mqtt_with_operator_scope(monkeypatch, db: Database):
    from obs.adapters import registry as adapter_registry
    from obs.adapters.mqtt.adapter import MqttAdapter

    dp_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_type="datapoint", node_id=str(dp_id), role="operator")
    await _insert_instance(db, instance_id, "MQTT")
    await _insert_instance_grant(db, instance_id)
    monkeypatch.setitem(adapter_registry._adapters, "MQTT", MqttAdapter)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))
    monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())

    created = await bindings_api.create_binding(
        dp_id=dp_id,
        body=AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE", config={"topic": "delegated/test"}),
        _user=_principal("alice"),
        db=db,
    )

    assert created.datapoint_id == dp_id
    assert created.adapter_type == "MQTT"


@pytest.mark.asyncio
async def test_non_admin_create_binding_hierarchy_deny_beats_direct_datapoint_allow(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_type="datapoint", node_id=str(dp_id), role="operator")
    await _insert_grant(db, node_id="allowed-room", role="operator", effect="deny")
    await _insert_instance(db, instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))

    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.create_binding(
            dp_id=dp_id,
            body=AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE"),
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_create_binding_direct_datapoint_deny_beats_hierarchy_operator(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room", role="operator")
    await _insert_grant(db, node_type="datapoint", node_id=str(dp_id), role="operator", effect="deny")
    await _insert_instance(db, instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))

    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.create_binding(
            dp_id=dp_id,
            body=AdapterBindingCreate(adapter_instance_id=instance_id, direction="SOURCE"),
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_update_binding_rejects_non_delegable_adapter_with_operator_scope(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room", role="operator")
    await _insert_instance(db, instance_id)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp_id, instance_id=instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))

    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.update_binding(
            dp_id=dp_id,
            binding_id=binding_id,
            body=AdapterBindingUpdate(enabled=False),
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_delete_binding_rejects_non_delegable_adapter_with_operator_scope(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room", role="operator")
    await _insert_instance(db, instance_id)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp_id, instance_id=instance_id)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))

    with pytest.raises(HTTPException) as exc_info:
        await bindings_api.delete_binding(
            dp_id=dp_id,
            binding_id=binding_id,
            _user=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_update_and_delete_binding_allow_delegable_mqtt_with_operator_scope(monkeypatch, db: Database):
    from obs.adapters import registry as adapter_registry
    from obs.adapters.mqtt.adapter import MqttAdapter

    dp_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room")
    await _insert_grant(db, node_id="allowed-room", role="operator")
    await _insert_instance(db, instance_id, "MQTT")
    await _insert_instance_grant(db, instance_id)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp_id, instance_id=instance_id, adapter_type="MQTT")
    monkeypatch.setitem(adapter_registry._adapters, "MQTT", MqttAdapter)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub(dp_id))
    monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())

    updated = await bindings_api.update_binding(
        dp_id=dp_id,
        binding_id=binding_id,
        body=AdapterBindingUpdate(enabled=False),
        _user=_principal("alice"),
        db=db,
    )
    await bindings_api.delete_binding(
        dp_id=dp_id,
        binding_id=binding_id,
        _user=_principal("alice"),
        db=db,
    )
    row = await db.fetchone("SELECT id FROM adapter_bindings WHERE id=?", (str(binding_id),))

    assert updated.enabled is False
    assert row is None


@pytest.mark.asyncio
async def test_binding_mutation_central_plant_requires_central_control(db: Database):
    from obs.api.v1.bindings import _ensure_binding_mutation_scope

    dp_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room", control_class="central_plant")
    await _insert_grant(db, node_id="allowed-room", role="operator")

    with pytest.raises(HTTPException) as exc_info:
        await _ensure_binding_mutation_scope(db, _principal("alice"), dp_id)

    assert exc_info.value.status_code == 403

    await db.execute_and_commit(
        "UPDATE authz_node_roles SET central_control=1 WHERE principal_id='alice' AND node_id='allowed-room'",
    )
    await _ensure_binding_mutation_scope(db, _principal("alice"), dp_id)


@pytest.mark.asyncio
async def test_binding_mutation_direct_datapoint_grant_central_plant_requires_central_control(db: Database):
    """Direct datapoint grant without central_control must not bypass the central-plant gate."""
    from obs.api.v1.bindings import _ensure_binding_mutation_scope

    dp_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, dp_id, "allowed-room", control_class="central_plant")
    # Direct grant on the datapoint itself (not via hierarchy), central_control defaults to 0
    await _insert_grant(db, node_type="datapoint", node_id=str(dp_id), role="operator")

    with pytest.raises(HTTPException) as exc_info:
        await _ensure_binding_mutation_scope(db, _principal("alice"), dp_id)

    assert exc_info.value.status_code == 403

    await db.execute_and_commit(
        "UPDATE authz_node_roles SET central_control=1 WHERE principal_id='alice' AND node_type='datapoint' AND node_id=?",
        (str(dp_id),),
    )
    await _ensure_binding_mutation_scope(db, _principal("alice"), dp_id)
