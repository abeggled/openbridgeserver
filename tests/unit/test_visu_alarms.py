"""Unit tests — Visu alarms (model, engine, routes, live channel)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.requests import Request

import obs.api.v1.visu_alarms as alarms_api
from obs.core.event_bus import DataValueEvent
from obs.db.database import Database
from obs.visu_alarms import (
    MAX_VISU_ALARMS,
    VISU_ALARMS_KEY,
    VisuAlarm,
    VisuAlarmEngine,
    VisuAlarmsIn,
    dump_visu_alarms,
    get_visu_alarm_engine,
    init_visu_alarm_engine,
    load_visu_alarms,
    parse_visu_alarms,
    reload_visu_alarms,
    reset_visu_alarm_engine,
)

DP = "11111111-1111-1111-1111-111111111111"
DP2 = "22222222-2222-2222-2222-222222222222"


def alarm(**overrides) -> VisuAlarm:
    data = {"id": "post", "name": "Post", "datapoint_id": DP, "title": "Post ist gekommen"}
    data.update(overrides)
    return VisuAlarm(**data)


def event(value, datapoint=DP, **kwargs) -> DataValueEvent:
    return DataValueEvent(datapoint_id=uuid.UUID(datapoint), value=value, quality="good", source_adapter="TEST", **kwargs)


@pytest.fixture(autouse=True)
def _fresh_engine():
    reset_visu_alarm_engine()
    yield
    reset_visu_alarm_engine()


# ── Model ─────────────────────────────────────────────────────────────────────


def test_defaults_and_normalisation():
    a = alarm(id="Post_1", name="  Briefkasten ", datapoint_id=DP.upper(), title=" Post ", page_id="")
    assert (a.id, a.name, a.datapoint_id, a.title, a.page_id) == ("Post_1", "Briefkasten", DP, "Post", None)
    assert (a.operator, a.compare_value, a.display, a.severity) == ("==", True, "message", "info")
    assert (a.ack_button, a.end_on_clear, a.sound, a.devices, a.enabled) == (True, False, False, [], True)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "with space"}, "id must consist"),
        ({"name": "   "}, "name must not be empty"),
        ({"datapoint_id": "not-a-uuid"}, "datapoint_id must be a UUID"),
        ({"display": "page", "page_id": None}, "needs page_id"),
        ({"title": "", "text": ""}, "needs a title or a text"),
        ({"ack_button": False, "end_on_clear": False}, "needs an end"),
        ({"operator": "any"}, "operator"),
    ],
)
def test_invalid_alarms_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        alarm(**overrides)


def test_valid_variants():
    assert alarm(display="page", page_id="page-1", title="").page_id == "page-1"
    assert alarm(title="", text="Nur Text").text == "Nur Text"
    assert alarm(ack_button=False, end_on_clear=True).end_on_clear is True


def test_ids_must_be_unique_and_count_is_limited():
    with pytest.raises(ValueError, match="duplicate alarm id: post"):
        VisuAlarmsIn(alarms=[alarm().model_dump(), alarm().model_dump()])
    with pytest.raises(ValueError):
        VisuAlarmsIn(alarms=[alarm(id=f"a{i}").model_dump() for i in range(MAX_VISU_ALARMS + 1)])


def test_stored_value_round_trip_and_lenient_load(caplog):
    raw = dump_visu_alarms([alarm(title="Grüße")])
    assert "Grüße" in raw
    assert parse_visu_alarms(raw) == [alarm(title="Grüße")]
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_visu_alarms("{broken")
    assert load_visu_alarms(None) == []
    assert load_visu_alarms("[{}]") == []
    assert VISU_ALARMS_KEY in caplog.text


# ── Engine ────────────────────────────────────────────────────────────────────


async def test_rising_edge_raises_and_falling_edge_does_not_end_without_end_on_clear():
    engine = VisuAlarmEngine()
    engine.configure([alarm()])

    await engine.handle_value_event(event(False))
    assert engine.active() == []
    await engine.handle_value_event(event(True))
    assert [a["id"] for a in engine.active()] == ["post"]
    await engine.handle_value_event(event(False))
    assert [a["id"] for a in engine.active()] == ["post"]  # stays until acknowledged


async def test_end_on_clear_ends_the_alarm_when_the_condition_goes_away():
    engine = VisuAlarmEngine()
    engine.configure([alarm(ack_button=False, end_on_clear=True, operator=">", compare_value=0)])

    await engine.handle_value_event(event(3))
    assert len(engine.active()) == 1
    assert engine.acknowledge("post") is False  # no acknowledge button
    await engine.handle_value_event(event(0))
    assert engine.active() == []


async def test_acknowledge_ends_the_alarm_and_the_next_rising_edge_raises_again():
    engine = VisuAlarmEngine()
    engine.configure([alarm()])

    await engine.handle_value_event(event(True))
    assert engine.acknowledge("post") is True
    assert engine.acknowledge("post") is False
    await engine.handle_value_event(event(True))  # still true: no new edge
    assert engine.active() == []
    await engine.handle_value_event(event(False))
    await engine.handle_value_event(event(True))
    assert len(engine.active()) == 1


async def test_events_for_other_datapoints_and_initialisation_are_ignored():
    engine = VisuAlarmEngine()
    engine.configure([alarm()])

    await engine.handle_value_event(event(True, datapoint=DP2))
    await engine.handle_value_event(event(True, initialization=True))
    assert engine.active() == []


async def test_several_alarms_on_one_datapoint_and_newest_first():
    engine = VisuAlarmEngine()
    engine.configure([alarm(id="low", operator=">", compare_value=1), alarm(id="high", operator=">", compare_value=5)])

    await engine.handle_value_event(event(3))
    await asyncio.sleep(0.001)
    await engine.handle_value_event(event(9))

    active = engine.active()
    assert [a["id"] for a in active] == ["high", "low"]
    assert set(active[0]) == {"id", "name", "display", "title", "text", "icon", "severity", "page_id", "ack_button", "sound", "devices", "since"}


async def test_configure_starts_from_current_values_and_keeps_or_ends_active_alarms():
    values = {uuid.UUID(DP): SimpleNamespace(value=True), uuid.UUID(DP2): SimpleNamespace(value=None)}
    engine = VisuAlarmEngine(values.get)
    engine.configure([alarm(), alarm(id="phone", datapoint_id=DP2), alarm(id="off", enabled=False, datapoint_id=DP2)])

    await engine.handle_value_event(event(True))  # already true before: no edge
    assert engine.active() == []
    await engine.handle_value_event(event(True, datapoint=DP2))
    assert [a["id"] for a in engine.active()] == ["phone"]

    engine.configure([alarm(id="phone", datapoint_id=DP2, title="Telefon klingelt")])
    assert engine.active()[0]["title"] == "Telefon klingelt"  # still active, updated display
    engine.configure([alarm()])
    assert engine.active() == []  # removed alarm ends


async def test_listeners_get_the_full_list_and_stalled_ones_only_the_newest():
    engine = VisuAlarmEngine()
    engine.configure([alarm()])
    queue = engine.listen()
    for i in range(20):
        await engine.handle_value_event(event(i % 2 == 0))
        engine.acknowledge("post")
    assert queue.qsize() == 16
    engine.unlisten(queue)
    await engine.handle_value_event(event(False))
    await engine.handle_value_event(event(True))
    assert queue.qsize() == 16


def test_singleton_helpers():
    assert get_visu_alarm_engine() is get_visu_alarm_engine()
    engine = init_visu_alarm_engine()
    assert get_visu_alarm_engine() is engine


# ── Routes ────────────────────────────────────────────────────────────────────


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    now = datetime.now(UTC).isoformat()
    await database.execute_and_commit(
        "INSERT INTO datapoints (id, name, mqtt_topic, created_at, updated_at) VALUES (?, 'Briefkasten', 'dp/x', ?, ?)",
        (DP, now, now),
    )
    await database.execute_and_commit(
        "INSERT INTO visu_nodes (id, name, type, node_order, created_at, updated_at) VALUES ('page-1', 'Post', 'PAGE', 0, ?, ?)",
        (now, now),
    )
    try:
        yield database
    finally:
        await database.disconnect()


def _request(path: str = "/api/v1/system/visu-alarms") -> Request:
    return Request({"type": "http", "method": "PUT", "path": path, "headers": [], "client": ("127.0.0.1", 1), "query_string": b""})


async def test_put_stores_applies_and_audits(db):
    body = VisuAlarmsIn(alarms=[alarm().model_dump(), alarm(id="page", display="page", page_id="page-1", title="").model_dump()])

    result = await alarms_api.update_visu_alarms(body=body, request=_request(), db=db, _admin="admin")

    assert result == body.alarms
    assert await alarms_api.get_visu_alarms(db=db, _admin="admin") == body.alarms
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_ALARMS_KEY,))
    assert json.loads(row["value"])[1]["page_id"] == "page-1"
    audit = await db.fetchone("SELECT * FROM audit_log_entries WHERE action = 'system.visu_alarms.updated'")
    assert (audit["actor"], audit["resource_type"], audit["resource_id"]) == ("admin", "visu_alarms", "global")
    await get_visu_alarm_engine().handle_value_event(event(True))
    assert {a["id"] for a in await alarms_api.get_active_visu_alarms()} == {"post", "page"}  # same datapoint


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [({"datapoint_id": DP2}, f"Unknown datapoint: {DP2}"), ({"display": "page", "page_id": "gone"}, "Unknown page: gone")],
)
async def test_put_rejects_unknown_references(db, overrides, detail):
    with pytest.raises(HTTPException) as exc:
        await alarms_api.update_visu_alarms(body=VisuAlarmsIn(alarms=[alarm(**overrides).model_dump()]), request=_request(), db=db, _admin="admin")
    assert (exc.value.status_code, exc.value.detail) == (422, detail)
    assert await alarms_api.get_visu_alarms(db=db, _admin="admin") == []


async def test_put_with_no_alarms_clears_everything(db):
    assert await alarms_api.update_visu_alarms(body=VisuAlarmsIn(alarms=[]), request=_request(), db=db, _admin="admin") == []


async def test_reload_reads_the_stored_definitions(db):
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (VISU_ALARMS_KEY, dump_visu_alarms([alarm()])))
    await reload_visu_alarms(db)
    await get_visu_alarm_engine().handle_value_event(event(True))
    assert len(get_visu_alarm_engine().active()) == 1
    await db.execute_and_commit("DELETE FROM app_settings WHERE key = ?", (VISU_ALARMS_KEY,))
    await reload_visu_alarms(db)
    assert get_visu_alarm_engine().active() == []


async def test_acknowledge_route():
    engine = get_visu_alarm_engine()
    engine.configure([alarm()])
    await engine.handle_value_event(event(True))
    acknowledge = alarms_api.acknowledge_visu_alarm.__wrapped__  # without the rate limiter

    await acknowledge("post", request=_request())
    assert engine.active() == []
    with pytest.raises(HTTPException) as exc:
        await acknowledge("post", request=_request())
    assert exc.value.status_code == 404


# ── Live channel ──────────────────────────────────────────────────────────────


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(alarms_api.router, prefix="/api/v1/system")
    app.state.limiter = alarms_api.limiter
    return app


def test_channel_sends_the_list_on_connect_and_after_every_change():
    engine = get_visu_alarm_engine()
    engine.configure([alarm()])
    asyncio.run(engine.handle_value_event(event(True)))

    with TestClient(_app()) as client, client.websocket_connect("/api/v1/system/visu-alarms/ws") as ws:
        first = ws.receive_json()
        assert first["action"] == "visu_alarms"
        assert [a["id"] for a in first["alarms"]] == ["post"]

        assert client.post("/api/v1/system/visu-alarms/post/acknowledge").status_code == 204
        assert ws.receive_json() == {"action": "visu_alarms", "alarms": []}
        assert client.get("/api/v1/system/visu-alarms/active").json() == []
    assert engine._listeners == set()


def test_channel_sends_keepalive_pings(monkeypatch):
    monkeypatch.setattr(alarms_api, "_KEEPALIVE_SECONDS", 0.05)
    with TestClient(_app()) as client, client.websocket_connect("/api/v1/system/visu-alarms/ws") as ws:
        assert ws.receive_json() == {"action": "visu_alarms", "alarms": []}
        assert ws.receive_json() == {"action": "ping"}
        ws.send_text("hello")  # client messages are ignored
        assert ws.receive_json() == {"action": "ping"}


async def test_channel_ends_quietly_when_sending_fails():
    class GoneSocket:
        async def accept(self):
            return None

        async def send_json(self, _data):
            raise WebSocketDisconnect(code=1006)

        async def receive_text(self):
            await asyncio.Event().wait()

    await alarms_api.visu_alarms_ws(GoneSocket())

    assert get_visu_alarm_engine()._listeners == set()
