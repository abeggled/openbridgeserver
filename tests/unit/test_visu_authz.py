from __future__ import annotations

import json
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
async def test_import_page_under_user_access_rolls_back_prior_inserted_nodes(db: Database):
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
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Imported Folder'") is None
    assert await db.fetchone("SELECT id FROM visu_nodes WHERE name = 'Imported Page'") is None


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
    await _insert_user(db, "alice")
    await _insert_user(db, "bob")
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
