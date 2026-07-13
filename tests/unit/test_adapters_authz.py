from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import adapters as adapters_api
from obs.db.database import Database
from obs.models.datapoint import DataPoint


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, datapoints: list[DataPoint]) -> None:
        self._datapoints = datapoints

    def all(self) -> list[DataPoint]:
        return list(self._datapoints)


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


def _dp(dp_id: uuid.UUID, name: str, data_type: str = "BOOLEAN") -> DataPoint:
    now = datetime.now(UTC)
    return DataPoint(id=dp_id, name=name, data_type=data_type, created_at=now, updated_at=now)


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
            ("secret-room", "secret-room", NOW, NOW),
        ],
    )
    await db.commit()


async def _insert_datapoint(db: Database, dp: DataPoint, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, ?, NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (str(dp.id), dp.name, dp.data_type, f"obs/test/{dp.id}", NOW, NOW),
    )
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{dp.id}", node_id, str(dp.id), NOW),
    )


async def _insert_grant(db: Database, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', 'hierarchy', ?, 'guest', 'allow')
        """,
        (node_id,),
    )


async def _insert_instance(
    db: Database,
    instance_id: uuid.UUID,
    adapter_type: str = "ANWESENHEITSSIMULATION",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'Presence', '{}', 0, ?, ?)
        """,
        (str(instance_id), adapter_type, NOW, NOW),
    )


async def _insert_instance_grant(db: Database, instance_id: uuid.UUID, role: str = "guest") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', 'adapter_instance', ?, ?, 'allow')
        """,
        (str(instance_id), role),
    )


async def _insert_binding(db: Database, *, binding_id: uuid.UUID, dp_id: uuid.UUID, instance_id: uuid.UUID) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'ANWESENHEITSSIMULATION', ?, 'SOURCE', '{}', 1, ?, ?)
        """,
        (str(binding_id), str(dp_id), str(instance_id), NOW, NOW),
    )


@pytest.mark.asyncio
async def test_list_instance_bindings_filters_unreadable_datapoints(db: Database):
    instance_id = uuid.uuid4()
    allowed = _dp(uuid.uuid4(), "Allowed")
    blocked = _dp(uuid.uuid4(), "Blocked")
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, allowed, "allowed-room")
    await _insert_datapoint(db, blocked, "secret-room")
    await _insert_grant(db, "allowed-room")
    await _insert_instance(db, instance_id)
    await _insert_instance_grant(db, instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=allowed.id, instance_id=instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=blocked.id, instance_id=instance_id)

    bindings = await adapters_api.list_instance_bindings(instance_id, _user=_principal(), db=db)

    assert [binding.datapoint_id for binding in bindings] == [allowed.id]


@pytest.mark.asyncio
async def test_list_instance_bindings_admin_sees_ungranted_datapoints(db: Database):
    instance_id = uuid.uuid4()
    allowed = _dp(uuid.uuid4(), "Allowed")
    blocked = _dp(uuid.uuid4(), "Blocked")
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, allowed, "allowed-room")
    await _insert_datapoint(db, blocked, "secret-room")
    await _insert_instance(db, instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=allowed.id, instance_id=instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=blocked.id, instance_id=instance_id)

    bindings = await adapters_api.list_instance_bindings(instance_id, _user=_principal(is_admin=True), db=db)

    assert [binding.datapoint_id for binding in bindings] == [allowed.id, blocked.id]


@pytest.mark.asyncio
async def test_anwesenheit_list_datapoints_filters_unreadable_candidates(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    allowed = _dp(uuid.uuid4(), "Allowed")
    blocked = _dp(uuid.uuid4(), "Blocked")
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, allowed, "allowed-room")
    await _insert_datapoint(db, blocked, "secret-room")
    await _insert_grant(db, "allowed-room")
    await _insert_instance(db, instance_id)
    await _insert_instance_grant(db, instance_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=allowed.id, instance_id=instance_id)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, blocked]))

    datapoints = await adapters_api.anwesenheit_list_datapoints(instance_id, _user=_principal(), db=db)

    assert [datapoint.id for datapoint in datapoints] == [str(allowed.id)]
    assert datapoints[0].has_binding is True


@pytest.mark.asyncio
async def test_anwesenheit_health_hides_instance_without_read_grant(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.anwesenheit_health(instance_id, _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_anwesenheit_list_datapoints_hides_instance_without_read_grant(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([]))

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.anwesenheit_list_datapoints(instance_id, _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_instance_read_is_filtered_by_central_instance_grant(db: Database):
    allowed_id = uuid.uuid4()
    blocked_id = uuid.uuid4()
    await _insert_instance(db, allowed_id)
    await _insert_instance(db, blocked_id)
    await _insert_instance_grant(db, allowed_id)

    visible = await adapters_api.list_instances(_user=_principal(), db=db)

    assert [instance.id for instance in visible] == [allowed_id]
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.get_instance(blocked_id, _user=_principal(), db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_instance_mutation_requires_operator_grant_and_code_declaration(monkeypatch, db: Database):
    from obs.adapters import registry as adapter_registry
    from obs.adapters.mqtt.adapter import MqttAdapter

    mqtt_id = uuid.uuid4()
    knx_id = uuid.uuid4()
    unknown_id = uuid.uuid4()
    await _insert_instance(db, mqtt_id, "MQTT")
    await _insert_instance(db, knx_id, "KNX")
    await _insert_instance(db, unknown_id, "UNKNOWN")
    await _insert_instance_grant(db, mqtt_id, "operator")
    await _insert_instance_grant(db, knx_id, "operator")
    await _insert_instance_grant(db, unknown_id, "operator")
    monkeypatch.setitem(adapter_registry._adapters, "MQTT", MqttAdapter)
    monkeypatch.setattr(adapter_registry, "stop_instance", AsyncMock())

    updated = await adapters_api.update_instance(
        mqtt_id,
        adapters_api.AdapterInstanceUpdate(name="Delegated MQTT", enabled=False),
        _user=_principal(),
        db=db,
    )
    assert updated.name == "Delegated MQTT"

    for denied_id in (knx_id, unknown_id):
        with pytest.raises(HTTPException) as exc_info:
            await adapters_api.update_instance(
                denied_id,
                adapters_api.AdapterInstanceUpdate(name="Denied"),
                _user=_principal(),
                db=db,
            )
        assert exc_info.value.status_code == 403
