"""Integration tests — DataPoint rename propagation into logic graphs (issue #333).

Verifies that:
  1. Renaming a DataPoint via PATCH /api/v1/datapoints/{id} immediately updates
     datapoint_name in all logic graph nodes that reference it.
  2. Importing a logic graph resolves datapoint_name from the current registry,
     discarding any stale name stored in the export JSON.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (duplicated locally for isolation)
# ---------------------------------------------------------------------------


async def _create_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_graph(client, auth_headers, name: str, nodes: list, edges: list | None = None) -> str:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": name, "description": "", "enabled": True, "flow_data": {"nodes": nodes, "edges": edges or []}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _get_graph(client, auth_headers, graph_id: str) -> dict:
    resp = await client.get(f"/api/v1/logic/graphs/{graph_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _cleanup(client, auth_headers, graph_ids: list[str] | None = None, dp_ids: list[str] | None = None) -> None:
    for gid in graph_ids or []:
        await client.delete(f"/api/v1/logic/graphs/{gid}", headers=auth_headers)
    for did in dp_ids or []:
        await client.delete(f"/api/v1/datapoints/{did}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Test 1 — Rename propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_datapoint_updates_logic_graph_nodes(client, auth_headers):
    """PATCH datapoint name → datapoint_name in all referencing logic nodes updated."""
    ts = time.time()
    dp = await _create_dp(client, auth_headers, f"IT-Rename-Before-{ts}")
    dp_id = dp["id"]
    graph_id = None

    try:
        graph_id = await _create_graph(
            client,
            auth_headers,
            f"IT-Rename-Graph-{ts}",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": dp_id, "datapoint_name": f"IT-Rename-Before-{ts}"},
                },
                {
                    "id": "w1",
                    "type": "datapoint_write",
                    "position": {"x": 200, "y": 0},
                    "data": {"datapoint_id": dp_id, "datapoint_name": f"IT-Rename-Before-{ts}"},
                },
            ],
        )

        new_name = f"IT-Rename-After-{ts}"
        rename_resp = await client.patch(
            f"/api/v1/datapoints/{dp_id}",
            json={"name": new_name},
            headers=auth_headers,
        )
        assert rename_resp.status_code == 200, rename_resp.text

        graph = await _get_graph(client, auth_headers, graph_id)
        nodes = graph["flow_data"]["nodes"]
        names = [n["data"].get("datapoint_name") for n in nodes]
        assert all(n == new_name for n in names), f"Expected all nodes to have '{new_name}', got {names}"

    finally:
        await _cleanup(client, auth_headers, graph_ids=[graph_id] if graph_id else [], dp_ids=[dp_id])


# ---------------------------------------------------------------------------
# Test 2 — Import resolves name from current registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_resolves_datapoint_name_from_registry(client, auth_headers):
    """Import uses the current registry name, ignoring the stale name in the JSON."""
    ts = time.time()
    dp = await _create_dp(client, auth_headers, f"IT-Import-Current-{ts}")
    dp_id = dp["id"]
    graph_id = None

    try:
        import_payload = {
            "obs_export": "logic_graph",
            "version": 1,
            "name": f"IT-Import-Rename-{ts}",
            "description": "",
            "enabled": True,
            "flow_data": {
                "nodes": [
                    {
                        "id": "r1",
                        "type": "datapoint_read",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "datapoint_id": dp_id,
                            "datapoint_name": "STALE_NAME_FROM_EXPORT",
                        },
                    }
                ],
                "edges": [],
            },
        }

        import_resp = await client.post("/api/v1/logic/graphs/import", json=import_payload, headers=auth_headers)
        assert import_resp.status_code == 201, import_resp.text
        graph_id = import_resp.json()["id"]

        graph = await _get_graph(client, auth_headers, graph_id)
        node_data = graph["flow_data"]["nodes"][0]["data"]
        assert node_data["datapoint_name"] == f"IT-Import-Current-{ts}", f"Expected current registry name, got '{node_data['datapoint_name']}'"

    finally:
        await _cleanup(client, auth_headers, graph_ids=[graph_id] if graph_id else [], dp_ids=[dp_id])
