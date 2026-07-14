from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import visu as visu_api
from obs.db.database import Database
from obs.models.visu import PageConfig, WidgetInstance


NOW = "2026-06-10T00:00:00+00:00"
ALLOWED_DP_ID = uuid.UUID("00000000-0000-0000-0000-000000006181")
BLOCKED_DP_ID = uuid.UUID("00000000-0000-0000-0000-000000006182")


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _principal(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


def _request() -> MagicMock:
    request = MagicMock()
    request.headers.get.return_value = None
    return request


def _page_config(dp_id: uuid.UUID) -> PageConfig:
    return PageConfig(
        widgets=[
            WidgetInstance(
                id="widget-1",
                name="Widget",
                type="value",
                datapoint_id=str(dp_id),
                x=0,
                y=0,
                w=2,
                h=1,
                config={},
            )
        ]
    )


async def _insert_tree(db: Database) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at)
        VALUES ('tree', 'tree', '', ?, ?)
        """,
        (NOW, NOW),
    )


async def _insert_hierarchy_node(db: Database, node_id: str) -> None:
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
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, 'Visu AuthZ DP', 'FLOAT', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (str(dp_id), f"obs/test/{dp_id}", NOW, NOW),
    )


async def _link_datapoint(db: Database, dp_id: uuid.UUID, node_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"link-{node_id}-{dp_id}", node_id, str(dp_id), NOW),
    )


async def _insert_grant(db: Database, *, node_id: str, role: str = "guest", principal_id: str = "alice") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', ?, 'hierarchy', ?, ?, 'allow')
        """,
        (principal_id, node_id, role),
    )


async def _insert_user(db: Database, username: str = "alice") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, is_admin, created_at)
        VALUES (?, ?, 'hash', 0, ?)
        """,
        (str(uuid.uuid4()), username, NOW),
    )


async def _insert_visu_page(
    db: Database,
    page_id: str,
    *,
    access: str,
    config: PageConfig,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES (?, NULL, ?, 'PAGE', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (page_id, page_id, access, config.model_dump_json(), NOW, NOW),
    )


async def _seed_scope(db: Database) -> None:
    await _insert_tree(db)
    await _insert_hierarchy_node(db, "allowed")
    await _insert_hierarchy_node(db, "blocked")
    await _insert_datapoint(db, ALLOWED_DP_ID)
    await _insert_datapoint(db, BLOCKED_DP_ID)
    await _link_datapoint(db, ALLOWED_DP_ID, "allowed")
    await _link_datapoint(db, BLOCKED_DP_ID, "blocked")


@pytest.mark.asyncio
async def test_get_page_user_assignment_still_requires_hierarchy_read_grant(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_page(db, "blocked-page", access="user", config=_page_config(BLOCKED_DP_ID))
    await db.execute_and_commit(
        "INSERT INTO visu_node_users (node_id, username) VALUES ('blocked-page', 'alice')",
    )

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_page("blocked-page", _request(), db=db, user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_page_with_hierarchy_read_grant_allows_user_page(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed")
    await _insert_visu_page(db, "allowed-page", access="user", config=_page_config(ALLOWED_DP_ID))
    await db.execute_and_commit(
        "INSERT INTO visu_node_users (node_id, username) VALUES ('allowed-page', 'alice')",
    )

    result = await visu_api.get_page("allowed-page", _request(), db=db, user=_principal())

    assert result.widgets[0].datapoint_id == str(ALLOWED_DP_ID)


@pytest.mark.asyncio
async def test_get_widget_ref_user_page_requires_hierarchy_read_grant(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed")
    await _insert_visu_page(db, "blocked-ref-page", access="user", config=_page_config(BLOCKED_DP_ID))
    await db.execute_and_commit(
        "INSERT INTO visu_node_users (node_id, username) VALUES ('blocked-ref-page', 'alice')",
    )

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_widget_ref("blocked-ref-page", _request(), db=db, user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_public_page_read_remains_compatible_without_hierarchy_grant(db: Database):
    await _seed_scope(db)
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(BLOCKED_DP_ID))

    result = await visu_api.get_page("public-page", _request(), db=db, user=_principal())

    assert result.widgets[0].datapoint_id == str(BLOCKED_DP_ID)


@pytest.mark.asyncio
async def test_save_page_requires_hierarchy_write_grant(db: Database):
    await _seed_scope(db)
    await _insert_grant(db, node_id="allowed", role="guest")
    await _insert_visu_page(db, "write-page", access="public", config=_page_config(ALLOWED_DP_ID))

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page("write-page", _page_config(ALLOWED_DP_ID), db=db, _user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_save_page_with_hierarchy_write_grant_allows_non_admin(db: Database):
    await _seed_scope(db)
    await _insert_grant(db, node_id="allowed", role="resident")
    await _insert_visu_page(db, "write-page", access="public", config=_page_config(ALLOWED_DP_ID))

    await visu_api.save_page("write-page", _page_config(ALLOWED_DP_ID), db=db, _user=_principal())

    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = 'write-page'")
    assert str(ALLOWED_DP_ID) in row["page_config"]


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["readonly", "protected"])
async def test_save_page_blocks_non_admin_on_readonly_and_protected_pages(db: Database, access: str):
    await _seed_scope(db)
    await _insert_grant(db, node_id="allowed", role="resident")
    await _insert_visu_page(db, f"{access}-write-page", access=access, config=_page_config(ALLOWED_DP_ID))

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page(f"{access}-write-page", _page_config(ALLOWED_DP_ID), db=db, _user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_save_page_user_page_requires_user_assignment(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed", role="resident")
    await _insert_visu_page(db, "user-write-page", access="user", config=_page_config(ALLOWED_DP_ID))

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page("user-write-page", _page_config(ALLOWED_DP_ID), db=db, _user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_save_page_without_hierarchy_target_denies_non_admin(db: Database):
    await _insert_visu_page(db, "empty-page", access="public", config=PageConfig())

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page("empty-page", PageConfig(), db=db, _user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_public_page_read_without_auth_remains_compatible(db: Database):
    await _seed_scope(db)
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(BLOCKED_DP_ID))

    result = await visu_api.get_page("public-page", _request(), db=db, user=None)

    assert result.widgets[0].datapoint_id == str(BLOCKED_DP_ID)
