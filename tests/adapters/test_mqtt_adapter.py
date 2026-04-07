"""
Unit tests for the MQTT adapter — _on_message and write() logic.
No broker connection; uses mocked bus and direct method calls.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.adapters.conftest import make_binding
from obs.adapters.mqtt.adapter import MqttAdapter, MqttBindingConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter(mock_bus):
    a = MqttAdapter(event_bus=mock_bus, config={"host": "localhost", "port": 1883})
    return a


def _add_binding(adapter: MqttAdapter, topic: str, **kwargs) -> object:
    binding = make_binding({"topic": topic, **kwargs})
    adapter._topic_map.setdefault(topic, []).append(binding)
    return binding


# ---------------------------------------------------------------------------
# _on_message — auto-parse
# ---------------------------------------------------------------------------

class TestOnMessageAutoParse:
    @pytest.mark.asyncio
    async def test_numeric_json_payload(self, adapter, mock_bus):
        binding = _add_binding(adapter, "sensor/temp")
        await adapter._on_message("sensor/temp", b"21.5")

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.value == 21.5
        assert event.quality == "good"
        assert event.datapoint_id == binding.datapoint_id

    @pytest.mark.asyncio
    async def test_boolean_json_payload_true(self, adapter, mock_bus):
        _add_binding(adapter, "switch/state")
        await adapter._on_message("switch/state", b"true")

        event = mock_bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_boolean_json_payload_false(self, adapter, mock_bus):
        _add_binding(adapter, "switch/state")
        await adapter._on_message("switch/state", b"false")

        event = mock_bus.publish.call_args[0][0]
        assert event.value is False

    @pytest.mark.asyncio
    async def test_string_payload_not_json(self, adapter, mock_bus):
        _add_binding(adapter, "sensor/label")
        await adapter._on_message("sensor/label", b"hello world")

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "hello world"

    @pytest.mark.asyncio
    async def test_json_object_payload(self, adapter, mock_bus):
        _add_binding(adapter, "device/status")
        await adapter._on_message("device/status", b'{"state": "on", "power": 100}')

        event = mock_bus.publish.call_args[0][0]
        assert isinstance(event.value, dict)
        assert event.value["state"] == "on"


# ---------------------------------------------------------------------------
# _on_message — source_data_type coercion
# ---------------------------------------------------------------------------

class TestOnMessageSourceDataType:
    @pytest.mark.asyncio
    async def test_source_type_float(self, adapter, mock_bus):
        binding = make_binding({"topic": "t", "source_data_type": "float"})
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b"22.75")

        event = mock_bus.publish.call_args[0][0]
        assert isinstance(event.value, float)
        assert event.value == pytest.approx(22.75)

    @pytest.mark.asyncio
    async def test_source_type_int(self, adapter, mock_bus):
        binding = make_binding({"topic": "t", "source_data_type": "int"})
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b"42")

        event = mock_bus.publish.call_args[0][0]
        assert isinstance(event.value, int)
        assert event.value == 42

    @pytest.mark.asyncio
    async def test_source_type_string(self, adapter, mock_bus):
        binding = make_binding({"topic": "t", "source_data_type": "string"})
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b"99.5")

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "99.5"

    @pytest.mark.asyncio
    async def test_source_type_bool_on_off(self, adapter, mock_bus):
        binding = make_binding({"topic": "t", "source_data_type": "bool"})
        adapter._topic_map["t"] = [binding]

        await adapter._on_message("t", b"on")
        ev_on = mock_bus.publish.call_args[0][0]
        assert ev_on.value is True

        mock_bus.publish.reset_mock()
        await adapter._on_message("t", b"off")
        ev_off = mock_bus.publish.call_args[0][0]
        assert ev_off.value is False

    @pytest.mark.asyncio
    async def test_json_key_extraction(self, adapter, mock_bus):
        binding = make_binding(
            {"topic": "t", "source_data_type": "json", "json_key": "temperature"}
        )
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b'{"temperature": 23.1, "humidity": 55}')

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(23.1)


# ---------------------------------------------------------------------------
# _on_message — value_map
# ---------------------------------------------------------------------------

class TestOnMessageValueMap:
    @pytest.mark.asyncio
    async def test_value_map_applied(self, adapter, mock_bus):
        binding = make_binding(
            {"topic": "t"},
            value_map={"1": "on", "0": "off"},
        )
        adapter._topic_map["t"] = [binding]

        await adapter._on_message("t", b"1")
        event = mock_bus.publish.call_args[0][0]
        assert event.value == "on"

    @pytest.mark.asyncio
    async def test_value_map_no_match_passthrough(self, adapter, mock_bus):
        binding = make_binding(
            {"topic": "t"},
            value_map={"1": "on"},
        )
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b"99")

        event = mock_bus.publish.call_args[0][0]
        assert event.value == 99  # auto-parsed as int, no map match


# ---------------------------------------------------------------------------
# _on_message — unknown topic
# ---------------------------------------------------------------------------

class TestOnMessageUnknownTopic:
    @pytest.mark.asyncio
    async def test_unknown_topic_no_event(self, adapter, mock_bus):
        await adapter._on_message("completely/unknown", b"data")
        mock_bus.publish.assert_not_called()


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------

class TestWrite:
    @pytest.mark.asyncio
    async def test_write_queues_topic_and_payload(self, adapter):
        binding = make_binding({"topic": "actuator/lamp"})
        await adapter.write(binding, True)

        assert not adapter._publish_queue.empty()
        topic, payload, retain = await adapter._publish_queue.get()
        assert topic == "actuator/lamp"
        assert payload == "true"  # json.dumps(True) == "true"
        assert retain is False

    @pytest.mark.asyncio
    async def test_write_uses_publish_topic_when_set(self, adapter):
        binding = make_binding({"topic": "sub/topic", "publish_topic": "pub/topic"})
        await adapter.write(binding, 42)

        topic, _, _ = await adapter._publish_queue.get()
        assert topic == "pub/topic"

    @pytest.mark.asyncio
    async def test_write_with_retain_flag(self, adapter):
        binding = make_binding({"topic": "sensor/val", "retain": True})
        await adapter.write(binding, 100)

        _, _, retain = await adapter._publish_queue.get()
        assert retain is True

    @pytest.mark.asyncio
    async def test_write_with_payload_template(self, adapter):
        binding = make_binding({
            "topic": "home/light",
            "payload_template": '{"state": "###DP###"}',
        })
        await adapter.write(binding, "on")

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == '{"state": "on"}'

    @pytest.mark.asyncio
    async def test_write_with_payload_template_non_string_value(self, adapter):
        binding = make_binding({
            "topic": "home/dim",
            "payload_template": '{"brightness": ###DP###}',
        })
        await adapter.write(binding, 75)

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == '{"brightness": 75}'

    @pytest.mark.asyncio
    async def test_write_with_value_map(self, adapter):
        # apply_value_map uses str(value).lower() for booleans → key must be lowercase
        binding = make_binding(
            {"topic": "switch/set"},
            value_map={"true": "ON", "false": "OFF"},
        )
        await adapter.write(binding, True)

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == "ON"
