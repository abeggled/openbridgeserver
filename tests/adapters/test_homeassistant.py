"""Unit-Tests für den Home Assistant Adapter (#9, #1392).

Keine echte HA-Instanz erforderlich: ``FakeHomeAssistant`` spricht das
WebSocket-Protokoll und die REST-API an der Protokollgrenze.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
import websockets

from obs.adapters.homeassistant import adapter as ha_adapter
from obs.adapters.homeassistant.adapter import HaActionError, HaNotConnectedError, HomeAssistantAdapter
from tests.adapters.conftest import make_binding
from tests.adapters.ha_fake import FakeHomeAssistant, settle

TOKEN = "secret-token"


def _events(bus) -> list:
    return [c.args[0] for c in bus.publish.await_args_list if hasattr(c.args[0], "datapoint_id")]


def _statuses(bus) -> list[str]:
    return [c.args[0].detail_code for c in bus.publish.await_args_list if hasattr(c.args[0], "detail_code")]


def _values(bus, binding) -> list[tuple]:
    return [(e.value, e.quality) for e in _events(bus) if e.binding_id == binding.id]


@pytest.fixture
def fake_ha() -> FakeHomeAssistant:
    return FakeHomeAssistant(
        {
            "sensor.temp": {"state": "21.5", "attributes": {"unit_of_measurement": "°C", "friendly_name": "Temp"}},
            "lock.shed": {"state": "locked", "attributes": {"friendly_name": "Shed"}},
            "cover.roof": {"state": "open", "attributes": {"current_position": 40, "supported_features": 15}},
            "light.desk": {"state": "on", "attributes": {"brightness": 128}},
        },
        token=TOKEN,
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)
        await asyncio.sleep(0)

    monkeypatch.setattr(ha_adapter, "_sleep", fake_sleep)
    return recorded


@pytest_asyncio.fixture(loop_scope="function")
async def adapter(monkeypatch, mock_bus, fake_ha, sleeps):
    monkeypatch.setattr(websockets, "connect", fake_ha.connect)
    monkeypatch.setattr(ha_adapter.httpx, "AsyncClient", fake_ha.http_client_factory())
    a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "ha.local", "port": 8123, "token": TOKEN, "ssl": False})
    await a.connect()
    await settle()
    yield a
    await a.disconnect()


async def _bind(adapter, *bindings) -> None:
    await adapter.reload_bindings(list(bindings))
    await settle()


# ---------------------------------------------------------------------------
# Lifecycle / status
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_without_token_publishes_error_status(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "ha.local", "token": ""})
        await a.connect()
        assert a.last_detail_code == "noToken"
        assert a._ws_task is None

    async def test_connect_without_websockets_package(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "x"})
        with patch.dict(sys.modules, {"websockets": None}):
            await a.connect()
        assert a.last_detail_code == "libNotInstalled"
        assert a.connected is False

    async def test_connected_only_after_authentication(self, adapter, mock_bus):
        statuses = _statuses(mock_bus)
        assert statuses[:2] == ["connecting", "connectedHa"]
        assert adapter.connected is True
        assert adapter.last_detail_params == {"version": "2025.10.1"}

    async def test_ws_uri_uses_wss_with_ssl(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "h", "port": 1, "token": "t", "ssl": True})
        a._cfg = a.config_schema(**a._config)
        assert a._ws_uri() == "wss://h:1/api/websocket"

    async def test_token_never_logged(self, adapter, caplog, fake_ha):
        caplog.set_level("DEBUG")
        fake_ha.socket.drop()
        await settle()
        assert TOKEN not in caplog.text

    async def test_disconnect_clears_state(self, adapter, mock_bus):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        await adapter.disconnect()
        assert adapter._ws_task is None
        assert adapter._http_client is None
        assert adapter._conn is None
        assert adapter._entity_map == {}
        assert adapter.last_detail_code == "disconnected"

    async def test_reload_before_connect_is_noop(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        await a.reload_bindings([make_binding({"entity_id": "sensor.temp"})])
        assert a._entity_map == {}


class TestReconnect:
    async def test_auth_invalid_waits_max_backoff(self, monkeypatch, mock_bus, fake_ha, sleeps):
        monkeypatch.setattr(websockets, "connect", fake_ha.connect)
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "h", "token": "wrong"})
        await a.connect()
        await settle()
        await a.disconnect()
        assert "tokenInvalid" in _statuses(mock_bus)
        assert sleeps[0] == ha_adapter.BACKOFF_MAX

    async def test_connect_errors_back_off_exponentially_and_bounded(self, monkeypatch, mock_bus, fake_ha, sleeps):
        fake_ha.connect_errors = [OSError("refused")] * 8
        monkeypatch.setattr(websockets, "connect", fake_ha.connect)
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "h", "token": TOKEN})
        await a.connect()
        await settle(80)
        assert a.connected is True
        await a.disconnect()
        assert sleeps[:8] == [1, 2, 4, 8, 16, 32, 60, 60]
        assert "wsErrorReconnecting" in _statuses(mock_bus)

    async def test_protocol_error_reconnects(self, monkeypatch, mock_bus, fake_ha, sleeps):
        class BadFirst(FakeHomeAssistant):
            async def connect(self, uri, **kw):
                sock = await super().connect(uri, **kw)
                if len(self.sockets) == 1:
                    sock._queue = asyncio.Queue()
                    sock.push({"type": "hello"})
                return sock

        bad = BadFirst(token=TOKEN)
        monkeypatch.setattr(websockets, "connect", bad.connect)
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"host": "h", "token": TOKEN})
        await a.connect()
        await settle(40)
        assert a.connected is True
        await a.disconnect()
        assert len(bad.sockets) == 2

    async def test_reconnect_resubscribes_and_reconciles(self, adapter, mock_bus, fake_ha, sleeps):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        # a change HA never reported before the connection broke
        fake_ha.states["sensor.temp"]["state"] = "23"
        fake_ha.socket.drop()
        await settle(40)
        assert _values(mock_bus, b) == [(21.5, "good"), (21.5, "bad"), (23, "good")]
        assert len(fake_ha.sockets) == 2
        sub = [m for m in fake_ha.socket.sent if m["type"] == "subscribe_entities"]
        assert sub[0]["entity_ids"] == ["sensor.temp"]
        assert sleeps == [1]
        assert "wsErrorReconnecting" in _statuses(mock_bus)

    async def test_backoff_resets_only_after_a_stable_connection(self, monkeypatch, adapter, fake_ha, sleeps):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        fake_ha.socket.drop()
        await settle(40)
        fake_ha.socket.drop()
        await settle(40)
        assert sleeps == [1, 2]
        monkeypatch.setattr(ha_adapter, "STABLE_AFTER", 0)
        fake_ha.socket.drop()
        await settle(40)
        assert sleeps == [1, 2, 1]

    async def test_recovered_unchanged_value_is_not_propagated(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        fake_ha.socket.drop()
        await settle(40)
        fake_ha.set_state("sensor.temp", "unknown")
        await settle()
        fake_ha.set_state("sensor.temp", "21.5")
        await settle()
        fake_ha.set_state("sensor.temp", "22")
        await settle()
        flags = [(e.value, e.quality, e.suppress_write_propagation, e.suppress_action_triggers) for e in _events(mock_bus) if e.binding_id == b.id]
        assert flags == [
            (21.5, "good", False, False),  # first value of a new binding
            (21.5, "bad", True, True),  # degraded: same value, only the quality changes
            (21.5, "good", True, True),  # reconnect snapshot: same value
            (21.5, "uncertain", True, True),
            (21.5, "good", True, True),  # back from unknown: same value
            (22, "good", False, False),  # a real change propagates
        ]

    async def test_value_restored_at_startup_is_not_propagated_again(self, monkeypatch, adapter, mock_bus):
        b = make_binding({"entity_id": "sensor.temp"})
        registry = MagicMock()
        registry.get_value = lambda dp_id: MagicMock(value=21.5)
        monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
        await _bind(adapter, b)
        (event,) = [e for e in _events(mock_bus) if e.binding_id == b.id]
        assert (event.value, event.quality, event.suppress_write_propagation) == (21.5, "good", True)

    async def test_unanswered_ping_reconnects(self, monkeypatch, adapter, fake_ha):
        monkeypatch.setattr(ha_adapter, "PING_INTERVAL", 0.01)
        monkeypatch.setattr(ha_adapter, "PING_TIMEOUT", 0.01)
        # restart the supervisor so the patched intervals apply
        await adapter.disconnect()
        await adapter.connect()
        await settle()
        fake_ha.answer_pings = False
        await asyncio.sleep(0.1)
        assert len(fake_ha.sockets) >= 3
        fake_ha.answer_pings = True

    async def test_answered_ping_keeps_connection(self, monkeypatch, adapter, fake_ha):
        monkeypatch.setattr(ha_adapter, "PING_INTERVAL", 0.01)
        await adapter.disconnect()
        await adapter.connect()
        await settle()
        await asyncio.sleep(0.05)
        assert len(fake_ha.sockets) == 2
        assert any(m["type"] == "ping" for m in fake_ha.socket.sent)


# ---------------------------------------------------------------------------
# Subscription / inbound values
# ---------------------------------------------------------------------------


class TestSubscription:
    async def test_new_binding_gets_initial_value_and_only_bound_entities_subscribed(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        assert _values(mock_bus, b) == [(21.5, "good")]
        sub = [m for m in fake_ha.socket.sent if m["type"] == "subscribe_entities"]
        assert sub == [{"type": "subscribe_entities", "entity_ids": ["sensor.temp"], "id": sub[0]["id"]}]

    async def test_dest_and_invalid_bindings_not_subscribed(self, adapter, fake_ha):
        await _bind(adapter, make_binding({"entity_id": "lock.shed"}, direction="DEST"), make_binding({"no_entity": 1}))
        assert not [m for m in fake_ha.socket.sent if m["type"] == "subscribe_entities"]

    async def test_same_entity_set_publishes_from_cache(self, adapter, mock_bus, fake_ha):
        b1 = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b1)
        b2 = make_binding({"entity_id": "sensor.temp", "attribute": "unit_of_measurement"})
        await _bind(adapter, b1, b2)
        assert _values(mock_bus, b2) == [("°C", "good")]
        assert len([m for m in fake_ha.socket.sent if m["type"] == "subscribe_entities"]) == 1

    async def test_reload_while_snapshot_pending_waits_for_it(self, adapter, mock_bus):
        b1 = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b1)
        adapter._snapshot_expected = {"sensor.temp"}  # snapshot of a resubscription still in flight
        b2 = make_binding({"entity_id": "sensor.temp", "attribute": "unit_of_measurement"})
        await _bind(adapter, b1, b2)
        assert _values(mock_bus, b2) == []
        await adapter._complete_snapshot(set())
        assert _values(mock_bus, b2) == [("°C", "good")]

    async def test_changed_entity_set_resubscribes(self, adapter, fake_ha):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        await _bind(adapter, make_binding({"entity_id": "lock.shed"}))
        types = [m["type"] for m in fake_ha.socket.sent]
        assert types.count("subscribe_entities") == 2
        assert "unsubscribe_events" in types
        assert fake_ha.socket.sent[-1]["entity_ids"] == ["lock.shed"]

    async def test_removing_all_bindings_unsubscribes(self, adapter, fake_ha):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        await _bind(adapter)
        assert fake_ha.socket.sent[-1]["type"] == "unsubscribe_events"
        assert fake_ha.socket.subscriptions == {}

    async def test_state_change_published_and_unrelated_attribute_change_deduplicated(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        fake_ha.set_state("sensor.temp", "22")
        await settle()
        fake_ha.set_state("sensor.temp", "22", {"unit_of_measurement": "°C", "friendly_name": "Renamed"})
        await settle()
        assert _values(mock_bus, b) == [(21.5, "good"), (22, "good")]

    async def test_attribute_only_change(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "light.desk", "attribute": "brightness"})
        await _bind(adapter, b)
        sub_id = next(iter(fake_ha.socket.subscriptions))
        fake_ha.socket.push({"id": sub_id, "type": "event", "event": {"c": {"light.desk": {"+": {"a": {"brightness": 5}}}}}})
        await settle()
        assert _values(mock_bus, b) == [(128, "good"), (5, "good")]

    async def test_closed_connection_defers_subscription_to_supervisor(self, adapter, fake_ha):
        adapter._conn._closed.set()
        await adapter.reload_bindings([make_binding({"entity_id": "sensor.temp"})])
        assert not [m for m in fake_ha.socket.sent if m["type"] == "subscribe_entities"]

    async def test_resubscribe_failure_without_connection(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        a._cfg = a.config_schema(**a._config)
        a._sync_subscription = AsyncMock(side_effect=RuntimeError("x"))
        await a.reload_bindings([make_binding({"entity_id": "sensor.temp"})])
        assert a.last_detail_code == "subscribeFailed"

    async def test_attribute_added_and_removed(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "light.desk", "attribute": "brightness"})
        await _bind(adapter, b)
        fake_ha.set_state("light.desk", "off", {})
        await settle()
        fake_ha.set_state("light.desk", "on", {"brightness": 10})
        await settle()
        assert _values(mock_bus, b) == [(128, "good"), (None, "good"), (10, "good")]

    async def test_quality_semantics(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        for state in ("unknown", "unavailable", "19"):
            fake_ha.set_state("sensor.temp", state)
            await settle()
        fake_ha.remove_entity("sensor.temp")
        await settle()
        assert _values(mock_bus, b) == [
            (21.5, "good"),
            (21.5, "uncertain"),
            (21.5, "bad"),
            (19, "good"),
            (19, "bad"),
        ]

    async def test_missing_entity_in_snapshot_is_bad(self, adapter, mock_bus):
        b = make_binding({"entity_id": "sensor.ghost"})
        await _bind(adapter, b)
        assert _values(mock_bus, b) == [(None, "bad")]

    async def test_entity_created_later(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.new"})
        await _bind(adapter, b)
        fake_ha.set_state("sensor.new", "5")
        await settle()
        assert _values(mock_bus, b) == [(None, "bad"), (5, "good")]

    async def test_lock_profile_transitional_and_fault_states(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "lock.shed", "profile": "lock.boolean_locked"}, direction="BOTH")
        await _bind(adapter, b)
        for state in ("unlocking", "unlocked", "locking", "jammed", "open", "weird"):
            fake_ha.set_state("lock.shed", state)
            await settle()
        assert _values(mock_bus, b) == [
            (True, "good"),
            (True, "uncertain"),
            (False, "good"),
            (False, "uncertain"),
            (False, "bad"),
            (False, "good"),
            (False, "uncertain"),
        ]

    async def test_formula_and_value_map_applied(self, adapter, mock_bus):
        b = make_binding({"entity_id": "sensor.temp"}, value_formula="x * 2", value_map={"43": "hot"})
        await _bind(adapter, b)
        assert _values(mock_bus, b) == [("hot", "good")]

    async def test_transform_error_skips_binding(self, adapter, mock_bus):
        b = make_binding({"entity_id": "sensor.temp"}, value_formula="x * 2")
        with patch("obs.core.formula.apply_formula", side_effect=ValueError("bad")):
            await _bind(adapter, b)
        assert _values(mock_bus, b) == []

    async def test_malformed_and_foreign_messages_ignored(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        sub_id = next(iter(fake_ha.socket.subscriptions))
        fake_ha.socket.push("not json")
        fake_ha.socket.push({"id": 999, "type": "event", "event": {"a": {}}})
        fake_ha.socket.push({"id": sub_id, "type": "event", "event": "nope"})
        fake_ha.socket.push({"id": sub_id, "type": "event", "event": {"c": {"sensor.unknown": {"+": {"s": "1"}}, "sensor.temp": "x"}}})
        fake_ha.socket.push([{"id": sub_id, "type": "event", "event": {"c": {"sensor.temp": {"+": {"s": "30"}}}}}, "junk"])
        fake_ha.socket.push({"id": sub_id, "type": "event", "event": {"a": {"sensor.temp": "not-a-state"}}})
        await settle()
        assert _values(mock_bus, b) == [(21.5, "good"), (30, "good")]
        assert adapter.connected is True

    async def test_subscribe_failure_drops_connection_with_growing_backoff(self, adapter, mock_bus, fake_ha, sleeps):
        fake_ha.subscribe_error = ("home_assistant_error", "boom")
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        await settle(80)
        assert "subscribeFailed" in _statuses(mock_bus)
        assert len(fake_ha.sockets) >= 3
        # a failure right after login must not reconnect every second
        assert sleeps[:3] == [1, 2, 4]

    async def test_invalid_format_falls_back_to_state_changed(self, adapter, mock_bus, fake_ha):
        fake_ha.subscribe_error = ("invalid_format", "extra keys not allowed @ data['entity_ids']")
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        assert adapter._sub_mode == "events"
        assert _values(mock_bus, b) == [(21.5, "good")]
        assert len(fake_ha.sockets) == 1

    async def test_malformed_entity_id_is_skipped_not_fatal(self, adapter, mock_bus, fake_ha):
        good = make_binding({"entity_id": " sensor.temp "})
        bad = make_binding({"entity_id": "sensor.my-temp"})
        await _bind(adapter, good, bad)
        assert _values(mock_bus, good) == [(21.5, "good")]
        assert fake_ha.socket.sent[-1]["entity_ids"] == ["sensor.temp"]
        assert len(fake_ha.sockets) == 1

    async def test_failed_resubscribe_closes_only_the_connection_it_used(self, adapter, fake_ha):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        adapter._ws_task.cancel()  # keep the supervisor out of this scenario
        with contextlib.suppress(asyncio.CancelledError):
            await adapter._ws_task
        adapter._ws_task = None
        used = adapter._conn
        newer = MagicMock(closed=False, close=AsyncMock())

        async def fail(conn):
            adapter._conn = newer  # the supervisor reconnected meanwhile
            raise RuntimeError("x")

        adapter._subscribe = fail
        await adapter.reload_bindings([make_binding({"entity_id": "lock.shed"})])
        assert used.closed
        newer.close.assert_not_awaited()
        adapter._conn = None

    async def test_dest_binding_reload_does_not_republish(self, adapter, mock_bus, fake_ha):
        src = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, src)
        await _bind(adapter, src, make_binding({"entity_id": "sensor.temp"}, direction="DEST"))
        await _bind(adapter, src, make_binding({"entity_id": "lock.shed"}))  # entity set changes
        assert _values(mock_bus, src) == [(21.5, "good")]

    async def test_changed_transformation_republishes(self, adapter, mock_bus):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        b.value_formula = "x * 2"
        await _bind(adapter, b)
        assert _values(mock_bus, b) == [(21.5, "good"), (43.0, "good")]

    async def test_restored_value_kept_when_ha_unreachable_after_restart(self, monkeypatch, mock_bus):
        b = make_binding({"entity_id": "sensor.temp"})
        other = make_binding({"entity_id": "sensor.other"})
        registry = MagicMock()
        registry.get_value = lambda dp_id: MagicMock(value=21.5) if dp_id == b.datapoint_id else None
        monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        a._cfg = a.config_schema(**a._config)
        await a.reload_bindings([b, other])
        await a._mark_connection_lost()
        assert _values(mock_bus, b) == [(21.5, "bad")]
        assert _values(mock_bus, other) == [(None, "bad")]

    async def test_binding_removed_during_connection_loss_is_skipped(self, adapter, mock_bus):
        b1, b2 = make_binding({"entity_id": "sensor.temp"}), make_binding({"entity_id": "lock.shed"})
        await _bind(adapter, b1, b2)
        adapter._mappings.pop(b2.id)
        await adapter._mark_connection_lost()
        assert _values(mock_bus, b2) == [("locked", "good")]


class TestStateChangedFallback:
    @pytest.fixture
    def fake_ha(self):
        return FakeHomeAssistant(
            {"sensor.temp": {"state": "21.5", "attributes": {}}, "sensor.other": {"state": "1", "attributes": {}}},
            token=TOKEN,
            subscribe_entities=False,
        )

    async def test_fallback_snapshot_events_and_removal(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        types = [m["type"] for m in fake_ha.socket.sent]
        assert "subscribe_events" in types and "get_states" in types
        fake_ha.set_state("sensor.other", "2")
        fake_ha.set_state("sensor.temp", "25")
        await settle()
        fake_ha.remove_entity("sensor.temp")
        await settle()
        assert _values(mock_bus, b) == [(21.5, "good"), (25, "good"), (25, "bad")]
        # a later resubscription goes straight to the fallback
        await _bind(adapter, make_binding({"entity_id": "sensor.other"}))
        assert [m["type"] for m in fake_ha.socket.sent].count("subscribe_entities") == 1

    async def test_events_during_the_snapshot_request_win_over_its_answer(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        command = adapter._conn.command

        async def command_then_event(payload, *args, **kwargs):
            result = await command(payload, *args, **kwargs)
            if payload["type"] == "get_states":
                # HA changes the entity right after answering; the event worker
                # applies it before the snapshot is merged.
                fake_ha.set_state("sensor.temp", "30")
                await settle()
            return result

        adapter._conn.command = command_then_event
        await _bind(adapter, b, make_binding({"entity_id": "sensor.other"}))
        assert _values(mock_bus, b) == [(21.5, "good"), (30, "good")]
        assert adapter._entity_cache["sensor.temp"]["state"] == "30"

    async def test_fallback_keeps_its_subscription_when_the_entity_set_changes(self, adapter, mock_bus, fake_ha):
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))
        other = make_binding({"entity_id": "sensor.other"})
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}), other)
        types = [m["type"] for m in fake_ha.socket.sent]
        assert types.count("subscribe_events") == 1
        assert "unsubscribe_events" not in types
        assert types.count("get_states") == 2  # a new snapshot covers the new entity
        assert _values(mock_bus, other) == [(1, "good")]
        await _bind(adapter)  # nothing bound any more: drop the subscription
        assert fake_ha.socket.sent[-1]["type"] == "unsubscribe_events"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class TestRead:
    async def test_read_state_attribute_and_unavailable(self, adapter, fake_ha):
        assert await adapter.read(make_binding({"entity_id": "sensor.temp"})) == 21.5
        assert await adapter.read(make_binding({"entity_id": "cover.roof", "profile": "cover.position"})) == 40
        fake_ha.set_state("sensor.temp", "unavailable")
        assert await adapter.read(make_binding({"entity_id": "sensor.temp"})) is None

    async def test_read_missing_entity_returns_none(self, adapter):
        assert await adapter.read(make_binding({"entity_id": "sensor.ghost"})) is None

    async def test_read_no_client_returns_none(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        assert await a.read(make_binding({"entity_id": "sensor.temp"})) is None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class TestWrite:
    @pytest.mark.parametrize(
        ("config", "value", "expected"),
        [
            ({"entity_id": "switch.fan"}, True, ("switch", "turn_on", {"entity_id": "switch.fan"})),
            ({"entity_id": "light.living_room"}, False, ("light", "turn_off", {"entity_id": "light.living_room"})),
            (
                {"entity_id": "input_number.t", "service_data_key": "value"},
                21.5,
                ("input_number", "set_value", {"entity_id": "input_number.t", "value": 21.5}),
            ),
            ({"entity_id": "input_text.t"}, "x", ("input_text", "set_value", {"entity_id": "input_text.t"})),
            ({"entity_id": "switch.fan", "service_name": "toggle"}, True, ("switch", "toggle", {"entity_id": "switch.fan"})),
            ({"entity_id": "switch.fan", "service_domain": "homeassistant"}, True, ("homeassistant", "turn_on", {"entity_id": "switch.fan"})),
            (
                {"entity_id": "input_datetime.t", "service_data_key": "time"},
                datetime.time(7, 30),
                ("input_datetime", "set_value", {"entity_id": "input_datetime.t", "time": "07:30:00"}),
            ),
            # legacy fields win over the profile (#1392): unchanged behaviour
            (
                {"entity_id": "lock.shed", "service_name": "lock", "profile": "lock.boolean_locked"},
                False,
                ("lock", "lock", {"entity_id": "lock.shed"}),
            ),
        ],
    )
    async def test_legacy_writes_unchanged(self, adapter, fake_ha, config, value, expected):
        await adapter.write(make_binding(config, direction="DEST"), value)
        assert fake_ha.service_calls == [expected]

    async def test_lock_profile_dispatches_by_boolean(self, adapter, fake_ha):
        b = make_binding({"entity_id": "lock.shed", "profile": "lock.boolean_locked"}, direction="BOTH")
        await adapter.write(b, True)
        await adapter.write(b, False)
        assert fake_ha.service_calls == [("lock", "lock", {"entity_id": "lock.shed"}), ("lock", "unlock", {"entity_id": "lock.shed"})]

    async def test_command_profile_ignores_false(self, adapter, fake_ha):
        b = make_binding({"entity_id": "scene.movie", "profile": "scene.activate"}, direction="DEST")
        await adapter.write(b, False)
        await adapter.write(b, True)
        assert fake_ha.service_calls == [("scene", "turn_on", {"entity_id": "scene.movie"})]

    async def test_read_only_profile_writes_nothing(self, adapter, fake_ha):
        await adapter.write(make_binding({"entity_id": "sensor.temp", "profile": "sensor.value"}, direction="BOTH"), 1)
        assert fake_ha.service_calls == []

    async def test_logical_value_selects_action_payload_uses_transformed_value(self, adapter, fake_ha):
        b = make_binding(
            {
                "entity_id": "select.mode",
                "write_actions": {
                    "1": {"domain": "select", "name": "select_option", "value_path": "option"},
                    "2": {"domain": "select", "name": "select_next"},
                },
            },
            direction="DEST",
        )
        assert await adapter.write_with_context(b, "eco", logical_value=1.0) is True
        await adapter.write_with_context(b, "x", logical_value=2)
        with pytest.raises(HaActionError, match="no action configured for value 3"):
            await adapter.write_with_context(b, "x", logical_value=3)
        assert fake_ha.service_calls == [
            ("select", "select_option", {"entity_id": "select.mode", "option": "eco"}),
            ("select", "select_next", {"entity_id": "select.mode"}),
        ]

    async def test_declarative_action_with_target_data_and_value_path(self, adapter, fake_ha):
        b = make_binding(
            {
                "entity_id": "cover.roof",
                "action": {
                    "domain": "cover",
                    "name": "set_cover_position",
                    "target": {"entity_id": ["cover.roof", "cover.roof2"]},
                    "data": {"extra.flag": True},
                    "value_path": "position",
                },
            },
            direction="DEST",
        )
        await adapter.write(b, 55)
        assert fake_ha.service_calls == [
            ("cover", "set_cover_position", {"entity_id": ["cover.roof", "cover.roof2"], "extra": {"flag": True}, "position": 55})
        ]

    async def test_ha_error_raises_and_reports_until_next_success(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "lock.shed", "profile": "lock.boolean_locked"}, direction="DEST")
        fake_ha.service_errors["lock.lock"] = (400, {"message": "Lock is jammed"})
        with pytest.raises(HaActionError, match="lock.lock for lock.shed failed: HTTP 400 Lock is jammed"):
            await adapter.write(b, True)
        assert adapter.last_detail_code == "haActionFailed"
        assert adapter.last_severity == "warning"
        assert adapter.connected is True
        await adapter.write(b, False)
        assert adapter.last_detail_code == "connectedHa"

    async def test_ha_error_without_json_message(self, adapter, fake_ha):
        fake_ha.service_errors["switch.turn_on"] = (500, "Internal Server Error")
        with pytest.raises(HaActionError, match=r"HTTP 500$"):
            await adapter.write(make_binding({"entity_id": "switch.fan"}), True)

    async def test_ha_error_with_non_object_json(self, adapter, fake_ha):
        fake_ha.service_errors["switch.turn_on"] = (400, ["x"])
        with pytest.raises(HaActionError, match=r"HTTP 400$"):
            await adapter.write(make_binding({"entity_id": "switch.fan"}), True)

    async def test_transport_error_raises(self, adapter):
        adapter._http_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(HaActionError, match="ConnectError"):
            await adapter.write(make_binding({"entity_id": "switch.fan"}), True)
        assert adapter.last_detail_code == "haActionFailed"

    async def test_success_after_failure_while_disconnected_keeps_status(self, adapter, fake_ha):
        fake_ha.service_errors["switch.turn_on"] = (400, {"message": "no"})
        with pytest.raises(HaActionError):
            await adapter.write(make_binding({"entity_id": "switch.fan"}), True)
        adapter._connected = False
        await adapter.write(make_binding({"entity_id": "switch.fan"}), False)
        assert adapter.last_detail_code == "haActionFailed"

    async def test_rejected_action_while_reconnecting_keeps_the_connection_status(self, adapter, fake_ha):
        await adapter._publish_status(False, "WebSocket Fehler — reconnecting", code="wsErrorReconnecting")
        fake_ha.service_errors["switch.turn_on"] = (400, {"message": "no"})
        with pytest.raises(HaActionError):
            await adapter.write(make_binding({"entity_id": "switch.fan"}), True)
        assert adapter.last_detail_code == "wsErrorReconnecting"

    async def test_success_never_clears_a_foreign_warning(self, adapter, fake_ha):
        await adapter._publish_status(True, "skipped", severity="warning", code="invalidBindingsSkipped")
        await adapter.write(make_binding({"entity_id": "switch.fan"}), True)
        assert adapter.last_detail_code == "invalidBindingsSkipped"

    async def test_write_without_client_raises(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        with pytest.raises(HaActionError):
            await a.write(make_binding({"entity_id": "switch.fan"}), True)


# ---------------------------------------------------------------------------
# Entity browser
# ---------------------------------------------------------------------------


class TestBrowse:
    async def test_browse_joins_registries_and_hides_sensitive_data(self, adapter, fake_ha):
        fake_ha.states["media_player.sonos"] = {
            "entity_id": "media_player.sonos",
            "state": "playing",
            "attributes": {
                "entity_picture": "/api/media_player_proxy/x?token=abc",
                "volume_level": 0.3,
                "source_list": ["a"],
                "friendly_name": "Sonos",
                "Code": "1234",
                "error_code": 0,
                "last_access": "today",
            },
        }
        fake_ha.entity_registry = [{"entity_id": "media_player.sonos", "platform": "sonos", "device_id": "d1", "unique_id": "RINCON_123"}, "junk"]
        fake_ha.device_registry = [
            {
                "id": "d1",
                "name": "Play:1",
                "name_by_user": "Kitchen speaker",
                "manufacturer": "Sonos",
                "model": "One",
                "area_id": "kitchen",
                "serial_number": "S1",
            }
        ]
        fake_ha.area_registry = [{"area_id": "kitchen", "name": "Küche"}]
        data = await adapter.browse_entities()
        assert data["metadata_available"] is True
        sonos = next(e for e in data["entities"] if e["entity_id"] == "media_player.sonos")
        # "code" is hidden only as the whole key; error_code / last_access are ordinary values
        assert sonos["attributes"] == {"volume_level": 0.3, "friendly_name": "Sonos", "error_code": 0, "last_access": "today"}
        assert sonos["device"] == "Kitchen speaker"
        assert sonos["area"] == "Küche"
        assert sonos["platform"] == "sonos"
        assert sonos["ha_uid"] and "RINCON" not in json.dumps(data)
        assert "S1" not in json.dumps(data)
        # cached
        sent = len(fake_ha.socket.sent)
        await adapter.browse_entities()
        assert len(fake_ha.socket.sent) == sent

    async def test_browse_without_admin_token_has_no_metadata(self, adapter, fake_ha):
        fake_ha.registries = False
        fake_ha.states["bogus"] = {"state": "x"}
        data = await adapter.browse_entities()
        assert data["metadata_available"] is False
        assert {e["entity_id"] for e in data["entities"]} == {"sensor.temp", "lock.shed", "cover.roof", "light.desk"}
        temp = next(e for e in data["entities"] if e["entity_id"] == "sensor.temp")
        assert temp["name"] == "Temp" and temp["unit"] == "°C" and temp["area"] is None

    async def test_browse_cache_keeps_metadata_flag(self, adapter, fake_ha):
        fake_ha.registries = False
        assert (await adapter.browse_entities())["metadata_available"] is False
        assert (await adapter.browse_entities())["metadata_available"] is False

    async def test_services_catalogue(self, adapter, fake_ha):
        fake_ha.services = {
            "lock": {
                "lock": {"name": "Lock", "fields": {"code": {"name": "Code", "required": False}}},
                "open": {"fields": {"advanced_fields": {"collapsed": True, "fields": {"code": {"required": True}}}, "bogus": "x"}},
                "weird": "not-a-dict",
            },
            "broken": ["x"],
            "cover": {"stop_cover": {"name": "", "fields": None}},
        }
        services = await adapter.browse_services()
        assert [d["domain"] for d in services] == ["cover", "lock"]
        lock = {s["service"]: s for s in services[1]["services"]}
        assert lock["lock"] == {"service": "lock", "name": "Lock", "fields": [{"key": "code", "name": "Code", "required": False}]}
        assert lock["open"]["fields"] == [{"key": "bogus", "name": None, "required": False}, {"key": "code", "name": None, "required": True}]
        assert lock["weird"]["fields"] == []
        assert services[0]["services"][0]["name"] is None
        sent = len(fake_ha.socket.sent)
        assert await adapter.browse_services() is services
        assert len(fake_ha.socket.sent) == sent

    async def test_services_non_dict_result(self, adapter, fake_ha):
        fake_ha.services = None
        assert await adapter.browse_services() == []

    async def test_services_not_connected(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        with pytest.raises(HaNotConnectedError):
            await a.browse_services()

    async def test_browse_passes_capabilities_separately(self, adapter, fake_ha):
        fake_ha.states["light.plain"] = {"entity_id": "light.plain", "state": "off", "attributes": {"supported_color_modes": ["onoff"]}}
        data = await adapter.browse_entities()
        plain = next(e for e in data["entities"] if e["entity_id"] == "light.plain")
        assert plain["capabilities"] == {"supported_color_modes": ["onoff"]}
        assert "supported_color_modes" not in plain["attributes"]  # not importable
        temp = next(e for e in data["entities"] if e["entity_id"] == "sensor.temp")
        assert temp["capabilities"] == {}

    async def test_browse_not_connected(self, mock_bus):
        a = HomeAssistantAdapter(event_bus=mock_bus, config={"token": "t"})
        with pytest.raises(HaNotConnectedError):
            await a.browse_entities()

    async def test_optional_command_non_list_result(self):
        conn = MagicMock()
        conn.command = AsyncMock(return_value={"x": 1})
        assert await HomeAssistantAdapter._optional_command(conn, "config/area_registry/list") is None


# ---------------------------------------------------------------------------
# Comparison with the DataPoint value (#1392 review)
# ---------------------------------------------------------------------------


class TestDataPointValue:
    def test_same_value_keeps_booleans_apart_from_numbers(self):
        assert ha_adapter._same_value(1, 1.0)
        assert ha_adapter._same_value(False, False)
        assert not ha_adapter._same_value(1, True)
        assert not ha_adapter._same_value(False, 0)
        assert not ha_adapter._same_value(True, False)

    async def test_a_number_turning_into_a_boolean_is_published(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        fake_ha.set_state("sensor.temp", "1")
        await _bind(adapter, b)
        fake_ha.set_state("sensor.temp", "on")
        await settle()
        values = [e.value for e in _events(mock_bus) if e.binding_id == b.id]
        assert values == [1, True] and type(values[1]) is bool

    async def test_degrade_and_recovery_use_the_current_datapoint_value(self, monkeypatch, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "lock.shed", "profile": "lock.boolean_locked"})
        values: dict = {}
        registry = MagicMock()
        registry.get_value = lambda dp_id: MagicMock(value=values[dp_id]) if dp_id in values else None
        monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
        await _bind(adapter, b)
        values[b.datapoint_id] = False  # another binding (e.g. KNX) changed the DataPoint
        fake_ha.set_state("lock.shed", "unavailable")
        await settle()
        fake_ha.set_state("lock.shed", "locked")
        await settle()
        flags = [(e.value, e.quality, e.suppress_write_propagation) for e in _events(mock_bus) if e.binding_id == b.id]
        assert flags == [
            (True, "good", False),
            (False, "bad", True),  # degrading keeps the DataPoint's value, not HA's last one
            (True, "good", False),  # HA's value differs from the DataPoint: propagate it
        ]

    async def test_connection_loss_never_retriggers_with_the_old_value(self, adapter, mock_bus, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        await _bind(adapter, b)
        await adapter._mark_connection_lost()
        (lost,) = [e for e in _events(mock_bus) if e.binding_id == b.id and e.quality == "bad"]
        assert (lost.value, lost.suppress_write_propagation, lost.suppress_action_triggers) == (21.5, True, True)


class TestReviewFollowUps:
    async def test_a_new_connection_retries_the_filtered_subscription(self, adapter, fake_ha):
        b = make_binding({"entity_id": "sensor.temp"})
        fake_ha.subscribe_error = ("invalid_format", "no filter")
        await _bind(adapter, b)
        assert adapter._sub_mode == "events"
        fake_ha.subscribe_error = None
        fake_ha.socket.drop()
        await settle(40)
        assert adapter._sub_mode == "entities"
        assert fake_ha.socket.sent[-1]["type"] == "subscribe_entities"

    async def test_failed_snapshot_request_ends_the_fresh_event_tracking(self, adapter, fake_ha):
        fake_ha.subscribe_error = ("invalid_format", "no filter")
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))

        async def failing(payload, *args, **kwargs):
            raise RuntimeError("timeout")

        conn = adapter._conn
        conn.command = failing
        adapter._sub_entities = None  # force a new snapshot on the existing event subscription
        with pytest.raises(RuntimeError):
            await adapter._subscribe(conn)
        assert adapter._fresh_events is None

    async def test_write_spec_is_cached_until_the_config_changes(self, monkeypatch, adapter, fake_ha):
        b = make_binding({"entity_id": "lock.shed", "profile": "lock.boolean_locked"}, direction="DEST")
        calls = []
        real = ha_adapter.resolve
        monkeypatch.setattr(ha_adapter, "resolve", lambda bc: calls.append(bc.entity_id) or real(bc))
        await adapter.write(b, True)
        await adapter.write(b, False)
        assert calls == ["lock.shed"]
        b.config = {"entity_id": "lock.door", "profile": "lock.boolean_locked"}
        await adapter.write(b, True)
        assert calls == ["lock.shed", "lock.door"]
        assert fake_ha.service_calls[-1] == ("lock", "lock", {"entity_id": "lock.door"})
        await _bind(adapter, b)
        assert b.id in adapter._write_specs  # still bound: kept
        await _bind(adapter, make_binding({"entity_id": "sensor.temp"}))  # b is no longer bound
        assert b.id not in adapter._write_specs
