import uuid

import pytest

from obs.core.event_bus import DataValueEvent
from obs.core.write_router import WriteRouter


class _Registry:
    def __init__(self, data_type: str = "BOOLEAN") -> None:
        self.data_type = data_type

    def get(self, _dp_id):
        return type("DP", (), {"data_type": self.data_type, "name": "dp"})()


@pytest.mark.anyio
async def test_handle_value_event_skips_bad_quality(monkeypatch):
    router = WriteRouter(db=None, registry=_Registry())
    called = False

    async def _fake_write(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(router, "_write_to_dest_bindings", _fake_write)

    event = DataValueEvent(
        datapoint_id=uuid.uuid4(),
        value=True,
        quality="bad",
        source_adapter="modbus_tcp",
        binding_id=uuid.uuid4(),
    )

    await router.handle_value_event(event)

    assert called is False


@pytest.mark.anyio
async def test_handle_value_event_skips_none_value(monkeypatch):
    router = WriteRouter(db=None, registry=_Registry())
    called = False

    async def _fake_write(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(router, "_write_to_dest_bindings", _fake_write)

    event = DataValueEvent(
        datapoint_id=uuid.uuid4(),
        value=None,
        quality="good",
        source_adapter="modbus_tcp",
        binding_id=uuid.uuid4(),
    )

    await router.handle_value_event(event)

    assert called is False


@pytest.mark.anyio
async def test_handle_value_event_skips_type_mismatch(monkeypatch):
    router = WriteRouter(db=None, registry=_Registry(data_type="BOOLEAN"))
    called = False

    async def _fake_write(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(router, "_write_to_dest_bindings", _fake_write)

    event = DataValueEvent(
        datapoint_id=uuid.uuid4(),
        value="not-bool",
        quality="good",
        source_adapter="modbus_tcp",
        binding_id=uuid.uuid4(),
    )

    await router.handle_value_event(event)

    assert called is False


@pytest.mark.anyio
async def test_handle_value_event_skips_bool_for_float_datapoint(monkeypatch):
    router = WriteRouter(db=None, registry=_Registry(data_type="FLOAT"))
    called = False

    async def _fake_write(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(router, "_write_to_dest_bindings", _fake_write)

    event = DataValueEvent(
        datapoint_id=uuid.uuid4(),
        value=True,
        quality="good",
        source_adapter="modbus_tcp",
        binding_id=uuid.uuid4(),
    )

    await router.handle_value_event(event)

    assert called is False


@pytest.mark.anyio
async def test_handle_value_event_propagates_good_typed_value(monkeypatch):
    router = WriteRouter(db=None, registry=_Registry(data_type="BOOLEAN"))
    received = {}

    async def _fake_write(dp_id, value, skip_binding_id):
        received["dp_id"] = dp_id
        received["value"] = value
        received["skip_binding_id"] = skip_binding_id

    monkeypatch.setattr(router, "_write_to_dest_bindings", _fake_write)

    event = DataValueEvent(
        datapoint_id=uuid.uuid4(),
        value=True,
        quality="good",
        source_adapter="modbus_tcp",
        binding_id=uuid.uuid4(),
    )

    await router.handle_value_event(event)

    assert received["dp_id"] == event.datapoint_id
    assert received["value"] is True
    assert received["skip_binding_id"] == event.binding_id
