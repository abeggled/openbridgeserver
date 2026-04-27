"""
Integration Tests — RTR-Widget (Issue #59)

Testet den Visu-API-Roundtrip für das RTR-Widget:
  - Seite mit vollständiger RTR-Config anlegen
  - Konfiguration korrekt zurücklesen (JSON-Serialisierung)
  - Optionale Datenpunkte (null) werden beibehalten
  - supported_modes-Array überlebt den Roundtrip
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

async def _create_float_dp(client, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "unit": "°C", "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_int_dp(client, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "INTEGER", "tags": []},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_page(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/visu/nodes",
        json={"name": f"RTR-Test-{uuid.uuid4()}", "type": "PAGE", "order": 999, "access": "protected"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _delete(client, auth_headers, path: str):
    await client.delete(path, headers=auth_headers)


def _rtr_widget(dp_id: str | None, config: dict, widget_id: str | None = None) -> dict:
    return {
        "id":                  widget_id or str(uuid.uuid4()),
        "name":                "RTR Test",
        "type":                "RTR",
        "datapoint_id":        dp_id,
        "status_datapoint_id": None,
        "x": 0, "y": 0, "w": 3, "h": 5,
        "config":              config,
    }


def _full_rtr_config(actual_dp: str | None = None, mode_dp: str | None = None) -> dict:
    return {
        "label":             "Wohnzimmer",
        "color":             "#ef4444",
        "min_temp":          5,
        "max_temp":          35,
        "step":              0.5,
        "decimals":          1,
        "setpoint_offset":   0.0,
        "actual_offset":     0.5,
        "actual_temp_dp_id": actual_dp,
        "mode_dp_id":        mode_dp,
        "show_modes":        True,
        "supported_modes":   [0, 1, 3, 6],
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_rtr_page_config_roundtrip(client, auth_headers):
    """Vollständige RTR-Config überlebt PUT → GET Roundtrip."""
    dp_soll   = await _create_float_dp(client, auth_headers, f"RTR-Soll-{uuid.uuid4()}")
    dp_actual = await _create_float_dp(client, auth_headers, f"RTR-Ist-{uuid.uuid4()}")
    dp_mode   = await _create_int_dp(client, auth_headers, f"RTR-Mode-{uuid.uuid4()}")
    page_id   = await _create_page(client, auth_headers)

    widget_id = str(uuid.uuid4())
    cfg       = _full_rtr_config(actual_dp=dp_actual, mode_dp=dp_mode)

    page_payload = {
        "grid_cols": 12, "grid_row_height": 80, "grid_cell_width": 80,
        "background": None,
        "widgets": [_rtr_widget(dp_soll, cfg, widget_id)],
    }

    try:
        put = await client.put(
            f"/api/v1/visu/pages/{page_id}",
            json=page_payload,
            headers=auth_headers,
        )
        assert put.status_code in (200, 204), put.text

        get = await client.get(f"/api/v1/visu/pages/{page_id}", headers=auth_headers)
        assert get.status_code == 200, get.text

        page = get.json()
        assert len(page["widgets"]) == 1

        w = page["widgets"][0]
        assert w["id"]           == widget_id
        assert w["type"]         == "RTR"
        assert w["datapoint_id"] == dp_soll

        c = w["config"]
        assert c["label"]             == "Wohnzimmer"
        assert c["color"]             == "#ef4444"
        assert c["min_temp"]          == 5
        assert c["max_temp"]          == 35
        assert c["step"]              == 0.5
        assert c["decimals"]          == 1
        assert c["actual_offset"]     == 0.5
        assert c["actual_temp_dp_id"] == dp_actual
        assert c["mode_dp_id"]        == dp_mode
        assert c["show_modes"]        is True
        assert c["supported_modes"]   == [0, 1, 3, 6]
    finally:
        await _delete(client, auth_headers, f"/api/v1/visu/nodes/{page_id}")
        await _delete(client, auth_headers, f"/api/v1/datapoints/{dp_soll}")
        await _delete(client, auth_headers, f"/api/v1/datapoints/{dp_actual}")
        await _delete(client, auth_headers, f"/api/v1/datapoints/{dp_mode}")


async def test_rtr_optional_dps_null(client, auth_headers):
    """RTR ohne optionale DPs: null-Werte werden korrekt gespeichert."""
    dp_soll = await _create_float_dp(client, auth_headers, f"RTR-Soll-{uuid.uuid4()}")
    page_id = await _create_page(client, auth_headers)

    cfg = _full_rtr_config(actual_dp=None, mode_dp=None)

    try:
        put = await client.put(
            f"/api/v1/visu/pages/{page_id}",
            json={
                "grid_cols": 12, "grid_row_height": 80, "grid_cell_width": 80,
                "background": None,
                "widgets": [_rtr_widget(dp_soll, cfg)],
            },
            headers=auth_headers,
        )
        assert put.status_code in (200, 204)

        get = await client.get(f"/api/v1/visu/pages/{page_id}", headers=auth_headers)
        assert get.status_code == 200

        c = get.json()["widgets"][0]["config"]
        assert c["actual_temp_dp_id"] is None
        assert c["mode_dp_id"]        is None
    finally:
        await _delete(client, auth_headers, f"/api/v1/visu/nodes/{page_id}")
        await _delete(client, auth_headers, f"/api/v1/datapoints/{dp_soll}")


async def test_rtr_partial_supported_modes(client, auth_headers):
    """supported_modes-Teilmenge [1, 3] überlebt den Roundtrip."""
    dp_soll = await _create_float_dp(client, auth_headers, f"RTR-Soll-{uuid.uuid4()}")
    page_id = await _create_page(client, auth_headers)

    cfg = _full_rtr_config()
    cfg["supported_modes"] = [1, 3]

    try:
        await client.put(
            f"/api/v1/visu/pages/{page_id}",
            json={
                "grid_cols": 12, "grid_row_height": 80, "grid_cell_width": 80,
                "background": None,
                "widgets": [_rtr_widget(dp_soll, cfg)],
            },
            headers=auth_headers,
        )

        get = await client.get(f"/api/v1/visu/pages/{page_id}", headers=auth_headers)
        c   = get.json()["widgets"][0]["config"]
        assert c["supported_modes"] == [1, 3]
    finally:
        await _delete(client, auth_headers, f"/api/v1/visu/nodes/{page_id}")
        await _delete(client, auth_headers, f"/api/v1/datapoints/{dp_soll}")
