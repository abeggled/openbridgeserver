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


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _dp(dp_id: str, name: str):
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


async def _insert_node(db: Database, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', NULL, ?, '', 0, NULL, ?, ?)
        """,
        (node_id, node_id, NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, ?, ?, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (str(dp.id), dp.name, dp.data_type, dp.unit, dp.mqtt_topic, NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: uuid.UUID, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), node_id, str(dp_id), NOW),
    )


async def _insert_grant(db: Database, node_id: str, *, principal_id: str = "alice") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, 'guest', 'allow')
        """,
        (principal_id, node_id),
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
async def test_get_value_assigned_user_visu_page_remains_compatible_without_grant(monkeypatch, db: Database):
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
    await db.execute_and_commit("INSERT INTO visu_node_users (node_id, username) VALUES ('page-user', 'alice')")
    registry = _RegistryStub([datapoint])
    state = ValueState()
    state.update(23.0, "good")
    registry._values[datapoint.id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)

    request = MagicMock()
    request.headers = {}
    result = await dp_api.get_value(
        dp_id=datapoint.id,
        request=request,
        user=Principal(subject="alice", type="user", is_admin=False),
        db=db,
    )

    assert result.value == 23.0
    assert result.quality == "good"
