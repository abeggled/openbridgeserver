from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from obs.adapters.base import AdapterDelegationCapability
from obs.api.auth import Principal
from obs.api.v1 import adapters as adapters_api
from obs.api.v1 import knxproj as knxproj_api
from obs.db.database import Database
from obs.models.datapoint import DataPoint


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, datapoints: list[DataPoint]) -> None:
        self._datapoints = datapoints

    def all(self) -> list[DataPoint]:
        return list(self._datapoints)

    def get(self, dp_id: uuid.UUID) -> DataPoint | None:
        return next((dp for dp in self._datapoints if dp.id == dp_id), None)


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


async def _insert_instance_grant(
    db: Database,
    instance_id: uuid.UUID,
    role: str = "guest",
    *,
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', 'adapter_instance', ?, ?, ?)
        """,
        (str(instance_id), role, effect),
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
async def test_holiday_discovery_hides_instance_without_read_grant(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "ZEITSCHALTUHR")

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.list_instance_holidays(instance_id, year=2026, _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["browse", "sample"])
async def test_mqtt_discovery_hides_instance_without_read_grant(operation: str, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "MQTT")

    with pytest.raises(HTTPException) as exc_info:
        if operation == "browse":
            await adapters_api.mqtt_browse_topics(instance_id, timeout=1, _user=_principal(), db=db)
        else:
            await adapters_api.mqtt_sample_payload(instance_id, topic="secret/topic", timeout=1, _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_iobroker_discovery_hides_instance_without_read_grant(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "IOBROKER")

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_browse_states(instance_id, q="", limit=10, _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_snmp_discovery_hides_instance_without_read_grant(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "SNMP")

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.snmp_walk(instance_id, host="192.0.2.1", _user=_principal(), db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_binding_migration_rejects_out_of_scope_datapoint(monkeypatch, db: Database):
    from obs.adapters import registry as adapter_registry
    from obs.adapters.mqtt.adapter import MqttAdapter

    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    blocked_dp = _dp(uuid.uuid4(), "Blocked")
    binding_id = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, blocked_dp, "secret-room")
    await _insert_instance(db, source_id, "MQTT")
    await _insert_instance(db, target_id, "MQTT")
    await _insert_instance_grant(db, source_id, "operator")
    await _insert_instance_grant(db, target_id, "operator")
    await _insert_binding(
        db,
        binding_id=binding_id,
        dp_id=blocked_dp.id,
        instance_id=source_id,
        adapter_type="MQTT",
    )
    monkeypatch.setitem(adapter_registry._adapters, "MQTT", MqttAdapter)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.migrate_instance_bindings(
            source_id,
            adapters_api.BindingMigrationRequest(target_instance_id=target_id),
            _user=_principal(),
            db=db,
        )

    binding_row = await db.fetchone("SELECT adapter_instance_id FROM adapter_bindings WHERE id=?", (str(binding_id),))
    assert exc_info.value.status_code == 403
    assert binding_row["adapter_instance_id"] == str(source_id)


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


def test_adapter_operation_delegation_requires_user_and_every_declared_capability(monkeypatch):
    supported = {
        AdapterDelegationCapability.CREATE_DATAPOINT,
        AdapterDelegationCapability.LINK_BINDING,
    }
    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "supports_delegation",
        lambda adapter_type, capability: adapter_type == "DECLARED" and capability in supported,
    )

    adapters_api._ensure_adapter_delegates_operation(
        _principal(),
        "DECLARED",
        AdapterDelegationCapability.CREATE_DATAPOINT,
        AdapterDelegationCapability.LINK_BINDING,
    )
    adapters_api._ensure_adapter_delegates_operation(
        _principal("admin", is_admin=True),
        "UNKNOWN",
    )

    denied = [
        (_principal(), "DECLARED", (AdapterDelegationCapability.CREATE_DEVICE,)),
        (_principal(), "UNKNOWN", (AdapterDelegationCapability.CREATE_DATAPOINT,)),
        (_principal(), "DECLARED", ()),
        (Principal(subject="key", type="api_key", is_admin=False), "DECLARED", tuple(supported)),
    ]
    for principal, adapter_type, capabilities in denied:
        with pytest.raises(HTTPException) as exc_info:
            adapters_api._ensure_adapter_delegates_operation(principal, adapter_type, *capabilities)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_iobroker_import_requires_instance_write_and_declared_create_capabilities(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "IOBROKER")
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda *_args: True)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_states(
            instance_id,
            adapters_api.IoBrokerImportRequest(),
            _user=_principal(),
            db=db,
        )
    assert exc_info.value.status_code == 403

    await _insert_instance_grant(db, instance_id, "operator")
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda *_args: False)
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_states(
            instance_id,
            adapters_api.IoBrokerImportRequest(),
            _user=_principal(),
            db=db,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_iobroker_preview_matches_import_scope_and_capability_contract(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "IOBROKER")
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda *_args: True)

    for concealed_id in (instance_id, uuid.uuid4()):
        with pytest.raises(HTTPException) as exc_info:
            await adapters_api.iobroker_import_preview(
                concealed_id,
                adapters_api.IoBrokerImportRequest(),
                _user=_principal(),
                db=db,
            )
        assert exc_info.value.status_code == 403

    await _insert_instance_grant(db, instance_id, "operator")
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda *_args: False)
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_preview(
            instance_id,
            adapters_api.IoBrokerImportRequest(),
            _user=_principal(),
            db=db,
        )
    assert exc_info.value.status_code == 403

    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
           (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('api_key', 'preview-key', 'adapter_instance', ?, 'operator', 'allow')""",
        (str(instance_id),),
    )
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda *_args: True)
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_preview(
            instance_id,
            adapters_api.IoBrokerImportRequest(),
            _user=Principal(subject="api_key:preview-key", type="api_key", is_admin=False),
            db=db,
        )
    assert exc_info.value.status_code == 403

    instance = SimpleNamespace(browse_states=AsyncMock(return_value=[]))
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _instance_id: instance)
    preview = await adapters_api.iobroker_import_preview(
        instance_id,
        adapters_api.IoBrokerImportRequest(),
        _user=_principal(),
        db=db,
    )
    assert preview.preview == []
    instance.browse_states.assert_awaited_once()

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_preview(
            uuid.uuid4(),
            adapters_api.IoBrokerImportRequest(),
            _user=_principal("admin", is_admin=True),
            db=db,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_iobroker_import_declared_path_creates_datapoint_and_binding(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    datapoint_id = uuid.uuid4()
    admin_datapoint_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "IOBROKER")
    await _insert_instance_grant(db, instance_id, "operator")
    instance = SimpleNamespace(
        browse_states=AsyncMock(
            return_value=[
                {
                    "id": "zigbee.0.lamp.state",
                    "name": "Lamp",
                    "type": "boolean",
                    "role": "switch.light",
                    "read": True,
                    "write": True,
                    "value": False,
                    "unit": None,
                }
            ]
        )
    )
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=datapoint_id)))
    create_binding = AsyncMock()
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _instance_id: instance)
    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "supports_delegation",
        lambda adapter_type, capability: (
            adapter_type == "IOBROKER" and capability in {AdapterDelegationCapability.CREATE_DATAPOINT, AdapterDelegationCapability.LINK_BINDING}
        ),
    )
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)

    result = await adapters_api.iobroker_import_states(
        instance_id,
        adapters_api.IoBrokerImportRequest(),
        _user=_principal(),
        db=db,
    )

    assert result.created_datapoints == 1
    assert result.created_bindings == 1
    registry.create.assert_awaited_once()
    create_binding.assert_awaited_once()
    operation_principal = create_binding.await_args.args[2]
    assert operation_principal.subject == "alice"
    assert operation_principal.is_admin is False
    grant = await db.fetchone(
        """SELECT role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='alice'
             AND node_type='datapoint' AND node_id=?""",
        (str(datapoint_id),),
    )
    assert dict(grant) == {"role": "operator", "effect": "allow"}

    registry.create.reset_mock()
    registry.create.return_value = SimpleNamespace(id=admin_datapoint_id)
    create_binding.reset_mock()
    admin_result = await adapters_api.iobroker_import_states(
        instance_id,
        adapters_api.IoBrokerImportRequest(),
        _user=_principal("admin", is_admin=True),
        db=db,
    )
    admin_grant = await db.fetchone(
        """SELECT role FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='admin'
             AND node_type='datapoint' AND node_id=?""",
        (str(admin_datapoint_id),),
    )

    assert admin_result.created_datapoints == 1
    assert admin_result.created_bindings == 1
    assert create_binding.await_args.args[2] == _principal("admin", is_admin=True)
    assert admin_grant is None


@pytest.mark.asyncio
async def test_adapter_instance_deny_wins_before_iobroker_import(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, "IOBROKER")
    await _insert_instance_grant(db, instance_id, "operator", effect="deny")
    supports_delegation = AsyncMock(return_value=True)
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", supports_delegation)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.iobroker_import_states(
            instance_id,
            adapters_api.IoBrokerImportRequest(),
            _user=_principal(),
            db=db,
        )

    assert exc_info.value.status_code == 403
    supports_delegation.assert_not_called()


@pytest.mark.asyncio
async def test_anwesenheit_sync_declared_link_uses_callers_scoped_principal(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    existing_dp = _dp(uuid.uuid4(), "Existing")
    added_dp = _dp(uuid.uuid4(), "Added")
    existing_binding = uuid.uuid4()
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, existing_dp, "allowed-room")
    await _insert_datapoint(db, added_dp, "allowed-room")
    await _insert_instance(db, instance_id)
    await _insert_instance_grant(db, instance_id, "operator")
    await _insert_binding(
        db,
        binding_id=existing_binding,
        dp_id=existing_dp.id,
        instance_id=instance_id,
    )
    create_binding = AsyncMock()
    delete_binding = AsyncMock()
    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "supports_delegation",
        lambda adapter_type, capability: adapter_type == "ANWESENHEITSSIMULATION" and capability is AdapterDelegationCapability.LINK_BINDING,
    )
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _instance_id: None)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)
    monkeypatch.setattr("obs.api.v1.bindings.delete_binding", delete_binding)

    result = await adapters_api.anwesenheit_sync_bindings(
        instance_id,
        adapters_api.AnwesenheitSyncRequest(datapoint_ids=[str(added_dp.id)]),
        _user=_principal(),
        db=db,
    )

    assert result.created == 1
    assert result.removed == 1
    assert create_binding.await_args.args[2] == _principal()
    assert delete_binding.await_args.args[2] == _principal()


def test_adapter_creation_route_dependencies_match_closed_contract():
    # CREATE_DEVICE is declaration-only until an adapter-owned runtime path
    # exists; Wave 13 must not manufacture a generic route for it.
    adapter_post_routes = [route for route in adapters_api.router.routes if isinstance(route, APIRoute) and "POST" in route.methods]
    assert not any("/devices" in route.path for route in adapter_post_routes)

    instance_create = next(route for route in adapter_post_routes if route.path == "/instances")
    assert any(dependency.call is adapters_api.get_admin_user for dependency in instance_create.dependant.dependencies)

    import_preview = next(route for route in adapter_post_routes if route.path.endswith("/iobroker/import-preview"))
    assert any(dependency.call is adapters_api.get_current_principal for dependency in import_preview.dependant.dependencies)

    type_test = next(route for route in adapter_post_routes if route.path == "/{adapter_type}/test")
    assert any(dependency.call is adapters_api.get_admin_user for dependency in type_test.dependant.dependencies)

    type_config = next(route for route in adapters_api.router.routes if isinstance(route, APIRoute) and route.path == "/{adapter_type}/config")
    assert any(dependency.call is adapters_api.get_admin_user for dependency in type_config.dependant.dependencies)

    knx_import_routes = [route for route in knxproj_api.router.routes if isinstance(route, APIRoute) and route.path in {"/import", "/import-csv"}]
    assert len(knx_import_routes) == 2
    assert all(any(dependency.call is knxproj_api.get_admin_user for dependency in route.dependant.dependencies) for route in knx_import_routes)
