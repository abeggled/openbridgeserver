from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import config as config_api
from obs.db.database import Database


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


class _EmptyRegistry:
    _points: dict = {}
    _values: dict = {}

    def all(self) -> list:
        return []


@pytest.mark.asyncio
async def test_json_export_import_preserves_central_authz_and_api_key_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    username = "backup-user"
    key_id = str(uuid.uuid4())
    now = "2026-07-13T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, 'hash', 0, ?)",
        (str(uuid.uuid4()), username, now),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'backup-key', ?, ?, ?)",
        (key_id, f"hash-{key_id}", username, now),
    )
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, source, created_at, updated_at) VALUES ('tree', 'Tree', '', '', ?, ?)",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO hierarchy_nodes
               (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES ('room', 'tree', NULL, 'Room', '', 0, NULL, ?, ?)""",
        (now, now),
    )
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, 'hierarchy', 'room', 'operator', 'deny')""",
        (username,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capability_sets (key_id, revision) VALUES (?, 7)",
        (key_id,),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capabilities (key_id, capability) VALUES (?, 'datapoint.metadata.write')",
        (key_id,),
    )

    registry = _EmptyRegistry()
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)
    with patch("obs.api.v1.icons._icons_dir") as icons_dir:
        icons_dir.return_value.glob.return_value = []
        exported = await config_api.export_config(_user="admin", db=db)

    assert [grant.model_dump() for grant in exported.authz_grants] == [
        {
            "principal_type": "user",
            "principal_id": username,
            "node_type": "hierarchy",
            "node_id": "room",
            "role": "operator",
            "effect": "deny",
        }
    ]
    assert [capability_set.model_dump() for capability_set in exported.api_key_capability_sets] == [
        {
            "key_id": key_id,
            "revision": 7,
            "capabilities": ["datapoint.metadata.write"],
        }
    ]

    await db.execute_and_commit("DELETE FROM authz_node_roles")
    await db.execute_and_commit("DELETE FROM api_key_capabilities")
    await db.execute_and_commit("DELETE FROM api_key_capability_sets")

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(body=exported, _user="admin", db=db)

    assert result.errors == []
    assert result.authz_grants_upserted == 1
    assert result.api_key_capability_sets_upserted == 1
    restored_grant = await db.fetchone(
        """SELECT role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id=? AND node_type='hierarchy' AND node_id='room'""",
        (username,),
    )
    assert (restored_grant["role"], restored_grant["effect"]) == ("operator", "deny")
    restored_set = await db.fetchone("SELECT revision FROM api_key_capability_sets WHERE key_id=?", (key_id,))
    assert restored_set["revision"] == 7
    restored_capabilities = await db.fetchall("SELECT capability FROM api_key_capabilities WHERE key_id=?", (key_id,))
    assert [row["capability"] for row in restored_capabilities] == ["datapoint.metadata.write"]


@pytest.mark.asyncio
async def test_factory_reset_clears_all_grants_while_clear_logic_stays_scoped(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    tmp_path,
) -> None:
    await db.executemany(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', ?, ?, 'owner', 'allow')
        """,
        [
            ("logic_graph", "graph"),
            ("hierarchy", "room"),
            ("datapoint", "datapoint"),
            ("adapter_instance", "adapter"),
            ("visu_page", "page"),
            ("ringbuffer_filterset", "filterset"),
        ],
    )
    await db.commit()

    monkeypatch.setattr(config_api, "get_registry", _EmptyRegistry)
    logic_manager = MagicMock()
    logic_manager.reload = AsyncMock()
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager", return_value=logic_manager),
        patch("obs.api.v1.icons._icons_dir", return_value=tmp_path),
    ):
        clear_result = await config_api.clear_logic(_admin="admin", db=db)
        grants_after_clear = await db.fetchall("SELECT node_type FROM authz_node_roles ORDER BY node_type")

        reset_result = await config_api.factory_reset(_admin="admin", db=db)
        grants_after_reset = await db.fetchall("SELECT node_type FROM authz_node_roles")

    assert clear_result.errors == []
    assert [row["node_type"] for row in grants_after_clear] == [
        "adapter_instance",
        "datapoint",
        "hierarchy",
        "ringbuffer_filterset",
        "visu_page",
    ]
    assert reset_result.errors == []
    assert grants_after_reset == []
