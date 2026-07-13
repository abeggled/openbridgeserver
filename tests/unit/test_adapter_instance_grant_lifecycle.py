from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import adapters as adapters_api
from obs.api.v1 import config as config_api
from obs.db.database import Database


NOW = "2026-07-13T00:00:00+00:00"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_datapoint(db: Database, datapoint_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias,
             persist_value, record_history, created_at, updated_at)
        VALUES (?, 'Test', 'BOOLEAN', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (datapoint_id, f"obs/test/{datapoint_id}", NOW, NOW),
    )


async def _insert_instance(db: Database, instance_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances
            (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, 'MQTT', ?, '{}', 0, ?, ?)
        """,
        (instance_id, f"MQTT {instance_id}", NOW, NOW),
    )


async def _insert_binding(db: Database, binding_id: str, datapoint_id: str, instance_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction,
             config, enabled, created_at, updated_at)
        VALUES (?, ?, 'MQTT', ?, 'SOURCE', '{}', 1, ?, ?)
        """,
        (binding_id, datapoint_id, instance_id, NOW, NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_id: str,
    node_type: str,
    node_id: str,
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, ?, ?, 'operator', ?)
        """,
        (principal_id, node_type, node_id, effect),
    )


async def _insert_instance_with_binding(db: Database, instance_id: str) -> tuple[str, str]:
    datapoint_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    await _insert_datapoint(db, datapoint_id)
    await _insert_instance(db, instance_id)
    await _insert_binding(db, binding_id, datapoint_id, instance_id)
    return datapoint_id, binding_id


@pytest.mark.asyncio
async def test_delete_instance_atomically_removes_matching_bindings_and_grants(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    deleted_id = str(uuid.uuid4())
    surviving_id = str(uuid.uuid4())
    _, binding_id = await _insert_instance_with_binding(db, deleted_id)
    await _insert_instance(db, surviving_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=deleted_id)
    await _insert_grant(db, principal_id="api-user", node_type="adapter_instance", node_id=deleted_id, effect="deny")
    await _insert_grant(db, principal_id="bob", node_type="adapter_instance", node_id=surviving_id, effect="deny")
    await _insert_grant(db, principal_id="alice", node_type="hierarchy", node_id="home", effect="deny")
    monkeypatch.setattr(adapters_api.adapter_registry, "stop_instance", AsyncMock())

    await adapters_api.delete_instance(uuid.UUID(deleted_id), _user="admin", db=db)

    assert await db.fetchone("SELECT 1 FROM adapter_instances WHERE id=?", (deleted_id,)) is None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (binding_id,)) is None
    assert (
        await db.fetchone(
            "SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance' AND node_id=?",
            (deleted_id,),
        )
        is None
    )
    surviving = await db.fetchall("SELECT principal_id, node_type, node_id, effect FROM authz_node_roles ORDER BY node_type, node_id")
    assert [tuple(row) for row in surviving] == [
        ("bob", "adapter_instance", surviving_id, "deny"),
        ("alice", "hierarchy", "home", "deny"),
    ]


@pytest.mark.asyncio
async def test_delete_instance_rolls_back_bindings_and_grants_on_instance_failure(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    instance_id = str(uuid.uuid4())
    _, binding_id = await _insert_instance_with_binding(db, instance_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=instance_id)
    await db.execute_and_commit(
        f"""
        CREATE TRIGGER reject_adapter_delete
        BEFORE DELETE ON adapter_instances
        WHEN OLD.id = '{instance_id}'
        BEGIN SELECT RAISE(ABORT, 'reject adapter delete'); END
        """
    )
    monkeypatch.setattr(adapters_api.adapter_registry, "stop_instance", AsyncMock())

    with pytest.raises(sqlite3.IntegrityError, match="reject adapter delete"):
        await adapters_api.delete_instance(uuid.UUID(instance_id), _user="admin", db=db)

    assert await db.fetchone("SELECT 1 FROM adapter_instances WHERE id=?", (instance_id,)) is not None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (binding_id,)) is not None
    assert (
        await db.fetchone(
            "SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance' AND node_id=?",
            (instance_id,),
        )
        is not None
    )


@pytest.mark.asyncio
async def test_clear_adapters_removes_all_instance_grants_and_preserves_unrelated_denies(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    first_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    await _insert_instance_with_binding(db, first_id)
    await _insert_instance_with_binding(db, second_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=first_id)
    await _insert_grant(db, principal_id="bob", node_type="adapter_instance", node_id=second_id, effect="deny")
    await _insert_grant(db, principal_id="alice", node_type="hierarchy", node_id="home", effect="deny")

    with patch("obs.adapters.registry.stop_all", new_callable=AsyncMock):
        result = await config_api.clear_adapters(_admin="admin", db=db)

    assert result.deleted == 2
    assert result.bindings_deleted == 2
    assert result.errors == []
    assert await db.fetchone("SELECT 1 FROM adapter_instances") is None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance'") is None
    unrelated = await db.fetchone("SELECT principal_id, node_type, node_id, effect FROM authz_node_roles WHERE node_type='hierarchy'")
    assert tuple(unrelated) == ("alice", "hierarchy", "home", "deny")


@pytest.mark.asyncio
async def test_clear_adapters_rolls_back_bindings_and_grants_on_instance_failure(
    db: Database,
) -> None:
    instance_id = str(uuid.uuid4())
    _, binding_id = await _insert_instance_with_binding(db, instance_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=instance_id)
    await db.execute_and_commit(
        """
        CREATE TRIGGER reject_all_adapter_deletes
        BEFORE DELETE ON adapter_instances
        BEGIN SELECT RAISE(ABORT, 'reject all adapter deletes'); END
        """
    )

    with patch("obs.adapters.registry.stop_all", new_callable=AsyncMock):
        result = await config_api.clear_adapters(_admin="admin", db=db)

    assert result.errors == ["Adapters clear failed: reject all adapter deletes"]
    assert await db.fetchone("SELECT 1 FROM adapter_instances WHERE id=?", (instance_id,)) is not None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (binding_id,)) is not None
    assert (
        await db.fetchone(
            "SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance' AND node_id=?",
            (instance_id,),
        )
        is not None
    )


@pytest.mark.asyncio
async def test_factory_reset_removes_adapter_instances_bindings_and_grants(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    instance_id = str(uuid.uuid4())
    await _insert_instance_with_binding(db, instance_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=instance_id)
    registry = SimpleNamespace(_points={}, _values={})
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as logic_manager,
        patch("obs.api.v1.icons._icons_dir", return_value=MagicMock(glob=MagicMock(return_value=[]))),
    ):
        logic_manager.return_value.reload = AsyncMock()
        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.bindings_deleted == 1
    assert result.adapter_instances_deleted == 1
    assert result.errors == []
    assert await db.fetchone("SELECT 1 FROM adapter_instances") is None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance'") is None


@pytest.mark.asyncio
async def test_factory_reset_rolls_back_adapter_resources_and_grants_together(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
) -> None:
    instance_id = str(uuid.uuid4())
    datapoint_id, binding_id = await _insert_instance_with_binding(db, instance_id)
    await _insert_grant(db, principal_id="alice", node_type="adapter_instance", node_id=instance_id)
    await db.execute_and_commit(
        """
        CREATE TRIGGER reject_factory_adapter_delete
        BEFORE DELETE ON adapter_instances
        BEGIN SELECT RAISE(ABORT, 'reject factory adapter delete'); END
        """
    )
    registry = SimpleNamespace(_points={}, _values={})
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as logic_manager,
        patch("obs.api.v1.icons._icons_dir", return_value=MagicMock(glob=MagicMock(return_value=[]))),
    ):
        logic_manager.return_value.reload = AsyncMock()
        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.errors == ["DataPoints and adapters reset failed: reject factory adapter delete"]
    assert await db.fetchone("SELECT 1 FROM datapoints WHERE id=?", (datapoint_id,)) is not None
    assert await db.fetchone("SELECT 1 FROM adapter_instances WHERE id=?", (instance_id,)) is not None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (binding_id,)) is not None
    assert (
        await db.fetchone(
            "SELECT 1 FROM authz_node_roles WHERE node_type='adapter_instance' AND node_id=?",
            (instance_id,),
        )
        is not None
    )
