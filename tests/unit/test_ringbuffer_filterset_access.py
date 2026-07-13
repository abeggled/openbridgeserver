from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import ringbuffer as rb_api
from obs.db.database import Database


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return self._body


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        await database.execute(
            """INSERT INTO users
               (id, username, password_hash, is_admin, mqtt_enabled, created_at)
               VALUES ('alice-id', 'alice', 'hash', 0, 0, 'now'),
                      ('bob-id', 'bob', 'hash', 0, 0, 'now'),
                      ('admin-id', 'admin', 'hash', 1, 0, 'now')"""
        )
        await database.commit()
        yield database
    finally:
        await database.disconnect()


def _principal(username: str, *, admin: bool = False) -> Principal:
    return Principal(subject=username, type="user", is_admin=admin)


def _payload(name: str = "Filter") -> rb_api.RingBufferFiltersetIn:
    return rb_api.RingBufferFiltersetIn(name=name, filter=rb_api.FilterCriteria(adapters=["api"]))


async def _insert_datapoint(db: Database, dp_id: str) -> None:
    await db.execute_and_commit(
        """INSERT INTO datapoints
               (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias,
                persist_value, record_history, created_at, updated_at)
           VALUES (?, ?, 'FLOAT', NULL, '[]', ?, NULL, 1, 1, 'now', 'now')""",
        (dp_id, f"DP {dp_id}", f"dp/{dp_id}/value"),
    )


async def _grant_datapoint(db: Database, username: str, dp_id: str) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, 'datapoint', ?, 'guest', 'allow')""",
        (username, dp_id),
    )


async def _grant(db: Database, username: str, filterset_id: str, role: str) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
           (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, 'ringbuffer_filterset', ?, ?, 'allow')
           ON CONFLICT(principal_type, principal_id, node_type, node_id) DO UPDATE SET role=excluded.role""",
        (username, filterset_id, role),
    )


@pytest.mark.asyncio
async def test_create_atomically_assigns_owner_and_rejects_api_keys(db: Database):
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))

    assert created.can_write is True
    assert created.created_by == "alice"
    grant = await db.fetchone(
        """SELECT role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='alice'
             AND node_type='ringbuffer_filterset' AND node_id=?""",
        (created.id,),
    )
    assert dict(grant) == {"role": "owner", "effect": "allow"}

    with pytest.raises(HTTPException) as exc:
        await rb_api._insert_filterset(
            db,
            payload=_payload("API key filter"),
            principal=Principal(subject="api_key:key-id", type="api_key", is_admin=False, owner="alice"),
        )
    assert exc.value.status_code == 403
    assert int((await db.fetchone("SELECT COUNT(*) AS n FROM ringbuffer_filtersets"))["n"]) == 1


@pytest.mark.asyncio
async def test_list_get_and_write_are_central_policy_driven(db: Database):
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))

    assert await rb_api.list_ringbuffer_filtersets(current_user=_principal("bob"), db=db) == []
    with pytest.raises(HTTPException) as hidden:
        await rb_api.get_ringbuffer_filterset(created.id, current_user=_principal("bob"), db=db)
    assert hidden.value.status_code == 404

    await _grant(db, "bob", created.id, "guest")
    visible = await rb_api.list_ringbuffer_filtersets(current_user=_principal("bob"), db=db)
    assert [item.id for item in visible] == [created.id]
    assert visible[0].can_write is False

    with pytest.raises(HTTPException) as readonly:
        await rb_api.update_ringbuffer_filterset(
            created.id,
            _JsonRequest({"name": "Denied"}),  # type: ignore[arg-type]
            current_user=_principal("bob"),
            db=db,
        )
    assert readonly.value.status_code == 404

    await _grant(db, "bob", created.id, "resident")
    updated = await rb_api.update_ringbuffer_filterset(
        created.id,
        _JsonRequest({"name": "Allowed"}),  # type: ignore[arg-type]
        current_user=_principal("bob"),
        db=db,
    )
    assert updated.name == "Allowed"
    assert updated.can_write is True


@pytest.mark.asyncio
async def test_topbar_and_order_ignore_out_of_scope_filtersets(db: Database):
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))

    result = await rb_api.patch_ringbuffer_filtersets_order(
        rb_api.RingBufferFiltersetOrderPatch(items=[{"id": created.id, "topbar_order": 7}]),
        current_user=_principal("bob"),
        db=db,
    )
    assert result == []
    assert (
        await db.fetchone(
            "SELECT 1 FROM ringbuffer_filterset_user_state WHERE username='bob' AND filterset_id=?",
            (created.id,),
        )
        is None
    )

    with pytest.raises(HTTPException) as hidden:
        await rb_api.patch_ringbuffer_filterset_topbar(
            created.id,
            rb_api.RingBufferFiltersetTopbarPatch(topbar_active=True),
            current_user=_principal("bob"),
            db=db,
        )
    assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_filterset_grants_atomically(db: Database):
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))
    await _grant(db, "bob", created.id, "guest")

    await rb_api.delete_ringbuffer_filterset(created.id, current_user=_principal("alice"), db=db)

    assert await db.fetchone("SELECT 1 FROM ringbuffer_filtersets WHERE id=?", (created.id,)) is None
    assert (
        await db.fetchone(
            "SELECT 1 FROM authz_node_roles WHERE node_type='ringbuffer_filterset' AND node_id=?",
            (created.id,),
        )
        is None
    )


@pytest.mark.asyncio
async def test_filterset_payload_remains_valid_json_after_central_access_changes(db: Database):
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))
    row = await db.fetchone("SELECT filter_json FROM ringbuffer_filtersets WHERE id=?", (created.id,))
    assert json.loads(row["filter_json"])["adapters"] == ["api"]


@pytest.mark.asyncio
async def test_create_requires_read_scope_for_every_explicit_datapoint_before_persistence(db: Database):
    allowed_id = str(uuid.uuid4())
    blocked_id = str(uuid.uuid4())
    await _insert_datapoint(db, allowed_id)
    await _insert_datapoint(db, blocked_id)
    await _grant_datapoint(db, "alice", allowed_id)
    payload = rb_api.RingBufferFiltersetIn(
        name="Scoped",
        filter=rb_api.FilterCriteria(datapoints=[allowed_id, blocked_id]),
    )

    with pytest.raises(HTTPException) as exc:
        await rb_api._insert_filterset(db, payload=payload, principal=_principal("alice"))

    assert exc.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM ringbuffer_filtersets") is None


@pytest.mark.asyncio
async def test_admin_can_persist_existing_explicit_datapoints_but_not_unknown_ids(db: Database):
    existing_id = str(uuid.uuid4())
    await _insert_datapoint(db, existing_id)

    created = await rb_api._insert_filterset(
        db,
        payload=rb_api.RingBufferFiltersetIn(name="Admin", filter=rb_api.FilterCriteria(datapoints=[existing_id])),
        principal=_principal("admin", admin=True),
    )
    assert created.filter.datapoints == [existing_id]

    with pytest.raises(HTTPException) as exc:
        await rb_api._insert_filterset(
            db,
            payload=rb_api.RingBufferFiltersetIn(name="Unknown", filter=rb_api.FilterCriteria(datapoints=[str(uuid.uuid4())])),
            principal=_principal("admin", admin=True),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_update_rejects_out_of_scope_explicit_datapoint_without_mutating_filter(db: Database):
    blocked_id = str(uuid.uuid4())
    await _insert_datapoint(db, blocked_id)
    created = await rb_api._insert_filterset(db, payload=_payload(), principal=_principal("alice"))

    with pytest.raises(HTTPException) as exc:
        await rb_api.update_ringbuffer_filterset(
            created.id,
            _JsonRequest({"filter": {"datapoints": [blocked_id]}}),  # type: ignore[arg-type]
            current_user=_principal("alice"),
            db=db,
        )

    assert exc.value.status_code == 403
    row = await db.fetchone("SELECT filter_json FROM ringbuffer_filtersets WHERE id=?", (created.id,))
    assert json.loads(row["filter_json"])["adapters"] == ["api"]
