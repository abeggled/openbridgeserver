from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import history as history_api
from obs.db.database import Database


NOW = "2026-06-10T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


class _RegistryStub:
    def __init__(self, dp_id: uuid.UUID):
        self._dp_id = dp_id
        self._dp = SimpleNamespace(id=dp_id)

    def get(self, dp_id: uuid.UUID):
        if dp_id == self._dp_id:
            return self._dp
        return None


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


async def _insert_datapoint(db: Database, dp_id: uuid.UUID) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, created_at, updated_at)
        VALUES (?, 'History DP', 'FLOAT', NULL, '[]', ?, NULL, ?, ?)
        """,
        (str(dp_id), f"obs/test/{dp_id}", NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: uuid.UUID, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{node_id}", node_id, str(dp_id), NOW),
    )


async def _insert_read_grant(db: Database, *, principal_id: str, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, 'guest', 'allow')
        """,
        (principal_id, node_id),
    )


async def _insert_public_visu_page(db: Database, page_id: str, dp_id: uuid.UUID) -> None:
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "widget-1",
          "name": "Chart",
          "type": "chart",
          "datapoint_id": "{dp_id}",
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 4,
          "h": 3,
          "config": {{}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES (?, NULL, 'Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_id, page_config, NOW, NOW),
    )


async def _seed_datapoint_scope(
    db: Database,
    dp_id: uuid.UUID,
    *,
    node_id: str = "room",
    grant_principal: str | None = None,
) -> None:
    await _insert_tree(db)
    await _insert_node(db, node_id)
    await _insert_datapoint(db, dp_id)
    await _link_datapoint(db, dp_id, node_id)
    if grant_principal is not None:
        await _insert_read_grant(db, principal_id=grant_principal, node_id=node_id)


def _request() -> MagicMock:
    request = MagicMock()
    request.headers.get.return_value = None
    return request


def _principal(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


@pytest.mark.asyncio
async def test_invalid_auth_with_public_page_falls_back_to_page_access(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _insert_datapoint(db, dp_id)
    await _insert_public_visu_page(db, "page-1", dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    async def _invalid_auth(*, credentials, api_key, db):
        raise HTTPException(status_code=401, detail="invalid auth")

    monkeypatch.setattr(history_api, "get_current_principal", _invalid_auth)

    principal = await history_api._optional_history_principal(
        credentials=SimpleNamespace(credentials="invalid-token"),
        api_key=None,
        db=db,
    )

    plugin = MagicMock()
    plugin.query = AsyncMock(return_value=[])
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)
    monkeypatch.setattr(history_api, "_resolve_page_access", AsyncMock(return_value="public"))

    request = MagicMock()
    request.headers.get.return_value = "page-1"

    result = await history_api.query_history(
        dp_id=dp_id,
        from_ts=None,
        to_ts=None,
        limit=100,
        request=request,
        principal=principal,
        db=db,
    )

    assert principal is None
    assert result == []
    plugin.query.assert_awaited_once()


@pytest.mark.asyncio
async def test_anonymous_public_page_history_requires_datapoint_on_page(monkeypatch, db: Database):
    page_dp_id = uuid.uuid4()
    hidden_dp_id = uuid.uuid4()
    await _insert_datapoint(db, page_dp_id)
    await _insert_datapoint(db, hidden_dp_id)
    await _insert_public_visu_page(db, "page-public-history", page_dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(hidden_dp_id))
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    plugin = MagicMock()
    plugin.query = AsyncMock()
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-history"}.get(key, default)

    with pytest.raises(HTTPException) as exc_info:
        await history_api.query_history(
            dp_id=hidden_dp_id,
            from_ts=None,
            to_ts=None,
            limit=100,
            request=request,
            principal=None,
            db=db,
        )

    assert exc_info.value.status_code == 404
    plugin.query.assert_not_called()


@pytest.mark.asyncio
async def test_query_history_with_read_grant_reaches_plugin(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id, grant_principal="alice")
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.query = AsyncMock(return_value=[{"ts": "2026-06-10T00:00:00Z", "v": 21.5, "u": "degC", "q": "good"}])
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    result = await history_api.query_history(
        dp_id=dp_id,
        from_ts="2026-06-10T00:00:00Z",
        to_ts="2026-06-10T01:00:00Z",
        limit=100,
        request=_request(),
        principal=_principal("alice"),
        db=db,
    )

    assert result[0].v == 21.5
    plugin.query.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_history_without_read_grant_returns_404_and_skips_plugin(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.query = AsyncMock()
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    with pytest.raises(HTTPException) as exc_info:
        await history_api.query_history(
            dp_id=dp_id,
            from_ts=None,
            to_ts=None,
            limit=100,
            request=_request(),
            principal=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 404
    plugin.query.assert_not_called()


@pytest.mark.asyncio
async def test_query_history_authenticated_public_page_context_remains_compatible_without_grant(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id)
    await _insert_public_visu_page(db, "page-public-history", dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.query = AsyncMock(return_value=[])
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)
    monkeypatch.setattr("obs.api.v1.visu._resolve_access_with_node", AsyncMock(return_value=("public", None)))

    request = MagicMock()
    request.headers.get = lambda key, default=None: {"X-Page-Id": "page-public-history"}.get(key, default)

    result = await history_api.query_history(
        dp_id=dp_id,
        from_ts=None,
        to_ts=None,
        limit=100,
        request=request,
        principal=_principal("alice"),
        db=db,
    )

    assert result == []
    plugin.query.assert_awaited_once()


@pytest.mark.asyncio
async def test_aggregate_history_with_read_grant_reaches_plugin(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id, grant_principal="alice")
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.aggregate = AsyncMock(return_value=[{"bucket": datetime(2026, 6, 10, tzinfo=UTC), "v": 21.5, "n": 1}])
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    result = await history_api.aggregate_history(
        dp_id=dp_id,
        fn="avg",
        interval="1h",
        from_ts="2026-06-10T00:00:00Z",
        to_ts="2026-06-10T01:00:00Z",
        request=_request(),
        principal=_principal("alice"),
        db=db,
    )

    assert result[0].bucket == "2026-06-10T00:00:00Z"
    plugin.aggregate.assert_awaited_once()


@pytest.mark.asyncio
async def test_aggregate_history_without_read_grant_returns_404_and_skips_plugin(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.aggregate = AsyncMock()
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    with pytest.raises(HTTPException) as exc_info:
        await history_api.aggregate_history(
            dp_id=dp_id,
            fn="avg",
            interval="1h",
            from_ts=None,
            to_ts=None,
            request=_request(),
            principal=_principal("alice"),
            db=db,
        )

    assert exc_info.value.status_code == 404
    plugin.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_admin_query_history_remains_allowed_without_grants(monkeypatch, db: Database):
    dp_id = uuid.uuid4()
    await _seed_datapoint_scope(db, dp_id)
    monkeypatch.setattr(history_api, "get_registry", lambda: _RegistryStub(dp_id))

    plugin = MagicMock()
    plugin.query = AsyncMock(return_value=[])
    monkeypatch.setattr(history_api, "get_history_plugin", lambda: plugin)

    result = await history_api.query_history(
        dp_id=dp_id,
        from_ts=None,
        to_ts=None,
        limit=100,
        request=_request(),
        principal=_principal("admin", is_admin=True),
        db=db,
    )

    assert result == []
    plugin.query.assert_awaited_once()
