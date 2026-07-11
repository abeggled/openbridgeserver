from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from obs.api.auth import Principal
from obs.api.v1 import ringbuffer as rb_api
from obs.db.database import Database


NOW = "2026-06-10T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, dps):
        self._dps = list(dps)

    def all(self):
        return list(self._dps)


class _RingbufferStub:
    def __init__(self, rows):
        self.rows = list(rows)
        self.query_kwargs = None
        self.query_v2_kwargs = None

    async def query(self, **kwargs):
        self.query_kwargs = kwargs
        q = (kwargs.get("q") or "").lower()
        dp_ids_by_name = set(kwargs.get("dp_ids") or [])
        if not q and not dp_ids_by_name:
            return list(self.rows)
        return [row for row in self.rows if q in row.datapoint_id.lower() or q in row.source_adapter.lower() or row.datapoint_id in dp_ids_by_name]

    async def query_v2(self, **kwargs):
        self.query_v2_kwargs = kwargs
        rows = self._filter(kwargs.get("datapoint_ids"))
        q = (kwargs.get("q") or "").lower()
        dp_ids_by_name = set(kwargs.get("dp_ids_by_name") or [])
        if not q and not dp_ids_by_name:
            return rows
        return [row for row in rows if q in row.datapoint_id.lower() or q in row.source_adapter.lower() or row.datapoint_id in dp_ids_by_name]

    def _filter(self, allowed_ids):
        if allowed_ids is None:
            return list(self.rows)
        allowed = set(allowed_ids)
        return [row for row in self.rows if row.datapoint_id in allowed]


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


def _row(row_id: int, datapoint_id: str, *, source_adapter: str = "api"):
    return SimpleNamespace(
        id=row_id,
        ts=f"2026-06-10T00:00:0{row_id}+00:00",
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=1,
        new_value=2,
        source_adapter=source_adapter,
        quality="good",
        metadata_version=1,
        metadata={},
    )


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


async def _grant_user(db: Database, node_id: str, username: str = "alice") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, 'guest', 'allow')
        """,
        (username, node_id),
    )


async def _prepare_authz(db: Database, allowed, hidden) -> None:
    await _insert_tree(db)
    await _insert_node(db, "allowed")
    await _insert_node(db, "hidden")
    await _insert_datapoint(db, allowed)
    await _insert_datapoint(db, hidden)
    await _link_datapoint(db, allowed.id, "allowed")
    await _link_datapoint(db, hidden.id, "hidden")
    await _grant_user(db, "allowed")


def _principal() -> Principal:
    return Principal(subject="alice", type="user", is_admin=False)


@pytest.mark.asyncio
async def test_query_v2_scopes_history_to_authorized_datapoints(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000619", "Allowed temperature")
    hidden = _dp("00000000-0000-0000-0000-000000000620", "Hidden temperature")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id)), _row(2, str(hidden.id))])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    rows = await rb_api.query_ringbuffer_v2(
        rb_api.RingBufferQueryV2(),
        _user=_principal(),
        db=db,
    )

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]


@pytest.mark.asyncio
async def test_query_v2_intersects_explicit_datapoint_filter_with_authz(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000621", "Allowed humidity")
    hidden = _dp("00000000-0000-0000-0000-000000000622", "Hidden humidity")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id)), _row(2, str(hidden.id))])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    body = rb_api.RingBufferQueryV2(
        filters=rb_api.RingBufferFiltersV2(datapoints=rb_api.RingBufferDatapointFilterV2(ids=[str(hidden.id), str(allowed.id)]))
    )
    rows = await rb_api.query_ringbuffer_v2(body, _user=_principal(), db=db)

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]


@pytest.mark.asyncio
async def test_legacy_query_scopes_name_matches_to_authorized_datapoints(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000623", "Room temperature")
    hidden = _dp("00000000-0000-0000-0000-000000000624", "Room temperature hidden")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id)), _row(2, str(hidden.id))])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    rows = await rb_api.query_ringbuffer(
        q="temperature",
        adapter="",
        from_ts="",
        limit=100,
        _user=_principal(),
        db=db,
    )

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]


@pytest.mark.asyncio
async def test_legacy_query_scopes_source_adapter_matches_to_authorized_datapoints(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000627", "Allowed room")
    hidden = _dp("00000000-0000-0000-0000-000000000628", "Hidden room")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id)), _row(2, str(hidden.id))])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    rows = await rb_api.query_ringbuffer(
        q="api",
        adapter="",
        from_ts="",
        limit=100,
        _user=_principal(),
        db=db,
    )

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]


@pytest.mark.asyncio
async def test_legacy_query_keeps_allowed_source_adapter_matches_when_name_matches_are_hidden(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000629", "Allowed room")
    hidden = _dp("00000000-0000-0000-0000-000000000630", "Hidden KNX datapoint")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id), source_adapter="knx"), _row(2, str(hidden.id), source_adapter="api")])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    rows = await rb_api.query_ringbuffer(
        q="knx",
        adapter="",
        from_ts="",
        limit=100,
        _user=_principal(),
        db=db,
    )

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]
    assert rb.query_v2_kwargs["dp_ids_by_name"] is None


@pytest.mark.asyncio
async def test_filtersets_multi_query_empty_selection_is_still_scoped(monkeypatch, db: Database):
    allowed = _dp("00000000-0000-0000-0000-000000000625", "Allowed pressure")
    hidden = _dp("00000000-0000-0000-0000-000000000626", "Hidden pressure")
    await _prepare_authz(db, allowed, hidden)
    rb = _RingbufferStub([_row(1, str(allowed.id)), _row(2, str(hidden.id))])

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, hidden]))
    monkeypatch.setattr(rb_api, "get_ringbuffer", lambda: rb)

    rows = await rb_api.query_ringbuffer_filtersets_multi(
        rb_api.RingBufferMultiQueryRequest(set_ids=[]),
        current_user=_principal(),
        db=db,
    )

    assert [row.datapoint_id for row in rows] == [str(allowed.id)]
    assert rb.query_v2_kwargs["datapoint_ids"] == [str(allowed.id)]
