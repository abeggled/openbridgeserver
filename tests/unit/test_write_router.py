from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from obs.adapters import registry as adapter_registry
from obs.core import write_router
from obs.core.write_router import WriteRouter


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
