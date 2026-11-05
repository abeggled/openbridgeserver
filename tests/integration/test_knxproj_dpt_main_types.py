""".knxproj import of group addresses that carry only a DPT main type (#1260).

Variants of the demo project (``tests/knxproj_style_variants.py``) replace the
datapoint type of a few group addresses with a main type only ("DPT-14"). They are
imported through ``POST /api/v1/knxproj/import``; the result is read back through
the group-address catalog, the instance's bindings and the datapoints.

- A fresh import sets the main type, not a guessed subtype.
- A re-import never replaces a subtype an existing binding already carries with the
  bare main type of that subtype (values would change, e.g. by a factor of 2.55 for
  DPT5); only an empty field gets the main type.
"""

from __future__ import annotations

import uuid

import pytest

from obs.adapters.knx.group_address import normalize_ga
from tests.knxproj_style_variants import knxproj_with_datapoint_types

pytestmark = pytest.mark.integration

POWER_L1 = 8455  # DPST-14-56 in the demo project
POWER_L2 = 8456  # DPST-14-56
DIMMER = 6401  # DPST-5-1
ENERGY = 8705  # DPST-13-10
TEMPERATURE = 4353  # DPST-9-1
HVAC_MODE = 5377  # DPST-20-102
CLOCK = 10497  # DPST-10-1

MAIN_ONLY = {POWER_L1: "DPT-14", DIMMER: "DPT-5", ENERGY: "DPT-13", TEMPERATURE: "DPT-9", HVAC_MODE: "DPT-20"}


def _ga(raw: int) -> str:
    return normalize_ga(str(raw))


@pytest.fixture
async def knx_instance(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "KNX", "name": f"KnxDpt-{uuid.uuid4().hex[:8]}", "config": {}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    instance = resp.json()
    yield instance
    resp = await client.delete(f"/api/v1/adapters/instances/{instance['id']}", headers=auth_headers)
    assert resp.status_code == 204, resp.text


async def _import(client, auth_headers, datapoint_types: dict[int, str | None], instance: dict) -> None:
    resp = await client.post(
        "/api/v1/knxproj/import",
        files={"file": ("demo-dpt.knxproj", knxproj_with_datapoint_types(datapoint_types), "application/octet-stream")},
        params={"adapter_name": instance["name"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def _bindings(client, auth_headers, instance: dict) -> dict[str, dict]:
    resp = await client.get(f"/api/v1/adapters/instances/{instance['id']}/bindings", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return {entry["config"]["group_address"]: entry for entry in resp.json()}


async def _datapoint(client, auth_headers, dp_id: str) -> dict:
    resp = await client.get(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _catalog_dpt(client, auth_headers, raw: int) -> str | None:
    resp = await client.get("/api/v1/knxproj/group-addresses", params={"q": _ga(raw)}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return {item["address"]: item["dpt"] for item in resp.json()["items"]}[_ga(raw)]


async def test_dpt_list_offers_the_main_types(client, auth_headers):
    resp = await client.get("/api/v1/adapters/knx/dpts", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    by_id = {d["dpt_id"]: d for d in resp.json()}
    assert by_id["DPT14"]["unit"] == ""
    assert by_id["DPT5"]["data_type"] == "INTEGER"


@pytest.mark.parametrize(
    ("raw", "dpt_id", "data_type"),
    [
        (POWER_L1, "DPT14", "FLOAT"),
        (DIMMER, "DPT5", "INTEGER"),
        (ENERGY, "DPT13", "INTEGER"),
        (TEMPERATURE, "DPT9", "FLOAT"),
        (HVAC_MODE, "DPT20", "INTEGER"),
    ],
)
async def test_import_sets_the_main_type_instead_of_a_guessed_subtype(raw, dpt_id, data_type, client, auth_headers, knx_instance):
    await _import(client, auth_headers, MAIN_ONLY, knx_instance)

    assert await _catalog_dpt(client, auth_headers, raw) == dpt_id
    binding = (await _bindings(client, auth_headers, knx_instance))[_ga(raw)]
    assert binding["config"]["dpt_id"] == dpt_id
    datapoint = await _datapoint(client, auth_headers, binding["datapoint_id"])
    assert datapoint["data_type"] == data_type
    assert datapoint["unit"] is None, "a main type carries no unit"


async def test_reimport_keeps_the_subtype_of_an_existing_binding(client, auth_headers, knx_instance):
    # Populated instance: bindings with subtypes from the project, one changed by hand.
    await _import(client, auth_headers, {}, knx_instance)
    bindings = await _bindings(client, auth_headers, knx_instance)
    dimmer = bindings[_ga(DIMMER)]
    resp = await client.patch(
        f"/api/v1/datapoints/{dimmer['datapoint_id']}/bindings/{dimmer['binding_id']}",
        json={"config": {**dimmer["config"], "dpt_id": "DPT5.004"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    before = {raw: await _datapoint(client, auth_headers, bindings[_ga(raw)]["datapoint_id"]) for raw in (POWER_L1, DIMMER)}

    await _import(client, auth_headers, MAIN_ONLY, knx_instance)

    after = await _bindings(client, auth_headers, knx_instance)
    assert after[_ga(POWER_L1)]["config"]["dpt_id"] == "DPT14.056"
    assert after[_ga(DIMMER)]["config"]["dpt_id"] == "DPT5.004"
    for raw in (POWER_L1, DIMMER):
        datapoint = await _datapoint(client, auth_headers, after[_ga(raw)]["datapoint_id"])
        assert (datapoint["data_type"], datapoint["unit"]) == (before[raw]["data_type"], before[raw]["unit"])
    # The catalog mirrors the project file.
    assert await _catalog_dpt(client, auth_headers, POWER_L1) == "DPT14"


async def test_reimport_fills_an_empty_dpt_with_the_main_type(client, auth_headers, knx_instance):
    await _import(client, auth_headers, {POWER_L2: None}, knx_instance)
    assert "dpt_id" not in (await _bindings(client, auth_headers, knx_instance))[_ga(POWER_L2)]["config"]

    await _import(client, auth_headers, {POWER_L2: "DPT-14"}, knx_instance)

    binding = (await _bindings(client, auth_headers, knx_instance))[_ga(POWER_L2)]
    assert binding["config"]["dpt_id"] == "DPT14"
    datapoint = await _datapoint(client, auth_headers, binding["datapoint_id"])
    assert (datapoint["data_type"], datapoint["unit"]) == ("FLOAT", None)


async def test_reimport_with_another_main_type_replaces_the_old_subtype(client, auth_headers, knx_instance):
    """The project changed the main type: the old subtype no longer applies."""
    await _import(client, auth_headers, {}, knx_instance)
    assert (await _bindings(client, auth_headers, knx_instance))[_ga(DIMMER)]["config"]["dpt_id"] == "DPT5.001"

    await _import(client, auth_headers, {DIMMER: "DPT-7"}, knx_instance)

    binding = (await _bindings(client, auth_headers, knx_instance))[_ga(DIMMER)]
    assert binding["config"]["dpt_id"] == "DPT7"
    datapoint = await _datapoint(client, auth_headers, binding["datapoint_id"])
    assert (datapoint["data_type"], datapoint["unit"]) == ("INTEGER", None)


async def test_reimport_with_a_main_type_that_only_shares_digits_replaces_the_subtype(client, auth_headers, knx_instance):
    """DPT10.001 is no subtype of DPT1: the guard compares whole main types, not text prefixes."""
    await _import(client, auth_headers, {}, knx_instance)
    assert (await _bindings(client, auth_headers, knx_instance))[_ga(CLOCK)]["config"]["dpt_id"] == "DPT10.001"

    await _import(client, auth_headers, {CLOCK: "DPT-1"}, knx_instance)

    binding = (await _bindings(client, auth_headers, knx_instance))[_ga(CLOCK)]
    assert binding["config"]["dpt_id"] == "DPT1"
    datapoint = await _datapoint(client, auth_headers, binding["datapoint_id"])
    assert (datapoint["data_type"], datapoint["unit"]) == ("BOOLEAN", None)


async def test_reimport_with_a_subtype_still_replaces_the_stored_one(client, auth_headers, knx_instance):
    """Regression guard: a subtype from the project wins over a stored main type, as before."""
    await _import(client, auth_headers, {POWER_L1: "DPT-14"}, knx_instance)
    await _import(client, auth_headers, {POWER_L1: "DPST-14-57"}, knx_instance)

    binding = (await _bindings(client, auth_headers, knx_instance))[_ga(POWER_L1)]
    assert binding["config"]["dpt_id"] == "DPT14.057"
