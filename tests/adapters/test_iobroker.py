"""
Unit-Tests für den ioBroker Adapter.

Keine echte ioBroker-Instanz erforderlich — Socket.IO-Client wird gemockt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.core.event_bus import AdapterStatusEvent, DataValueEvent
from tests.adapters.conftest import make_binding
from obs.adapters.iobroker.adapter import IoBrokerAdapter, _coerce_iobroker_value


@pytest.fixture
def adapter(mock_bus):
    a = IoBrokerAdapter(
        event_bus=mock_bus,
        config={
            "host": "192.168.1.50",
            "port": 8084,
            "username": "obs",
            "password": "secret",
            "ssl": False,
            "path": "/socket.io",
        },
    )
    socket = MagicMock()
    socket.connected = True
    socket.call = AsyncMock()
    socket.disconnect = AsyncMock()
    a._socket = socket
    a._cfg = a.config_schema(**a._config)
    return a


class TestCoerceIoBrokerValue:
    def test_boolean_strings(self):
        assert _coerce_iobroker_value("true") is True
        assert _coerce_iobroker_value("false") is False
        assert _coerce_iobroker_value("on") is True
        assert _coerce_iobroker_value("off") is False

    def test_numbers(self):
        assert _coerce_iobroker_value("42") == 42
        assert _coerce_iobroker_value("22.5") == pytest.approx(22.5)

    def test_json_object(self):
        assert _coerce_iobroker_value('{"val": 12}') == {"val": 12}


class TestCallSocket:
    @pytest.mark.asyncio
    async def test_callback_tuple_returns_value(self, adapter):
        adapter._socket.call = AsyncMock(return_value=[None, {"val": 21.5}])

        result = await adapter._call_socket("getState", "foo")

        assert result == {"val": 21.5}
        adapter._socket.call.assert_awaited_once_with("getState", "foo", timeout=10.0)

    @pytest.mark.asyncio
    async def test_callback_error_raises(self, adapter):
        adapter._socket.call = AsyncMock(return_value=["not allowed", None])

        with pytest.raises(RuntimeError):
            await adapter._call_socket("getState", "foo")

    def test_connected_property_uses_engineio_state(self, adapter):
        adapter._socket.connected = False
        adapter._socket.eio = MagicMock()
        adapter._socket.eio.state = "connected"

        assert adapter.connected is True


class TestRead:
    @pytest.mark.asyncio
    async def test_get_state_extracts_val(self, adapter):
        binding = make_binding({"state_id": "0_userdata.0.temp"})
        adapter._socket.call = AsyncMock(return_value=[None, {"val": 21.5, "ack": True}])

        value = await adapter.read(binding)

        assert value == pytest.approx(21.5)
        adapter._socket.call.assert_awaited_once_with("getState", "0_userdata.0.temp", timeout=10.0)

    @pytest.mark.asyncio
    async def test_get_state_text_is_coerced(self, adapter):
        binding = make_binding({"state_id": "hm-rpc.0.foo.STATE"})
        adapter._socket.call = AsyncMock(return_value=[None, "true"])

        value = await adapter.read(binding)

        assert value is True


class TestStateChange:
    @pytest.mark.asyncio
    async def test_state_change_publishes_bound_state(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.temp", "source_data_type": "float"})
        adapter._state_map["0_userdata.0.temp"] = [binding]

        await adapter._on_state_change_event("0_userdata.0.temp", {"val": "21.75"})

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(21.75)
        assert event.quality == "good"
        assert event.datapoint_id == binding.datapoint_id
        assert event.source_adapter == "IOBROKER"

    @pytest.mark.asyncio
    async def test_state_change_send_on_change_skips_duplicate_values(self, adapter, mock_bus):
        binding = make_binding(
            {"state_id": "0_userdata.0.light", "source_data_type": "bool"},
            send_on_change=True,
        )
        adapter._state_map["0_userdata.0.light"] = [binding]

        await adapter._on_state_change_event("0_userdata.0.light", {"val": True})
        await adapter._on_state_change_event("0_userdata.0.light", {"val": True})
        await adapter._on_state_change_event("0_userdata.0.light", {"val": False})

        assert mock_bus.publish.call_count == 2
        assert mock_bus.publish.await_args_list[0].args[0].value is True
        assert mock_bus.publish.await_args_list[1].args[0].value is False

    @pytest.mark.asyncio
    async def test_state_change_min_delta_skips_small_numeric_changes(self, adapter, mock_bus):
        binding = make_binding(
            {"state_id": "0_userdata.0.temp", "source_data_type": "float"},
            send_min_delta=1.0,
        )
        adapter._state_map["0_userdata.0.temp"] = [binding]

        await adapter._on_state_change_event("0_userdata.0.temp", {"val": 20.0})
        await adapter._on_state_change_event("0_userdata.0.temp", {"val": 20.5})
        await adapter._on_state_change_event("0_userdata.0.temp", {"val": 21.1})

        assert mock_bus.publish.call_count == 2
        assert mock_bus.publish.await_args_list[0].args[0].value == pytest.approx(20.0)
        assert mock_bus.publish.await_args_list[1].args[0].value == pytest.approx(21.1)

    @pytest.mark.asyncio
    async def test_unknown_state_ignored(self, adapter, mock_bus):
        await adapter._on_state_change_event("unknown.state", {"val": 1})

        mock_bus.publish.assert_not_called()


class TestSubscribe:
    @staticmethod
    def _data_events(mock_bus):
        return [call.args[0] for call in mock_bus.publish.await_args_list if isinstance(call.args[0], DataValueEvent)]

    @pytest.mark.asyncio
    async def test_subscribe_uses_state_list_and_initial_read(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.temp"})
        adapter._state_map["0_userdata.0.temp"] = [binding]
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, None],  # subscribe
                [None, {"val": 22.0}],  # getState initial read
            ]
        )

        await adapter._subscribe_bound_states()

        assert adapter._socket.call.await_args_list[0].args == (
            "subscribe",
            ["0_userdata.0.temp"],
        )
        event = self._data_events(mock_bus)[-1]
        assert event.value == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_watchdog_resync_skips_unchanged_initial_values(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.temp"})
        adapter._state_map["0_userdata.0.temp"] = [binding]
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, None],
                [None, {"val": 22.0}],
                [None, None],
                [None, {"val": 22.0}],
            ]
        )

        await adapter._subscribe_bound_states(force_publish_initial=True)
        await adapter._subscribe_bound_states(force_publish_initial=False)

        assert len(self._data_events(mock_bus)) == 1

    @pytest.mark.asyncio
    async def test_watchdog_resync_publishes_changed_initial_values(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.temp"})
        adapter._state_map["0_userdata.0.temp"] = [binding]
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, None],
                [None, {"val": 22.0}],
                [None, None],
                [None, {"val": 23.0}],
            ]
        )

        await adapter._subscribe_bound_states(force_publish_initial=True)
        await adapter._subscribe_bound_states(force_publish_initial=False)

        assert len(self._data_events(mock_bus)) == 2
        event = self._data_events(mock_bus)[-1]
        assert event.value == pytest.approx(23.0)

    @pytest.mark.asyncio
    async def test_initial_read_seeds_source_filter_state(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.light"}, send_on_change=True)
        adapter._state_map["0_userdata.0.light"] = [binding]
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, None],
                [None, {"val": True}],
            ]
        )

        await adapter._subscribe_bound_states(force_publish_initial=True)
        await adapter._on_state_change_event("0_userdata.0.light", {"val": True})

        assert len(self._data_events(mock_bus)) == 1

    @pytest.mark.asyncio
    async def test_subscribe_marks_connected_when_engineio_is_connected(self, adapter, mock_bus):
        binding = make_binding({"state_id": "0_userdata.0.temp"})
        adapter._state_map["0_userdata.0.temp"] = [binding]
        adapter._socket.connected = False
        adapter._socket.eio = MagicMock()
        adapter._socket.eio.state = "connected"
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, None],
                [None, {"val": 22.0}],
            ]
        )

        await adapter._subscribe_bound_states(force_publish_initial=True)

        status_events = [call.args[0] for call in mock_bus.publish.await_args_list if isinstance(call.args[0], AdapterStatusEvent)]
        assert status_events
        assert status_events[-1].connected is True


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_loop_retries_until_connect_succeeds(self, adapter, monkeypatch):
        adapter._disconnect_requested = False
        adapter._cfg = adapter.config_schema(**{**adapter._config, "reconnect_interval_seconds": 1})
        adapter._socket.connected = False
        adapter._connect_socket = AsyncMock(side_effect=[False, False, True])
        sleep_mock = AsyncMock()
        monkeypatch.setattr("obs.adapters.iobroker.adapter.asyncio.sleep", sleep_mock)

        await adapter._reconnect_loop()

        assert adapter._connect_socket.await_count == 3
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_reconnect_loop_stops_when_disconnect_requested(self, adapter):
        adapter._disconnect_requested = True
        adapter._cfg = adapter.config_schema(**adapter._config)
        adapter._connect_socket = AsyncMock()

        await adapter._reconnect_loop()

        adapter._connect_socket.assert_not_called()


class TestBrowseStates:
    @pytest.mark.asyncio
    async def test_short_query_prefers_iobroker_namespace(self, adapter):
        adapter._socket.call = AsyncMock(
            side_effect=[
                [
                    None,
                    {
                        "rows": [
                            {
                                "id": "hue.0.SZ_Hue_white_lamp_1.on",
                                "value": {
                                    "common": {
                                        "name": "Lamp on",
                                        "type": "boolean",
                                        "role": "switch.light",
                                        "write": True,
                                    }
                                },
                            },
                        ]
                    },
                ],
                [
                    None,
                    {
                        "rows": [
                            {
                                "id": "system.adapter.hue.0.alive",
                                "value": {
                                    "common": {
                                        "name": "hue alive",
                                        "type": "boolean",
                                        "role": "indicator.state",
                                        "write": False,
                                    }
                                },
                            },
                        ]
                    },
                ],
                [None, {"val": False}],
                [None, {"val": True}],
            ]
        )

        result = await adapter.browse_states("hue", 10)

        assert [item["id"] for item in result] == [
            "hue.0.SZ_Hue_white_lamp_1.on",
            "system.adapter.hue.0.alive",
        ]
        first_options = adapter._socket.call.await_args_list[0].args[1][2]
        assert first_options["startkey"] == "hue."

    @pytest.mark.asyncio
    async def test_short_query_falls_back_to_full_text(self, adapter):
        adapter._socket.call = AsyncMock(
            side_effect=[
                [None, {"rows": []}],
                [
                    None,
                    {
                        "rows": [
                            {
                                "id": "system.adapter.hue.0.alive",
                                "value": {
                                    "common": {
                                        "name": "hue alive",
                                        "type": "boolean",
                                        "role": "indicator.state",
                                    }
                                },
                            },
                        ]
                    },
                ],
                [None, {"val": True}],
            ]
        )

        result = await adapter.browse_states("alive", 10)

        assert [item["id"] for item in result] == ["system.adapter.hue.0.alive"]
        assert adapter._socket.call.await_args_list[0].args[1][2]["startkey"] == "alive."
        assert adapter._socket.call.await_args_list[1].args[1][2]["startkey"] == ""


class TestWrite:
    @pytest.mark.asyncio
    async def test_set_state_write(self, adapter):
        binding = make_binding({"state_id": "0_userdata.0.light", "ack": False})
        adapter._socket.call = AsyncMock(return_value=[None, None])

        await adapter.write(binding, True)

        adapter._socket.call.assert_awaited_once_with(
            "setState",
            ("0_userdata.0.light", {"val": True, "ack": False}),
            timeout=10.0,
        )

    @pytest.mark.asyncio
    async def test_write_uses_command_state_id_and_ack(self, adapter):
        binding = make_binding(
            {
                "state_id": "device.0.light.STATE",
                "command_state_id": "device.0.light.SET",
                "ack": True,
            }
        )
        adapter._socket.call = AsyncMock(return_value=[None, None])

        await adapter.write(binding, "ON")

        adapter._socket.call.assert_awaited_once_with(
            "setState",
            ("device.0.light.SET", {"val": "ON", "ack": True}),
            timeout=10.0,
        )
