"""Regression tests for central grants tied to DataPoint lifecycle operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.api.v1 import config as config_api
from obs.core.registry import DataPointRegistry
from obs.db.database import Database
from obs.models.datapoint import DataPointCreate


NOW = "2026-07-13T00:00:00+00:00"
API_KEY_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _registry(db: Database) -> DataPointRegistry:
    return DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=AsyncMock())


async def _insert_principals(db: Database) -> None:
    await db.execute_and_commit(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, 'alice', 'hash', 0, ?)",
        (str(uuid.uuid4()), NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'key-hash', 'alice', ?)",
        (API_KEY_ID, NOW),
    )


async def _insert_grant(
    db: Database,
    *,
    principal_type: str = "user",
    principal_id: str = "alice",
    node_type: str = "datapoint",
    node_id: str,
    role: str = "guest",
    effect: str = "allow",
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles
            (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (principal_type, principal_id, node_type, node_id, role, effect),
    )


async def _insert_binding(db: Database, datapoint_id: str) -> str:
    binding_id = str(uuid.uuid4())
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction,
             config, enabled, created_at, updated_at)
        VALUES (?, ?, 'MQTT', NULL, 'SOURCE', '{}', 1, ?, ?)
        """,
        (binding_id, datapoint_id, NOW, NOW),
    )
    return binding_id


async def _grant_rows(db: Database) -> list[dict]:
    rows = await db.fetchall(
        """
        SELECT principal_type, principal_id, node_type, node_id, role, effect
        FROM authz_node_roles
        ORDER BY node_type, node_id, principal_type, principal_id
        """
    )
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_individual_delete_removes_only_matching_datapoint_grants(db: Database) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    deleted = await registry.create(DataPointCreate(name="Deleted"))
    surviving = await registry.create(DataPointCreate(name="Surviving"))
    await _insert_grant(db, node_id=str(deleted.id), role="resident")
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id="key-1",
        node_id=str(deleted.id),
        role="operator",
        effect="deny",
    )
    await _insert_grant(db, node_id=str(surviving.id), role="resident", effect="deny")
    await _insert_grant(db, node_type="hierarchy", node_id="room", role="guest")

    await registry.delete(deleted.id)

    assert registry.get(deleted.id) is None
    assert await db.fetchone("SELECT 1 FROM datapoints WHERE id=?", (str(deleted.id),)) is None
    assert await _grant_rows(db) == [
        {
            "principal_type": "user",
            "principal_id": "alice",
            "node_type": "datapoint",
            "node_id": str(surviving.id),
            "role": "resident",
            "effect": "deny",
        },
        {
            "principal_type": "user",
            "principal_id": "alice",
            "node_type": "hierarchy",
            "node_id": "room",
            "role": "guest",
            "effect": "allow",
        },
    ]


@pytest.mark.asyncio
async def test_individual_delete_rolls_back_grants_and_keeps_registry_on_resource_failure(db: Database) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    datapoint = await registry.create(DataPointCreate(name="Blocked"))
    await _insert_grant(db, node_id=str(datapoint.id), role="resident")
    await db.execute_and_commit(
        """
        CREATE TRIGGER block_datapoint_delete
        BEFORE DELETE ON datapoints
        BEGIN
            SELECT RAISE(ABORT, 'blocked datapoint delete');
        END
        """
    )

    with pytest.raises(Exception, match="blocked datapoint delete"):
        await registry.delete(datapoint.id)

    assert registry.get(datapoint.id) is datapoint
    assert await db.fetchone("SELECT 1 FROM datapoints WHERE id=?", (str(datapoint.id),)) is not None
    assert [row["node_id"] for row in await _grant_rows(db)] == [str(datapoint.id)]


@pytest.mark.asyncio
async def test_clear_datapoints_removes_all_datapoint_grants_and_preserves_other_denies(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    first = await registry.create(DataPointCreate(name="First"))
    second = await registry.create(DataPointCreate(name="Second"))
    await _insert_grant(db, node_id=str(first.id), role="resident")
    await _insert_grant(
        db,
        principal_type="api_key",
        principal_id="key-1",
        node_id=str(second.id),
        role="operator",
        effect="deny",
    )
    await _insert_grant(db, node_type="hierarchy", node_id="room", role="operator", effect="deny")
    await _insert_binding(db, str(first.id))
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.clear_datapoints(_admin="admin", db=db)

    assert result.deleted == 2
    assert result.bindings_deleted == 1
    assert result.errors == []
    assert registry.count() == 0
    assert await db.fetchone("SELECT 1 FROM datapoints LIMIT 1") is None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings LIMIT 1") is None
    assert await _grant_rows(db) == [
        {
            "principal_type": "user",
            "principal_id": "alice",
            "node_type": "hierarchy",
            "node_id": "room",
            "role": "operator",
            "effect": "deny",
        }
    ]


@pytest.mark.asyncio
async def test_clear_datapoints_rolls_back_grants_resources_and_bindings(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    datapoint = await registry.create(DataPointCreate(name="Blocked"))
    await _insert_grant(db, node_id=str(datapoint.id), role="resident")
    binding_id = await _insert_binding(db, str(datapoint.id))
    await db.execute_and_commit(
        """
        CREATE TRIGGER block_datapoint_delete
        BEFORE DELETE ON datapoints
        BEGIN
            SELECT RAISE(ABORT, 'blocked datapoint delete');
        END
        """
    )
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.clear_datapoints(_admin="admin", db=db)

    assert result.errors and "blocked datapoint delete" in result.errors[0]
    assert registry.get(datapoint.id) is datapoint
    assert await db.fetchone("SELECT 1 FROM datapoints WHERE id=?", (str(datapoint.id),)) is not None
    assert await db.fetchone("SELECT 1 FROM adapter_bindings WHERE id=?", (binding_id,)) is not None
    assert [row["node_id"] for row in await _grant_rows(db)] == [str(datapoint.id)]


@pytest.mark.asyncio
async def test_factory_reset_removes_datapoint_grants(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    datapoint = await registry.create(DataPointCreate(name="Reset"))
    await _insert_grant(db, node_id=str(datapoint.id), role="resident")
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)

    icons_dir = MagicMock()
    icons_dir.glob.return_value = []
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as logic_manager,
        patch("obs.api.v1.icons._icons_dir", return_value=icons_dir),
    ):
        logic_manager.return_value.reload = AsyncMock()
        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.datapoints_deleted == 1
    assert not any(error.startswith("DataPoints reset failed") for error in result.errors)
    assert registry.count() == 0
    assert not [row for row in await _grant_rows(db) if row["node_type"] == "datapoint"]


@pytest.mark.asyncio
async def test_config_import_rejects_orphan_datapoint_grant_and_keeps_valid_grants(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_principals(db)
    registry = _registry(db)
    datapoint = await registry.create(DataPointCreate(name="Imported target"))
    await _insert_grant(db, node_type="hierarchy", node_id="room", role="operator", effect="deny")
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)
    body = config_api.ConfigExport(
        obs_version="5",
        exported_at=datetime.now(UTC).isoformat(),
        datapoints=[],
        bindings=[],
        authz_grants=[
            config_api.ExportedAuthzGrant(
                principal_type="user",
                principal_id="alice",
                node_type="datapoint",
                node_id=str(datapoint.id),
                role="resident",
            ),
            config_api.ExportedAuthzGrant(
                principal_type="api_key",
                principal_id=API_KEY_ID,
                node_type="datapoint",
                node_id=str(uuid.uuid4()),
                role="operator",
                effect="deny",
            ),
        ],
    )

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        result = await config_api.import_config(body=body, _user="admin", db=db)

    assert result.authz_grants_upserted == 1
    assert len(result.errors) == 1
    assert "Unknown datapoint grant targets" in result.errors[0]
    rows = await _grant_rows(db)
    assert {(row["node_type"], row["node_id"], row["effect"]) for row in rows} == {
        ("datapoint", str(datapoint.id), "allow"),
        ("hierarchy", "room", "deny"),
    }
