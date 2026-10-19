"""Integration Tests — Visu alarms through the running app.

Covers the admin configuration routes and the public runtime routes: a value
written to the trigger datapoint raises the alarm, an anonymous Visu sees it in
the active list and can acknowledge it.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

URL = "/api/v1/system/visu-alarms"


async def _datapoint(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"Briefkasten-{uuid.uuid4().hex[:8]}", "data_type": "BOOLEAN"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _write(client, auth_headers, dp_id: str, value) -> None:
    resp = await client.post(f"/api/v1/datapoints/{dp_id}/value", json={"value": value}, headers=auth_headers)
    assert resp.status_code == 204, resp.text


@pytest.fixture
async def reset_alarms(client, auth_headers):
    yield
    await client.put(URL, json={"alarms": []}, headers=auth_headers)


async def test_configuration_is_admin_only(client, reset_alarms):
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(URL, json={"alarms": []})).status_code == 401


async def test_unknown_datapoint_is_rejected(client, auth_headers, reset_alarms):
    missing = str(uuid.uuid4())
    resp = await client.put(
        URL,
        json={"alarms": [{"id": "x", "name": "X", "datapoint_id": missing, "title": "X"}]},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert missing in resp.json()["detail"]


async def test_value_change_raises_alarm_visible_and_acknowledgeable_without_login(client, auth_headers, reset_alarms):
    dp_id = await _datapoint(client, auth_headers)
    await _write(client, auth_headers, dp_id, False)
    alarm = {
        "id": "post",
        "name": "Post",
        "datapoint_id": dp_id,
        "operator": "==",
        "compare_value": True,
        "title": "Post ist gekommen",
        "icon": "📬",
        "sound": True,
        "devices": ["wandpanel"],
    }
    resp = await client.put(URL, json={"alarms": [alarm]}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert (await client.get(URL, headers=auth_headers)).json()[0]["title"] == "Post ist gekommen"
    assert (await client.get(f"{URL}/active")).json() == []

    await _write(client, auth_headers, dp_id, True)

    active = (await client.get(f"{URL}/active")).json()  # no login
    assert len(active) == 1
    assert {k: active[0][k] for k in ("id", "title", "icon", "sound", "devices", "ack_button")} == {
        "id": "post",
        "title": "Post ist gekommen",
        "icon": "📬",
        "sound": True,
        "devices": ["wandpanel"],
        "ack_button": True,
    }
    assert "value" not in active[0]

    assert (await client.post(f"{URL}/post/acknowledge")).status_code == 204  # no login
    assert (await client.get(f"{URL}/active")).json() == []
    assert (await client.post(f"{URL}/post/acknowledge")).status_code == 404


async def test_end_on_clear_alarm_follows_the_datapoint(client, auth_headers, reset_alarms):
    dp_id = await _datapoint(client, auth_headers)
    await _write(client, auth_headers, dp_id, False)
    alarm = {
        "id": "phone",
        "name": "Telefon",
        "datapoint_id": dp_id,
        "title": "Anruf",
        "ack_button": False,
        "end_on_clear": True,
    }
    assert (await client.put(URL, json={"alarms": [alarm]}, headers=auth_headers)).status_code == 200

    await _write(client, auth_headers, dp_id, True)
    assert [a["id"] for a in (await client.get(f"{URL}/active")).json()] == ["phone"]
    assert (await client.post(f"{URL}/phone/acknowledge")).status_code == 404  # no acknowledge button

    await _write(client, auth_headers, dp_id, False)
    assert (await client.get(f"{URL}/active")).json() == []
