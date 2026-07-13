from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import datapoints as dp_api
from obs.core.registry import ValueState
from obs.db.database import Database


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, dps=None):
        self._dps = {dp.id: dp for dp in dps or []}
        self._values: dict[uuid.UUID, ValueState] = {}

    def all(self):
        return list(self._dps.values())

    def get(self, dp_id):
        return self._dps.get(dp_id)

    def get_value(self, dp_id):
        return self._values.get(dp_id)

    async def update(self, dp_id, body):
        datapoint = self._dps[dp_id]
        for field in body.model_fields_set:
            value = getattr(body, field)
            if value is not None and field != "value":
                setattr(datapoint, field, value)
        return datapoint


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _dp(dp_id: str, name: str, *, control_class: str = "room_local"):
    parsed_id = uuid.UUID(dp_id)
    return SimpleNamespace(
        id=parsed_id,
        name=name,
        data_type="FLOAT",
        unit="degC",
        tags=[],
        mqtt_topic=f"dp/{parsed_id}/value",
        mqtt_alias=None,
        persist_value=True,
        record_history=True,
        control_class=control_class,
        created_at=datetime(2026, 6, 10, tzinfo=UTC),
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )


def _tagged_dp(dp_id: str, name: str, tags: list[str]):
    datapoint = _dp(dp_id, name)
    datapoint.tags = tags
    return datapoint


async def _insert_tree(db: Database) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (NOW, NOW),
    )


async def _insert_node(db: Database, node_id: str, *, parent_id: str | None = None) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', ?, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, parent_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, control_class, created_at, updated_at)
        VALUES (?, ?, ?, ?, '[]', ?, NULL, 1, 1, ?, ?, ?)
        """,
        (str(dp.id), dp.name, dp.data_type, dp.unit, dp.mqtt_topic, dp.control_class, NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: uuid.UUID, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), node_id, str(dp_id), NOW),
    )


async def _insert_grant(
    db: Database,
    node_id: str,
    *,
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    role: str = "guest",
    effect: str = "allow",
    central_control: bool = False,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect, central_control)
        VALUES ('user', ?, ?, ?, ?, ?, ?)
        """,
        (principal_id, node_type, node_id, role, effect, int(central_control)),
    )


async def _prepare_linked_datapoints(db: Database, dps, *, authorized_node: str = "allowed") -> None:
    await _insert_tree(db)
    await _insert_node(db, authorized_node)
    await _insert_node(db, "blocked")
    for dp in dps:
        await _insert_datapoint(db, dp)
    await _insert_grant(db, authorized_node)


@pytest.mark.asyncio
async def test_list_datapoints_filters_before_pagination_and_preserves_sort(monkeypatch, db: Database):
    hidden = _dp("00000000-0000-0000-0000-000000000001", "Alpha hidden")
    allowed_b = _dp("00000000-0000-0000-0000-000000000002", "Bravo allowed")
    allowed_c = _dp("00000000-0000-0000-0000-000000000003", "Charlie allowed")
    await _prepare_linked_datapoints(db, [hidden, allowed_b, allowed_c])
    await _link_datapoint(db, hidden.id, "blocked")
    await _link_datapoint(db, allowed_b.id, "allowed")
    await _link_datapoint(db, allowed_c.id, "allowed")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([hidden, allowed_b, allowed_c]))

    result = await dp_api.list_datapoints(
        page=0,
        size=1,
        sort="name",
        order="asc",
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.total == 2
    assert result.pages == 2
    assert [item.name for item in result.items] == ["Bravo allowed"]


@pytest.mark.asyncio
async def test_list_tags_filters_to_readable_datapoints(monkeypatch, db: Database):
    hidden = _tagged_dp("00000000-0000-0000-0000-000000000004", "Hidden", ["secret-room"])
    allowed = _tagged_dp("00000000-0000-0000-0000-000000000005", "Allowed", ["public-room"])
    await _prepare_linked_datapoints(db, [hidden, allowed])
    await _link_datapoint(db, hidden.id, "blocked")
    await _link_datapoint(db, allowed.id, "allowed")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([hidden, allowed]))

    result = await dp_api.list_tags(
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result == ["public-room"]


@pytest.mark.asyncio
async def test_get_datapoint_returns_404_for_existing_out_of_scope_datapoint(monkeypatch, db: Database):
    hidden = _dp("00000000-0000-0000-0000-000000000011", "Hidden")
    await _prepare_linked_datapoints(db, [hidden])
    await _link_datapoint(db, hidden.id, "blocked")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([hidden]))

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_datapoint(
            dp_id=hidden.id,
            _user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_value_authenticated_returns_404_for_existing_out_of_scope_datapoint(monkeypatch, db: Database):
    hidden = _dp("00000000-0000-0000-0000-000000000021", "Hidden value")
    await _prepare_linked_datapoints(db, [hidden])
    await _link_datapoint(db, hidden.id, "blocked")
    registry = _RegistryStub([hidden])
    state = ValueState()
    state.update(21.5, "good")
    registry._values[hidden.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)

    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(
            dp_id=hidden.id,
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_list_keeps_no_grant_visibility(monkeypatch, db: Database):
    first = _dp("00000000-0000-0000-0000-000000000031", "Alpha")
    second = _dp("00000000-0000-0000-0000-000000000032", "Bravo")
    await _insert_datapoint(db, first)
    await _insert_datapoint(db, second)
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([first, second]))

    result = await dp_api.list_datapoints(
        page=0,
        size=50,
        sort="name",
        order="asc",
        _user=Principal(subject="admin", type="user", is_admin=True),
        db=db,
    )

    assert result.total == 2
    assert [item.name for item in result.items] == ["Alpha", "Bravo"]


@pytest.mark.asyncio
async def test_api_key_metadata_capability_still_requires_target_write_grant_and_excludes_values(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000041", "Metadata")
    await _insert_datapoint(db, datapoint)
    key_id = "00000000-0000-0000-0000-000000000989"
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'hash-989', 'admin', ?)",
        (key_id, NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capabilities (key_id, capability) VALUES (?, 'datapoint.metadata.write')",
        (key_id,),
    )
    principal = Principal(subject=f"api_key:{key_id}", type="api_key", is_admin=False, owner="admin")
    registry = _RegistryStub([datapoint])
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)

    with pytest.raises(HTTPException) as missing_scope:
        await dp_api.update_datapoint(dp_id=datapoint.id, body=dp_api.DataPointUpdate(name="Denied"), request=None, _user=principal, db=db)
    assert missing_scope.value.status_code == 403

    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('api_key', ?, 'datapoint', ?, 'resident', 'allow')
        """,
        (key_id, str(datapoint.id)),
    )
    result = await dp_api.update_datapoint(dp_id=datapoint.id, body=dp_api.DataPointUpdate(name="Allowed"), request=None, _user=principal, db=db)
    assert result.name == "Allowed"

    with pytest.raises(HTTPException) as runtime_value:
        await dp_api.update_datapoint(dp_id=datapoint.id, body=dp_api.DataPointUpdate(value=21.5), request=None, _user=principal, db=db)
    assert runtime_value.value.status_code == 403

    audit = await db.fetchall("SELECT details_json FROM audit_log_entries WHERE action='api_key.capability.use' ORDER BY id")
    assert '"result":"allowed"' in audit[-2]["details_json"]
    assert '"result":"denied"' in audit[-1]["details_json"]


@pytest.mark.asyncio
async def test_user_write_grant_can_update_datapoint_metadata_without_api_key_capability(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000042", "Delegated metadata")
    await _insert_datapoint(db, datapoint)
    await _insert_grant(db, str(datapoint.id), node_type="datapoint", role="resident")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))

    result = await dp_api.update_datapoint(
        dp_id=datapoint.id,
        body=dp_api.DataPointUpdate(name="Updated by user"),
        request=None,
        _user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.name == "Updated by user"


@pytest.mark.asyncio
async def test_write_value_requires_write_grant_for_authenticated_principal(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000061", "Writable")
    await _insert_tree(db)
    await _insert_node(db, "allowed")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "allowed")
    await _insert_grant(db, "allowed", role="resident")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)

    request = MagicMock()
    request.headers = {}
    await dp_api.write_value(
        dp_id=datapoint.id,
        body=dp_api.WriteValueIn(value=22.5),
        request=request,
        user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_central_datapoint_write_requires_central_control_grant(monkeypatch, db: Database):
    datapoint = _dp(
        "00000000-0000-0000-0000-000000000066",
        "Central writable",
        control_class="central_plant",
    )
    await _insert_tree(db)
    await _insert_node(db, "plant")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "plant")
    await _insert_grant(db, "plant", role="resident")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)
    request = MagicMock()
    request.headers = {}
    principal = Principal(subject="alice", type="user", is_admin=False)

    with pytest.raises(HTTPException) as denied:
        await dp_api.write_value(
            dp_id=datapoint.id,
            body=dp_api.WriteValueIn(value=22.5),
            request=request,
            user=principal,
            db=db,
        )
    assert denied.value.status_code == 403
    event_bus.publish.assert_not_awaited()

    await db.execute_and_commit("UPDATE authz_node_roles SET central_control=1 WHERE principal_id='alice' AND node_id='plant'")
    await dp_api.write_value(
        dp_id=datapoint.id,
        body=dp_api.WriteValueIn(value=23.0),
        request=request,
        user=principal,
        db=db,
    )
    await dp_api.write_value(
        dp_id=datapoint.id,
        body=dp_api.WriteValueIn(value=24.0),
        request=request,
        user=Principal(subject="admin", type="user", is_admin=True),
        db=db,
    )
    assert event_bus.publish.await_count == 2


@pytest.mark.asyncio
async def test_write_value_rejects_missing_or_explicitly_denied_write_grant(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000062", "Blocked write")
    await _insert_tree(db)
    await _insert_node(db, "blocked")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "blocked")
    await _insert_grant(db, "blocked", role="resident", effect="deny")
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)

    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=datapoint.id,
            body=dp_api.WriteValueIn(value=22.5),
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_value_explicit_deny_overrides_public_page_scope(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000064", "Denied public write")
    await _insert_tree(db)
    await _insert_node(db, "denied")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "denied")
    await _insert_grant(db, "denied", role="resident", effect="deny")
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-deny-write', NULL, 'Public', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (
            '{"grid_cols":12,"grid_row_height":80,"grid_cell_width":120,"background":null,'
            f'"widgets":[{{"id":"w","name":"W","type":"value","datapoint_id":"{datapoint.id}",'
            '"status_datapoint_id":null,"x":0,"y":0,"w":1,"h":1,"config":{}}]}',
            NOW,
            NOW,
        ),
    )
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)
    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-deny-write"}.get(key, default)

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=datapoint.id,
            body=dp_api.WriteValueIn(value=22.5),
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_value_user_page_assignment_does_not_replace_write_grant(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000065", "User page write")
    await _insert_datapoint(db, datapoint)
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, created_at, is_admin)
        VALUES ('user-id', 'alice', 'hash', ?, 0)
        """,
        (NOW,),
    )
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-user-write', NULL, 'User', 'PAGE', 0, NULL, 'user', NULL, ?, ?, ?)
        """,
        (
            '{"grid_cols":12,"grid_row_height":80,"grid_cell_width":120,"background":null,'
            f'"widgets":[{{"id":"w","name":"W","type":"value","datapoint_id":"{datapoint.id}",'
            '"status_datapoint_id":null,"x":0,"y":0,"w":1,"h":1,"config":{}}]}',
            NOW,
            NOW,
        ),
    )
    await db.execute_and_commit(
        "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('page-user-write', 'user')",
    )
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'visu_page', 'page-user-write', 'guest', 'allow')""",
    )
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)
    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-user-write"}.get(key, default)

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=datapoint.id,
            body=dp_api.WriteValueIn(value=22.5),
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 403
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [None, Principal(subject="alice", type="user", is_admin=False)],
    ids=["anonymous", "authenticated-without-grant"],
)
async def test_write_value_preserves_public_page_scope(monkeypatch, db: Database, user: Principal | None):
    datapoint = _dp("00000000-0000-0000-0000-000000000063", "Public page write")
    await _insert_datapoint(db, datapoint)
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-write', NULL, 'Public', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (
            '{"grid_cols":12,"grid_row_height":80,"grid_cell_width":120,"background":null,'
            f'"widgets":[{{"id":"w","name":"W","type":"value","datapoint_id":"{datapoint.id}",'
            '"status_datapoint_id":null,"x":0,"y":0,"w":1,"h":1,"config":{}}]}',
            NOW,
            NOW,
        ),
    )
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-write"}.get(key, default)
    await dp_api.write_value(
        dp_id=datapoint.id,
        body=dp_api.WriteValueIn(value=22.5),
        request=request,
        user=user,
        db=db,
    )

    event_bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_value_public_page_path_remains_compatible_without_grant(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000041", "Public page value")
    await _insert_datapoint(db, datapoint)
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public', NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(19.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public"}.get(key, default)
    result = await dp_api.get_value(dp_id=datapoint.id, request=request, user=None, db=db)

    assert result.value == 19.0
    assert result.quality == "good"


@pytest.mark.asyncio
async def test_get_value_authenticated_public_page_path_remains_compatible_without_grant(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000042", "Authenticated public page value")
    await _insert_datapoint(db, datapoint)
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-auth', NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(20.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-auth"}.get(key, default)
    result = await dp_api.get_value(
        dp_id=datapoint.id,
        request=request,
        user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.value == 20.0
    assert result.quality == "good"


@pytest.mark.asyncio
async def test_get_value_authenticated_page_fallback_honors_explicit_deny(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000044", "Denied page value")
    await _insert_tree(db)
    await _insert_node(db, "denied")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "denied")
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', 'hierarchy', 'denied', 'guest', 'deny')
        """
    )
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-deny', NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(20.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-deny"}.get(key, default)
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(
            dp_id=datapoint.id,
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_value_page_fallback_ignores_descendant_deny_for_ancestor_datapoint(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000046", "Ancestor page value")
    await _insert_tree(db)
    await _insert_node(db, "floor")
    await _insert_node(db, "room", parent_id="floor")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "floor")
    await _insert_grant(db, "room", effect="deny")
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-child-deny', NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(22.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-child-deny"}.get(key, default)
    result = await dp_api.get_value(
        dp_id=datapoint.id,
        request=request,
        user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.value == 22.0
    assert result.quality == "good"


@pytest.mark.asyncio
async def test_get_value_page_fallback_honors_direct_datapoint_deny(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000045", "Direct denied page value")
    await _insert_tree(db)
    await _insert_node(db, "linked")
    await _insert_datapoint(db, datapoint)
    await _link_datapoint(db, datapoint.id, "linked")
    await _insert_grant(db, str(datapoint.id), node_type="datapoint", effect="deny")
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-public-direct-deny', NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(20.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-direct-deny"}.get(key, default)
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(
            dp_id=datapoint.id,
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_value_authenticated_protected_source_page_remains_compatible_without_session(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000043", "Authenticated protected source page value")
    await _insert_datapoint(db, datapoint)
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-protected-auth', NULL, 'Page', 'PAGE', 0, NULL, 'protected', '1234', ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(21.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("protected", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-protected-auth"}.get(key, default)
    result = await dp_api.get_value(
        dp_id=datapoint.id,
        request=request,
        user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.value == 21.0
    assert result.quality == "good"


@pytest.mark.asyncio
async def test_get_value_assigned_user_visu_page_requires_datapoint_read_grant(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-000000000051", "User page value")
    await _insert_datapoint(db, datapoint)
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, created_at, is_admin)
        VALUES ('user-id', 'alice', 'hash', ?, 0)
        """,
        (NOW,),
    )
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Widget",
          "type": "value",
          "datapoint_id": "{datapoint.id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 2,
          "h": 1,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-user', NULL, 'User Page', 'PAGE', 0, NULL, 'user', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('page-user', 'user')",
    )
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'visu_page', 'page-user', 'guest', 'allow')""",
    )
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(23.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)

    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_value(
            dp_id=datapoint.id,
            request=request,
            user=Principal(subject="alice", type="user", is_admin=False),
            db=db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, Principal(subject="alice", type="user", is_admin=False)], ids=["anonymous", "no-grant"])
async def test_central_plant_write_blocked_via_page_scope(monkeypatch, db: Database, user):
    datapoint = _dp("00000000-0000-0000-0000-000000000067", "Central plant page", control_class="central_plant")
    await _insert_datapoint(db, datapoint)
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-central-public', NULL, 'Central', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (
            '{"grid_cols":12,"grid_row_height":80,"grid_cell_width":120,"background":null,'
            f'"widgets":[{{"id":"w","name":"W","type":"value","datapoint_id":"{datapoint.id}",'
            '"status_datapoint_id":null,"x":0,"y":0,"w":1,"h":1,"config":{}}]}',
            NOW,
            NOW,
        ),
    )
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub([datapoint]))
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(dp_api, "get_event_bus", lambda: event_bus)

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-central-public"}.get(key, default)

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(
            dp_id=datapoint.id,
            body=dp_api.WriteValueIn(value=1.0),
            request=request,
            user=user,
            db=db,
        )

    assert exc_info.value.status_code == 403
    event_bus.publish.assert_not_awaited()
