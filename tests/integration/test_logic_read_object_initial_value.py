"""Integration tests — Read Object initial value on sheet activation (issue #1031).

Verifies that saving/activating a logic sheet immediately seeds every
datapoint_read node with the current registry value and propagates it through
the graph, without waiting for the next external DataPoint update:

  1. Creating an enabled graph initializes downstream writes; later updates
     still propagate normally.
  2. Multiple Read Object blocks are initialized independently.
  3. A DataPoint without a current value is handled deterministically
     (no write, no error) — including downstream nodes that would coerce
     the missing value to 0/False.
  4. A disabled graph is not initialized; re-activating it (PATCH) is.
  5. A full save (PUT) that enables the graph initializes it.
  6. Import and duplicate behave like create.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_dp(client, auth_headers, name: str, data_type: str = "INTEGER") -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": data_type, "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_value(client, auth_headers, dp_id: str, value) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def _get_value(client, auth_headers, dp_id: str):
    resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["value"]


def _read_write_flow(src_id: str, dst_id: str, suffix: str = "") -> dict:
    return {
        "nodes": [
            {
                "id": f"r{suffix or 1}",
                "type": "datapoint_read",
                "position": {"x": 0, "y": 0},
                "data": {"datapoint_id": src_id, "datapoint_name": "src"},
            },
            {
                "id": f"w{suffix or 1}",
                "type": "datapoint_write",
                "position": {"x": 300, "y": 0},
                "data": {"datapoint_id": dst_id, "datapoint_name": "dst"},
            },
        ],
        "edges": [
            {
                "id": f"e{suffix or 1}",
                "source": f"r{suffix or 1}",
                "sourceHandle": "value",
                "target": f"w{suffix or 1}",
                "targetHandle": "value",
            }
        ],
    }


async def _create_graph(client, auth_headers, name: str, flow_data: dict, enabled: bool = True) -> str:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": name, "description": "Integration test #1031", "enabled": enabled, "flow_data": flow_data},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _cleanup(client, auth_headers, graph_ids: list | None = None, dp_ids: list | None = None) -> None:
    for graph_id in graph_ids or []:
        if graph_id:
            await client.delete(f"/api/v1/logic/graphs/{graph_id}", headers=auth_headers)
    for dp_id in dp_ids or []:
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_initializes_read_object_and_updates_still_propagate(client, auth_headers):
    """Saving an enabled sheet seeds the read block; later updates propagate normally."""
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Init-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Init-Dst-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_id, 42)

        graph_id = await _create_graph(client, auth_headers, "IT-1031-Init", _read_write_flow(src_id, dst_id))

        # Initialized from the current value — without any new external update
        assert await _get_value(client, auth_headers, dst_id) == 42

        # Subsequent updates continue to propagate normally
        await _set_value(client, auth_headers, src_id, 43)
        assert await _get_value(client, auth_headers, dst_id) == 43
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_multiple_read_blocks_initialized_independently(client, auth_headers):
    ts = time.time()
    src_a = await _create_dp(client, auth_headers, f"IT-1031-Multi-SrcA-{ts}")
    src_b = await _create_dp(client, auth_headers, f"IT-1031-Multi-SrcB-{ts}")
    dst_a = await _create_dp(client, auth_headers, f"IT-1031-Multi-DstA-{ts}")
    dst_b = await _create_dp(client, auth_headers, f"IT-1031-Multi-DstB-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_a, 7)
        await _set_value(client, auth_headers, src_b, 8)

        flow_a = _read_write_flow(src_a, dst_a, "A")
        flow_b = _read_write_flow(src_b, dst_b, "B")
        flow = {"nodes": flow_a["nodes"] + flow_b["nodes"], "edges": flow_a["edges"] + flow_b["edges"]}
        graph_id = await _create_graph(client, auth_headers, "IT-1031-Multi", flow)

        assert await _get_value(client, auth_headers, dst_a) == 7
        assert await _get_value(client, auth_headers, dst_b) == 8
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_a, src_b, dst_a, dst_b])


@pytest.mark.asyncio
async def test_no_current_value_is_deterministic(client, auth_headers):
    """A source DataPoint without a value must not produce a write or an error."""
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-NoVal-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-NoVal-Dst-{ts}")
    graph_id = None
    try:
        graph_id = await _create_graph(client, auth_headers, "IT-1031-NoVal", _read_write_flow(src_id, dst_id))

        assert await _get_value(client, auth_headers, dst_id) is None

        # The graph still works once the source receives its first value
        await _set_value(client, auth_headers, src_id, 5)
        assert await _get_value(client, auth_headers, dst_id) == 5
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_unseeded_branch_does_not_write_coerced_value(client, auth_headers):
    """A branch fed by a Read Object without a current value must not write a
    coerced 0/False, while independent seeded branches still initialize."""
    ts = time.time()
    src_a = await _create_dp(client, auth_headers, f"IT-1031-Taint-SrcA-{ts}")
    src_b = await _create_dp(client, auth_headers, f"IT-1031-Taint-SrcB-{ts}")
    dst_a = await _create_dp(client, auth_headers, f"IT-1031-Taint-DstA-{ts}")
    dst_b = await _create_dp(client, auth_headers, f"IT-1031-Taint-DstB-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_a, 7)
        # src_b intentionally has no value — math_map would coerce None to 0

        flow_a = _read_write_flow(src_a, dst_a, "A")
        flow = {
            "nodes": flow_a["nodes"]
            + [
                {
                    "id": "rB",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 200},
                    "data": {"datapoint_id": src_b, "datapoint_name": "srcB"},
                },
                {
                    "id": "mB",
                    "type": "math_map",
                    "position": {"x": 150, "y": 200},
                    "data": {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1},
                },
                {
                    "id": "wB",
                    "type": "datapoint_write",
                    "position": {"x": 300, "y": 200},
                    "data": {"datapoint_id": dst_b, "datapoint_name": "dstB"},
                },
            ],
            "edges": flow_a["edges"]
            + [
                {"id": "eB1", "source": "rB", "sourceHandle": "value", "target": "mB", "targetHandle": "value"},
                {"id": "eB2", "source": "mB", "sourceHandle": "result", "target": "wB", "targetHandle": "value"},
            ],
        }
        graph_id = await _create_graph(client, auth_headers, "IT-1031-Taint", flow)

        assert await _get_value(client, auth_headers, dst_a) == 7
        assert await _get_value(client, auth_headers, dst_b) is None
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_a, src_b, dst_a, dst_b])


@pytest.mark.asyncio
async def test_disabled_graph_not_initialized_but_reactivation_is(client, auth_headers):
    """Disabled sheets are not executed; PATCH enabled=true initializes them."""
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Toggle-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Toggle-Dst-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_id, 11)

        graph_id = await _create_graph(client, auth_headers, "IT-1031-Toggle", _read_write_flow(src_id, dst_id), enabled=False)
        assert await _get_value(client, auth_headers, dst_id) is None

        resp = await client.patch(f"/api/v1/logic/graphs/{graph_id}", json={"enabled": True}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert await _get_value(client, auth_headers, dst_id) == 11
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_full_update_enabling_graph_initializes(client, auth_headers):
    """PUT (full save) that activates the sheet seeds the read block."""
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Put-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Put-Dst-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_id, 21)

        flow = _read_write_flow(src_id, dst_id)
        graph_id = await _create_graph(client, auth_headers, "IT-1031-Put", flow, enabled=False)
        assert await _get_value(client, auth_headers, dst_id) is None

        resp = await client.put(
            f"/api/v1/logic/graphs/{graph_id}",
            json={"name": "IT-1031-Put", "description": "Integration test #1031", "enabled": True, "flow_data": flow},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert await _get_value(client, auth_headers, dst_id) == 21
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_import_initializes_read_object(client, auth_headers):
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Import-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Import-Dst-{ts}")
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_id, 33)

        resp = await client.post(
            "/api/v1/logic/graphs/import",
            json={
                "obs_export": "logic_graph",
                "version": 1,
                "name": "IT-1031-Import",
                "description": "Integration test #1031",
                "enabled": True,
                "flow_data": _read_write_flow(src_id, dst_id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        graph_id = resp.json()["id"]

        assert await _get_value(client, auth_headers, dst_id) == 33
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_duplicate_initializes_read_object(client, auth_headers):
    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Dup-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Dup-Dst-{ts}")
    graph_id = None
    copy_id = None
    try:
        await _set_value(client, auth_headers, src_id, 55)
        graph_id = await _create_graph(client, auth_headers, "IT-1031-Dup", _read_write_flow(src_id, dst_id))
        assert await _get_value(client, auth_headers, dst_id) == 55

        # Overwrite the destination so the duplicate's initial run is observable
        await _set_value(client, auth_headers, dst_id, 99)

        resp = await client.post(f"/api/v1/logic/graphs/{graph_id}/duplicate", headers=auth_headers)
        assert resp.status_code == 201, resp.text
        copy_id = resp.json()["id"]

        assert await _get_value(client, auth_headers, dst_id) == 55
    finally:
        await _cleanup(client, auth_headers, [graph_id, copy_id], [src_id, dst_id])


@pytest.mark.asyncio
async def test_config_import_initializes_read_object(client, auth_headers):
    """A graph restored via the full configuration import is initialized too."""
    import uuid as _uuid

    ts = time.time()
    src_id = await _create_dp(client, auth_headers, f"IT-1031-Restore-Src-{ts}")
    dst_id = await _create_dp(client, auth_headers, f"IT-1031-Restore-Dst-{ts}")
    graph_id = str(_uuid.uuid4())
    try:
        await _set_value(client, auth_headers, src_id, 66)

        resp = await client.post(
            "/api/v1/config/import",
            json={
                "obs_version": "5",
                "exported_at": "2026-01-01T00:00:00",
                "datapoints": [],
                "bindings": [],
                "logic_graphs": [
                    {
                        "id": graph_id,
                        "name": "IT-1031-Restore",
                        "description": "Integration test #1031",
                        "enabled": True,
                        "flow_data": _read_write_flow(src_id, dst_id),
                    }
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["logic_graphs_created"] == 1

        assert await _get_value(client, auth_headers, dst_id) == 66
    finally:
        await _cleanup(client, auth_headers, [graph_id], [src_id, dst_id])
