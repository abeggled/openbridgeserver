"""Unit tests for the MQTT adapter — _on_message and write() logic.
No broker connection; uses mocked bus and direct method calls.
"""

from __future__ import annotations

import asyncio
import datetime
import unittest.mock as mock

import pytest

from obs.adapters.mqtt.adapter import MqttAdapter, MqttAdapterConfig
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(mock_bus):
    a = MqttAdapter(event_bus=mock_bus, config={"host": "localhost", "port": 1883})
    return a


def _mock_create_task(coro, *, name=None, context=None):
    """Side-effect for patched asyncio.create_task — closes the coroutine so Python
    does not emit 'coroutine was never awaited' RuntimeWarnings during GC."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return mock.MagicMock()


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
        binding = make_binding({"topic": "t", "source_data_type": "json", "json_key": "temperature"})
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
        binding = make_binding(
            {
                "topic": "home/light",
                "payload_template": '{"state": "###DP###"}',
            },
        )
        await adapter.write(binding, "on")

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == '{"state": "on"}'

    @pytest.mark.asyncio
    async def test_write_with_payload_template_non_string_value(self, adapter):
        binding = make_binding(
            {
                "topic": "home/dim",
                "payload_template": '{"brightness": ###DP###}',
            },
        )
        await adapter.write(binding, 75)

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == '{"brightness": 75}'

    @pytest.mark.asyncio
    async def test_write_publishes_raw_time_value_without_json_quotes(self, adapter):
        binding = make_binding({"topic": "clock/time"})
        await adapter.write(binding, datetime.time(10, 30, 0))

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == "10:30:00"

    @pytest.mark.asyncio
    async def test_write_payload_template_serializes_time_value(self, adapter):
        binding = make_binding(
            {
                "topic": "clock/time",
                "payload_template": '{"time": ###DP###}',
            },
        )
        await adapter.write(binding, datetime.time(10, 30, 0))

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == '{"time": "10:30:00"}'

    @pytest.mark.asyncio
    async def test_write_passes_through_pretransformed_value(self, adapter):
        # write_router applies value_map before calling adapter.write();
        # the adapter receives an already-transformed value and must not re-map it.
        binding = make_binding({"topic": "switch/set"})
        await adapter.write(binding, "ON")

        _, payload, _ = await adapter._publish_queue.get()
        assert payload == "ON"


# ---------------------------------------------------------------------------
# client_id — ensures brokers with allow_zero_length_clientid false work
# ---------------------------------------------------------------------------


class TestClientId:
    def test_config_client_id_defaults_to_none(self):
        cfg = MqttAdapterConfig(host="broker.example.com")
        assert cfg.client_id is None

    def test_config_accepts_explicit_client_id(self):
        cfg = MqttAdapterConfig(host="broker.example.com", client_id="my-device-001")
        assert cfg.client_id == "my-device-001"

    @pytest.mark.asyncio
    async def test_connect_generates_nonempty_client_ids(self, mock_bus):
        """connect() must always set non-empty client IDs — zero-length IDs are rejected
        by brokers configured with allow_zero_length_clientid false."""
        adapter = MqttAdapter(event_bus=mock_bus, config={"host": "broker.example.com"})
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert adapter._pub_client_id
        assert adapter._sub_client_id
        assert len(adapter._pub_client_id) > 1
        assert len(adapter._sub_client_id) > 1

    @pytest.mark.asyncio
    async def test_connect_uses_configured_client_id_as_base(self, mock_bus):
        adapter = MqttAdapter(
            event_bus=mock_bus,
            config={"host": "broker.example.com", "client_id": "my-device"},
        )
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert adapter._pub_client_id == "my-device-pub"
        assert adapter._sub_client_id == "my-device-sub"

    @pytest.mark.asyncio
    async def test_pub_and_sub_ids_differ(self, mock_bus):
        """Publisher and subscriber must use different client IDs — a broker rejects
        two simultaneous connections with the same ID."""
        adapter = MqttAdapter(event_bus=mock_bus, config={"host": "broker.example.com"})
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert adapter._pub_client_id != adapter._sub_client_id


# ---------------------------------------------------------------------------
# TLS config
# ---------------------------------------------------------------------------


class TestTlsConfig:
    def test_tls_defaults_to_false(self):
        cfg = MqttAdapterConfig(host="broker.example.com")
        assert cfg.tls is False
        assert cfg.tls_insecure is False

    def test_tls_flag_accepted(self):
        cfg = MqttAdapterConfig(host="hivemq.cloud", port=8883, tls=True)
        assert cfg.tls is True

    @pytest.mark.asyncio
    async def test_connect_builds_tls_context_when_tls_enabled(self, mock_bus):
        adapter = MqttAdapter(
            event_bus=mock_bus,
            config={"host": "hivemq.cloud", "port": 8883, "tls": True},
        )
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        import ssl

        assert isinstance(adapter._tls_context, ssl.SSLContext)

    @pytest.mark.asyncio
    async def test_connect_no_tls_context_when_tls_disabled(self, mock_bus):
        adapter = MqttAdapter(event_bus=mock_bus, config={"host": "localhost"})
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert adapter._tls_context is None

    @pytest.mark.asyncio
    async def test_connect_tls_insecure_disables_cert_verification(self, mock_bus):
        import ssl

        adapter = MqttAdapter(
            event_bus=mock_bus,
            config={"host": "self-signed.example.com", "port": 8883, "tls": True, "tls_insecure": True},
        )
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert isinstance(adapter._tls_context, ssl.SSLContext)
        assert adapter._tls_context.check_hostname is False
        assert adapter._tls_context.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Connection status accuracy
# ---------------------------------------------------------------------------


class TestConnectionStatus:
    @pytest.mark.asyncio
    async def test_connect_does_not_report_connected_before_broker_responds(self, mock_bus):
        """connect() must not set connected=True — that happens only after the broker
        accepts the MQTT CONNECT packet (inside _publisher_loop)."""
        adapter = MqttAdapter(event_bus=mock_bus, config={"host": "broker.example.com"})
        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter.connect()

        assert adapter.connected is False


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


class TestRead:
    @pytest.mark.asyncio
    async def test_read_returns_none(self, adapter):
        binding = make_binding({"topic": "sensor/temp"})
        result = await adapter.read(binding)
        assert result is None


# ---------------------------------------------------------------------------
# disconnect()
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_cancels_pub_and_sub_tasks(self, adapter, mock_bus):
        async def sleeper():
            await asyncio.sleep(100)

        adapter._pub_task = asyncio.create_task(sleeper())
        adapter._sub_task = asyncio.create_task(sleeper())
        adapter._topic_map["t"] = [make_binding({"topic": "t"})]

        await adapter.disconnect()

        assert adapter._pub_task is None
        assert adapter._sub_task is None
        assert adapter._topic_map == {}
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_disconnect_without_tasks_does_not_raise(self, adapter):
        await adapter.disconnect()
        assert adapter._pub_task is None
        assert adapter._sub_task is None

    @pytest.mark.asyncio
    async def test_disconnect_with_only_pub_task(self, adapter):
        async def sleeper():
            await asyncio.sleep(100)

        adapter._pub_task = asyncio.create_task(sleeper())

        await adapter.disconnect()

        assert adapter._pub_task is None
        assert adapter._sub_task is None


# ---------------------------------------------------------------------------
# _on_bindings_reloaded()
# ---------------------------------------------------------------------------


class TestOnBindingsReloaded:
    @pytest.mark.asyncio
    async def test_returns_early_when_cfg_is_none(self, adapter):
        """_on_bindings_reloaded does nothing before connect() is called."""
        adapter._bindings = [make_binding({"topic": "t"})]
        await adapter._on_bindings_reloaded()
        assert adapter._topic_map == {}

    @pytest.mark.asyncio
    async def test_builds_topic_map_for_source_and_both(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="localhost")
        adapter._bindings = [
            make_binding({"topic": "src"}, direction="SOURCE"),
            make_binding({"topic": "both"}, direction="BOTH"),
            make_binding({"topic": "dst"}, direction="DEST"),
        ]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert "src" in adapter._topic_map
        assert "both" in adapter._topic_map
        assert "dst" not in adapter._topic_map

    @pytest.mark.asyncio
    async def test_skips_invalid_binding_config(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="localhost")
        bad = make_binding({})  # missing required 'topic' -> ValidationError
        good = make_binding({"topic": "ok"}, direction="SOURCE")
        adapter._bindings = [bad, good]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert "ok" in adapter._topic_map
        assert len(adapter._topic_map) == 1

    @pytest.mark.asyncio
    async def test_cancels_old_sub_task_on_reload(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="localhost")

        async def sleeper():
            await asyncio.sleep(100)

        old_task = asyncio.create_task(sleeper())
        adapter._sub_task = old_task
        adapter._bindings = [make_binding({"topic": "t"}, direction="SOURCE")]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert old_task.cancelled()

    @pytest.mark.asyncio
    async def test_no_sub_task_when_only_dest_bindings(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="localhost")
        adapter._bindings = [make_binding({"topic": "t"}, direction="DEST")]

        await adapter._on_bindings_reloaded()

        assert adapter._sub_task is None
        assert adapter._topic_map == {}

    @pytest.mark.asyncio
    async def test_multiple_bindings_same_topic_are_grouped(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="localhost")
        b1 = make_binding({"topic": "t"}, direction="SOURCE")
        b2 = make_binding({"topic": "t"}, direction="BOTH")
        adapter._bindings = [b1, b2]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert len(adapter._topic_map["t"]) == 2


# ---------------------------------------------------------------------------
# _on_message -- formula application
# ---------------------------------------------------------------------------


class TestOnMessageFormula:
    @pytest.mark.asyncio
    async def test_formula_applied_to_numeric_value(self, adapter, mock_bus):
        binding = make_binding(
            {"topic": "t", "source_data_type": "float"},
            value_formula="x * 2",
        )
        adapter._topic_map["t"] = [binding]
        await adapter._on_message("t", b"5.0")

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_formula_skipped_when_value_is_none(self, adapter, mock_bus):
        binding = make_binding({"topic": "t"}, value_formula="x * 2")
        adapter._topic_map["t"] = [binding]

        with mock.patch("obs.adapters.mqtt.adapter.apply_source_type", return_value=None):
            await adapter._on_message("t", b"5.0")

        event = mock_bus.publish.call_args[0][0]
        assert event.value is None


# ---------------------------------------------------------------------------
# _on_message -- exception handling in binding processing
# ---------------------------------------------------------------------------


class TestOnMessageExceptionHandling:
    @pytest.mark.asyncio
    async def test_exception_in_binding_processing_is_caught(self, adapter, mock_bus):
        bad = mock.MagicMock()
        bad.config = None  # MqttBindingConfig(**None) raises TypeError
        bad.value_formula = None
        bad.value_map = None
        adapter._topic_map["t"] = [bad]

        # Exception is caught; event is still published using the raw auto_value fallback.
        await adapter._on_message("t", b"data")

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.value == "data"  # json.loads("data") fails -> raw string


# ---------------------------------------------------------------------------
# write() -- exception handling
# ---------------------------------------------------------------------------


class TestWriteExceptionHandling:
    @pytest.mark.asyncio
    async def test_write_exception_is_caught(self, adapter):
        bad = make_binding({})  # missing topic -> Pydantic ValidationError

        await adapter.write(bad, "value")

        assert adapter._publish_queue.empty()


# ---------------------------------------------------------------------------
# _subscriber_loop
# ---------------------------------------------------------------------------


def _make_mock_aiomqtt_client(messages_gen):
    """Build an async-context-manager mock for aiomqtt.Client."""
    client = mock.AsyncMock()
    client.__aenter__ = mock.AsyncMock(return_value=client)
    client.__aexit__ = mock.AsyncMock(return_value=None)
    client.subscribe = mock.AsyncMock()
    client.messages = messages_gen
    return client


class TestSubscriberLoop:
    @pytest.mark.asyncio
    async def test_subscribes_and_dispatches_messages(self, adapter, mock_bus):
        adapter._cfg = MqttAdapterConfig(host="broker.test")
        adapter._sub_client_id = "obs-sub"
        _add_binding(adapter, "t")

        mock_msg = mock.MagicMock()
        mock_msg.topic.__str__ = lambda self: "t"
        mock_msg.payload = b"42"

        dispatched = asyncio.Event()
        original = adapter._on_message

        async def tracking(topic, payload):
            await original(topic, payload)
            dispatched.set()

        adapter._on_message = tracking

        async def fake_messages():
            yield mock_msg
            await asyncio.Event().wait()  # block until task is cancelled

        mock_client = _make_mock_aiomqtt_client(fake_messages())
        mock_aiomqtt = mock.MagicMock()
        mock_aiomqtt.Client.return_value = mock_client

        with mock.patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            task = asyncio.create_task(adapter._subscriber_loop())
            await asyncio.wait_for(dispatched.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert mock_bus.publish.called
        mock_client.subscribe.assert_called_once_with("t")

    @pytest.mark.asyncio
    async def test_reconnects_on_connection_error(self, adapter):
        adapter._cfg = MqttAdapterConfig(host="broker.test")
        adapter._sub_client_id = "obs-sub"

        attempt = 0

        class FailingCM:
            async def __aenter__(self_cm):
                nonlocal attempt
                attempt += 1
                if attempt >= 3:
                    raise asyncio.CancelledError()
                raise ConnectionRefusedError("broker down")

            async def __aexit__(self_cm, *args):
                pass

        mock_aiomqtt = mock.MagicMock()
        mock_aiomqtt.Client.return_value = FailingCM()

        with mock.patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            with mock.patch("asyncio.sleep", new_callable=mock.AsyncMock):
                task = asyncio.create_task(adapter._subscriber_loop())
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert attempt >= 3


# ---------------------------------------------------------------------------
# _publisher_loop
# ---------------------------------------------------------------------------


class TestPublisherLoop:
    @pytest.mark.asyncio
    async def test_publishes_message_from_queue(self, adapter, mock_bus):
        adapter._cfg = MqttAdapterConfig(host="broker.test")
        adapter._pub_client_id = "obs-pub"
        adapter._tls_context = None

        published = asyncio.Event()

        mock_client = mock.AsyncMock()
        mock_client.__aenter__ = mock.AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = mock.AsyncMock(return_value=None)

        async def track_publish(topic, payload, retain=False):
            published.set()

        mock_client.publish.side_effect = track_publish

        mock_aiomqtt = mock.MagicMock()
        mock_aiomqtt.Client.return_value = mock_client

        await adapter._publish_queue.put(("home/lamp", "on", False))

        with mock.patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            task = asyncio.create_task(adapter._publisher_loop())
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_client.publish.assert_called_once_with("home/lamp", "on", retain=False)

    @pytest.mark.asyncio
    async def test_publisher_reports_connected_status(self, adapter, mock_bus):
        adapter._cfg = MqttAdapterConfig(host="broker.test")
        adapter._pub_client_id = "obs-pub"
        adapter._tls_context = None

        connected_event = asyncio.Event()
        original_publish_status = adapter._publish_status

        async def track_status(connected, detail="", severity="ok"):
            if connected:
                connected_event.set()
            await original_publish_status(connected, detail, severity)

        adapter._publish_status = track_status

        mock_client = mock.AsyncMock()
        mock_client.__aenter__ = mock.AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = mock.AsyncMock(return_value=None)
        mock_client.publish = mock.AsyncMock()

        mock_aiomqtt = mock.MagicMock()
        mock_aiomqtt.Client.return_value = mock_client

        with mock.patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            task = asyncio.create_task(adapter._publisher_loop())
            await asyncio.wait_for(connected_event.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert adapter.connected is True

    @pytest.mark.asyncio
    async def test_publisher_reconnects_on_connection_error(self, adapter, mock_bus):
        adapter._cfg = MqttAdapterConfig(host="broker.test")
        adapter._pub_client_id = "obs-pub"
        adapter._tls_context = None

        attempt = 0

        class FailingCM:
            async def __aenter__(self_cm):
                nonlocal attempt
                attempt += 1
                if attempt >= 3:
                    raise asyncio.CancelledError()
                raise ConnectionRefusedError("publisher down")

            async def __aexit__(self_cm, *args):
                pass

        mock_aiomqtt = mock.MagicMock()
        mock_aiomqtt.Client.return_value = FailingCM()

        with mock.patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}):
            with mock.patch("asyncio.sleep", new_callable=mock.AsyncMock):
                task = asyncio.create_task(adapter._publisher_loop())
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert attempt >= 3
