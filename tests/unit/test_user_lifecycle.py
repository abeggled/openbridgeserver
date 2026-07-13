from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from obs.api import auth
from obs.api.v1 import logic, visu
from obs.db.database import Database
from obs.logic.models import LogicGraphCreate
from obs.models.visu import VisuNodeCreate


async def _user(db: Database, username: str, *, admin: bool = False) -> None:
    await db.execute(
        """INSERT INTO users
           (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at)
           VALUES (?, ?, 'hash', ?, 0, NULL, ?)""",
        (f"user-{username}", username, int(admin), datetime.now(UTC).isoformat()),
    )
    await db.commit()


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        await _user(database, "admin", admin=True)
        await _user(database, "alice")
        await _user(database, "bob")
        yield database
    finally:
        await database.disconnect()


async def _owned_state(db: Database) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        """INSERT INTO visu_nodes
           (id, name, type, page_config, created_at, updated_at, created_by)
           VALUES ('page-a', 'Page', 'PAGE', '{}', ?, ?, 'alice')""",
        (now, now),
    )
    await db.execute(
        """INSERT INTO logic_graphs
           (id, name, flow_data, created_at, updated_at, created_by)
           VALUES ('graph-a', 'Graph', '{}', ?, ?, 'alice')""",
        (now, now),
    )
    await db.execute(
        """INSERT INTO ringbuffer_filtersets
           (id, name, filter_json, created_at, updated_at, created_by)
           VALUES ('filter-a', 'Filter', '{}', ?, ?, 'alice')""",
        (now, now),
    )
    await db.execute(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES ('key-a', 'Key', 'hash-a', 'alice', ?)",
        (now,),
    )
    await db.executemany(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('api_key', ?, 'hierarchy', 'home', 'resident', 'allow')""",
        [("key-a",), ("api_key:key-a",)],
    )
    await db.execute(
        "INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role) VALUES ('user', 'alice', 'hierarchy', 'home', 'owner')"
    )
    await db.execute(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role)
           VALUES ('user', 'alice', 'visu_page', 'page-a', 'guest')""",
    )
    await db.execute(
        """INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role)
           VALUES ('user', 'alice', 'ringbuffer_filterset', 'filter-a', 'owner')"""
    )
    # V43 snapshots legacy all-authenticated READ access, so a successor may
    # already hold a GUEST row for the owned filterset.
    await db.execute(
        """INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role)
           VALUES ('user', 'bob', 'ringbuffer_filterset', 'filter-a', 'guest')"""
    )
    await db.execute("INSERT INTO ringbuffer_filterset_user_state (username, filterset_id) VALUES ('alice', 'filter-a')")
    await db.commit()


@pytest.mark.asyncio
async def test_migration_keeps_legacy_artifacts_system_owned(db: Database):
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO logic_graphs (id, name, flow_data, created_at, updated_at) VALUES ('legacy', 'Legacy', '{}', ?, ?)",
        (now, now),
    )
    await db.commit()
    row = await db.fetchone("SELECT created_by FROM logic_graphs WHERE id='legacy'")
    assert row["created_by"] is None


@pytest.mark.asyncio
async def test_new_pages_and_graphs_record_the_creating_user(db: Database):
    graph = await logic.create_graph(LogicGraphCreate(name="Owned graph"), _user="alice", db=db)
    page = await visu.create_node(VisuNodeCreate(name="Owned page"), _user="alice", db=db)
    folder = await visu.create_node(VisuNodeCreate(name="System folder", type="LOCATION"), _user="alice", db=db)

    assert (await db.fetchone("SELECT created_by FROM logic_graphs WHERE id=?", (graph.id,)))["created_by"] == "alice"
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id=?", (page.id,)))["created_by"] == "alice"
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id=?", (folder.id,)))["created_by"] is None


@pytest.mark.asyncio
async def test_preflight_is_deterministic_and_non_sensitive(db: Database):
    await _owned_state(db)
    first = await auth._deletion_inventory(db, "alice")
    second = await auth._deletion_inventory(db, "alice")
    assert first == second
    assert first.model_dump() == {
        "revision": first.revision,
        "username": "alice",
        "visu_page_ids": ["page-a"],
        "logic_graph_ids": ["graph-a"],
        "filterset_ids": ["filter-a"],
        "api_key_ids": ["key-a"],
        "grant_count": 3,
        "visu_acl_count": 1,
        "filterset_state_count": 1,
    }
    assert "hash-a" not in first.model_dump_json()


@pytest.mark.asyncio
async def test_preflight_endpoint_and_missing_user(db: Database):
    result = await auth.get_user_deletion_preflight("alice", _admin="admin", db=db)
    assert result.username == "alice"
    with pytest.raises(HTTPException) as exc:
        await auth._deletion_inventory(db, "missing")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_transfers_revokes_and_cleans_references_atomically(db: Database):
    await _owned_state(db)
    preflight = await auth._deletion_inventory(db, "alice")
    await auth.delete_user(
        "alice",
        auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
        admin_user="admin",
        db=db,
    )

    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is None
    assert await db.fetchone("SELECT 1 FROM api_keys WHERE id='key-a'") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE principal_type='api_key' AND principal_id IN ('key-a', 'api_key:key-a')") is None
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id='page-a'"))["created_by"] == "bob"
    assert (await db.fetchone("SELECT created_by FROM logic_graphs WHERE id='graph-a'"))["created_by"] == "bob"
    assert (await db.fetchone("SELECT created_by FROM ringbuffer_filtersets WHERE id='filter-a'"))["created_by"] is None
    transferred_artifact_grants = await db.fetchall(
        """SELECT node_type, node_id, role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='bob'
             AND node_type IN ('visu_page', 'logic_graph')
           ORDER BY node_type, node_id"""
    )
    assert [dict(row) for row in transferred_artifact_grants] == [
        {"node_type": "logic_graph", "node_id": "graph-a", "role": "owner", "effect": "allow"},
        {"node_type": "visu_page", "node_id": "page-a", "role": "owner", "effect": "allow"},
    ]
    transferred = await db.fetchone(
        """SELECT role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='bob'
             AND node_type='ringbuffer_filterset' AND node_id='filter-a'"""
    )
    assert dict(transferred) == {"role": "owner", "effect": "allow"}
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE principal_id='alice'") is None
    assert await db.fetchone("SELECT 1 FROM ringbuffer_filterset_user_state WHERE username='alice'") is None
    audit = await db.fetchone("SELECT details_json FROM audit_log_entries WHERE action='auth.user.deleted'")
    assert audit is not None
    assert "hash-a" not in audit["details_json"]


@pytest.mark.asyncio
async def test_admin_successor_keeps_transferred_owner_grants_after_demotion(db: Database):
    await _owned_state(db)
    await db.execute_and_commit("UPDATE users SET is_admin=1 WHERE username='bob'")
    preflight = await auth._deletion_inventory(db, "alice")

    await auth.delete_user(
        "alice",
        auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
        admin_user="admin",
        db=db,
    )
    await auth.update_user("bob", auth.UserUpdate(is_admin=False), _admin="admin", db=db)

    transferred_grants = await db.fetchall(
        """SELECT node_type, node_id, role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='bob'
             AND node_type IN ('visu_page', 'logic_graph')
           ORDER BY node_type, node_id"""
    )
    assert [dict(row) for row in transferred_grants] == [
        {"node_type": "logic_graph", "node_id": "graph-a", "role": "owner", "effect": "allow"},
        {"node_type": "visu_page", "node_id": "page-a", "role": "owner", "effect": "allow"},
    ]


@pytest.mark.asyncio
async def test_stale_preflight_rolls_back_everything(db: Database):
    await _owned_state(db)
    preflight = await auth._deletion_inventory(db, "alice")
    await db.execute("UPDATE logic_graphs SET created_by='bob' WHERE id='graph-a'")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await auth.delete_user(
            "alice",
            auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
            admin_user="admin",
            db=db,
        )
    assert exc.value.status_code == 409
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is not None
    assert await db.fetchone("SELECT 1 FROM api_keys WHERE id='key-a'") is not None


@pytest.mark.asyncio
async def test_same_count_principal_reference_race_invalidates_revision(db: Database):
    await _owned_state(db)
    preflight = await auth._deletion_inventory(db, "alice")
    await db.execute("UPDATE authz_node_roles SET node_id='other-home' WHERE principal_id='alice'")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await auth.delete_user(
            "alice",
            auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
            admin_user="admin",
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_requires_an_accepted_preflight_revision(db: Database):
    with pytest.raises(HTTPException) as exc:
        await auth.delete_user("alice", admin_user="admin", db=db)
    assert exc.value.status_code == 428
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is not None


@pytest.mark.asyncio
async def test_owned_artifacts_require_a_valid_successor(db: Database):
    await _owned_state(db)
    preflight = await auth._deletion_inventory(db, "alice")
    with pytest.raises(HTTPException) as missing:
        await auth.delete_user(
            "alice",
            auth.UserDeletionRequest(revision=preflight.revision),
            admin_user="admin",
            db=db,
        )
    assert missing.value.status_code == 422

    with pytest.raises(HTTPException) as invalid:
        await auth.delete_user(
            "alice",
            auth.UserDeletionRequest(revision=preflight.revision, successor_username="missing"),
            admin_user="admin",
            db=db,
        )
    assert invalid.value.status_code == 422
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is not None


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_deletion(db: Database, monkeypatch):
    await _owned_state(db)
    preflight = await auth._deletion_inventory(db, "alice")

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await auth.delete_user(
            "alice",
            auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
            admin_user="admin",
            db=db,
        )
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is not None
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id='page-a'"))["created_by"] == "alice"
    assert await db.fetchone("SELECT 1 FROM api_keys WHERE id='key-a'") is not None
    key_grants = await db.fetchall("SELECT principal_id FROM authz_node_roles WHERE principal_type='api_key' ORDER BY principal_id")
    assert [row["principal_id"] for row in key_grants] == ["api_key:key-a", "key-a"]


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_user_creation(db: Database, monkeypatch):
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await auth.create_user(auth.UserCreate(username="charlie", password="secret"), _admin="admin", db=db)

    assert await db.fetchone("SELECT 1 FROM users WHERE username='charlie'") is None
    assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.created'") is None


@pytest.mark.asyncio
async def test_rename_preserves_ownership_and_principal_references(db: Database):
    await _owned_state(db)
    result = await auth.update_user("alice", auth.UserUpdate(username="alicia"), _admin="admin", db=db)
    assert result.username == "alicia"
    for table, column in (
        ("api_keys", "owner"),
        ("logic_graphs", "created_by"),
        ("visu_nodes", "created_by"),
        ("ringbuffer_filtersets", "created_by"),
        ("authz_node_roles", "principal_id"),
        ("ringbuffer_filterset_user_state", "username"),
    ):
        assert await db.fetchone(f"SELECT 1 FROM {table} WHERE {column}='alicia'") is not None


@pytest.mark.asyncio
async def test_rename_audit_failure_rolls_back_principal_and_ownership(db: Database, monkeypatch):
    await _owned_state(db)

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await auth.update_user("alice", auth.UserUpdate(username="alicia"), _admin="admin", db=db)
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is not None
    assert await db.fetchone("SELECT 1 FROM users WHERE username='alicia'") is None
    assert (await db.fetchone("SELECT created_by FROM logic_graphs WHERE id='graph-a'"))["created_by"] == "alice"


@pytest.mark.asyncio
async def test_last_admin_cannot_be_demoted_or_deleted(db: Database):
    with pytest.raises(HTTPException) as demote:
        await auth.update_user("admin", auth.UserUpdate(is_admin=False), _admin="admin", db=db)
    assert demote.value.status_code == 400

    preflight = await auth._deletion_inventory(db, "admin")
    with pytest.raises(HTTPException) as delete:
        await auth.delete_user(
            "admin",
            auth.UserDeletionRequest(revision=preflight.revision),
            admin_user="other-admin",
            db=db,
        )
    assert delete.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_principal_name_cannot_be_reused(db: Database):
    await db.execute(
        "INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role) VALUES ('user', 'retired', 'hierarchy', 'home', 'guest')"
    )
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await auth.create_user(auth.UserCreate(username="retired", password="secret"), _admin="admin", db=db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_user_audit_failure_rolls_back(db: Database, monkeypatch):
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await auth.create_user(auth.UserCreate(username="newuser", password="secret"), _admin="admin", db=db)
    assert await db.fetchone("SELECT 1 FROM users WHERE username='newuser'") is None


@pytest.mark.asyncio
async def test_delete_cleans_up_api_key_grants(db: Database):
    await _owned_state(db)
    await db.execute(
        "INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role) VALUES ('api_key', 'key-a', 'hierarchy', 'home', 'guest')"
    )
    await db.commit()

    preflight = await auth._deletion_inventory(db, "alice")
    await auth.delete_user(
        "alice",
        auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
        admin_user="admin",
        db=db,
    )

    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE principal_type='api_key' AND principal_id='key-a'") is None


@pytest.mark.asyncio
async def test_delete_transfers_grants_to_admin_successor(db: Database):
    await _owned_state(db)
    await db.execute("UPDATE users SET is_admin=1 WHERE username='bob'")
    await db.commit()

    preflight = await auth._deletion_inventory(db, "alice")
    await auth.delete_user(
        "alice",
        auth.UserDeletionRequest(revision=preflight.revision, successor_username="bob"),
        admin_user="admin",
        db=db,
    )

    transferred = await db.fetchall(
        """SELECT node_type, node_id, role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='bob'
             AND node_type IN ('visu_page', 'logic_graph')
           ORDER BY node_type, node_id"""
    )
    assert [dict(r) for r in transferred] == [
        {"node_type": "logic_graph", "node_id": "graph-a", "role": "owner", "effect": "allow"},
        {"node_type": "visu_page", "node_id": "page-a", "role": "owner", "effect": "allow"},
    ]
