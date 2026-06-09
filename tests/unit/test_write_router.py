from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from obs.adapters import registry as adapter_registry
from obs.adapters.mqtt.adapter import MqttAdapter
from obs.core.event_bus import DataValueEvent
from obs.core import write_router
from obs.core.write_router import WriteRouter, _cached_value_equals, _row_value, _to_cached_value
from tests.adapters.conftest import make_binding


class _FakeDb:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def fetchall(self, _query: str, _params: tuple):
        return self._rows


class _FakeInstance:
    def __init__(self):
        self.writes: list[object] = []

    async def write(self, _binding, value):
        self.writes.append(value)


def _row(**overrides):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "datapoint_id": str(uuid.uuid4()),
        "adapter_type": "MQTT",
        "adapter_instance_id": str(uuid.uuid4()),
        "direction": "DEST",
        "config": "{}",
        "enabled": 1,
        "send_throttle_ms": None,
        "send_on_change": 0,
        "send_min_delta": None,
        "send_min_delta_pct": None,
        "value_formula": None,
        "value_map": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _binding(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "adapter_instance_id": None,
        "adapter_type": "MQTT",
        "send_throttle_ms": None,
        "send_on_change": False,
        "send_min_delta": None,
        "send_min_delta_pct": None,
        "value_formula": None,
        "value_map": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_router(db_rows: list[dict]) -> WriteRouter:
    router = WriteRouter.__new__(WriteRouter)
    router._db = _FakeDb(db_rows)
    router._registry = None
    router._last_sent = {}
    router._last_value = {}
    return router


def _patch_registry(monkeypatch, binding, instance):
    monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda _row: binding)
    monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda _id: instance)
    monkeypatch.setattr(adapter_registry, "get_instance", lambda _adapter_type: instance)


@pytest.mark.asyncio
async def test_send_on_change_skips_duplicate_large_string(monkeypatch):
    binding = _binding(send_on_change=True)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])

    _patch_registry(monkeypatch, binding, instance)

    large_value = "X" * 12000
    dp_id = uuid.uuid4()

    await router._write_to_dest_bindings(dp_id, large_value, skip_binding_id=None)
    await router._write_to_dest_bindings(dp_id, large_value, skip_binding_id=None)

    assert len(instance.writes) == 1


def test_cached_value_helpers_cover_short_strings_and_digest_type_mismatch():
    cached = _to_cached_value("short")

    assert cached == "short"
    assert _cached_value_equals("short", cached) is True
    assert _cached_value_equals(42, ("__str_digest__", 5, "unused")) is False


@pytest.mark.asyncio
async def test_no_value_filters_does_not_keep_last_value_cache(monkeypatch):
    binding = _binding(send_on_change=False, send_min_delta=None, send_min_delta_pct=None)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])
    router._last_value[binding.id] = "stale"

    _patch_registry(monkeypatch, binding, instance)

    await router._write_to_dest_bindings(uuid.uuid4(), "value", skip_binding_id=None)

    assert binding.id not in router._last_value


@pytest.mark.asyncio
async def test_send_on_change_does_not_cache_full_large_object(monkeypatch):
    binding = _binding(send_on_change=True)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])

    _patch_registry(monkeypatch, binding, instance)

    large_obj = {"payload": "Y" * 25000}
    await router._write_to_dest_bindings(uuid.uuid4(), large_obj, skip_binding_id=None)

    cached = router._last_value[binding.id]
    assert cached is not large_obj


@pytest.mark.asyncio
async def test_send_on_change_skips_duplicate_large_object(monkeypatch):
    binding = _binding(send_on_change=True)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])
    _patch_registry(monkeypatch, binding, instance)

    value = {"payload": "Y" * 25000}
    await router._write_to_dest_bindings(uuid.uuid4(), value, skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), {"payload": "Y" * 25000}, skip_binding_id=None)

    assert len(instance.writes) == 1


@pytest.mark.asyncio
async def test_send_min_delta_skips_small_numeric_changes(monkeypatch):
    binding = _binding(send_min_delta=1.0)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])
    _patch_registry(monkeypatch, binding, instance)

    await router._write_to_dest_bindings(uuid.uuid4(), 10.0, skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), 10.5, skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), 11.2, skip_binding_id=None)

    assert len(instance.writes) == 2


@pytest.mark.asyncio
async def test_send_throttle_skips_second_write_within_interval(monkeypatch):
    binding = _binding(send_throttle_ms=1000)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])
    _patch_registry(monkeypatch, binding, instance)

    monotonic_values = iter([100.0, 100.1, 100.2, 101.5, 102.0])
    monkeypatch.setattr(
        write_router,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )

    await router._write_to_dest_bindings(uuid.uuid4(), "first", skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), "second", skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), "third", skip_binding_id=None)

    assert instance.writes == ["first", "third"]


@pytest.mark.asyncio
async def test_handle_value_event_forwards_skip_binding_id(monkeypatch):
    router = _make_router([])
    router._registry = SimpleNamespace(get=lambda _: SimpleNamespace(name="dp", data_type="UNKNOWN"))
    router._write_to_dest_bindings = AsyncMock()

    event = SimpleNamespace(
        datapoint_id=uuid.uuid4(),
        value=42,
        binding_id=uuid.uuid4(),
        quality="good",
    )
    await router.handle_value_event(event)

    router._write_to_dest_bindings.assert_awaited_once_with(
        event.datapoint_id,
        event.value,
        skip_binding_id=event.binding_id,
    )


@pytest.mark.asyncio
async def test_handle_uses_json_fallback_when_deserializer_fails(monkeypatch):
    dp_id = uuid.uuid4()
    router = _make_router([])
    router._registry = SimpleNamespace(get=lambda _dp_id: SimpleNamespace(name="dp", data_type="dummy"))
    router._write_to_dest_bindings = AsyncMock()

    def _failing_deserializer(_raw):
        raise ValueError("boom")

    fake_dt = SimpleNamespace(mqtt_deserializer=_failing_deserializer)
    monkeypatch.setattr("obs.models.types.DataTypeRegistry.get", lambda _dt: fake_dt)

    await router.handle(dp_id, '{"n": 7}')
    router._write_to_dest_bindings.assert_awaited_once_with(dp_id, {"n": 7}, skip_binding_id=None)


@pytest.mark.asyncio
async def test_handle_uses_raw_payload_when_deserializer_and_json_fallback_fail(monkeypatch):
    dp_id = uuid.uuid4()
    router = _make_router([])
    router._registry = SimpleNamespace(get=lambda _dp_id: SimpleNamespace(name="dp", data_type="dummy"))
    router._write_to_dest_bindings = AsyncMock()

    def _failing_deserializer(_raw):
        raise ValueError("boom")

    fake_dt = SimpleNamespace(mqtt_deserializer=_failing_deserializer)
    monkeypatch.setattr("obs.models.types.DataTypeRegistry.get", lambda _dt: fake_dt)

    await router.handle(dp_id, "not json")
    router._write_to_dest_bindings.assert_awaited_once_with(dp_id, "not json", skip_binding_id=None)


@pytest.mark.asyncio
async def test_time_value_event_routes_to_mqtt_raw_payload_without_template(monkeypatch):
    dp_id = uuid.uuid4()
    binding = make_binding({"topic": "clock/time"}, direction="DEST")
    binding.datapoint_id = dp_id
    binding.adapter_type = "MQTT"
    adapter = MqttAdapter(event_bus=AsyncMock(), config={"host": "localhost", "port": 1883})
    router = _make_router([{"id": str(binding.id)}])
    router._registry = SimpleNamespace(get=lambda _dp_id: SimpleNamespace(name="Clock", data_type="TIME"))
    _patch_registry(monkeypatch, binding, adapter)

    await router.handle_value_event(
        DataValueEvent(
            datapoint_id=dp_id,
            value=datetime.time(10, 30, 0),
            quality="good",
            source_adapter="KNX",
            binding_id=uuid.uuid4(),
        )
    )

    topic, payload, retain = await adapter._publish_queue.get()
    assert topic == "clock/time"
    assert payload == "10:30:00"
    assert retain is False


@pytest.mark.asyncio
async def test_write_router_skips_invalid_binding_row_and_writes_valid_row(monkeypatch):
    dp_id = uuid.uuid4()
    valid_binding_id = uuid.uuid4()
    rows = [
        _row(id=str(uuid.uuid4()), datapoint_id="not-a-uuid"),
        _row(id=str(valid_binding_id), datapoint_id=str(dp_id), adapter_instance_id=None),
    ]
    instance = _FakeInstance()
    router = _make_router(rows)
    monkeypatch.setattr(adapter_registry, "get_instance", lambda _adapter_type: instance)

    await router._write_to_dest_bindings(dp_id, "value", skip_binding_id=None)

    assert instance.writes == ["value"]


@pytest.mark.asyncio
async def test_send_min_delta_ignores_non_numeric_values(monkeypatch):
    binding = _binding(send_min_delta=1.0)
    instance = _FakeInstance()
    router = _make_router([{"id": str(binding.id)}])
    _patch_registry(monkeypatch, binding, instance)

    await router._write_to_dest_bindings(uuid.uuid4(), "old", skip_binding_id=None)
    await router._write_to_dest_bindings(uuid.uuid4(), "new", skip_binding_id=None)

    assert instance.writes == ["old", "new"]


def test_row_value_returns_none_for_rows_that_do_not_support_lookup():
    class BadRow:
        def __getitem__(self, _key):
            raise TypeError("unsupported")

    assert _row_value(BadRow(), "id") is None


@pytest.mark.asyncio
async def test_handle_value_event_records_type_mismatch_diagnostic():
    dp_id = uuid.uuid4()
    registry = SimpleNamespace(
        get=lambda _dp_id: SimpleNamespace(name="Status", data_type="FLOAT"),
        report_type_mismatch=AsyncMock(),
        clear_diagnostic=AsyncMock(),
    )
    router = _make_router([])
    router._registry = registry
    router._write_to_dest_bindings = AsyncMock()

    event = SimpleNamespace(
        datapoint_id=dp_id,
        value="online",
        binding_id=uuid.uuid4(),
        quality="good",
        source_adapter="MQTT",
    )
    await router.handle_value_event(event)

    registry.report_type_mismatch.assert_awaited_once_with(
        dp_id,
        expected="float",
        got="str",
        source_adapter="MQTT",
        value="online",
    )
    registry.clear_diagnostic.assert_not_awaited()
    router._write_to_dest_bindings.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_value_event_clears_type_mismatch_after_valid_value():
    dp_id = uuid.uuid4()
    registry = SimpleNamespace(
        get=lambda _dp_id: SimpleNamespace(name="Temperature", data_type="FLOAT"),
        report_type_mismatch=AsyncMock(),
        clear_diagnostic=AsyncMock(),
    )
    router = _make_router([])
    router._registry = registry
    router._write_to_dest_bindings = AsyncMock()

    event = SimpleNamespace(
        datapoint_id=dp_id,
        value=21.5,
        binding_id=uuid.uuid4(),
        quality="good",
        source_adapter="MQTT",
    )
    await router.handle_value_event(event)

    registry.clear_diagnostic.assert_awaited_once_with(dp_id, "type_mismatch")
    registry.report_type_mismatch.assert_not_awaited()
    router._write_to_dest_bindings.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_type_mismatch_noops_when_registry_has_no_reporter():
    router = _make_router([])
    router._registry = SimpleNamespace()
    event = SimpleNamespace(datapoint_id=uuid.uuid4(), value="online", source_adapter="MQTT")

    await router._report_type_mismatch(event, "float", "str")


@pytest.mark.asyncio
async def test_clear_type_mismatch_noops_when_registry_has_no_clearer():
    router = _make_router([])
    router._registry = SimpleNamespace()

    await router._clear_type_mismatch(uuid.uuid4())
