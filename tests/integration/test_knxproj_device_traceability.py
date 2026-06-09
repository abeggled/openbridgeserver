from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from obs.db.database import get_db

pytestmark = pytest.mark.integration


async def _seed_device_graph(pa: str, ga: str, *, manufacturer: str = "Acme", order_number: str = "ORD-1") -> dict:
    db = get_db()
    now = datetime.now(UTC).isoformat()
    device_id = str(uuid.uuid4())
    co_id = str(uuid.uuid4())

    await db.execute(
        """INSERT OR IGNORE INTO knx_group_addresses
               (address, name, description, dpt, imported_at, main_group_name, mid_group_name)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ga, f"GA {ga}", "", "DPT1.001", now, "", ""),
    )
    await db.execute(
        """INSERT INTO knx_devices
               (id, individual_address, name, description, product_name, product_refid, hardware2program_refid, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (device_id, pa, f"Device {pa}", "", manufacturer, order_number, "APP-REF", now),
    )
    await db.execute(
        """INSERT INTO knx_comm_objects
               (id, device_id, number, name, text, function_text, datapoint_type, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (co_id, device_id, "1", "Switch", "", "", "DPT1.001", now),
    )
    await db.execute(
        "INSERT INTO knx_co_ga_links (comm_object_id, ga_address) VALUES (?, ?)",
        (co_id, ga),
    )
    await db.commit()

    return {"device_id": device_id, "co_id": co_id}


async def _create_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "persist_value": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_binding(dp_id: str, ga: str) -> None:
    db = get_db()
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            dp_id,
            "knx",
            "SOURCE",
            '{"group_address": "%s"}' % ga,
            1,
            now,
            now,
        ),
    )


async def _write_value(client, auth_headers, dp_id: str, value: float) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def test_knxproj_device_endpoints_expose_v34_device_graph(client, auth_headers):
    pa = f"1.1.{30 + (uuid.uuid4().int % 40)}"
    ga = f"3/1/{10 + (uuid.uuid4().int % 40)}"
    await _seed_device_graph(pa, ga, manufacturer="Siemens", order_number="S-500")

    list_resp = await client.get(
        "/api/v1/knxproj/devices",
        params={"q": "siemens", "manufacturer": "siem", "room": "EG", "trade": "Licht", "size": 50},
        headers=auth_headers,
    )
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    assert body["total"] >= 1
    assert any(item["pa"] == pa and item["manufacturer"] == "Siemens" for item in body["items"])

    detail_resp = await client.get(f"/api/v1/knxproj/devices/{pa}", headers=auth_headers)
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["pa"] == pa
    assert detail["order_number"] == "S-500"
    assert detail["comm_objects"]
    assert ga in detail["comm_objects"][0]["ga_addresses"]

    ga_resp = await client.get(f"/api/v1/knxproj/group-addresses/{ga}/devices", headers=auth_headers)
    assert ga_resp.status_code == 200, ga_resp.text
    ga_body = ga_resp.json()
    assert ga_body["total"] >= 1
    assert any(item["pa"] == pa for item in ga_body["items"])


async def test_device_pa_filter_matches_same_ga_as_knxproj_lookup(client, auth_headers):
    pa = f"1.1.{70 + (uuid.uuid4().int % 40)}"
    ga = f"4/2/{10 + (uuid.uuid4().int % 40)}"
    await _seed_device_graph(pa, ga)

    dp_match = await _create_dp(client, auth_headers, f"RB-KNX-MATCH-{uuid.uuid4()}")
    dp_other = await _create_dp(client, auth_headers, f"RB-KNX-OTHER-{uuid.uuid4()}")
    await _insert_binding(dp_match["id"], ga)
    await _insert_binding(dp_other["id"], "7/7/7")
    await _write_value(client, auth_headers, dp_match["id"], 42.0)
    await _write_value(client, auth_headers, dp_other["id"], 9.0)

    create_resp = await client.post(
        "/api/v1/ringbuffer/filtersets",
        json={"name": f"RB Device Trace {uuid.uuid4()}", "filter": {"devices": [pa]}},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    set_id = create_resp.json()["id"]

    try:
        ga_devices_resp = await client.get(f"/api/v1/knxproj/group-addresses/{ga}/devices", headers=auth_headers)
        assert ga_devices_resp.status_code == 200, ga_devices_resp.text
        ga_pas = {item["pa"] for item in ga_devices_resp.json()["items"]}
        assert pa in ga_pas

        query_resp = await client.post(
            "/api/v1/ringbuffer/filtersets/query",
            json={"set_ids": [set_id], "limit": 100},
            headers=auth_headers,
        )
        assert query_resp.status_code == 200, query_resp.text
        rows = query_resp.json()
        assert rows
        ids = {row["datapoint_id"] for row in rows}
        assert dp_match["id"] in ids
        assert dp_other["id"] not in ids
    finally:
        await client.delete(f"/api/v1/ringbuffer/filtersets/{set_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_match['id']}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_other['id']}", headers=auth_headers)
        db = get_db()
        await db.execute("DELETE FROM knx_co_ga_links WHERE ga_address = ?", (ga,))
        await db.execute(
            "DELETE FROM knx_comm_objects WHERE id IN (SELECT id FROM knx_comm_objects WHERE device_id IN (SELECT id FROM knx_devices WHERE individual_address = ?))",
            (pa,),
        )
        await db.execute("DELETE FROM knx_devices WHERE individual_address = ?", (pa,))
        await db.execute("DELETE FROM knx_group_addresses WHERE address = ?", (ga,))
        await db.commit()
