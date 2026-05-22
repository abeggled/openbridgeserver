from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def _create_instance(client, auth_headers, adapter_type: str, name: str) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": adapter_type,
            "name": name,
            "config": {},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_dp(client, auth_headers, suffix: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={
            "name": f"binding-migration-{suffix}-{uuid.uuid4().hex[:6]}",
            "data_type": "BOOLEAN",
            "tags": ["integration", "binding-migration"],
            "persist_value": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_binding(client, auth_headers, dp_id: str, instance_id: str, *, enabled: bool = True, config: dict | None = None) -> dict:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/bindings",
        json={
            "adapter_instance_id": instance_id,
            "direction": "SOURCE",
            "config": config or {},
            "enabled": enabled,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _list_instance_bindings(client, auth_headers, instance_id: str) -> list[dict]:
    resp = await client.get(f"/api/v1/adapters/instances/{instance_id}/bindings", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_migrate_bindings_moves_all_bindings_to_target_instance(client, auth_headers):
    source = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"src-{uuid.uuid4().hex[:6]}")
    target = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"dst-{uuid.uuid4().hex[:6]}")

    dp1 = await _create_dp(client, auth_headers, "m1")
    dp2 = await _create_dp(client, auth_headers, "m2")

    b1 = await _create_binding(client, auth_headers, dp1["id"], source["id"], config={"offset_override": 3})
    b2 = await _create_binding(client, auth_headers, dp2["id"], source["id"], enabled=False)

    migrate = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert migrate.status_code == 200, migrate.text
    body = migrate.json()
    assert body["migrated"] == 2
    assert body["skipped"] == 0
    assert body["total_source_bindings"] == 2

    source_bindings = await _list_instance_bindings(client, auth_headers, source["id"])
    target_bindings = await _list_instance_bindings(client, auth_headers, target["id"])

    assert source_bindings == []
    migrated_ids = {b1["id"], b2["id"]}
    assert migrated_ids.issubset({item["binding_id"] for item in target_bindings})


async def test_migrate_bindings_skips_datapoints_already_bound_on_target(client, auth_headers):
    source = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"src-{uuid.uuid4().hex[:6]}")
    target = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"dst-{uuid.uuid4().hex[:6]}")

    dp1 = await _create_dp(client, auth_headers, "s1")
    dp2 = await _create_dp(client, auth_headers, "s2")

    moved_binding = await _create_binding(client, auth_headers, dp1["id"], source["id"])
    skipped_binding = await _create_binding(client, auth_headers, dp2["id"], source["id"])
    existing_target = await _create_binding(client, auth_headers, dp2["id"], target["id"])

    migrate = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert migrate.status_code == 200, migrate.text
    body = migrate.json()
    assert body["migrated"] == 1
    assert body["skipped"] == 1
    assert body["total_source_bindings"] == 2

    source_bindings = await _list_instance_bindings(client, auth_headers, source["id"])
    target_bindings = await _list_instance_bindings(client, auth_headers, target["id"])

    source_ids = {item["binding_id"] for item in source_bindings}
    target_ids = {item["binding_id"] for item in target_bindings}

    assert moved_binding["id"] in target_ids
    assert existing_target["id"] in target_ids
    assert skipped_binding["id"] in source_ids


async def test_migrate_bindings_rejects_same_source_and_target(client, auth_headers):
    source = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"src-{uuid.uuid4().hex[:6]}")

    resp = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": source["id"]},
        headers=auth_headers,
    )

    assert resp.status_code == 422


async def test_migrate_bindings_returns_404_for_missing_source_instance(client, auth_headers):
    target = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"dst-{uuid.uuid4().hex[:6]}")

    resp = await client.post(
        "/api/v1/adapters/instances/00000000-0000-0000-0000-000000000000/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert resp.status_code == 404


async def test_migrate_bindings_rejects_different_adapter_types(client, auth_headers):
    source = await _create_instance(client, auth_headers, "ANWESENHEITSSIMULATION", f"src-{uuid.uuid4().hex[:6]}")
    target = await _create_instance(client, auth_headers, "ZEITSCHALTUHR", f"dst-{uuid.uuid4().hex[:6]}")

    dp1 = await _create_dp(client, auth_headers, "x1")
    await _create_binding(client, auth_headers, dp1["id"], source["id"])

    resp = await client.post(
        f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
        json={"target_instance_id": target["id"]},
        headers=auth_headers,
    )

    assert resp.status_code == 422
