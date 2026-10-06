"""Seam S3 (#1296): .knxproj import in every ETS address style → read endpoints.

Each project variant (three-level, two-level, free) is derived at test time from
the demo project (see ``tests/knxproj_style_variants.py``) and imported through
``POST /api/v1/knxproj/import``. The read endpoints must then show the internal
three-level notation, whatever the project style, and accept a group address in
any notation.
"""

from __future__ import annotations

import uuid

import pytest

from tests.knxproj_style_variants import (
    CO_STATUS_RAW,
    CO_SWITCH_RAW,
    DEVICE_PA,
    FUNCTION_RAW,
    INTERNAL,
    NOTATION,
    STYLES,
    knxproj_in_style,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def knx_instance(client, auth_headers):
    """One disabled KNX instance for the module, removed with its bindings afterwards.

    Re-imports update the instance's bindings instead of adding datapoints, and
    removing it keeps the many imported KNX bindings out of later test modules
    (e.g. the adapter filter of the search).
    """
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "KNX", "name": f"KnxStyle-{uuid.uuid4().hex[:8]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    instance = resp.json()
    yield instance
    resp = await client.delete(f"/api/v1/adapters/instances/{instance['id']}", headers=auth_headers)
    assert resp.status_code == 204, resp.text


async def _import(client, auth_headers, style: str, **params) -> dict:
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": (f"demo-{style}.knxproj", knxproj_in_style(style), "application/octet-stream")},
        params=params,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _datapoint(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"KnxStyle-{uuid.uuid4().hex[:8]}", "data_type": "BOOLEAN"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def clean_group_addresses(client, auth_headers):
    resp = await client.delete("/api/v1/knxproj/group-addresses", headers=auth_headers)
    assert resp.status_code == 204


@pytest.mark.parametrize("style", STYLES)
async def test_import_stores_internal_addresses_and_reports_the_style(style, client, auth_headers, clean_group_addresses):
    body = await _import(client, auth_headers, style)
    assert body["imported"] == 500
    assert body["group_address_style"] == style

    resp = await client.get("/api/v1/knxproj/group-addresses", params={"size": 500}, headers=auth_headers)
    assert resp.status_code == 200
    page = resp.json()
    assert page["group_address_style"] == style
    assert page["total"] == 500
    addresses = {item["address"]: item["name"] for item in page["items"]}
    assert addresses[INTERNAL[CO_SWITCH_RAW]] == "Licht EG Schalten"
    assert all(address.count("/") == 2 for address in addresses), "only the internal three-level notation is stored"


@pytest.mark.parametrize("style", STYLES)
async def test_search_finds_an_address_in_project_and_internal_notation(style, client, auth_headers, clean_group_addresses):
    await _import(client, auth_headers, style)

    for query in {NOTATION[style][CO_SWITCH_RAW], INTERNAL[CO_SWITCH_RAW]}:
        resp = await client.get("/api/v1/knxproj/group-addresses", params={"q": query}, headers=auth_headers)
        assert resp.status_code == 200
        assert INTERNAL[CO_SWITCH_RAW] in [item["address"] for item in resp.json()["items"]], query


@pytest.mark.parametrize("style", STYLES)
async def test_device_datapoints_link_comm_objects_and_bindings_by_internal_address(style, client, auth_headers, knx_instance):
    instance = knx_instance
    await _import(client, auth_headers, style, adapter_name=instance["name"])

    resp = await client.get(f"/api/v1/knxproj/devices/{DEVICE_PA}/datapoints", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    context = resp.json()
    by_address = {ga["address"]: ga for co in context["comm_objects"] for ga in co["group_addresses"]}
    assert set(by_address) == {INTERNAL[CO_SWITCH_RAW], INTERNAL[CO_STATUS_RAW]}
    for address, ga in by_address.items():
        bound = [dp for dp in ga["datapoints"] if dp["instance_name"] == instance["name"]]
        assert [dp["ga_address"] for dp in bound] == [address], "the imported binding must carry the internal address"


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("notation", STYLES)
async def test_devices_by_group_address_accept_every_notation(style, notation, client, auth_headers):
    await _import(client, auth_headers, style)

    path_address = NOTATION[notation][CO_SWITCH_RAW]
    resp = await client.get(f"/api/v1/knxproj/group-addresses/{path_address}/devices", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert [device["pa"] for device in resp.json()["items"]] == [DEVICE_PA]


async def test_devices_by_group_address_rejects_an_invalid_address(client, auth_headers):
    resp = await client.get("/api/v1/knxproj/group-addresses/1/8/0/devices", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.parametrize("style", STYLES)
async def test_function_links_reach_a_hand_made_binding_in_another_notation(style, client, auth_headers, knx_instance):
    """Function→GA, import binding upsert and a hand-made binding meet on the internal address.

    A binding created by hand in the internal notation must be recognized by a
    later import of a two-level/free project: the import updates it instead of
    creating a second datapoint, and the building's function links it.
    """
    instance = knx_instance
    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={"adapter_instance_id": instance["id"], "direction": "SOURCE", "config": {"group_address": INTERNAL[FUNCTION_RAW]}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    body = await _import(client, auth_headers, style, adapter_name=instance["name"], hierarchy_modes="buildings")
    buildings = next(result for result in body["hierarchies"] if result["mode"] == "buildings")
    assert buildings["status"] == "created", buildings

    resp = await client.get(f"/api/v1/hierarchy/datapoints/{datapoint['id']}/nodes", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert buildings["tree_id"] in [node["tree_id"] for node in resp.json()]


@pytest.mark.parametrize("notation", STYLES)
async def test_binding_api_stores_the_internal_notation(notation, client, auth_headers, knx_instance):
    instance = knx_instance
    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={
            "adapter_instance_id": instance["id"],
            "direction": "BOTH",
            "config": {"group_address": NOTATION[notation][CO_SWITCH_RAW], "state_group_address": NOTATION[notation][CO_STATUS_RAW]},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    binding = resp.json()
    assert binding["config"]["group_address"] == INTERNAL[CO_SWITCH_RAW]
    assert binding["config"]["state_group_address"] == INTERNAL[CO_STATUS_RAW]

    resp = await client.patch(
        f"/api/v1/datapoints/{datapoint['id']}/bindings/{binding['id']}",
        json={"config": {"group_address": NOTATION[notation][FUNCTION_RAW]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"] == {"group_address": INTERNAL[FUNCTION_RAW]}


async def test_binding_api_rejects_an_invalid_group_address(client, auth_headers, knx_instance):
    instance = knx_instance
    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={"adapter_instance_id": instance["id"], "direction": "SOURCE", "config": {"group_address": "1/8/0"}},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_binding_api_keeps_an_empty_state_group_address_empty(client, auth_headers, knx_instance):
    instance = knx_instance
    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={
            "adapter_instance_id": instance["id"],
            "direction": "SOURCE",
            "config": {"group_address": "1/234", "state_group_address": " "},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"] == {"group_address": "1/0/234", "state_group_address": " "}
