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
    STATE_RAW,
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
    """Each project style × each notation of the path parameter.

    The diagonal (path typed in the project's own notation: ThreeLevel-ThreeLevel,
    TwoLevel-TwoLevel, Free-Free) is a regression guard and green on b765f253 as
    well — there the stored text and the path text happened to be the same raw
    string. The six off-diagonal cases document the fix.
    """
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

    # A broken feedback address is tolerated in stored data, but not accepted on save.
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={"adapter_instance_id": instance["id"], "direction": "BOTH", "config": {"group_address": "1/234", "state_group_address": "1/2/x"}},
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


async def test_csv_import_endpoint_is_removed(client, auth_headers):
    """P4: the CSV import was superseded by the .knxproj import (#67) and only accepted three-level rows."""
    resp = await client.post(
        "/api/v1/knxproj/import-csv",
        files={"file": ("ga.csv", b'"Group name";"Address"\n"Spots";"1/234"\n', "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code in (404, 405), resp.text


async def _insert_raw_binding(datapoint_id: str, config: dict) -> None:
    """A binding stored without passing any entrance, as data from before #1296 or a foreign tool.

    Without adapter instance, so it stays out of the per-instance assertions of other tests.
    """
    import json
    from datetime import UTC, datetime

    from obs.db.database import get_db

    now = datetime.now(UTC).isoformat()
    await get_db().execute_and_commit(
        """INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, 'KNX', NULL, 'SOURCE', ?, 1, ?, ?)""",
        (str(uuid.uuid4()), datapoint_id, json.dumps(config), now, now),
    )


@pytest.mark.parametrize("style", STYLES)
async def test_traceability_per_style(style, client, auth_headers, knx_instance):
    """GA→devices, device→datapoints and the datapoint's KNX context after an import in each style.

    The second datapoint carries its binding in the project's notation without
    having passed an entrance; the KNX context still resolves it (name, device).
    """
    await _import(client, auth_headers, style, adapter_name=knx_instance["name"])
    switch = INTERNAL[CO_SWITCH_RAW]

    for notation in STYLES:
        resp = await client.get(f"/api/v1/knxproj/group-addresses/{NOTATION[notation][CO_SWITCH_RAW]}/devices", headers=auth_headers)
        assert [device["pa"] for device in resp.json()["items"]] == [DEVICE_PA], notation

    resp = await client.get(f"/api/v1/knxproj/devices/{DEVICE_PA}/datapoints", headers=auth_headers)
    imported = [dp for dp in resp.json()["datapoints"] if dp["instance_name"] == knx_instance["name"] and dp["ga_address"] == switch]
    assert len(imported) == 1

    raw_dp = await _datapoint(client, auth_headers)
    await _insert_raw_binding(raw_dp["id"], {"group_address": NOTATION[style][CO_SWITCH_RAW]})
    for dp_id in (imported[0]["id"], raw_dp["id"]):
        resp = await client.get(f"/api/v1/datapoints/{dp_id}/knx-context", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        [ga] = resp.json()["group_addresses"]
        assert (ga["address"], ga["name"], [device["pa"] for device in ga["devices"]]) == (switch, "Licht EG Schalten", [DEVICE_PA])


# One address per notation, so an earlier parameter cannot leave the expected row behind.
CONFIG_GA = {"ThreeLevel": ("1/4/77", "1/4/77"), "TwoLevel": ("1/1102", "1/4/78"), "Free": ("3151", "1/4/79")}


@pytest.mark.parametrize("notation", STYLES)
async def test_config_import_stores_internal_addresses(notation, client, auth_headers, knx_instance, clean_group_addresses):
    datapoint_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/config/import",
        json={
            "obs_version": "5",
            "exported_at": "2026-01-01T00:00:00",
            "datapoints": [{"id": datapoint_id, "name": f"CfgDP-{notation}", "data_type": "BOOLEAN", "unit": None, "tags": [], "mqtt_alias": None}],
            "bindings": [
                {
                    "id": str(uuid.uuid4()),
                    "datapoint_id": datapoint_id,
                    "adapter_type": "KNX",
                    "adapter_instance_id": knx_instance["id"],
                    "direction": "SOURCE",
                    "config": {"group_address": CONFIG_GA[notation][0]},
                    "enabled": True,
                }
            ],
            "knx_group_addresses": [{"address": CONFIG_GA[notation][0], "name": "CfgGA", "description": "", "dpt": None}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == []

    resp = await client.get(f"/api/v1/datapoints/{datapoint_id}/knx-context", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert [(ga["address"], ga["name"]) for ga in resp.json()["group_addresses"]] == [(CONFIG_GA[notation][1], "CfgGA")]


@pytest.mark.parametrize("binding_notation", STYLES)
async def test_ringbuffer_group_address_filter_accepts_every_notation(binding_notation, client, auth_headers, knx_instance):
    from tests.integration.test_ringbuffer_filters import _query_ringbuffer_v2, _write_value

    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={"adapter_instance_id": knx_instance["id"], "direction": "SOURCE", "config": {"group_address": NOTATION[binding_notation][STATE_RAW]}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    await _write_value(client, auth_headers, datapoint["id"], True)

    for filter_notation in STYLES:
        rows = await _query_ringbuffer_v2(
            client,
            auth_headers,
            {"filters": {"metadata": {"group_addresses_any_of": [NOTATION[filter_notation][STATE_RAW]]}}},
        )
        assert datapoint["id"] in {row["datapoint_id"] for row in rows}, filter_notation


@pytest.mark.parametrize("style", ["TwoLevel", "Free"])
async def test_every_entrance_stores_only_internal_addresses(style, client, auth_headers):
    """Data invariant: drive every entrance with a project-notation input, then scan all GA storage.

    Catches a raw store through any module, field name or SQL statement; the
    ``knx_*`` tables additionally reject raw texts through database triggers (V56).
    """
    from obs.db.database import get_db
    from tests.knx_group_address_invariant import non_internal_group_addresses

    db = get_db()
    before = {row["id"] for row in await db.fetchall("SELECT id FROM adapter_bindings")}
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "KNX", "name": f"KnxInvariant-{uuid.uuid4().hex[:8]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    knx_instance = resp.json()

    await _import(client, auth_headers, style, adapter_name=knx_instance["name"], hierarchy_modes="buildings,trades")
    datapoint = await _datapoint(client, auth_headers)
    resp = await client.post(
        f"/api/v1/datapoints/{datapoint['id']}/bindings",
        json={
            "adapter_instance_id": knx_instance["id"],
            "direction": "BOTH",
            "config": {"group_address": NOTATION[style][STATE_RAW], "state_group_address": NOTATION[style][FUNCTION_RAW]},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    binding_id = resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/datapoints/{datapoint['id']}/bindings/{binding_id}",
        json={"config": {"group_address": NOTATION[style][CO_STATUS_RAW]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/datapoints/{datapoint['id']}/duplicate", json={"name": "Invariant copy"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    imported_dp = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/config/import",
        json={
            "obs_version": "5",
            "exported_at": "2026-01-01T00:00:00",
            "datapoints": [{"id": imported_dp, "name": "Invariant", "data_type": "BOOLEAN", "unit": None, "tags": [], "mqtt_alias": None}],
            "bindings": [
                {
                    "id": str(uuid.uuid4()),
                    "datapoint_id": imported_dp,
                    "adapter_type": "KNX",
                    "adapter_instance_id": knx_instance["id"],
                    "direction": "BOTH",
                    "config": {"group_address": NOTATION[style][CO_SWITCH_RAW], "state_group_address": NOTATION[style][STATE_RAW]},
                    "enabled": True,
                }
            ],
            "knx_group_addresses": [{"address": NOTATION[style][STATE_RAW], "name": "Invariant", "description": "", "dpt": None}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == []

    created = {row["id"] for row in await db.fetchall("SELECT id FROM adapter_bindings")} - before
    assert len(created) >= 500 + 3
    # Ringbuffer: the binding snapshot of a new entry, also for a binding stored without any entrance.
    from tests.integration.test_ringbuffer_filters import _query_ringbuffer, _write_value
    from tests.knx_group_address_invariant import non_internal_ringbuffer_bindings

    bypass_dp = await _datapoint(client, auth_headers)
    await _insert_raw_binding(bypass_dp["id"], {"group_address": NOTATION[style][STATE_RAW], "state_group_address": NOTATION[style][CO_SWITCH_RAW]})
    entries = []
    for dp_id in (datapoint["id"], bypass_dp["id"]):
        await _write_value(client, auth_headers, dp_id, True)
        entries += await _query_ringbuffer(client, auth_headers, {"q": dp_id, "limit": 1})
    assert len(entries) == 2
    try:
        assert await non_internal_group_addresses(db, binding_ids=created) == []
        assert non_internal_ringbuffer_bindings(entries) == []
    finally:
        await client.delete(f"/api/v1/adapters/instances/{knx_instance['id']}", headers=auth_headers)


async def test_import_with_destination_direction_creates_dest_bindings(client, auth_headers):
    """The bulk binding import with direction=DEST (only the removed CSV tests covered it)."""
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "KNX", "name": f"KnxDest-{uuid.uuid4().hex[:8]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    instance = resp.json()
    try:
        body = await _import(client, auth_headers, "TwoLevel", adapter_name=instance["name"], direction="DEST")
        assert body["created"] == 500
        from obs.db.database import get_db

        rows = await get_db().fetchall("SELECT direction, config FROM adapter_bindings WHERE adapter_instance_id = ?", (instance["id"],))
        assert {row["direction"] for row in rows} == {"DEST"}
        assert len(rows) == 500
    finally:
        await client.delete(f"/api/v1/adapters/instances/{instance['id']}", headers=auth_headers)
