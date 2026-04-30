"""Integration Tests — Logic Engine Transformation (issue #287)

Verifies that value_formula and value_map on datapoint_read / datapoint_write
nodes are correctly applied during graph execution, including the edge case
of Python bool values (as produced by KNX DPT1.x) with numeric-keyed maps.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_dp(client, auth_headers, name: str, data_type: str = "INTEGER") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": data_type, "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_value(client, auth_headers, dp_id: str, value) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, f"value write failed: {resp.text}"


async def _create_graph(client, auth_headers, name: str, nodes: list, edges: list) -> str:
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={
            "name": name,
            "description": "Integration test",
            "enabled": True,
            "flow_data": {"nodes": nodes, "edges": edges},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _run_graph(client, auth_headers, graph_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/logic/graphs/{graph_id}/run",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["outputs"]


async def _cleanup(client, auth_headers, graph_id: str | None = None, dp_ids: list | None = None) -> None:
    if graph_id:
        await client.delete(f"/api/v1/logic/graphs/{graph_id}", headers=auth_headers)
    for dp_id in dp_ids or []:
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_datapoint_read_value_formula_applied(client, auth_headers):
    """value_formula on datapoint_read transforms the value before output."""
    dp = await _create_dp(client, auth_headers, f"IT-Read-Formula-{__import__('time').time()}")
    dp_id = dp["id"]
    graph_id = None
    try:
        await _set_value(client, auth_headers, dp_id, 10)

        graph_id = await _create_graph(
            client,
            auth_headers,
            "IT-Read-Formula",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "datapoint_id": dp_id,
                        "datapoint_name": "test",
                        "value_formula": "x * 2",
                    },
                }
            ],
            [],
        )

        outputs = await _run_graph(client, auth_headers, graph_id)
        assert outputs["r1"]["value"] == pytest.approx(20.0)
    finally:
        await _cleanup(client, auth_headers, graph_id, [dp_id])


@pytest.mark.asyncio
async def test_datapoint_read_value_map_integer_applied(client, auth_headers):
    """value_map with integer input maps the value correctly."""
    dp = await _create_dp(client, auth_headers, f"IT-Read-Map-Int-{__import__('time').time()}")
    dp_id = dp["id"]
    graph_id = None
    try:
        await _set_value(client, auth_headers, dp_id, 1)

        graph_id = await _create_graph(
            client,
            auth_headers,
            "IT-Read-Map-Int",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "datapoint_id": dp_id,
                        "datapoint_name": "test",
                        "value_map": {"0": "1", "1": "0"},
                    },
                }
            ],
            [],
        )

        outputs = await _run_graph(client, auth_headers, graph_id)
        # Integer 1 → key "1" → mapped to "0"
        assert outputs["r1"]["value"] == "0"
    finally:
        await _cleanup(client, auth_headers, graph_id, [dp_id])


@pytest.mark.asyncio
async def test_datapoint_read_value_map_bool_input_applied(client, auth_headers):
    """value_map with numeric keys works for boolean DataPoint values (issue #287).

    KNX DPT1.x decodes to Python bool. The num_invert preset {"0":"1","1":"0"}
    must apply even when the runtime value is True / False.
    """
    dp = await _create_dp(client, auth_headers, f"IT-Read-Map-Bool-{__import__('time').time()}", "BOOLEAN")
    dp_id = dp["id"]
    graph_id = None
    try:
        await _set_value(client, auth_headers, dp_id, True)

        graph_id = await _create_graph(
            client,
            auth_headers,
            "IT-Read-Map-Bool",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "datapoint_id": dp_id,
                        "datapoint_name": "test",
                        "value_map": {"0": "1", "1": "0"},
                    },
                }
            ],
            [],
        )

        outputs = await _run_graph(client, auth_headers, graph_id)
        # True → numeric fallback key "1" → mapped to "0"
        assert outputs["r1"]["value"] == "0"
    finally:
        await _cleanup(client, auth_headers, graph_id, [dp_id])


@pytest.mark.asyncio
async def test_datapoint_write_value_map_applied(client, auth_headers):
    """value_map on datapoint_write transforms the written value (issue #287)."""
    src = await _create_dp(client, auth_headers, f"IT-Write-Map-Src-{__import__('time').time()}")
    dst = await _create_dp(client, auth_headers, f"IT-Write-Map-Dst-{__import__('time').time()}")
    src_id = src["id"]
    dst_id = dst["id"]
    graph_id = None
    try:
        await _set_value(client, auth_headers, src_id, 0)

        graph_id = await _create_graph(
            client,
            auth_headers,
            "IT-Write-Map",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": src_id, "datapoint_name": "src"},
                },
                {
                    "id": "w1",
                    "type": "datapoint_write",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "datapoint_id": dst_id,
                        "datapoint_name": "dst",
                        "value_map": {"0": "Aus", "1": "An"},
                    },
                },
            ],
            [
                {
                    "id": "e1",
                    "source": "r1",
                    "sourceHandle": "value",
                    "target": "w1",
                    "targetHandle": "value",
                }
            ],
        )

        outputs = await _run_graph(client, auth_headers, graph_id)
        # write node output is _write_value (private, not in outputs)
        # but the transformation must have produced "Aus" from integer 0
        assert outputs["w1"]["_write_value"] == "Aus"
    finally:
        await _cleanup(client, auth_headers, graph_id, [src_id, dst_id])


@pytest.mark.asyncio
async def test_datapoint_read_formula_then_value_map(client, auth_headers):
    """Formula runs first, then value_map is applied to the formula result."""
    dp = await _create_dp(client, auth_headers, f"IT-Formula-Map-{__import__('time').time()}")
    dp_id = dp["id"]
    graph_id = None
    try:
        await _set_value(client, auth_headers, dp_id, 1)

        graph_id = await _create_graph(
            client,
            auth_headers,
            "IT-Formula-Map",
            [
                {
                    "id": "r1",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "datapoint_id": dp_id,
                        "datapoint_name": "test",
                        "value_formula": "x * 2",  # 1 * 2 = 2
                        "value_map": {"2": "Zwei"},  # 2 → "Zwei"
                    },
                }
            ],
            [],
        )

        outputs = await _run_graph(client, auth_headers, graph_id)
        assert outputs["r1"]["value"] == "Zwei"
    finally:
        await _cleanup(client, auth_headers, graph_id, [dp_id])
