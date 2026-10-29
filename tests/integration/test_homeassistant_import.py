"""Home Assistant integration layer end to end (#1392).

The real FastAPI app, database, adapter registry and WriteRouter run; Home
Assistant itself is replaced at the protocol boundary by ``FakeHomeAssistant``
(WebSocket protocol + REST API).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import websockets

from obs.adapters.homeassistant import adapter as ha_adapter
from tests.adapters.ha_fake import FakeHomeAssistant

pytestmark = pytest.mark.integration

TOKEN = "llat-integration-secret"


@pytest.fixture
def fake_ha(monkeypatch) -> FakeHomeAssistant:
    fake = FakeHomeAssistant(
        {
            "lock.shed": {"state": "locked", "attributes": {"friendly_name": "Shed lock"}},
            "cover.roof": {"state": "open", "attributes": {"friendly_name": "Roof window", "current_position": 40, "supported_features": 15}},
            "media_player.kitchen": {
                "state": "paused",
                "attributes": {
                    "friendly_name": "Kitchen",
                    "volume_level": 0.2,
                    "is_volume_muted": False,
                    "supported_features": 4 | 8 | 1 | 16384 | 32 | 16 | 4096,
                },
            },
        },
        token=TOKEN,
    )
    fake.entity_registry = [{"entity_id": "cover.roof", "platform": "velux", "unique_id": "roof-1", "device_id": None}]
    fake.services = {"cover": {"stop_cover": {"name": "Stop", "fields": {}}, "set_cover_position": {"fields": {"position": {"required": True}}}}}
    monkeypatch.setattr(websockets, "connect", fake.connect)
    monkeypatch.setattr(ha_adapter.httpx, "AsyncClient", fake.http_client_factory())
    return fake


async def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def _value_when(client, auth_headers, dp_id: str, check, timeout: float = 5.0) -> dict:
    """Poll a DataPoint value until *check* holds (the adapter works in background tasks)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = await _value(client, auth_headers, dp_id)
        if check(value):
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"value of {dp_id} never matched: {value}")
        await asyncio.sleep(0.02)


async def _value(client, auth_headers, dp_id: str) -> dict:
    resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_browse_import_and_write_through_write_router(client, auth_headers, fake_ha):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "HOME_ASSISTANT", "name": f"HA-{uuid.uuid4().hex[:6]}", "config": {"host": "ha.test", "token": TOKEN}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    instance_id = resp.json()["id"]
    base = f"/api/v1/adapters/instances/{instance_id}/homeassistant"
    try:
        await _wait(lambda: fake_ha.sockets and fake_ha.socket.authenticated)

        # Browse + filter (the adapter becomes browsable right after its login)
        for _attempt in range(250):
            resp = await client.get(f"{base}/entities", params={"domain": "cover"}, headers=auth_headers)
            if resp.status_code != 503:
                break
            await asyncio.sleep(0.02)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert [i["entity_id"] for i in page["items"]] == ["cover.roof"]
        assert page["items"][0]["platform"] == "velux"
        assert "roof-1" not in resp.text and TOKEN not in resp.text

        # Action catalogue for the binding form pickers
        resp = await client.get(f"{base}/services", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        cover = resp.json()[0]
        assert cover["domain"] == "cover"
        assert {"service": "set_cover_position", "name": None, "fields": [{"key": "position", "name": None, "required": True}]} in cover["services"]

        selections = {
            "selections": [
                {"entity_id": "lock.shed", "name": "Gartenhaus Schloss"},
                {"entity_id": "cover.roof"},
                {"entity_id": "cover.roof", "profile": "cover.position"},
                {"entity_id": "cover.roof", "profile": "cover.stop"},
                {"entity_id": "media_player.kitchen", "profile": "media_player.volume"},
                {"entity_id": "media_player.kitchen", "attribute": "volume_level"},  # same binding: planned once
                {"entity_id": "media_player.kitchen", "profile": "media_player.play_pause"},
            ],
            "tags": ["ha-it"],
        }
        # Preview mutates nothing
        resp = await client.post(f"{base}/import-preview", json=selections, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        preview = resp.json()["preview"]
        assert [c["status"] for c in preview] == ["new"] * 6
        assert preview[0]["convention"] == "trueLocked"
        bindings = await client.get(f"/api/v1/adapters/instances/{instance_id}/bindings", headers=auth_headers)
        assert bindings.json() == []

        # Import
        resp = await client.post(f"{base}/import", json=selections, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert (result["created_datapoints"], result["created_bindings"], result["errors"]) == (6, 6, [])

        # Idempotent: a second import creates nothing
        resp = await client.post(f"{base}/import", json=selections, headers=auth_headers)
        assert resp.json()["skipped_existing"] == 6 and resp.json()["created_datapoints"] == 0

        rows = (await client.get(f"/api/v1/adapters/instances/{instance_id}/bindings", headers=auth_headers)).json()
        by_key = {(r["config"]["entity_id"], r["config"].get("profile")): r["datapoint_id"] for r in rows}
        lock_dp = by_key[("lock.shed", "lock.boolean_locked")]
        lock = (await client.get(f"/api/v1/datapoints/{lock_dp}", headers=auth_headers)).json()
        assert lock["name"] == "Gartenhaus Schloss"  # name edited in the preview
        position_dp = by_key[("cover.roof", "cover.position")]
        stop_dp = by_key[("cover.roof", "cover.stop")]
        play_dp = by_key[("media_player.kitchen", "media_player.play_pause")]
        volume_dp = by_key[("media_player.kitchen", "media_player.volume")]

        # Initial values arrive without waiting for an HA state change
        await _value_when(client, auth_headers, lock_dp, lambda v: v["value"] is True)
        await _value_when(client, auth_headers, position_dp, lambda v: v["value"] == 40)
        await _value_when(client, auth_headers, volume_dp, lambda v: v["value"] == 0.2)

        # HA state changes reach OBS, transitional lock states keep the stable value
        fake_ha.set_state("lock.shed", "unlocking")
        await _value_when(client, auth_headers, lock_dp, lambda v: (v["value"], v["quality"]) == (True, "uncertain"))
        fake_ha.set_state("lock.shed", "unlocked")
        await _value_when(client, auth_headers, lock_dp, lambda v: (v["value"], v["quality"]) == (False, "good"))

        # OBS writes traverse the WriteRouter into HA actions
        fake_ha.service_calls.clear()
        for dp_id, value in ((lock_dp, True), (position_dp, 75), (stop_dp, True), (stop_dp, False), (play_dp, True), (volume_dp, 0.5)):
            resp = await client.post(f"/api/v1/datapoints/{dp_id}/value", json={"value": value}, headers=auth_headers)
            assert resp.status_code == 204, resp.text
        assert fake_ha.service_calls == [
            ("lock", "lock", {"entity_id": "lock.shed"}),
            ("cover", "set_cover_position", {"entity_id": "cover.roof", "position": 75}),
            ("cover", "stop_cover", {"entity_id": "cover.roof"}),
            ("media_player", "media_play", {"entity_id": "media_player.kitchen"}),
            ("media_player", "volume_set", {"entity_id": "media_player.kitchen", "volume_level": 0.5}),
        ]

        # A rejected action surfaces on the adapter status
        fake_ha.service_errors["lock.unlock"] = (400, {"message": "jammed"})
        await client.post(f"/api/v1/datapoints/{lock_dp}/value", json={"value": False}, headers=auth_headers)
        inst = (await client.get(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)).json()
        assert (inst["severity"], inst["status_detail_code"]) == ("warning", "haActionFailed")
        assert inst["config"]["token"] == TOKEN  # admins keep the editable value

        # Changing only the direction is still checked against the profile
        stop = next(r for r in rows if r["config"].get("profile") == "cover.stop")
        resp = await client.patch(
            f"/api/v1/datapoints/{stop['datapoint_id']}/bindings/{stop['binding_id']}",
            json={"direction": "SOURCE"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "cover.stop" in resp.text
    finally:
        resp = await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
        assert resp.status_code == 204, resp.text
