"""Integration tests — GET /api/v1/logic/datapoint/{dp_id}/usages (issue #366).

Verifies that the endpoint correctly scans all logic graphs and returns
synthetic usage entries for every node that references a given DataPoint,
with the correct direction from the DataPoint's perspective:

  datapoint_read  → SOURCE  (logic reads the DP)
  datapoint_write → DEST    (logic writes to the DP)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_graph(client, auth_headers, name: str, nodes: list, edges: list | None = None, enabled: bool = True) -> str:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={
            "name": name,
            "description": "",
            "enabled": enabled,
            "flow_data": {"nodes": nodes, "edges": edges or []},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _read_node(dp_id: str, node_id: str = "n1") -> dict:
    return {
        "id": node_id,
        "type": "datapoint_read",
        "position": {"x": 0, "y": 0},
        "data": {"datapoint_id": dp_id, "datapoint_name": "test"},
    }


def _write_node(dp_id: str, node_id: str = "n2") -> dict:
    return {
        "id": node_id,
        "type": "datapoint_write",
        "position": {"x": 100, "y": 0},
        "data": {"datapoint_id": dp_id, "datapoint_name": "test"},
    }


async def _cleanup(client, auth_headers, graph_ids: list[str] | None = None, dp_ids: list[str] | None = None) -> None:
    for gid in graph_ids or []:
        await client.delete(f"/api/v1/logic/graphs/{gid}", headers=auth_headers)
    for did in dp_ids or []:
        await client.delete(f"/api/v1/datapoints/{did}", headers=auth_headers)


async def _get_usages(client, auth_headers, dp_id: str) -> list[dict]:
    resp = await client.get(f"/api/v1/logic/datapoint/{dp_id}/usages", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_usages_when_dp_not_in_any_graph(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "unused_dp_usages_test")
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert usages == []
    finally:
        await _cleanup(client, auth_headers, dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_read_node_yields_source_direction(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "dp_read_source_test")
    gid = await _create_graph(client, auth_headers, "graph_read_source", [_read_node(dp["id"])])
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert len(usages) == 1
        u = usages[0]
        assert u["graph_id"] == gid
        assert u["graph_name"] == "graph_read_source"
        assert u["graph_enabled"] is True
        assert u["node_type"] == "datapoint_read"
        assert u["direction"] == "SOURCE"
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid], dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_write_node_yields_dest_direction(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "dp_write_dest_test")
    gid = await _create_graph(client, auth_headers, "graph_write_dest", [_write_node(dp["id"])])
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert len(usages) == 1
        u = usages[0]
        assert u["node_type"] == "datapoint_write"
        assert u["direction"] == "DEST"
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid], dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_both_node_types_in_same_graph(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "dp_both_types_test")
    gid = await _create_graph(
        client,
        auth_headers,
        "graph_both",
        [_read_node(dp["id"], "nR"), _write_node(dp["id"], "nW")],
    )
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert len(usages) == 2
        directions = {u["direction"] for u in usages}
        assert directions == {"SOURCE", "DEST"}
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid], dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_multiple_graphs_referencing_same_dp(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "dp_multi_graph_test")
    gid1 = await _create_graph(client, auth_headers, "graph_mg_1", [_read_node(dp["id"])])
    gid2 = await _create_graph(client, auth_headers, "graph_mg_2", [_write_node(dp["id"])])
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert len(usages) == 2
        graph_ids = {u["graph_id"] for u in usages}
        assert gid1 in graph_ids
        assert gid2 in graph_ids
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid1, gid2], dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_disabled_graph_still_appears_in_usages(client, auth_headers):
    dp = await _create_dp(client, auth_headers, "dp_disabled_graph_test")
    gid = await _create_graph(client, auth_headers, "graph_disabled", [_read_node(dp["id"])], enabled=False)
    try:
        usages = await _get_usages(client, auth_headers, dp["id"])
        assert len(usages) == 1
        assert usages[0]["graph_enabled"] is False
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid], dp_ids=[dp["id"]])


@pytest.mark.asyncio
async def test_other_dp_not_included(client, auth_headers):
    dp_a = await _create_dp(client, auth_headers, "dp_other_a_test")
    dp_b = await _create_dp(client, auth_headers, "dp_other_b_test")
    gid = await _create_graph(client, auth_headers, "graph_only_a", [_read_node(dp_a["id"])])
    try:
        usages_b = await _get_usages(client, auth_headers, dp_b["id"])
        assert usages_b == []
        usages_a = await _get_usages(client, auth_headers, dp_a["id"])
        assert len(usages_a) == 1
    finally:
        await _cleanup(client, auth_headers, graph_ids=[gid], dp_ids=[dp_a["id"], dp_b["id"]])


@pytest.mark.asyncio
async def test_requires_authentication(client):
    resp = await client.get("/api/v1/logic/datapoint/00000000-0000-0000-0000-000000000000/usages")
    assert resp.status_code == 401
