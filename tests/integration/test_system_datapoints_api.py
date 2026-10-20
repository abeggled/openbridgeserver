"""Integration tests — read-only system DataPoints and the update status API (issue #1370).

Every test enters through a supported entry point of the running app: the HTTP
API, an MQTT ``dp/{uuid}/set`` publish against the real broker, or a config
import. The session app runs with ``update_check.enabled = false`` (see
conftest), so no test talks to GitHub.
"""

from __future__ import annotations

import asyncio
import uuid

import aiomqtt
import pytest

from obs.core.system_datapoints import CURRENT_VERSION, LAST_CHECK, LATEST_VERSION, SYSTEM_DATAPOINTS, UPDATE_AVAILABLE, UPDATE_CHANNEL

pytestmark = pytest.mark.integration

_ADAPTER_TYPE = "SNMP"


async def _create_instance(client, auth_headers, adapter_type=_ADAPTER_TYPE, *, enabled=False) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": adapter_type, "name": f"SysDpInst-{uuid.uuid4().hex[:6]}", "config": {}, "enabled": enabled},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _value(client, auth_headers, definition) -> dict:
    resp = await client.get(f"/api/v1/datapoints/{definition.id}/value", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Creation and initial values
# ---------------------------------------------------------------------------


async def test_system_datapoints_exist_once_with_their_definition(client, auth_headers):
    resp = await client.get("/api/v1/datapoints/", params={"size": 500}, headers=auth_headers)
    assert resp.status_code == 200
    by_id = {item["id"]: item for item in resp.json()["items"]}
    for definition in SYSTEM_DATAPOINTS:
        item = by_id[str(definition.id)]
        assert (item["name"], item["data_type"], item["system"]) == (definition.name, definition.data_type, True)
    names = [item["name"] for item in resp.json()["items"]]
    assert names.count("OBS Update Channel") == 1


async def test_channel_and_version_are_known_right_after_start(client, auth_headers):
    from obs.update.service import get_update_service

    channel = await _value(client, auth_headers, UPDATE_CHANNEL)
    assert channel["quality"] == "good"
    assert channel["value"] in ("stable", "pre-release", "nightly")
    current = await _value(client, auth_headers, CURRENT_VERSION)
    assert (current["value"], current["quality"]) == (get_update_service().state.installed_version, "good")
    # No online check ran (disabled): no false "no update available".
    for definition in (LATEST_VERSION, UPDATE_AVAILABLE, LAST_CHECK):
        value = await _value(client, auth_headers, definition)
        assert (value["value"], value["quality"]) == (None, "uncertain")


# ---------------------------------------------------------------------------
# Every external write path is closed
# ---------------------------------------------------------------------------


async def test_api_value_write_is_refused(client, auth_headers):
    resp = await client.post(f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}/value", json={"value": True}, headers=auth_headers)
    assert resp.status_code == 403
    assert (await _value(client, auth_headers, UPDATE_AVAILABLE))["value"] is None


async def test_patch_rename_and_value_are_refused(client, auth_headers):
    for body in ({"name": "Mine"}, {"value": True}, {"external_write_enabled": True}):
        resp = await client.patch(f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}", json=body, headers=auth_headers)
        assert resp.status_code == 403, body
    resp = await client.get(f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}", headers=auth_headers)
    assert resp.json()["name"] == "OBS Update Available"


async def test_delete_and_duplicate_are_refused(client, auth_headers):
    resp = await client.delete(f"/api/v1/datapoints/{UPDATE_CHANNEL.id}", headers=auth_headers)
    assert resp.status_code == 403
    resp = await client.post(f"/api/v1/datapoints/{UPDATE_CHANNEL.id}/duplicate", json={"name": "Copy"}, headers=auth_headers)
    assert resp.status_code == 403
    assert (await client.get(f"/api/v1/datapoints/{UPDATE_CHANNEL.id}", headers=auth_headers)).status_code == 200


async def test_mqtt_set_is_ignored(client, auth_headers, mosquitto_port):
    async with aiomqtt.Client(hostname="localhost", port=mosquitto_port) as mqtt:
        await mqtt.publish(f"dp/{UPDATE_AVAILABLE.id}/set", payload=b"true")
    await asyncio.sleep(0.5)
    assert (await _value(client, auth_headers, UPDATE_AVAILABLE))["value"] is None


async def test_bindings_may_read_but_never_write(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    url = f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}/bindings"
    for direction in ("SOURCE", "BOTH"):
        resp = await client.post(url, json={"adapter_instance_id": inst["id"], "direction": direction, "config": {}}, headers=auth_headers)
        assert resp.status_code == 422, direction

    resp = await client.post(url, json={"adapter_instance_id": inst["id"], "direction": "DEST", "config": {}}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    binding_id = resp.json()["id"]
    try:
        resp = await client.patch(f"{url}/{binding_id}", json={"direction": "SOURCE"}, headers=auth_headers)
        assert resp.status_code == 422
    finally:
        assert (await client.delete(f"{url}/{binding_id}", headers=auth_headers)).status_code == 204


@pytest.mark.parametrize("adapter_type", ["ZEITSCHALTUHR", "ANWESENHEITSSIMULATION"])
async def test_timer_and_presence_simulation_cannot_bind_a_system_datapoint(client, auth_headers, adapter_type):
    """Both only write into their DataPoint: a DEST binding of theirs would stay dead."""
    inst = await _create_instance(client, auth_headers, adapter_type)
    url = f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}/bindings"
    resp = await client.post(url, json={"adapter_instance_id": inst["id"], "direction": "DEST", "config": {}}, headers=auth_headers)
    assert resp.status_code == 422, resp.text
    assert adapter_type in resp.json()["detail"]
    assert (await client.get(url, headers=auth_headers)).json() == []
    # The Admin GUI does not offer the instance in the first place.
    assert inst["system_bindable"] is False


async def test_config_import_cannot_repoint_an_existing_system_binding(client, auth_headers):
    inst = await _create_instance(client, auth_headers)
    url = f"/api/v1/datapoints/{UPDATE_AVAILABLE.id}/bindings"
    resp = await client.post(url, json={"adapter_instance_id": inst["id"], "direction": "DEST", "config": {}}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    binding_id = resp.json()["id"]
    user_dp = (
        await client.post("/api/v1/datapoints/", json={"name": f"SysImpUser-{uuid.uuid4().hex[:6]}", "data_type": "BOOLEAN"}, headers=auth_headers)
    ).json()
    try:
        payload = {
            "obs_version": "5",
            "exported_at": "2026-10-17T00:00:00",
            "datapoints": [],
            "bindings": [
                {
                    # Same binding id, but claiming another DataPoint: the import
                    # only updates the row, which stays on the system DataPoint.
                    "id": binding_id,
                    "datapoint_id": user_dp["id"],
                    "adapter_type": _ADAPTER_TYPE,
                    "adapter_instance_id": inst["id"],
                    "direction": "SOURCE",
                    "config": {},
                    "enabled": True,
                }
            ],
        }
        resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["bindings_updated"] == 0
        bindings = (await client.get(url, headers=auth_headers)).json()
        assert [(b["id"], b["direction"]) for b in bindings] == [(binding_id, "DEST")]
    finally:
        await client.delete(f"{url}/{binding_id}", headers=auth_headers)


async def test_config_import_cannot_touch_system_datapoints(client, auth_headers):
    inst_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2026-10-17T00:00:00",
        "datapoints": [{"id": str(UPDATE_CHANNEL.id), "name": "Hijacked", "data_type": "FLOAT", "unit": None, "tags": [], "mqtt_alias": None}],
        "bindings": [
            {
                "id": str(uuid.uuid4()),
                "datapoint_id": str(UPDATE_CHANNEL.id),
                "adapter_type": _ADAPTER_TYPE,
                "adapter_instance_id": inst_id,
                "direction": "SOURCE",
                "config": {},
                "enabled": True,
            }
        ],
        "adapter_instances": [{"id": inst_id, "adapter_type": _ADAPTER_TYPE, "name": f"SysImp-{inst_id[:6]}", "config": {}, "enabled": False}],
        "app_settings": [{"key": "update.channel", "value": "nightly"}, {"key": "update.check_state", "value": "{}"}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["datapoints_created"], body["datapoints_updated"], body["bindings_created"], body["app_settings_upserted"]) == (0, 0, 0, 0)
    assert any("only DEST bindings" in error for error in body["errors"])
    dp = (await client.get(f"/api/v1/datapoints/{UPDATE_CHANNEL.id}", headers=auth_headers)).json()
    assert (dp["name"], dp["data_type"]) == ("OBS Update Channel", "STRING")


async def test_new_system_bindings_get_the_value_without_a_value_event(client, auth_headers, monkeypatch):
    """Only the bindings are fed: Logic, notifications and other bindings see no event.

    The SNMP instance connects without a network. Creating a binding feeds it,
    and the config import restarts every adapter (stop_all + start_all), after
    which each system binding of the instance gets the value again — the one
    the import brought along and the one it did not mention.
    """
    from obs.adapters.snmp.adapter import SnmpAdapter
    from obs.core.event_bus import DataValueEvent, get_event_bus

    fed = []
    events = []

    # Recorded where the value reaches the instance: proof it was found by ID.
    async def record_write(self, binding, value):
        fed.append((binding.datapoint_id, str(binding.id), value))

    async def record_event(event):
        if event.datapoint_id == CURRENT_VERSION.id:
            events.append(event)

    monkeypatch.setattr(SnmpAdapter, "write", record_write)
    get_event_bus().subscribe(DataValueEvent, record_event)
    version = (await _value(client, auth_headers, CURRENT_VERSION))["value"]
    inst = await _create_instance(client, auth_headers, enabled=True)
    url = f"/api/v1/datapoints/{CURRENT_VERSION.id}/bindings"
    imported_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2026-10-17T00:00:00",
        "datapoints": [],
        "bindings": [
            {
                "id": imported_id,
                "datapoint_id": str(CURRENT_VERSION.id),
                "adapter_type": _ADAPTER_TYPE,
                "adapter_instance_id": inst["id"],
                "direction": "DEST",
                "config": {},
                "enabled": True,
            },
            # Refused (SOURCE on a system DataPoint): nothing to feed.
            {
                "id": str(uuid.uuid4()),
                "datapoint_id": str(UPDATE_CHANNEL.id),
                "adapter_type": _ADAPTER_TYPE,
                "adapter_instance_id": inst["id"],
                "direction": "SOURCE",
                "config": {},
                "enabled": True,
            },
        ],
    }
    created_id = None
    try:
        resp = await client.post(url, json={"adapter_instance_id": inst["id"], "direction": "DEST", "config": {}}, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        created_id = resp.json()["id"]
        assert fed == [(CURRENT_VERSION.id, created_id, version)]
        fed.clear()

        resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["bindings_created"] == 1
        assert sorted(fed) == sorted([(CURRENT_VERSION.id, created_id, version), (CURRENT_VERSION.id, imported_id, version)])
        assert events == []
    finally:
        get_event_bus().unsubscribe(DataValueEvent, record_event)
        for binding_id in (created_id, imported_id):
            if binding_id:
                await client.delete(f"{url}/{binding_id}", headers=auth_headers)
        await client.delete(f"/api/v1/adapters/instances/{inst['id']}", headers=auth_headers)


def _write_flow(dp_id, node_type="datapoint_write") -> dict:
    return {"nodes": [{"id": "w", "type": node_type, "position": {"x": 0, "y": 0}, "data": {"datapoint_id": str(dp_id)}}], "edges": []}


async def test_logic_can_read_but_not_write_a_system_datapoint(client, auth_headers):
    """The event bus drops a Logic write: such a graph would report writes that never happen."""
    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": f"SysWrite-{uuid.uuid4().hex[:6]}", "flow_data": _write_flow(UPDATE_CHANNEL.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "read-only system DataPoint" in resp.json()["detail"]

    resp = await client.post(
        "/api/v1/logic/graphs",
        json={"name": f"SysRead-{uuid.uuid4().hex[:6]}", "flow_data": _write_flow(UPDATE_CHANNEL.id, "datapoint_read")},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    await client.delete(f"/api/v1/logic/graphs/{resp.json()['id']}", headers=auth_headers)


async def test_config_import_refuses_a_logic_graph_writing_a_system_datapoint(client, auth_headers):
    graph_id = str(uuid.uuid4())
    payload = {
        "obs_version": "5",
        "exported_at": "2026-10-17T00:00:00",
        "datapoints": [],
        "bindings": [],
        "logic_graphs": [{"id": graph_id, "name": "SysWriteImport", "description": "", "enabled": True, "flow_data": _write_flow(LAST_CHECK.id)}],
    }
    resp = await client.post("/api/v1/config/import", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert any("read-only system DataPoint" in error for error in resp.json()["errors"])
    assert (await client.get(f"/api/v1/logic/graphs/{graph_id}", headers=auth_headers)).status_code == 404


# ---------------------------------------------------------------------------
# Update status API
# ---------------------------------------------------------------------------


async def test_update_status_requires_login(client):
    assert (await client.get("/api/v1/system/update-status")).status_code == 401


async def test_update_status_reports_the_disabled_check(client, auth_headers):
    resp = await client.get("/api/v1/system/update-status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["channels"] == ["stable", "pre-release", "nightly"]
    assert (body["latest_version"], body["update_available"], body["release_notes_count"]) == (None, None, 0)
    notes = await client.get("/api/v1/system/update-status/release-notes", headers=auth_headers)
    assert notes.json() == []


async def test_manual_check_is_refused_while_disabled(client, auth_headers):
    resp = await client.post("/api/v1/system/update-status/check", headers=auth_headers)
    assert resp.status_code == 409


async def test_channel_change_updates_the_datapoint(client, auth_headers):
    before = (await client.get("/api/v1/system/update-status", headers=auth_headers)).json()["channel"]
    try:
        resp = await client.put("/api/v1/system/update-channel", json={"channel": "nightly"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["channel"] == "nightly"
        assert (await _value(client, auth_headers, UPDATE_CHANNEL))["value"] == "nightly"
        assert (await client.put("/api/v1/system/update-channel", json={"channel": "beta"}, headers=auth_headers)).status_code == 422
    finally:
        await client.put("/api/v1/system/update-channel", json={"channel": before}, headers=auth_headers)


# ---------------------------------------------------------------------------
# A real check end to end — GitHub replaced by a scripted source.
# Kept last in this file: it leaves a successful result behind until it resets.
# ---------------------------------------------------------------------------


class _ScriptedSource:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def fetch(self, etag):
        from obs.update.service import FetchResult

        self.calls += 1
        return FetchResult(not_modified=False, etag='W/"it"', payload=self.payload)


async def test_manual_check_publishes_every_system_datapoint(client, auth_headers, monkeypatch):
    from obs.update.service import STATE_SETTING_KEY, get_update_service

    service = get_update_service()
    source = _ScriptedSource(
        [
            {
                "tag_name": "nightly-20990102",
                "draft": False,
                "prerelease": True,
                "published_at": "2099-01-02T07:00:00Z",
                "body": "n",
                "html_url": "https://x/n",
            },
            {
                "tag_name": "2099.1.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2099-01-01T07:00:00Z",
                "body": "### New\n* a",
                "html_url": "https://x/s",
            },
            {
                "tag_name": "2098.12.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2098-12-01T07:00:00Z",
                "body": "* b",
                "html_url": "https://x/o",
            },
        ]
    )
    previous_channel = service.state.channel
    # Before the source is patched in: a channel change without a cached release
    # list would otherwise run a check of its own.
    await client.put("/api/v1/system/update-channel", json={"channel": "stable"}, headers=auth_headers)
    for attr in ("_releases", "_releases_fetched_at", "_etag"):
        monkeypatch.setattr(service, attr, getattr(service, attr))
    monkeypatch.setattr(service, "_source", source)
    monkeypatch.setattr(service, "_installed_version", "2098.12.0")
    monkeypatch.setattr(service.state, "installed_version", "2098.12.0")
    try:
        resp = await client.post("/api/v1/system/update-status/check", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["enabled"], body["latest_version"], body["update_available"], body["release_notes_count"]) == (True, "2099.1.0", True, 1)
        assert source.calls == 1

        assert (await _value(client, auth_headers, LATEST_VERSION)) | {"ts": None} == {
            "id": str(LATEST_VERSION.id),
            "value": "2099.1.0",
            "unit": None,
            "quality": "good",
            "ts": None,
        }
        assert (await _value(client, auth_headers, UPDATE_AVAILABLE))["value"] is True
        last_check = await _value(client, auth_headers, LAST_CHECK)
        assert last_check["quality"] == "good"
        assert last_check["value"].startswith(body["last_check_at"][:19])

        notes = (await client.get("/api/v1/system/update-status/release-notes", headers=auth_headers)).json()
        assert [n["version"] for n in notes] == ["2099.1.0"]
        assert notes[0]["body"] == "### New\n* a"
    finally:
        monkeypatch.undo()
        await client.put("/api/v1/system/update-channel", json={"channel": previous_channel}, headers=auth_headers)
        from obs.db.database import get_db

        await get_db().execute_and_commit("DELETE FROM app_settings WHERE key = ?", (STATE_SETTING_KEY,))
        await service.load()


async def test_concurrent_manual_checks_queued_behind_channel_change(client, auth_headers, monkeypatch):
    """Two "Check now" requests waiting behind a channel change ask GitHub once (PR #1371 review)."""
    from datetime import UTC, datetime

    from obs.db.database import get_db
    from obs.update import service as svc

    service = svc.get_update_service()

    class Source:
        def __init__(self):
            self.calls = 0

        async def fetch(self, etag):
            self.calls += 1
            await asyncio.sleep(0)
            return svc.FetchResult(False, None, [])

    source = Source()
    previous_channel = service.state.channel
    monkeypatch.setattr(service, "_source", source)
    monkeypatch.setattr(service, "_releases", [])
    monkeypatch.setattr(service, "_releases_fetched_at", datetime(2026, 10, 18, tzinfo=UTC))
    monkeypatch.setattr(service, "_etag", None)
    # Earlier tests' checks would hold the manual cool-down.
    monkeypatch.setattr(service, "_last_attempt_at", None)

    entered = asyncio.Event()
    release = asyncio.Event()
    original = svc._write_setting
    blocked_once = False

    async def slow_write_setting(db, key, value):
        nonlocal blocked_once
        if key == svc.CHANNEL_SETTING_KEY and not blocked_once:
            blocked_once = True
            entered.set()
            await release.wait()
        await original(db, key, value)

    monkeypatch.setattr(svc, "_write_setting", slow_write_setting)
    new_channel = "nightly" if previous_channel != "nightly" else "stable"
    try:
        channel_change = asyncio.create_task(client.put("/api/v1/system/update-channel", json={"channel": new_channel}, headers=auth_headers))
        await entered.wait()
        checks = [asyncio.create_task(client.post("/api/v1/system/update-status/check", headers=auth_headers)) for _ in range(2)]
        await asyncio.sleep(0)
        release.set()

        responses = await asyncio.gather(channel_change, *checks)
        assert [response.status_code for response in responses] == [200, 200, 200]
        assert source.calls == 1
    finally:
        monkeypatch.undo()
        await client.put("/api/v1/system/update-channel", json={"channel": previous_channel}, headers=auth_headers)
        await get_db().execute_and_commit("DELETE FROM app_settings WHERE key = ?", (svc.STATE_SETTING_KEY,))
        await service.load()
