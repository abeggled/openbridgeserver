from __future__ import annotations

import json
import sqlite3
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal
from obs.api.v1 import config as config_api
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


async def _insert_user(db: Database, username: str = "alice", *, is_admin: bool = False) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, is_admin, created_at)
        VALUES (?, ?, 'hash', ?, ?)
        """,
        (str(uuid.uuid4()), username, int(is_admin), NOW),
    )


async def _assign_visu_user(db: Database, *, node_id: str, username: str = "alice") -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, 'visu_page', ?, 'guest', 'allow')""",
        (username, node_id),
    )


async def _deny_visu_user(db: Database, *, node_id: str, username: str = "alice") -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', ?, 'visu_page', ?, 'guest', 'deny')""",
        (username, node_id),
    )


async def _insert_visu_grant(
    db: Database,
    *,
    node_id: str,
    role: str = "operator",
    effect: str = "allow",
    principal_id: str = "alice",
    principal_type: str = "user",
) -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES (?, ?, 'visu_page', ?, ?, ?)""",
        (principal_type, principal_id, node_id, role, effect),
    )


async def _insert_visu_page(
    db: Database,
    page_id: str,
    *,
    access: str | None,
    config: PageConfig,
    parent_id: str | None = None,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES (?, ?, ?, 'PAGE', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (page_id, parent_id, page_id, access, config.model_dump_json(), NOW, NOW),
    )
    if access is not None:
        await db.execute_and_commit(
            "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)",
            (page_id, access),
        )


async def _insert_visu_location(
    db: Database,
    node_id: str,
    *,
    access: str,
    parent_id: str | None = None,
) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES (?, ?, ?, 'LOCATION', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (node_id, parent_id, node_id, access, PageConfig().model_dump_json(), NOW, NOW),
    )
    if access is not None:
        await db.execute_and_commit(
            "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)",
            (node_id, access),
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
    await _assign_visu_user(db, node_id="blocked-page")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_page("blocked-page", _request(), db=db, user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_page_with_hierarchy_read_grant_allows_user_page(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed")
    await _insert_visu_page(db, "allowed-page", access="user", config=_page_config(ALLOWED_DP_ID))
    await _assign_visu_user(db, node_id="allowed-page")

    result = await visu_api.get_page("allowed-page", _request(), db=db, user=_principal())

    assert result.widgets[0].datapoint_id == str(ALLOWED_DP_ID)


@pytest.mark.asyncio
async def test_get_widget_ref_user_page_requires_hierarchy_read_grant(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed")
    await _insert_visu_page(db, "blocked-ref-page", access="user", config=_page_config(BLOCKED_DP_ID))
    await _assign_visu_user(db, node_id="blocked-ref-page")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_widget_ref("blocked-ref-page", _request(), db=db, user=_principal())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_public_page_read_remains_compatible_without_hierarchy_grant(db: Database):
    await _seed_scope(db)
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(BLOCKED_DP_ID))

    result = await visu_api.get_page("public-page", _request(), db=db, user=_principal())

    assert result.widgets[0].datapoint_id == str(BLOCKED_DP_ID)


def test_save_page_route_requires_authenticated_principal_dependency():
    route = next(route for route in visu_api.router.routes if getattr(route, "path", "") == "/pages/{node_id}" and "PUT" in route.methods)

    assert any(dependency.call is visu_api.get_current_principal for dependency in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_save_user_page_validates_target_users_can_read_referenced_datapoints(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed", role="guest")
    await _insert_visu_page(db, "target-page", access="user", config=_page_config(ALLOWED_DP_ID))
    await _assign_visu_user(db, node_id="target-page")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page("target-page", _page_config(BLOCKED_DP_ID), request=None, db=db)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "visu_target_audience_datapoints_denied"


@pytest.mark.asyncio
async def test_save_user_page_allows_datapoints_readable_by_target_users(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed", role="guest")
    await _insert_visu_page(db, "target-page", access="user", config=_page_config(ALLOWED_DP_ID))
    await _assign_visu_user(db, node_id="target-page")

    await visu_api.save_page("target-page", _page_config(ALLOWED_DP_ID), request=None, db=db)

    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = 'target-page'")
    assert str(ALLOWED_DP_ID) in row["page_config"]


@pytest.mark.asyncio
async def test_api_key_page_capability_preserves_access_boundary_and_audits_use(db: Database):
    await _seed_scope(db)
    key_id = "00000000-0000-0000-0000-000000000989"
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'visu-hash', 'admin', ?)",
        (key_id, NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO api_key_capabilities (key_id, capability) VALUES (?, 'visu.page_config.write')",
        (key_id,),
    )
    principal = Principal(subject=f"api_key:{key_id}", type="api_key", is_admin=False, owner="admin")
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(ALLOWED_DP_ID))
    await _insert_visu_page(db, "readonly-page", access="readonly", config=_page_config(ALLOWED_DP_ID))
    await _insert_visu_grant(db, node_id="public-page", principal_id=key_id, principal_type="api_key")
    await _insert_visu_grant(db, node_id="readonly-page", principal_id=key_id, principal_type="api_key")
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('api_key', ?, 'hierarchy', 'allowed', 'operator', 'allow'),
                  ('api_key', ?, 'hierarchy', 'blocked', 'operator', 'allow')""",
        (key_id, key_id),
    )

    await visu_api.save_page("public-page", _page_config(BLOCKED_DP_ID), request=None, db=db, _user=principal)
    with pytest.raises(HTTPException) as exc_info:
        await visu_api.save_page("readonly-page", _page_config(BLOCKED_DP_ID), request=None, db=db, _user=principal)

    assert exc_info.value.status_code == 403
    audit = await db.fetchall("SELECT resource_id, details_json FROM audit_log_entries WHERE action='api_key.capability.use' ORDER BY id")
    assert [(row["resource_id"], json.loads(row["details_json"])["result"]) for row in audit] == [
        ("public-page", "allowed"),
        ("readonly-page", "denied"),
    ]


@pytest.mark.asyncio
async def test_existing_visu_update_requires_generate_grant_after_discovery(db: Database):
    await _insert_visu_page(db, "public-page", access="public", config=PageConfig())
    principal = _principal()

    with pytest.raises(HTTPException) as denied:
        await visu_api.update_node(
            "public-page",
            visu_api.VisuNodeUpdate(name="Denied"),
            db=db,
            _user=principal,
        )
    assert denied.value.status_code == 403

    await _insert_visu_grant(db, node_id="public-page")
    updated = await visu_api.update_node(
        "public-page",
        visu_api.VisuNodeUpdate(name="Allowed"),
        db=db,
        _user=principal,
    )
    assert updated.name == "Allowed"


@pytest.mark.asyncio
async def test_existing_visu_mutation_conceals_undiscoverable_user_page(db: Database):
    await _insert_user(db)
    await _insert_visu_page(db, "hidden-page", access="user", config=PageConfig())

    with pytest.raises(HTTPException) as exc:
        await visu_api.update_node(
            "hidden-page",
            visu_api.VisuNodeUpdate(name="Leak"),
            db=db,
            _user=_principal(),
        )
    assert exc.value.status_code == 404


def test_visu_creation_routes_use_authenticated_principal_dependency():
    expected = {
        ("/nodes", "POST"),
        ("/nodes/import", "POST"),
        ("/nodes/{node_id}/copy", "POST"),
    }
    routes = {(route.path, method): route for route in visu_api.router.routes for method in route.methods if (route.path, method) in expected}

    assert set(routes) == expected
    for route in routes.values():
        assert any(dependency.call is visu_api.get_current_principal for dependency in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_delegated_create_requires_generate_on_existing_parent_and_records_creator(db: Database):
    await _insert_visu_location(db, "scope-root", access="public")
    await _insert_visu_location(db, "parent", access="public", parent_id="scope-root")
    await _insert_visu_grant(db, node_id="scope-root")

    page = await visu_api.create_node(
        visu_api.VisuNodeCreate(parent_id="parent", name="Delegated page"),
        db=db,
        _user=_principal(),
    )
    location = await visu_api.create_node(
        visu_api.VisuNodeCreate(parent_id="parent", name="Delegated folder", type="LOCATION"),
        db=db,
        _user=_principal(),
    )

    rows = await db.fetchall(
        "SELECT name, parent_id, created_by FROM visu_nodes WHERE id IN (?, ?) ORDER BY name",
        (page.id, location.id),
    )
    assert [(row["name"], row["parent_id"], row["created_by"]) for row in rows] == [
        ("Delegated folder", "parent", None),
        ("Delegated page", "parent", "alice"),
    ]


@pytest.mark.asyncio
async def test_delegated_creation_denies_root_api_keys_and_out_of_scope_parents(db: Database):
    await _insert_visu_location(db, "public-parent", access="public")
    await _insert_visu_location(db, "hidden-parent", access="user")
    key = Principal(subject="api_key:key-1", type="api_key", is_admin=False, owner="admin")
    await _insert_visu_grant(db, node_id="public-parent", principal_id="key-1", principal_type="api_key")

    with pytest.raises(HTTPException) as root_denied:
        await visu_api.create_node(visu_api.VisuNodeCreate(name="Root escape"), db=db, _user=_principal())
    assert root_denied.value.status_code == 403

    with pytest.raises(HTTPException) as key_denied:
        await visu_api.create_node(
            visu_api.VisuNodeCreate(parent_id="public-parent", name="Key escape"),
            db=db,
            _user=key,
        )
    assert key_denied.value.status_code == 403

    with pytest.raises(HTTPException) as missing_grant:
        await visu_api.create_node(
            visu_api.VisuNodeCreate(parent_id="public-parent", name="Out of scope"),
            db=db,
            _user=_principal(),
        )
    assert missing_grant.value.status_code == 403

    with pytest.raises(HTTPException) as hidden_parent:
        await visu_api.create_node(
            visu_api.VisuNodeCreate(parent_id="hidden-parent", name="Hidden scope"),
            db=db,
            _user=_principal(),
        )
    assert hidden_parent.value.status_code == 404
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name LIKE '%escape' OR name LIKE '%scope'") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["copy", "import"])
async def test_copy_and_import_conceal_undiscoverable_target_parent(db: Database, operation: str):
    await _insert_visu_location(db, "hidden-parent", access="user")
    await _insert_visu_page(db, "source-page", access="public", config=PageConfig())

    with pytest.raises(HTTPException) as denied:
        if operation == "copy":
            await visu_api.copy_node(
                "source-page",
                visu_api.CopyNodeRequest(target_parent_id="hidden-parent", new_name="Hidden copy"),
                db=db,
                _user=_principal(),
            )
        else:
            await visu_api.import_nodes(
                visu_api.VisuImportRequest(
                    obs_export="visu_subtree",
                    version=1,
                    target_parent_id="hidden-parent",
                    nodes=[{"id": "old", "name": "Hidden import", "type": "PAGE"}],
                ),
                db=db,
                _user=_principal(),
            )

    assert denied.value.status_code == 404
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name IN ('Hidden copy', 'Hidden import')") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "copy", "import"])
async def test_api_keys_cannot_create_visu_nodes_even_with_parent_generate_grant(db: Database, operation: str):
    await _insert_visu_location(db, "parent", access="public")
    await _insert_visu_page(db, "source-page", access="public", config=PageConfig())
    await _insert_visu_grant(db, node_id="parent", principal_id="key-1", principal_type="api_key")
    principal = Principal(subject="api_key:key-1", type="api_key", is_admin=False, owner="admin")

    with pytest.raises(HTTPException) as denied:
        if operation == "create":
            await visu_api.create_node(
                visu_api.VisuNodeCreate(parent_id="parent", name="Key create"),
                db=db,
                _user=principal,
            )
        elif operation == "copy":
            await visu_api.copy_node(
                "source-page",
                visu_api.CopyNodeRequest(target_parent_id="parent", new_name="Key copy"),
                db=db,
                _user=principal,
            )
        else:
            await visu_api.import_nodes(
                visu_api.VisuImportRequest(
                    obs_export="visu_subtree",
                    version=1,
                    target_parent_id="parent",
                    nodes=[{"id": "old", "name": "Key import", "type": "PAGE"}],
                ),
                db=db,
                _user=principal,
            )

    assert denied.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name IN ('Key create', 'Key copy', 'Key import')") is None


@pytest.mark.asyncio
async def test_delegated_copy_requires_parent_and_datapoint_generate_scope(db: Database):
    await _seed_scope(db)
    await _insert_visu_location(db, "target-parent", access="public")
    await _insert_visu_page(db, "source-page", access="public", config=_page_config(BLOCKED_DP_ID))
    await _insert_visu_grant(db, node_id="target-parent")

    body = visu_api.CopyNodeRequest(target_parent_id="target-parent", new_name="Delegated copy")
    with pytest.raises(HTTPException) as denied:
        await visu_api.copy_node("source-page", body, db=db, _user=_principal())
    assert denied.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name='Delegated copy'") is None

    await _insert_grant(db, node_id="blocked", role="operator")
    copied = await visu_api.copy_node("source-page", body, db=db, _user=_principal())

    assert copied.parent_id == "target-parent"
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id=?", (copied.id,)))["created_by"] == "alice"


@pytest.mark.asyncio
async def test_delegated_import_requires_parent_and_datapoint_generate_scope(db: Database):
    await _seed_scope(db)
    await _insert_visu_location(db, "target-parent", access="public")
    await _insert_visu_grant(db, node_id="target-parent")
    body = visu_api.VisuImportRequest(
        obs_export="visu_subtree",
        version=1,
        target_parent_id="target-parent",
        nodes=[
            {
                "id": "imported-page",
                "name": "Delegated import",
                "type": "PAGE",
                "access": "public",
                "page_config": _page_config(ALLOWED_DP_ID).model_dump(mode="json"),
            }
        ],
    )

    with pytest.raises(HTTPException) as denied:
        await visu_api.import_nodes(body, db=db, _user=_principal())
    assert denied.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name='Delegated import'") is None

    await _insert_grant(db, node_id="allowed", role="operator")
    imported = await visu_api.import_nodes(body, db=db, _user=_principal())

    assert imported.parent_id == "target-parent"
    assert (await db.fetchone("SELECT created_by FROM visu_nodes WHERE id=?", (imported.id,)))["created_by"] == "alice"


@pytest.mark.asyncio
async def test_delete_subtree_applies_generate_policy_to_every_node_and_deny_wins(db: Database):
    await _insert_visu_location(db, "root", access="public")
    await _insert_visu_page(db, "child", access=None, config=PageConfig(), parent_id="root")
    await _insert_visu_grant(db, node_id="root")
    await _insert_visu_grant(db, node_id="child", role="guest", effect="deny")

    with pytest.raises(HTTPException) as exc:
        await visu_api.delete_node("root", db=db, _user=_principal())

    assert exc.value.status_code == 403
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE id='root'") is not None
    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE id='child'") is not None


@pytest.mark.asyncio
async def test_save_page_requires_generate_scope_for_page_and_every_proposed_datapoint(db: Database):
    await _seed_scope(db)
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(BLOCKED_DP_ID))
    await _insert_visu_grant(db, node_id="public-page")
    await _insert_grant(db, node_id="allowed", role="operator")
    principal = _principal()

    with pytest.raises(HTTPException) as denied:
        await visu_api.save_page(
            "public-page",
            _page_config(ALLOWED_DP_ID),
            request=None,
            db=db,
            _user=principal,
        )
    assert denied.value.status_code == 403
    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id='public-page'")
    assert str(BLOCKED_DP_ID) in row["page_config"]

    await _insert_grant(db, node_id="blocked", role="operator")
    await visu_api.save_page(
        "public-page",
        _page_config(ALLOWED_DP_ID),
        request=None,
        db=db,
        _user=principal,
    )
    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id='public-page'")
    assert str(ALLOWED_DP_ID) in row["page_config"]


@pytest.mark.asyncio
async def test_save_user_page_preserves_promoted_assignee_admin_status(db: Database):
    await _seed_scope(db)
    await _insert_user(db, is_admin=True)
    await _insert_visu_page(db, "target-page", access="user", config=_page_config(ALLOWED_DP_ID))
    await _assign_visu_user(db, node_id="target-page")

    await visu_api.save_page("target-page", _page_config(BLOCKED_DP_ID), request=None, db=db)

    row = await db.fetchone("SELECT page_config FROM visu_nodes WHERE id = 'target-page'")
    assert str(BLOCKED_DP_ID) in row["page_config"]


@pytest.mark.asyncio
async def test_set_node_users_validates_existing_page_datapoints_for_new_target_group(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_page(db, "target-page", access="user", config=_page_config(BLOCKED_DP_ID))

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.set_node_users(
            "target-page",
            visu_api.VisuNodeUsersUpdate(usernames=["alice"]),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert await db.fetchall("SELECT principal_id FROM authz_node_roles WHERE node_type='visu_page' AND node_id='target-page'") == []


@pytest.mark.asyncio
async def test_set_node_users_allows_target_group_with_datapoint_read_access(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_grant(db, node_id="allowed", role="guest")
    await _insert_visu_page(db, "target-page", access="user", config=_page_config(ALLOWED_DP_ID))

    await visu_api.set_node_users(
        "target-page",
        visu_api.VisuNodeUsersUpdate(usernames=["alice"]),
        db=db,
    )

    rows = await db.fetchall("SELECT principal_id FROM authz_node_roles WHERE node_type='visu_page' AND node_id='target-page'")
    assert [row["principal_id"] for row in rows] == ["alice"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "effect"),
    [("operator", "allow"), ("owner", "allow"), ("guest", "deny")],
)
async def test_set_node_users_preserves_advanced_grant_or_deny(db: Database, role: str, effect: str):
    await _insert_user(db)
    await _insert_visu_page(db, "target-page", access="user", config=PageConfig())
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'visu_page', 'target-page', ?, ?)""",
        (role, effect),
    )

    await visu_api.set_node_users(
        "target-page",
        visu_api.VisuNodeUsersUpdate(usernames=["alice"]),
        db=db,
    )

    row = await db.fetchone(
        """SELECT role, effect FROM authz_node_roles
           WHERE principal_type='user' AND principal_id='alice'
             AND node_type='visu_page' AND node_id='target-page'""",
    )
    assert (row["role"], row["effect"]) == (role, effect)
    assert await visu_api.get_node_users("target-page", db=db) == []


@pytest.mark.asyncio
async def test_update_inherited_protected_child_pin_fails_closed(db: Database):
    await _insert_visu_location(db, "protected-folder", access="protected")
    await _insert_visu_page(db, "child-page", access=None, config=PageConfig(), parent_id="protected-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.update_node(
            "child-page",
            visu_api.VisuNodeUpdate(access_pin="1234"),
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert await db.fetchone("SELECT 1 FROM authz_visu_page_credentials WHERE node_id='child-page'") is None


@pytest.mark.asyncio
async def test_set_node_users_validates_inherited_user_access_pages(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "secure-folder", access="user")
    await _insert_visu_page(db, "child-page", access=None, config=_page_config(BLOCKED_DP_ID), parent_id="secure-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.set_node_users(
            "secure-folder",
            visu_api.VisuNodeUsersUpdate(usernames=["alice"]),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert await db.fetchall("SELECT principal_id FROM authz_node_roles WHERE node_type='visu_page' AND node_id='secure-folder'") == []


@pytest.mark.asyncio
async def test_update_node_to_user_access_validates_existing_target_group(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "public-folder", access="public")
    await _insert_visu_page(db, "child-page", access=None, config=_page_config(BLOCKED_DP_ID), parent_id="public-folder")
    await _assign_visu_user(db, node_id="public-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.update_node(
            "public-folder",
            visu_api.VisuNodeUpdate(access="user"),
            db=db,
        )

    assert exc_info.value.status_code == 403
    row = await db.fetchone("SELECT access FROM visu_nodes WHERE id = 'public-folder'")
    assert row["access"] == "public"


@pytest.mark.asyncio
async def test_move_node_under_user_access_validates_inherited_target_group(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "secure-folder", access="user")
    await _insert_visu_location(db, "public-folder", access=None)
    await _insert_visu_page(db, "child-page", access=None, config=_page_config(BLOCKED_DP_ID), parent_id="public-folder")
    await _assign_visu_user(db, node_id="secure-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.move_node(
            "public-folder",
            visu_api.MoveNodeRequest(new_parent_id="secure-folder", order=0),
            db=db,
        )

    assert exc_info.value.status_code == 403
    row = await db.fetchone("SELECT parent_id FROM visu_nodes WHERE id = 'public-folder'")
    assert row["parent_id"] is None


@pytest.mark.asyncio
async def test_copy_page_under_user_access_validates_inherited_target_group(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "secure-folder", access="user")
    await _insert_visu_page(db, "source-page", access=None, config=_page_config(BLOCKED_DP_ID))
    await _assign_visu_user(db, node_id="secure-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.copy_node(
            "source-page",
            visu_api.CopyNodeRequest(target_parent_id="secure-folder", new_name="Copied Page"),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Copied Page'") is None


@pytest.mark.asyncio
async def test_import_page_under_user_access_validates_inherited_target_group(db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "secure-folder", access="user")
    await _assign_visu_user(db, node_id="secure-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.import_nodes(
            visu_api.VisuImportRequest(
                obs_export="visu_subtree",
                version=1,
                target_parent_id="secure-folder",
                nodes=[
                    {
                        "id": "imported-page",
                        "parent_id": None,
                        "name": "Imported Page",
                        "type": "PAGE",
                        "node_order": 0,
                        "icon": None,
                        "access": None,
                        "page_config": _page_config(BLOCKED_DP_ID).model_dump(mode="json"),
                    }
                ],
            ),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Imported Page'") is None


@pytest.mark.asyncio
async def test_import_page_under_user_access_validates_before_any_insert(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _seed_scope(db)
    await _insert_user(db)
    await _insert_visu_location(db, "secure-folder", access="user")
    await _assign_visu_user(db, node_id="secure-folder")
    original_check = visu_api._check_user_page_target_datapoint_policy
    validation_observations: list[bool] = []

    async def check_before_insert(*args, **kwargs):
        validation_observations.append(await db.fetchone("SELECT 1 FROM visu_nodes WHERE name LIKE 'Imported %'") is None)
        return await original_check(*args, **kwargs)

    monkeypatch.setattr(visu_api, "_check_user_page_target_datapoint_policy", check_before_insert)

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.import_nodes(
            visu_api.VisuImportRequest(
                obs_export="visu_subtree",
                version=1,
                target_parent_id="secure-folder",
                nodes=[
                    {
                        "id": "imported-folder",
                        "parent_id": None,
                        "name": "Imported Folder",
                        "type": "LOCATION",
                        "node_order": 0,
                        "icon": None,
                        "access": None,
                        "page_config": None,
                    },
                    {
                        "id": "imported-page",
                        "parent_id": "imported-folder",
                        "name": "Imported Page",
                        "type": "PAGE",
                        "node_order": 1,
                        "icon": None,
                        "access": None,
                        "page_config": _page_config(BLOCKED_DP_ID).model_dump(mode="json"),
                    },
                ],
            ),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert validation_observations == [True]
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Imported Folder'") is None
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Imported Page'") is None


@pytest.mark.asyncio
async def test_create_rolls_back_node_policy_and_credential_on_failure(db: Database):
    await db.execute_and_commit(
        """CREATE TRIGGER fail_visu_credential
           BEFORE INSERT ON authz_visu_page_credentials
           BEGIN SELECT RAISE(ABORT, 'credential write failed'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="credential write failed"):
        await visu_api.create_node(
            visu_api.VisuNodeCreate(name="Atomic create", access="protected", access_pin="1234"),
            db=db,
            _user="admin",
        )

    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name='Atomic create'") is None
    assert await db.fetchone("SELECT 1 FROM authz_visu_page_policies") is None
    assert await db.fetchone("SELECT 1 FROM authz_visu_page_credentials") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["copy", "import"])
async def test_copy_and_import_roll_back_node_and_policy_on_failure(db: Database, operation: str):
    await _insert_visu_page(db, "source-page", access="public", config=PageConfig())
    await db.execute_and_commit(
        """CREATE TRIGGER fail_visu_policy
           BEFORE INSERT ON authz_visu_page_policies
           WHEN NEW.node_id != 'source-page'
           BEGIN SELECT RAISE(ABORT, 'policy write failed'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="policy write failed"):
        if operation == "copy":
            await visu_api.copy_node(
                "source-page",
                visu_api.CopyNodeRequest(new_name="Atomic copy"),
                db=db,
                _user="admin",
            )
        else:
            await visu_api.import_nodes(
                visu_api.VisuImportRequest(
                    obs_export="visu_subtree",
                    version=1,
                    nodes=[{"id": "old", "name": "Atomic import", "type": "PAGE", "access": "public"}],
                ),
                db=db,
                _user="admin",
            )

    assert await db.fetchone("SELECT 1 FROM visu_nodes WHERE name IN ('Atomic copy', 'Atomic import')") is None
    policies = await db.fetchall("SELECT node_id FROM authz_visu_page_policies ORDER BY node_id")
    assert [row["node_id"] for row in policies] == ["source-page"]


@pytest.mark.asyncio
async def test_public_page_read_without_auth_remains_compatible(db: Database):
    await _seed_scope(db)
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(BLOCKED_DP_ID))

    result = await visu_api.get_page("public-page", _request(), db=db, user=None)

    assert result.widgets[0].datapoint_id == str(BLOCKED_DP_ID)


@pytest.mark.asyncio
async def test_discovery_redacts_page_configs_and_hides_user_scoped_nodes(db: Database):
    await _insert_user(db, "alice")
    await _insert_user(db, "bob")
    await _insert_visu_page(db, "public-page", access="public", config=_page_config(ALLOWED_DP_ID))
    await _insert_visu_page(db, "protected-page", access="protected", config=_page_config(BLOCKED_DP_ID))
    await _insert_visu_page(db, "user-page", access="user", config=_page_config(BLOCKED_DP_ID))
    await _assign_visu_user(db, node_id="user-page", username="alice")

    anonymous = await visu_api.get_tree(db=db, user=None)
    assert [node.id for node in anonymous] == ["public-page", "protected-page"]
    assert all(not hasattr(node, "page_config") for node in anonymous)

    out_of_scope = await visu_api.get_tree(db=db, user=_principal("bob"))
    assert [node.id for node in out_of_scope] == ["public-page", "protected-page"]

    assigned = await visu_api.get_tree(db=db, user=_principal("alice"))
    assert [node.id for node in assigned] == ["public-page", "protected-page", "user-page"]

    admin = await visu_api.get_tree(db=db, user=_principal("admin", is_admin=True))
    assert [node.id for node in admin] == ["public-page", "protected-page", "user-page"]
    assert all(not hasattr(node, "page_config") for node in admin)

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_node("user-page", db=db, user=_principal("bob"))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_discovery_honors_explicit_visu_deny_without_leaking_breadcrumbs_or_children(db: Database):
    await _insert_user(db, "alice")
    await _insert_visu_location(db, "assigned-root", access="user")
    await _insert_visu_page(db, "denied-child", access=None, config=_page_config(BLOCKED_DP_ID), parent_id="assigned-root")
    await _assign_visu_user(db, node_id="assigned-root", username="alice")
    await _deny_visu_user(db, node_id="denied-child", username="alice")

    visible = await visu_api.get_tree(db=db, user=_principal("alice"))
    assert [node.id for node in visible] == ["assigned-root"]

    children = await visu_api.get_children("assigned-root", db=db, user=_principal("alice"))
    assert children == []

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.get_breadcrumb("denied-child", db=db, user=_principal("alice"))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_export_omits_hidden_subtrees_and_allows_assigned_user(db: Database):
    await _seed_scope(db)
    await _insert_user(db, "alice")
    await _insert_user(db, "bob")
    await _insert_grant(db, node_id="blocked")
    await _insert_visu_location(db, "root", access="public")
    await _insert_visu_page(db, "public-child", access="public", config=_page_config(ALLOWED_DP_ID), parent_id="root")
    await _insert_visu_page(db, "user-child", access="user", config=_page_config(BLOCKED_DP_ID), parent_id="root")
    await _assign_visu_user(db, node_id="user-child", username="alice")

    bob_response = await visu_api.export_node("root", db=db, _user=_principal("bob"))
    bob_export = json.loads(bob_response.body)
    assert [node["id"] for node in bob_export["nodes"]] == ["root", "public-child"]
    assert str(BLOCKED_DP_ID) not in bob_response.body.decode()

    alice_response = await visu_api.export_node("root", db=db, _user=_principal("alice"))
    alice_export = json.loads(alice_response.body)
    assert [node["id"] for node in alice_export["nodes"]] == ["root", "public-child", "user-child"]
    assert str(BLOCKED_DP_ID) in alice_response.body.decode()


@pytest.mark.asyncio
async def test_export_user_page_requires_datapoint_read_grant(db: Database):
    await _seed_scope(db)
    await _insert_user(db, "alice")
    await _insert_visu_page(db, "user-page", access="user", config=_page_config(BLOCKED_DP_ID))
    await _assign_visu_user(db, node_id="user-page", username="alice")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.export_node("user-page", db=db, _user=_principal("alice"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_export_hides_user_page_from_api_key_with_visu_grant(db: Database):
    key_id = "00000000-0000-0000-0000-000000000995"
    await _insert_visu_location(db, "root", access="public")
    await _insert_visu_page(db, "user-child", access="user", config=_page_config(BLOCKED_DP_ID), parent_id="root")
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'key', 'hash', 'admin', ?)",
        (key_id, NOW),
    )
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('api_key', ?, 'visu_page', 'user-child', 'guest', 'allow')""",
        (key_id,),
    )
    principal = Principal(subject=f"api_key:{key_id}", type="api_key", is_admin=False, owner="admin")

    tree = await visu_api.get_tree(db=db, user=principal)
    children = await visu_api.get_children("root", db=db, user=principal)
    assert [node.id for node in tree] == ["root"]
    assert children == []
    with pytest.raises(HTTPException) as node_error:
        await visu_api.get_node("user-child", db=db, user=principal)
    assert node_error.value.status_code == 404
    with pytest.raises(HTTPException) as breadcrumb_error:
        await visu_api.get_breadcrumb("user-child", db=db, user=principal)
    assert breadcrumb_error.value.status_code == 404

    response = await visu_api.export_node(
        "root",
        db=db,
        _user=principal,
    )

    payload = json.loads(response.body)
    assert [node["id"] for node in payload["nodes"]] == ["root"]
    assert str(BLOCKED_DP_ID) not in response.body.decode()


@pytest.mark.asyncio
async def test_atomic_access_and_target_update_rolls_back_before_mutation(db: Database):
    await _seed_scope(db)
    await _insert_user(db, "alice")
    await _insert_visu_location(db, "public-folder", access="public")
    await _insert_visu_page(db, "child-page", access=None, config=_page_config(BLOCKED_DP_ID), parent_id="public-folder")

    with pytest.raises(HTTPException) as exc_info:
        await visu_api.update_node(
            "public-folder",
            visu_api.VisuNodeUpdate(name="Renamed", access="user", usernames=["alice"]),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "code": "visu_target_audience_datapoints_denied",
        "username": "alice",
        "datapoint_ids": [str(BLOCKED_DP_ID)],
    }
    node = await db.fetchone("SELECT name FROM visu_nodes WHERE id='public-folder'")
    policy = await db.fetchone("SELECT access_mode FROM authz_visu_page_policies WHERE node_id='public-folder'")
    assert node["name"] == "public-folder"
    assert policy["access_mode"] == "public"
    assert await db.fetchall("SELECT * FROM authz_node_roles WHERE node_type='visu_page'") == []


@pytest.mark.asyncio
async def test_atomic_access_and_target_update_commits_policy_metadata_and_grants(db: Database):
    await _seed_scope(db)
    await _insert_user(db, "alice")
    await _insert_grant(db, node_id="allowed")
    await _insert_visu_location(db, "public-folder", access="public")
    await db.execute_and_commit(
        "INSERT INTO authz_visu_page_credentials (node_id, pin_hash) VALUES ('public-folder', 'stale-hash')",
    )
    await _insert_visu_page(db, "child-page", access=None, config=_page_config(ALLOWED_DP_ID), parent_id="public-folder")

    result = await visu_api.update_node(
        "public-folder",
        visu_api.VisuNodeUpdate(name="Assigned folder", access="user", usernames=["alice"]),
        db=db,
    )

    assert result.name == "Assigned folder"
    assert result.access == "user"
    grants = await db.fetchall(
        "SELECT principal_id, role, effect FROM authz_node_roles WHERE node_type='visu_page' AND node_id='public-folder'",
    )
    assert [(row["principal_id"], row["role"], row["effect"]) for row in grants] == [("alice", "guest", "allow")]
    assert await db.fetchone("SELECT 1 FROM authz_visu_page_credentials WHERE node_id='public-folder'") is None


@pytest.mark.asyncio
async def test_delete_subtree_removes_only_its_visu_page_grants(db: Database):
    await _insert_user(db, "alice")
    await _insert_visu_location(db, "root", access="user")
    await _insert_visu_page(db, "child", access=None, config=PageConfig(), parent_id="root")
    await _insert_visu_page(db, "unrelated", access="user", config=PageConfig())
    await _assign_visu_user(db, node_id="root")
    await _assign_visu_user(db, node_id="child")
    await _assign_visu_user(db, node_id="unrelated")

    await visu_api.delete_node("root", db=db)

    assert await db.fetchall("SELECT id FROM visu_nodes WHERE id IN ('root', 'child')") == []
    remaining = await db.fetchall(
        "SELECT node_id FROM authz_node_roles WHERE node_type='visu_page' ORDER BY node_id",
    )
    assert [row["node_id"] for row in remaining] == ["unrelated"]


@pytest.mark.asyncio
async def test_delete_subtree_grant_cleanup_terminates_for_parent_cycle(db: Database):
    await _insert_user(db, "alice")
    await _insert_visu_location(db, "root", access="user")
    await _insert_visu_page(db, "child", access=None, config=PageConfig(), parent_id="root")
    await _assign_visu_user(db, node_id="root")
    await _assign_visu_user(db, node_id="child")
    await db.execute_and_commit("UPDATE visu_nodes SET parent_id='child' WHERE id='root'")

    await visu_api.delete_node("root", db=db)

    assert await db.fetchall("SELECT id FROM visu_nodes WHERE id IN ('root', 'child')") == []
    assert await db.fetchall("SELECT node_id FROM authz_node_roles WHERE node_type='visu_page'") == []


@pytest.mark.asyncio
async def test_factory_reset_removes_visu_page_grants_and_preserves_unrelated_grants(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_user(db, "alice")
    await _insert_visu_location(db, "root", access="user")
    await _insert_visu_page(db, "child", access=None, config=PageConfig(), parent_id="root")
    await _assign_visu_user(db, node_id="root")
    await _assign_visu_user(db, node_id="child")
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'logic_capability', 'http_request', 'resident', 'deny')"""
    )
    monkeypatch.setattr(config_api, "get_registry", lambda: SimpleNamespace(_points={}, _values={}))

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.logic.manager.get_logic_manager") as manager,
        patch("obs.api.v1.icons._icons_dir") as icons_dir,
    ):
        manager.return_value.reload = AsyncMock()
        icons_dir.return_value = MagicMock(glob=MagicMock(return_value=[]))
        result = await config_api.factory_reset(_admin="admin", db=db)

    assert result.errors == []
    assert result.visu_nodes_deleted == 2
    assert await db.fetchone("SELECT 1 FROM visu_nodes") is None
    assert await db.fetchone("SELECT 1 FROM authz_node_roles WHERE node_type='visu_page'") is None
    unrelated = await db.fetchone("SELECT effect FROM authz_node_roles WHERE node_type='logic_capability' AND node_id='http_request'")
    assert unrelated is not None
    assert unrelated["effect"] == "deny"
