"""Tests for principal grant persistence administration (#983)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid

import aiosqlite
import pytest
from fastapi import FastAPI, HTTPException, Response, status
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.requests import Request

from obs.api.auth import get_admin_user
from obs.api.v1 import authz as authz_api
from obs.db.database import Database, get_db
from obs.models.authz import AuthzPrincipalGrant, AuthzPrincipalGrantsReplace, AuthzPrincipalGrantsResponse

NOW = "2026-07-10T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.fixture
async def file_db(tmp_path) -> Database:
    database = Database(str(tmp_path / "authz-grants.sqlite"))
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/v1/authz/principals/user/alice/grants",
            "query_string": b"",
            "headers": [(b"x-request-id", b"grant-test"), (b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
        }
    )


async def _insert_user(db: Database, username: str = "alice") -> None:
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, 'hash', 0, ?)",
        (str(uuid.uuid4()), username, NOW),
    )


async def _insert_api_key(db: Database, key_id: str) -> None:
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', ?, 'alice', ?)",
        (key_id, f"hash-{key_id}", NOW),
    )


async def _insert_tree(db: Database) -> None:
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES ('tree', 'tree', '', ?, ?)",
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


async def _insert_datapoint(db: Database, datapoint_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'FLOAT', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (datapoint_id, datapoint_id, f"obs/test/{datapoint_id}", NOW, NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_type: str = "user",
    principal_id: str = "alice",
    node_type: str = "hierarchy",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (principal_type, principal_id, node_type, node_id, role, effect),
    )


def _replace(grants: list[AuthzPrincipalGrant]) -> AuthzPrincipalGrantsReplace:
    return AuthzPrincipalGrantsReplace(grants=grants)


async def _get_grants(
    db: Database,
    principal_type: str,
    principal_id: str,
) -> tuple[AuthzPrincipalGrantsResponse, Response]:
    http_response = Response()
    result = await authz_api.get_principal_grants(
        principal_type,
        principal_id,
        response=http_response,
        db=db,
        _admin="admin",
    )
    return result, http_response


async def _replace_current(
    db: Database,
    principal_type: str,
    principal_id: str,
    body: AuthzPrincipalGrantsReplace,
) -> tuple[AuthzPrincipalGrantsResponse, Response]:
    _, current_response = await _get_grants(db, principal_type, principal_id)
    http_response = Response()
    result = await authz_api.replace_principal_grants(
        principal_type,
        principal_id,
        body,
        _request(),
        response=http_response,
        if_match=current_response.headers["etag"],
        db=db,
        _admin="admin",
    )
    return result, http_response


def _grant_sha(grants: list[dict[str, str]]) -> str:
    payload = json.dumps(
        sorted(grants, key=lambda grant: (grant["node_type"], grant["node_id"])), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@pytest.mark.asyncio
async def test_replace_user_grants_roundtrips_full_set_and_audits_only_counts(db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    for node_id in ("added", "changed", "removed", "same"):
        await _insert_node(db, node_id)
    await _insert_grant(db, node_id="changed", role="guest")
    await _insert_grant(db, node_id="removed", role="resident")
    await _insert_grant(db, node_id="same", role="operator", effect="deny")

    response, put_http_response = await _replace_current(
        db,
        "user",
        "alice",
        _replace(
            [
                AuthzPrincipalGrant(node_type="hierarchy", node_id="same", role="operator", effect="deny"),
                AuthzPrincipalGrant(node_type="hierarchy", node_id="added", role="owner"),
                AuthzPrincipalGrant(node_type="hierarchy", node_id="changed", role="resident"),
            ]
        ),
    )

    assert response.model_dump() == {
        "principal": {"principal_type": "user", "principal_id": "alice"},
        "grants": [
            {"node_type": "hierarchy", "node_id": "added", "role": "owner", "effect": "allow"},
            {"node_type": "hierarchy", "node_id": "changed", "role": "resident", "effect": "allow"},
            {"node_type": "hierarchy", "node_id": "same", "role": "operator", "effect": "deny"},
        ],
    }
    loaded, get_http_response = await _get_grants(db, "user", "alice")
    assert loaded == response
    assert put_http_response.headers["etag"] == get_http_response.headers["etag"]
    assert put_http_response.headers["cache-control"] == "no-store"

    audit = await db.fetchone("SELECT * FROM audit_log_entries WHERE action='authz.grants.replace'")
    assert audit is not None
    assert audit["actor"] == "admin"
    assert audit["resource_type"] == "authz_principal"
    assert audit["resource_id"] == "user:alice"
    assert audit["request_id"] == "grant-test"
    details = json.loads(audit["details_json"])
    before_grants = [
        {"node_type": "hierarchy", "node_id": "changed", "role": "guest", "effect": "allow"},
        {"node_type": "hierarchy", "node_id": "removed", "role": "resident", "effect": "allow"},
        {"node_type": "hierarchy", "node_id": "same", "role": "operator", "effect": "deny"},
    ]
    after_grants = response.model_dump()["grants"]
    assert details == {
        "added_count": 1,
        "after_count": 3,
        "after_sha256": _grant_sha(after_grants),
        "before_count": 3,
        "before_sha256": _grant_sha(before_grants),
        "changes": [
            {
                "node_type": "hierarchy",
                "node_id": "added",
                "before": None,
                "after": {"role": "owner", "effect": "allow"},
            },
            {
                "node_type": "hierarchy",
                "node_id": "changed",
                "before": {"role": "guest", "effect": "allow"},
                "after": {"role": "resident", "effect": "allow"},
            },
            {
                "node_type": "hierarchy",
                "node_id": "removed",
                "before": {"role": "resident", "effect": "allow"},
                "after": None,
            },
        ],
        "removed_count": 1,
        "unchanged_count": 1,
        "updated_count": 1,
    }


@pytest.mark.asyncio
async def test_replace_with_empty_list_removes_every_grant_atomically(db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")
    await _insert_grant(db, node_id="room")

    response, _ = await _replace_current(db, "user", "alice", _replace([]))

    assert response.grants == []
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE principal_id='alice'") is None


@pytest.mark.asyncio
async def test_api_key_id_is_canonicalized_to_raw_lowercase_uuid_in_response_and_db(db: Database) -> None:
    key_id = str(uuid.uuid4())
    datapoint_id = str(uuid.uuid4())
    await _insert_api_key(db, key_id)
    await _insert_datapoint(db, datapoint_id)

    response, _ = await _replace_current(
        db,
        "api_key",
        f"api_key:{key_id.upper()}",
        _replace([AuthzPrincipalGrant(node_type="datapoint", node_id=datapoint_id, role="resident")]),
    )

    assert response.principal.principal_id == key_id
    row = await db.fetchone("SELECT principal_id FROM authz_node_roles")
    assert row is not None
    assert row["principal_id"] == key_id
    loaded, _ = await _get_grants(db, "api_key", f"api_key:{key_id}")
    assert loaded == response


@pytest.mark.asyncio
async def test_api_key_get_then_put_preserves_grants_and_removes_legacy_prefixed_alias(db: Database) -> None:
    key_id = str(uuid.uuid4())
    first_datapoint_id = str(uuid.uuid4())
    second_datapoint_id = str(uuid.uuid4())
    await _insert_api_key(db, key_id)
    await _insert_datapoint(db, first_datapoint_id)
    await _insert_datapoint(db, second_datapoint_id)
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id=key_id,
        node_type="datapoint",
        node_id=first_datapoint_id,
        role="guest",
    )
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id=f"api_key:{key_id}",
        node_type="datapoint",
        node_id=second_datapoint_id,
        role="resident",
        effect="deny",
    )

    loaded, raw_response = await _get_grants(db, "api_key", key_id)
    prefixed_loaded, prefixed_response = await _get_grants(db, "api_key", f"api_key:{key_id}")
    assert prefixed_loaded == loaded
    assert prefixed_response.headers["etag"] == raw_response.headers["etag"]
    replaced, _ = await _replace_current(db, "api_key", f"api_key:{key_id}", _replace(loaded.grants))

    assert replaced == loaded
    rows = await db.fetchall("SELECT principal_id, node_id FROM authz_node_roles ORDER BY node_id")
    assert {row["node_id"] for row in rows} == {first_datapoint_id, second_datapoint_id}
    assert {row["principal_id"] for row in rows} == {key_id}


@pytest.mark.asyncio
async def test_api_key_conflicting_raw_and_prefixed_grants_fail_closed(db: Database) -> None:
    key_id = str(uuid.uuid4())
    datapoint_id = str(uuid.uuid4())
    await _insert_api_key(db, key_id)
    await _insert_datapoint(db, datapoint_id)
    await _insert_grant(db, principal_type="api_key", principal_id=key_id, node_type="datapoint", node_id=datapoint_id, role="guest")
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id=f"api_key:{key_id}",
        node_type="datapoint",
        node_id=datapoint_id,
        role="owner",
    )

    with pytest.raises(HTTPException) as get_error:
        await _get_grants(db, "api_key", key_id)
    assert get_error.value.status_code == status.HTTP_409_CONFLICT

    with pytest.raises(HTTPException) as put_error:
        await authz_api.replace_principal_grants(
            "api_key",
            key_id,
            _replace([AuthzPrincipalGrant(node_type="datapoint", node_id=datapoint_id, role="resident")]),
            _request(),
            response=Response(),
            if_match='"' + "0" * 64 + '"',
            db=db,
            _admin="admin",
        )
    assert put_error.value.status_code == status.HTTP_409_CONFLICT

    rows = await db.fetchall("SELECT principal_id, role, effect FROM authz_node_roles ORDER BY principal_id")
    assert {(row["principal_id"], row["role"], row["effect"]) for row in rows} == {
        (key_id, "guest", "allow"),
        (f"api_key:{key_id}", "owner", "allow"),
    }
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_missing_principals_and_invalid_api_key_id_are_rejected(db: Database) -> None:
    with pytest.raises(HTTPException) as missing_user:
        await _get_grants(db, "user", "missing")
    assert missing_user.value.status_code == status.HTTP_404_NOT_FOUND

    key_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as missing_key:
        await _get_grants(db, "api_key", key_id)
    assert missing_key.value.status_code == status.HTTP_404_NOT_FOUND

    with pytest.raises(HTTPException) as invalid_key:
        await _get_grants(db, "api_key", "api_key:not-a-uuid")
    assert invalid_key.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", ["hierarchy", "datapoint", "ringbuffer_filterset"])
async def test_unknown_grant_target_is_rejected_without_changing_existing_set(db: Database, node_type: str) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "existing")
    await _insert_grant(db, node_id="existing")

    with pytest.raises(HTTPException) as exc_info:
        await _replace_current(db, "user", "alice", _replace([AuthzPrincipalGrant(node_type=node_type, node_id="missing", role="owner")]))

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    row = await db.fetchone("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert row is not None
    assert row["node_id"] == "existing"
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_get_exposes_orphan_grant_but_put_rejects_it_without_data_loss(db: Database) -> None:
    await _insert_user(db)
    await _insert_grant(db, node_id="deleted-node")

    loaded, loaded_response = await _get_grants(db, "user", "alice")
    assert [grant.node_id for grant in loaded.grants] == ["deleted-node"]

    with pytest.raises(HTTPException) as exc_info:
        await authz_api.replace_principal_grants(
            "user",
            "alice",
            _replace(loaded.grants),
            _request(),
            response=Response(),
            if_match=loaded_response.headers["etag"],
            db=db,
            _admin="admin",
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_id='deleted-node'") is not None


def test_replace_model_rejects_duplicate_node_type_and_node_id() -> None:
    grant = {"node_type": "hierarchy", "node_id": "room", "role": "guest"}
    with pytest.raises(ValidationError, match="Duplicate grants"):
        AuthzPrincipalGrantsReplace(grants=[grant, {**grant, "role": "owner"}])


def test_grant_etag_is_strong_and_independent_of_input_order() -> None:
    grants = [
        AuthzPrincipalGrant(node_type="hierarchy", node_id="z-room", role="guest"),
        AuthzPrincipalGrant(node_type="datapoint", node_id="a-dp", role="owner", effect="deny"),
    ]
    etag = authz_api._grants_etag(grants)
    assert re.fullmatch(r'"[0-9a-f]{64}"', etag)
    assert authz_api._grants_etag(list(reversed(grants))) == etag


@pytest.mark.asyncio
@pytest.mark.parametrize("if_match", [None, "*", "not-quoted", 'W/"' + "0" * 64 + '"', '"' + "A" * 64 + '"'])
async def test_replace_rejects_missing_or_invalid_if_match(db: Database, if_match: str | None) -> None:
    await _insert_user(db)
    with pytest.raises(HTTPException) as exc_info:
        await authz_api.replace_principal_grants(
            "user",
            "alice",
            _replace([]),
            _request(),
            response=Response(),
            if_match=if_match,
            db=db,
            _admin="admin",
        )
    assert exc_info.value.status_code == status.HTTP_428_PRECONDITION_REQUIRED
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_stale_if_match_does_not_mutate_or_audit(db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")
    _, initial_response = await _get_grants(db, "user", "alice")
    await _replace_current(
        db,
        "user",
        "alice",
        _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="room", role="guest")]),
    )

    with pytest.raises(HTTPException) as exc_info:
        await authz_api.replace_principal_grants(
            "user",
            "alice",
            _replace([]),
            _request(),
            response=Response(),
            if_match=initial_response.headers["etag"],
            db=db,
            _admin="admin",
        )

    assert exc_info.value.status_code == status.HTTP_412_PRECONDITION_FAILED
    rows = await db.fetchall("SELECT node_id, role FROM authz_node_roles")
    assert [(row["node_id"], row["role"]) for row in rows] == [("room", "guest")]
    audit_count = await db.fetchone("SELECT COUNT(*) AS count FROM audit_log_entries")
    assert audit_count is not None
    assert audit_count["count"] == 1


@pytest.mark.asyncio
async def test_concurrent_replacements_with_same_etag_have_one_winner(file_db: Database) -> None:
    await _insert_user(file_db)
    await _insert_tree(file_db)
    await _insert_node(file_db, "room-a")
    await _insert_node(file_db, "room-b")
    app = _api(file_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        initial_response = await client.get("/api/v1/authz/principals/user/alice/grants")
        initial_etag = initial_response.headers["etag"]

        async def replace(node_id: str):
            return await client.put(
                "/api/v1/authz/principals/user/alice/grants",
                json={"grants": [{"node_type": "hierarchy", "node_id": node_id, "role": "owner"}]},
                headers={"If-Match": initial_etag},
            )

        results = await asyncio.gather(replace("room-a"), replace("room-b"))

    successes = [result for result in results if result.status_code == status.HTTP_200_OK]
    failures = [result for result in results if result.status_code == status.HTTP_412_PRECONDITION_FAILED]
    assert len(successes) == 1
    assert len(failures) == 1
    row = await file_db.fetchone("SELECT node_id FROM authz_node_roles")
    assert row is not None
    assert row["node_id"] == successes[0].json()["grants"][0]["node_id"]
    audit_count = await file_db.fetchone("SELECT COUNT(*) AS count FROM audit_log_entries")
    assert audit_count is not None
    assert audit_count["count"] == 1


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_delete_and_insert(monkeypatch: pytest.MonkeyPatch, db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "before")
    await _insert_node(db, "after")
    await _insert_grant(db, node_id="before")

    original_execute = db.execute

    async def fail_audit_insert(sql: str, params=()):
        if "INSERT INTO audit_log_entries" in sql:
            raise RuntimeError("audit unavailable")
        return await original_execute(sql, params)

    monkeypatch.setattr(db, "execute", fail_audit_insert)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await _replace_current(
            db,
            "user",
            "alice",
            _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="after", role="owner")]),
        )

    rows = await db.fetchall("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert [row["node_id"] for row in rows] == ["before"]
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_commit_failure_after_audit_insert_rolls_back_both(monkeypatch: pytest.MonkeyPatch, db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "before")
    await _insert_node(db, "after")
    await _insert_grant(db, node_id="before")

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    original_open_connection = db._open_connection

    async def open_connection_with_failing_commit():
        conn = await original_open_connection()
        monkeypatch.setattr(conn, "commit", fail_commit)
        return conn

    monkeypatch.setattr(db, "_open_connection", open_connection_with_failing_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        await _replace_current(
            db,
            "user",
            "alice",
            _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="after", role="owner")]),
        )

    rows = await db.fetchall("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert [row["node_id"] for row in rows] == ["before"]
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_insert_failure_rolls_back_delete(monkeypatch: pytest.MonkeyPatch, db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "before")
    await _insert_node(db, "after")
    await _insert_grant(db, node_id="before")

    async def fail_insert(*_args, **_kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(db, "executemany", fail_insert)
    with pytest.raises(RuntimeError, match="insert failed"):
        await _replace_current(
            db,
            "user",
            "alice",
            _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="after", role="owner")]),
        )

    rows = await db.fetchall("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert [row["node_id"] for row in rows] == ["before"]
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_cancellation_between_delete_and_insert_rolls_back(monkeypatch: pytest.MonkeyPatch, db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "before")
    await _insert_node(db, "after")
    await _insert_grant(db, node_id="before")
    insert_started = asyncio.Event()
    wait_forever = asyncio.Event()

    async def pause_insert(*_args, **_kwargs):
        insert_started.set()
        await wait_forever.wait()

    monkeypatch.setattr(db, "executemany", pause_insert)
    _, current_response = await _get_grants(db, "user", "alice")
    replace_task = asyncio.create_task(
        authz_api.replace_principal_grants(
            "user",
            "alice",
            _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="after", role="owner")]),
            _request(),
            response=Response(),
            if_match=current_response.headers["etag"],
            db=db,
            _admin="admin",
        )
    )
    await insert_started.wait()
    replace_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replace_task

    rows = await db.fetchall("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert [row["node_id"] for row in rows] == ["before"]
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_normal_commit_cannot_interleave_with_replace_transaction(monkeypatch: pytest.MonkeyPatch, file_db: Database) -> None:
    db = file_db
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "before")
    await _insert_node(db, "after")
    await _insert_grant(db, node_id="before")
    audit_started = asyncio.Event()
    release_audit = asyncio.Event()

    async def pause_then_fail_audit(*_args, **_kwargs) -> int:
        audit_started.set()
        await release_audit.wait()
        raise RuntimeError("audit failed")

    monkeypatch.setattr(authz_api.AuditLogWriter, "write", pause_then_fail_audit)
    _, current_response = await _get_grants(db, "user", "alice")
    replace_task = asyncio.create_task(
        authz_api.replace_principal_grants(
            "user",
            "alice",
            _replace([AuthzPrincipalGrant(node_type="hierarchy", node_id="after", role="owner")]),
            _request(),
            response=Response(),
            if_match=current_response.headers["etag"],
            db=db,
            _admin="admin",
        )
    )
    await audit_started.wait()
    concurrent_commit = asyncio.create_task(db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES ('race', 'safe')"))
    await asyncio.sleep(0)
    assert not concurrent_commit.done()

    release_audit.set()
    with pytest.raises(RuntimeError, match="audit failed"):
        await replace_task
    await concurrent_commit

    rows = await db.fetchall("SELECT node_id FROM authz_node_roles WHERE principal_id='alice'")
    assert [row["node_id"] for row in rows] == ["before"]
    assert await db.fetchone("SELECT value FROM app_settings WHERE key='race'") is not None
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_nested_database_transaction_is_rejected(db: Database) -> None:
    with pytest.raises(RuntimeError, match="Nested database transactions"):
        async with db.transaction():
            async with db.transaction():
                pass


@pytest.mark.asyncio
async def test_disconnected_database_rejects_explicit_transaction() -> None:
    disconnected = Database(":memory:")
    with pytest.raises(RuntimeError, match="Database.connect"):
        async with disconnected.transaction():
            pass


@pytest.mark.asyncio
async def test_database_commit_and_rollback_helpers_respect_transaction_lock(db: Database) -> None:
    await db.execute("INSERT INTO app_settings (key, value) VALUES ('rolled-back', 'value')")
    await db.rollback()
    assert await db.fetchone("SELECT 1 FROM app_settings WHERE key='rolled-back'") is None

    await db.execute("INSERT INTO app_settings (key, value) VALUES ('committed', 'value')")
    await db.commit()
    assert await db.fetchone("SELECT value FROM app_settings WHERE key='committed'") is not None


@pytest.mark.asyncio
async def test_file_transaction_isolated_from_direct_shared_connection_commit(file_db: Database, tmp_path) -> None:
    transaction_started = asyncio.Event()
    release_transaction = asyncio.Event()

    async def rollback_transaction() -> None:
        with pytest.raises(RuntimeError, match="rollback requested"):
            async with file_db.transaction():
                await file_db.execute("INSERT INTO app_settings (key, value) VALUES ('transaction', 'partial')")
                transaction_started.set()
                await release_transaction.wait()
                raise RuntimeError("rollback requested")

    transaction_task = asyncio.create_task(rollback_transaction())
    await transaction_started.wait()
    shared_conn = file_db._conn
    assert shared_conn is not None

    async def direct_shared_write() -> None:
        await shared_conn.execute("INSERT INTO app_settings (key, value) VALUES ('shared', 'committed')")
        await shared_conn.commit()

    shared_write_task = asyncio.create_task(direct_shared_write())
    await asyncio.sleep(0.05)
    assert not shared_write_task.done()

    observer = await aiosqlite.connect(str(tmp_path / "authz-grants.sqlite"))
    try:
        async with observer.execute("SELECT 1 FROM app_settings WHERE key='transaction'") as cursor:
            assert await cursor.fetchone() is None
    finally:
        await observer.close()

    release_transaction.set()
    await transaction_task
    await shared_write_task
    assert await file_db.fetchone("SELECT 1 FROM app_settings WHERE key='transaction'") is None
    assert await file_db.fetchone("SELECT value FROM app_settings WHERE key='shared'") is not None


@pytest.mark.asyncio
async def test_legacy_execute_then_commit_does_not_deadlock_explicit_transaction(file_db: Database) -> None:
    await file_db.execute("INSERT INTO app_settings (key, value) VALUES ('legacy', 'pending')")
    transaction_attempted = asyncio.Event()

    async def explicit_write() -> None:
        transaction_attempted.set()
        async with file_db.transaction():
            await file_db.execute("INSERT INTO app_settings (key, value) VALUES ('explicit', 'committed')")

    transaction_task = asyncio.create_task(explicit_write())
    await transaction_attempted.wait()
    await asyncio.sleep(0.05)
    assert not transaction_task.done()

    await file_db.commit()
    await asyncio.wait_for(transaction_task, timeout=2)
    rows = await file_db.fetchall("SELECT key FROM app_settings WHERE key IN ('legacy', 'explicit') ORDER BY key")
    assert [row["key"] for row in rows] == ["explicit", "legacy"]


@pytest.mark.asyncio
async def test_disconnect_waits_for_explicit_transaction(file_db: Database, tmp_path) -> None:
    transaction_started = asyncio.Event()
    release_transaction = asyncio.Event()

    async def paused_transaction() -> None:
        async with file_db.transaction():
            await file_db.execute("INSERT INTO app_settings (key, value) VALUES ('disconnect', 'safe')")
            transaction_started.set()
            await release_transaction.wait()

    transaction_task = asyncio.create_task(paused_transaction())
    await transaction_started.wait()
    disconnect_task = asyncio.create_task(file_db.disconnect())
    await asyncio.sleep(0.05)
    assert not disconnect_task.done()

    release_transaction.set()
    await transaction_task
    await disconnect_task

    observer = await aiosqlite.connect(str(tmp_path / "authz-grants.sqlite"))
    try:
        async with observer.execute("SELECT value FROM app_settings WHERE key='disconnect'") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "safe"
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_conn_property_outside_event_loop_returns_shared_connection(file_db: Database) -> None:
    shared_conn = await asyncio.to_thread(lambda: file_db.conn)
    assert shared_conn is file_db._conn


async def _allow_admin() -> str:
    return "admin"


async def _deny_admin() -> str:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")


def _api(db: Database, admin_dependency=_allow_admin) -> FastAPI:
    app = FastAPI()
    app.include_router(authz_api.router, prefix="/api/v1/authz")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_admin_user] = admin_dependency
    return app


@pytest.mark.asyncio
async def test_http_put_requires_valid_strong_if_match(db: Database) -> None:
    await _insert_user(db)
    app = _api(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        missing = await client.put("/api/v1/authz/principals/user/alice/grants", json={"grants": []})
        weak = await client.put(
            "/api/v1/authz/principals/user/alice/grants",
            json={"grants": []},
            headers={"If-Match": 'W/"' + "0" * 64 + '"'},
        )

    assert missing.status_code == status.HTTP_428_PRECONDITION_REQUIRED
    assert weak.status_code == status.HTTP_428_PRECONDITION_REQUIRED
    assert await db.fetchone("SELECT 1 FROM audit_log_entries") is None


@pytest.mark.asyncio
async def test_http_api_roundtrip_and_duplicate_validation(db: Database) -> None:
    await _insert_user(db)
    await _insert_tree(db)
    await _insert_node(db, "room")
    app = _api(db)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        initial_response = await client.get("/api/v1/authz/principals/user/alice/grants")
        put_response = await client.put(
            "/api/v1/authz/principals/user/alice/grants",
            json={"grants": [{"node_type": "hierarchy", "node_id": "room", "role": "guest", "effect": "allow"}]},
            headers={"If-Match": initial_response.headers["etag"]},
        )
        get_response = await client.get("/api/v1/authz/principals/user/alice/grants")
        duplicate_response = await client.put(
            "/api/v1/authz/principals/user/alice/grants",
            json={
                "grants": [
                    {"node_type": "hierarchy", "node_id": "room", "role": "guest"},
                    {"node_type": "hierarchy", "node_id": "room", "role": "owner"},
                ]
            },
        )
        missing_grants_response = await client.put(
            "/api/v1/authz/principals/user/alice/grants",
            json={},
        )

    assert put_response.status_code == status.HTTP_200_OK
    assert get_response.status_code == status.HTTP_200_OK
    assert initial_response.headers["cache-control"] == "no-store"
    assert put_response.headers["cache-control"] == "no-store"
    assert get_response.headers["etag"] == put_response.headers["etag"]
    assert initial_response.headers["etag"] != put_response.headers["etag"]
    assert re.fullmatch(r'"[0-9a-f]{64}"', get_response.headers["etag"])
    assert get_response.json() == put_response.json()
    assert duplicate_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert missing_grants_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert get_response.json()["grants"] == [{"node_type": "hierarchy", "node_id": "room", "role": "guest", "effect": "allow"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_http_grant_endpoints_are_admin_only(db: Database, method: str) -> None:
    app = _api(db, _deny_admin)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.request(
            method,
            "/api/v1/authz/principals/user/alice/grants",
            json={"grants": []} if method == "PUT" else None,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_http_api_rejects_unknown_principal_and_node_types(db: Database) -> None:
    app = _api(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        principal_response = await client.get("/api/v1/authz/principals/service/alice/grants")
        node_response = await client.put(
            "/api/v1/authz/principals/user/alice/grants",
            json={"grants": [{"node_type": "visu", "node_id": "room", "role": "guest"}]},
        )

    assert principal_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert node_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_http_api_forbids_extra_fields_and_supports_slash_in_username(db: Database) -> None:
    await _insert_user(db, "team/alice")
    app = _api(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        initial = await client.get("/api/v1/authz/principals/user/team%2Falice/grants")
        extra_body = await client.put(
            "/api/v1/authz/principals/user/team%2Falice/grants",
            json={"grants": [], "unexpected": True},
        )
        extra_grant = await client.put(
            "/api/v1/authz/principals/user/team%2Falice/grants",
            json={"grants": [{"node_type": "hierarchy", "node_id": "room", "role": "guest", "unexpected": True}]},
        )
        valid = await client.put(
            "/api/v1/authz/principals/user/team%2Falice/grants",
            json={"grants": []},
            headers={"If-Match": initial.headers["etag"]},
        )

    assert extra_body.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert extra_grant.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert valid.status_code == status.HTTP_200_OK
    assert valid.json()["principal"] == {"principal_type": "user", "principal_id": "team/alice"}
